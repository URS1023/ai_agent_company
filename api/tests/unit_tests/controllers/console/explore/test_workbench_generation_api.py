from inspect import unwrap
from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from werkzeug.exceptions import Conflict, NotFound

from controllers.console.explore.wraps import InstalledAppResource
from models import Account
from models.enums import MessageStatus
from services.errors.message import MessageNotExistsError
from services.workbench_context_service import WorkbenchGenerationState
from tests.unit_tests.controllers.console.explore.test_workbench_context import (
    fixture as fixture,  # noqa: PLC0414 -- explicit pytest fixture re-export
)


def invoke(fixture, app, *, headers=None, payload=None):
    module, account, workspace, installed = fixture
    resource = module.WorkbenchGenerationStateApi()
    session = MagicMock()
    with (
        app.test_request_context(
            "/",
            method="POST",
            headers=headers
            if headers is not None
            else {"X-Enterprise-Expected-Workspace": workspace, "X-Enterprise-Expected-Actor": account.id},
        ),
        patch.object(Account, "current_tenant_id", new_callable=PropertyMock, return_value=workspace),
        patch.object(type(module.console_ns), "payload", new_callable=PropertyMock, return_value=payload),
    ):
        return unwrap(resource.post)(resource, session, account, installed)


def test_state_route_preserves_native_access_decorators(fixture):
    assert fixture[0].WorkbenchGenerationStateApi.method_decorators is InstalledAppResource.method_decorators


def test_state_route_binds_identity_and_returns_only_observation(fixture, app):
    module, account, workspace, installed = fixture
    payload = {"conversation_id": str(uuid4()), "message_id": str(uuid4())}
    with (
        patch.object(module.DifyAPIRepositoryFactory, "create_api_workflow_run_repository") as factory,
        patch.object(
            module,
            "read_workbench_generation_state",
            return_value=WorkbenchGenerationState(MessageStatus.PAUSED, None, None, False),
        ) as read,
    ):
        body, status, headers = invoke(fixture, app, payload=payload)
    assert status == 200
    assert body == {
        "workspace_id": workspace,
        "actor_id": account.id,
        "installed_app_id": installed.id,
        **payload,
        "message_status": "paused",
        "workflow_run_id": None,
        "workflow_status": None,
        "workflow_finished": False,
        "message_terminal": None,
    }
    assert headers["Cache-Control"] == "private, no-store"
    assert read.call_args.kwargs["user"] is account
    assert read.call_args.kwargs["workflow_runs"] is factory.return_value
    assert read.call_args.kwargs["message_id"] == payload["message_id"]
    read.call_args.kwargs["session"].commit.assert_not_called()


def test_identity_mismatch_prevents_repository_construction(fixture, app):
    module = fixture[0]
    with patch.object(module.DifyAPIRepositoryFactory, "create_api_workflow_run_repository") as factory:
        with pytest.raises(Conflict):
            invoke(fixture, app, headers={})
        factory.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"conversation_id": "bad", "message_id": "bad"},
        {"conversation_id": str(uuid4()), "message_id": str(uuid4()), "outcome": "succeeded"},
    ],
)
def test_invalid_or_claimed_outcome_payload_rejected(fixture, app, payload):
    module = fixture[0]
    with patch.object(module.DifyAPIRepositoryFactory, "create_api_workflow_run_repository") as factory:
        with pytest.raises(ValidationError):
            invoke(fixture, app, payload=payload)
        factory.assert_not_called()


def test_inaccessible_message_does_not_expose_native_error_details(fixture, app):
    module = fixture[0]
    with (
        patch.object(module.DifyAPIRepositoryFactory, "create_api_workflow_run_repository"),
        patch.object(module, "read_workbench_generation_state", side_effect=MessageNotExistsError("private")),
    ):
        with pytest.raises(NotFound) as error:
            invoke(fixture, app, payload={"conversation_id": str(uuid4()), "message_id": str(uuid4())})
    assert "private" not in error.value.description


def test_state_route_exposes_only_validated_plain_terminal(fixture, app):
    from core.app.task_pipeline.workbench_terminal_metadata import PlainChatTerminalMetadata

    module = fixture[0]
    payload = {"conversation_id": str(uuid4()), "message_id": str(uuid4())}
    marker = PlainChatTerminalMetadata(version=1, task_id="task", outcome="stopped", stop_reason="user_manual")
    with (
        patch.object(module.DifyAPIRepositoryFactory, "create_api_workflow_run_repository"),
        patch.object(
            module,
            "read_workbench_generation_state",
            return_value=WorkbenchGenerationState(MessageStatus.NORMAL, None, None, False, marker),
        ),
    ):
        body, status, _ = invoke(fixture, app, payload=payload)
    assert status == 200
    assert body["message_terminal"] == marker.model_dump(mode="json")
    assert "answer" not in body
    assert "inputs" not in body
