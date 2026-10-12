"""以隔离平台库验证提交身份在终态、重启与权限边界下不会重复执行业务。"""
from __future__ import annotations

import pytest
import socket
from sqlalchemy import select

from app.models import Conversation, Message, Run
from app.modules.chat import router
from app.modules.runtime import chat_jobs, chat_liveness, engine
from app.modules.runtime.service import create_message, create_run, finalize_run
from test_chat_unlimited_lifecycle import local_leases
from test_operations import user_id
from test_system_foundation import activate_user, create_role, create_user


def seed_submission(client, headers, db, *, status="completed", request_id="web-0123456789abcdef0123456789abcdef"):
    """直接建立已持久化的运行，模拟应用重启后没有任何内存任务的情况。"""
    cid = client.post("/api/chat/conversations", headers=headers, json={}).json()["id"]
    payload = router.SendRequest(content="查询本月业务概况", request_id=request_id)
    workspace = db.get(Conversation, cid).workspace_id
    run = create_run(db, workspace_id=workspace, conversation_id=cid, user_id=user_id(client, headers),
        context_snapshot={"client_request_id": request_id, "client_request_fingerprint": router._submission_fingerprint(payload)})
    create_message(db, workspace_id=workspace, conversation_id=cid, role="user", content=payload.content)
    finalize_run(db, run, status=status)
    db.commit()
    return cid, run, payload.model_dump(mode="json")


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_submission_replays_persisted_run_before_active_check(client, administrator, db, monkeypatch, status):
    cid, run, payload = seed_submission(client, administrator, db, status=status)
    assert run.id not in chat_jobs._jobs
    def forbidden(*args, **kwargs):
        raise AssertionError("已存在的提交不能再检查其它任务或启动执行")
    monkeypatch.setattr(router, "_active_conversation_run", forbidden)
    monkeypatch.setattr(chat_jobs, "start_job", forbidden)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json=payload)
    assert response.status_code == 200 and run.id in response.text
    assert ("event: run.completed" if status == "completed" else "event: run.failed") in response.text
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 1
    assert len(db.scalars(select(Message).where(Message.conversation_id == cid)).all()) == 1


@pytest.mark.parametrize("changed", [
    {"content": "查询另一家企业"}, {"file_ids": ["new-resource"]},
    {"model_profile": "different-model"}, {"plugin_inputs": {"customer": "different"}},
])
def test_active_submission_rejects_changed_request_or_resource(client, administrator, db, changed):
    """同 request_id 的载荷变化只在任务仍活跃时被 409 拒绝（评审 R16）。"""
    cid, run, payload = seed_submission(client, administrator, db)
    run.status = "running"
    run.context_snapshot = {**run.context_snapshot, "chatLease": {"host": socket.gethostname(), "runId": run.id}}
    db.commit()
    lease = chat_liveness.chat_lease(run.id)
    assert lease.acquire()
    try:
        response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json={**payload, **changed})
        assert response.status_code == 409 and run.id in response.text
        assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 1
    finally:
        lease.release()


def test_terminal_submission_allows_new_payload_with_same_request_id(client, administrator, db, monkeypatch):
    """已终态的历史 run 不永久占用 request_id：同标识可承载新载荷发起新任务（评审 R16）。"""
    from app.modules.runtime import engine
    cid, run, payload = seed_submission(client, administrator, db, status="completed")
    executed = []

    def complete(session, run, *args, **kwargs):
        executed.append(run.id)
        from app.modules.runtime.service import finalize_run
        finalize_run(session, run, status="completed")
        session.commit()
        yield {"type": "run.completed", "payload": {"status": "completed"}}

    monkeypatch.setattr(engine, "run_chat", complete)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={**payload, "content": "切换后的新任务内容"})
    assert response.status_code == 200
    assert executed and executed[0] != run.id
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 2
    # 同一个 request_id 现在绑定到新 run 的新指纹。
    new_run = db.scalar(select(Run).where(Run.conversation_id == cid, Run.id != run.id))
    assert (new_run.context_snapshot or {}).get("client_request_id") == payload["request_id"]


def test_same_request_id_same_payload_replays_even_when_terminal(client, administrator, db):
    """终态后同 request_id + 同载荷仍幂等重放原 run，不重复执行。"""
    cid, run, payload = seed_submission(client, administrator, db, status="completed")
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json=payload)
    assert response.status_code == 200 and run.id in response.text
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 1


def test_chat_member_can_replay_own_submission_without_runs_menu_permission(client, administrator, db):
    role = create_role(client, administrator, code="chat_sender", permissions=["home.view", "chat.view", "chat.create", "chat.execute"])
    create_user(client, administrator, [role["id"]], username="chat-sender")
    member = activate_user(client, username="chat-sender")
    cid, run, payload = seed_submission(client, member, db)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=member, json=payload)
    assert response.status_code == 200 and run.id in response.text
    foreign = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json=payload)
    assert foreign.status_code == 404 and run.id not in foreign.text
    assert client.get(f"/api/chat/runs/{run.id}/events", headers=member).status_code == 200
    assert client.post(f"/api/chat/runs/{run.id}/cancel", headers=member).status_code == 200
    assert client.get(f"/api/chat/runs/{run.id}/events", headers=administrator).status_code == 404
    assert client.post(f"/api/chat/runs/{run.id}/cancel", headers=administrator).status_code == 404
    assert client.get(f"/api/chat/conversations/{cid}", headers=member).json()["last_run"]["id"] == run.id


def test_active_chat_member_cannot_create_second_native_writer(client, administrator, db, monkeypatch):
    role = create_role(client, administrator, code="active_sender", permissions=["home.view", "chat.view", "chat.create", "chat.execute"])
    create_user(client, administrator, [role["id"]], username="active-sender")
    member = activate_user(client, username="active-sender")
    cid, run, payload = seed_submission(client, member, db)
    run.status = "running"
    run.context_snapshot = {**run.context_snapshot, "chatLease": {"host": socket.gethostname(), "runId": run.id}}
    db.commit()
    lease = chat_liveness.chat_lease(run.id)
    assert lease.acquire()
    requested = []
    monkeypatch.setattr(chat_jobs, "cancel_job", lambda key: requested.append(key) or True)
    try:
        response = client.post(f"/api/chat/conversations/{cid}/send", headers=member,
                               json={**payload, "request_id": "web-new-request-identity"})
        assert response.status_code == 409 and run.id in response.text
        detail = client.get(f"/api/chat/conversations/{cid}", headers=member).json()
        assert detail["last_run"]["id"] == run.id and detail["last_run"]["status"] == "running"
        cancelled = client.post(f"/api/chat/runs/{run.id}/cancel", headers=member)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "stopping" and requested == [run.id]
        assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 1
    finally:
        lease.release()


def test_new_submission_executes_once_and_omitted_id_retains_legacy_behavior(client, administrator, db, monkeypatch):
    executions = []
    def complete(session, run, *args, **kwargs):
        executions.append(run.id)
        finalize_run(session, run, status="completed")
        session.commit()
        yield {"type": "run.completed", "payload": {"status": "completed"}}
    monkeypatch.setattr(engine, "run_chat", complete)
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    payload = {"content": "同一条分析请求", "request_id": "web-fixed-12345678"}
    first = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json=payload)
    second = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.text.replace(": keepalive\n\n", "") == second.text.replace(": keepalive\n\n", "")
    assert len(executions) == 1
    for next_payload in ({**payload, "request_id": "web-new-12345678"}, {"content": payload["content"]}):
        assert client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json=next_payload).status_code == 200
    assert len(executions) == 3
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 3


def test_request_id_validation_and_canonical_object_order(client, administrator):
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "分析", "request_id": "../unsafe"})
    assert response.status_code == 422
    one = router.SendRequest(content="分析", plugin_inputs={"a": 1, "b": 2})
    two = router.SendRequest(content="分析", plugin_inputs={"b": 2, "a": 1}, request_id="different-id")
    assert router._submission_fingerprint(one) == router._submission_fingerprint(two)
