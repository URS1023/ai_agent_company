import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from enterprise_platform.adapters.workflow_publish_client import DifyWorkflowPublishClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_publish_execution import PublishRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

APP = "3fd3b2c4-dc39-45e2-b97c-1f4f714f7f51"
WORKFLOW = "51449c3d-7ee6-4244-bae2-993f0327893e"
OPERATION = "d8cd095a-aef5-469c-8d0f-86a2edbff568"
HASH = "a" * 64
PRINCIPAL = Principal(
    workspace_id="8e14f342-7fbc-4c18-8e51-96a3ec41e828",
    actor_id="actor-1",
    workspace_role="owner",
    display_name="Owner",
)
SESSION = NativeSetupSession("access_token=session; csrf_token=csrf; refresh_token=hidden", None, "csrf")


def capabilities(**changes):
    return httpx.Response(
        200,
        json={
            "enabled": True,
            "publish_enabled": True,
            "workspace_id": "8e14f342-7fbc-4c18-8e51-96a3ec41e828",
            **changes,
        },
    )


def published(**changes):
    return httpx.Response(
        200,
        headers={"X-Enterprise-Workspace": "8e14f342-7fbc-4c18-8e51-96a3ec41e828"},
        json={
            "result": "success",
            "created_at": 1788883200,
            "app_id": APP,
            "workflow_id": WORKFLOW,
            "accepted_draft_hash": HASH,
            **changes,
        },
    )


def publish(handler: Callable[[httpx.Request], httpx.Response], *, principal=PRINCIPAL, **changes):
    client = DifyWorkflowPublishClient(
        base_url="https://dify.internal/console/api", transport=httpx.MockTransport(handler)
    )
    return asyncio.run(
        client.publish(
            principal,
            SESSION,
            **{"app_id": APP, "expected_draft_hash": HASH, "operation_id": OPERATION, **changes},
        )
    )


def test_publishes_exact_app_and_graph_hash_after_scoped_preflight_without_latest_lookup() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return capabilities() if request.method == "GET" else published()

    result = publish(handle)
    assert result.state == "published"
    assert result.app_id == APP and result.workflow_id == WORKFLOW
    assert result.accepted_draft_hash == HASH
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/console/api/enterprise/workflow-setup/capabilities"),
        ("POST", f"/console/api/apps/{APP}/workflows/publish"),
    ]
    for request in requests:
        assert request.headers["cookie"] == "access_token=session; csrf_token=csrf"
        assert request.headers["x-csrf-token"] == "csrf"
        assert "hidden" not in str(request.headers)
    assert requests[1].headers["x-enterprise-expected-workspace"] == "8e14f342-7fbc-4c18-8e51-96a3ec41e828"
    assert requests[1].headers["x-enterprise-expected-draft-hash"] == HASH
    body = json.loads(requests[1].content)
    assert len(body["marked_name"]) <= 20
    assert body["marked_comment"] == f"enterprise-provisioning:{OPERATION}"
    assert set(body) == {"marked_name", "marked_comment"}


@pytest.mark.parametrize(
    "response",
    [
        capabilities(enabled=False),
        capabilities(publish_enabled=False),
        capabilities(publish_enabled="true"),
        capabilities(workspace_id="other"),
        httpx.Response(200, json={"enabled": True, "workspace_id": "8e14f342-7fbc-4c18-8e51-96a3ec41e828"}),
        httpx.Response(404),
        httpx.Response(302, headers={"location": "https://elsewhere.test"}),
    ],
)
def test_unpatched_or_wrong_workspace_preflight_never_posts(response: httpx.Response) -> None:
    requests = []
    with pytest.raises(PublishRejected):
        publish(lambda request: (requests.append(request), response)[1])
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize(
    "changes",
    [
        {"app_id": "../other"},
        {"app_id": "00000000-0000-0000-0000-000000000000"},
        {"operation_id": "invalid"},
        {"expected_draft_hash": "a" * 63},
        {"expected_draft_hash": "A" * 64},
        {"expected_draft_hash": "\r\ninvalid"},
    ],
)
def test_invalid_command_fails_before_http(changes) -> None:
    requests = []
    with pytest.raises(PublishRejected):
        publish(lambda request: (requests.append(request), capabilities())[1], **changes)
    assert requests == []


@pytest.mark.parametrize(
    "response",
    [
        published(app_id=WORKFLOW),
        published(workflow_id="draft"),
        published(workflow_id="00000000-0000-0000-0000-000000000000"),
        published(accepted_draft_hash="b" * 64),
        published(result="pending"),
        published(created_at=True),
        httpx.Response(200, json={"result": "success", "created_at": 1788883200}),
        httpx.Response(409, json={"code": "workflow_hash_not_equal"}),
        httpx.Response(500),
        httpx.Response(302, headers={"location": "https://elsewhere.test"}),
    ],
)
def test_unconfirmed_publish_never_becomes_ready_or_retries(response: httpx.Response) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return capabilities() if request.method == "GET" else response

    result = publish(handle)
    assert result.state == "uncertain" and result.workflow_id is None
    assert len(requests) == 2


def test_lost_publish_response_does_not_retry() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return capabilities()
        raise httpx.ReadTimeout("hidden native detail", request=request)

    result = publish(handle)
    assert result.state == "uncertain"
    assert "hidden" not in repr(result)
    assert len(requests) == 2


@pytest.mark.parametrize("acknowledgement", [None, "other"])
def test_publish_requires_explicit_response_workspace_ack(acknowledgement) -> None:
    response = published()
    if acknowledgement is None:
        del response.headers["X-Enterprise-Workspace"]
    else:
        response.headers["X-Enterprise-Workspace"] = acknowledgement
    result = publish(lambda request: capabilities() if request.method == "GET" else response)
    assert result.state == "uncertain" and result.workflow_id is None


def test_oversized_response_is_uncertain() -> None:
    result = publish(
        lambda request: (
            capabilities()
            if request.method == "GET"
            else httpx.Response(
                200,
                content=b"x" * (256 * 1024 + 1),
                headers={"X-Enterprise-Workspace": "8e14f342-7fbc-4c18-8e51-96a3ec41e828"},
            )
        )
    )
    assert result.state == "uncertain"


@pytest.mark.parametrize(
    "url", ["https://user:secret@host/console/api", "https://host/v1", "https://host/../console/api"]
)
def test_native_publish_endpoint_is_fixed_configuration(url: str) -> None:
    with pytest.raises(ValueError):
        DifyWorkflowPublishClient(base_url=url)


@pytest.mark.parametrize(
    "workspace", ["ws-1", "00000000-0000-0000-0000-000000000000", "8E14F342-7FBC-4C18-8E51-96A3EC41E828"]
)
def test_noncanonical_workspace_is_rejected_before_transport(workspace: str) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return capabilities(workspace_id=workspace) if request.method == "GET" else published()

    with pytest.raises(PublishRejected):
        publish(handle, principal=PRINCIPAL.model_copy(update={"workspace_id": workspace}))
    assert requests == []
