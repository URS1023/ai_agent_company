"""规范工具回执：末段与业务台账共同提交，恢复仅重查授权与已存结果。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select

from app.common.json_numbers import preserve_json_integers
from app.errors import ConflictError, ValidationError
from app.models import Message, Run, RunEvent, RunStep, ToolCall
from app.providers.tool.gateway import ToolGateway, build_context
from .context import session

PHASES = {"execution", "pre_dispatch_rejection"}
SAFE_REJECTIONS = {
    "run_already_settled": "本次运行已经结束，不能发起新的工具执行。",
    "tool_not_available": "当前业务插件未提供此工具。",
}


def identity(run, call_id, invocation_seq, native_agent_session_id=None):
    """身份来自宿主原生事件；参数摘要另行核验，不能靠换参数取得第二次执行。"""
    from .chat import session_identity
    root = session_identity(run.workspace_id, run.started_by_user_id, run.conversation_id)
    agent = native_agent_session_id if native_agent_session_id is not None else root
    if (not isinstance(call_id, str) or not call_id or len(call_id) > 200
            or not isinstance(agent, str) or not agent or len(agent) > 200
            or type(invocation_seq) is not int or invocation_seq < 0):
        raise ValidationError("工具回执身份无效")
    return {"run_id": run.id, "call_id": call_id, "invocation_seq": invocation_seq,
            "native_agent_session_id": agent, "session_id": root,
            "workspace_id": run.workspace_id, "user_id": run.started_by_user_id}


def fingerprint(name, arguments):
    """固定工具名和参数的完整语义，不保存额外密钥副本。"""
    return hashlib.sha256(json.dumps([name, arguments], ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def key_for(owner, phase):
    if phase not in PHASES:
        raise ValidationError("工具回执阶段无效")
    return hashlib.sha256(json.dumps([owner, phase], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rows_for_run(db, run):
    """内部台账固定运行与发起人，不因展示域过滤产生虚假缺失。"""
    events, runs = RunEvent.__table__, Run.__table__
    return select(events).join(runs, events.c.run_id == runs.c.id).where(
        events.c.run_id == run.id, events.c.workspace_id == run.workspace_id,
        runs.c.workspace_id == run.workspace_id, runs.c.started_by_user_id == run.started_by_user_id)


def view(row):
    payload = row.payload
    return {"id": row.id, "sequence": row.sequence, "run_id": row.run_id,
            "session_id": payload["data"]["session_id"], "event": payload["event"],
            "data": payload["data"], "identity": payload["receipt"]["identity"],
            "phase": payload["receipt"]["phase"], "receipt_key": row.receipt_key}


def envelope(row):
    return {"receipt_protocol": 1, "state": "committed", "receipt": view(row),
            "result": row.payload["data"]["result"]}


def project(db, run, owner, name, arguments, result):
    """执行结果和安全派发前拒绝都在同一末段事务落消息投影。"""
    from app.core.parts import artifact_part, tool_call_part, tool_result_part
    message = db.get(Message, run.context_snapshot["assistant_message_id"], populate_existing=True)
    call_id = owner["call_id"]
    preserved = [part for part in message.parts_json or [] if not (
        part.get("id") == call_id and part.get("type") in {"tool_call", "tool_result"})]
    message.parts_json = preserved + [{**tool_call_part(call_id, name, arguments), "status": result["status"]},
        tool_result_part(call_id, name, result)] + [artifact_part(item) for item in result.get("artifacts") or []]


def find(db, run, owner, name, arguments):
    """阶段独立寻址，未知阶段绝不当作成功执行回执。"""
    for candidate in db.execute(rows_for_run(db, run).where(RunEvent.__table__.c.receipt_key.is_not(None))).all():
        saved = candidate.payload.get("receipt") or {}
        if (candidate.payload.get("data", {}).get("call_id") == owner["call_id"]
                and saved.get("identity") != owner):
            raise ConflictError("调用标识已属于不同原生身份，不能覆盖消息投影")
    found = db.execute(rows_for_run(db, run).where(RunEvent.__table__.c.receipt_key.in_(
        [key_for(owner, phase) for phase in PHASES]))).first()
    if found:
        saved = found.payload.get("receipt") or {}
        if saved.get("identity") != owner or saved.get("fingerprint") != fingerprint(name, arguments):
            raise ConflictError("同一工具回执身份不能更换工具或参数")
        if saved.get("phase") not in PHASES or found.receipt_key != key_for(owner, saved["phase"]):
            raise ConflictError("工具回执阶段与身份不匹配")
    return found


def persist(db, run, owner, name, arguments, result, *, phase="execution", tool_call_id=None):
    """调用方持有运行事件锁；只 flush，不独立 commit，避免成功台账与事件撕裂。"""
    existing = find(db, run, owner, name, arguments)
    if existing:
        if existing.payload["receipt"]["phase"] != phase or existing.payload["data"]["result"] != result:
            raise ConflictError("规范工具回执不可覆盖")
        return existing
    snapshot = run.context_snapshot or {}
    body = {"run_id": run.id, "session_id": owner["session_id"], "message_id": snapshot["assistant_message_id"],
            "call_id": owner["call_id"], "tool_name": name, "result": result, "status": result["status"],
            "agent_session_id": owner["native_agent_session_id"]}
    events = RunEvent.__table__
    sequence = int(db.scalar(select(func.max(events.c.sequence)).where(events.c.run_id == run.id)) or 0) + 1
    row = RunEvent(workspace_id=run.workspace_id, run_id=run.id, event_type="chat.stream", sequence=sequence,
        receipt_key=key_for(owner, phase), created_at=datetime.utcnow(), payload={"event": "tool.completed", "data": body,
        "receipt": {"version": 1, "identity": owner, "phase": phase, "fingerprint": fingerprint(name, arguments),
                    "tool_call_id": tool_call_id}})
    db.add(row)
    db.flush()
    return row


def rejection_result(code):
    """只有服务端固定的派发前拒绝可以在缺失工具规格时安全读取。"""
    from app.providers.tool.base import ToolResult
    return ToolResult(status="failed", error_code=code, error_summary=SAFE_REJECTIONS[code],
                      metadata={"operationOutcome": "not_sent", "retryable": False}).to_dict()


def execution_identity(db, call):
    """旧调用键保持不变；额外核验步骤里的完整身份以隔离根代理与子代理。"""
    steps = RunStep.__table__
    meta = db.scalar(select(steps.c.metadata_json).where(steps.c.id == call.step_id,
        steps.c.run_id == call.run_id, steps.c.workspace_id == call.workspace_id))
    return (meta or {}).get("receipt_identity")


def authorize(db, run, actor, row, name, arguments):
    """只读当前授权；拒绝读取不改写已有成功事实或伪称原操作尚未派发。"""
    saved = row.payload["receipt"]
    if saved["phase"] == "pre_dispatch_rejection":
        result = row.payload["data"]["result"]
        code = result.get("error_code")
        if code in SAFE_REJECTIONS and result == rejection_result(code) and saved.get("tool_call_id") is None:
            return None
        raise ConflictError("派发前控制回执包含非可信载荷")
    calls = ToolCall.__table__
    call = db.execute(select(calls).where(calls.c.id == saved.get("tool_call_id"),
        calls.c.run_id == run.id, calls.c.workspace_id == run.workspace_id)).first()
    if not call or call.status not in {"completed", "failed"} or call.result_json != row.payload["data"]["result"]:
        raise ConflictError("规范回执与工具台账不一致")
    snapshot = run.context_snapshot or {}
    context = build_context(db=db, workspace_id=run.workspace_id, user_id=run.started_by_user_id, run_id=run.id,
        conversation_id=run.conversation_id, attached_file_ids=snapshot.get("file_ids") or [],
        selected_datasource_ids=snapshot.get("datasource_ids") or [],
        selected_dashboard_template_id=snapshot.get("dashboard_template_id"),
        selected_dashboard_template_mode=snapshot.get("dashboard_template_mode") or "auto")
    context.update(auth_context=actor, task_id=snapshot.get("task_id"), execution_owner="dsh")
    from . import registry
    from app.providers.tool.base import ToolResult
    gateway = ToolGateway(db)
    names = registry.tool_names(snapshot.get("skill_ids"))
    selected = next((spec for spec in gateway.list_available(run.workspace_id, execution_context=context)
        if spec.name == name and spec.provider != "sandbox" and (name in names or (
            not snapshot.get("skill_ids") and spec.provider in {"http", "mcp", "database"}))), None)
    if selected is None:
        return ToolResult(status="failed", error_code="tool_not_available", error_summary="当前业务插件未提供此工具。")
    return gateway.authorize_replay(run.workspace_id, name, arguments, context,
        tool_version_id=call.tool_version_id, saved_result=call.result_json)


def get(run_id, call_id, invocation_seq, name, arguments, native_agent_session_id=None):
    """独立 Session 查询，不发起 provider、补写旧事件、提交事务或改变运行状态。"""
    from .chat import get_run, run_actor
    from .tools import _saved_tool_calls
    with session() as db:
        run = get_run(db, run_id)
        actor = run_actor(db, run)
        owner = identity(run, call_id, invocation_seq, native_agent_session_id)
        row = find(db, run, owner, name, arguments)
        if row:
            denied = authorize(db, run, actor, row, name, arguments)
            if denied:
                return {"receipt_protocol": 1, "state": "access_denied", "error": denied.error_code}
            return preserve_json_integers(envelope(row))
        old_key = "dsh:" + hashlib.sha256(f"{owner['session_id']}:{invocation_seq}:{call_id}".encode()).hexdigest()
        previous = _saved_tool_calls(db, run, idempotency_key=old_key).first()
        if previous:
            if previous.tool_name != name or previous.arguments_json != arguments:
                raise ConflictError("同一原生调用不能更换工具或参数")
            saved_owner = execution_identity(db, previous)
            if (saved_owner and saved_owner != owner) or (not saved_owner and owner["native_agent_session_id"] != owner["session_id"]):
                raise ConflictError("旧调用键对应其他原生代理身份")
            return {"receipt_protocol": 1, "state": "legacy" if previous.status in {"completed", "failed"} else "unknown"}
        return {"receipt_protocol": 1, "state": "absent"}
