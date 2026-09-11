"""Input preflight binds native context and schema before inspecting user values."""

from inspect import unwrap
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from controllers.console.explore.wraps import InstalledAppResource
from graphon.variables.input_entities import VariableEntity, VariableEntityType
from models import Account
from services.errors.message import MessageNotExistsError
from tests.unit_tests.controllers.console.explore.test_workbench_context import (
    fixture as fixture,  # noqa: PLC0414 -- explicit pytest fixture re-export
)


def invoke(fixture, app, payload, headers=None):
    module, account, workspace, installed = fixture
    resource = module.WorkbenchInputApi()
    with (
        app.test_request_context(
            "/",
            method="POST",
            headers=headers
            if headers is not None
            else {
                "X-Enterprise-Expected-Workspace": workspace,
                "X-Enterprise-Expected-Actor": account.id,
            },
        ),
        patch.object(Account, "current_tenant_id", new_callable=PropertyMock, return_value=workspace),
        patch.object(type(module.console_ns), "payload", new_callable=PropertyMock, return_value=payload),
    ):
        return unwrap(resource.post)(resource, MagicMock(), account, installed)


def test_inherits_native_app_access(fixture):
    assert fixture[0].WorkbenchInputApi.method_decorators is InstalledAppResource.method_decorators


def test_scoped_context_and_native_variables_precede_input_validation(fixture, app):
    module, account, workspace, installed = fixture
    values = {"temperature": "42", "document": {"transfer_method": "local_file", "upload_file_id": "selected"}}
    with (
        patch.object(module, "require_workbench_context") as context,
        patch.object(module, "read_workbench_variables") as variables,
        patch.object(module, "require_workbench_inputs") as inputs,
    ):
        body, status, headers = invoke(fixture, app, {"inputs": values})
    assert status == 200
    assert body == {
        "workspace_id": workspace,
        "actor_id": account.id,
        "installed_app_id": installed.id,
        "inputs_verified": True,
        "selected_files_verified": False,
        "branch_verified": False,
    }
    assert headers["Cache-Control"] == "private, no-store"
    assert variables.call_args.kwargs["conversation"] is context.return_value
    assert inputs.call_args.kwargs["variables"] is variables.return_value
    assert inputs.call_args.kwargs["inputs"] == values
    assert inputs.call_args.kwargs["user"] is account
    assert inputs.call_args.kwargs["tenant_id"] == installed.app.tenant_id
    context.call_args.kwargs["session"].commit.assert_not_called()


def test_identity_denial_stops_before_input_lookup(fixture, app):
    with patch.object(fixture[0], "require_workbench_context") as context:
        with pytest.raises(Conflict):
            invoke(fixture, app, {"inputs": {}}, headers={})
        context.assert_not_called()


def test_hidden_history_stops_before_variable_read(fixture, app):
    module = fixture[0]
    with (
        patch.object(module, "require_workbench_context", side_effect=MessageNotExistsError("private")),
        patch.object(module, "read_workbench_variables") as variables,
    ):
        with pytest.raises(NotFound):
            invoke(fixture, app, {"inputs": {}})
        variables.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [{"inputs": []}, {"inputs": {}, "variables": []}, {"inputs": {}, "query": "private"}, {"inputs": {}, "files": []}],
)
def test_only_inputs_and_context_fields_are_accepted(fixture, app, payload):
    with patch.object(fixture[0], "require_workbench_context") as context:
        with pytest.raises(ValidationError):
            invoke(fixture, app, payload)
        context.assert_not_called()


@pytest.mark.parametrize("step", ["read_workbench_variables", "require_workbench_inputs"])
def test_bad_config_or_input_details_are_opaque(fixture, app, step):
    module = fixture[0]
    with (
        patch.object(module, "require_workbench_context"),
        patch.object(module, "read_workbench_variables"),
        patch.object(module, "require_workbench_inputs"),
        patch.object(module, step, side_effect=ValueError("private input or file")),
    ):
        with pytest.raises(BadRequest) as caught:
            invoke(fixture, app, {"inputs": {}})
    assert caught.value.description == "Inputs unavailable"


@pytest.mark.parametrize("value", ["0", "not-a-number"])
def test_endpoint_calls_real_native_input_validation(fixture, app, value):
    module, _, workspace, installed = fixture
    installed.app.tenant_id = workspace
    variables = [
        VariableEntity(variable="temperature", label="Temperature", type=VariableEntityType.NUMBER, required=True)
    ]
    with (
        patch.object(module, "require_workbench_context", return_value=None),
        patch.object(module, "read_workbench_variables", return_value=variables),
    ):
        if value == "0":
            body, status, _ = invoke(fixture, app, {"inputs": {"temperature": value}})
            assert status == 200
            assert body["inputs_verified"] is True
            assert "temperature" not in body
        else:
            with pytest.raises(BadRequest):
                invoke(fixture, app, {"inputs": {"temperature": value}})
