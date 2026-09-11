"""Metadata-only managed draft reads retain native ordinary GET behavior without external I/O."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from controllers.console.app import workflow as controller
from services.agent.workflow_publish_service import WorkflowAgentPublishService

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
DRAFT = "33333333-3333-4333-8333-333333333333"
HASH = "a" * 64
HEADERS = {"X-Enterprise-Expected-Workspace": WS, "X-Enterprise-Draft-Operation": "read-assessment-draft"}


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    service = MagicMock()
    service.get_draft_workflow.return_value = SimpleNamespace(
        id=DRAFT, tenant_id=WS, app_id=APP, version="draft", unique_hash=HASH
    )
    factory = MagicMock(return_value=service)
    session = MagicMock()
    serialize = MagicMock()
    serialize.return_value.model_dump.return_value = {
        "id": DRAFT,
        "graph": {},
        "environment_variables": [{"value": "sensitive-native-snapshot"}],
    }
    projection = MagicMock(return_value={"projected": "native-agent-graph"})
    monkeypatch.setattr(controller, "WorkflowService", factory)
    monkeypatch.setattr(controller, "db", SimpleNamespace(session=session))
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    monkeypatch.setattr(controller.WorkflowResponse, "model_validate", serialize)
    monkeypatch.setattr(WorkflowAgentPublishService, "project_draft_bindings_to_graph", projection)
    return {
        "factory": factory,
        "service": service,
        "session": session,
        "serialize": serialize,
        "projection": projection,
    }


def read(headers: dict[str, str], tenant: str = WS) -> object:
    api = controller.DraftWorkflowApi()
    with Flask(__name__).test_request_context("/", method="GET", headers=headers):
        return unwrap(api.get)(api, SimpleNamespace(id=APP, tenant_id=tenant))


def test_managed_read_is_exact_metadata_without_variable_serialization_or_projection(
    native: dict[str, MagicMock],
) -> None:
    result = read(HEADERS)
    assert result == (
        {"app_id": APP, "draft_id": DRAFT, "hash": HASH, "version": "draft"},
        200,
        {"X-Enterprise-Workspace": WS},
    )
    kwargs = native["service"].get_draft_workflow.call_args.kwargs
    assert kwargs["app_model"].id == APP
    assert kwargs["app_model"].tenant_id == WS
    assert kwargs["session"] is native["session"].return_value
    native["serialize"].assert_not_called()
    native["projection"].assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
def test_ordinary_read_keeps_full_native_projection(enabled: bool, native: dict[str, MagicMock]) -> None:
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    response = read({})
    assert response == {
        "id": DRAFT,
        "graph": {"projected": "native-agent-graph"},
        "environment_variables": [{"value": "sensitive-native-snapshot"}],
    }
    native["serialize"].assert_called_once()
    native["projection"].assert_called_once()


@pytest.mark.parametrize(
    ("headers", "tenant", "enabled", "error"),
    [
        (HEADERS, WS, False, Forbidden),
        (HEADERS, APP, True, Conflict),
        ({"X-Enterprise-Expected-Workspace": WS}, WS, True, BadRequest),
        ({"X-Enterprise-Draft-Operation": "read-assessment-draft"}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Draft-Operation": "bind-assessment-credential"}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Workspace": ""}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Workspace": "bad"}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Workspace": "00000000-0000-0000-0000-000000000000"}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Expected-Workspace": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}, WS, True, BadRequest),
    ],
)
def test_invalid_scope_stops_before_service_or_session(
    headers: dict[str, str], tenant: str, enabled: bool, error: type[Exception], native: dict[str, MagicMock]
) -> None:
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    with pytest.raises(error):
        read(headers, tenant)
    for mock in native.values():
        mock.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("tenant_id", APP), ("app_id", DRAFT), ("version", "published"), ("id", "bad"), ("unique_hash", "BAD")],
)
def test_returned_draft_scope_and_metadata_must_be_exact(field: str, value: str, native: dict[str, MagicMock]) -> None:
    setattr(native["service"].get_draft_workflow.return_value, field, value)
    with pytest.raises(Conflict):
        read(HEADERS)
    native["serialize"].assert_not_called()
    native["projection"].assert_not_called()


@pytest.mark.parametrize("managed", [False, True])
def test_missing_draft_retains_native_not_found(managed: bool, native: dict[str, MagicMock]) -> None:
    native["service"].get_draft_workflow.return_value = None
    with pytest.raises(controller.DraftWorkflowNotExist):
        read(HEADERS if managed else {})
    native["projection"].assert_not_called()
