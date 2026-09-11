"""Native registration client tests use an HTTP mock transport, never a service."""

from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from core.workflow.enterprise_execution import ManagedToolRegistry
from core.workflow.enterprise_registration import (
    NativeRegistrationClient,
    RegistrationUnavailableError,
    resolve_factory_registry,
)

WS, APP, WORKFLOW, CREDENTIAL = (str(UUID(int=i)) for i in range(1, 5))
TOKEN = "fixture-registration-" + "a" * 40


def receipt():
    return {
        "workspace_id": WS,
        "app_id": APP,
        "workflow_id": WORKFLOW,
        "credential_id": CREDENTIAL,
        "graph_hash": "a" * 64,
        "provider_id": "enterprise/enterprise_device_assessment/enterprise_device",
        "tool_name": "evaluate_device",
        "node_id": "assessment",
    }


def test_exact_registration_uses_only_dedicated_service_credential():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=receipt())

    client = NativeRegistrationClient(
        "https://enterprise.test", SecretStr(TOKEN), transport=httpx.MockTransport(respond)
    )
    result = client.fetch(WS, APP, WORKFLOW)
    assert len(result.registrations) == 1
    assert result.registrations[0].credential_id == CREDENTIAL
    assert len(calls) == 1
    assert calls[0].url.path == "/enterprise/internal/v1/workflow-registration"
    assert dict(calls[0].url.params) == {"workspace_id": WS, "app_id": APP, "workflow_id": WORKFLOW}
    assert calls[0].headers["x-enterprise-registration-token"] == TOKEN
    assert "authorization" not in calls[0].headers
    assert "cookie" not in calls[0].headers


def test_null_is_an_empty_registry():
    client = NativeRegistrationClient(
        "https://enterprise.test",
        SecretStr(TOKEN),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=None, content=b"null")),
    )
    assert client.fetch(WS, APP, WORKFLOW).registrations == ()


@pytest.mark.parametrize("change", ["scope", "extra", "oversized", "redirect", "failure"])
def test_invalid_or_unavailable_response_is_not_adopted_or_retried(change):
    calls = []

    def respond(request):
        calls.append(request)
        value = receipt()
        if change == "scope":
            value["app_id"] = str(UUID(int=99))
        if change == "extra":
            value["secret"] = "private"
        if change == "oversized":
            return httpx.Response(200, content=b"x" * 8193)
        if change == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.test"})
        if change == "failure":
            return httpx.Response(503, text="private failure")
        return httpx.Response(200, json=value)

    client = NativeRegistrationClient(
        "https://enterprise.test", SecretStr(TOKEN), transport=httpx.MockTransport(respond)
    )
    with pytest.raises(RegistrationUnavailableError) as error:
        client.fetch(WS, APP, WORKFLOW)
    assert "private" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://enterprise.test",
        "https://user:pass@enterprise.test",
        "https://enterprise.test/path",
        "https://enterprise.test?query=1",
    ],
)
def test_invalid_origin_is_rejected(url):
    with pytest.raises(ValueError):
        NativeRegistrationClient(url, SecretStr(TOKEN))


def test_factory_policy_keeps_non_service_api_off_transport():
    client = NativeRegistrationClient(
        "https://enterprise.test",
        SecretStr(TOKEN),
        transport=httpx.MockTransport(lambda _: pytest.fail("No registration request for browser/debug execution")),
    )
    assert (
        resolve_factory_registry(ManagedToolRegistry(), client, WS, APP, WORKFLOW, invoke_from="debugger").registrations
        == ()
    )


def test_factory_policy_deduplicates_exact_static_dynamic_registration():
    import json

    value = receipt()
    value.pop("graph_hash")
    static = ManagedToolRegistry.from_json(json.dumps([value]))
    client = NativeRegistrationClient(
        "https://enterprise.test",
        SecretStr(TOKEN),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=receipt())),
    )
    assert resolve_factory_registry(static, client, WS, APP, WORKFLOW, invoke_from="service-api") == static


def test_factory_policy_rejects_conflicting_registration_for_same_node():
    import json

    value = receipt()
    value.pop("graph_hash")
    value["credential_id"] = str(UUID(int=99))
    static = ManagedToolRegistry.from_json(json.dumps([value]))
    client = NativeRegistrationClient(
        "https://enterprise.test",
        SecretStr(TOKEN),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=receipt())),
    )
    with pytest.raises(RegistrationUnavailableError):
        resolve_factory_registry(static, client, WS, APP, WORKFLOW, invoke_from="service-api")
