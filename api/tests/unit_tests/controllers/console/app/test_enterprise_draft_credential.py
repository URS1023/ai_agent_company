"""Managed credential-only draft mutation without native database or plugin I/O."""

import json
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy.dialects import postgresql
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, UnsupportedMediaType

from controllers.console.app import workflow as controller
from models.workflow import Workflow
from services import workflow_service as module
from services.errors.app import WorkflowHashNotEqualError

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
DRAFT = "33333333-3333-4333-8333-333333333333"
CREDENTIAL = "44444444-4444-4444-8444-444444444444"
HASH = "a" * 64
HEADERS = {"X-Enterprise-Expected-Workspace": WS, "X-Enterprise-Draft-Operation": "bind-assessment-credential"}
BODY = {"draft_id": DRAFT, "hash": HASH, "credential_id": CREDENTIAL}


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    factory, service = MagicMock(), MagicMock()
    monkeypatch.setattr(controller, "db", SimpleNamespace(engine=object()))
    monkeypatch.setattr(controller, "Session", factory)
    monkeypatch.setattr(controller, "WorkflowService", MagicMock(return_value=service))
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    return factory, service


def invoke(
    headers: dict[str, str],
    body: object,
    *,
    workspace: str | None = WS,
    tenant: str = WS,
    content_type: str = "application/json",
) -> object:
    api = controller.DraftWorkflowApi()
    with Flask(__name__).test_request_context(
        "/", method="POST", headers=headers, data=json.dumps(body), content_type=content_type
    ):
        return unwrap(api.post)(
            api, SimpleNamespace(id="account", current_tenant_id=workspace), SimpleNamespace(id=APP, tenant_id=tenant)
        )


def test_managed_receipt_and_exact_arguments(native: tuple[MagicMock, MagicMock]) -> None:
    factory, service = native
    service.bind_assessment_credential.return_value = SimpleNamespace(
        app_id=APP, draft_id=DRAFT, accepted_draft_hash=HASH, hash="b" * 64, credential_id=CREDENTIAL
    )
    result = invoke(HEADERS, BODY)
    assert result == (
        {
            "result": "success",
            "app_id": APP,
            "draft_id": DRAFT,
            "accepted_draft_hash": HASH,
            "hash": "b" * 64,
            "credential_id": CREDENTIAL,
        },
        200,
        {"X-Enterprise-Workspace": WS},
    )
    kwargs = service.bind_assessment_credential.call_args.kwargs
    assert kwargs["draft_id"] == DRAFT
    assert kwargs["credential_id"] == CREDENTIAL
    assert kwargs["expected_draft_hash"] == HASH
    service.sync_draft_workflow.assert_not_called()
    factory.assert_called_once()


@pytest.mark.parametrize(
    ("headers", "body", "enabled", "workspace", "tenant", "content_type", "error"),
    [
        (HEADERS, BODY, False, WS, WS, "application/json", Forbidden),
        (HEADERS, BODY, True, None, WS, "application/json", Conflict),
        (HEADERS, BODY, True, APP, WS, "application/json", Conflict),
        (HEADERS, BODY, True, WS, APP, "application/json", Conflict),
        ({"X-Enterprise-Expected-Workspace": WS}, BODY, True, WS, WS, "application/json", BadRequest),
        (
            {"X-Enterprise-Draft-Operation": "bind-assessment-credential"},
            BODY,
            True,
            WS,
            WS,
            "application/json",
            BadRequest,
        ),
        ({**HEADERS, "X-Enterprise-Draft-Operation": "other"}, BODY, True, WS, WS, "application/json", BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Workspace": "bad"}, BODY, True, WS, WS, "application/json", BadRequest),
        (HEADERS, {**BODY, "graph": {}}, True, WS, WS, "application/json", BadRequest),
        (HEADERS, {**BODY, "hash": "A" * 64}, True, WS, WS, "application/json", BadRequest),
        (HEADERS, {**BODY, "draft_id": "bad"}, True, WS, WS, "application/json", BadRequest),
        (
            HEADERS,
            {**BODY, "credential_id": "00000000-0000-0000-0000-000000000000"},
            True,
            WS,
            WS,
            "application/json",
            BadRequest,
        ),
        (HEADERS, BODY, True, WS, WS, "text/plain", UnsupportedMediaType),
    ],
)
def test_guard_precedes_native_work(
    headers: dict[str, str],
    body: object,
    enabled: bool,
    workspace: str | None,
    tenant: str,
    content_type: str,
    error: type[Exception],
    native: tuple[MagicMock, MagicMock],
) -> None:
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    with pytest.raises(error):
        invoke(headers, body, workspace=workspace, tenant=tenant, content_type=content_type)
    native[0].assert_not_called()
    native[1].bind_assessment_credential.assert_not_called()
    native[1].sync_draft_workflow.assert_not_called()


def draft() -> Workflow:
    return Workflow(
        id=DRAFT,
        tenant_id=WS,
        app_id=APP,
        version="draft",
        features='{"file_upload": {"enabled": false}}',
        graph=json.dumps(
            {
                "nodes": [
                    {
                        "id": "assessment",
                        "position": {"x": 5},
                        "data": {
                            "type": "tool",
                            "provider_type": "builtin",
                            "tool_name": "evaluate_device",
                            "provider_id": "enterprise/enterprise_device_assessment/enterprise_device",
                        },
                    }
                ],
                "edges": [],
            }
        ),
        _environment_variables='{"new": "concurrent-environment-snapshot"}',
        _conversation_variables='{"new": "concurrent-conversation-snapshot"}',
    )


def test_service_patches_only_graph_and_captures_before_commit_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.agent.workflow_publish_service import WorkflowAgentPublishService

    service = object.__new__(module.WorkflowService)
    workflow = draft()
    original_hash = workflow.unique_hash
    original_fields = (workflow.features, workflow._environment_variables, workflow._conversation_variables)
    expected_graph = workflow.graph_dict
    expected_graph["nodes"][0]["data"]["credential_id"] = CREDENTIAL
    session = MagicMock()
    session.scalar.return_value = workflow
    feature_validate, graph_validate, sync, agent_validate, event = [MagicMock() for _ in range(5)]
    monkeypatch.setattr(service, "validate_features_structure", feature_validate)
    monkeypatch.setattr(service, "validate_graph_structure", graph_validate)
    monkeypatch.setattr(WorkflowAgentPublishService, "sync_agent_bindings_for_draft", sync)
    monkeypatch.setattr(WorkflowAgentPublishService, "validate_agent_nodes_for_draft_sync", agent_validate)
    monkeypatch.setattr(module, "app_draft_workflow_was_synced", event)

    def expire() -> None:
        workflow.graph = '{"later": true}'
        workflow.id = APP

    session.commit.side_effect = expire
    result = service.bind_assessment_credential(
        session=session,
        app_model=SimpleNamespace(id=APP, tenant_id=WS),
        account=SimpleNamespace(id="account"),
        draft_id=DRAFT,
        expected_draft_hash=original_hash,
        credential_id=CREDENTIAL,
    )
    assert result.draft_id == DRAFT
    assert result.accepted_draft_hash == original_hash
    expected = draft()
    expected.graph = json.dumps(expected_graph)
    assert result.hash == expected.unique_hash
    assert result.credential_id == CREDENTIAL
    assert result.app_id == APP
    assert (workflow.features, workflow._environment_variables, workflow._conversation_variables) == original_fields
    stmt = session.scalar.call_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "FOR UPDATE" in str(compiled)
    assert set(compiled.params.values()) == {WS, APP, DRAFT, "draft"}
    assert stmt.get_execution_options()["populate_existing"] is True
    feature_validate.assert_called_once()
    graph_validate.assert_called_once_with(graph=expected_graph)
    sync.assert_called_once()
    agent_validate.assert_called_once()
    session.commit.assert_called_once()
    event.send.assert_called_once()
    session.add.assert_not_called()


def test_service_hash_mismatch_precedes_all_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    service = object.__new__(module.WorkflowService)
    workflow = draft()
    graph = workflow.graph
    session = MagicMock()
    session.scalar.return_value = workflow
    validate = MagicMock()
    monkeypatch.setattr(service, "validate_graph_structure", validate)
    with pytest.raises(WorkflowHashNotEqualError):
        service.bind_assessment_credential(
            session=session,
            app_model=SimpleNamespace(id=APP, tenant_id=WS),
            account=SimpleNamespace(id="account"),
            draft_id=DRAFT,
            expected_draft_hash=HASH,
            credential_id=CREDENTIAL,
        )
    assert workflow.graph == graph
    validate.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("mutation", ["missing", "wrong_provider", "wrong_tool", "duplicate", "duplicate_id"])
def test_service_rejects_missing_or_ambiguous_target_without_writes(mutation: str) -> None:
    service = object.__new__(module.WorkflowService)
    workflow = draft()
    graph = workflow.graph_dict
    if mutation == "wrong_provider":
        graph["nodes"][0]["data"]["provider_id"] = "other/provider"
    elif mutation == "wrong_tool":
        graph["nodes"][0]["data"]["tool_name"] = "other"
    elif mutation in {"duplicate", "duplicate_id"}:
        other = json.loads(json.dumps(graph["nodes"][0]))
        if mutation == "duplicate":
            other["id"] = "another"
        graph["nodes"].append(other)
    workflow.graph = json.dumps(graph)
    session = MagicMock()
    session.scalar.return_value = None if mutation == "missing" else workflow
    with pytest.raises(ValueError):
        service.bind_assessment_credential(
            session=session,
            app_model=SimpleNamespace(id=APP, tenant_id=WS),
            account=SimpleNamespace(id="account"),
            draft_id=DRAFT,
            expected_draft_hash=workflow.unique_hash,
            credential_id=CREDENTIAL,
        )
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("kind", ["hash", "target"])
def test_controller_conflicts_close_session_without_ordinary_sync(
    kind: str, native: tuple[MagicMock, MagicMock]
) -> None:
    factory, service = native
    service.bind_assessment_credential.side_effect = (
        WorkflowHashNotEqualError() if kind == "hash" else module.DraftCredentialBindingError("invalid target")
    )
    error = controller.DraftWorkflowNotSync if kind == "hash" else Conflict
    with pytest.raises(error):
        invoke(HEADERS, BODY)
    assert factory.return_value.__exit__.call_args.args[0] is error
    service.sync_draft_workflow.assert_not_called()


@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
def test_ordinary_sync_keeps_original_payload_and_response(
    content_type: str, native: tuple[MagicMock, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    session = MagicMock()
    monkeypatch.setattr(controller, "db", SimpleNamespace(session=MagicMock(return_value=session)))
    native[1].sync_draft_workflow.return_value = SimpleNamespace(
        unique_hash=HASH, updated_at=None, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    response = invoke({}, {"graph": {}, "features": {}, "hash": HASH}, content_type=content_type)
    assert response == {"result": "success", "hash": HASH, "updated_at": 1767225600}
    kwargs = native[1].sync_draft_workflow.call_args.kwargs
    assert kwargs["environment_variables"] == []
    assert kwargs["conversation_variables"] == []
    assert kwargs["graph"] == {}
    assert kwargs["features"] == {}
    assert kwargs["unique_hash"] == HASH
    assert kwargs["session"] is session
    native[0].assert_not_called()
    native[1].bind_assessment_credential.assert_not_called()


def test_ordinary_invalid_json_preserves_original_400(native: tuple[MagicMock, MagicMock]) -> None:
    assert invoke({}, []) == ({"message": "Invalid JSON data"}, 400)
    native[0].assert_not_called()
    native[1].bind_assessment_credential.assert_not_called()
