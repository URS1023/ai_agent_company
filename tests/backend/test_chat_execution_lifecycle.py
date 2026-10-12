"""Recovery needs executor evidence; elapsed time alone cannot fail live work.

中文说明：聊天任务的生命周期与恢复：清理只依据执行者证据（租约/进程），仅凭超时不能把活跃任务判失败。
验证行为：活跃与等待中的任务被保留；旧清理尊重指定的 run_id；启动失败要登记所有者并清理运行与租约；
租约写入失败不能提交无主运行；流已开启后的执行者退出可被识别；取消孤儿执行者返回终态；
中断不覆盖并发完成的结果。
隔离方式：monkeypatch + MemoryStatus/临时目录 + 内存 SQLite，不连业务库、不调用模型。
"""
import socket

import pytest
from sqlalchemy import select, update

from app.models import Run, RunStep, ToolCall
from app.modules.runtime import chat_jobs, chat_liveness
from app.modules.system_tools.scheduler_lease import SchedulerLease
from test_operations import seed_run, user_id


@pytest.fixture
def leases(monkeypatch, tmp_path):
    factory = lambda run_id: SchedulerLease(tmp_path / (run_id + ".lock"))
    monkeypatch.setattr(chat_liveness, "chat_lease", factory)
    monkeypatch.setattr(chat_jobs, "chat_lease", factory)
    return factory


def abandoned(client, headers, db, **changes):
    row = seed_run(db, headers["X-Workspace-Id"], user_id(client, headers), status="running", stale=True)
    row.context_snapshot = {"chatLease": {"host": socket.gethostname(), "runId": row.id}}
    for name, value in changes.items():
        setattr(row, name, value)
    db.commit()
    return row


@pytest.mark.parametrize("legacy", [False, True])
def test_cleanup_preserves_live_waiting_and_unverifiable_runs(client, administrator, db, leases, legacy):
    lost = abandoned(client, administrator, db)
    active = abandoned(client, administrator, db)
    waiting = abandoned(client, administrator, db, status="waiting")
    workflow = abandoned(client, administrator, db, context_snapshot={"entry": "workflow", "execution_scope": "standalone"})
    unknown = abandoned(client, administrator, db, context_snapshot={})
    foreign = abandoned(client, administrator, db)
    foreign.context_snapshot = {"chatLease": {"host": "another-host", "runId": foreign.id}}
    db.commit()
    for model in (RunStep, ToolCall):
        db.execute(update(model).where(model.run_id == lost.id).values(status="running", completed_at=None))
    db.commit()
    lock = leases(active.id)
    assert lock.acquire()
    try:
        if legacy:
            response = client.post("/api/runs/recover-stale?dry_run=false", headers=administrator)
            data = response.json()
        else:
            response = client.post("/api/v1/operations/runs/recover", headers=administrator,
                                   json={"thresholdSeconds": 60, "dryRun": False})
            data = response.json().get("data", {})
        assert response.status_code == 200, response.text
        assert data["count"] == 1, data
        db.expire_all()
        assert db.get(Run, lost.id).status == "failed"
        assert db.get(Run, waiting.id).status == "waiting"
        for row in (active, workflow, unknown, foreign):
            assert db.get(Run, row.id).status == "running"
        for model in (RunStep, ToolCall):
            assert db.scalar(select(model).where(model.run_id == lost.id)).status == "failed"
    finally:
        lock.release()


def test_legacy_cleanup_respects_requested_run_id(client, administrator, db, leases):
    selected = abandoned(client, administrator, db)
    other = abandoned(client, administrator, db)
    response = client.post(f"/api/runs/recover-stale?dry_run=false&run_id={selected.id}", headers=administrator)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    db.expire_all()
    assert db.get(Run, other.id).status == "running"


def test_start_failure_registers_owner_then_cleans_run_and_lease(client, administrator, db, leases, monkeypatch):
    from app.db import new_session
    observed = []
    def cannot_start(thread):
        if not thread.name.startswith("chat-"):
            return original(thread)
        run_id = thread.name[5:]
        with new_session() as session:
            row = session.get(Run, run_id)
            observed.append((row.context_snapshot or {}).get("chatLease"))
        raise RuntimeError("isolated thread start failure")
    original = chat_jobs.threading.Thread.start
    monkeypatch.setattr(chat_jobs.threading.Thread, "start", cannot_start)
    conversation = client.post("/api/chat/conversations", headers=administrator, json={"title": "启动失败"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator,
                           json={"content": "请说明功能"})
    assert response.status_code == 500
    row = db.scalar(select(Run).where(Run.conversation_id == conversation["id"]))
    assert observed == [{"host": socket.gethostname(), "runId": row.id}]
    assert row.status == "failed" and row.error_code == "chat_interrupted"
    assert row.id not in chat_jobs._jobs
    lock = leases(row.id)
    assert lock.acquire()
    lock.release()


def test_lease_storage_failure_does_not_commit_an_ownerless_run(client, administrator, db, monkeypatch):
    from unittest.mock import Mock
    unavailable = Mock()
    unavailable.acquire.return_value = False
    monkeypatch.setattr(chat_liveness, "chat_lease", lambda _: unavailable)
    conversation = client.post("/api/chat/conversations", headers=administrator,
                               json={"title": "租约存储不可用"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator,
                           json={"content": "请说明功能"})
    assert response.status_code == 500
    assert db.scalars(select(Run).where(Run.conversation_id == conversation["id"])).all() == []


def test_replay_detects_executor_exit_after_stream_open(client, administrator, db, leases, monkeypatch):
    row = abandoned(client, administrator, db)
    lock = leases(row.id)
    assert lock.acquire()
    monkeypatch.setattr(chat_jobs.time, "sleep", lambda _: None)
    stream = chat_jobs.replay(row.id, row.workspace_id)
    try:
        assert "keepalive" in next(stream)
        lock.release()
        packets = []
        for _ in range(5):
            packets.append(next(stream))
            if "event: done" in packets[-1]:
                break
        assert any("run.failed" in packet and "chat_interrupted" in packet for packet in packets)
        assert "event: done" in packets[-1]
    finally:
        lock.release()
        stream.close()


def test_cancel_of_abandoned_local_executor_returns_terminal_state(client, administrator, db, leases):
    row = abandoned(client, administrator, db)
    response = client.post(f"/api/chat/runs/{row.id}/cancel", headers=administrator)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"


def test_interruption_does_not_overwrite_a_concurrent_completion(client, administrator, db, leases):
    row = abandoned(client, administrator, db)
    db.execute(update(Run).where(Run.id == row.id).values(status="completed").execution_options(synchronize_session=False))
    db.commit()
    assert row.status == "running", "Fixture deliberately retains an old ORM view"
    assert not chat_liveness.mark_interrupted(db, row)
    db.expire_all()
    assert db.get(Run, row.id).status == "completed"


def test_recovery_preserves_real_waiting_workflow_and_it_can_resume(client, administrator, db):
    from datetime import datetime, timedelta
    from uuid import uuid4
    from test_workflows import API, create_flow, execute, graph, node, result
    flow = create_flow(client, administrator, graph(node("ask", "question", {
        "prompt": "是否批准？", "answerKey": "decision", "required": True,
    })))
    waiting = result(execute(client, administrator, flow), 202)
    run = db.get(Run, waiting["runId"])
    run.created_at = run.started_at = datetime.utcnow() - timedelta(hours=2)
    db.commit()
    response = client.post("/api/v1/operations/runs/recover", headers=administrator,
                           json={"thresholdSeconds": 60, "dryRun": False})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["count"] == 0
    assert response.json()["data"]["skippedCount"] == 1
    db.expire_all()
    assert db.get(Run, run.id).status == "waiting"
    resumed = result(client.post(API + f"/{flow['id']}/runs/{waiting['id']}/resume", headers=administrator,
                                 json={"version": waiting["version"], "idempotencyKey": uuid4().hex, "input": "批准"}))
    assert resumed["status"] == "completed"
