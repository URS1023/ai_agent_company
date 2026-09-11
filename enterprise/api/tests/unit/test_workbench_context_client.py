import asyncio
import json
from importlib import import_module

import httpx
import pytest
from test_workbench_messages import intent

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workbench_messages import create_message_intent
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

PRINCIPAL = Principal(workspace_id="w", actor_id="actor", workspace_role="normal", display_name="User")
SESSION = NativeSetupSession("access_token=native; csrf_token=csrf; refresh_token=private", None, "csrf")


@pytest.fixture
def module():
    return import_module("enterprise_platform.adapters.workbench_context_client")


def response_body():
    return {
        "workspace_id": "w",
        "actor_id": "actor",
        "installed_app_id": str(intent().scope.installed_app_id),
        "context_verified": True,
        "attachments_verified": False,
        "branch_verified": False,
    }


def check(module, handler, *, requested=None, session=SESSION, **options):
    client = module.DifyWorkbenchContextClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(client.check_context(PRINCIPAL, session, requested or intent()))


def test_only_context_and_current_session_cross_native_boundary(module):
    calls = []
    payload = {
        "query": "private prompt",
        "inputs": {"value": "private input"},
        "files": [{"upload_file_id": "private-file"}],
        "conversation_id": "00000000-0000-0000-0000-000000000003",
        "parent_message_id": "",
    }
    requested = create_message_intent(intent().scope, intent().client_message_id, payload)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_body())

    assert check(module, handler, requested=requested) is None
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path.endswith(
        f"/installed-apps/{requested.scope.installed_app_id}/enterprise/workbench/context-check"
    )
    assert json.loads(request.content) == {"conversation_id": payload["conversation_id"], "parent_message_id": ""}
    assert request.headers["X-Enterprise-Expected-Workspace"] == "w"
    assert request.headers["X-Enterprise-Expected-Actor"] == "actor"
    assert "refresh_token" not in request.headers.get("cookie", "")
    assert request.headers["X-CSRF-Token"] == "csrf"


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "other"},
        {"actor_id": "other"},
        {"installed_app_id": "other"},
        {"context_verified": False},
        {"context_verified": 1},
        {"attachments_verified": True},
        {"branch_verified": True},
    ],
)
def test_wrong_identity_or_overbroad_acknowledgement_is_rejected(module, change):
    with pytest.raises(module.ContextCheckRejected):
        check(module, lambda _: httpx.Response(200, json={**response_body(), **change}))


@pytest.mark.parametrize("status", [301, 401, 403, 404, 409, 500])
def test_http_failures_never_retry_or_follow_redirect(module, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://elsewhere.invalid"}, text="private")

    with pytest.raises(module.ContextCheckRejected):
        check(module, handler)
    assert len(calls) == 1


def test_wrong_principal_scope_never_opens_connection(module):
    calls = []
    requested = intent().model_copy(update={"scope": intent().scope.model_copy(update={"actor_id": "other"})})
    with pytest.raises(module.ContextCheckRejected):
        check(module, lambda request: calls.append(request), requested=requested)
    assert calls == []


def test_missing_session_never_opens_connection(module):
    calls = []
    with pytest.raises(module.ContextCheckRejected):
        check(module, lambda request: calls.append(request), session=NativeSetupSession(None, None, None))
    assert calls == []


@pytest.mark.parametrize("body", [b"not json", b"x" * 1025])
def test_invalid_or_oversize_response_is_opaque(module, body):
    with pytest.raises(module.ContextCheckRejected) as error:
        check(module, lambda _: httpx.Response(200, content=body), max_response_bytes=1024)
    assert str(error.value) == "native_chat_context_unavailable"


def test_total_deadline_is_enforced(module):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=response_body())

    with pytest.raises(module.ContextCheckRejected):
        check(module, handler, timeout_seconds=0.01)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:secret@native/console/api",
        "https://native/console/api?token=x",
        "file:///console/api",
        "https://native/other",
        "https://native/../console/api",
    ],
)
def test_endpoint_configuration_rejects_credentials_and_ambiguous_paths(module, base_url):
    with pytest.raises(ValueError):
        module.DifyWorkbenchContextClient(base_url=base_url)


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"max_response_bytes": 0},
        {"max_response_bytes": True},
    ],
)
def test_configuration_requires_finite_bounded_limits(module, options):
    with pytest.raises(ValueError):
        module.DifyWorkbenchContextClient(base_url="https://native/console/api", **options)


def test_transport_failure_is_opaque_and_not_retried(module):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadError("private transport details")

    with pytest.raises(module.ContextCheckRejected, match="^native_chat_context_unavailable$"):
        check(module, handler)
    assert len(calls) == 1


@pytest.mark.parametrize("value", [False, "bad-id"])
def test_invalid_context_id_is_rejected_before_network(module, value):
    calls = []
    requested = create_message_intent(intent().scope, intent().client_message_id, {"conversation_id": value})
    with pytest.raises(module.ContextCheckRejected):
        check(module, lambda request: calls.append(request), requested=requested)
    assert calls == []


def test_branch_access_checks_native_app_without_a_synthetic_message(module):
    calls = []
    client = module.DifyWorkbenchContextClient(
        base_url="https://native/console/api",
        transport=httpx.MockTransport(lambda req: (calls.append(req), httpx.Response(200, json=response_body()))[1]),
    )
    asyncio.run(client.check_branch_access(PRINCIPAL, SESSION, intent().scope))
    assert len(calls) == 1 and json.loads(calls[0].content) == {}
    assert "branch" not in calls[0].content.decode()
    assert "refresh_token" not in calls[0].headers.get("cookie", "")


def test_branch_access_foreign_scope_stops_before_network(module):
    calls = []
    client = module.DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(lambda req: calls.append(req))
    )
    with pytest.raises(module.ContextCheckRejected):
        asyncio.run(
            client.check_branch_access(PRINCIPAL, SESSION, intent().scope.model_copy(update={"actor_id": "other"}))
        )
    assert calls == []
