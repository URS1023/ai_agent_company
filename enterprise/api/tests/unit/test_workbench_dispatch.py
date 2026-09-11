import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import intent, receipt
from test_workbench_stream_identity import event

from enterprise_platform.adapters.workbench_context_client import ChatDispatchUncertain, DifyWorkbenchContextClient
from enterprise_platform.application.errors import Conflict, PersistenceError
from enterprise_platform.application.workbench_messages import (
    acknowledge_message,
    claim_message,
    mark_send_uncertain,
)
from enterprise_platform.application.workbench_service import DispatchPreparation


class Ledger:
    def __init__(self, current):
        self.current = current
        self.acks = 0
        self.uncertain = 0
        self.fail_ack = False

    def get(self, scope, client_message_id):
        assert scope == self.current.scope and client_message_id == self.current.client_message_id
        return self.current

    def acknowledge(self, native_receipt, *, expected_revision):
        self.acks += 1
        if self.fail_ack:
            raise PersistenceError()
        assert self.current.revision == expected_revision
        self.current = acknowledge_message(self.current, native_receipt)
        return self.current

    def mark_uncertain(self, scope, client_message_id, *, expected_revision):
        self.uncertain += 1
        assert self.current.revision == expected_revision
        self.current = mark_send_uncertain(self.current)
        return self.current


def setup_dispatch(events=(), *, status=200, current=None, dispatch=True):
    from enterprise_platform.adapters.workbench_dispatch import WorkbenchChatDispatcher

    current = current or claim_message(intent())
    ledger = Ledger(current)
    preparer = Mock(prepare=AsyncMock(return_value=DispatchPreparation(current, dispatch)))
    requests = []

    def handler(request):
        requests.append(request)
        body = b"".join(("data: " + json.dumps(item) + "\n\n").encode() for item in events)
        return httpx.Response(status, headers={"content-type": "text/event-stream"}, content=body)

    native = DifyWorkbenchContextClient(base_url="https://native/console/api", transport=httpx.MockTransport(handler))
    return WorkbenchChatDispatcher(preparer, ledger, native), ledger, requests, preparer


def open_dispatch(dispatcher):
    return dispatcher.open(
        PRINCIPAL,
        SESSION,
        installed_app_id=intent().scope.installed_app_id,
        branch_id="branch",
        client_message_id=intent().client_message_id,
        payload=json.loads(intent().payload_json),
    )


def test_receipt_is_committed_before_first_event_and_only_once():
    dispatcher, ledger, calls, preparer = setup_dispatch([event(), event("message_end")])

    async def run():
        async with open_dispatch(dispatcher) as stream:
            assert stream.intent.status == "dispatched"
            seen = []
            async for item in stream.events:
                assert ledger.current.receipt == receipt()
                assert stream.intent == ledger.current
                seen.append(item)
            assert len(seen) == 2
            assert stream.intent.terminal is None

    asyncio.run(run())
    assert ledger.acks == 1 and ledger.uncertain == 0
    assert len(calls) == 1
    preparer.prepare.assert_awaited_once()


@pytest.mark.parametrize(
    "current",
    [
        claim_message(intent()),
        mark_send_uncertain(claim_message(intent())),
        acknowledge_message(claim_message(intent()), receipt()),
    ],
)
def test_previously_claimed_request_never_posts_again(current):
    dispatcher, ledger, calls, _ = setup_dispatch(current=current, dispatch=False)

    async def run():
        async with open_dispatch(dispatcher) as stream:
            assert [item async for item in stream.events] == []
            assert stream.intent == current

    asyncio.run(run())
    assert calls == [] and ledger.uncertain == 0


@pytest.mark.parametrize("status", [200, 403, 500])
def test_empty_or_failed_response_persists_uncertain_without_retry(status):
    dispatcher, ledger, calls, _ = setup_dispatch(status=status)

    async def run():
        async with open_dispatch(dispatcher) as stream:
            assert [item async for item in stream.events] == []

    if status == 200:
        asyncio.run(run())
    else:
        with pytest.raises(ChatDispatchUncertain):
            asyncio.run(run())
    assert ledger.current.status == "uncertain" and ledger.uncertain == 1
    assert len(calls) == 1


def test_ack_failure_does_not_forward_event_or_release_branch():
    dispatcher, ledger, calls, _ = setup_dispatch([event()])
    ledger.fail_ack = True
    seen = []

    async def run():
        async with open_dispatch(dispatcher) as stream:
            async for item in stream.events:
                seen.append(item)

    with pytest.raises(PersistenceError):
        asyncio.run(run())
    assert seen == [] and ledger.current.status == "uncertain"
    assert len(calls) == 1


@pytest.mark.parametrize("accepted", [False, True])
def test_consumer_cancellation_preserves_accepted_or_marks_unacknowledged(accepted):
    dispatcher, ledger, calls, _ = setup_dispatch([event()])

    async def run():
        async with open_dispatch(dispatcher) as stream:
            if accepted:
                await anext(stream.events)
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert ledger.current.status == ("accepted" if accepted else "uncertain")
    assert ledger.current.terminal is None and len(calls) == 1


def test_prepare_failure_never_opens_native_stream():
    dispatcher, ledger, calls, preparer = setup_dispatch()
    preparer.prepare.side_effect = Conflict()

    async def run():
        async with open_dispatch(dispatcher):
            pytest.fail("Preparation failed")

    with pytest.raises(Conflict):
        asyncio.run(run())
    assert calls == [] and ledger.uncertain == 0


def test_cancellation_during_ack_waits_for_commit_before_cleanup():
    from threading import Event

    dispatcher, ledger, calls, _ = setup_dispatch([event()])
    entered, release = Event(), Event()
    original_ack = ledger.acknowledge

    def slow_ack(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_ack(*args, **kwargs)

    ledger.acknowledge = slow_ack
    seen = []

    async def consume():
        async with open_dispatch(dispatcher) as stream:
            async for item in stream.events:
                seen.append(item)

    async def run():
        task = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    asyncio.run(run())
    assert ledger.current.status == "accepted" and ledger.current.receipt == receipt()
    assert ledger.uncertain == 0 and seen == [] and len(calls) == 1


def test_actual_preparer_duplicate_request_runs_native_generation_once():
    from enterprise_platform.application.workbench_service import WorkbenchSendService

    dispatcher, ledger, calls, _ = setup_dispatch([event()])
    ledger.current = intent()

    def create_or_get(requested):
        from enterprise_platform.application.workbench_messages import ensure_same_send

        return ensure_same_send(ledger.current, requested)

    def claim(scope, client_message_id, *, expected_revision):
        assert ledger.current.revision == expected_revision
        ledger.current = claim_message(ledger.current)
        return ledger.current

    repository = Mock(create_or_get=create_or_get, claim=claim)
    checks = Mock(check_context=AsyncMock(), check_selected_attachments=AsyncMock())
    authority = Mock(require_send=AsyncMock())
    dispatcher._preparer = WorkbenchSendService(repository, authority, checks)

    async def run():
        async with open_dispatch(dispatcher) as first:
            assert len([item async for item in first.events]) == 1
        async with open_dispatch(dispatcher) as duplicate:
            assert [item async for item in duplicate.events] == []
            assert duplicate.intent.receipt == receipt()

    asyncio.run(run())
    assert len(calls) == 1 and ledger.acks == 1
    assert checks.check_context.await_count == 2


def test_cleanup_does_not_overwrite_concurrent_acknowledgement():
    dispatcher, ledger, calls, _ = setup_dispatch()

    def concurrent_ack(*args, **kwargs):
        ledger.current = acknowledge_message(ledger.current, receipt())
        raise Conflict()

    ledger.mark_uncertain = concurrent_ack

    async def run():
        async with open_dispatch(dispatcher) as stream:
            assert [item async for item in stream.events] == []
        assert stream.intent.receipt == receipt()

    asyncio.run(run())
    assert ledger.current.status == "accepted" and len(calls) == 1


def test_fully_consumed_workflow_persists_terminal_only_after_native_state_check():
    from test_workbench_completion import workflow_event
    from test_workbench_generation_client import body

    from enterprise_platform.adapters.workbench_context_client import GenerationStateObservation
    from enterprise_platform.application.workbench_messages import finish_message

    dispatcher, ledger, calls, _ = setup_dispatch(
        [
            workflow_event("workflow_started"),
            event("message_end"),
            workflow_event("workflow_finished"),
        ]
    )
    reader = Mock(read_generation_state=AsyncMock(return_value=GenerationStateObservation(**body())))
    dispatcher._completion_reader = reader
    finished = []

    def finish(terminal, *, expected_revision):
        assert reader.read_generation_state.await_count == 1
        assert expected_revision == ledger.current.revision
        finished.append(terminal)
        ledger.current = finish_message(ledger.current, terminal)
        return ledger.current

    ledger.finish_generation = finish

    async def run():
        async with open_dispatch(dispatcher) as stream:
            async for _item in stream.events:
                assert ledger.current.terminal is None
            assert stream.intent.terminal.outcome == "succeeded"

    asyncio.run(run())
    assert len(finished) == 1 and len(calls) == 1


def test_early_stream_exit_never_reads_or_persists_completion():
    from test_workbench_completion import workflow_event

    dispatcher, ledger, _, _ = setup_dispatch(
        [
            workflow_event("workflow_started"),
            event("message_end"),
            workflow_event("workflow_finished"),
        ]
    )
    reader = Mock(read_generation_state=AsyncMock())
    dispatcher._completion_reader = reader
    ledger.finish_generation = Mock()

    async def run():
        async with open_dispatch(dispatcher) as stream:
            await anext(stream.events)

    asyncio.run(run())
    reader.read_generation_state.assert_not_called()
    ledger.finish_generation.assert_not_called()
    assert ledger.current.terminal is None


def test_terminal_commit_failure_does_not_report_completion_or_retry_generation():
    from test_workbench_completion import workflow_event
    from test_workbench_generation_client import body

    dispatcher, ledger, calls, _ = setup_dispatch(
        [
            workflow_event("workflow_started"),
            event("message_end"),
            workflow_event("workflow_finished"),
        ]
    )
    state_calls = []

    def state_handler(request):
        state_calls.append(request)
        assert request.url.path.endswith("/enterprise/workbench/generation-state")
        return httpx.Response(200, json=body())

    dispatcher._completion_reader = DifyWorkbenchContextClient(
        base_url="https://native/console/api",
        transport=httpx.MockTransport(state_handler),
    )
    ledger.finish_generation = Mock(side_effect=PersistenceError())

    async def run():
        async with open_dispatch(dispatcher) as stream:
            async for _item in stream.events:
                pass

    with pytest.raises(PersistenceError):
        asyncio.run(run())
    assert ledger.current.status == "accepted" and ledger.current.terminal is None
    assert len(calls) == len(state_calls) == 1
    ledger.finish_generation.assert_called_once()


@pytest.mark.parametrize("outcome, reason", [("succeeded", None), ("stopped", "user_manual")])
def test_plain_chat_dispatch_finishes_using_native_persisted_task_marker(outcome, reason):
    from test_workbench_generation_client import plain_body

    from enterprise_platform.application.workbench_messages import finish_message

    dispatcher, ledger, calls, _ = setup_dispatch([event(), event("message_end")])
    state_calls = []

    def read_state(request):
        state_calls.append(request)
        return httpx.Response(200, json=plain_body(outcome=outcome, stop_reason=reason))

    dispatcher._completion_reader = DifyWorkbenchContextClient(
        base_url="https://native/console/api",
        transport=httpx.MockTransport(read_state),
    )

    def finish(terminal, *, expected_revision):
        assert expected_revision == ledger.current.revision
        ledger.current = finish_message(ledger.current, terminal)
        return ledger.current

    ledger.finish_generation = finish

    async def run():
        async with open_dispatch(dispatcher) as stream:
            assert len([item async for item in stream.events]) == 2
        assert stream.intent.terminal.outcome == outcome

    asyncio.run(run())
    assert len(calls) == len(state_calls) == 1


def test_error_only_stream_records_receipt_and_failed_terminal_without_retry():
    from test_workbench_generation_client import plain_body

    from enterprise_platform.application.workbench_messages import finish_message

    dispatcher, ledger, calls, _ = setup_dispatch([event("error", code="completion_request_error")])
    state_calls = []

    def state(request):
        state_calls.append(request)
        return httpx.Response(200, json={**plain_body(outcome="failed"), "message_status": "error"})

    dispatcher._completion_reader = DifyWorkbenchContextClient(
        base_url="https://native/console/api",
        transport=httpx.MockTransport(state),
    )

    def finish(terminal, *, expected_revision):
        assert expected_revision == ledger.current.revision
        ledger.current = finish_message(ledger.current, terminal)
        return ledger.current

    ledger.finish_generation = finish

    async def run():
        async with open_dispatch(dispatcher) as stream:
            seen = [item async for item in stream.events]
        assert [item["event"] for item in seen] == ["error"]
        assert stream.intent.receipt == receipt()
        assert stream.intent.terminal.outcome == "failed"

    asyncio.run(run())
    assert ledger.acks == 1 and len(calls) == len(state_calls) == 1
