import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from enterprise_platform.adapters.workflow_setup_client import DifyWorkflowSetupClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession, SetupImportRejected

APP_ID = "3fd3b2c4-dc39-45e2-b97c-1f4f714f7f51"
IMPORT_ID = "51449c3d-7ee6-4244-bae2-993f0327893e"
PRINCIPAL = Principal(workspace_id="ws-1", actor_id="actor-1", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession(
    cookie_header="access_token=session-one; csrf_token=csrf-one; refresh_token=hidden-refresh",
    authorization=None,
    csrf_token="csrf-one",
)


def run_import(handler: Callable[[httpx.Request], httpx.Response], **kwargs):
    client = DifyWorkflowSetupClient(
        base_url="https://dify.internal/console/api", transport=httpx.MockTransport(handler), **kwargs
    )
    return asyncio.run(
        client.import_default(PRINCIPAL, SESSION, setup_id="setup-1", scenario="alert", name="Pump / alert")
    )


def capabilities():
    return httpx.Response(200, json={"enabled": True, "workspace_id": "ws-1"})


def imported(status="completed", code=200, *, app_id=APP_ID, acknowledgement="ws-1"):
    return httpx.Response(
        code,
        json={"id": IMPORT_ID, "app_id": app_id, "status": status},
        headers={"X-Enterprise-Workspace": acknowledgement},
    )


def test_imports_only_bundled_dsl_after_native_capabilities_and_scope_check() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return capabilities()
        return imported()

    outcome = run_import(handle)

    assert outcome.state == "draft_ready"
    assert outcome.app_id == APP_ID and outcome.import_id == IMPORT_ID
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/console/api/enterprise/workflow-setup/capabilities"),
        ("POST", "/console/api/apps/imports"),
    ]
    for request in requests:
        assert request.headers["cookie"] == "access_token=session-one; csrf_token=csrf-one"
        assert request.headers["x-csrf-token"] == "csrf-one"
        assert "hidden-refresh" not in str(request.headers)
    request = requests[-1]
    assert request.headers["x-enterprise-expected-workspace"] == "ws-1"
    body = json.loads(request.content)
    assert body["mode"] == "yaml-content" and body["name"] == "Pump / alert"
    assert body["description"] == "enterprise-setup:setup-1"
    assert "app_id" not in body and "yaml_url" not in body
    graph = json.loads(body["yaml_content"])["workflow"]["graph"]
    assert [n["data"]["type"] for n in graph["nodes"]] == ["start", "tool", "end"]
    assert graph["nodes"][1]["data"]["tool_parameters"] == {}
    assert "ws-1" not in body["yaml_content"] and "session-one" not in body["yaml_content"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(403),
        httpx.Response(302, headers={"Location": "https://other.test"}),
        httpx.Response(200, json={"enabled": False, "workspace_id": "ws-1"}),
        httpx.Response(200, json={"enabled": True, "workspace_id": "other"}),
        httpx.Response(200, json={"enabled": "true", "workspace_id": "ws-1"}),
    ],
)
def test_missing_disabled_or_wrong_scope_native_capability_never_imports(response) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return response

    with pytest.raises(SetupImportRejected):
        run_import(handle)
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize(
    ("response", "state", "reason"),
    [
        (imported("completed-with-warnings"), "draft_ready", "native_import_warnings"),
        (imported("pending", 202, app_id=None), "confirmation_required", "native_import_confirmation_required"),
        (imported("failed", 400, app_id=None), "failed", "native_import_failed"),
        (imported(acknowledgement="other"), "uncertain", "native_import_scope_unconfirmed"),
        (httpx.Response(200, json={"result": "success"}), "uncertain", "native_import_scope_unconfirmed"),
        (httpx.Response(503), "uncertain", "native_import_scope_unconfirmed"),
        (imported("pending", 200, app_id=None), "uncertain", "native_import_protocol_mismatch"),
        (imported(app_id=None), "uncertain", "native_import_protocol_mismatch"),
    ],
)
def test_distinguishes_drafts_pending_failure_and_ambiguous_outcomes(response, state, reason) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return capabilities() if request.method == "GET" else response

    outcome = run_import(handle)
    assert outcome.state == state and outcome.reason_code == reason
    assert len(requests) == 2
    if state == "uncertain":
        assert outcome.app_id is None and outcome.import_id is None


def test_lost_post_response_is_uncertain_and_never_automatically_retried() -> None:
    posts = 0

    def handle(request):
        nonlocal posts
        if request.method == "GET":
            return capabilities()
        posts += 1
        raise httpx.ReadTimeout("private-driver-detail", request=request)

    outcome = run_import(handle)
    assert posts == 1 and outcome.state == "uncertain"
    assert outcome.reason_code == "native_import_transport_uncertain"
    assert "private-driver-detail" not in repr(outcome)


def test_native_session_repr_does_not_contain_authentication_values() -> None:
    assert "session-one" not in repr(SESSION) and "csrf-one" not in repr(SESSION)
