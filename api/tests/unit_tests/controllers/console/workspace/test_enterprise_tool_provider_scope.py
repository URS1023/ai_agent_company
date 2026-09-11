"""Exercise opt-in tool credential scope against real native controllers without external I/O."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from controllers.console.workspace import tool_providers as controller
from models.account import Account, Tenant

WORKSPACE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PROVIDER = "org/plugin/tool"
ROUTES = ("providers", "tools", "info", "add")
CLASSES = {
    "providers": controller.ToolProviderListApi,
    "tools": controller.ToolBuiltinProviderListToolsApi,
    "info": controller.ToolBuiltinProviderGetCredentialInfoApi,
    "add": controller.ToolBuiltinProviderAddApi,
}


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    services = {
        "providers": MagicMock(return_value=[{"provider": PROVIDER}]),
        "tools": MagicMock(return_value=[{"tool": "evaluate_device"}]),
        "info": MagicMock(return_value={"credentials": []}),
        "add": MagicMock(return_value={"result": "success"}),
    }
    monkeypatch.setattr(controller.ToolCommonService, "list_tool_providers", services["providers"])
    for method, route in (
        ("list_builtin_tool_provider_tools", "tools"),
        ("get_builtin_tool_provider_credential_info", "info"),
        ("add_builtin_tool_provider", "add"),
    ):
        monkeypatch.setattr(controller.BuiltinToolManageService, method, services[route])
    monkeypatch.setattr(controller, "_dump_tool_provider_payload_list", lambda value: value)
    monkeypatch.setattr(controller, "dump_response", lambda schema, value: value)
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    services["session"] = MagicMock()
    monkeypatch.setattr(controller, "db", SimpleNamespace(session=services["session"]))
    return services


def invoke(route: str, expected: str | None, tenant_id: str = WORKSPACE, user_tenant: str | None = WORKSPACE) -> object:
    account = Account(name="Native user", email="native@example.test")
    account.id = "33333333-3333-4333-8333-333333333333"
    if user_tenant is not None:
        tenant = Tenant(name="Native workspace")
        tenant.id = user_tenant
        account._current_tenant = tenant
    api = CLASSES[route]()
    method = "post" if route == "add" else "get"
    with Flask(__name__).test_request_context(
        "/?type=builtin&include_credential_ids=credential-a",
        method=method.upper(),
        headers={} if expected is None else {"X-Enterprise-Expected-Workspace": expected},
        json={"type": "api-key", "credentials": {"test_key": "fixture"}, "name": "owned", "visibility": "private"},
    ):
        if route == "providers":
            return unwrap(api.get)(api, tenant_id=tenant_id, user=account)
        if route == "tools":
            return unwrap(api.get)(api, tenant_id=tenant_id, provider=PROVIDER)
        return unwrap(getattr(api, method))(api, tenant_id=tenant_id, user=account, provider=PROVIDER)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("managed", [False, True])
def test_scope_ack_preserves_service_arguments_and_ordinary_body(
    route: str, managed: bool, native: dict[str, MagicMock]
) -> None:
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = managed
    result = invoke(route, WORKSPACE if managed else None)
    body = native[route].return_value
    assert result == (body, 200, {"X-Enterprise-Workspace": WORKSPACE}) if managed else result == body
    call = native[route].call_args
    if route == "providers":
        assert call.args == ("33333333-3333-4333-8333-333333333333", WORKSPACE, "builtin")
    elif route == "tools":
        assert call.args == (WORKSPACE, PROVIDER)
    else:
        assert call.kwargs["tenant_id"] == WORKSPACE
        assert call.kwargs["provider"] == PROVIDER
        if route == "info":
            assert call.kwargs["user"].current_tenant_id == WORKSPACE
            assert call.kwargs["include_credential_ids"] == ["credential-a"]
        else:
            assert call.kwargs["credentials"] == {"test_key": "fixture"}
            assert call.kwargs["name"] == "owned"
            assert call.kwargs["visibility"] == "private"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("expected", "tenant_id", "enabled", "error"),
    [
        (WORKSPACE, WORKSPACE, False, Forbidden),
        (WORKSPACE, OTHER, True, Conflict),
        ("", WORKSPACE, True, BadRequest),
        ("not-a-uuid", WORKSPACE, True, BadRequest),
        ("00000000-0000-0000-0000-000000000000", WORKSPACE, True, BadRequest),
        ("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", WORKSPACE, True, BadRequest),
        ("11111111111141118111111111111111", WORKSPACE, True, BadRequest),
    ],
)
def test_scope_failure_precedes_services_and_session(
    route: str, expected: str, tenant_id: str, enabled: bool, error: type[Exception], native: dict[str, MagicMock]
) -> None:
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    with pytest.raises(error):
        invoke(route, expected, tenant_id=tenant_id)
    for service in native.values():
        service.assert_not_called()


@pytest.mark.parametrize("route", ["providers", "info", "add"])
@pytest.mark.parametrize("user_tenant", [OTHER, None])
def test_same_cached_account_tenant_must_match_service_tenant(
    route: str, user_tenant: str | None, native: dict[str, MagicMock]
) -> None:
    with pytest.raises(Conflict):
        invoke(route, WORKSPACE, user_tenant=user_tenant)
    for service in native.values():
        service.assert_not_called()
