"""Managed app-key issuance exercises real controllers without database access."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from flask import Flask
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from controllers.console import apikey as controller

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
HEADERS = {"X-Enterprise-Expected-Workspace": WS, "X-Enterprise-Key-Operation": "issue-workflow-key"}
ITEM = {"id": "key-id", "type": "app", "token": "app-fixture", "last_used_at": None, "created_at": None}


@pytest.fixture
def native(monkeypatch):
    create = MagicMock(return_value=SimpleNamespace(**ITEM))
    monkeypatch.setattr(controller.BaseApiKeyListResource, "_create_api_key", create)
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    return create


def issue(headers, tenant=WS, resource_class=controller.AppApiKeyListResource):
    api = resource_class()
    with Flask(__name__).test_request_context("/", method="POST", headers=headers):
        return unwrap(api.post)(api, tenant, UUID(APP))


def test_managed_issue_returns_exact_new_key_and_scope_ack(native):
    assert issue(HEADERS) == (
        {**ITEM, "app_id": APP},
        201,
        {"X-Enterprise-Workspace": WS, "Cache-Control": "private, no-store"},
    )
    native.assert_called_once_with(APP, WS)


@pytest.mark.parametrize("enabled", [True, False])
def test_ordinary_issue_preserves_native_response(native, enabled):
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    assert issue({}) == (ITEM, 201)
    native.assert_called_once_with(APP, WS)


@pytest.mark.parametrize(
    ("headers", "tenant", "enabled", "error"),
    [
        (HEADERS, WS, False, Forbidden),
        (HEADERS, APP, True, Conflict),
        ({"X-Enterprise-Expected-Workspace": WS}, WS, True, BadRequest),
        ({"X-Enterprise-Key-Operation": "issue-workflow-key"}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Key-Operation": ""}, WS, True, BadRequest),
        ({**HEADERS, "X-Enterprise-Key-Operation": "other"}, WS, True, BadRequest),
        *[
            ({**HEADERS, "X-Enterprise-Expected-Workspace": value}, WS, True, BadRequest)
            for value in [
                "",
                "bad",
                "0" * 32,
                "00000000-0000-0000-0000-000000000000",
                WS.replace("-", ""),
                " " + WS,
                "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            ]
        ],
    ],
)
def test_managed_invalid_scope_stops_before_creation(native, headers, tenant, enabled, error):
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    with pytest.raises(error):
        issue(headers, tenant)
    native.assert_not_called()


def test_dataset_issue_ignores_managed_app_headers(native):
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = False
    assert issue(HEADERS, resource_class=controller.DatasetApiKeyListResource) == (ITEM, 201)
    native.assert_called_once_with(APP, WS)


@pytest.mark.parametrize("headers", [{}, HEADERS])
def test_native_limit_preserved_before_generation(monkeypatch, headers):
    session = MagicMock()
    session.scalar.return_value = 10
    resource = MagicMock()
    generate = MagicMock()
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    monkeypatch.setattr(controller, "_get_resource", resource)
    monkeypatch.setattr(controller, "db", SimpleNamespace(session=session))
    monkeypatch.setattr(controller.ApiToken, "generate_api_key", generate)
    with pytest.raises(BadRequest):
        issue(headers)
    resource.assert_called_once_with(APP, WS, controller.App)
    generate.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("headers", [{}, HEADERS])
def test_native_resource_denial_prevents_generation(monkeypatch, headers):
    resource = MagicMock(side_effect=Forbidden())
    generate = MagicMock()
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    monkeypatch.setattr(controller, "_get_resource", resource)
    monkeypatch.setattr(controller.ApiToken, "generate_api_key", generate)
    with pytest.raises(Forbidden):
        issue(headers)
    resource.assert_called_once_with(APP, WS, controller.App)
    generate.assert_not_called()


def test_managed_key_schema_registered_without_replacing_native_schema():
    assert "WorkflowKeyItem" in controller.console_ns.models
    assert "ApiKeyItem" in controller.console_ns.models
