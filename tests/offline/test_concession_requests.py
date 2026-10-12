"""Offline request/idempotency checks for incoming-quality concessions.

These tests call the request service with a session-shaped fake.  They never
create a database engine: the fake raises the same application conflict that
the unique request-key constraint translates to in service_helpers.

中文说明：来料质检让步申请的离线请求/幂等检查：用"会话形状"的假对象调用服务，
不创建数据库引擎；假对象抛出与唯一请求键约束等价的业务冲突，从而验证幂等分支。
"""
from types import SimpleNamespace

import pytest

from app.errors import ConflictError
from app.modules.quality import concessions
from app.modules.quality.concession_schemas import ConcessionEvidenceInput


def workflow_payload(*, sample_id: str = "S-001") -> ConcessionEvidenceInput:
    return ConcessionEvidenceInput.model_validate({
        "requestKey": "concession-key-001",
        "sampleId": sample_id,
        "productCode": "",
        "ruleId": None,
        "source": {
            "kind": "workflow", "recordId": "execution-1", "workflowId": "workflow-1",
            "fields": {
                "originalWeight": "$.outputs.incoming.weight",
                "productCode": "$.outputs.incoming.product",
                "metric-a.value": "$.outputs.incoming.quality.value",
                "metric-a.unit": "$.outputs.incoming.quality.unit",
                "metric-a.standardMin": "$.outputs.incoming.quality.min",
                "metric-a.standardMax": "$.outputs.incoming.quality.max",
            },
        },
        "originalWeight": None,
        "weightUnit": "t",
        "measurements": [],
    })


class FakeSession:
    """Enough session behavior to exercise create_request's conflict branch."""

    def __init__(self, *, winner_hash: str | None):
        self.winner_hash = winner_hash
        self.created = None
        self.existing = None
        self.scalar_calls = 0
        self.rollback_count = 0

    def scalar(self, _statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return None
        return self.existing

    def add(self, row):
        self.created = row

    def flush(self):
        self.created.id = "request-winner"

    def rollback(self):
        self.rollback_count += 1


def fake_context(db: FakeSession):
    return SimpleNamespace(db=db, workspace_id="workspace-1", user_id="user-1")


def install_service_stubs(monkeypatch, db: FakeSession):
    monkeypatch.setattr(concessions, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(concessions, "validate_body_secrets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(concessions, "_authorize_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(concessions, "_match_rule", lambda *_args, **_kwargs: (
        None, {"status": "needs_confirmation", "reason": "等待业务确认"},
    ))
    monkeypatch.setattr(concessions, "request_view", lambda row: row)
    monkeypatch.setattr(concessions, "get_request", lambda _ctx, _request_id: db.existing)
    from app.modules.workflows import execution
    monkeypatch.setattr(execution, "get_execution", lambda *_args, **_kwargs: SimpleNamespace(
        status="completed",
        result={"outputs": {"incoming": {"weight": 16, "product": "RAW-A", "quality": {
            "value": 108, "unit": "%", "min": 100, "max": 120,
        }}}},
    ))


def test_competing_same_key_replays_winner_and_persists_derived_source(monkeypatch):
    db = FakeSession(winner_hash="same")
    ctx = fake_context(db)
    install_service_stubs(monkeypatch, db)

    def competing_commit(*_args, **_kwargs):
        # The unique request-key winner becomes visible after this caller's
        # flush/commit loses the race.
        db.existing = db.created
        raise ConflictError("request key already exists")

    monkeypatch.setattr(concessions, "commit_change", competing_commit)
    result = concessions.create_request(ctx, workflow_payload())

    assert db.rollback_count == 1
    assert db.scalar_calls == 2
    assert result is db.existing
    assert result.product_code == "RAW-A"
    assert result.weight_unit == "t"
    assert result.original_weight == "16"
    assert result.source_snapshot["kind"] == "workflow"
    assert result.source_snapshot["workflowId"] == "workflow-1"


def test_competing_same_key_with_different_hash_is_conflict(monkeypatch):
    db = FakeSession(winner_hash="different")
    ctx = fake_context(db)
    install_service_stubs(monkeypatch, db)

    def competing_commit(*_args, **_kwargs):
        db.existing = SimpleNamespace(request_hash="different", id="other-request")
        raise ConflictError("request key already exists")

    monkeypatch.setattr(concessions, "commit_change", competing_commit)
    with pytest.raises(ConflictError, match="提交键已用于其他让步证据"):
        concessions.create_request(ctx, workflow_payload(sample_id="S-002"))

    assert db.rollback_count == 1
    assert db.scalar_calls == 2
