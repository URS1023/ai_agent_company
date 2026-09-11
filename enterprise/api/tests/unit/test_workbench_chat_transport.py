import asyncio
import json

import httpx
import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import intent
from test_workbench_stream_identity import event

from enterprise_platform.adapters.workbench_context_client import DifyWorkbenchContextClient
from enterprise_platform.application.workbench_messages import claim_message, mark_send_uncertain


def send(handler, claimed=None, **options):
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(handler), **options
    )

    async def run():
        async with client.open_chat(PRINCIPAL, SESSION, claimed or claim_message(intent())) as events:
            return [event async for event in events]

    return asyncio.run(run())


def test_claimed_snapshot_is_sent_once_to_guarded_native_route():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            content=(
                "data: " + json.dumps(event(answer="hello")) + "\n\ndata: " + json.dumps(event("message_end")) + "\n\n"
            ).encode(),
        )

    events = send(handler)
    assert [event["event"] for event in events] == ["message", "message_end"]
    assert len(calls) == 1
    assert calls[0].url.path.endswith("/enterprise/workbench/chat-messages")
    assert json.loads(calls[0].content) == json.loads(intent().payload_json)
    assert calls[0].headers["X-Enterprise-Expected-Actor"] == "actor"
    assert calls[0].headers["accept"] == "text/event-stream"
    assert calls[0].headers["accept-encoding"] == "identity"
    assert "refresh_token" not in calls[0].headers["cookie"]


@pytest.mark.parametrize("claimed", [intent(), mark_send_uncertain(claim_message(intent()))])
def test_unclaimed_or_uncertain_intent_never_sends(claimed):
    from enterprise_platform.application.errors import InvalidState

    calls = []
    with pytest.raises(InvalidState):
        send(lambda request: calls.append(request), claimed)
    assert calls == []


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_non_success_requires_reconciliation_without_retries(status):
    from enterprise_platform.adapters.workbench_context_client import ChatDispatchUncertain

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.invalid"})

    with pytest.raises(ChatDispatchUncertain):
        send(handler)
    assert len(calls) == 1


def test_non_sse_body_is_not_accepted_as_chat_completion():
    from enterprise_platform.adapters.workbench_context_client import ChatDispatchUncertain

    with pytest.raises(ChatDispatchUncertain):
        send(lambda _: httpx.Response(200, json={"result": "success"}))


def test_truncated_stream_remains_uncertain():
    from enterprise_platform.adapters.workbench_context_client import ChatDispatchUncertain

    with pytest.raises(ChatDispatchUncertain):
        send(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=b'data: {"event":"message_end"}'
            )
        )


@pytest.mark.parametrize("cancel", [False, True])
def test_early_exit_or_cancellation_closes_native_stream(cancel):
    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield ("data: " + json.dumps(event(answer="first")) + "\n\n").encode()
            await asyncio.Future()

        async def aclose(self):
            self.closed = True

    stream = Stream()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def scenario():
        client = DifyWorkbenchContextClient(
            base_url="https://native/console/api", transport=httpx.MockTransport(handler)
        )
        async with client.open_chat(PRINCIPAL, SESSION, claim_message(intent())) as events:
            assert (await anext(events))["answer"] == "first"
            if cancel:
                raise asyncio.CancelledError()

    if cancel:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scenario())
    else:
        asyncio.run(scenario())
    assert stream.closed is True
    assert len(calls) == 1


def test_foreign_principal_stops_before_dispatch():
    from enterprise_platform.application.errors import AccessDenied

    calls = []

    async def scenario():
        client = DifyWorkbenchContextClient(
            base_url="https://native/console/api", transport=httpx.MockTransport(lambda request: calls.append(request))
        )
        async with client.open_chat(
            PRINCIPAL.model_copy(update={"actor_id": "other"}), SESSION, claim_message(intent())
        ):
            pytest.fail("Foreign principal entered stream")

    with pytest.raises(AccessDenied):
        asyncio.run(scenario())
    assert calls == []


def test_transport_rejects_cross_task_event_before_forwarding_it():
    from test_workbench_stream_identity import event

    from enterprise_platform.adapters.workbench_context_client import ChatDispatchUncertain

    payload = b"".join(("data: " + json.dumps(item) + "\n\n").encode() for item in [event(), event(task_id="foreign")])
    seen = []

    async def scenario():
        client = DifyWorkbenchContextClient(
            base_url="https://native/console/api",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)
            ),
        )
        async with client.open_chat(PRINCIPAL, SESSION, claim_message(intent())) as events:
            async for item in events:
                seen.append(item)

    with pytest.raises(ChatDispatchUncertain):
        asyncio.run(scenario())
    assert len(seen) == 1
