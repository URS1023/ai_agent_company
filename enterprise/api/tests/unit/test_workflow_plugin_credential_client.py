import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from enterprise_platform.adapters.workflow_plugin_credential_client import DifyWorkflowPluginCredentialClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_plugin_credentials import (
    PluginCredentialConfiguration,
    PluginCredentialRejected,
)
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "f19ff571-2297-43fe-8c30-e7e103da664e"
APP = "a595ef4b-2a37-4531-93e6-e85a8691338f"
OP = "d9e7c318-1e4b-4678-a5b4-07d2b1b4a8d1"
CRED = "7b593aa9-9c30-4f3d-8127-3a441c64c6d7"
PROVIDER = "enterprise/enterprise_device_assessment/enterprise_device"
PLUGIN = "enterprise/enterprise_device_assessment"
PIN = PLUGIN + ":0.1.0@digest"
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession("access_token=native; csrf_token=csrf; refresh_token=private", None, "csrf")


def configuration():
    return PluginCredentialConfiguration(
        origin="https://enterprise.internal",
        key_id="key-one",
        signing_secret=SecretStr("private-signing-secret-0123456789012345"),
        expected_plugin_unique_identifier=PIN,
    )


def entry():
    return dict(
        id=CRED,
        name="ent-" + OP.replace("-", "")[:26],
        provider=PROVIDER,
        credential_type="api-key",
        visibility="all_team_members",
        created_by="actor",
        from_other_member=False,
        credentials=dict(
            origin="https://enterprise.internal",
            key_id="key-one",
            expected_app_id=APP,
            allow_insecure_http=False,
            secret="masked",
        ),
    )


def handler_factory(change=None):
    calls = []

    def handle(request):
        calls.append(request)
        index = len(calls)
        bodies = [
            dict(enabled=True, credential_setup_enabled=True, workspace_id=WS),
            [dict(id=PROVIDER, name=PROVIDER, plugin_id=PLUGIN, plugin_unique_identifier=PIN)],
            [dict(name="evaluate_device")],
            dict(supported_credential_types=["api-key"], credentials=[]),
            dict(result="success"),
            dict(supported_credential_types=["api-key"], credentials=[entry()]),
        ]
        body = bodies[index - 1]
        if change:
            body = change(index, body)
        return httpx.Response(200, json=body, headers={"X-Enterprise-Workspace": WS})

    return calls, handle


def prepare(handler, **kwargs):
    client = DifyWorkflowPluginCredentialClient(
        base_url="https://dify.internal/console/api",
        configuration=configuration(),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    return asyncio.run(client.prepare(PRINCIPAL, SESSION, app_id=APP, operation_id=OP))


def test_confirmed_post_and_exact_readback_only_create_credential():
    calls, handler = handler_factory()
    result = prepare(handler)
    assert result.state == "credential_created" and result.credential_id == CRED
    assert result.plugin_unique_identifier == PIN
    assert [call.method for call in calls] == ["GET", "GET", "GET", "GET", "POST", "GET"]
    for call in calls:
        assert call.headers["X-Enterprise-Expected-Workspace"] == WS
        assert "refresh_token" not in call.headers["Cookie"]
    body = json.loads(calls[4].content)
    assert body == dict(
        name=entry()["name"],
        type="api-key",
        visibility="all_team_members",
        credentials=dict(
            origin="https://enterprise.internal",
            key_id="key-one",
            secret=configuration().signing_secret.get_secret_value(),
            expected_app_id=APP,
            allow_insecure_http=False,
        ),
    )
    assert "private-signing" not in repr(configuration()) + configuration().model_dump_json() + repr(result)


@pytest.mark.parametrize(
    "step,replacement",
    [
        (1, dict(enabled=True, workspace_id=WS)),
        (2, []),
        (3, []),
        (4, dict(supported_credential_types=[], credentials=[])),
    ],
)
def test_preflight_rejects_without_post(step, replacement):
    calls, handler = handler_factory(lambda index, body: replacement if index == step else body)
    with pytest.raises(PluginCredentialRejected):
        prepare(handler)
    assert not any(call.method == "POST" for call in calls)


@pytest.mark.parametrize(
    "field,value", [("plugin_unique_identifier", "wrong"), ("plugin_id", "wrong"), ("name", "wrong")]
)
def test_operator_pinned_provider_identity_is_required(field, value):
    def change(index, body):
        if index == 2:
            body[0][field] = value
        return body

    calls, handler = handler_factory(change)
    with pytest.raises(PluginCredentialRejected):
        prepare(handler)
    assert len(calls) == 2


def test_existing_owned_name_is_uncertain_without_adoption_or_post():
    calls, handler = handler_factory(
        lambda index, body: dict(supported_credential_types=["api-key"], credentials=[entry()]) if index == 4 else body
    )
    result = prepare(handler)
    assert result.state == "uncertain" and result.credential_id is None
    assert result.reason_code == "native_plugin_credential_name_exists" and len(calls) == 4


def test_preflight_capacity_preserves_native_limit_without_post():
    entries = [entry() | {"name": f"other-{index}"} for index in range(100)]
    calls, handler = handler_factory(
        lambda index, body: dict(supported_credential_types=["api-key"], credentials=entries) if index == 4 else body
    )
    with pytest.raises(PluginCredentialRejected, match="native_plugin_credential_capacity"):
        prepare(handler)
    assert len(calls) == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad"),
        ("id", "00000000-0000-0000-0000-000000000000"),
        ("provider", "wrong"),
        ("credential_type", "oauth2"),
        ("visibility", "only_me"),
        ("created_by", "other"),
        ("from_other_member", True),
    ],
)
def test_created_credential_readback_must_be_owned_and_scoped(field, value):
    def change(index, body):
        if index == 6:
            body["credentials"][0][field] = value
        return body

    calls, handler = handler_factory(change)
    result = prepare(handler)
    assert result.state == "uncertain" and result.credential_id is None
    assert sum(call.method == "POST" for call in calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "https://other.internal"),
        ("key_id", "other"),
        ("expected_app_id", OP),
        ("allow_insecure_http", True),
        ("allow_insecure_http", 0),
    ],
)
def test_readback_public_configuration_must_match_strictly(field, value):
    def change(index, body):
        if index == 6:
            body["credentials"][0]["credentials"][field] = value
        return body

    _, handler = handler_factory(change)
    assert prepare(handler).state == "uncertain"


@pytest.mark.parametrize("entries", [[], [entry(), entry()]])
def test_missing_or_ambiguous_readback_never_adopted(entries):
    calls, handler = handler_factory(
        lambda index, body: dict(supported_credential_types=["api-key"], credentials=entries) if index == 6 else body
    )
    assert prepare(handler).state == "uncertain"
    assert sum(call.method == "POST" for call in calls) == 1


@pytest.mark.parametrize("step", [2, 3, 4, 5, 6])
def test_missing_scope_ack_is_rejected_or_uncertain_by_post_boundary(step):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            response.headers.pop("X-Enterprise-Workspace")
        return response

    if step < 5:
        with pytest.raises(PluginCredentialRejected):
            prepare(handler)
    else:
        assert prepare(handler).state == "uncertain"
    assert len(calls) == step


@pytest.mark.parametrize("step", [5, 6])
def test_transport_loss_never_retries_post(step):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            raise httpx.ReadError("private-signing-secret", request=request)
        return response

    result = prepare(handler)
    assert result.state == "uncertain" and "private-signing-secret" not in repr(result)
    assert sum(call.method == "POST" for call in calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "bad"),
        ("app_id", "bad"),
        ("operation_id", "bad"),
        ("app_id", "00000000-0000-0000-0000-000000000000"),
        ("operation_id", OP.upper()),
    ],
)
def test_invalid_identity_rejected_before_http(field, value):
    calls, handler = handler_factory()
    principal = PRINCIPAL.model_copy(update={"workspace_id": value}) if field == "workspace_id" else PRINCIPAL
    client = DifyWorkflowPluginCredentialClient(
        base_url="https://dify.internal/console/api",
        configuration=configuration(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(PluginCredentialRejected):
        asyncio.run(
            client.prepare(
                principal,
                SESSION,
                app_id=value if field == "app_id" else APP,
                operation_id=value if field == "operation_id" else OP,
            )
        )
    assert calls == []


def test_oversized_response_and_overall_timeout_reject_before_post():
    calls, handler = handler_factory()
    with pytest.raises(PluginCredentialRejected):
        prepare(handler, max_response_bytes=1)
    assert len(calls) == 1

    async def slow(request):
        await asyncio.sleep(0.05)
        return handler(request)

    with pytest.raises(PluginCredentialRejected):
        prepare(slow, timeout_seconds=0.001)


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "http://enterprise.internal"},
        {"origin": "https://enterprise.internal/path"},
        {"allow_insecure_http": 1},
        {"key_id": "space key"},
        {"signing_secret": SecretStr("short")},
        {"expected_plugin_unique_identifier": ""},
    ],
)
def test_invalid_server_configuration(change):
    from pydantic import ValidationError

    config = configuration()
    with pytest.raises(ValidationError):
        PluginCredentialConfiguration.model_validate(
            config.model_dump() | {"signing_secret": config.signing_secret} | change
        )


def test_explicit_http_opt_in_and_secret_mask_is_never_compared():
    config = configuration()
    explicit = PluginCredentialConfiguration.model_validate(
        config.model_dump()
        | {"signing_secret": config.signing_secret, "origin": "http://enterprise.internal", "allow_insecure_http": True}
    )
    assert explicit.allow_insecure_http
    for masked in ("", "**", "a totally different partial mask"):

        def change(index, body, masked=masked):
            if index == 6:
                body["credentials"][0]["credentials"]["secret"] = masked
            return body

        _, handler = handler_factory(change)
        assert prepare(handler).state == "credential_created"


def test_capability_actual_contract_proves_scope_in_body_without_ack_header():
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == 1:
            response.headers.pop("X-Enterprise-Workspace")
        return response

    assert prepare(handler).state == "credential_created"


@pytest.mark.parametrize("step,status", [(2, 302), (5, 302), (5, 201), (5, 500), (6, 403)])
def test_non200_and_redirects_never_retry_or_follow(step, status):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            return httpx.Response(
                status,
                json={"result": "success"},
                headers={"X-Enterprise-Workspace": WS, "Location": "https://untrusted.invalid"},
            )
        return response

    if step < 5:
        with pytest.raises(PluginCredentialRejected):
            prepare(handler)
    else:
        assert prepare(handler).state == "uncertain"
    assert len(calls) == step
    assert all(call.url.host == "dify.internal" for call in calls)


def test_overall_deadline_after_post_reports_uncertain_once():
    calls, valid = handler_factory()

    async def handler(request):
        response = valid(request)
        if request.method == "POST":
            await asyncio.sleep(0.1)
        return response

    assert prepare(handler, timeout_seconds=0.02).state == "uncertain"
    assert sum(call.method == "POST" for call in calls) == 1
