import asyncio

import httpx
import pytest

from enterprise_platform.adapters.workflow_publication_read_client import DifyWorkflowPublicationReadClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_publication_read import PublicationReadRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "f19ff571-2297-43fe-8c30-e7e103da664e"
APP = "a595ef4b-2a37-4531-93e6-e85a8691338f"
WORKFLOW = "d9e7c318-1e4b-4678-a5b4-07d2b1b4a8d1"
CREDENTIAL = "6ade89f1-81d3-4a0c-a950-1914d1e0e1c7"
PROVIDER = "enterprise/enterprise_device_assessment/enterprise_device"
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession("access_token=native; csrf_token=csrf; refresh_token=private", None, "csrf")


def handler_factory(change=None):
    calls = []

    def handle(request):
        calls.append(request)
        step = len(calls)
        body = (
            dict(enabled=True, publication_read_enabled=True, workspace_id=WS)
            if step == 1
            else dict(
                app_id=APP,
                workflow_id=WORKFLOW,
                hash="a" * 64,
                provider_id=PROVIDER,
                tool_name="evaluate_device",
                credential_id=CREDENTIAL,
                node_id="assessment",
            )
        )
        return httpx.Response(
            200,
            json=change(step, body) if change else body,
            headers={} if step == 1 else {"X-Enterprise-Workspace": WS},
        )

    return calls, handle


def read(handler, **options):
    client = DifyWorkflowPublicationReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(
        client.read(
            PRINCIPAL, SESSION, app_id=APP, workflow_id=WORKFLOW, expected_graph_hash="a" * 64, credential_id=CREDENTIAL
        )
    )


def test_metadata_only_scoped_get():
    calls, handler = handler_factory()
    result = read(handler)
    assert (
        result.workspace_id == WS
        and result.app_id == APP
        and result.workflow_id == WORKFLOW
        and result.graph_hash == "a" * 64
    )
    assert len(calls) == 2 and all(call.method == "GET" for call in calls)
    assert calls[1].url.path == f"/console/api/apps/{APP}/workflows/publish"
    assert calls[1].headers["X-Enterprise-Expected-Workspace"] == WS
    assert calls[1].headers["X-Enterprise-Expected-Workflow"] == WORKFLOW
    assert calls[1].headers["X-Enterprise-Expected-Graph-Hash"] == "a" * 64
    assert result.credential_id == CREDENTIAL and result.provider_id == PROVIDER
    assert result.tool_name == "evaluate_device" and result.node_id == "assessment"
    assert calls[1].headers["X-Enterprise-Publication-Operation"] == "read-assessment-publication"
    assert all("refresh_token" not in call.headers["Cookie"] for call in calls)


@pytest.mark.parametrize(
    "step,change",
    [
        (1, {"publication_read_enabled": False}),
        (1, {"workspace_id": APP}),
        (2, {"app_id": WS}),
        (2, {"workflow_id": "bad"}),
        (2, {"hash": "A" * 64}),
        (2, {"workflow_id": APP}),
        (2, {"credential_id": APP}),
        (2, {"credential_id": "bad"}),
        (2, {"hash": "b" * 64}),
        (2, {"provider_id": "wrong"}),
        (2, {"tool_name": "wrong"}),
        (2, {"node_id": "other"}),
    ],
)
def test_protocol_rejection(step, change):
    _, handler = handler_factory(lambda index, body: body | change if index == step else body)
    with pytest.raises(PublicationReadRejected):
        read(handler)


@pytest.mark.parametrize(
    "step,status,ack", [(1, 302, None), (1, 403, None), (2, 302, WS), (2, 403, WS), (2, 200, None), (2, 200, APP)]
)
def test_status_scope_and_redirect_reject_without_retry(step, status, ack):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            headers = {"Location": "https://other.invalid"}
            if ack:
                headers["X-Enterprise-Workspace"] = ack
            return httpx.Response(status, content=response.content, headers=headers)
        return response

    with pytest.raises(PublicationReadRejected):
        read(handler)
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_transport_failure_sanitized_and_not_retried(step):
    calls, valid = handler_factory()

    def handler(request):
        result = valid(request)
        if len(calls) == step:
            raise httpx.ReadError("private-session", request=request)
        return result

    with pytest.raises(PublicationReadRejected) as caught:
        read(handler)
    assert "private-session" not in str(caught.value) and len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_overall_timeout(step):
    calls, valid = handler_factory()

    async def handler(request):
        result = valid(request)
        if len(calls) == step:
            await asyncio.sleep(0.1)
        return result

    with pytest.raises(PublicationReadRejected):
        read(handler, timeout_seconds=0.02)
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_response_size_bound(step):
    calls, valid = handler_factory()

    def handler(request):
        result = valid(request)
        if len(calls) == step:
            return httpx.Response(200, content=b"x" * 2048, headers={"X-Enterprise-Workspace": WS})
        return result

    with pytest.raises(PublicationReadRejected):
        read(handler, max_response_bytes=1024)
    assert len(calls) == step


@pytest.mark.parametrize("app_id", ["bad", APP.upper(), "00000000-0000-0000-0000-000000000000", None])
def test_invalid_app_before_http(app_id):
    calls, handler = handler_factory()
    client = DifyWorkflowPublicationReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(PublicationReadRejected):
        asyncio.run(
            client.read(
                PRINCIPAL,
                SESSION,
                app_id=app_id,
                workflow_id=WORKFLOW,
                expected_graph_hash="a" * 64,
                credential_id=CREDENTIAL,
            )
        )
    assert calls == []


@pytest.mark.parametrize("workspace", ["bad", WS.upper(), "00000000-0000-0000-0000-000000000000"])
def test_invalid_workspace_before_http(workspace):
    calls, handler = handler_factory()
    client = DifyWorkflowPublicationReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(PublicationReadRejected):
        asyncio.run(
            client.read(
                PRINCIPAL.model_copy(update={"workspace_id": workspace}),
                SESSION,
                app_id=APP,
                workflow_id=WORKFLOW,
                expected_graph_hash="a" * 64,
                credential_id=CREDENTIAL,
            )
        )
    assert calls == []


@pytest.mark.parametrize("field", ["graph", "features", "environment_variables", "conversation_variables"])
def test_snapshot_response_is_not_metadata_contract(field):
    _, handler = handler_factory(lambda step, body: body | {field: {"secret": "private-value"}} if step == 2 else body)
    with pytest.raises(PublicationReadRejected) as caught:
        read(handler)
    assert "private-value" not in str(caught.value)


def test_returned_metadata_frozen_and_only_public_fields():
    from pydantic import ValidationError

    _, handler = handler_factory()
    result = read(handler)
    assert set(result.model_dump()) == {
        "workspace_id",
        "app_id",
        "workflow_id",
        "graph_hash",
        "provider_id",
        "tool_name",
        "credential_id",
        "node_id",
    }
    with pytest.raises(ValidationError):
        result.workflow_id = APP


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"max_response_bytes": 0},
        {"max_response_bytes": True},
        {"max_response_bytes": 2 * 1024 * 1024 + 1},
    ],
)
def test_configuration_bounds(options):
    with pytest.raises(ValueError):
        DifyWorkflowPublicationReadClient(base_url="https://native.internal/console/api", **options)


@pytest.mark.parametrize(
    "url",
    [
        "https://native.internal/v1",
        "https://user:pass@native.internal/console/api",
        "https://native.internal/console/api?a=1",
        "https://native.internal/console/api#x",
        "https://native.internal/x/../console/api",
    ],
)
def test_fixed_console_url(url):
    with pytest.raises(ValueError):
        DifyWorkflowPublicationReadClient(base_url=url)


def test_missing_capability_field_rejects_without_getdraft():
    calls, handler = handler_factory(lambda step, body: {"enabled": True, "workspace_id": WS} if step == 1 else body)
    with pytest.raises(PublicationReadRejected):
        read(handler)
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["workflow_id", "credential_id"])
@pytest.mark.parametrize("value", ["bad", APP.upper(), "00000000-0000-0000-0000-000000000000", None])
def test_invalid_publication_identity_before_http(field, value):
    calls, handler = handler_factory()
    client = DifyWorkflowPublicationReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    command = dict(app_id=APP, workflow_id=WORKFLOW, expected_graph_hash="a" * 64, credential_id=CREDENTIAL)
    command[field] = value
    with pytest.raises(PublicationReadRejected):
        asyncio.run(client.read(PRINCIPAL, SESSION, **command))
    assert calls == []


@pytest.mark.parametrize("value", ["a" * 63, "A" * 64, "g" * 64, None, 123])
def test_invalid_expected_hash_before_http(value):
    calls, handler = handler_factory()
    client = DifyWorkflowPublicationReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(PublicationReadRejected):
        asyncio.run(
            client.read(
                PRINCIPAL,
                SESSION,
                app_id=APP,
                workflow_id=WORKFLOW,
                expected_graph_hash=value,
                credential_id=CREDENTIAL,
            )
        )
    assert calls == []


@pytest.mark.parametrize("step", [1, 2])
def test_cancel_propagates_without_retry(step):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            raise asyncio.CancelledError()
        return response

    with pytest.raises(asyncio.CancelledError):
        read(handler)
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_chunked_response_enforces_cumulative_bound(step):
    calls, valid = handler_factory()

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(3):
                yield b"x" * 400

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            return httpx.Response(200, stream=Chunks(), headers={"X-Enterprise-Workspace": WS})
        return response

    with pytest.raises(PublicationReadRejected):
        read(handler, max_response_bytes=1024)
    assert len(calls) == step


@pytest.mark.parametrize("field", ["enabled", "publication_read_enabled"])
@pytest.mark.parametrize("value", [False, "true", 1])
def test_capability_requires_explicit_boolean_enable(field, value):
    calls, handler = handler_factory(lambda step, body: body | {field: value} if step == 1 else body)
    with pytest.raises(PublicationReadRejected):
        read(handler)
    assert len(calls) == 1
