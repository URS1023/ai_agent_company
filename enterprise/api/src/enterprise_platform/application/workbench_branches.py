"""Branch context invariants, not a history copier or persistence implementation.

Fork creation never copies messages, workflow variables or business actions. The
caller must verify fork-point visibility, reconstruct only permitted history into
a fresh native conversation and verify its receipt before binding it here. Store
and advance branch revisions atomically with send claims to prevent racing heads.
"""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .contracts import Contract, JsonObject
from .errors import Conflict, InvalidState
from .workbench_messages import ChatSendIntent, MessageScope, NativeGenerationTerminal

_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ForkOrigin(Contract):
    scope: MessageScope
    conversation_id: UUID
    message_id: UUID

    @field_validator("conversation_id", "message_id")
    @classmethod
    def nonzero_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("Fork origin requires real native identities")
        return value


class BranchContext(Contract):
    scope: MessageScope
    revision: int = Field(default=1, strict=True, ge=1)
    state: Literal["preparing", "ready", "archived"]
    conversation_id: UUID | None = None
    head_message_id: UUID | None = None
    inflight_client_message_id: UUID | None = None
    origin: ForkOrigin | None = None

    @field_validator("conversation_id", "head_message_id", "inflight_client_message_id")
    @classmethod
    def nonzero_id(cls, value: UUID | None) -> UUID | None:
        if value is not None and value.int == 0:
            raise ValueError("Stored context requires real native identities")
        return value

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.head_message_id is not None and self.conversation_id is None:
            raise ValueError("A message head requires its conversation")
        if self.origin is not None:
            parent, own = self.origin.scope, self.scope
            if (parent.workspace_id, parent.actor_id, parent.installed_app_id) != (
                own.workspace_id,
                own.actor_id,
                own.installed_app_id,
            ) or parent.branch_id == own.branch_id:
                raise ValueError("Fork must preserve owner and app but use a new branch")
            if self.conversation_id == self.origin.conversation_id:
                raise ValueError("Fork requires an independent native conversation")
            if self.state == "ready" and (self.conversation_id is None or self.head_message_id is None):
                raise ValueError("Fork reconstruction must finish before sending")
        if self.state == "preparing" and (
            self.origin is None
            or self.conversation_id is not None
            or self.head_message_id is not None
            or self.inflight_client_message_id is not None
        ):
            raise ValueError("Preparing fork must remain unbound")
        return self


def create_root_branch(scope: MessageScope) -> BranchContext:
    return BranchContext(scope=scope, state="ready")


def fork_branch(parent: BranchContext, branch_id: str, message_id: UUID) -> BranchContext:
    """Record an already-authorized fork point; do not replay its actions."""
    if parent.state != "ready" or parent.conversation_id is None:
        raise InvalidState("branch_parent_not_ready")
    scope = MessageScope.model_validate({**parent.scope.model_dump(), "branch_id": branch_id})
    return BranchContext(
        scope=scope,
        state="preparing",
        origin=ForkOrigin(scope=parent.scope, conversation_id=parent.conversation_id, message_id=message_id),
    )


def bind_fork_context(branch: BranchContext, conversation_id: UUID, head_message_id: UUID) -> BranchContext:
    """Bind verified reconstruction output; caller must authenticate that output."""
    if branch.state != "preparing":
        raise InvalidState("branch_not_preparing")
    return BranchContext.model_validate(
        {
            **branch.model_dump(),
            "state": "ready",
            "revision": branch.revision + 1,
            "conversation_id": conversation_id,
            "head_message_id": head_message_id,
        }
    )


def _request_id(payload: JsonObject, key: str, *, root_allowed: bool = False) -> UUID | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid context identifier")
    parsed = UUID(value)
    if parsed.int == 0:
        if root_allowed:
            return None
        raise ValueError("Invalid conversation identifier")
    return parsed


def require_branch_context(branch: BranchContext, requested: ChatSendIntent) -> None:
    """Check exact context; repository must still lock this revision during claim."""
    if branch.scope != requested.scope:
        raise Conflict("branch_scope_mismatch")
    if branch.state != "ready":
        raise InvalidState("branch_not_ready")
    if branch.inflight_client_message_id is not None:
        raise Conflict("branch_busy")
    try:
        payload = _OBJECT.validate_json(requested.payload_json)
        conversation_id = _request_id(payload, "conversation_id")
        parent_message_id = _request_id(payload, "parent_message_id", root_allowed=True)
    except ValueError:
        raise Conflict("branch_context_invalid") from None
    if (conversation_id, parent_message_id) != (branch.conversation_id, branch.head_message_id):
        raise Conflict("branch_context_mismatch")


def reserve_branch(branch: BranchContext, requested: ChatSendIntent) -> BranchContext:
    """Reserve until verified terminal generation, not just a native ID acknowledgement."""
    require_branch_context(branch, requested)
    if requested.status != "queued":
        raise InvalidState("message_already_dispatched")
    return BranchContext.model_validate(
        {
            **branch.model_dump(),
            "revision": branch.revision + 1,
            "inflight_client_message_id": requested.client_message_id,
        }
    )


def finish_branch(branch: BranchContext, terminal: NativeGenerationTerminal) -> BranchContext:
    receipt = terminal.receipt
    if branch.scope != receipt.scope or branch.inflight_client_message_id != receipt.client_message_id:
        raise Conflict("branch_terminal_occupancy_mismatch")
    if branch.conversation_id is not None and branch.conversation_id != receipt.conversation_id:
        raise Conflict("branch_terminal_conversation_mismatch")
    return BranchContext.model_validate(
        {
            **branch.model_dump(),
            "revision": branch.revision + 1,
            "conversation_id": receipt.conversation_id,
            "head_message_id": receipt.message_id,
            "inflight_client_message_id": None,
        }
    )
