import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import JsonValue

from enterprise_platform.adapters.dify_identity import DifyIdentityClient
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable, Unauthenticated

BASE_URL = "https://dify.internal/console/api"
PROFILE = "/console/api/account/profile"
CURRENT = "/console/api/workspaces/current"
MEMBERS = "/console/api/workspaces/current/members"
COOKIE = "access_token=session-one; csrf_token=csrf-one"


def payloads(
    *, actor_id: str = "account-one", role: str = "owner", status: str = "active"
) -> dict[str, dict[str, JsonValue]]:
    return {
        PROFILE: {"id": actor_id, "name": "Operator", "email": "operator@example.test", "is_password_set": True},
        CURRENT: {"id": "workspace-one", "name": "Workspace", "status": "normal", "role": role},
        MEMBERS: {
            "accounts": [
                {
                    "id": "other-account",
                    "name": "Other",
                    "email": "other@example.test",
                    "role": "normal",
                    "status": "closed",
                },
                {"id": actor_id, "name": "Operator", "email": "operator@example.test", "role": role, "status": status},
            ]
        },
    }


def adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_response_bytes: int = 2 * 1024 * 1024,
) -> DifyIdentityClient:
    return DifyIdentityClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
        max_response_bytes=max_response_bytes,
    )


def resolve(
    client: DifyIdentityClient,
    *,
    cookie: str | None = COOKIE,
    authorization: str | None = None,
    csrf: str | None = "csrf-one",
) -> Principal:
    return asyncio.run(client.resolve(cookie_header=cookie, authorization=authorization, csrf_token=csrf))


def test_authenticates_native_session_and_forwards_only_authentication_cookies() -> None:
    requests: list[httpx.Request] = []
    responses = payloads()

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses[request.url.path])

    principal = resolve(adapter(handle), cookie=f"{COOKIE}; refresh_token=private-refresh; tracking=private-tracking")

    assert principal.actor_id == "account-one"
    assert principal.workspace_id == "workspace-one"
    assert principal.workspace_role == "owner"
    assert principal.display_name == "Operator"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", PROFILE),
        ("POST", CURRENT),
        ("GET", MEMBERS),
        ("POST", CURRENT),
    ]
    for request in requests:
        assert request.url.host == "dify.internal"
        assert request.headers["cookie"] == COOKIE
        assert request.headers["x-csrf-token"] == "csrf-one"
        assert "x-workspace-id" not in request.headers
        assert "authorization" not in request.headers
        assert request.content == b""


@pytest.mark.parametrize("role", ["owner", "admin", "editor", "normal", "dataset_operator"])
def test_authenticates_all_known_active_roles_without_granting_business_permissions(role: str) -> None:
    responses = payloads(role=role)
    principal = resolve(adapter(lambda request: httpx.Response(200, json=responses[request.url.path])))
    assert principal.workspace_role == role


@pytest.mark.parametrize(
    "cookie,authorization",
    [
        ("csrf_token=csrf-one", "bearer session-one"),
        ("__Host-access_token=session-one; __Host-csrf_token=csrf-one", None),
    ],
)
def test_preserves_native_bearer_and_host_cookie_modes(cookie: str, authorization: str | None) -> None:
    responses = payloads()

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == cookie
        if authorization:
            assert request.headers["authorization"] == "Bearer session-one"
        return httpx.Response(200, json=responses[request.url.path])

    assert resolve(adapter(handle), cookie=cookie, authorization=authorization).actor_id == "account-one"


@pytest.mark.parametrize(
    "cookie,authorization,csrf",
    [
        (None, None, None),
        ("csrf_token=csrf-one", None, "csrf-one"),
        ("access_token=session-one", None, "csrf-one"),
        (COOKIE, None, None),
        (COOKIE, None, "csrf-two"),
        ("csrf_token=csrf-one", "Basic private-secret", "csrf-one"),
        (COOKIE, "Bearer injected\r\nX-WORKSPACE-ID: other", "csrf-one"),
        (f"{COOKIE}; access_token=session-two", None, "csrf-one"),
        (f"{COOKIE}\r\nX-WORKSPACE-ID: other", None, "csrf-one"),
    ],
)
def test_rejects_missing_or_ambiguous_credentials_before_network(
    cookie: str | None, authorization: str | None, csrf: str | None
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid credentials must not reach Dify")

    with pytest.raises(Unauthenticated):
        resolve(adapter(handle), cookie=cookie, authorization=authorization, csrf=csrf)


@pytest.mark.parametrize("status", ["pending", "uninitialized", "banned", "closed", "unknown"])
def test_requires_active_status_from_own_member_record_not_profile(status: str) -> None:
    responses = payloads(status=status)
    with pytest.raises(Unauthenticated):
        resolve(adapter(lambda request: httpx.Response(200, json=responses[request.url.path])))


def test_rejects_unknown_roles() -> None:
    responses = payloads(role="custom-unrecognized")
    with pytest.raises(AccessDenied):
        resolve(adapter(lambda request: httpx.Response(200, json=responses[request.url.path])))


def test_rejects_archived_workspace() -> None:
    responses = payloads()
    responses[CURRENT]["status"] = "archive"
    with pytest.raises(AccessDenied):
        resolve(adapter(lambda request: httpx.Response(200, json=responses[request.url.path])))


@pytest.mark.parametrize("member_rows", [[], [{"id": "someone-else", "role": "owner", "status": "active"}]])
def test_rejects_profile_without_current_workspace_membership(member_rows: list[dict[str, str]]) -> None:
    responses = payloads()
    responses[MEMBERS] = {"accounts": member_rows}
    with pytest.raises(AccessDenied):
        resolve(adapter(lambda request: httpx.Response(200, json=responses[request.url.path])))


def test_rejects_member_role_different_from_current_workspace_role() -> None:
    responses = payloads()
    responses[MEMBERS] = {"accounts": [{"id": "account-one", "role": "editor", "status": "active"}]}
    with pytest.raises(AccessDenied):
        resolve(adapter(lambda request: httpx.Response(200, json=responses[request.url.path])))


@pytest.mark.parametrize("field,value", [("id", "workspace-two"), ("role", "normal"), ("status", "archive")])
def test_rejects_current_workspace_changes_during_identity_lookup(field: str, value: str) -> None:
    responses = payloads()
    current_reads = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal current_reads
        if request.url.path == CURRENT:
            current_reads += 1
            if current_reads == 2:
                return httpx.Response(200, json={**responses[CURRENT], field: value})
        return httpx.Response(200, json=responses[request.url.path])

    with pytest.raises(AccessDenied):
        resolve(adapter(handle))


@pytest.mark.parametrize(
    "code,error",
    [
        (401, Unauthenticated),
        (403, AccessDenied),
        (302, DependencyUnavailable),
        (404, DependencyUnavailable),
        (429, DependencyUnavailable),
        (503, DependencyUnavailable),
    ],
)
def test_upstream_denials_license_errors_and_redirects_fail_closed_without_disclosing_responses(
    code: int,
    error: type[Exception],
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            code, json={"message": "private-secret"}, headers={"location": "https://untrusted.example/"}
        )

    with pytest.raises(error) as raised:
        resolve(adapter(handle))
    assert "private-secret" not in str(raised.value)
    assert calls == 1


@pytest.mark.parametrize("content", [b"not-json", b"[]", b"{}", b'{"id":12,"name":"Operator"}'])
def test_malformed_profile_is_not_authenticated(content: bytes) -> None:
    with pytest.raises(DependencyUnavailable):
        resolve(adapter(lambda request: httpx.Response(200, content=content)))


def test_limits_response_size_before_parsing() -> None:
    with pytest.raises(DependencyUnavailable):
        resolve(adapter(lambda request: httpx.Response(200, content=b"x" * 2048), max_response_bytes=1024))


def test_transport_errors_are_sanitized_without_retry() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private-secret", request=request)

    with pytest.raises(DependencyUnavailable) as raised:
        resolve(adapter(handle))
    assert "private-secret" not in str(raised.value)


def test_bounds_the_whole_identity_lookup_duration() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=payloads()[request.url.path])

    client = DifyIdentityClient(base_url=BASE_URL, transport=httpx.MockTransport(handle), timeout_seconds=0.01)
    with pytest.raises(DependencyUnavailable):
        resolve(client)


def test_deadline_applies_to_the_sum_of_all_native_identity_requests() -> None:
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return httpx.Response(200, json=payloads()[request.url.path])

    client = DifyIdentityClient(base_url=BASE_URL, transport=httpx.MockTransport(handle), timeout_seconds=0.05)
    with pytest.raises(DependencyUnavailable):
        resolve(client)
    assert calls <= 2


def test_concurrent_lookups_keep_credentials_and_principals_isolated() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        actor = "account-one" if request.headers["authorization"] == "Bearer session-one" else "account-two"
        await asyncio.sleep(0)
        return httpx.Response(200, json=payloads(actor_id=actor)[request.url.path])

    async def authenticate_both() -> tuple[Principal, Principal]:
        client = DifyIdentityClient(base_url=BASE_URL, transport=httpx.MockTransport(handle))
        first, second = await asyncio.gather(
            client.resolve(
                cookie_header="csrf_token=csrf-one", authorization="Bearer session-one", csrf_token="csrf-one"
            ),
            client.resolve(
                cookie_header="csrf_token=csrf-two", authorization="Bearer session-two", csrf_token="csrf-two"
            ),
        )
        return first, second

    first, second = asyncio.run(authenticate_both())
    assert first.actor_id == "account-one"
    assert second.actor_id == "account-two"


def test_does_not_reuse_response_cookies_between_requests_or_identities() -> None:
    seen_cookies: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers["cookie"])
        actor = "account-two" if "authorization" in request.headers else "account-one"
        return httpx.Response(
            200,
            json=payloads(actor_id=actor)[request.url.path],
            headers={"set-cookie": "access_token=untrusted-response-cookie; Path=/"},
        )

    client = adapter(handle)
    assert resolve(client).actor_id == "account-one"
    assert resolve(client, cookie="csrf_token=csrf-one", authorization="Bearer session-two").actor_id == "account-two"
    assert seen_cookies == [COOKIE] * 4 + ["csrf_token=csrf-one"] * 4


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///console/api",
        "https://user:private-secret@dify.internal/console/api",
        "https://dify.internal/v1",
        "https://dify.internal/console/api?url=other",
        "https://dify.internal/console/api#fragment",
        "https://dify.internal:invalid/console/api",
    ],
)
def test_rejects_non_console_or_credential_bearing_upstream_configuration(base_url: str) -> None:
    with pytest.raises(ValueError) as raised:
        DifyIdentityClient(base_url=base_url)
    assert "private-secret" not in str(raised.value)
