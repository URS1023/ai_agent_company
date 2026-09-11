"""Private branch storage and canonical mapping; no connections or migrations.

Bound native conversations are unique within a workspace/installed app, including
archived branches. PostgreSQL permits multiple NULL bindings for unbound branches.
The repository must serialize branch-head changes with message claims; this table
alone does not coordinate dispatch, reconstruct history or prove resource access.
"""

from hashlib import sha256

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from enterprise_platform.application.contracts import canonical_json
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workbench_branches import BranchContext
from enterprise_platform.application.workbench_messages import MessageScope


class BranchContextBase(DeclarativeBase):
    pass


class BranchContextRow(BranchContextBase):
    __tablename__ = "enterprise_chat_branches"
    __table_args__ = (
        UniqueConstraint("workspace_id", "installed_app_id", "conversation_id", name="uq_chat_branch_conversation"),
        CheckConstraint("revision > 0", name="ck_chat_branch_revision"),
        CheckConstraint("state IN ('preparing', 'ready', 'archived')", name="ck_chat_branch_state"),
        CheckConstraint("head_message_id IS NULL OR conversation_id IS NOT NULL", name="ck_chat_branch_head"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    installed_app_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    branch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    head_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    inflight_client_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)


def branch_context_row(branch: BranchContext) -> BranchContextRow:
    document = canonical_json(branch.model_dump(mode="json"))
    return BranchContextRow(
        workspace_id=branch.scope.workspace_id,
        actor_id=branch.scope.actor_id,
        installed_app_id=str(branch.scope.installed_app_id),
        branch_id=branch.scope.branch_id,
        revision=branch.revision,
        state=branch.state,
        conversation_id=str(branch.conversation_id) if branch.conversation_id is not None else None,
        head_message_id=str(branch.head_message_id) if branch.head_message_id is not None else None,
        inflight_client_message_id=(
            str(branch.inflight_client_message_id) if branch.inflight_client_message_id is not None else None
        ),
        document_json=document,
        document_hash=sha256(document.encode("utf-8")).hexdigest(),
    )


def as_branch_context(row: BranchContextRow, scope: MessageScope) -> BranchContext:
    try:
        content = row.document_json.encode("utf-8")
        if len(content) > 65536 or sha256(content).hexdigest() != row.document_hash:
            raise ValueError("Branch document hash/size mismatch")
        branch = BranchContext.model_validate_json(content)
        if branch.scope != scope:
            raise ValueError("Branch scope mismatch")
        expected = branch_context_row(branch)
        for column in BranchContextRow.__table__.columns:
            if getattr(row, column.key) != getattr(expected, column.key):
                raise ValueError("Branch index/document mismatch")
        return branch
    except (ValueError, TypeError, RecursionError):
        raise PersistenceError("branch_context_storage_invalid") from None
