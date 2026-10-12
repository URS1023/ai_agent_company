"""真实 Worker 提交后丢回复的隔离回执合同，不重复调用工具冒充丢包。"""
from __future__ import annotations

import io
import json

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools, receipts
from app.business_worker.context import session
from app.business_worker.protocol import JsonRpc
from app.models import Message, Run, RunEvent, RunStep, ToolCall, User
from app.errors import AuthError, ConflictError
from test_business_worker import prepare, worker_context  # noqa: F401
from test_tool_replay_authorization import replay_actor, grant  # noqa: F401

REAL_GATEWAY_METHODS = {name: getattr(tools.ToolGateway, name) for name in ("execute", "_dispatch", "_prepare_secrets")}


def lose_reply_after_real_commit(worker_context):
    """只丢一个真实 result 包，丢包前从独立 Session 确认工具结果已经提交。"""
    prepared = prepare(worker_context)
    run_id = prepared["run_id"]
    call_id = "receipt-clock"
    chat.append_event(run_id, "tool.started", {"call_id": call_id, "tool_name": "current_datetime", "arguments": {}}, 1)
    state = {"dropped": [], "wire": [], "handler_calls": 0}

    class Sink:
        """短注入只影响私有协议回包，不替换业务、commit 或持久数据。"""
        def write(self, raw):
            packet = json.loads(raw)
            if "result" in packet and not state["dropped"]:
                with session() as db:
                    saved = db.scalars(select(ToolCall).where(ToolCall.run_id == run_id)).one()
                    assert saved.status == "completed"
                    assert saved.result_json["status"] == "success"
                    state["committed_result"] = saved.result_json
                state["dropped"].append(packet)
                raise BrokenPipeError("受控：真实提交后的result回复丢失")
            state["wire"].append(packet)

        def flush(self):
            pass

    rpc = JsonRpc(io.StringIO(), Sink())
    def handle(method, params):
        assert method == "tool.execute"
        state["handler_calls"] += 1
        return tools.execute(**params)
    rpc.handler = handle
    try:
        rpc.dispatch({"jsonrpc": "2.0", "id": "host-fixture", "method": "tool.execute",
                      "params": {"run_id": run_id, "call_id": call_id, "invocation_seq": 1,
                                 "name": "current_datetime", "arguments": {}}})
    finally:
        for executor in (rpc.executor, rpc.tool_executor, rpc.control_executor):
            executor.shutdown(wait=True)
    assert state["handler_calls"] == 1 and len(state["dropped"]) == 1
    assert len(state["wire"]) == 1 and "error" in state["wire"][0]
    print("DURABLE_ACK_RAW " + json.dumps(state, ensure_ascii=False, default=str))
    return prepared, call_id, state


def test_committed_result_has_canonical_event_after_lost_reply(worker_context):
    """实际丢失回复后，规范完成事件必须已经与结果共同提交。"""
    prepared, call_id, state = lose_reply_after_real_commit(worker_context)
    with session() as db:
        events = db.scalars(select(RunEvent).where(RunEvent.run_id == prepared["run_id"])).all()
        results = [row for row in events if row.payload.get("event") == "tool.completed"
                   and row.payload.get("data", {}).get("call_id") == call_id]
        print("DURABLE_ACK_EVENTS " + json.dumps([row.payload for row in events], ensure_ascii=False))
        assert len(results) == 1
        assert results[0].payload["data"]["result"] == state["committed_result"]


def test_lost_reply_failure_projection_cannot_replace_committed_success(worker_context):
    """模拟当前 Host 无回执分支，真实事件入口不能把已提交成功覆成失败。"""
    prepared, call_id, state = lose_reply_after_real_commit(worker_context)
    chat.append_event(prepared["run_id"], "tool.failed", {"call_id": call_id,
        "tool_name": "current_datetime", "error": state["wire"][0]["error"]["message"]})
    with session() as db:
        run = db.get(Run, prepared["run_id"])
        message = db.get(Message, run.context_snapshot["assistant_message_id"])
        result = next(part for part in message.parts_json if part.get("type") == "tool_result" and part.get("id") == call_id)
        print("DURABLE_ACK_MESSAGE " + json.dumps(result, ensure_ascii=False))
        assert result["result"]["status"] == "success"


def call_params(prepared, **extra):
    return {"run_id": prepared["run_id"], "call_id": "durable-clock", "invocation_seq": 17,
            "name": "current_datetime", "arguments": {}, "native_agent_session_id": prepared["session_id"], **extra}


def test_lookup_is_read_only_and_preserves_canonical_cursor(worker_context, monkeypatch):
    """独立 Session 可恢复同一事件；禁止 lookup 偷偷走 execute/provider。"""
    prepared = prepare(worker_context)
    params = call_params(prepared)
    committed = tools.execute(**params, durable_receipt=True)
    monkeypatch.setattr(tools.ToolGateway, "execute", lambda *a, **kw: pytest.fail("恢复不能执行 provider"))
    with session() as db:
        before = [tuple(row) for row in db.execute(select(RunEvent.__table__)).all()]
    assert receipts.get(**params) == committed
    assert receipts.get(**params) == committed
    assert tools.execute(**params, durable_receipt=True) == committed
    with session() as db:
        assert [tuple(row) for row in db.execute(select(RunEvent.__table__)).all()] == before
        assert len(db.scalars(select(ToolCall)).all()) == 1


def test_receipt_identity_and_phase_cannot_alias(worker_context):
    """改参数、子代理同旧键、同显示ID不同序号都拒绝；不泄露成功载荷。"""
    prepared = prepare(worker_context)
    params = call_params(prepared)
    result = tools.execute(**params, durable_receipt=True)
    for changed in ({"arguments": {"changed": True}}, {"native_agent_session_id": "child"}, {"invocation_seq": 18}):
        with pytest.raises(ConflictError):
            receipts.get(**{**params, **changed})
        with pytest.raises(ConflictError):
            tools.execute(**{**params, **changed}, durable_receipt=True)
    with pytest.raises(ConflictError):
        chat.append_event(prepared["run_id"], "tool.failed", {"call_id": params["call_id"],
            "agent_session_id": "child", "invocation_seq": params["invocation_seq"]})
    with session() as db:
        row = db.get(RunEvent, result["receipt"]["id"])
        row.payload = {**row.payload, "receipt": {**row.payload["receipt"], "phase": "forged"}}
        db.commit()
    with pytest.raises(ConflictError):
        receipts.get(**params)


def test_pre_dispatch_rejection_has_atomic_projection_and_safe_lookup(worker_context, monkeypatch):
    """不可用工具的安全拒绝也可恢复；刷新历史不能遗留 running。"""
    prepared = prepare(worker_context)
    params = call_params(prepared, name="not_registered")
    chat.append_event(prepared["run_id"], "tool.started", {"call_id": params["call_id"], "tool_name": params["name"],
        "agent_session_id": prepared["session_id"], "invocation_seq": params["invocation_seq"]})
    result = tools.execute(**params, durable_receipt=True)
    assert result["receipt"]["phase"] == "pre_dispatch_rejection"
    assert result["result"]["metadata"]["operationOutcome"] == "not_sent"
    monkeypatch.setattr(tools.ToolGateway, "execute", lambda *a, **kw: pytest.fail("安全拒绝不执行 provider"))
    assert receipts.get(**params) == result
    with session() as db:
        assert not db.scalars(select(ToolCall)).all()
        message = db.get(Message, prepared["message_id"])
        assert next(part for part in message.parts_json if part["type"] == "tool_call")["status"] == "failed"
        assert next(part for part in message.parts_json if part["type"] == "tool_result")["result"] == result["result"]
        row = db.get(RunEvent, result["receipt"]["id"])
        row.payload = {**row.payload, "data": {**row.payload["data"], "result": {**result["result"], "data": {"secret": 1}}}}
        db.commit()
    with pytest.raises(ConflictError):
        receipts.get(**params)


def test_current_actor_revocation_cannot_read_or_change_success(worker_context):
    prepared = prepare(worker_context)
    params = call_params(prepared)
    result = tools.execute(**params, durable_receipt=True)
    with session() as db:
        run = db.get(Run, prepared["run_id"])
        db.get(User, run.started_by_user_id).status = "disabled"
        db.commit()
    with pytest.raises(AuthError):
        receipts.get(**params)
    with session() as db:
        assert db.get(RunEvent, result["receipt"]["id"]).payload["data"]["result"] == result["result"]
        assert db.scalars(select(ToolCall)).one().status == "completed"


def test_lookup_resource_denial_is_control_only(worker_context, monkeypatch):
    """当前资源校验拒绝只影响读取，不改写成功事件、ToolCall 或 Message。"""
    from app.providers.tool.base import ToolResult
    prepared = prepare(worker_context)
    params = call_params(prepared)
    result = tools.execute(**params, durable_receipt=True)
    monkeypatch.setattr(tools.ToolGateway, "authorize_replay", lambda *a, **kw: ToolResult(
        status="failed", error_code="replay_authorization_required", error_summary="受控资源撤权"))
    assert receipts.get(**params) == {"receipt_protocol": 1, "state": "access_denied", "error": "replay_authorization_required"}
    assert tools.execute(**params, durable_receipt=True)["state"] == "access_denied"
    with session() as db:
        assert db.scalars(select(ToolCall)).one().result_json == result["result"]
        assert db.get(RunEvent, result["receipt"]["id"]).payload["data"]["result"] == result["result"]
        assert next(p for p in db.get(Message, prepared["message_id"]).parts_json if p["type"] == "tool_result")["result"] == result["result"]


def test_final_transaction_failure_keeps_unconfirmed_dispatch(worker_context, monkeypatch):
    """规范事件 flush 后注入最终事务失败，不能留下假的已完成台账或消息。"""
    prepared = prepare(worker_context)
    params = call_params(prepared)
    original = receipts.persist
    def fail_after_event(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("受控最终事务失败")
    monkeypatch.setattr(receipts, "persist", fail_after_event)
    with pytest.raises(RuntimeError, match="受控最终事务失败"):
        tools.execute(**params, durable_receipt=True)
    with session() as db:
        call = db.scalars(select(ToolCall)).one()
        assert call.status == "dispatch_started"
        assert call.result_json["status"] == "running"
        assert not db.scalars(select(RunEvent).where(RunEvent.receipt_key.is_not(None))).all()
        assert not any(p["type"] == "tool_result" for p in db.get(Message, prepared["message_id"]).parts_json)
    assert receipts.get(**params)["state"] == "unknown"


def test_legacy_completed_call_is_not_backfilled_or_reexecuted(worker_context, monkeypatch):
    """旧键无原子事件时保留 legacy，不能靠第二次 invoke 补造原始提交证明。"""
    prepared = prepare(worker_context)
    params = call_params(prepared)
    result = tools.execute(**params, durable_receipt=True)
    with session() as db:
        db.delete(db.get(RunEvent, result["receipt"]["id"]))
        step = db.scalars(select(RunStep)).one()
        step.metadata_json = {}
        db.commit()
    monkeypatch.setattr(tools.ToolGateway, "execute", lambda *a, **kw: pytest.fail("旧完成调用不能重发"))
    assert receipts.get(**params)["state"] == "legacy"
    assert tools.execute(**params, durable_receipt=True)["state"] == "legacy"
    with pytest.raises(ConflictError):
        tools.execute(**{**params, "native_agent_session_id": "child"}, durable_receipt=True)


def test_same_identity_started_cannot_disclose_file_after_real_grant_revocation(replay_actor, monkeypatch):
    """真实读取临时文件后删除资源授权；started/failed 防覆盖入口不提前泄露旧结果。"""
    from app.common.storage import get_storage
    from app.models import File
    actor = replay_actor
    for name, method in REAL_GATEWAY_METHODS.items():
        monkeypatch.setattr(tools.ToolGateway, name, method)
    secret = "撤权后不能由started事件披露的实际文件内容"
    key = "receipts/shared.txt"
    get_storage().put_bytes(key, secret.encode())
    row = File(id="receipt-shared-file", workspace_id=actor.workspace_id, owner_user_id="other-user", name="共享.txt",
        mime_type="text/plain", size=len(secret.encode()), storage_key=key, sha256="0" * 64, created_at=actor.stamp)
    actor.db.add(row)
    actor.db.commit()
    share = grant(actor, "files", row.id, ["view"])
    params = {"run_id": "replay-run", "call_id": "file-call", "invocation_seq": 7, "name": "read_file",
              "arguments": {"file_id": row.id, "include_images": False},
              "native_agent_session_id": chat.session_identity(actor.workspace_id, actor.user.id, "replay-conversation")}
    committed = tools.execute(**params, durable_receipt=True)
    assert committed["state"] == "committed" and committed["result"]["status"] == "success"
    assert secret in str(committed["result"])
    actor.db.delete(share)
    actor.db.commit()
    for event in ("tool.started", "tool.failed", "tool.completed"):
        ignored = chat.append_event("replay-run", event, {"call_id": "file-call", "tool_name": "read_file",
            "agent_session_id": params["native_agent_session_id"], "invocation_seq": 7})
        assert ignored["event"] == "tool.receipt.ignored"
        assert "result" not in ignored["data"] and secret not in str(ignored)
    assert receipts.get(**params)["state"] == "access_denied"
    assert tools.execute(**params, durable_receipt=True)["state"] == "access_denied"
    with session() as db:
        assert db.get(RunEvent, committed["receipt"]["id"]).payload["data"]["result"] == committed["result"]
        assert db.scalars(select(ToolCall)).one().status == "completed"


def test_native_event_collision_cannot_overwrite_real_business_success(worker_context):
    """真实业务成功后，native标记不能让同调用ID的失败事件绕过规范回执保护。"""
    prepared = prepare(worker_context)
    params = call_params(prepared)
    committed = tools.execute(**params, durable_receipt=True)
    assert committed["result"]["status"] == "success"
    returned = chat.append_event(prepared["run_id"], "tool.failed", {"call_id": params["call_id"],
        "tool_name": "native-fixture", "native": True, "error": "受控原生事件ID碰撞",
        "agent_session_id": params["native_agent_session_id"], "invocation_seq": params["invocation_seq"]})
    with session() as db:
        message = db.get(Message, prepared["message_id"])
        part = next(item for item in message.parts_json if item.get("type") == "tool_result" and item.get("id") == params["call_id"])
        print("DURABLE_NATIVE_COLLISION_RAW " + json.dumps({"committed": committed, "returned": returned, "message": part}, ensure_ascii=False))
        assert part["result"] == committed["result"]
        assert db.scalars(select(ToolCall)).one().status == "completed"
        assert returned["event"] == "tool.receipt.ignored" and "result" not in returned["data"]
