from uuid import UUID

import pytest
from test_workbench_messages import intent, receipt

from enterprise_platform.adapters.workbench_stream import ChatStreamInvalid
from enterprise_platform.application.workbench_messages import claim_message, create_message_intent


def tracker(request=None):
    from enterprise_platform.adapters.workbench_stream_identity import ChatStreamIdentity

    return ChatStreamIdentity(claim_message(request or intent()))


def event(kind="message", **changes):
    return {
        "event": kind,
        "conversation_id": str(UUID(int=3)),
        "message_id": str(UUID(int=4)),
        "task_id": "task-1",
        **changes,
    }


def test_receipt_uses_claim_scope_and_client_not_event_fields():
    stream = tracker()
    assert stream.observe(event(workspace_id="foreign", client_message_id=str(UUID(int=99)))) == receipt()
    assert stream.receipt == receipt()


@pytest.mark.parametrize("kind", ["message_end", "workflow_paused", "workflow_finished", "future_event"])
def test_events_identify_message_without_marking_generation_complete(kind):
    stream = tracker()
    assert stream.observe(event(kind)) == receipt()
    assert not hasattr(stream, "terminal")


@pytest.mark.parametrize(
    "field,value",
    [
        ("conversation_id", str(UUID(int=7))),
        ("message_id", str(UUID(int=8))),
        ("task_id", "other"),
    ],
)
def test_later_foreign_identity_rejected_without_replacing_receipt(field, value):
    stream = tracker()
    stream.observe(event())
    with pytest.raises(ChatStreamInvalid):
        stream.observe(event(**{field: value}))
    assert stream.receipt == receipt()


def test_existing_conversation_must_match_frozen_request():
    request = create_message_intent(
        intent().scope,
        intent().client_message_id,
        {"query": "hello", "inputs": {}, "conversation_id": str(UUID(int=9))},
    )
    stream = tracker(request)
    with pytest.raises(ChatStreamInvalid):
        stream.observe(event())
    assert stream.receipt is None


@pytest.mark.parametrize("value", [None, 4, "", "bad", str(UUID(int=0))])
def test_invalid_message_identity_rejected(value):
    with pytest.raises(ChatStreamInvalid):
        tracker().observe(event(message_id=value))


def test_error_without_task_id_does_not_invent_receipt():
    stream = tracker()
    body = event("error")
    del body["task_id"]
    assert stream.observe(body) is None
    stream.observe(event())
    assert stream.observe(body) == receipt()
    with pytest.raises(ChatStreamInvalid):
        stream.observe({**body, "message_id": str(UUID(int=9))})


def test_missing_normal_task_is_not_assembled_from_previous_event():
    stream = tracker()
    stream.observe(event())
    body = event()
    del body["task_id"]
    with pytest.raises(ChatStreamInvalid):
        stream.observe(body)


def test_event_specific_id_is_not_a_message_alias():
    assert tracker().observe(event("agent_thought", id="thought-id")) == receipt()
    assert tracker().observe(event("message_file", id="file-id")) == receipt()


def test_requires_dispatched_claim():
    from enterprise_platform.adapters.workbench_stream_identity import ChatStreamIdentity
    from enterprise_platform.application.errors import InvalidState

    with pytest.raises(InvalidState):
        ChatStreamIdentity(intent())
