"""Transactional PostgreSQL send ledger; no native calls occur inside this repository.

Dispatch callers must wait for claim() to return after commit. A failed/uncertain
commit is not permission to POST. Authorization belongs to the enclosing service.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.workbench_branches import finish_branch, reserve_branch
from enterprise_platform.application.workbench_messages import (
    ChatSendIntent,
    MessageScope,
    NativeGenerationTerminal,
    NativeMessageReceipt,
    acknowledge_message,
    claim_message,
    ensure_same_send,
    finish_message,
    mark_send_uncertain,
)

from .mapping import audit, transaction, utc_now, validate_revision
from .workbench_branch_repository import branch_query
from .workbench_branches import as_branch_context, branch_context_row
from .workbench_messages import MessageIntentRow, as_message_intent, message_intent_row


def _query(scope: MessageScope, client_message_id: UUID) -> Select[tuple[MessageIntentRow]]:
    return (
        select(MessageIntentRow)
        .where(
            MessageIntentRow.workspace_id == scope.workspace_id,
            MessageIntentRow.actor_id == scope.actor_id,
            MessageIntentRow.installed_app_id == str(scope.installed_app_id),
            MessageIntentRow.branch_id == scope.branch_id,
            MessageIntentRow.client_message_id == str(client_message_id),
        )
        .execution_options(populate_existing=True)
    )


def _audit(session: Session, intent: ChatSendIntent, *, event: str | None = None) -> None:
    audit(
        session,
        intent.scope.workspace_id,
        "chat_send",
        str(intent.client_message_id),
        intent.scope.actor_id,
        event or "chat_send_" + intent.status,
        utc_now(),
        {
            "revision": intent.revision,
            "state": intent.status,
            "installed_app_id": str(intent.scope.installed_app_id),
            "branch_id": intent.scope.branch_id,
        },
    )


class SqlAlchemyMessageIntentRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, scope: MessageScope, client_message_id: UUID) -> ChatSendIntent:
        with transaction(self._sessions) as session:
            row = session.scalar(_query(scope, client_message_id))
            if row is None:
                raise NotFound("message_intent_not_found")
            result = as_message_intent(row, scope, client_message_id)
        return result

    def create_or_get(self, requested: ChatSendIntent) -> ChatSendIntent:
        requested = ChatSendIntent.model_validate_json(requested.model_dump_json())
        if requested.status != "queued" or requested.revision != 1:
            raise Conflict("message_initial_state_required")
        candidate = message_intent_row(requested)
        with transaction(self._sessions) as session:
            inserted = session.scalar(
                insert(MessageIntentRow)
                .values(
                    workspace_id=candidate.workspace_id,
                    actor_id=candidate.actor_id,
                    installed_app_id=candidate.installed_app_id,
                    branch_id=candidate.branch_id,
                    client_message_id=candidate.client_message_id,
                    revision=candidate.revision,
                    status=candidate.status,
                    payload_hash=candidate.payload_hash,
                    document_json=candidate.document_json,
                    document_hash=candidate.document_hash,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        MessageIntentRow.workspace_id,
                        MessageIntentRow.actor_id,
                        MessageIntentRow.installed_app_id,
                        MessageIntentRow.branch_id,
                        MessageIntentRow.client_message_id,
                    ]
                )
                .returning(MessageIntentRow.client_message_id)
            )
            if inserted is not None and inserted != candidate.client_message_id:
                raise PersistenceError("message_insert_identity_invalid")
            row = session.scalar(_query(requested.scope, requested.client_message_id).with_for_update())
            if row is None:
                raise PersistenceError("message_insert_not_visible")
            result = ensure_same_send(as_message_intent(row, requested.scope, requested.client_message_id), requested)
            if inserted is not None:
                _audit(session, result)
        return result

    def claim(self, scope: MessageScope, client_message_id: UUID, *, expected_revision: int) -> ChatSendIntent:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            branch_row = session.scalar(branch_query(scope).with_for_update())
            if branch_row is None:
                raise NotFound("branch_not_found")
            branch = as_branch_context(branch_row, scope)
            row = session.scalar(_query(scope, client_message_id).with_for_update())
            if row is None:
                raise NotFound("message_intent_not_found")
            current = as_message_intent(row, scope, client_message_id)
            if current.revision != expected_revision:
                raise Conflict("message_revision_conflict")
            result = claim_message(current)
            reserved = branch_context_row(reserve_branch(branch, current))
            updated = message_intent_row(result)
            branch_row.revision = reserved.revision
            branch_row.inflight_client_message_id = reserved.inflight_client_message_id
            branch_row.document_json, branch_row.document_hash = reserved.document_json, reserved.document_hash
            row.revision, row.status = updated.revision, updated.status
            row.document_json, row.document_hash = updated.document_json, updated.document_hash
            _audit(session, result)
            session.flush()
        return result

    def mark_uncertain(self, scope: MessageScope, client_message_id: UUID, *, expected_revision: int) -> ChatSendIntent:
        return self._change(scope, client_message_id, expected_revision, mark_send_uncertain)

    def acknowledge(self, receipt: NativeMessageReceipt, *, expected_revision: int) -> ChatSendIntent:
        return self._change(
            receipt.scope,
            receipt.client_message_id,
            expected_revision,
            lambda current: acknowledge_message(current, receipt),
        )

    def finish_generation(self, terminal: NativeGenerationTerminal, *, expected_revision: int) -> ChatSendIntent:
        """Apply verified terminal evidence; caller is responsible for native provenance."""
        validate_revision(expected_revision)
        terminal = NativeGenerationTerminal.model_validate_json(terminal.model_dump_json())
        receipt = terminal.receipt
        with transaction(self._sessions) as session:
            branch_row = session.scalar(branch_query(receipt.scope).with_for_update())
            if branch_row is None:
                raise NotFound("branch_not_found")
            branch = as_branch_context(branch_row, receipt.scope)
            row = session.scalar(_query(receipt.scope, receipt.client_message_id).with_for_update())
            if row is None:
                raise NotFound("message_intent_not_found")
            current = as_message_intent(row, receipt.scope, receipt.client_message_id)
            result = finish_message(current, terminal)
            if result != current:
                if current.revision != expected_revision:
                    raise Conflict("message_revision_conflict")
                finished = branch_context_row(finish_branch(branch, terminal))
                updated = message_intent_row(result)
                branch_row.revision = finished.revision
                branch_row.conversation_id = finished.conversation_id
                branch_row.head_message_id = finished.head_message_id
                branch_row.inflight_client_message_id = finished.inflight_client_message_id
                branch_row.document_json, branch_row.document_hash = finished.document_json, finished.document_hash
                row.revision = updated.revision
                row.document_json, row.document_hash = updated.document_json, updated.document_hash
                _audit(session, result, event="chat_send_generation_finished")
                session.flush()
        return result

    def _change(
        self,
        scope: MessageScope,
        client_message_id: UUID,
        expected_revision: int,
        transition: Callable[[ChatSendIntent], ChatSendIntent],
    ) -> ChatSendIntent:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            row = session.scalar(_query(scope, client_message_id).with_for_update())
            if row is None:
                raise NotFound("message_intent_not_found")
            current = as_message_intent(row, scope, client_message_id)
            if current.revision != expected_revision:
                raise Conflict("message_revision_conflict")
            result = transition(current)
            if result != current:
                updated = message_intent_row(result)
                row.revision = updated.revision
                row.status = updated.status
                row.document_json = updated.document_json
                row.document_hash = updated.document_hash
                _audit(session, result)
                session.flush()
        return result
