"""Pure send-intent transitions; storage must CAS a claim before native dispatch.

An accepted intent identifies an acknowledged native message, not a completed reply
or business action. This module performs no I/O, authorization or automatic retry.
Payloads are canonical immutable snapshots of requests validated by the gateway.
"""

from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .contracts import Contract, Identifier, JsonObject, canonical_json
from .errors import Conflict, InvalidState

type SendStatus = Literal["queued", "dispatched", "uncertain", "accepted"]
type GenerationOutcome = Literal["succeeded", "partial-succeeded", "failed", "stopped"]
_PAYLOAD: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_PAYLOAD_BYTES = 262144


class MessageScope(Contract):
    workspace_id: Identifier
    actor_id: Identifier
    installed_app_id: UUID
    branch_id: Identifier


class NativeMessageReceipt(Contract):
    scope: MessageScope
    client_message_id: UUID
    conversation_id: UUID
    message_id: UUID
    task_id: Identifier


class NativeGenerationTerminal(Contract):
    """Trusted reconciler output, never a client body or an unqualified message_end.

    Advanced-chat message_end can also mean paused; the reconciler must verify
    actual terminal runtime status before constructing this record.
    """

    receipt: NativeMessageReceipt
    outcome: GenerationOutcome


class ChatSendIntent(Contract):
    scope: MessageScope
    client_message_id: UUID
    payload_json: str = Field(strict=True)
    status: SendStatus = "queued"
    revision: int = Field(default=1, strict=True, ge=1)
    receipt: NativeMessageReceipt | None = None
    terminal: NativeGenerationTerminal | None = None

    @field_validator("payload_json")
    @classmethod
    def validate_snapshot(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Message snapshot exceeds 256 KiB")
        document = _PAYLOAD.validate_json(value)
        if canonical_json(document) != value:
            raise ValueError("Message snapshot must be canonical JSON")
        return value

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.terminal is not None and (self.status != "accepted" or self.terminal.receipt != self.receipt):
            raise ValueError("Terminal generation must match the accepted native receipt")
        if (self.status == "accepted") != (self.receipt is not None):
            raise ValueError("Only accepted sends have native receipts")
        if self.receipt is not None and (
            self.receipt.scope != self.scope or self.receipt.client_message_id != self.client_message_id
        ):
            raise ValueError("Native receipt does not belong to this send")
        return self

    @property
    def payload_hash(self) -> str:
        return sha256(self.payload_json.encode("utf-8")).hexdigest()


def create_message_intent(scope: MessageScope, client_message_id: UUID, payload: JsonObject) -> ChatSendIntent:
    return ChatSendIntent(scope=scope, client_message_id=client_message_id, payload_json=canonical_json(payload))


def ensure_same_send(existing: ChatSendIntent, requested: ChatSendIntent) -> ChatSendIntent:
    """Return the current record without resetting dispatch, receipt or revision."""
    if (
        existing.scope != requested.scope
        or existing.client_message_id != requested.client_message_id
        or existing.payload_json != requested.payload_json
    ):
        raise Conflict("message_request_conflict")
    return existing


def _transition(
    intent: ChatSendIntent, status: SendStatus, receipt: NativeMessageReceipt | None = None
) -> ChatSendIntent:
    return ChatSendIntent.model_validate(
        {**intent.model_dump(), "status": status, "revision": intent.revision + 1, "receipt": receipt}
    )


def claim_message(intent: ChatSendIntent) -> ChatSendIntent:
    """The returned state must be committed via CAS before any outbound POST."""
    if intent.status != "queued":
        raise InvalidState("message_already_dispatched")
    return _transition(intent, "dispatched")


def mark_send_uncertain(intent: ChatSendIntent) -> ChatSendIntent:
    if intent.status in {"accepted", "uncertain"}:
        return intent
    if intent.status != "dispatched":
        raise InvalidState("message_not_dispatched")
    return _transition(intent, "uncertain")


def acknowledge_message(intent: ChatSendIntent, receipt: NativeMessageReceipt) -> ChatSendIntent:
    if receipt.scope != intent.scope or receipt.client_message_id != intent.client_message_id:
        raise Conflict("message_receipt_scope_mismatch")
    if intent.status == "accepted":
        if intent.receipt != receipt:
            raise Conflict("message_receipt_conflict")
        return intent
    if intent.status not in {"dispatched", "uncertain"}:
        raise InvalidState("message_not_dispatched")
    return _transition(intent, "accepted", receipt)


def finish_message(intent: ChatSendIntent, terminal: NativeGenerationTerminal) -> ChatSendIntent:
    if intent.status != "accepted":
        raise InvalidState("message_not_acknowledged")
    if intent.receipt != terminal.receipt:
        raise Conflict("message_terminal_identity_mismatch")
    if intent.terminal is not None:
        if intent.terminal != terminal:
            raise Conflict("message_terminal_conflict")
        return intent
    return ChatSendIntent.model_validate(
        {
            **intent.model_dump(),
            "revision": intent.revision + 1,
            "terminal": terminal,
        }
    )
