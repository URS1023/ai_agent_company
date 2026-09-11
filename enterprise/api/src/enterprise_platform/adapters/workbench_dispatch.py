"""Compose authorized claim, native stream and durable receipts without generation retries.

This is an internal gateway adapter, not a public endpoint accepting receipts/claims.
Workflow completion requires clean stream exhaustion and matching native persisted state.
Unmarked plain-chat, paused and interrupted paths retain occupancy for separate reconciliation.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Protocol
from uuid import UUID

from anyio import CancelScope

from enterprise_platform.application.contracts import JsonObject, Principal
from enterprise_platform.application.errors import Conflict, InvalidInput, PersistenceError
from enterprise_platform.application.workbench_messages import (
    ChatSendIntent,
    MessageScope,
    NativeGenerationTerminal,
    NativeMessageReceipt,
    acknowledge_message,
    ensure_same_send,
    finish_message,
)
from enterprise_platform.application.workbench_service import WorkbenchSendService
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .workbench_completion import ChatStreamCompletion, NativeGenerationReader
from .workbench_context_client import DifyWorkbenchContextClient
from .workbench_stream_identity import ChatStreamIdentity


class DispatchLedger(Protocol):
    def get(self, scope: MessageScope, client_message_id: UUID) -> ChatSendIntent: ...

    def acknowledge(self, receipt: NativeMessageReceipt, *, expected_revision: int) -> ChatSendIntent: ...

    def finish_generation(self, terminal: NativeGenerationTerminal, *, expected_revision: int) -> ChatSendIntent: ...

    def mark_uncertain(
        self, scope: MessageScope, client_message_id: UUID, *, expected_revision: int
    ) -> ChatSendIntent: ...


async def _settled_io[T](operation: Callable[[], T]) -> T:
    # A cancelled await does not stop a database thread. Settle it before reconciliation.
    pending = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            with CancelScope(shield=True):
                await pending
        finally:
            raise


@dataclass
class ChatDispatch:
    intent: ChatSendIntent
    events: AsyncIterator[JsonObject]


async def _empty_events() -> AsyncGenerator[JsonObject, None]:
    empty: tuple[JsonObject, ...] = ()
    for item in empty:
        yield item


class WorkbenchChatDispatcher:
    def __init__(
        self,
        preparer: WorkbenchSendService,
        ledger: DispatchLedger,
        native: DifyWorkbenchContextClient,
        *,
        completion_reader: NativeGenerationReader | None = None,
    ) -> None:
        self._preparer = preparer
        self._ledger = ledger
        self._native = native
        self._completion_reader = completion_reader

    async def read_message(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        installed_app_id: UUID,
        branch_id: str,
        client_message_id: UUID,
    ) -> ChatSendIntent:
        """Read the caller's ledger after current native app authorization, without mutation.

        This is a durable observation, not abandoned-stream reconciliation: it does not
        dispatch, refresh native terminal evidence, or release branch occupancy.
        """
        try:
            scope = MessageScope(
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                installed_app_id=installed_app_id,
                branch_id=branch_id,
            )
            if installed_app_id.int == 0 or client_message_id.int == 0:
                raise ValueError("Nonzero identity required")
        except (ValueError, TypeError):
            raise InvalidInput("message_identity_invalid") from None
        await self._native.check_branch_access(principal, session, scope)
        stored = await asyncio.to_thread(self._ledger.get, scope, client_message_id)
        try:
            stored = ChatSendIntent.model_validate_json(stored.model_dump_json())
            if stored.scope != scope or stored.client_message_id != client_message_id:
                raise ValueError("Message identity mismatch")
        except (ValueError, TypeError, RecursionError):
            raise PersistenceError("message_dispatch_storage_invalid") from None
        return stored

    async def _read(self, claimed: ChatSendIntent) -> ChatSendIntent:
        stored = await _settled_io(partial(self._ledger.get, claimed.scope, claimed.client_message_id))
        try:
            stored = ChatSendIntent.model_validate_json(stored.model_dump_json())
            return ensure_same_send(stored, claimed)
        except (ValueError, TypeError, Conflict):
            raise PersistenceError("message_dispatch_storage_invalid") from None

    async def _settle(self, claimed: ChatSendIntent) -> ChatSendIntent:
        current = await self._read(claimed)
        if current.status != "dispatched":
            return current
        try:
            await _settled_io(
                partial(
                    self._ledger.mark_uncertain,
                    current.scope,
                    current.client_message_id,
                    expected_revision=current.revision,
                )
            )
        except Conflict:
            # A trusted reconciler can acknowledge concurrently; never overwrite it.
            current = await self._read(claimed)
            if current.status not in {"accepted", "uncertain"}:
                raise
            return current
        return await self._read(claimed)

    @asynccontextmanager
    async def open(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        installed_app_id: UUID,
        branch_id: str,
        client_message_id: UUID,
        payload: JsonObject,
    ) -> AsyncIterator[ChatDispatch]:
        prepared = await self._preparer.prepare(
            principal,
            session,
            installed_app_id=installed_app_id,
            branch_id=branch_id,
            client_message_id=client_message_id,
            payload=payload,
        )
        stream = ChatDispatch(prepared.intent, _empty_events())
        if not prepared.dispatch_claimed:
            yield stream
            return
        claimed = prepared.intent
        try:
            identity = ChatStreamIdentity(claimed)
            async with self._native.open_chat(principal, session, claimed) as native_events:

                async def persist_events() -> AsyncGenerator[JsonObject, None]:
                    completion: ChatStreamCompletion | None = None
                    async for event in native_events:
                        receipt = identity.observe(event)
                        if receipt is not None and stream.intent.receipt is None:
                            expected = acknowledge_message(stream.intent, receipt)
                            stored = await _settled_io(
                                partial(
                                    self._ledger.acknowledge,
                                    receipt,
                                    expected_revision=stream.intent.revision,
                                )
                            )
                            if stored != expected:
                                raise PersistenceError("message_acknowledgement_result_invalid")
                            stream.intent = stored
                        if receipt is not None and self._completion_reader is not None:
                            if completion is None:
                                completion = ChatStreamCompletion(receipt)
                            completion.observe(event)
                        yield event
                    # Reached only after clean EOF, never aclose/cancellation/parser failure.
                    if completion is not None and self._completion_reader is not None:
                        terminal = await completion.reconcile(principal, session, self._completion_reader)
                        if terminal is not None:
                            expected = finish_message(stream.intent, terminal)
                            stored = await _settled_io(
                                partial(
                                    self._ledger.finish_generation,
                                    terminal,
                                    expected_revision=stream.intent.revision,
                                )
                            )
                            if stored != expected:
                                raise PersistenceError("message_completion_result_invalid")
                            stream.intent = stored

                events = persist_events()
                stream.events = events
                try:
                    yield stream
                finally:
                    await events.aclose()
        finally:
            # ASGI disconnects cancel an AnyIO scope repeatedly at each await.
            # Shield only reconciliation, never generation or the consumer stream.
            with CancelScope(shield=True):
                stream.intent = await self._settle(claimed)
