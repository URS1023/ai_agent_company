"""Offline checks for the AI/analytics backend fixes.

The module only imports pure helpers and SQLAlchemy table metadata.  It never
opens a session, starts an application, calls a provider, contacts a network,
or executes sandbox user code.  Run through ``run_python_checks.py``.

中文说明：AI/分析后端修复项的离线检查；只导入纯函数与 SQLAlchemy 表元数据。
隔离方式：不开会话、不启动应用、不调用提供方、不发网络请求、不执行沙箱代码，经 run_python_checks.py 运行。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def test_json_zero_and_reference_fixes_are_pure():
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.modules.capability.planner import extract_json
    from app.modules.presentation.engine import _infer_layout, _item_value
    from app.providers.tool.compiler import reference_resolver

    assert extract_json('前缀 {"text":"保留 } 字符","object":"skill"}') == {
        "text": "保留 } 字符", "object": "skill",
    }
    assert reference_resolver({"lookups": {"project": {"P01": "项目一"}}})("project", "项目一") == "P01"
    assert _item_value({"number": 0}) == "0"
    assert _infer_layout({"items": [
        {"title": "停机次数", "value": 0}, {"title": "告警次数", "value": 0},
    ]}) == "metrics"


def test_http_binding_uses_real_columns_and_never_sql():
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.modules.reports.generation import _has_bound_data, _http_catalog_schema
    from app.modules.reports.generation_bindings import normalize_binding
    from app.modules.reports.template_lock import assert_visual_unchanged

    columns = [
        SimpleNamespace(model_dump=lambda mode="json": {"columnName": "name", "dataType": "string", "comment": ""}),
        SimpleNamespace(model_dump=lambda mode="json": {"columnName": "api_token", "dataType": "string", "comment": "secret"}),
    ]
    schema = _http_catalog_schema(
        SimpleNamespace(id="http-1", name="设备接口", connection_config={}),
        SimpleNamespace(columns=columns, data=[{"name": "设备A", "api_token": "secret-value"}],
                        queryId="q-1", datasourceVersion=1),
    )
    assert schema["sourceType"] == "http"
    assert schema["tables"][0]["columns"] == [{"columnName": "name", "dataType": "string", "comment": ""}]
    assert schema["tables"][0]["sampleRows"] == [{"name": "设备A"}]
    widget = {"config": {}, "query": {"datasourceId": "http-1", "sourceType": "HTTP"}}
    binding = normalize_binding(widget, {"http-1"})
    assert binding == {"datasourceId": "http-1", "sql": None, "sourceType": "http", "parameters": {}}
    assert _has_bound_data({"query": binding, "config": {"dataStatus": "verified"}})
    template = {"canvas": {"styleLocked": True}, "widgets": [{"id": "w1", "component": "JBar", "config": {"option": {}}}]}
    bound = {"canvas": {"styleLocked": True}, "widgets": [{"id": "w1", "component": "JBar", "sourceType": "http",
              "data": [{"name": "设备A"}], "query": binding, "config": {"option": {}, "sourceType": "http",
              "dataType": 2, "chartData": "[]", "dataStatus": "verified"}}]}
    assert_visual_unchanged(template, bound)


def test_idempotency_schema_and_claim_are_atomic_before_side_effects():
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.common.idempotency import claim_idempotency
    from app.errors import ConflictError
    from app.models import IdempotencyClaim
    from sqlalchemy.exc import IntegrityError

    class FakeSession:
        """Small session double for the claim transaction state machine."""

        def __init__(self, winner=None, *, collide=False):
            self.winner = winner
            self.collide = collide
            self.added = []
            self.flush_count = 0
            self.rollback_count = 0
            self.commit_count = 0
            self.is_active = True

        def add(self, value):
            self.added.append(value)

        def flush(self):
            self.flush_count += 1
            if self.collide:
                raise IntegrityError("INSERT", {}, RuntimeError("duplicate key"))

        def rollback(self):
            self.rollback_count += 1
            self.is_active = True

        def scalar(self, _statement):
            return self.winner

        def commit(self):
            self.commit_count += 1

    def context(db):
        return SimpleNamespace(db=db, workspace_id="ws-1", user_id="user-1")

    unique = next(
        constraint for constraint in IdempotencyClaim.__table__.constraints
        if constraint.name == "uq_idempotency_claim_scope_key"
    )
    assert list(unique.columns.keys()) == ["workspace_id", "owner_user_id", "scope", "idempotency_key"]

    first_db = FakeSession()
    first = claim_idempotency(
        context(first_db), scope="report_generation:report-1",
        key="retry-1", request_hash="hash-a",
    )
    assert first is not None and first.reused is False
    assert first_db.flush_count == 1
    assert first_db.commit_count == 1

    side_effects = []
    side_effects.append("model-or-run")
    assert first_db.commit_count == 1

    winner = first.claim
    winner.status = "completed"
    winner.result_json = {"id": "generation-1"}
    replay_db = FakeSession(winner, collide=True)
    replay = claim_idempotency(
        context(replay_db), scope="report_generation:report-1",
        key="retry-1", request_hash="hash-a",
    )
    assert replay is not None and replay.reused is True
    assert replay.claim is winner
    assert replay_db.rollback_count == 1
    assert replay_db.commit_count == 0
    assert side_effects == ["model-or-run"]

    different_request_db = FakeSession(winner, collide=True)
    with pytest.raises(ConflictError):
        claim_idempotency(
            context(different_request_db), scope="report_generation:report-1",
            key="retry-1", request_hash="hash-b",
        )
    assert different_request_db.rollback_count == 1

    in_progress = IdempotencyClaim(
        id="claim-2", workspace_id="ws-1", owner_user_id="user-1",
        scope="report_generation:report-1", idempotency_key="retry-2",
        request_hash="hash-a", status="claimed",
    )
    in_progress_db = FakeSession(in_progress, collide=True)
    with pytest.raises(ConflictError):
        claim_idempotency(
            context(in_progress_db), scope="report_generation:report-1",
            key="retry-2", request_hash="hash-a",
        )
    assert in_progress_db.rollback_count == 1


def test_generation_validation_and_template_read_fail_before_claim(monkeypatch):
    """Pure request/template failures must not create a durable claim."""
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.errors import ValidationError
    from app.modules.reports import generation

    ctx = SimpleNamespace(db=SimpleNamespace(), workspace_id="ws-1", user_id="user-1")
    monkeypatch.setattr(generation, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(generation, "_customer_requirement", lambda *_args, **_kwargs: ("需求", {"text": "需求"}))
    monkeypatch.setattr(generation, "explicit_component_count", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(generation, "_resolve_sources", lambda *_args, **_kwargs: [])
    claims = []

    def claim_should_not_run(*_args, **_kwargs):
        claims.append(True)
        raise AssertionError("validation/template read must precede idempotency claim")

    monkeypatch.setattr(generation, "claim_idempotency", claim_should_not_run)

    invalid_auto = SimpleNamespace(
        template_mode="auto", template_id="template-1", session_id="retry-1",
        model_dump=lambda **_kwargs: {},
    )
    with pytest.raises(ValidationError):
        generation.generate(ctx, invalid_auto)

    def denied_template(*_args, **_kwargs):
        raise PermissionError("templates.view denied")

    monkeypatch.setattr(generation, "get_template", denied_template)
    denied_manual = SimpleNamespace(
        template_mode="manual", template_id="template-1", session_id="retry-2",
        model_dump=lambda **_kwargs: {},
    )
    with pytest.raises(PermissionError):
        generation.generate(ctx, denied_manual)
    assert claims == []


def test_runtime_network_is_provider_explicit_and_sandbox_file_hook_covers_imported_apis(monkeypatch):
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.workers import sandbox_runner

    runtime_source = (BACKEND / "app/modules/runtime/agent.py").read_text(encoding="utf-8")
    assert "allow_network=True" not in runtime_source
    assert 'offered_spec.provider in {"http", "mcp"}' in runtime_source

    captured = []
    monkeypatch.setattr(sys, "addaudithook", lambda hook: captured.append(hook))
    root = Path("sandbox-root").resolve()
    sandbox_runner._install_file_audit(str(root))
    assert len(captured) == 1
    hook = captured[0]
    hook("open", (str(root / "result.json"), "w", 0))
    hook("open", (str(root / "input.json"), "r", 0))
    with pytest.raises(PermissionError):
        hook("open", (str(root.parent / "host-secret.txt"), "r", 0))
    with pytest.raises(PermissionError):
        hook("open", (str(root.parent / "host-out.txt"), "w", 0))
    with pytest.raises(PermissionError):
        hook("os.listdir", (str(root.parent),))
