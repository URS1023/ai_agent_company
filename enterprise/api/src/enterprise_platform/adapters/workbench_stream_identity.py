"""Correlate trusted native transport events, never client bodies or completion proof.

The dispatcher must persist receipts before forwarding events. This observer performs
no database writes and does not release branch occupancy, including on message_end.
"""

import json
from uuid import UUID

from pydantic import TypeAdapter

from enterprise_platform.application.contracts import Identifier, JsonObject
from enterprise_platform.application.errors import InvalidState
from enterprise_platform.application.workbench_messages import ChatSendIntent, NativeMessageReceipt

from .workbench_stream import ChatStreamInvalid

_TASK = TypeAdapter(Identifier)


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("Invalid native identity")
    parsed = UUID(value)
    if parsed.int == 0 or str(parsed) != value:
        raise ValueError("Invalid native identity")
    return parsed


class ChatStreamIdentity:
    def __init__(self, claimed: ChatSendIntent) -> None:
        self._claimed = ChatSendIntent.model_validate(claimed.model_dump())
        if self._claimed.status != "dispatched":
            raise InvalidState("chat_stream_requires_claim")
        payload = json.loads(self._claimed.payload_json)
        conversation = payload.get("conversation_id")
        self._conversation = _uuid(conversation) if conversation else None
        self._receipt: NativeMessageReceipt | None = None

    @property
    def receipt(self) -> NativeMessageReceipt | None:
        return self._receipt

    def observe(self, event: JsonObject) -> NativeMessageReceipt | None:
        try:
            kind = event.get("event")
            if not isinstance(kind, str) or not kind or len(kind) > 128:
                raise ValueError("Invalid event")
            conversation = _uuid(event["conversation_id"])
            message = _uuid(event["message_id"])
            if self._conversation is not None and conversation != self._conversation:
                raise ValueError("Conversation changed")
            if self._receipt is not None and (
                conversation != self._receipt.conversation_id or message != self._receipt.message_id
            ):
                raise ValueError("Message changed")
            # Older native error responses may omit task_id. Never infer it from another event.
            if kind == "error" and "task_id" not in event:
                return self._receipt
            task = _TASK.validate_python(event["task_id"])
            candidate = NativeMessageReceipt(
                scope=self._claimed.scope,
                client_message_id=self._claimed.client_message_id,
                conversation_id=conversation,
                message_id=message,
                task_id=task,
            )
            if self._receipt is not None and candidate != self._receipt:
                raise ValueError("Task changed")
        except (ValueError, KeyError, TypeError) as exc:
            raise ChatStreamInvalid("Native chat identity unavailable") from exc
        self._receipt = candidate
        return candidate
