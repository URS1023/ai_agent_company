"""Attachment preflight checks context first and never dispatches generation."""

from inspect import unwrap
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from controllers.console.explore.wraps import InstalledAppResource
from models import Account
from services.errors.message import MessageNotExistsError
from tests.unit_tests.controllers.console.explore.test_workbench_context import (
    fixture as fixture,  # noqa: PLC0414 -- explicit pytest fixture re-export
)


def invoke(fixture, app, payload, headers=None):
    module, account, workspace, installed = fixture
    resource = module.WorkbenchAttachmentApi()
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


def test_inherits_native_access_decorators(fixture):
    assert fixture[0].WorkbenchAttachmentApi.method_decorators is InstalledAppResource.method_decorators


def test_checks_history_then_effective_config_then_every_attachment(fixture, app):
    module, account, workspace, installed = fixture
    files = [{"transfer_method": "local_file", "upload_file_id": "selected"}]
    with (
        patch.object(module, "require_workbench_context") as context,
        patch.object(module, "read_workbench_attachment_config") as config,
        patch.object(module, "require_workbench_attachments") as attachments,
    ):
        result, status, headers = invoke(fixture, app, {"files": files})
    assert status == 200
    assert result["workspace_id"] == workspace
    assert result["actor_id"] == account.id
    assert result["installed_app_id"] == installed.id
    assert result["selected_files_verified"] is True
    assert result["input_files_verified"] is False
    assert result["branch_verified"] is False
    assert result["file_count"] == 1
    assert headers["Cache-Control"] == "private, no-store"
    assert config.call_args.kwargs["conversation"] is context.return_value
    assert attachments.call_args.kwargs["config"] is config.return_value
    assert attachments.call_args.kwargs["mappings"] == files
    assert attachments.call_args.kwargs["tenant_id"] == installed.app.tenant_id
    context.call_args.kwargs["session"].commit.assert_not_called()


def test_disabled_uploads_reject_selected_files_before_resolution(fixture, app):
    module = fixture[0]
    with (
        patch.object(module, "require_workbench_context"),
        patch.object(module, "read_workbench_attachment_config", return_value=None),
        patch.object(module, "require_workbench_attachments") as attachments,
    ):
        with pytest.raises(BadRequest):
            invoke(fixture, app, {"files": [{"transfer_method": "local_file"}]})
        attachments.assert_not_called()


def test_identity_mismatch_precedes_all_file_work(fixture, app):
    with patch.object(fixture[0], "require_workbench_context") as context:
        with pytest.raises(Conflict):
            invoke(fixture, app, {"files": []}, headers={})
        context.assert_not_called()


@pytest.mark.parametrize("payload", [{"files": "wrong"}, {"files": [], "inputs": {}}, {"files": [], "query": "secret"}])
def test_rejects_generation_fields_and_invalid_file_list(fixture, app, payload):
    with patch.object(fixture[0], "require_workbench_context") as context:
        with pytest.raises(ValidationError):
            invoke(fixture, app, payload)
        context.assert_not_called()


def test_hidden_history_stops_before_configuration_or_file_resolution(fixture, app):
    module = fixture[0]
    with (
        patch.object(module, "require_workbench_context", side_effect=MessageNotExistsError("private")),
        patch.object(module, "read_workbench_attachment_config") as config,
        patch.object(module, "require_workbench_attachments") as attachments,
    ):
        with pytest.raises(NotFound) as caught:
            invoke(fixture, app, {"files": []})
        config.assert_not_called()
        attachments.assert_not_called()
    assert "private" not in caught.value.description


@pytest.mark.parametrize("failing_step", ["read_workbench_attachment_config", "require_workbench_attachments"])
def test_attachment_failures_do_not_expose_details(fixture, app, failing_step):
    module = fixture[0]
    with (
        patch.object(module, "require_workbench_context"),
        patch.object(module, "read_workbench_attachment_config"),
        patch.object(module, "require_workbench_attachments"),
        patch.object(module, failing_step, side_effect=ValueError("private URL or file name")),
    ):
        with pytest.raises(BadRequest) as caught:
            invoke(fixture, app, {"files": []})
    assert caught.value.description == "Selected attachments unavailable"


def test_empty_selection_is_valid_when_uploads_are_disabled(fixture, app):
    module = fixture[0]
    with (
        patch.object(module, "require_workbench_context"),
        patch.object(module, "read_workbench_attachment_config", return_value=None),
        patch.object(module, "require_workbench_attachments") as attachments,
    ):
        body, status, _ = invoke(fixture, app, {"files": []})
    assert status == 200
    assert body["file_count"] == 0
    assert attachments.call_args.kwargs["mappings"] == []
