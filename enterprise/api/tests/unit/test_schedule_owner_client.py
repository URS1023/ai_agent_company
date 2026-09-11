import asyncio

import httpx
import pytest

from enterprise_platform.adapters.schedule_owner_client import DifyScheduleOwnerClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import DependencyUnavailable
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
WF = "33333333-3333-4333-8333-333333333333"
ACTOR = "44444444-4444-4444-8444-444444444444"
BODY = {"workspace_id": WS, "app_id": APP, "workflow_id": WF, "graph_hash": "a" * 64, "native_owner_present": False}
HEADERS = {"X-Enterprise-Workspace": WS, "Cache-Control": "private, no-store"}
SESSION = NativeSetupSession("access_token=secret; csrf_token=csrf; refresh_token=refresh-secret", None, "csrf")


def client(handler, **kwargs):
    return DifyScheduleOwnerClient(
        base_url="https://native.example.test/console/api", transport=httpx.MockTransport(handler), **kwargs
    )


def read(adapter, **kwargs):
    return asyncio.run(
        adapter.read(
            Principal(workspace_id=WS, actor_id=ACTOR, workspace_role="editor", display_name="Worker"),
            SESSION,
            app_id=APP,
            workflow_id=WF,
            expected_hash="a" * 64,
            **kwargs,
        )
    )


@pytest.mark.parametrize("present", [False, True])
def test_exact_native_owner_receipt_and_only_filtered_session_headers(present) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=BODY | {"native_owner_present": present}, headers=HEADERS)

    assert read(client(handle)) is present
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET" and request.url.path == f"/console/api/apps/{APP}/enterprise/schedule-owner"
    assert request.headers["cookie"] == "access_token=secret; csrf_token=csrf"
    assert request.headers["X-Enterprise-Expected-Workspace"] == WS
    assert request.headers["X-Enterprise-Expected-Workflow"] == WF
    assert request.headers["X-Enterprise-Expected-Graph-Hash"] == "a" * 64
    assert request.headers["X-Enterprise-Schedule-Operation"] == "inspect-native-owner"
    assert request.content == b""


@pytest.mark.parametrize(
    "patch",
    [
        {"workspace_id": APP},
        {"app_id": WS},
        {"workflow_id": APP},
        {"graph_hash": "b" * 64},
        {"native_owner_present": 0},
        {"native_owner_present": "false"},
        {"extra": "secret"},
    ],
)
def test_mismatched_or_malformed_receipt_is_not_interpreted_as_no_owner(patch) -> None:
    with pytest.raises(DependencyUnavailable):
        read(client(lambda request: httpx.Response(200, json=BODY | patch, headers=HEADERS)))


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 409, 500])
def test_http_failures_never_follow_redirects_or_return_false(status) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example.test/"})

    with pytest.raises(DependencyUnavailable):
        read(client(handle))
    assert len(requests) == 1


def test_response_size_is_bounded() -> None:
    with pytest.raises(DependencyUnavailable):
        read(client(lambda request: httpx.Response(200, content=b"x" * 1025, headers=HEADERS), max_response_bytes=1024))


def test_timeout_is_an_error_not_absent_owner() -> None:
    def handle(request):
        raise httpx.ReadTimeout("private-endpoint-details")

    with pytest.raises(DependencyUnavailable) as error:
        read(client(handle))
    assert str(error.value) == "native_schedule_owner_unavailable"


@pytest.mark.parametrize("headers", [{}, {"X-Enterprise-Workspace": APP}, HEADERS | {"Cache-Control": "public"}])
def test_missing_scope_or_private_cache_policy_rejects_response(headers) -> None:
    with pytest.raises(DependencyUnavailable):
        read(client(lambda request: httpx.Response(200, json=BODY, headers=headers)))


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@native.example.test/console/api",
        "https://native.example.test/console/api?token=secret",
        "https://native.example.test/console/api#fragment",
        "file:///console/api",
    ],
)
def test_endpoint_rejects_embedded_credentials_and_non_http_targets(url) -> None:
    with pytest.raises(ValueError):
        DifyScheduleOwnerClient(base_url=url)


def test_whole_response_deadline_includes_slow_body_stream() -> None:
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(60)
            yield b"{}"

    with pytest.raises(DependencyUnavailable):
        read(client(lambda request: httpx.Response(200, headers=HEADERS, stream=SlowBody()), timeout_seconds=0.01))


def test_invalid_native_identity_is_rejected_before_network() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=BODY, headers=HEADERS)

    adapter = client(handle)
    with pytest.raises(DependencyUnavailable):
        asyncio.run(
            adapter.read(
                Principal(
                    workspace_id="not-a-native-uuid", actor_id=ACTOR, workspace_role="editor", display_name="Worker"
                ),
                SESSION,
                app_id=APP,
                workflow_id=WF,
                expected_hash="a" * 64,
            )
        )
    assert requests == []
