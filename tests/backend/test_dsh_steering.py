"""运行中补充复用原生运行，身份、原话和幂等回执由 Worker 保存。"""
import pytest
from sqlalchemy import select

from test_business_worker import worker_context, prepare, request
from app.business_worker import chat
from app.errors import ConflictError, ValidationError
from app.models import Run


def submit(worker_context, run_id, text="改成七月份", request_id="steer-request-001", **extra):
    headers, cid = worker_context
    return chat.steer(request(headers, f"/api/chat/conversations/{cid}/send",
        {"content": text, "request_id": request_id, "delivery_mode": "steer", **extra}, "POST"), cid, run_id)


def test_steering_keeps_single_run_and_persists_separate_user_messages(worker_context, db):
    initial = prepare(worker_context, "财务八月应收做成大屏", request_id="original-request-001")
    chat.append_event(initial["run_id"], "run.started")
    receipt = submit(worker_context, initial["run_id"])
    duplicate = submit(worker_context, initial["run_id"])
    assert receipt["user_message"]["id"] == duplicate["user_message"]["id"]
    assert duplicate["duplicate"]
    db.expire_all()
    run = db.get(Run, initial["run_id"])
    assert run.context_snapshot["submitted_content"] == "财务八月应收做成大屏"
    assert run.context_snapshot["user_request"] == "改成七月份"
    assert len(run.context_snapshot["steering_messages"]) == 1
    assert len(db.scalars(select(Run).where(Run.conversation_id == run.conversation_id)).all()) == 1
    headers, cid = worker_context
    history = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)
    users = [message for message in history["messages"] if message["role"] == "user"]
    assert [message["request_id"] for message in users] == ["original-request-001", "steer-request-001"]
    assert all(message["run_id"] == run.id for message in users)
    assert run.context_snapshot["business_task"]["required_deliveries"] == [{"type": "dashboard", "count": 1}]


def test_steering_rejects_changed_idempotent_content_and_resources(worker_context):
    initial = prepare(worker_context)
    chat.append_event(initial["run_id"], "run.started")
    submit(worker_context, initial["run_id"])
    with pytest.raises(ConflictError):
        submit(worker_context, initial["run_id"], text="改成六月")
    with pytest.raises(ConflictError):
        submit(worker_context, initial["run_id"], request_id="steer-source-002", datasource_ids=["other-source"])
    with pytest.raises(ConflictError):
        submit(worker_context, initial["run_id"], request_id="steer-model-002", model_profile="another-model")


def test_steering_requires_active_run_and_request_identity(worker_context):
    initial = prepare(worker_context)
    with pytest.raises(ConflictError):
        submit(worker_context, initial["run_id"])
    chat.append_event(initial["run_id"], "run.started")
    with pytest.raises(ValidationError):
        submit(worker_context, initial["run_id"], request_id=None)
    chat.settle(initial["run_id"], "completed", "已完成")
    with pytest.raises(ConflictError):
        submit(worker_context, initial["run_id"])
