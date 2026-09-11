"""Managed publish contracts using native code and isolated session doubles, without external I/O."""

from datetime import UTC, datetime
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy.dialects import postgresql
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from controllers.console.app import workflow as controller
from services import workflow_service as service_module
from services.errors.app import WorkflowHashNotEqualError

WORKSPACE = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
PUBLISHED = "33333333-3333-4333-8333-333333333333"
HASH = "a" * 64
HEADERS = {"X-Enterprise-Expected-Workspace": WORKSPACE, "X-Enterprise-Expected-Draft-Hash": HASH}


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    factory = MagicMock()
    factory.return_value.begin.return_value.__enter__.return_value = session
    service = MagicMock()
    service.publish_workflow.return_value = SimpleNamespace(id=PUBLISHED, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(controller, "db", SimpleNamespace(engine=object()))
    monkeypatch.setattr(controller, "sessionmaker", factory)
    monkeypatch.setattr(controller, "WorkflowService", MagicMock(return_value=service))
    # raising=False lets RED exercise absent opt-in behavior rather than fixture import errors.
    monkeypatch.setattr(
        controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True), raising=False
    )
    return factory, service


def publish(
    headers: dict[str, str], workspace: str | None = WORKSPACE, tenant: str = WORKSPACE
) -> dict[str, object] | tuple[dict[str, object], int, dict[str, str]]:
    api = controller.PublishedWorkflowApi()
    with Flask(__name__).test_request_context("/", method="POST", headers=headers, json={}):
        return unwrap(api.post)(
            api,
            SimpleNamespace(id="account", current_tenant_id=workspace),
            SimpleNamespace(id=APP, tenant_id=tenant, workflow_id="not-the-created-workflow"),
        )


@pytest.mark.parametrize("managed", [False, True])
def test_publish_returns_exact_created_identity_only_when_managed(
    managed: bool, native: tuple[MagicMock, MagicMock]
) -> None:
    factory, service = native
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = managed
    response = publish(HEADERS if managed else {})
    if managed:
        body, status, headers = response
        assert status == 200
        assert headers == {"X-Enterprise-Workspace": WORKSPACE}
        assert body == {
            "result": "success",
            "created_at": 1767225600,
            "workflow_id": PUBLISHED,
            "app_id": APP,
            "accepted_draft_hash": HASH,
        }
        assert service.publish_workflow.call_args.kwargs["expected_draft_hash"] == HASH
    else:
        assert response == {"result": "success", "created_at": 1767225600}
        assert "expected_draft_hash" not in service.publish_workflow.call_args.kwargs
    factory.assert_called_once()


@pytest.mark.parametrize(
    ("headers", "workspace", "tenant", "enabled", "error"),
    [
        (HEADERS, WORKSPACE, WORKSPACE, False, Forbidden),
        (HEADERS, None, WORKSPACE, True, Conflict),
        (HEADERS, APP, WORKSPACE, True, Conflict),
        (HEADERS, WORKSPACE, APP, True, Conflict),
        ({"X-Enterprise-Expected-Workspace": WORKSPACE}, WORKSPACE, WORKSPACE, True, BadRequest),
        ({"X-Enterprise-Expected-Draft-Hash": HASH}, WORKSPACE, WORKSPACE, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Draft-Hash": "bad"}, WORKSPACE, WORKSPACE, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Draft-Hash": "A" * 64}, WORKSPACE, WORKSPACE, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Workspace": ""}, WORKSPACE, WORKSPACE, True, BadRequest),
    ],
)
def test_guard_before_session(
    headers: dict[str, str],
    workspace: str | None,
    tenant: str,
    enabled: bool,
    error: type[Exception],
    native: tuple[MagicMock, MagicMock],
) -> None:
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    with pytest.raises(error):
        publish(headers, workspace, tenant)
    native[0].assert_not_called()
    native[1].publish_workflow.assert_not_called()


def test_controller_hash_mismatch_is_explicit_conflict(native: tuple[MagicMock, MagicMock]) -> None:
    native[1].publish_workflow.side_effect = WorkflowHashNotEqualError()
    with pytest.raises(Conflict):
        publish(HEADERS)


def test_service_locks_and_refreshes_exact_draft_before_hash_check(monkeypatch: pytest.MonkeyPatch) -> None:
    service = object.__new__(service_module.WorkflowService)
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(unique_hash="b" * 64)
    validate = MagicMock()
    monkeypatch.setattr(service, "validate_graph_structure", validate)
    event = MagicMock()
    monkeypatch.setattr(service_module, "app_published_workflow_was_updated", event)
    with pytest.raises(WorkflowHashNotEqualError):
        service.publish_workflow(
            session=session,
            app_model=SimpleNamespace(tenant_id=WORKSPACE, id=APP),
            account=SimpleNamespace(id="account"),
            expected_draft_hash=HASH,
        )
    stmt = session.scalar.call_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "FOR UPDATE" in str(compiled)
    assert set(compiled.params.values()) == {WORKSPACE, APP, "draft"}
    assert stmt.get_execution_options()["populate_existing"] is True
    session.add.assert_not_called()
    validate.assert_not_called()
    event.send.assert_not_called()


@pytest.mark.parametrize("managed", [False, True])
def test_service_preserves_validation_and_publishes_locked_graph(
    managed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.agent.workflow_publish_service import WorkflowAgentPublishService
    from services.feature_service import FeatureService

    service = object.__new__(service_module.WorkflowService)
    session = MagicMock()
    draft = SimpleNamespace(
        unique_hash=HASH,
        type="workflow",
        graph='{"nodes": []}',
        graph_dict={"nodes": []},
        features="{}",
        environment_variables=[],
        conversation_variables=[],
        rag_pipeline_variables=[],
    )
    session.scalar.return_value = draft
    validate = MagicMock()
    monkeypatch.setattr(service, "validate_graph_structure", validate)
    monkeypatch.setattr(
        FeatureService, "get_system_features", lambda: SimpleNamespace(plugin_manager=SimpleNamespace(enabled=False))
    )
    agent_validate, agent_copy, event = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(WorkflowAgentPublishService, "validate_agent_nodes_for_publish", agent_validate)
    monkeypatch.setattr(WorkflowAgentPublishService, "copy_agent_node_bindings_to_published", agent_copy)
    monkeypatch.setattr(service_module, "app_published_workflow_was_updated", event)
    monkeypatch.setattr(service_module, "dify_config", SimpleNamespace(BILLING_ENABLED=False))
    created = SimpleNamespace(id=PUBLISHED)
    create = MagicMock(return_value=created)
    monkeypatch.setattr(service_module.Workflow, "new", create)
    app_model = SimpleNamespace(tenant_id=WORKSPACE, id=APP)
    result = service.publish_workflow(
        session=session,
        app_model=app_model,
        account=SimpleNamespace(id="account"),
        **({"expected_draft_hash": HASH} if managed else {}),
    )
    assert result is created
    assert create.call_args.kwargs["graph"] == draft.graph
    validate.assert_called_once_with(graph=draft.graph_dict)
    agent_validate.assert_called_once_with(session=session, draft_workflow=draft)
    agent_copy.assert_called_once_with(session=session, draft_workflow=draft, published_workflow=created)
    session.add.assert_called_once_with(created)
    event.send.assert_called_once_with(app_model, published_workflow=created)
    stmt = session.scalar.call_args.args[0]
    assert ("FOR UPDATE" in str(stmt.compile(dialect=postgresql.dialect()))) is managed
    assert bool(stmt.get_execution_options().get("populate_existing")) is managed
