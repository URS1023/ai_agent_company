from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_platform.application.contracts import JsonObject
from enterprise_platform.application.errors import Conflict, InvalidState
from enterprise_platform.application.workbench_messages import (
    ChatSendIntent,
    MessageScope,
    NativeMessageReceipt,
    acknowledge_message,
    claim_message,
    create_message_intent,
    ensure_same_send,
    mark_send_uncertain,
)


def scope() -> MessageScope:
    return MessageScope(workspace_id="w", actor_id="actor", installed_app_id=UUID(int=1), branch_id="branch")


def intent() -> ChatSendIntent:
    return create_message_intent(scope(), UUID(int=2), {"query": "  设备分析\n", "inputs": {}})


def receipt() -> NativeMessageReceipt:
    return NativeMessageReceipt(
        scope=scope(),
        client_message_id=UUID(int=2),
        conversation_id=UUID(int=3),
        message_id=UUID(int=4),
        task_id="task-1",
    )


def test_snapshot_is_canonical_and_detached_from_mutable_inputs() -> None:
    body: JsonObject = {"query": "  设备分析\n", "inputs": {}}
    saved = create_message_intent(scope(), UUID(int=2), body)
    body["query"] = "changed"

    assert saved.payload_json == '{"inputs":{},"query":"  设备分析\\n"}'
    assert ensure_same_send(saved, intent()) is saved
    assert saved.status == "queued"
    assert saved.revision == 1


@pytest.mark.parametrize("state", ["dispatched", "uncertain", "accepted"])
def test_retries_return_existing_state_without_requeue(state: str) -> None:
    saved = claim_message(intent())
    if state == "uncertain":
        saved = mark_send_uncertain(saved)
    elif state == "accepted":
        saved = acknowledge_message(saved, receipt())

    assert ensure_same_send(saved, intent()) is saved
    with pytest.raises(InvalidState):
        claim_message(saved)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("actor_id", "other"),
        ("installed_app_id", UUID(int=8)),
        ("branch_id", "other"),
    ],
)
def test_scope_changes_cannot_reuse_a_send(field: str, value: str | UUID) -> None:
    other_scope = MessageScope.model_validate({**scope().model_dump(), field: value})
    retry = create_message_intent(other_scope, UUID(int=2), {"inputs": {}, "query": "  设备分析\n"})
    with pytest.raises(Conflict):
        ensure_same_send(intent(), retry)


def test_changed_payload_or_client_id_conflicts() -> None:
    with pytest.raises(Conflict):
        ensure_same_send(intent(), create_message_intent(scope(), UUID(int=2), {"query": "different"}))
    with pytest.raises(Conflict):
        ensure_same_send(intent(), create_message_intent(scope(), UUID(int=7), {"query": "  设备分析\n", "inputs": {}}))


def test_uncertain_send_can_be_reconciled_but_not_dispatched_again() -> None:
    original = intent()
    claimed = claim_message(original)
    uncertain = mark_send_uncertain(claimed)
    accepted = acknowledge_message(uncertain, receipt())

    assert [item.revision for item in (original, claimed, uncertain, accepted)] == [1, 2, 3, 4]
    assert accepted.status == "accepted"
    assert accepted.receipt == receipt()
    assert acknowledge_message(accepted, receipt()) is accepted
    assert mark_send_uncertain(accepted) is accepted
    assert original.status == "queued"


def test_receipts_must_match_scope_and_client_request() -> None:
    for update in ({"client_message_id": UUID(int=9)}, {"scope": {**scope().model_dump(), "branch_id": "other"}}):
        foreign = NativeMessageReceipt.model_validate({**receipt().model_dump(), **update})
        with pytest.raises(Conflict):
            acknowledge_message(claim_message(intent()), foreign)


@pytest.mark.parametrize(
    "field,value", [("message_id", UUID(int=99)), ("conversation_id", UUID(int=99)), ("task_id", "other-task")]
)
def test_conflicting_native_identity_never_overwrites_an_acknowledgement(field: str, value: UUID | str) -> None:
    saved = acknowledge_message(claim_message(intent()), receipt())
    changed = NativeMessageReceipt.model_validate({**receipt().model_dump(), field: value})
    with pytest.raises(Conflict):
        acknowledge_message(saved, changed)


def test_queued_intent_cannot_be_acknowledged_or_marked_uncertain() -> None:
    with pytest.raises(InvalidState):
        acknowledge_message(intent(), receipt())
    with pytest.raises(InvalidState):
        mark_send_uncertain(intent())


def test_snapshot_size_and_nonfinite_numbers_are_rejected() -> None:
    with pytest.raises(ValueError):
        create_message_intent(scope(), UUID(int=2), {"query": "中" * 100000})
    with pytest.raises(ValueError):
        create_message_intent(scope(), UUID(int=2), {"inputs": {"value": float("nan")}})


def test_persisted_state_cannot_claim_acceptance_without_a_receipt() -> None:
    saved = intent()
    with pytest.raises(ValidationError):
        type(saved).model_validate({**saved.model_dump(), "status": "accepted"})
    with pytest.raises(ValidationError):
        type(saved).model_validate({**saved.model_dump(), "receipt": receipt()})


@pytest.mark.parametrize("snapshot", ["[]", '{"query": "noncanonical"}', '{"number":NaN}'])
def test_loaded_snapshots_must_be_canonical_finite_json_objects(snapshot: str) -> None:
    with pytest.raises(ValueError):
        ChatSendIntent.model_validate({**intent().model_dump(), "payload_json": snapshot})
