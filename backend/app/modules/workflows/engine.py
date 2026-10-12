"""Bounded DAG execution with explicit branch skipping and durable node outcomes.

中文补充：工作流执行内核。按 ``inspect_graph`` 给出的拓扑序顺序执行节点，把每个节点的
真实状态（pending/running/completed/skipped/failed/waiting/cancelled）写进
``workflow_executions.result_json``，同时通过统一运行中心记录 run/step。

设计要点与已知行为：

- **分支跳过是显式的**：没有活跃上游（``active_parent_ids`` 为空）的节点会被标记为
  ``skipped`` 并说明原因（上游失败 / 未选中此分支），绝不用空值冒充输入。
- **重试**：只重试"重跑无副作用"的节点（``nodes.node_retry_is_safe``），
  默认保守——未列入安全名单的 kind 不重试。重试次数写入 step，便于运行中心展示。
- **恢复**：``waiting`` 状态节点携带 ``waiting.nodeId``，恢复时只把该节点置为 pending
  并把用户输入作为 ``resume_value`` 传入，其余 completed 节点直接从 trace 复用，不重跑外部请求。
- **取消**：节点之间检查 ``cancel_requested``；请求取消会先提交一次控制版本，
  执行器通过条件 UPDATE（``persist_node_trace`` 里的版本比对）感知，因此不会用过期状态覆盖。
- **乐观锁**：``persist_node_trace`` 每写一次节点状态就递增 ``version``；版本冲突时若远端是
  ``cancel_requested`` 则转为取消流程，否则抛 ConflictError。
- **已知行为（重要）**：收尾时提交扫描/采集窗口进度的冲突会被转成失败文案
  （"本轮结果已保留，但扫描进度未能保存；下轮会重新检查本批记录。"），
  于是 ``execution.status`` 被标记为 **failed**——即使所有节点都成功执行。
  这是刻意取舍：进度未落盘意味着下一轮会重复检查，必须让运行历史暴露该事实。
- 节点结果合计上限 ``MAX_TRACE_BYTES``（24 MB），超出直接失败，不做截断。
"""
from __future__ import annotations

from app.core.observability import log_event, observe

from copy import deepcopy
from datetime import datetime
import json
import logging
import time

from pydantic import ValidationError as ContractError
from sqlalchemy import func, select, update
from sqlalchemy.orm.attributes import set_committed_value

from app.errors import AppError, ConflictError, ValidationError
from app.models import RunStep
from app.modules.analytics.connection_pool import read_batch, release_read_batch
from app.modules.collection.extraction import budget_tree, evaluation_budget, matches
from app.modules.runtime import service as runtime
from app.modules.system.service_helpers import commit_change
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.nodes import (
    NO_RESUME, WorkflowPause, bounded_output, execute_node, output_summary, should_retry_node,
)
from app.modules.workflows.models import WorkflowExecution
from app.modules.workflows.schemas import FlowGraph, FlowNode

logger = logging.getLogger(__name__)
MAX_TRACE_BYTES = 24 * 1024 * 1024


def _remote_execution_state(db, execution_id: str):
    """Read the control version as a current row after a failed CAS.

    MySQL's REPEATABLE READ can otherwise answer a plain SELECT from the
    transaction snapshot established before the competing cancel committed.
    A locking read is deliberately used here because this helper is only
    called while resolving a version conflict or finalizing cancellation.

    中文补充：仅在版本冲突或取消收尾时调用，用 ``with_for_update`` 强制读最新已提交行；
    不要在常规路径上顺手替换成普通 SELECT，否则会读到取消前的旧状态。
    """
    with db.no_autoflush:
        return db.execute(select(
            WorkflowExecution.status, WorkflowExecution.version,
            WorkflowExecution.result_json, WorkflowExecution.error_message,
        ).where(WorkflowExecution.id == execution_id).with_for_update()).one_or_none()


def persist_node_trace(db, execution, trace: dict, node_id: str) -> bool:
    """Persist the changed node without resending every preceding row batch.

    Initialize and finalize with a full trace. Intermediate updates on MySQL
    and SQLite use bound JSON values and retain all other node outputs.

    中文补充：这是执行器的乐观锁写入点。三条不变量：

    1. 只有 ``status == "running"`` 且版本匹配时才允许写入，否则说明运行已被取消或
       被其他操作改动——若远端状态是 ``cancel_requested`` 返回 False（由调用方转入取消流程），
       其他情况抛 ConflictError。返回 False 表示"本次运行应当停止并且不要覆盖对方状态"。
    2. 第一次写入（trace 里还没有 nodes）整体写 result_json；之后走增量 JSON Patch：
       ``$.collectionWindow``、本节点状态、所有 skipped 状态、以及本节点的 outputs。
       之所以也写 skipped，是因为同一轮里可能有多个节点被跳过，而每次只 patch 一个节点。
    3. 参数按 80 个一批切分：旧版 SQLite 的 json_set 参数上限为 127，分批才能兼容。
       ``set_committed_value`` 只是同步 ORM 身份映射，不会额外触发一次整表 JSON 写回。

    非 mysql/sqlite 方言直接整体赋值并返回 True（无 JSON Patch 能力）。
    """
    if db.get_bind().dialect.name not in {"mysql", "sqlite"}:
        execution.result_json = deepcopy(trace)
        return True
    expected_version = execution.version
    if not (execution.result_json or {}).get("nodes"):
        result = db.execute(update(WorkflowExecution).where(
            WorkflowExecution.id == execution.id,
            WorkflowExecution.version == expected_version,
            WorkflowExecution.status == "running",
        ).values(result_json=deepcopy(trace), version=expected_version + 1).execution_options(
            synchronize_session=False,
        ))
        if result.rowcount != 1:
            remote = _remote_execution_state(db, execution.id)
            if remote and remote.status == "cancel_requested":
                return False
            raise ConflictError("执行记录已被其他操作修改，请刷新后重试")
        set_committed_value(execution, "result_json", deepcopy(trace))
        set_committed_value(execution, "version", expected_version + 1)
        return True
    changes = {node_id: trace["nodes"][node_id]}
    changes.update({key: state for key, state in trace["nodes"].items() if state.get("status") == "skipped"})
    arguments = []
    if trace.get('collectionWindow'):
        arguments.extend(['$.collectionWindow', func.json_extract(json.dumps(trace['collectionWindow']), '$')])
    for key, state in changes.items():
        arguments.extend(["$.nodes." + json.dumps(key, ensure_ascii=False),
                          func.json_extract(json.dumps(state, ensure_ascii=False, allow_nan=False), "$")])
    if node_id in trace["outputs"]:
        arguments.extend(["$.outputs." + json.dumps(node_id, ensure_ascii=False),
                          func.json_extract(json.dumps(trace["outputs"][node_id], ensure_ascii=False, allow_nan=False), "$")])
    value = WorkflowExecution.result_json
    # Bound function arity for SQLite builds with the older 127-argument limit.
    for offset in range(0, len(arguments), 80):
        value = func.json_set(value, *arguments[offset:offset + 80])
    if "waiting" not in trace:
        value = func.json_remove(value, "$.waiting")
    result = db.execute(update(WorkflowExecution).where(
        WorkflowExecution.id == execution.id,
        WorkflowExecution.version == expected_version,
        WorkflowExecution.status == "running",
    ).values(result_json=value, version=expected_version + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        remote = _remote_execution_state(db, execution.id)
        if remote and remote.status == "cancel_requested":
            return False
        raise ConflictError("执行记录已被其他操作修改，请刷新后重试")
    # Keep the identity map current without scheduling a second, full JSON write.
    set_committed_value(execution, "result_json", deepcopy(trace))
    set_committed_value(execution, "version", expected_version + 1)
    return True


def select_inputs(node: FlowNode, states: dict, active_parents: list[str], budget: RuleBudget, graph: FlowGraph | None = None) -> dict | None:
    """Resolve a single JSONPath value per named input, never the first wildcard match.

    返回 ``{输入名: 值}``；返回 None 表示"引用的上游分支没执行，本节点应跳过"
    （调用方据此标记 skipped，而不是用空值继续）。

    规则：
    - 配置了 ``inputs``：逐个解析，路径必须恰好匹配 1 个值——0 个报错、多个报错并提示
      用户引用数组字段本身（如 ``$.data``）而不是 ``$.data[*]``。上游未完成且未被异常处理接管时，
      merge 节点跳过该输入，其他节点整体返回 None。
    - 未配置 ``inputs``：merge 节点按活跃上游逐个取值（键是上游节点 id）；
      其他节点要求唯一有效上游，否则报错。
    - 条件节点的逐行分支输出（``rowBranches=True``）会按连线端口取 ``trueRows``/``falseRows``，
      两条线都连时合并成一行结果包，保证"空批次"也能沿 ELSE 走到结束节点。
    """
    if node.inputs:
        selected = {}
        for name, reference in node.inputs.items():
            state = states[reference.node_id]
            if state["status"] != "completed" and not state.get("handled"):
                if node.kind == "merge":
                    continue
                return None
            with evaluation_budget(budget.deadline):
                found = matches(reference.path, budget_tree(state["output"]), limit=2)
            if not found:
                raise ValidationError(
                    f"输入“{name}”的路径 {reference.path} 未匹配到值；请检查上游输出或重新选择变量",
                    input_name=name, source_node_id=reference.node_id, path=reference.path,
                )
            if len(found) > 1:
                raise ValidationError(
                    f"输入“{name}”的路径 {reference.path} 匹配到多个值；数组请引用数组字段本身（如 $.data），不要使用 $.data[*]",
                    input_name=name, source_node_id=reference.node_id, path=reference.path,
                )
            selected[name] = found[0]
        return selected
    def parent_output(key):
        """取某个上游节点的输出；对"逐行分支"输出按连线端口挑选对应行数组。

        两条分支线同时连到本节点时，把 trueRows 与 falseRows 合并成一个结果包，
        使下游看到的行集合与上游输入一致（不会因为分流而丢行）。
        """
        output = states[key]["output"]
        # 条件节点逐行分支的输出按端口选择；只连一条时直接取其数组，两条都连时合并为结果包。
        if graph is not None and isinstance(output, dict) and output.get("rowBranches") is True:
            ports = {e.port for e in graph.edges if e.source == key and e.target == node.id}
            selected = [output[port + "Rows"] for port in ("true", "false") if port in ports]
            if len(selected) == 1:
                return selected[0]
            if len(selected) > 1:
                from app.modules.workflows.nodes import packet
                return packet([r for part in selected for r in part["data"]])
        return output
    if node.kind == "merge":
        return {key: parent_output(key) for key in dict.fromkeys(active_parents)}
    if len(set(active_parents)) != 1:
        raise ValidationError("节点缺少唯一有效上游或明确输入映射")
    return {"input": parent_output(active_parents[0])}


def active_parent_ids(graph: FlowGraph, node_id: str, states: dict) -> list[str]:
    """列出"本节点真正应该接收"的上游节点 id，决定节点执行还是跳过。

    这是分支语义的落点，与 ``graph.inspect_graph`` 的端口校验互为表里：

    - ``out``：上游 completed 即活跃；上游被异常策略接管时，只有 ``default-value`` 才继续
      （用默认值当输出），``fail-branch`` 走 error 线。
    - ``true``/``false``：单对象判断看 ``matched``；逐行判断（rowBranches）看该端口行数是否非空，
      但"上游总行数为 0"时 false 分支仍然活跃——否则空结果集会让流程无法走到结束节点。
    - ``case:<编码>``：上游输出的 ``category`` 等于端口编码才算命中。
    - ``error``：只有上游被 ``fail-branch`` 接管时才活跃。

    返回空列表表示该节点没有活跃上游，调用方把它标记为 skipped 并写明原因。
    """
    output = []
    for edge in graph.edges:
        if edge.target != node_id:
            continue
        parent = states[edge.source]
        value = parent.get("output") or {}
        row_branch = isinstance(value, dict) and value.get("rowBranches") is True
        branch_active = False
        if row_branch:
            # Preserve an empty batch through ELSE so an empty result can finish.
            branch_active = bool(value.get(edge.port + "Rows", {}).get("data")) or (
                edge.port == "false" and value.get("totalCount") == 0
            )
        elif isinstance(value, dict):
            branch_active = value.get("matched") is (edge.port == "true")
        category_active = (edge.port.startswith("case:") and isinstance(value, dict)
                           and value.get("category") == edge.port[5:])
        if parent["status"] == "completed" and (edge.port == "out" or category_active or edge.port in {"true", "false"} and branch_active):
            output.append(edge.source)
        elif parent.get("handled") and (
            edge.port == "error" and parent.get("errorStrategy") == "fail-branch"
            or edge.port == "out" and parent.get("errorStrategy") == "default-value"
        ):
            output.append(edge.source)
    return output


def _safe_error(cause: Exception) -> tuple[str, str, dict]:
    """Persist known business errors, never raw driver, model or validation inputs.

    返回 ``(errorCode, 用户可读消息, 追溯引用)``。已知业务异常保留其 code 与 message（截断 2000 字）；
    契约错误统一成"节点运行时输入不符合字段类型"；其他异常只记录异常类型到日志，
    对外返回固定文案——不把驱动原文、模型输出或参数值写进运行记录。
    """
    if isinstance(cause, AppError):
        trace = {key: cause.extra[key] for key in ("query_id", "run_id", "write_operation_id") if key in cause.extra}
        return cause.code, cause.message[:2000], trace
    if isinstance(cause, ContractError):
        return "invalid_node_input", "节点运行时输入不符合字段类型，请检查映射与参数", {}
    logger.error("Workflow node failed unexpectedly (%s)", type(cause).__name__)
    return "workflow_node_failed", "节点执行失败，请检查数据源和节点配置；未使用演示或替代结果", {}


def _cancel_requested(ctx, execution) -> bool:
    """Observe a control request committed by another request between nodes.

    中文补充：取消请求由另一个请求提交，本事务的 ORM 缓存里看不到。因此先 expire 再 refresh
    两个控制列，强制读库。只在节点边界调用（不是每行/每秒），成本可接受。
    """
    ctx.db.expire(execution, ["status", "version"])
    ctx.db.refresh(execution, ["status", "version"])
    return execution.status == "cancel_requested"


def _finalize_cancelled(ctx, execution, run, trace: dict, started: float, prior: dict) -> None:
    """Commit cancellation from the newest control version and retain the node result.

    中文补充：取消收尾必须基于远端最新版本提交，否则会覆盖取消请求刚写入的状态。
    处理顺序：
    1. 远端已结束（非 running/cancel_requested/waiting）则直接返回，不重复收尾；
    2. 控制审计条目取远端为主并把本地新增项合并去重（远端持有最新版本），上限 100 条；
    3. 把 pending/waiting/running 的节点统一改写为 cancelled（保留已完成节点的结果），
       清掉 waiting 标记并累加耗时；
    4. 用 ``set_committed_value`` 把 ORM 基线对齐到远端版本，让随后的 flush 只做一次条件取消写入；
    5. 按节点状态回填 RunStep（completed/handled -> completed，failed -> failed，其余 cancelled），
       以 cancelled 结束统一运行并写审计。

    异常：远端记录已不存在时抛 ConflictError。副作用：更新执行记录、run_steps、审计。
    """
    remote = _remote_execution_state(ctx.db, execution.id)
    if remote is None:
        raise ConflictError("执行记录已不存在，无法完成取消")
    if remote.status not in {"running", "cancel_requested", "waiting"}:
        return
    # The control request owns the newest version.  Keep its control audit
    # entries while merging the executor's last completed node into the trace.
    remote_trace = deepcopy(remote.result_json or {})
    controls = list(remote_trace.get("controls") or [])
    local_controls = list(trace.get("controls") or [])
    trace["controls"] = controls or local_controls
    for item in local_controls:
        if not any(existing.get("idempotencyKey") == item.get("idempotencyKey") for existing in trace["controls"]):
            trace["controls"].append(item)
    trace["controls"] = trace["controls"][-100:]
    for state in trace.get("nodes", {}).values():
        if state.get("status") in {"pending", "waiting", "running"}:
            state.update(status="cancelled", message="运行已由授权用户取消")
    trace.pop("waiting", None)
    trace["durationMs"] = int(prior.get("durationMs") or 0) + int((time.monotonic() - started) * 1000)
    # Reset the ORM baseline to the version committed by the control request;
    # the normal versioned flush then performs one conditional cancellation.
    set_committed_value(execution, "status", remote.status)
    set_committed_value(execution, "version", remote.version)
    set_committed_value(execution, "result_json", remote.result_json)
    set_committed_value(execution, "error_message", remote.error_message)
    execution.status = "cancelled"
    execution.error_message = ""
    execution.result_json = deepcopy(trace)
    states = trace.get("nodes") or {}
    for step in ctx.db.scalars(select(RunStep).where(
        RunStep.run_id == run.id, RunStep.status.in_(("running", "waiting")),
    )).all():
        node_id = str((step.input_json or {}).get("node_id") or "")
        state = states.get(node_id) or {}
        if state.get("status") == "completed" or state.get("handled"):
            step.status = "completed"
            step.output_json = state.get("summary") or {}
        elif state.get("status") == "failed":
            step.status = "failed"
            step.error_code = state.get("errorCode") or "workflow_node_failed"
            step.error_summary = str(state.get("message") or "流程节点执行失败")[:4000]
        elif state.get("status") == "skipped":
            step.status = "skipped"
            step.output_json = state.get("summary") or {}
        else:
            step.status = "cancelled"
        step.completed_at = datetime.utcnow()
    runtime.finalize_run(ctx.db, run, status="cancelled")
    commit_change(
        ctx, "workflow.cancel", "workflows", execution.workflow_id,
        {"execution_id": execution.id, "run_id": run.id, "revision": execution.revision},
    )


@observe("workflow.execute", "工作流执行器", fields={"workspace_id": "ctx.workspace_id", "run_id": "run.id", "execution_id": "execution.id", "workflow_id": "execution.workflow_id"}, status_from="execution.status")
@read_batch()
def execute_graph(
    ctx, execution, run, graph: FlowGraph, order: list[str], timeout_seconds: int,
    *, resume_node_id: str = "", resume_value=None, has_resume_value: bool = False,
    wait_context: dict | None = None,
) -> None:
    """Persist actual node outcomes, including durable waits, resumes and cancellation.

    参数：
        execution / run：已落库的执行记录与统一运行记录（调用方负责创建并提交唯一键）。
        graph / order：已通过 ``executable_graph`` 的图与拓扑序；order 来自校验结果，不要重算。
        timeout_seconds：整条流程的预算，用于构造 ``RuleBudget``（超时后不再开始后续节点）。
        resume_node_id / resume_value / has_resume_value：恢复上下文。只有指定节点会收到
            ``wait_context`` 与恢复值；question 节点才允许把用户输入当作答案
            （has_resume_value），延迟恢复不传值。
        wait_context：上次等待保存的 ``waiting`` 负载（含容器/子图检查点）。

    返回：无。结果全部落在 ``execution.status/result_json`` 与 run/step 上。

    核心流程：加载或重建 trace -> 逐节点（跳过已完成/已处理）-> 检查取消 -> 计算活跃上游
    -> 写 running -> 执行（含重试循环）-> 写终态。遇到 ``WorkflowPause`` 时把 waiting 标记
    落到执行记录并直接返回，等待恢复时再次调用本函数。
    最后统一提交扫描/窗口进度，任何失败都会把整次运行标记为 failed（见模块 docstring）。

    异常：外部异常经 ``_safe_error`` 转成失败文案并落库；取消、冲突等特殊情况按上述路径收尾。
    副作用：写 workflow_executions、run_steps、审计，并可能推进扫描/采集窗口进度。
    """
    started = time.monotonic()
    budget = RuleBudget(started + timeout_seconds)
    nodes = {node.id: node for node in graph.nodes}
    prior = deepcopy(execution.result_json or {})
    # 只有"旧 trace 的节点集合与当前图完全一致"才复用：图变了（例如恢复时用户已经改过版本）
    # 必须整体重建，否则旧节点状态会被错配到新图。复用时要清掉上一次的 waiting 标记。
    if prior.get("nodes") and set(prior["nodes"]) == set(nodes):
        states = prior["nodes"]
        trace = prior
        trace.setdefault("outputs", {})
        trace.pop("waiting", None)
    else:
        states = {
            key: {"nodeId": key, "kind": node.kind, "title": node.title, "status": "pending"}
            for key, node in nodes.items()
        }
        trace = {"nodes": states, "outputs": {}, "durationMs": 0, "controls": []}
    trace.setdefault("controls", [])
    # 增量采集窗口只在开始节点的配置里声明；本轮 trace 没有窗口时才创建，恢复时直接沿用。
    window_config = next((n.config for n in graph.nodes if n.kind == "start" and n.config.get("incrementalWindow")), None)
    failure, trace_bytes, cancelled = "", 0, False
    for state in states.values():
        if (state.get("status") == "completed" or state.get("handled")) and "output" in state:
            trace_bytes += len(json.dumps(state["output"], ensure_ascii=False, allow_nan=False).encode())
    sequence = int(ctx.db.scalar(
        select(func.max(RunStep.sequence)).where(RunStep.run_id == run.id)
    ) or 0)

    for key in order:
        node, state = nodes[key], states[key]
        if node.kind not in {"read-values", "database-query"}:
            release_read_batch()
        if state.get("status") in {"completed", "skipped", "cancelled"} or state.get("handled"):
            continue
        if _cancel_requested(ctx, execution):
            cancelled = True
            break
        parents = active_parent_ids(graph, key, states)
        # failure 一旦置位，后续节点不再执行（含正常的待执行节点），统一记为"上游失败，未执行"。
        if failure or (node.kind != "start" and not parents):
            log_event("workflow.node.skipped", "节点未执行", node_id=key, node_kind=node.kind,
                      reason="upstream_failed" if failure else "branch_not_selected")
            state.update(status="skipped", message="上游失败，未执行" if failure else "未选中此分支，未执行")
            continue
        sequence += 1
        step = runtime.start_step(
            ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, sequence=sequence,
            step_type="workflow_node", name=node.title,
            input_data={"workflow_id": execution.workflow_id, "node_id": key, "kind": node.kind},
        )
        state.update(status="running", startedAt=datetime.utcnow().isoformat())
        # Use the same conditional trace write for local and I/O nodes.  This
        # makes a concurrent cancel visible before the executor commits a
        # stale running state, while still patching only the changed JSON path.
        if not persist_node_trace(ctx.db, execution, trace, key):
            ctx.db.rollback()
            cancelled = True
            break
        ctx.db.commit()
        node_started = time.monotonic()
        try:
            budget.consume()
            if node.kind == 'start' and window_config and not trace.get('collectionWindow'):
                from app.modules.workflows.windows import prepare_window
                trace['collectionWindow'] = prepare_window(ctx, execution, window_config)
                set_committed_value(execution, 'result_json', deepcopy(trace))
            inputs = deepcopy(execution.input_json) if node.kind == "start" else select_inputs(node, states, parents, budget, graph)
            if inputs is None:
                log_event("workflow.node.skipped", "输入分支未执行，跳过当前节点", node_id=key, node_kind=node.kind)
                state.update(status="skipped", message="引用的输入分支未执行，未用空值代替")
                step.status, step.completed_at = "skipped", datetime.utcnow()
            else:
                retry_count = 0
                while True:
                    try:
                        output = execute_node(
                            ctx, node, inputs, budget, execution_id=execution.id,
                            run_id=run.id, step_id=step.id,
                            resume_value=(resume_value if key == resume_node_id and has_resume_value else NO_RESUME),
                            wait_context=(wait_context if key == resume_node_id else None),
                        )
                        break
                    except WorkflowPause:
                        raise
                    except Exception as cause:
                        # 重试前必须回滚并重新取 step：失败可能来自数据库操作，
                        # 脏会话状态下继续重试会把错误状态带进下一次尝试。
                        release_read_batch()
                        ctx.db.rollback()
                        step = ctx.db.get(RunStep, step.id)
                        if (not node.retry_config.retry_enabled
                                or not should_retry_node(node, cause)
                                or retry_count >= node.retry_config.max_retries):
                            raise
                        retry_count += 1
                        log_event("workflow.node.retry", "准备重试工作流节点", level=logging.WARNING,
                                  node_id=key, attempt=retry_count + 1,
                                  error_type=type(cause).__name__, retry_interval_ms=node.retry_config.retry_interval)
                        step.retry_count = retry_count
                        step.metadata_json = {**(step.metadata_json or {}), "retryIntervalMs": node.retry_config.retry_interval}
                        ctx.db.commit()
                        time.sleep(node.retry_config.retry_interval / 1000)
                        budget.consume()
                budget.consume()
                bounded_output(output)
                size = len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode())
                if trace_bytes + size > MAX_TRACE_BYTES:
                    raise ValidationError("流程节点结果合计超过 24 MB，请减少字段、行数或拆分批次")
                trace_bytes += size
                state.pop("wait", None)
                state.update(status="completed", output=output, summary=output_summary(output))
                if retry_count:
                    state["retryCount"] = retry_count
                runtime.complete_step(ctx.db, step, state["summary"])
                if node.kind in {"end", "reply"}:
                    trace["outputs"][key] = output
        except WorkflowPause as pause:
            # 持久化等待：节点状态记为 waiting 并保留 step 为 waiting；执行与运行状态同步转为
            # waiting 后直接返回，等恢复请求再次调用本函数（不会继续执行后续节点）。
            release_read_batch()
            state.update(
                status="waiting", message=str(pause),
                wait={"kind": pause.kind, **pause.payload},
            )
            step.status = "waiting"
            step.output_json = {"wait": state["wait"]}
            trace["waiting"] = {"nodeId": key, "title": node.title, **state["wait"]}
            state["durationMs"] = int((time.monotonic() - node_started) * 1000)
            trace["durationMs"] = int(prior.get("durationMs") or 0) + int((time.monotonic() - started) * 1000)
            execution.status = "waiting"
            execution.error_message = ""
            execution.result_json = deepcopy(trace)
            run.status = "waiting"
            run.completed_at = None
            try:
                commit_change(
                    ctx, "workflow.wait", "workflows", execution.workflow_id,
                    {"execution_id": execution.id, "node_id": key, "kind": pause.kind},
                )
            except ConflictError:
                ctx.db.rollback()
                remote = _remote_execution_state(ctx.db, execution.id)
                if not remote or remote.status != "cancel_requested":
                    raise
                _finalize_cancelled(ctx, execution, run, trace, started, prior)
            return
        except Exception as cause:
            release_read_batch()
            ctx.db.rollback()
            step = ctx.db.get(RunStep, step.id)
            code, failure, references = _safe_error(cause)
            state.update(
                status="failed", message=failure, errorCode=code, references=references,
                retryCount=step.retry_count, errorStrategy=node.error_strategy,
            )
            runtime.fail_step(ctx.db, step, code, failure)
            # 异常策略：default-value 用配置的默认值继续，fail-branch 输出结构化错误对象并走 ERROR 线。
            # 两者都把节点标记为 handled（表示"已被策略接管"），并清掉 failure 让后续节点继续执行；
            # 默认值本身也要通过大小校验，超出预算时退回为未处理失败。
            if node.error_strategy in {"default-value", "fail-branch"}:
                try:
                    handled_output = deepcopy(node.default_value) if node.error_strategy == "default-value" else {
                        "errorType": code, "errorMessage": failure, "references": references,
                    }
                    bounded_output(handled_output)
                    size = len(json.dumps(handled_output, ensure_ascii=False, allow_nan=False).encode())
                    if trace_bytes + size > MAX_TRACE_BYTES:
                        raise ValidationError("流程节点结果合计超过 24 MB，请减少默认值或拆分流程")
                except Exception as handling_cause:
                    code, failure, references = _safe_error(handling_cause)
                    state.update(message=failure, errorCode=code, references=references)
                else:
                    trace_bytes += size
                    state.update(handled=True, output=handled_output, summary=output_summary(handled_output))
                    failure = ""
        state["durationMs"] = int((time.monotonic() - node_started) * 1000)
        if not persist_node_trace(ctx.db, execution, trace, key):
            ctx.db.rollback()
            cancelled = True
            break
        ctx.db.commit()

    release_read_batch()
    if cancelled:
        _finalize_cancelled(ctx, execution, run, trace, started, prior)
        return
    if not failure and not trace["outputs"]:
        # 图校验已要求至少一个 end/reply，因此走到这里说明运行期没有任何结束节点产出，
        # 属于配置或分支逻辑问题，必须报失败而不是当成成功。
        failure = "没有结束节点产生有效输出，请检查条件分支和输入映射"
    if not failure:
        from app.modules.workflows.scanning import commit_scans
        try:
            commit_scans(ctx, execution, trace)
            from app.modules.workflows.windows import finish_window
            finish_window(ctx, execution, trace)
        except Exception:
            # 见模块 docstring：进度提交失败会保留本轮结果，但把运行标记为 failed，
            # 提醒运维"下一轮会重复检查本批记录"。不要改成仅记 warning。
            ctx.db.rollback()
            failure = "本轮结果已保留，但扫描进度未能保存；下轮会重新检查本批记录。"
    trace.pop("waiting", None)
    trace["durationMs"] = int(prior.get("durationMs") or 0) + int((time.monotonic() - started) * 1000)
    execution.status = "failed" if failure else "completed"
    execution.error_message = failure
    execution.result_json = deepcopy(trace)
    runtime.finalize_run(
        ctx.db, run, status=execution.status,
        error_code="workflow_failed" if failure else None, error_summary=failure or None,
    )
    try:
        commit_change(
            ctx, "workflow.execute", "workflows", execution.workflow_id,
            {"execution_id": execution.id, "run_id": run.id, "revision": execution.revision, "status": execution.status},
        )
    except ConflictError:
        ctx.db.rollback()
        remote = _remote_execution_state(ctx.db, execution.id)
        if not remote or remote.status != "cancel_requested":
            raise
        _finalize_cancelled(ctx, execution, run, trace, started, prior)
