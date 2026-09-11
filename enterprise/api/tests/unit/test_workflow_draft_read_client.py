import asyncio

import httpx
import pytest

from enterprise_platform.adapters.workflow_draft_read_client import DifyWorkflowDraftReadClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_draft_read import DraftReadRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "f19ff571-2297-43fe-8c30-e7e103da664e"
APP = "a595ef4b-2a37-4531-93e6-e85a8691338f"
DRAFT = "d9e7c318-1e4b-4678-a5b4-07d2b1b4a8d1"
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession("access_token=native; csrf_token=csrf; refresh_token=private", None, "csrf")


def handler_factory(change=None):
    calls = []

    def handle(request):
        calls.append(request)
        step = len(calls)
        body = (
            dict(enabled=True, draft_read_enabled=True, workspace_id=WS)
            if step == 1
            else dict(app_id=APP, draft_id=DRAFT, hash="a" * 64, version="draft")
        )
        return httpx.Response(
            200,
            json=change(step, body) if change else body,
            headers={} if step == 1 else {"X-Enterprise-Workspace": WS},
        )

    return calls, handle


def read(handler, **options):
    client = DifyWorkflowDraftReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(client.read(PRINCIPAL, SESSION, app_id=APP))


def test_metadata_only_scoped_get():
    calls, handler = handler_factory()
    result = read(handler)
    assert (
        result.workspace_id == WS
        and result.app_id == APP
        and result.draft_id == DRAFT
        and result.draft_hash == "a" * 64
    )
    assert len(calls) == 2 and all(call.method == "GET" for call in calls)
    assert calls[1].url.path == f"/console/api/apps/{APP}/workflows/draft"
    assert calls[1].headers["X-Enterprise-Expected-Workspace"] == WS
    assert calls[1].headers["X-Enterprise-Draft-Operation"] == "read-assessment-draft"
    assert all("refresh_token" not in call.headers["Cookie"] for call in calls)


@pytest.mark.parametrize(
    "step,change",
    [
        (1, {"draft_read_enabled": False}),
        (1, {"workspace_id": APP}),
        (2, {"app_id": WS}),
        (2, {"draft_id": "bad"}),
        (2, {"hash": "A" * 64}),
        (2, {"version": "published"}),
    ],
)
def test_protocol_rejection(step, change):
    _, handler = handler_factory(lambda index, body: body | change if index == step else body)
    with pytest.raises(DraftReadRejected):
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

    with pytest.raises(DraftReadRejected):
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

    with pytest.raises(DraftReadRejected) as caught:
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

    with pytest.raises(DraftReadRejected):
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

    with pytest.raises(DraftReadRejected):
        read(handler, max_response_bytes=1024)
    assert len(calls) == step


@pytest.mark.parametrize("app_id", ["bad", APP.upper(), "00000000-0000-0000-0000-000000000000", None])
def test_invalid_app_before_http(app_id):
    calls, handler = handler_factory()
    client = DifyWorkflowDraftReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DraftReadRejected):
        asyncio.run(client.read(PRINCIPAL, SESSION, app_id=app_id))
    assert calls == []


@pytest.mark.parametrize("workspace", ["bad", WS.upper(), "00000000-0000-0000-0000-000000000000"])
def test_invalid_workspace_before_http(workspace):
    calls, handler = handler_factory()
    client = DifyWorkflowDraftReadClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DraftReadRejected):
        asyncio.run(client.read(PRINCIPAL.model_copy(update={"workspace_id": workspace}), SESSION, app_id=APP))
    assert calls == []


@pytest.mark.parametrize("field", ["graph", "features", "environment_variables", "conversation_variables"])
def test_snapshot_response_is_not_metadata_contract(field):
    _, handler = handler_factory(lambda step, body: body | {field: {"secret": "private-value"}} if step == 2 else body)
    with pytest.raises(DraftReadRejected) as caught:
        read(handler)
    assert "private-value" not in str(caught.value)


def test_returned_metadata_frozen_and_only_public_fields():
    from pydantic import ValidationError

    _, handler = handler_factory()
    result = read(handler)
    assert set(result.model_dump()) == {"workspace_id", "app_id", "draft_id", "draft_hash"}
    with pytest.raises(ValidationError):
        result.draft_id = APP


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
        DifyWorkflowDraftReadClient(base_url="https://native.internal/console/api", **options)


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
        DifyWorkflowDraftReadClient(base_url=url)


def test_missing_capability_field_rejects_without_getdraft():
    calls, handler = handler_factory(lambda step, body: {"enabled": True, "workspace_id": WS} if step == 1 else body)
    with pytest.raises(DraftReadRejected):
        read(handler)
    assert len(calls) == 1
