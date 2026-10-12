"""Bounded cyclic keyset scans; advance only after all visual judgments succeed.

中文补充：为"有界连续扫描"（database-query 配置 scanKey）提供游标状态。
与采集窗口的区别：窗口按时间，扫描按单调整数列，一轮扫到末尾后下一轮从首条重新复核
已修改记录（见 ``scan_output`` 的 coverage 文案）。

关键设计：
- 状态键为 ``scan:<nodeId>``，按流程 + 节点维度保存；同一节点配置变更会导致
  ``signature`` 变化，此时游标归零重新从头扫描（不能沿用过期的位置）。
- 每个查询节点只保存一个检查点，即使结果包被下游多个节点透传也不会重复推进。
- 推进同样使用乐观锁：``expected`` 与库中旧值不一致即抛 ConflictError。

已知行为（重要）：``commit_scans`` 抛出的 ConflictError 会被 ``engine.execute_graph``
的收尾逻辑捕获并转换成 failure 文案（"本轮结果已保留，但扫描进度未能保存"），
于是整次运行状态被标记为 **failed**，尽管所有节点都已成功执行。
保留这个行为是为了让"重复检查"这个副作用在运行历史里可见，而不是被当成成功隐藏。
"""
import hashlib
import json

from sqlalchemy import select

from app.errors import ConflictError, ValidationError
from app.modules.workflows.models import WorkflowBusinessState, WorkflowExecution


def prepare_scan(ctx, config, execution_id, node_id):
    """构造本次扫描的检查点上下文，返回 ``stateKey / signature / expected / afterKey``。

    ``afterKey`` 只有在签名一致（同一节点、同一 SQL、同一参数、同一位置列）时才沿用历史值，
    否则从 0 开始重新扫描。

    异常：未保存的草稿执行（没有 execution_id）或查询里没有 ``:afterKey`` 参数时抛
    ValidationError；运行不属于当前工作区时同样拒绝。
    副作用：只读取状态表。
    """
    if not execution_id or ":afterKey" not in config.query:
        raise ValidationError("连续扫描需要已保存流程和 afterKey 查询参数")
    execution = ctx.db.get(WorkflowExecution, execution_id)
    if not execution or execution.workspace_id != ctx.workspace_id:
        raise ValidationError("扫描运行不属于当前工作区")
    signature = hashlib.sha256(json.dumps({"query": config.query, "source": config.datasource_id,
        "parameters": config.parameters, "key": config.scan_key}, sort_keys=True).encode()).hexdigest()
    key = "scan:" + node_id
    state = ctx.db.scalar(select(WorkflowBusinessState).where(
        WorkflowBusinessState.workspace_id == ctx.workspace_id,
        WorkflowBusinessState.workflow_id == execution.workflow_id, WorkflowBusinessState.state_key == key))
    previous = state.value if state else {}
    return {"stateKey": key, "signature": signature,
            "expected": previous, "afterKey": previous.get("afterKey", 0) if previous.get("signature") == signature else 0}


def scan_output(config, rows, state):
    """校验本批扫描结果并生成扫描回执（写入节点输出供报告与收尾提交使用）。

    位置列必须是纯数字（含数字字符串，兼容驱动把 bigint 读成文本的情况），
    必须唯一递增且严格大于本轮起始位置，否则抛 ValidationError——位置倒退意味着会漏检或重复
    判定，因此宁可让本节点失败也不推进。空结果集视为"本轮扫描完成"（cycleComplete）。
    """
    keys = [row.get(config.scan_key) for row in rows]
    if any(isinstance(k, bool) or not isinstance(k, (int, str)) or not str(k).isdigit() for k in keys):
        raise ValidationError("连续扫描的记录位置无效，未推进扫描")
    keys = [int(k) for k in keys]
    if keys != sorted(set(keys)) or any(k <= state["afterKey"] for k in keys):
        raise ValidationError("连续扫描必须按唯一递增位置读取，未推进扫描")
    return {**state, "nextKey": keys[-1] if keys else 0, "rows": len(rows),
            "cycleComplete": not rows,
            "coverage": "本批连续扫描；完成后下一周期从首条复核修改记录。"}


def commit_scans(ctx, execution, trace):
    """整轮成功后统一提交各查询节点的扫描游标；冲突时抛 ConflictError。

    只为 status == completed 且输出里带 ``scan`` 的节点提交；同一 stateKey 只提交一次
    （结果包可能被下游 report/end 节点原样保留）。同一执行重复提交直接跳过（幂等），
    其他执行已推进则抛 ConflictError——调用方（引擎收尾）会把它记为运行失败，见模块 docstring。
    """
    seen = set()
    for node in trace.get("nodes", {}).values():
        scan = (node.get("output") or {}).get("scan") if isinstance(node.get("output"), dict) else None
        if node.get("status") != "completed" or not scan:
            continue
        # Report/end nodes may preserve the upstream packet. Each query owns
        # one checkpoint even when that packet appears in several node outputs.
        if scan["stateKey"] in seen:
            continue
        seen.add(scan["stateKey"])
        state = ctx.db.scalar(select(WorkflowBusinessState).where(
            WorkflowBusinessState.workspace_id == ctx.workspace_id,
            WorkflowBusinessState.workflow_id == execution.workflow_id,
            WorkflowBusinessState.state_key == scan["stateKey"]).with_for_update())
        previous = state.value if state else {}
        if previous.get("executionId") == execution.id:
            continue
        if previous != scan["expected"]:
            raise ConflictError("另一轮检查已推进扫描位置，本轮结果已保留但不会覆盖新位置")
        if state is None:
            state = WorkflowBusinessState(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                workflow_id=execution.workflow_id, state_key=scan["stateKey"], value={})
            ctx.db.add(state)
        state.value = {"signature": scan["signature"], "afterKey": scan["nextKey"], "executionId": execution.id}
