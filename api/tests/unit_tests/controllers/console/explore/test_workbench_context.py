"""Context preflight is read-only and never substitutes for full send authority."""

from importlib import import_module
from inspect import unwrap
from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from flask import Flask
from pydantic import ValidationError
from werkzeug.exceptions import Conflict, NotFound

from controllers.console.explore.wraps import InstalledAppResource
from models import Account
from models.model import AppMode
from services.app_generate_service import AppGenerateService
from services.errors.message import MessageNotExistsError


@pytest.fixture
def fixture():
    module = import_module("controllers.console.explore.workbench_context")
    account = Account(name="User", email="user@example.test")
    account.id = str(uuid4())
    workspace = str(uuid4())
    installed = MagicMock(id=str(uuid4()), tenant_id=workspace, app=MagicMock(mode=AppMode.CHAT))
    return module, account, workspace, installed


def invoke(fixture, app: Flask, headers=None, payload=None):
    module, account, workspace, installed = fixture
    resource = module.WorkbenchContextApi()
    with (
        app.test_request_context(
            "/",
            method="POST",
            headers=headers
            if headers is not None
            else {"X-Enterprise-Expected-Workspace": workspace, "X-Enterprise-Expected-Actor": account.id},
        ),
        patch.object(Account, "current_tenant_id", new_callable=PropertyMock, return_value=workspace),
        patch.object(type(module.console_ns), "payload", new_callable=PropertyMock, return_value=payload or {}),
    ):
        return unwrap(resource.post)(resource, MagicMock(), account, installed)


def test_inherits_native_login_installation_and_app_access(fixture):
    module = fixture[0]
    assert module.WorkbenchContextApi.method_decorators is InstalledAppResource.method_decorators


def test_success_binds_identity_and_explicitly_does_not_verify_files_or_branches(fixture, app):
    module, account, workspace, installed = fixture
    with (
        patch.object(module, "require_workbench_context") as check,
        patch.object(AppGenerateService, "generate") as generate,
    ):
        body, status, headers = invoke(fixture, app)
    assert status == 200
    assert body == {
        "workspace_id": workspace,
        "actor_id": account.id,
        "installed_app_id": installed.id,
        "context_verified": True,
        "attachments_verified": False,
        "branch_verified": False,
    }
    assert headers["Cache-Control"] == "private, no-store"
    assert check.call_args.kwargs["user"] is account
    assert check.call_args.kwargs["app_model"] is installed.app
    generate.assert_not_called()
    check.call_args.kwargs["session"].commit.assert_not_called()


@pytest.mark.parametrize(
    "headers", [{}, {"X-Enterprise-Expected-Workspace": "foreign"}, {"X-Enterprise-Expected-Actor": "foreign"}]
)
def test_identity_mismatch_stops_before_history_read(fixture, app, headers):
    with patch.object(fixture[0], "require_workbench_context") as check:
        with pytest.raises(Conflict):
            invoke(fixture, app, headers)
        check.assert_not_called()


def test_installed_workspace_mismatch_stops_before_history_read(fixture, app):
    fixture[3].tenant_id = "foreign"
    with patch.object(fixture[0], "require_workbench_context") as check:
        with pytest.raises(Conflict):
            invoke(fixture, app)
        check.assert_not_called()


def test_hidden_parent_returns_opaque_not_found(fixture, app):
    with patch.object(fixture[0], "require_workbench_context", side_effect=MessageNotExistsError("private")):
        with pytest.raises(NotFound) as error:
            invoke(fixture, app)
    assert "private" not in error.value.description


@pytest.mark.parametrize("field", ["X-Enterprise-Expected-Workspace", "X-Enterprise-Expected-Actor"])
def test_each_identity_binding_is_required_independently(fixture, app, field):
    module, account, workspace, _ = fixture
    headers = {"X-Enterprise-Expected-Workspace": workspace, "X-Enterprise-Expected-Actor": account.id}
    headers[field] = str(uuid4())
    with patch.object(module, "require_workbench_context") as check:
        with pytest.raises(Conflict):
            invoke(fixture, app, headers)
        check.assert_not_called()


def test_context_ids_reach_native_check_without_query_or_model_fields(fixture, app):
    conversation, parent = str(uuid4()), str(uuid4())
    with patch.object(fixture[0], "require_workbench_context") as check:
        invoke(fixture, app, payload={"conversation_id": conversation, "parent_message_id": parent})
    assert check.call_args.kwargs["conversation_id"] == conversation
    assert check.call_args.kwargs["parent_message_id"] == parent


@pytest.mark.parametrize("payload", [{"query": "private"}, {"conversation_id": "bad"}, {"parent_message_id": False}])
def test_invalid_context_payload_stops_before_history_read(fixture, app, payload):
    with patch.object(fixture[0], "require_workbench_context") as check:
        with pytest.raises(ValidationError):
            invoke(fixture, app, payload=payload)
        check.assert_not_called()
