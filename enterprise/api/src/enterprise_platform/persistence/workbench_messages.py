"""Message ledger schema and validated mappings, without connections or migrations.

The composite primary key is the retry scope. A repository must lock/compare revisions
and commit dispatch claims before network I/O; constructing a row does not claim a send.
"""

from hashlib import sha256
from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from enterprise_platform.application.contracts import canonical_json
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workbench_messages import ChatSendIntent, MessageScope


class MessageIntentBase(DeclarativeBase):
    pass


class MessageIntentRow(MessageIntentBase):
    __tablename__ = "enterprise_chat_send_intents"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_chat_send_revision"),
        CheckConstraint("status IN ('queued', 'dispatched', 'uncertain', 'accepted')", name="ck_chat_send_status"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    installed_app_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    branch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)


def message_intent_row(intent: ChatSendIntent) -> MessageIntentRow:
    document = canonical_json(intent.model_dump(mode="json"))
    return MessageIntentRow(
        workspace_id=intent.scope.workspace_id,
        actor_id=intent.scope.actor_id,
        installed_app_id=str(intent.scope.installed_app_id),
        branch_id=intent.scope.branch_id,
        client_message_id=str(intent.client_message_id),
        revision=intent.revision,
        status=intent.status,
        payload_hash=intent.payload_hash,
        document_json=document,
        document_hash=sha256(document.encode("utf-8")).hexdigest(),
    )


def as_message_intent(row: MessageIntentRow, scope: MessageScope, client_message_id: UUID) -> ChatSendIntent:
    try:
        content = row.document_json.encode("utf-8")
        if len(content) > 1024 * 1024 or sha256(content).hexdigest() != row.document_hash:
            raise ValueError("Message document hash/size mismatch")
        intent = ChatSendIntent.model_validate_json(content)
        if intent.scope != scope or intent.client_message_id != client_message_id:
            raise ValueError("Message scope mismatch")
        expected = message_intent_row(intent)
        for column in MessageIntentRow.__table__.columns:
            if getattr(row, column.key) != getattr(expected, column.key):
                raise ValueError("Message index/document mismatch")
        return intent
    except (ValueError, TypeError, RecursionError):
        raise PersistenceError("message_intent_storage_invalid") from None
