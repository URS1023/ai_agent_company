"""记录工具进入发送边界的事实，恢复时区分未派发与结果尚未核实。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update

from app.errors import ConflictError
from app.models import Run, RunStep, ToolCall
from app.providers.tool.base import ToolResult
from app.providers.tool.side_effects import uncertain_result


TERMINAL_RUNS = {"completed", "failed", "cancelled", "partial", "interrupted"}
UNFINISHED_CALLS = {"prepared", "dispatch_started", "pending", "running"}
DATA_TOOLS = {"query_business_data", "query_inventory_data", "collect_device_monitoring_snapshot",
              "create_dashboard_from_queries", "get_business_dashboard", "view_device_alerts",
              "create_business_dashboard", "update_business_dashboard"}
DSH_SAVED_DELIVERY_TOOLS = {"create_dashboard_from_queries", "create_business_dashboard",
                          "get_business_dashboard", "update_business_dashboard", "view_device_alerts"}


def accesses_datasource(name, provider="", *, execution_owner=None):
    """仅 DSH 内置历史读取和静态交付不属于新取数；仍须经过当前权限及快照校验。

    Worker 固定的执行归属保证 create_business_dashboard 只复用 Q；其他调用方的同名
    入口仍可能生成新 SQL。数据库连接器、结构刷新、采样和采集始终保留停止边界。
    """
    if provider == "database":
        return True
    if provider == "builtin" and execution_owner == "dsh" and name in DSH_SAVED_DELIVERY_TOOLS:
        return False
    return name in DATA_TOOLS


def has_external_side_effect(spec):
    """依据服务端工具配置分类；HTTP GET 和本地工具不套用外部副作用冻结。"""
    return spec.provider == "mcp" or (spec.provider == "http" and
        str((getattr(spec, "_connector_spec", None) or {}).get("method") or "POST").upper() == "POST")


def prepared_result(call, spec):
    """在任何 provider 预检前持久登记准备阶段，模型参数不能写入此信任标记。"""
    return ToolResult(status="running", metadata={"dispatch": {
        "origin": "business_worker", "version": 1, "phase": "prepared", "provider": spec.provider,
        "externalSideEffect": has_external_side_effect(spec), "idempotencyKey": call.idempotency_key,
    }}).to_dict()


def journal(call):
    """读取绑定原调用键的派发日志；不把业务返回数据或模型参数当日志。"""
    result = call.result_json if isinstance(call.result_json, dict) else {}
    metadata = result.get("metadata") or {}
    value = metadata.get("dispatch") if isinstance(metadata, dict) else None
    if (isinstance(value, dict) and value.get("origin") == "business_worker" and value.get("version") == 1
            and value.get("phase") in {"prepared", "dispatch_started"}
            and value.get("idempotencyKey") == call.idempotency_key):
        return value
    return None


def start_dispatch(db, run, call, *, provider, external_side_effect):
    """预检通过后、发送尝试之前提交派发意图；提交失败必须阻止真正发送。"""
    from app.modules.runtime.task_control import checkpoint
    checkpoint()
    runs = Run.__table__
    status = db.scalar(select(runs.c.status).where(runs.c.id == run.id, runs.c.workspace_id == run.workspace_id))
    if status in TERMINAL_RUNS or status is None:
        raise ConflictError("本次运行已经结束，不能再派发工具")
    state = journal(call)
    if not state or call.status != "prepared":
        raise ConflictError("工具派发阶段不符合预期，不能重复发送")
    state = {**state, "phase": "dispatch_started", "provider": provider,
             "externalSideEffect": external_side_effect is True}
    call.status = "dispatch_started"
    call.result_json = ToolResult(status="running", metadata={"dispatch": state}).to_dict()
    try:
        db.commit()
    except Exception:
        # 登记未提交时回到持久 prepared，不能将尚未发送的请求误标成派发完成。
        db.rollback()
        raise


def unfinished_external_result(call, provider):
    """把可信派发日志或旧版无阶段遗留记录转成保守未知状态，不宣称已送达。"""
    if provider not in {"http", "mcp"}:
        return None
    state = journal(call)
    if state:
        if call.status not in UNFINISHED_CALLS or state["phase"] != "dispatch_started" or state.get("externalSideEffect") is not True:
            return None
        result = ToolResult(**uncertain_result(key=call.idempotency_key, provider=state["provider"],
            error_summary="此前操作已进入发送边界，但最终回执未保存；不能确定远端是否执行，请先核实。"))
        result.metadata.update(dispatch=state, receiptOrigin="dispatch_journal")
        return result
    saved = call.result_json if isinstance(call.result_json, dict) else {}
    metadata = saved.get("metadata") or {}
    legacy = (call.status in {"running", "pending"} and not call.result_json) or (
        call.status == "failed" and saved.get("error_code") == "tool_interrupted_unknown"
        and isinstance(metadata, dict) and metadata.get("receiptOrigin") == "legacy_unstaged")
    if legacy:
        result = ToolResult(**uncertain_result(key=call.idempotency_key, provider=provider,
            error_summary="旧调用缺少派发阶段且没有最终回执，无法确定是否已发送；请先核实原操作。"))
        result.metadata["receiptOrigin"] = "legacy_unstaged"
        return result
    return None


def _calls(db, run):
    """恢复属于服务端生命周期，固定运行归属，不受展示域 runs.view 过滤。"""
    calls, runs = ToolCall.__table__, Run.__table__
    return db.execute(select(calls).join(runs, runs.c.id == calls.c.run_id).where(
        calls.c.run_id == run.id, calls.c.workspace_id == run.workspace_id,
        runs.c.workspace_id == run.workspace_id, runs.c.started_by_user_id == run.started_by_user_id))


def _task_run_criteria(run):
    """生命周期读取共用持久任务与三重归属条件；缺少任务或会话时只允许本运行。"""
    runs = Run.__table__
    snapshot = run.context_snapshot or {}
    identity = snapshot.get("task_id")
    allowed = runs.c.id == run.id
    if isinstance(identity, str) and identity and run.conversation_id:
        allowed = or_(allowed, runs.c.context_snapshot["task_id"].as_string() == identity)
        task = snapshot.get("business_task") or {}
        if isinstance(task, dict) and task.get("task_id") == identity and task.get("resume_from_run_id"):
            # 历史草稿迁移只纳入持久绑定的原运行，仍受下方会话和主体限制。
            allowed = or_(allowed, runs.c.id == task["resume_from_run_id"])
    return (allowed, runs.c.workspace_id == run.workspace_id,
            runs.c.started_by_user_id == run.started_by_user_id, runs.c.conversation_id == run.conversation_id)


def task_datasource_halted(db, run) -> bool:
    """派发前读取同任务已提交的停止事实；排队和新Run不构成异常恢复许可。

    只读平台状态，不连接业务库，也不判断旧SQL是否已在服务端停止。
    内部表读取不受runs.view展示过滤，不把历史业务结果返回给新运行。
    """
    runs = Run.__table__
    return db.scalar(select(runs.c.id).where(*_task_run_criteria(run),
        runs.c.context_snapshot["database_access_halted"].as_boolean().is_(True)).limit(1)) is not None


def _task_calls(db, run, tool_name=None):
    """只读同任务内部回执；展示域权限不能隐藏未知操作，查询不对用户输出历史数据。"""
    calls, runs = ToolCall.__table__, Run.__table__
    statement = select(calls).join(runs, runs.c.id == calls.c.run_id).where(
        calls.c.workspace_id == run.workspace_id, *_task_run_criteria(run))
    if tool_name is not None:
        statement = statement.where(calls.c.tool_name == tool_name,
            calls.c.status.in_(["failed", "prepared", "dispatch_started", "running", "pending"]))
    return db.execute(statement.order_by(calls.c.started_at, calls.c.id))


def unconfirmed_task_replay_result(db, run, *, tool_name, provider):
    """当前工具已通过可用性检查后，沿任务读取未知回执；不重发也不改写原始调用。"""
    from .replay_guard import unconfirmed_replay_result

    for call in _task_calls(db, run, tool_name):
        blocked = unconfirmed_replay_result(run_id=call.run_id, tool_name=tool_name, provider=provider,
                                            previous_calls=[call])
        if blocked is None:
            continue
        if call.run_id != run.id:
            blocked.metadata.pop("originalErrorSummary", None)
            blocked.metadata.update(replayScope="same_task_same_tool", originalRunId=call.run_id,
                guidance="同一业务任务此前的外部操作尚未核实，继续或更换参数不会重发。其他独立工具仍可继续。")
            blocked.error_summary = "同一业务任务此前的外部操作结果尚未核实，已阻止重复执行；请先核实原操作。"
        return blocked
    return None


def summarize_dispatches(db, run):
    """给宿主提供是否曾派发的安全摘要，不包含工具参数或原始业务结果。"""
    summary = {"prepared": 0, "dispatched": 0, "legacy_unstaged": 0, "has_execution_trace": False}
    for call in _calls(db, run):
        state = journal(call)
        result = call.result_json if isinstance(call.result_json, dict) else {}
        metadata = result.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("operationOutcome") == "uncertain":
            summary["dispatched"] += 1
        elif state and state["phase"] == "prepared":
            summary["prepared"] += 1
        elif state:
            summary["dispatched"] += 1
        else:
            summary["legacy_unstaged"] += 1
    summary["has_execution_trace"] = bool(summary["dispatched"] or summary["legacy_unstaged"])
    return summary


def unconfirmed_operations(db, run) -> int:
    """同任务续办仍核对可信未知回执；终态 failed 或新Run不等于原外部操作已核实。"""
    from .replay_guard import unconfirmed_replay_result

    count = 0
    for call in _task_calls(db, run):
        result = call.result_json if isinstance(call.result_json, dict) else {}
        metadata = result.get("metadata") or {}
        state = journal(call)
        if not isinstance(metadata, dict) or state and state.get("externalSideEffect") is False:
            continue
        if (call.status == "failed" and result.get("error_code") == "tool_interrupted_unknown"
                and metadata.get("receiptOrigin") == "legacy_unstaged"
                and metadata.get("operationOutcome") == "unknown" and state is None):
            # 旧版没有provider可回查时也不能声称已完成；保留原未核实事实，不猜是否已发送。
            count += 1
            continue
        provider = metadata.get("provider") or (state or {}).get("provider")
        if unconfirmed_replay_result(run_id=call.run_id, tool_name=call.tool_name, provider=provider,
                                     previous_calls=[call]) is not None:
            count += 1
    return count


def reconcile_dispatches(db, run):
    """在执行器已停止或持有运行工具锁的终态边界收尾；事务由调用方提交。"""
    counts = {"not_dispatched": 0, "unknown": 0, "read_interrupted": 0}
    now = datetime.utcnow()
    for call in list(_calls(db, run)):
        if call.status not in UNFINISHED_CALLS:
            continue
        state = journal(call)
        if state and state["phase"] == "prepared":
            result = ToolResult(status="failed", error_code="tool_not_dispatched", error_summary="工具在派发前中断，没有发出本次调用。",
                metadata={"dispatch": state, "operationOutcome": "not_sent", "retryable": True})
            counts["not_dispatched"] += 1
        elif state and state.get("externalSideEffect") is True:
            result = unfinished_external_result(call, state["provider"])
            counts["unknown"] += 1
        else:
            result = ToolResult(status="failed", error_code="tool_interrupted" if state else "tool_interrupted_unknown",
                error_summary="工具执行已中断。" if state else "旧调用没有派发阶段和最终回执，无法确定此前执行结果。",
                metadata={"dispatch": state} if state else {"receiptOrigin": "legacy_unstaged", "operationOutcome": "unknown"})
            counts["read_interrupted" if state else "unknown"] += 1
        db.execute(update(ToolCall.__table__).where(ToolCall.id == call.id, ToolCall.workspace_id == run.workspace_id,
            ToolCall.status.in_(UNFINISHED_CALLS)).values(status="failed", result_json=result.to_dict(),
                error_code=result.error_code, error_summary=result.error_summary, completed_at=now))
        if call.step_id:
            db.execute(update(RunStep.__table__).where(RunStep.id == call.step_id, RunStep.run_id == run.id,
                RunStep.status.in_(["pending", "running"])).values(status="failed", error_code=result.error_code,
                    error_summary=result.error_summary, completed_at=now))
    return counts
