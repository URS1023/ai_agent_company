"""Persist loop position and completed nested nodes across human/delay waits.

中文补充：loop / iterator / while 三类容器节点的执行入口。容器体子图在内存中逐轮运行，
但每轮的位置、历史结果与子图轨迹都要落盘，否则"提问""延迟"等持久化等待会让循环从头重跑。
``wait_context["container"]`` 就是这份检查点，恢复时按它还原下标与已完成节点。
约束：容器必须有界（iterations / 记录条数 / max_iterations），达到上限仍未满足条件时直接报错，
不会静默退出循环；子图每轮必须返回对象，否则无法作为下一轮输入。
"""
from copy import deepcopy
from app.errors import ValidationError
from app.modules.workflows.expressions import boolean, evaluate


def execute_container(ctx, node, config, value, budget, *, execution_id, run_id, wait_context, resume_value):
    """运行有界容器节点（loop/iterator/while），支持在子图内部暂停后原样恢复。

    参数：
        ctx：请求上下文（含 db、workspace_id、user_id）。
        node：FlowNode，kind 必须是 loop / iterator / while。
        config：已校验的 LoopConfig / IteratorConfig / WhileConfig。
        value：上游输入值；loop 与 while 要求对象，iterator 要求记录数组。
        budget：RuleBudget，循环每轮都要 ``consume()``，超时或超次数即失败。
        execution_id / run_id：用于子图节点的副作用去重键与统一运行中心追踪。
        wait_context：上次等待保存的 ``container`` 检查点；为空表示首轮。
        resume_value：恢复时交给等待节点的用户输入；无恢复值时为 NO_RESUME。

    返回：loop/while 返回 ``{"data": ..., "iterations": ..., "terminated": ...}``；
    iterator 返回结果记录数组的结果包（带 iteratedCount）。

    异常：ValidationError（输入类型不符、条件循环达到上限）；遇到子图持久化等待时
    抛出 WorkflowPause，并把已完成轮次的检查点一起带上。
    """
    from app.modules.workflows.nodes import (
        NO_RESUME, WorkflowPause, _execute_container_subgraph, _append_subgraph_trace, records, packet,
    )
    state = (wait_context or {}).get("container") or {}
    # 没有检查点时全部按首轮处理：index=0、history/traces 为空。
    index = int(state.get("index", 0))
    history = deepcopy(state.get("history") or [])
    traces = deepcopy(state.get("traces") or [])
    used = int(state.get("traceBytes", 0))
    output = deepcopy(state.get("output") or [])
    if node.kind != "iterator" and not isinstance(value, dict):
        raise ValidationError("循环节点需要对象输入")
    # current 是"上一轮结束时的对象"，iterator 每轮改用 rows[index] 作为输入。
    current = deepcopy(state.get("current", value))
    rows = records(value) if node.kind == "iterator" else []
    # bodyWait 非空表示上一轮在子图内部暂停过，本轮要先走恢复而不是重新判断循环条件。
    body_wait = state.get("bodyWait") or {}
    while True:
        budget.consume()
        if node.kind == "loop" and index >= config.iterations:
            break
        if node.kind == "iterator" and index >= len(rows):
            break
        # 条件循环在恢复轮里不再重新求值：否则"暂停后条件已不成立"会丢掉那一轮结果。
        if node.kind == "while" and not body_wait and not boolean(evaluate(config.expression, current, budget)):
            break
        if node.kind == "while" and index >= config.max_iterations:
            raise ValidationError("条件循环达到最大次数仍未终止")
        initial = rows[index] if node.kind == "iterator" else current
        try:
            result, trace = _execute_container_subgraph(ctx, config.subgraph,
                {**initial, "_index": index, "_iteration": index + 1}, budget,
                execution_id=execution_id, run_id=run_id, parent_node_id=node.id, iteration=index,
                resume_state=body_wait.get("subgraph"), resume_value=resume_value if body_wait else NO_RESUME)
        except WorkflowPause as pause:
            # 子图在等待，把"当前是第几轮、之前轮次的结果、子图内部已完成节点"整体存档。
            saved = {"index": index, "current": current, "history": history, "traces": traces,
                     "traceBytes": used, "output": output, "bodyWait": pause.payload}
            raise WorkflowPause(pause.kind, {**pause.payload, "container": saved}) from pause
        if not isinstance(result, dict):
            raise ValidationError("循环体子图必须返回对象")
        current = result
        used = _append_subgraph_trace(traces, trace, used)
        if node.kind == "iterator":
            current.pop("_iteration", None)
            output.append(current)
        elif node.kind == "loop" and config.keep_history:
            history.append(deepcopy(current))
        index += 1
        body_wait, resume_value = {}, NO_RESUME
    if node.kind == "iterator":
        return packet(output, iteratedCount=len(output), subgraphHistory=traces)
    current.pop("_index", None)
    current.pop("_iteration", None)
    return {"data": current, "iterations": index, "subgraphHistory": traces,
            **({"history": history} if node.kind == "loop" and config.keep_history else {}),
            **({"terminated": True} if node.kind == "while" else {})}
