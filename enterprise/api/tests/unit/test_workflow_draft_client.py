import asyncio
import json

import httpx
import pytest

from enterprise_platform.adapters.workflow_draft_client import DifyWorkflowDraftClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_draft_execution import DraftCredentialRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "f19ff571-2297-43fe-8c30-e7e103da664e"
APP = "a595ef4b-2a37-4531-93e6-e85a8691338f"
DRAFT = "d9e7c318-1e4b-4678-a5b4-07d2b1b4a8d1"
CRED = "7b593aa9-9c30-4f3d-8127-3a441c64c6d7"
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession("access_token=native; csrf_token=csrf; refresh_token=private", None, "csrf")


def response():
    return dict(
        result="success", app_id=APP, draft_id=DRAFT, accepted_draft_hash="a" * 64, hash="b" * 64, credential_id=CRED
    )


def run(handler, **overrides):
    client = DifyWorkflowDraftClient(
        base_url="https://dify.internal/console/api", transport=httpx.MockTransport(handler)
    )
    return asyncio.run(
        client.bind_credential(
            PRINCIPAL,
            SESSION,
            **(dict(app_id=APP, draft_id=DRAFT, expected_draft_hash="a" * 64, credential_id=CRED) | overrides),
        )
    )


def handler_factory(change=None):
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "GET":
            body = dict(enabled=True, draft_credential_bind_enabled=True, workspace_id=WS)
            return httpx.Response(200, json=change(1, body) if change else body)
        body = response()
        return httpx.Response(200, json=change(2, body) if change else body, headers={"X-Enterprise-Workspace": WS})

    return calls, handle


def test_exact_delta_post_without_snapshot_rewrite():
    calls, handler = handler_factory()
    result = run(handler)
    assert result.state == "draft_bound" and result.draft_hash == "b" * 64
    assert (result.app_id, result.draft_id, result.credential_id, result.accepted_draft_hash) == (
        APP,
        DRAFT,
        CRED,
        "a" * 64,
    )
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/console/api/enterprise/workflow-setup/capabilities"),
        ("POST", f"/console/api/apps/{APP}/workflows/draft"),
    ]
    assert json.loads(calls[1].content) == dict(draft_id=DRAFT, hash="a" * 64, credential_id=CRED)
    assert calls[1].headers["X-Enterprise-Expected-Workspace"] == WS
    assert calls[1].headers["X-Enterprise-Draft-Operation"] == "bind-assessment-credential"
    assert all("refresh_token" not in r.headers["Cookie"] for r in calls)


@pytest.mark.parametrize(
    "body",
    [{}, dict(enabled=True, workspace_id=WS), dict(enabled=True, draft_credential_bind_enabled=False, workspace_id=WS)],
)
def test_capability_failure_never_posts(body):
    calls, handler = handler_factory(lambda step, value: body if step == 1 else value)
    with pytest.raises(DraftCredentialRejected):
        run(handler)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_id", DRAFT),
        ("draft_id", APP),
        ("credential_id", APP),
        ("accepted_draft_hash", "c" * 64),
        ("hash", "bad"),
        ("hash", "A" * 64),
        ("result", "failed"),
    ],
)
def test_unconfirmed_response_is_uncertain_without_retry(field, value):
    calls, handler = handler_factory(lambda step, body: body | {field: value} if step == 2 else body)
    result = run(handler)
    assert result.state == "uncertain" and result.draft_hash is None
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_id", "bad"),
        ("draft_id", DRAFT.upper()),
        ("credential_id", "00000000-0000-0000-0000-000000000000"),
        ("expected_draft_hash", "a" * 63),
        ("expected_draft_hash", "A" * 64),
        ("app_id", None),
    ],
)
def test_invalid_delta_rejected_before_io(field, value):
    calls, handler = handler_factory()
    with pytest.raises(DraftCredentialRejected):
        run(handler, **{field: value})
    assert calls == []


@pytest.mark.parametrize("workspace", ["bad", "00000000-0000-0000-0000-000000000000", WS.upper()])
def test_invalid_principal_workspace_never_sends_request(workspace):
    calls, handler = handler_factory()
    client = DifyWorkflowDraftClient(
        base_url="https://dify.internal/console/api", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DraftCredentialRejected):
        asyncio.run(
            client.bind_credential(
                PRINCIPAL.model_copy(update={"workspace_id": workspace}),
                SESSION,
                app_id=APP,
                draft_id=DRAFT,
                credential_id=CRED,
                expected_draft_hash="a" * 64,
            )
        )
    assert calls == []


@pytest.mark.parametrize("status,ack", [(200, None), (200, APP), (201, WS), (403, WS), (409, WS), (500, WS), (302, WS)])
def test_post_status_scope_and_redirect_unconfirmed(status, ack):
    calls, valid = handler_factory()

    def handler(request):
        result = valid(request)
        if request.method == "POST":
            headers = {"Location": "https://other.invalid"}
            if ack is not None:
                headers["X-Enterprise-Workspace"] = ack
            return httpx.Response(status, json=response(), headers=headers)
        return result

    assert run(handler).state == "uncertain"
    assert len(calls) == 2


@pytest.mark.parametrize("step", [1, 2])
def test_transport_failure_respects_side_effect_boundary_and_hides_error(step):
    calls, valid = handler_factory()

    def handler(request):
        result = valid(request)
        if len(calls) == step:
            raise httpx.ReadError("private-session-secret", request=request)
        return result

    if step == 1:
        with pytest.raises(DraftCredentialRejected) as caught:
            run(handler)
        assert "private-session-secret" not in str(caught.value)
    else:
        result = run(handler)
        assert result.state == "uncertain" and "private-session-secret" not in repr(result)
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_overall_timeout_has_no_retry(step):
    calls, valid = handler_factory()

    async def handler(request):
        result = valid(request)
        if len(calls) == step:
            await asyncio.sleep(0.1)
        return result

    client = DifyWorkflowDraftClient(
        base_url="https://dify.internal/console/api", transport=httpx.MockTransport(handler), timeout_seconds=0.02
    )
    action = client.bind_credential(
        PRINCIPAL, SESSION, app_id=APP, draft_id=DRAFT, credential_id=CRED, expected_draft_hash="a" * 64
    )
    if step == 1:
        with pytest.raises(DraftCredentialRejected):
            asyncio.run(action)
    else:
        assert asyncio.run(action).state == "uncertain"
    assert len(calls) == step


@pytest.mark.parametrize("step", [1, 2])
def test_response_byte_bound(step):
    calls, valid = handler_factory()

    def handler(request):
        result = valid(request)
        if len(calls) == step:
            return httpx.Response(200, content=b"x" * 2048, headers={"X-Enterprise-Workspace": WS})
        return result

    client = DifyWorkflowDraftClient(
        base_url="https://dify.internal/console/api", transport=httpx.MockTransport(handler), max_response_bytes=1024
    )
    action = client.bind_credential(
        PRINCIPAL, SESSION, app_id=APP, draft_id=DRAFT, credential_id=CRED, expected_draft_hash="a" * 64
    )
    if step == 1:
        with pytest.raises(DraftCredentialRejected):
            asyncio.run(action)
    else:
        assert asyncio.run(action).state == "uncertain"
    assert len(calls) == step


@pytest.mark.parametrize(
    "value",
    [
        "https://dify.internal/v1",
        "https://user:secret@dify.internal/console/api",
        "https://dify.internal/console/api?query=x",
        "https://dify.internal/console/api#fragment",
        "https://dify.internal/a/../console/api",
    ],
)
def test_fixed_console_configuration(value):
    with pytest.raises(ValueError):
        DifyWorkflowDraftClient(base_url=value)


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 0},
        {"max_response_bytes": True},
        {"max_response_bytes": 0},
        {"max_response_bytes": 2 * 1024 * 1024 + 1},
    ],
)
def test_resource_bounds_configuration(options):
    with pytest.raises(ValueError):
        DifyWorkflowDraftClient(base_url="https://dify.internal/console/api", **options)


def test_switched_capability_workspace_is_not_accepted():
    calls, handler = handler_factory(lambda step, body: body | {"workspace_id": APP} if step == 1 else body)
    with pytest.raises(DraftCredentialRejected):
        run(handler)
    assert len(calls) == 1
