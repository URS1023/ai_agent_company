"""Guard expected workbench identity before delegating to the original native chat handler."""

from inspect import unwrap
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError
from werkzeug.exceptions import Conflict

import controllers.console.explore.completion as completion_module
from controllers.console.explore.completion import ChatApi
from controllers.console.explore.wraps import InstalledAppResource
from models import Account
from tests.unit_tests.controllers.console.explore.test_workbench_context import (
    fixture as fixture,  # noqa: PLC0414 -- explicit pytest fixture re-export
)


def invoke(fixture, app, payload=None, headers=None):
    module, account, workspace, installed = fixture
    resource = module.WorkbenchChatApi()
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
        patch.object(
            type(module.console_ns),
            "payload",
            new_callable=PropertyMock,
            return_value=payload if payload is not None else {"inputs": {}, "query": "hello"},
        ),
    ):
        return unwrap(resource.post)(resource, account, installed)


def test_route_keeps_original_native_access_decorators(fixture):
    assert fixture[0].WorkbenchChatApi.method_decorators is InstalledAppResource.method_decorators


def test_expected_identity_delegates_once_and_preserves_native_stream_response(fixture, app):
    sentinel = object()
    with patch.object(ChatApi, "post", return_value=sentinel) as native:
        assert invoke(fixture, app) is sentinel
    native.assert_called_once_with(installed_app=fixture[3])


@pytest.mark.parametrize(
    "headers", [{}, {"X-Enterprise-Expected-Workspace": "foreign"}, {"X-Enterprise-Expected-Actor": "foreign"}]
)
def test_identity_denial_precedes_native_side_effects(fixture, app, headers):
    with patch.object(ChatApi, "post") as native:
        with pytest.raises(Conflict):
            invoke(fixture, app, headers=headers)
        native.assert_not_called()


@pytest.mark.parametrize(
    "extra", [{"model_config": {}}, {"workflow_id": "private"}, {"response_mode": "blocking"}, {"user": "other"}]
)
def test_rejects_noninstalled_chat_fields_before_native_handler(fixture, app, extra):
    with patch.object(ChatApi, "post") as native:
        with pytest.raises(ValidationError):
            invoke(fixture, app, payload={"inputs": {}, "query": "hello", **extra})
        native.assert_not_called()


def test_delegation_runs_original_account_and_session_wrappers(fixture, app):
    _, account, _, installed = fixture
    sentinel = object()
    session = MagicMock()
    with (
        patch("controllers.console.wraps.current_account_with_tenant", return_value=(account, None)) as identity,
        patch("controllers.common.session.session_factory") as sessions,
        patch.object(completion_module, "db") as db,
        patch.object(completion_module.AppGenerateService, "generate", return_value=sentinel) as generate,
        patch.object(completion_module.helper, "compact_generate_response", return_value=sentinel),
    ):
        sessions.get_session_maker.return_value.begin.return_value.__enter__.return_value = session
        assert invoke(fixture, app, payload={"inputs": {"count": "0"}, "query": "  hello\n"}) is sentinel
        identity.assert_called_once()
        db.session.commit.assert_called_once()
        generate.assert_called_once()
    assert generate.call_args.kwargs["user"] is account
    assert generate.call_args.kwargs["session"] is session
    assert generate.call_args.kwargs["app_model"] is installed.app
    assert generate.call_args.kwargs["streaming"] is True
    assert generate.call_args.kwargs["args"]["query"] == "  hello\n"
    assert generate.call_args.kwargs["args"]["inputs"] == {"count": "0"}
    assert generate.call_args.kwargs["args"]["auto_generate_name"] is False
