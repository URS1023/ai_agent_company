import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from enterprise_platform.adapters.workflow_token_client import DifyWorkflowTokenClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession
from enterprise_platform.application.workflow_token_execution import NativeWorkflowTokenOutcome, TokenIssueRejected

WS = "f19ff571-2297-43fe-8c30-e7e103da664e"
APP = "a595ef4b-2a37-4531-93e6-e85a8691338f"
OP = "d9e7c318-1e4b-4678-a5b4-07d2b1b4a8d1"
TOKEN_ID = "7b593aa9-9c30-4f3d-8127-3a441c64c6d7"
TOKEN = "app-" + "aB3" * 8
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession("access_token=native; csrf_token=csrf; refresh_token=private", None, "csrf")


def handler_factory(change=None):
    calls = []

    def handler(request):
        calls.append(request)
        step = len(calls)
        body = (
            dict(enabled=True, service_api_token_issue_enabled=True, workspace_id=WS)
            if step == 1
            else dict(id=TOKEN_ID, type="app", token=TOKEN, app_id=APP, last_used_at=None, created_at=123)
        )
        return httpx.Response(
            200 if step == 1 else 201,
            json=change(step, body) if change else body,
            headers={} if step == 1 else {"X-Enterprise-Workspace": WS, "Cache-Control": "private, no-store"},
        )

    return calls, handler


def issue(handler, **options):
    client = DifyWorkflowTokenClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(client.issue(PRINCIPAL, SESSION, app_id=APP, operation_id=OP))


def test_confirmed_bodyless_token_issue_is_scoped_and_secret_safe():
    calls, handler = handler_factory()
    result = issue(handler)
    assert result.state == "issued" and result.token == SecretStr(TOKEN)
    assert (result.workspace_id, result.app_id, result.token_id) == (WS, APP, TOKEN_ID)
    assert [call.method for call in calls] == ["GET", "POST"]
    post = calls[1]
    assert post.url.path == f"/console/api/apps/{APP}/api-keys" and post.content == b""
    assert post.headers["X-Enterprise-Expected-Workspace"] == WS
    assert post.headers["X-Enterprise-Key-Operation"] == "issue-workflow-key"
    assert post.headers["X-Enterprise-Operation-Id"] == OP
    assert all("refresh_token" not in call.headers["Cookie"] for call in calls)
    assert TOKEN not in repr(result) + result.model_dump_json()
    assert "token" not in result.model_dump()


@pytest.mark.parametrize(
    "body",
    [
        {},
        dict(enabled=True, workspace_id=WS),
        dict(enabled=True, service_api_token_issue_enabled=False, workspace_id=WS),
    ],
)
def test_preflight_rejects_before_post(body):
    calls, handler = handler_factory(lambda step, value: body if step == 1 else value)
    with pytest.raises(TokenIssueRejected):
        issue(handler)
    assert len(calls) == 1


def test_uncertain_outcome_never_retains_token_or_identity():
    with pytest.raises(ValidationError):
        NativeWorkflowTokenOutcome(state="uncertain", reason_code="unknown", token=SecretStr(TOKEN), token_id=TOKEN_ID)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad"),
        ("id", "00000000-0000-0000-0000-000000000000"),
        ("app_id", WS),
        ("type", "dataset"),
        ("token", ""),
        ("token", "app-" + "x" * 23),
        ("token", "app-" + "x" * 25),
        ("token", "app-" + "!" * 24),
        ("token", "app-" + "é" * 24),
        ("token", "app-" + " " * 24),
    ],
)
def test_unconfirmed_token_payload_discards_secret_and_ids(field, value):
    calls, handler = handler_factory(lambda step, body: body | {field: value} if step == 2 else body)
    result = issue(handler)
    assert result.state == "uncertain" and result.token is None and result.token_id is None
    assert result.workspace_id is None and result.app_id is None
    assert TOKEN not in repr(result) + result.model_dump_json()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "step,status,ack",
    [
        (1, 302, None),
        (1, 403, None),
        (2, 200, WS),
        (2, 400, WS),
        (2, 403, WS),
        (2, 500, WS),
        (2, 302, WS),
        (2, 201, None),
        (2, 201, APP),
    ],
)
def test_response_status_and_ack_fail_closed_without_retry(step, status, ack):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            headers = {"Location": "https://other.invalid"}
            if ack is not None:
                headers["X-Enterprise-Workspace"] = ack
            return httpx.Response(status, content=response.content, headers=headers)
        return response

    if step == 1:
        with pytest.raises(TokenIssueRejected):
            issue(handler)
    else:
        assert issue(handler).state == "uncertain"
    assert len(calls) == step
    assert all(call.url.host == "native.internal" for call in calls)


@pytest.mark.parametrize("step", [1, 2])
def test_transport_failures_sanitized_without_retry(step):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            raise httpx.ReadError(TOKEN, request=request)
        return response

    if step == 1:
        with pytest.raises(TokenIssueRejected) as caught:
            issue(handler)
        assert TOKEN not in str(caught.value)
    else:
        result = issue(handler)
        assert result.state == "uncertain" and TOKEN not in repr(result)
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_response_bound(step):
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if len(calls) == step:
            return httpx.Response(
                200 if step == 1 else 201, content=b"x" * 2048, headers={"X-Enterprise-Workspace": WS}
            )
        return response

    if step == 1:
        with pytest.raises(TokenIssueRejected):
            issue(handler, max_response_bytes=1024)
    else:
        assert issue(handler, max_response_bytes=1024).state == "uncertain"
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_overall_deadline(step):
    calls, valid = handler_factory()

    async def handler(request):
        response = valid(request)
        if len(calls) == step:
            await asyncio.sleep(0.1)
        return response

    if step == 1:
        with pytest.raises(TokenIssueRejected):
            issue(handler, timeout_seconds=0.02)
    else:
        assert issue(handler, timeout_seconds=0.02).state == "uncertain"
    assert len(calls) == step


def test_cancellation_propagates_after_post_without_retries():
    calls, valid = handler_factory()

    def handler(request):
        response = valid(request)
        if request.method == "POST":
            raise asyncio.CancelledError()
        return response

    with pytest.raises(asyncio.CancelledError):
        issue(handler)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_id", "bad"),
        ("operation_id", OP.upper()),
        ("workspace_id", "00000000-0000-0000-0000-000000000000"),
        ("app_id", None),
    ],
)
def test_invalid_command_before_http(field, value):
    calls, handler = handler_factory()
    client = DifyWorkflowTokenClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    principal = PRINCIPAL.model_copy(update={field: value}) if field == "workspace_id" else PRINCIPAL
    with pytest.raises(TokenIssueRejected):
        asyncio.run(
            client.issue(
                principal,
                SESSION,
                app_id=value if field == "app_id" else APP,
                operation_id=value if field == "operation_id" else OP,
            )
        )
    assert calls == []


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"max_response_bytes": 0},
        {"max_response_bytes": True},
        {"max_response_bytes": 2 * 1024 * 1024 + 1},
    ],
)
def test_resource_configuration(options):
    with pytest.raises(ValueError):
        DifyWorkflowTokenClient(base_url="https://native.internal/console/api", **options)


@pytest.mark.parametrize(
    "url",
    [
        "https://native.internal/v1",
        "https://user:pass@native.internal/console/api",
        "https://native.internal/console/api?a=1",
        "https://native.internal/console/api#f",
        "https://native.internal/a/../console/api",
    ],
)
def test_fixed_console_url(url):
    with pytest.raises(ValueError):
        DifyWorkflowTokenClient(base_url=url)


def test_outcome_strict_frozen_and_issued_requires_all_fields():
    with pytest.raises(ValidationError):
        NativeWorkflowTokenOutcome(state="issued")
    value = NativeWorkflowTokenOutcome(
        state="issued", workspace_id=WS, app_id=APP, token_id=TOKEN_ID, token=SecretStr(TOKEN)
    )
    with pytest.raises(ValidationError):
        value.token_id = APP
    with pytest.raises(ValidationError):
        NativeWorkflowTokenOutcome(state="uncertain", reason_code="unknown", app_id=APP)


def test_switched_capability_workspace_rejected_before_post():
    calls, handler = handler_factory(lambda step, body: body | {"workspace_id": APP} if step == 1 else body)
    with pytest.raises(TokenIssueRejected):
        issue(handler)
    assert len(calls) == 1
