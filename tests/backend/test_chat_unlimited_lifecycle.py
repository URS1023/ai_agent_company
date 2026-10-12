"""Unlimited chats remain cancellable and always replay their durable terminal state.

隔离SQLite与模型桩：不连接MySQL，不调用外部模型；时间跳跃仅作用于任务控制时钟。
"""
from datetime import datetime
import socket
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.models import Message, Run
from app.modules.runtime import chat_jobs, chat_liveness, task_control
from app.modules.openwebui import runtime as native_runtime
from chat_native_fakes import block_live_native
from app.modules.runtime.service import create_message, create_run, finalize_run
from app.modules.system_tools.scheduler_lease import SchedulerLease


@pytest.fixture(autouse=True)
def local_leases(monkeypatch, tmp_path):
    factory = lambda run_id: SchedulerLease(tmp_path / (run_id + ".lock"))
    monkeypatch.setattr(chat_liveness, "chat_lease", factory)
    monkeypatch.setattr(chat_jobs, "chat_lease", factory)
    monkeypatch.setattr(chat_liveness, "conversation_send_lease",
                        lambda cid: SchedulerLease(tmp_path / ("conversation-" + cid + ".lock")))


def conversation(client, headers):
    return client.post("/api/chat/conversations", headers=headers, json={}).json()["id"]


def stored_run(client, headers, db, cid, *, status="running"):
    uid = client.get("/api/auth/me", headers=headers).json()["user"]["id"]
    create_message(db, workspace_id=headers["X-Workspace-Id"], conversation_id=cid,
                   role="user", content="分析设备运行数据")
    row = create_run(db, workspace_id=headers["X-Workspace-Id"], conversation_id=cid,
                     user_id=uid)
    row.status = status
    row.context_snapshot = {"chatLease": {"host": socket.gethostname(), "runId": row.id}}
    if status == "cancelled":
        row.error_code = "chat_cancelled"
    db.commit()
    return row


def test_default_chat_has_no_deadline_and_ignores_general_run_budget(monkeypatch):
    monkeypatch.setattr(chat_jobs.settings, "CHAT_TASK_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(chat_jobs.settings, "RUN_TIMEOUT_SECONDS", 1)
    control = chat_jobs.ChatJob("isolated", "workspace").control
    assert control.deadline is None
    monkeypatch.setattr(task_control.time, "monotonic", lambda: 10**12)
    control.check()
    control.cancelled.set()
    with pytest.raises(task_control.ChatCancelled):
        control.check()


def test_explicit_chat_deadline_does_not_inherit_general_run_cap(monkeypatch):
    monkeypatch.setattr(chat_jobs.settings, "CHAT_TASK_TIMEOUT_SECONDS", 900)
    monkeypatch.setattr(chat_jobs.settings, "RUN_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(chat_jobs.time, "monotonic", lambda: 100)
    assert chat_jobs.ChatJob("isolated", "workspace").control.deadline == 1000


@pytest.mark.parametrize("deadline,requested,expected", [
    (None, None, None), (None, 3, 3), (120, None, 20), (120, 3, 3), (120, 60, 20),
])
def test_remaining_timeout_retains_each_operation_budget(monkeypatch, deadline, requested, expected):
    monkeypatch.setattr(task_control.time, "monotonic", lambda: 100)
    token = task_control.current.set(task_control.TaskControl(deadline=deadline))
    try:
        assert task_control.remaining_timeout(requested) == expected
    finally:
        task_control.current.reset(token)


def test_unlimited_retry_is_once_and_cancel_still_wins():
    control = task_control.TaskControl()
    assert control.claim_model_connection_retry()
    assert not control.claim_model_connection_retry()
    control.cancelled.set()
    token = task_control.current.set(control)
    try:
        with pytest.raises(task_control.ChatCancelled):
            task_control.remaining_timeout(None)
    finally:
        task_control.current.reset(token)


def test_lease_failure_rolls_back_user_message_too(client, administrator, db, monkeypatch):
    cid = conversation(client, administrator)
    unavailable = Mock()
    unavailable.acquire.return_value = False
    monkeypatch.setattr(chat_liveness, "chat_lease", lambda _: unavailable)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "请分析"})
    assert response.status_code == 500
    assert db.scalars(select(Message).where(Message.conversation_id == cid)).all() == []
    assert db.scalars(select(Run).where(Run.conversation_id == cid)).all() == []


def test_first_read_after_crash_contains_the_recovered_reply(client, administrator, db):
    cid = conversation(client, administrator)
    stored_run(client, administrator, db, cid)
    response = client.get(f"/api/chat/conversations/{cid}", headers=administrator)
    assert response.status_code == 200
    data = response.json()
    assert data["last_run"]["status"] == "failed"
    replies = [message for message in data["messages"] if message["role"] == "assistant"]
    assert len(replies) == 1 and "中断" in replies[0]["content"]


def test_executor_return_without_terminal_event_reports_interruption(client, administrator, db, monkeypatch):
    monkeypatch.setattr(native_runtime, "run_chat", lambda *args, **kwargs: iter([]))
    cid = conversation(client, administrator)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "请分析"})
    assert "event: run.failed" in response.text
    assert response.text.index("event: run.failed") < response.text.index("event: done")
    assert "event: run.completed" not in response.text
    db.expire_all()
    assert db.scalar(select(Run).where(Run.conversation_id == cid)).status == "failed"


@pytest.mark.parametrize("fail_event_write", [False, True])
def test_completed_commit_survives_missing_terminal_packet(client, administrator, db, monkeypatch, fail_event_write):
    def completed(session, run, *args, **kwargs):
        create_message(session, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                       role="assistant", content="隔离验收结果：本次分析已完成。")
        finalize_run(session, run, status="completed")
        session.commit()
        if fail_event_write:
            yield {"type": "run.completed", "payload": {"status": "completed"}}
    monkeypatch.setattr(native_runtime, "run_chat", completed)
    save = chat_jobs.ChatJob._save
    failed = []
    def one_failed_packet(job, event, data):
        if fail_event_write and event == "run.completed" and not failed:
            failed.append(event)
            raise RuntimeError("isolated SSE write failure after successful commit")
        return save(job, event, data)
    monkeypatch.setattr(chat_jobs.ChatJob, "_save", one_failed_packet)
    cid = conversation(client, administrator)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "请分析"})
    assert response.text.count("event: run.completed") == 1
    assert "event: done" in response.text and "event: run.failed" not in response.text
    db.expire_all()
    assert db.scalar(select(Run).where(Run.conversation_id == cid)).status == "completed"


def test_cancel_before_agent_exception_handler_remains_cancelled(client, administrator, db, monkeypatch):
    def cancelled(*args, **kwargs):
        control = task_control.current.get()
        control.cancelled.set()
        control.check()
        yield
    monkeypatch.setattr(native_runtime, "run_chat", cancelled)
    cid = conversation(client, administrator)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "请分析"})
    assert "event: run.failed" in response.text and "chat_cancelled" in response.text
    assert "event: done" in response.text
    db.expire_all()
    assert db.scalar(select(Run).where(Run.conversation_id == cid)).status == "cancelled"
    assert "已停止" in db.scalar(select(Message).where(
        Message.conversation_id == cid, Message.role == "assistant")).content


@pytest.mark.parametrize("status,event", [("completed", "run.completed"), ("cancelled", "run.failed")])
def test_replay_recovers_terminal_packet_after_process_restart(client, administrator, db, status, event):
    cid = conversation(client, administrator)
    row = stored_run(client, administrator, db, cid, status=status)
    packets = list(chat_jobs.replay(row.id, row.workspace_id))
    assert len(packets) == 2
    assert f"event: {event}" in packets[0] and f'"status": "{status}"' in packets[0]
    assert "event: done" in packets[1]


def test_duplicate_send_returns_active_run_without_appending_message(client, administrator, db, monkeypatch):
    cid = conversation(client, administrator)
    row = stored_run(client, administrator, db, cid)
    lease = chat_liveness.chat_lease(row.id)
    assert lease.acquire()
    started = Mock()
    monkeypatch.setattr(chat_jobs, "start_job", started)
    try:
        response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                               json={"content": "重复发送"})
        assert response.status_code == 409, response.text
        assert row.id in response.text
        assert len(db.scalars(select(Message).where(Message.conversation_id == cid)).all()) == 1
        started.assert_not_called()
    finally:
        lease.release()


def test_concurrent_send_admission_does_not_create_a_second_run(client, administrator, db, monkeypatch):
    cid = conversation(client, administrator)
    admission = chat_liveness.conversation_send_lease(cid)
    assert admission.acquire()
    started = Mock()
    monkeypatch.setattr(chat_jobs, "start_job", started)
    try:
        response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                               json={"content": "并发发送"})
        assert response.status_code == 409
        assert db.scalars(select(Run).where(Run.conversation_id == cid)).all() == []
        assert db.scalars(select(Message).where(Message.conversation_id == cid)).all() == []
        started.assert_not_called()
    finally:
        admission.release()


@pytest.mark.parametrize("legacy", [False, True])
def test_stale_or_ownerless_run_does_not_permanently_lock_conversation(client, administrator, db, monkeypatch, legacy):
    cid = conversation(client, administrator)
    previous = stored_run(client, administrator, db, cid)
    if legacy:
        previous.context_snapshot = {}
        db.commit()
    monkeypatch.setattr(native_runtime, "run_chat", lambda *args, **kwargs: iter([]))
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "现在分析设备"})
    assert response.status_code == 200 and "event: run.created" in response.text
    db.expire_all()
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 2
    assert db.get(Run, previous.id).status == ("running" if legacy else "failed")


@pytest.mark.parametrize("status,event", [("completed", "run.completed"), ("cancelled", "run.failed"), ("failed", "run.failed")])
def test_historical_done_without_terminal_event_is_repaired(client, administrator, db, status, event):
    from app.models import RunEvent
    cid = conversation(client, administrator)
    row = stored_run(client, administrator, db, cid, status=status)
    db.add(RunEvent(workspace_id=row.workspace_id, run_id=row.id, event_type="chat.stream",
                    payload={"event": "done", "data": {"run_id": row.id}},
                    created_at=datetime.utcnow(), sequence=1))
    db.commit()
    packets = list(chat_jobs.replay(row.id, row.workspace_id))
    assert len(packets) == 2 and f"event: {event}" in packets[0]
    assert "event: done" in packets[1]


def test_concurrent_completion_during_recovery_does_not_block_send(client, administrator, db, monkeypatch):
    cid = conversation(client, administrator)
    row = stored_run(client, administrator, db, cid)
    def concurrent_completion(session, loaded):
        loaded.status = "completed"
        session.commit()
        return False
    monkeypatch.setattr(chat_liveness, "recover_abandoned_chat", concurrent_completion)
    monkeypatch.setattr(native_runtime, "run_chat", lambda *args, **kwargs: iter([]))
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "下一次分析"})
    assert response.status_code == 200 and "event: run.created" in response.text
