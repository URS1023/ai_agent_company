"""Authorized claim preparation, not a native transport or public receipt endpoint."""

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from .contracts import JsonObject, Principal
from .errors import AccessDenied, InvalidInput, InvalidState, PersistenceError
from .workbench_branches import BranchContext
from .workbench_messages import (
    ChatSendIntent,
    MessageScope,
    claim_message,
    create_message_intent,
    ensure_same_send,
)
from .workflow_setup_execution import NativeSetupSession


class _NativeChatRequest(BaseModel):
    """Installed-chat fields, not debugger fields; file ownership is checked separately.

    Validate without rewriting the original snapshot or filling omitted defaults.
    Native ChatMessageExplorePayload remains authoritative for actual generation.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    inputs: JsonObject
    query: str
    files: list[JsonObject] | None = None
    conversation_id: str | None = None
    parent_message_id: str | None = None
    retriever_from: str = "explore_app"

    @field_validator("conversation_id", "parent_message_id")
    @classmethod
    def validate_native_id(cls, value: str | None) -> str | None:
        if value:
            UUID(value)
        return value


class MessageIntentRepository(Protocol):
    def create_or_get(self, requested: ChatSendIntent) -> ChatSendIntent: ...

    def claim(self, scope: MessageScope, client_message_id: UUID, *, expected_revision: int) -> ChatSendIntent: ...


class MessageSendAuthority(Protocol):
    async def require_send(self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent) -> None:
        """Complete branch and file-valued input authority; raise on denial.

        Implementations must use the authenticated user's native access, not an
        administrator/service credential or a business-management role shortcut.
        """
        ...


class NativeContextChecker(Protocol):
    async def check_context(self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent) -> None:
        """Check current native identity/app/history; not a full send permit."""
        ...

    async def check_selected_attachments(
        self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent
    ) -> None:
        """Check selected files with effective native config, not file-valued inputs."""
        ...


class BranchReader(Protocol):
    def get(self, scope: MessageScope) -> BranchContext: ...


class NativeInputChecker(Protocol):
    async def check_inputs(self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent) -> None:
        """Validate native variables and file-valued inputs using effective app configuration."""
        ...


class WorkbenchSendAuthority:
    """Read-only branch ownership/readiness plus mandatory native input checks.

    Native context and selected attachments are checked by WorkbenchSendService.
    Busy branches are not rejected here: an identical previously dispatched intent
    must still be reconcilable. The repository's locked claim validates the exact
    current head and occupancy for every new send, not this potentially stale read.
    """

    def __init__(self, branches: BranchReader, inputs: NativeInputChecker) -> None:
        self._branches = branches
        self._inputs = inputs

    async def require_send(self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent) -> None:
        try:
            requested = ChatSendIntent.model_validate_json(requested.model_dump_json())
        except (ValueError, TypeError, RecursionError):
            raise InvalidInput("message_request_invalid") from None
        if (requested.scope.workspace_id, requested.scope.actor_id) != (principal.workspace_id, principal.actor_id):
            raise AccessDenied()
        branch = await asyncio.to_thread(self._branches.get, requested.scope)
        try:
            branch = BranchContext.model_validate_json(branch.model_dump_json())
        except (ValueError, TypeError, RecursionError):
            raise PersistenceError("branch_context_storage_invalid") from None
        if branch.scope != requested.scope:
            raise AccessDenied()
        if branch.state != "ready":
            raise InvalidState("branch_not_ready")
        await self._inputs.check_inputs(principal, session, requested)


@dataclass(frozen=True)
class DispatchPreparation:
    intent: ChatSendIntent
    dispatch_claimed: bool


class WorkbenchSendService:
    def __init__(
        self,
        repository: MessageIntentRepository,
        authority: MessageSendAuthority,
        context_checker: NativeContextChecker,
    ) -> None:
        self._repository = repository
        self._authority = authority
        self._context_checker = context_checker

    async def prepare(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        installed_app_id: UUID,
        branch_id: str,
        client_message_id: UUID,
        payload: JsonObject,
    ) -> DispatchPreparation:
        """Await native context/files and remaining authority before any durable claim.

        Cancellation during a database worker does not stop its transaction. The
        caller receives no send permission and must reconcile the durable ledger,
        not infer that cancellation rolled back the claim or authorize another POST.
        """
        scope = MessageScope(
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            installed_app_id=installed_app_id,
            branch_id=branch_id,
        )
        try:
            requested = create_message_intent(scope, client_message_id, payload)
            _NativeChatRequest.model_validate_json(requested.payload_json)
        except (ValueError, TypeError, RecursionError):
            raise InvalidInput("message_request_invalid") from None
        await self._context_checker.check_context(principal, session, requested)
        await self._context_checker.check_selected_attachments(principal, session, requested)
        await self._authority.require_send(principal, session, requested)
        current = ensure_same_send(await asyncio.to_thread(self._repository.create_or_get, requested), requested)
        if current.status != "queued":
            return DispatchPreparation(current, dispatch_claimed=False)
        expected = claim_message(current)
        claimed = await asyncio.to_thread(
            self._repository.claim, scope, client_message_id, expected_revision=current.revision
        )
        if claimed != expected:
            raise PersistenceError("message_claim_result_invalid")
        return DispatchPreparation(claimed, dispatch_claimed=True)
