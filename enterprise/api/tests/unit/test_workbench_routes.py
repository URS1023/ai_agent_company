import json
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_workbench_context_client import PRINCIPAL
from test_workbench_dispatch import setup_dispatch
from test_workbench_messages import intent, receipt
from test_workbench_stream_identity import event

from enterprise_platform.application.errors import AccessDenied, Unauthenticated
from enterprise_platform.application.workbench_messages import acknowledge_message, claim_message
from enterprise_platform.http.app import create_app

URL = f"/enterprise/api/v1/workbench/apps/{UUID(int=1)}/branches/branch/messages"
HEADERS = {
    "origin": "https://portal",
    "authorization": "Bearer session",
    "x-csrf-token": "csrf",
    "cookie": "csrf_token=csrf",
}
BODY = {"client_message_id": str(UUID(int=2)), "payload": {"query": "hello", "inputs": {}}}


def client_for(dispatcher, identity=None):
    identity = identity or Mock(resolve=AsyncMock(return_value=PRINCIPAL))
    return TestClient(create_app(Mock(), identity, allowed_origins=("https://portal",), workbench=dispatcher)), identity


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_preserves_native_events_and_reports_persisted_state_without_payload():
    dispatcher, ledger, calls, preparer = setup_dispatch([event(answer="hello"), event("message_end")])
    client, identity = client_for(dispatcher)
    with client:
        response = client.post(URL, json=BODY, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    data = frames(response)
    assert [item["event"] for item in data] == [
        "enterprise_send_state",
        "message",
        "message_end",
        "enterprise_send_state",
    ]
    assert data[1] == event(answer="hello")
    assert data[-1]["data"]["status"] == "accepted"
    assert data[-1]["data"]["message_id"] == str(receipt().message_id)
    assert "payload" not in response.text and "Bearer" not in response.text
    assert ledger.acks == 1 and len(calls) == 1
    identity.resolve.assert_awaited_once_with(
        cookie_header="csrf_token=csrf", authorization="Bearer session", csrf_token="csrf"
    )
    assert preparer.prepare.call_args.args[0] == PRINCIPAL


def test_duplicate_returns_state_without_native_dispatch():
    current = acknowledge_message(claim_message(intent()), receipt())
    dispatcher, ledger, calls, _ = setup_dispatch(current=current, dispatch=False)
    client, _ = client_for(dispatcher)
    with client:
        response = client.post(URL, json=BODY, headers=HEADERS)
    assert all(item["event"] == "enterprise_send_state" for item in frames(response))
    assert frames(response)[-1]["data"]["status"] == "accepted"
    assert calls == [] and ledger.acks == 0


@pytest.mark.parametrize("error", [Unauthenticated("private-token"), AccessDenied("private-token")])
def test_identity_denial_is_private_and_precedes_dispatch(error):
    dispatcher, _, calls, preparer = setup_dispatch()
    client, _ = client_for(dispatcher, Mock(resolve=AsyncMock(side_effect=error)))
    with client:
        response = client.post(URL, json=BODY, headers=HEADERS)
    assert response.status_code == error.status_code
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"
    assert "private-token" not in response.text
    assert calls == [] and preparer.prepare.await_count == 0


def test_untrusted_origin_never_resolves_identity_or_prepares():
    dispatcher, _, calls, preparer = setup_dispatch()
    client, identity = client_for(dispatcher)
    with client:
        response = client.post(URL, json=BODY, headers={**HEADERS, "origin": "https://other"})
    assert response.status_code == 403
    identity.resolve.assert_not_called()
    preparer.prepare.assert_not_called()
    assert calls == []


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "workspace_id": "other"},
        {**BODY, "client_message_id": "bad"},
        {**BODY, "client_message_id": str(UUID(int=0))},
        {**BODY, "payload": []},
    ],
)
def test_invalid_envelope_never_dispatches(body):
    dispatcher, _, calls, preparer = setup_dispatch()
    client, _ = client_for(dispatcher)
    with client:
        response = client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    assert response.headers["cache-control"] == "private, no-store"
    assert calls == [] and preparer.prepare.await_count == 0


def test_unconfigured_workbench_returns_private_503():
    client, _ = client_for(None)
    with client:
        response = client.post(URL, json=BODY, headers=HEADERS)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"


def test_native_failure_after_stream_headers_is_opaque_stream_error():
    dispatcher, ledger, calls, _ = setup_dispatch(status=500)
    client, _ = client_for(dispatcher)
    with client:
        response = client.post(URL, json=BODY, headers=HEADERS)
    assert response.status_code == 200
    assert frames(response) == [{"event": "enterprise_send_error", "code": "native_chat_dispatch_uncertain"}]
    assert ledger.current.status == "uncertain" and len(calls) == 1


def test_unexpected_stream_error_is_sanitized():
    dispatcher, _, calls, preparer = setup_dispatch()
    preparer.prepare.side_effect = RuntimeError("private-token")
    client, _ = client_for(dispatcher)
    with client:
        response = client.post(URL, json=BODY, headers=HEADERS)
    assert frames(response) == [{"event": "enterprise_send_error", "code": "enterprise_error"}]
    assert "private-token" not in response.text and calls == []


def test_real_identity_adapter_rejects_mismatched_csrf_before_preparation():
    import httpx

    from enterprise_platform.adapters.dify_identity import DifyIdentityClient

    dispatcher, _, calls, preparer = setup_dispatch()
    native_calls = []
    identity = DifyIdentityClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(lambda req: native_calls.append(req))
    )
    client, _ = client_for(dispatcher, identity)
    with client:
        response = client.post(URL, json=BODY, headers={**HEADERS, "x-csrf-token": "different"})
    assert response.status_code == 401
    assert calls == [] and native_calls == []
    preparer.prepare.assert_not_called()


@pytest.mark.parametrize("after_message", [False, True])
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_asgi_disconnect_closes_native_stream_and_settles_ledger(after_message, spec_version):
    import asyncio

    import httpx
    from starlette.requests import ClientDisconnect

    from enterprise_platform.adapters.workbench_context_client import DifyWorkbenchContextClient

    class NativeStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if after_message:
                yield ("data: " + json.dumps(event()) + "\n\n").encode()
            await asyncio.Future()

        async def aclose(self):
            self.closed = True

    native_stream = NativeStream()
    dispatcher, ledger, _, _ = setup_dispatch()
    dispatcher._native = DifyWorkbenchContextClient(
        base_url="https://native/console/api",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=native_stream)
        ),
    )
    client, _ = client_for(dispatcher)

    async def run():
        disconnect = asyncio.Event()
        initial = True

        async def receive():
            nonlocal initial
            if initial:
                initial = False
                return {"type": "http.request", "body": json.dumps(BODY).encode(), "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                frame = json.loads(message["body"].decode().split("data: ", 1)[1])
                if frame["event"] == ("message" if after_message else "enterprise_send_state"):
                    if spec_version == "2.4":
                        raise OSError("Disconnected")
                    disconnect.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": URL,
            "raw_path": URL.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (key.encode(), value.encode()) for key, value in {**HEADERS, "content-type": "application/json"}.items()
            ],
            "server": ("portal", 443),
            "client": ("test", 123),
        }
        try:
            await asyncio.wait_for(client.app(scope, receive, send), timeout=5)
        except ClientDisconnect:
            assert spec_version == "2.4"
        assert native_stream.closed
        assert ledger.current.status == ("accepted" if after_message else "uncertain")

    asyncio.run(run())
    assert native_stream.closed
    assert ledger.current.status == ("accepted" if after_message else "uncertain")
