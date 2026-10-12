"""执行确定性业务工具并保存回执，普通失败不冻结后续对话。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select

from app.core.parts import artifact_part, tool_call_part, tool_result_part
from app.errors import AppError, ConflictError
from app.models import Artifact, Message, Run, RunStep, ToolCall
from app.providers.tool.base import ToolResult
from app.providers.tool.gateway import ToolGateway, build_context
from app.modules.chat_artifacts.artifacts import persist_artifacts
from app.modules.runtime.service import start_step, record_tool_call, finish_tool_call, complete_step, fail_step
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout
from .context import session, run_context, lock_for
from .chat import get_run, run_actor, session_identity
from . import registry, transport, receipts
from .dispatch_state import (TERMINAL_RUNS, accesses_datasource, has_external_side_effect, journal, prepared_result,
                             start_dispatch, unfinished_external_result, unconfirmed_task_replay_result,
                             task_datasource_halted)

def classification(name, result, provider="", *, execution_owner=None):
    """按明确故障来源返回恢复动作，不能用 generic code 推断数据库异常。"""
    if result.status == "success":
        return {"outcome": "succeeded", "recovery": "none"}
    code, summary = result.error_code or "tool_failed", result.error_summary or ""
    if code in {"chat_cancelled", "chat_timeout", "chat_task_timeout"}:
        return {"category": "cancelled", "outcome": "failed", "recovery": "none"}
    if code.startswith("model_") or any(word in summary for word in ("模型服务", "模型调用", "模型额度", "模型连接")):
        return {"category": "model", "outcome": "failed", "recovery": "correct_input"}
    database_failure = accesses_datasource(name, provider, execution_owner=execution_owner) and ((result.metadata or {}).get("failure_domain") == "datasource" or code.startswith(("database_", "db_", "query_timeout")) or
        any(word in summary.lower() for word in ("数据库超时", "数据库连接", "断连", "operationalerror", "connection reset")))
    if database_failure:
        return {"category": "datasource", "outcome": "failed", "recovery": "stop_datasource"}
    if code in {"tool_result_unconfirmed", "operation_unknown"}:
        return {"category": "transport", "outcome": "unknown", "recovery": "reconcile_operation"}
    return {"category": "validation" if "validation" in code else "rendering" if name.startswith(("render_", "finalize_", "write_presentation")) else "tool",
            "outcome": "failed", "recovery": "correct_input"}


def _saved_tool_calls(db, run, *, idempotency_key=None, failed_tool=None):
    """按已验证运行的归属读取内部回执，不把 runs.view 展示权限当作幂等记录的可见开关。

    使用显式表查询避开展示域 ORM 过滤，但仍固定运行、工作区与发起人；返回回执前另走当前工具/资源鉴权。
    """
    calls, runs = ToolCall.__table__, Run.__table__
    statement = select(calls).join(runs, runs.c.id == calls.c.run_id).where(
        calls.c.run_id == run.id, calls.c.workspace_id == run.workspace_id,
        runs.c.workspace_id == run.workspace_id, runs.c.started_by_user_id == run.started_by_user_id)
    if idempotency_key is not None:
        statement = statement.where(calls.c.idempotency_key == idempotency_key)
    if failed_tool is not None:
        statement = statement.where(calls.c.tool_name == failed_tool,
            calls.c.status.in_(["failed", "prepared", "dispatch_started", "running", "pending"]))
    return db.execute(statement)


def execute(run_id, call_id, invocation_seq, name, arguments, native_agent_session_id=None, durable_receipt=False):
    """同一持久调用按当前授权回放；外部结果未知时阻断同任务同工具，不影响独立工具。"""
    from app.common.json_numbers import preserve_json_integers
    with lock_for("tool:" + run_id), session() as db:
        run = get_run(db, run_id)
        actor = run_actor(db, run)
        snapshot = run.context_snapshot or {}
        owner = receipts.identity(run, call_id, invocation_seq, native_agent_session_id)
        canonical = receipts.find(db, run, owner, name, arguments)
        if canonical:
            denied = receipts.authorize(db, run, actor, canonical, name, arguments)
            if denied:
                return ({"receipt_protocol": 1, "state": "access_denied", "error": denied.error_code}
                        if durable_receipt else denied.to_dict())
            return receipts.envelope(canonical) if durable_receipt else canonical.payload["data"]["result"]

        def reject_before_dispatch(code):
            result = receipts.rejection_result(code)
            with lock_for("events:" + run_id):
                receipts.project(db, run, owner, name, arguments, result)
                row = receipts.persist(db, run, owner, name, arguments, result, phase="pre_dispatch_rejection")
                db.commit()
                return receipts.envelope(row) if durable_receipt else result

        def unconfirmed(result):
            # 旧回执没有规范事件，不能在本次读取时伪造与原业务提交原子的证明。
            return {"receipt_protocol": 1, "state": "unknown", "error": result.error_code} if durable_receipt else result.to_dict()
        identity = session_identity(run.workspace_id, run.started_by_user_id, run.conversation_id)
        key = "dsh:" + hashlib.sha256(f"{identity}:{invocation_seq}:{call_id}".encode()).hexdigest()
        previous = _saved_tool_calls(db, run, idempotency_key=key).first()
        if previous:
            if previous.tool_name != name or previous.arguments_json != arguments:
                raise ConflictError("同一原生调用不能更换工具或参数")
            saved_owner = receipts.execution_identity(db, previous)
            if (saved_owner and saved_owner != owner) or (not saved_owner and owner["native_agent_session_id"] != owner["session_id"]):
                raise ConflictError("旧调用键不能用于不同或未证明的原生代理身份")
        elif run.status in TERMINAL_RUNS:
            return reject_before_dispatch("run_already_settled")
        context = build_context(db=db, workspace_id=run.workspace_id, user_id=run.started_by_user_id, run_id=run.id,
            conversation_id=run.conversation_id, attached_file_ids=snapshot.get("file_ids") or [],
            selected_datasource_ids=snapshot.get("datasource_ids") or [],
            selected_dashboard_template_id=snapshot.get("dashboard_template_id"),
            selected_dashboard_template_mode=snapshot.get("dashboard_template_mode") or "auto")
        context["auth_context"] = actor
        context["task_id"] = snapshot.get("task_id")
        # provider 需要同一持久调用的幂等键；它只放入请求头，不进入参数或响应正文。
        context["idempotency_key"] = key
        # 此标志由唯一业务 Worker 写入，兼容未保存 chat_engine 标记的既有会话。
        context["execution_owner"] = "dsh"
        names = registry.tool_names(snapshot.get("skill_ids"))
        gateway = ToolGateway(db)
        specs = gateway.list_available(run.workspace_id, execution_context=context)
        selected = next((spec for spec in specs if spec.name == name and spec.provider != "sandbox" and
                         (name in names or (not snapshot.get("skill_ids") and spec.provider in {"http", "mcp", "database"}))), None)
        if selected is None:
            if previous:
                return unconfirmed(ToolResult(status="failed", error_code="tool_not_available", error_summary="当前业务插件未提供此工具。"))
            return reject_before_dispatch("tool_not_available")
        if previous:
            if previous.result_json is not None and previous.status in {"completed", "failed"}:
                # 幂等只防止重复执行，不延续已撤销的资源权限；拒绝回放时保留原始回执。
                denied = gateway.authorize_replay(run.workspace_id, name, arguments, context,
                    tool_version_id=previous.tool_version_id, saved_result=previous.result_json)
                if durable_receipt:
                    return {"receipt_protocol": 1, "state": "access_denied" if denied else "legacy"}
                return denied.to_dict() if denied else preserve_json_integers(previous.result_json)
            if previous.status == "prepared" and (journal(previous) or {}).get("phase") == "prepared":
                return unconfirmed(ToolResult(status="failed", error_code="tool_not_dispatched", error_summary="此前调用在发送前中断，可以在当前有效运行中重新发起。",
                                  metadata={"operationOutcome": "not_sent", "retryable": True}))
            return unconfirmed(ToolResult(status="failed", error_code="operation_unknown", error_summary="这次操作结果尚未核实，不能重复执行。",
                              metadata={"outcome": "unknown", "recovery": "reconcile_operation"}))
        context["allow_network"] = selected.provider in {"http", "mcp"}
        # 内部步骤序号同样不受 runs.view 展示权限影响，避免受限角色产生重复序号。
        steps = RunStep.__table__
        sequence = int(db.scalar(select(func.max(steps.c.sequence)).where(
            steps.c.run_id == run.id, steps.c.workspace_id == run.workspace_id)) or 0) + 1
        step = start_step(db, workspace_id=run.workspace_id, run_id=run.id, sequence=sequence, step_type="tool", name=name, input_data=arguments)
        step.metadata_json = {**(step.metadata_json or {}), "receipt_identity": owner}
        call = record_tool_call(db, workspace_id=run.workspace_id, run_id=run.id, step_id=step.id,
                                tool_name=name, tool_version_id=getattr(selected, "_tool_version_id", None), arguments=arguments)
        call.idempotency_key = key
        call.status = "prepared"
        call.result_json = prepared_result(call, selected)
        db.commit()
        # 回调仅由网关/provider 使用；参数来自服务端配置，模型不能声明已派发或只读。
        context["_before_tool_dispatch"] = lambda *, provider, external_side_effect: start_dispatch(
            db, run, call, provider=provider, external_side_effect=external_side_effect)
        with run_context(run, sink=lambda event, data: transport.progress(run.id, event, data)):
            try:
                from app.modules.runtime.task_control import checkpoint
                checkpoint()
                blocked_replay = unconfirmed_task_replay_result(db, run, tool_name=name,
                    provider=selected.provider) if has_external_side_effect(selected) else None
                if blocked_replay is not None:
                    # 阻断也按本次调用正常落回执；更换原生调用标识不能重发结果未知的外部操作。
                    result = blocked_replay
                elif accesses_datasource(name, selected.provider, execution_owner=context["execution_owner"]) and (
                        snapshot.get("database_access_halted") or task_datasource_halted(db, run)):
                    result = ToolResult(status="failed", error_code="database_access_halted",
                                        error_summary="同一任务的数据源访问已因异常停止；请先核实异常与原查询状态，再明确发起新任务。")
                else:
                    result = gateway.execute(run.workspace_id, name, arguments, context)
                    persist_artifacts(db, run, name, result, {})
            except ChatCancelled as exc:
                result = ToolResult(status="cancelled", error_code="chat_cancelled", error_summary=str(exc))
            except ChatTaskTimeout as exc:
                result = ToolResult(status="failed", error_code="chat_task_timeout", error_summary=str(exc))
            except AppError as exc:
                result = ToolResult(status="failed", error_code=exc.code, error_summary=exc.message,
                                    metadata={"failure_domain": exc.extra.get("failure_domain")})
            except Exception as exc:
                db.rollback()
                result = ToolResult(status="failed", error_code="tool_internal_error", error_summary=f"工具处理失败：{type(exc).__name__}")
        if result.status not in {"success", "failed", "cancelled"}:
            result.status, result.error_code = "failed", "tool_result_unconfirmed"
            result.error_summary = "工具仅返回受理状态，尚未确认业务结果。"
        if result.error_code in {"tool_internal_error", "tool_result_unconfirmed"}:
            # 派发后的本地处理错误不能降级为可修参数重试的普通失败。
            uncertain = unfinished_external_result(call, selected.provider)
            if uncertain and not (result.metadata or {}).get("operationOutcome"):
                result = uncertain
        result.metadata = {**(result.metadata or {}), **classification(name, result, selected.provider,
                           execution_owner=context["execution_owner"]), "task_id": snapshot.get("task_id"),
                           "source_run_id": run.id, "call_id": call_id, "invocation_seq": invocation_seq}
        state = journal(call)
        if state:
            result.metadata["dispatch"] = state
        if result.metadata.get("recovery") == "stop_datasource":
            run.context_snapshot = {**(run.context_snapshot or {}), "database_access_halted": True}
        for reference in result.artifacts:
            row = db.get(Artifact, reference.get("id"))
            if row:
                row.metadata_json = {**(row.metadata_json or {}), "task_id": snapshot.get("task_id"), "source_run_id": row.run_id}
                reference.update(task_id=snapshot.get("task_id"), source_run_id=row.run_id, artifact_id=row.id, status=row.status)
        if result.status == "success" and result.artifacts:
            result.metadata["delivery"] = {"already_presented": True, "channel": "artifact_cards",
                "guidance": "工件已自动显示为可下载文件卡片，无需再调用 present。download_url 是下载接口，不是本地路径；list_files 只列文件库，不包含这些工件，无需 glob 或 shell 查找。"}
        elif result.status == "success" and name in {"write_file", "copy_file", "rename_file"} and isinstance(result.data, dict) and result.data.get("file_id"):
            result.data["download_url"] = f"/api/files/{result.data['file_id']}/download"
            result.metadata["delivery"] = {"channel": "file_library", "guidance": "文件已保存到文件库，可直接引用 download_url；它不在原生会话目录，无需 present 或搜索本地路径。"}
        # 原始整数以同一精确文本进入持久回执和宿主，避免 Node 回调覆盖出不同数值。
        result_value = preserve_json_integers(result.to_dict())
        finish_tool_call(db, call, result_value, error_code=result.error_code if result.status != "success" else None,
                         error_summary=result.error_summary)
        if result.status == "success":
            complete_step(db, step, result_value)
        else:
            fail_step(db, step, result.error_code or "tool_failed", result.error_summary or "工具未完成")
        with lock_for("events:" + run_id):
            receipts.project(db, run, owner, name, arguments, result_value)
            row = receipts.persist(db, run, owner, name, arguments, result_value, tool_call_id=call.id)
            db.commit()
            return receipts.envelope(row) if durable_receipt else result_value
