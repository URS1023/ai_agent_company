import asyncio
import json
from unittest.mock import AsyncMock, create_autospec

import httpx
import pytest
from test_dify_identity import BASE_URL, COOKIE, payloads

from enterprise_platform.adapters.dify_identity import DifyIdentityClient
from enterprise_platform.adapters.schedule_identity import DifyScheduleIdentity, FileScheduleSession
from enterprise_platform.application.errors import AccessDenied, Unauthenticated
from enterprise_platform.application.schedule_loop import ScheduleLoop, ScheduleLoopPolicy
from enterprise_platform.application.schedule_polling import SchedulePoller
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession


def test_identity_reloads_and_revalidates_native_session_each_call() -> None:
    requests = []
    responses = payloads()

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=responses[request.url.path])

    sessions = AsyncMock(
        side_effect=[
            NativeSetupSession(COOKIE, None, "csrf-one"),
            NativeSetupSession("access_token=two; csrf_token=csrf-two", None, "csrf-two"),
        ]
    )
    identity = DifyScheduleIdentity(
        DifyIdentityClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)),
        sessions,
        workspace_id="workspace-one",
        actor_id="account-one",
    )

    async def scenario():
        assert (await identity()).workspace_role == "owner"
        assert (await identity()).actor_id == "account-one"

    asyncio.run(scenario())
    assert sessions.await_count == 2 and len(requests) == 8
    assert requests[0].headers["cookie"] == COOKIE
    assert requests[4].headers["cookie"] == "access_token=two; csrf_token=csrf-two"


@pytest.mark.parametrize(
    "role,actor,workspace",
    [
        ("normal", "account-one", "workspace-one"),
        ("owner", "other", "workspace-one"),
        ("owner", "account-one", "other"),
    ],
)
def test_role_or_configured_identity_mismatch_denies_scheduler(role, actor, workspace) -> None:
    responses = payloads(role=role)
    client = DifyIdentityClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=responses[request.url.path])),
    )
    identity = DifyScheduleIdentity(
        client,
        AsyncMock(return_value=NativeSetupSession(COOKIE, None, "csrf-one")),
        workspace_id=workspace,
        actor_id=actor,
    )
    with pytest.raises(AccessDenied):
        asyncio.run(identity())


def test_expired_native_session_has_no_cached_principal_fallback() -> None:
    responses = payloads()
    expired = False

    def handle(request):
        return httpx.Response(401) if expired else httpx.Response(200, json=responses[request.url.path])

    identity = DifyScheduleIdentity(
        DifyIdentityClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)),
        AsyncMock(return_value=NativeSetupSession(COOKIE, None, "csrf-one")),
        workspace_id="workspace-one",
        actor_id="account-one",
    )
    asyncio.run(identity())
    expired = True
    with pytest.raises(Unauthenticated):
        asyncio.run(identity())


def test_file_session_is_bounded_rotatable_and_hidden_from_repr(tmp_path) -> None:
    path = tmp_path / "session.json"
    provider = FileScheduleSession(path)
    path.write_text(json.dumps({"cookie_header": COOKIE, "authorization": None, "csrf_token": "csrf-one"}))
    first = asyncio.run(provider())
    assert first.cookie_header == COOKIE and "session-one" not in repr(first)
    path.write_text(json.dumps({"cookie_header": None, "authorization": "Bearer rotated", "csrf_token": "csrf-two"}))
    assert asyncio.run(provider()).authorization == "Bearer rotated"


@pytest.mark.parametrize(
    "content", ["not-json", '{"unexpected":"secret"}', "x" * 32769], ids=["json", "schema", "size"]
)
def test_invalid_session_file_errors_do_not_include_its_content(tmp_path, content) -> None:
    path = tmp_path / "session.json"
    path.write_text(content)
    with pytest.raises(Unauthenticated) as error:
        asyncio.run(FileScheduleSession(path)())
    assert str(error.value) == "scheduler_session_unavailable"


def test_missing_session_file_is_not_replaced_or_created(tmp_path) -> None:
    path = tmp_path / "missing.json"
    with pytest.raises(Unauthenticated):
        asyncio.run(FileScheduleSession(path)())
    assert not path.exists()


def test_loop_with_native_identity_adapter_stops_polling_when_session_expires() -> None:
    async def scenario():
        responses = payloads()
        expired = False

        def handle(request):
            return httpx.Response(401) if expired else httpx.Response(200, json=responses[request.url.path])

        identity = DifyScheduleIdentity(
            DifyIdentityClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)),
            AsyncMock(return_value=NativeSetupSession(COOKIE, None, "csrf-one")),
            workspace_id="workspace-one",
            actor_id="account-one",
        )
        poller = create_autospec(SchedulePoller, instance=True)
        poller.poll.return_value = ()
        stop = asyncio.Event()
        reports = []

        async def report(value):
            nonlocal expired
            reports.append(value)
            expired = True
            if len(reports) == 2:
                stop.set()

        loop = ScheduleLoop(
            poller, identity, report, workspace_id="workspace-one", actor_id="account-one", policy=ScheduleLoopPolicy()
        )
        loop._wait = AsyncMock()
        await loop.run(stop)
        assert [item.status for item in reports] == ["ok", "error"]
        poller.poll.assert_awaited_once()

    asyncio.run(scenario())
