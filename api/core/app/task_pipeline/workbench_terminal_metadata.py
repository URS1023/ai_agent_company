"""Server-owned terminal metadata, written in the existing native message transaction.

Only queue completion/stop/error events establish this record. It is not a public receipt
payload or branch-release permit. Existing metadata is preserved; absent terminal
evidence leaves its original bytes unchanged. No I/O or independent commit occurs.
"""

import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from core.app.entities.queue_entities import QueueErrorEvent, QueueMessageEndEvent, QueueStopEvent

_METADATA: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


class PlainChatTerminalMetadata(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)

    version: int = Field(ge=1, le=1)
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    outcome: Literal["succeeded", "stopped", "failed"]
    stop_reason: Literal["user_manual", "annotation_reply", "output_moderation", "input_moderation"] | None

    @model_validator(mode="after")
    def consistent_stop(self) -> Self:
        if (self.outcome == "stopped") != (self.stop_reason is not None):
            raise ValueError("Inconsistent native stop evidence")
        return self


def read_terminal_metadata(metadata_json: str | None) -> PlainChatTerminalMetadata | None:
    """Old, malformed and unsupported metadata remains unproven, without echoing content."""
    if metadata_json is None:
        return None
    try:
        metadata = _METADATA.validate_json(metadata_json)
        return PlainChatTerminalMetadata.model_validate(metadata.get("enterprise_generation"))
    except (ValueError, TypeError, RecursionError):
        return None


def with_terminal_metadata(
    metadata_json: str,
    *,
    task_id: str,
    event: QueueMessageEndEvent | QueueStopEvent | QueueErrorEvent | None,
) -> str:
    if event is None:
        return metadata_json
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128 or task_id.strip() != task_id:
        raise ValueError("Native generation task identity required")
    metadata = _METADATA.validate_json(metadata_json)
    stopped = isinstance(event, QueueStopEvent)
    metadata["enterprise_generation"] = {
        "version": 1,
        "task_id": task_id,
        "outcome": "failed" if isinstance(event, QueueErrorEvent) else "stopped" if stopped else "succeeded",
        "stop_reason": event.stopped_by.value if isinstance(event, QueueStopEvent) else None,
    }
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
