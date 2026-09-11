"""Append-only SQL draft snapshots with a parent revision fence and minimal audit."""

import hashlib
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from enterprise_platform.application.contracts import canonical_json
from enterprise_platform.application.dashboard_sql_drafts import SqlDraftRecord
from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError

from .dashboard_models import DashboardRow
from .mapping import audit, transaction, utc


class SqlDraftBase(DeclarativeBase):
    pass


class SqlDraftRow(SqlDraftBase):
    __tablename__ = "enterprise_dashboard_sql_drafts"
    __table_args__ = (
        CheckConstraint("dashboard_revision > 0", name="ck_sql_draft_dashboard_revision"),
        Index("ix_sql_draft_dashboard", "workspace_id", "dashboard_id", "created_at", "draft_id"),
    )

    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String(256), nullable=False)
    dashboard_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _decode(row: SqlDraftRow, workspace_id: str, draft_id: str) -> SqlDraftRecord:
    try:
        content = row.document_json.encode("utf-8")
        if len(content) > 512 * 1024 or hashlib.sha256(content).hexdigest() != row.document_hash:
            raise ValueError("Draft document mismatch")
        record = SqlDraftRecord.model_validate_json(content)
        if (
            (row.workspace_id, row.draft_id) != (workspace_id, draft_id)
            or (record.workspace_id, record.draft_id) != (workspace_id, draft_id)
            or (record.dashboard_id, record.dashboard_revision, record.actor_id, record.created_at)
            != (row.dashboard_id, row.dashboard_revision, row.actor_id, utc(row.created_at))
        ):
            raise ValueError("Draft context mismatch")
        return record
    except (ValueError, TypeError, RecursionError):
        raise PersistenceError("sql_draft_document_invalid") from None


class SqlAlchemySqlDraftRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(self, record: SqlDraftRecord) -> SqlDraftRecord:
        try:
            document = canonical_json(record.model_dump(mode="json"))
            if len(document.encode("utf-8")) > 512 * 1024:
                raise ValueError("Draft too large")
            record = SqlDraftRecord.model_validate_json(document)
        except (ValueError, TypeError, RecursionError):
            raise InvalidInput("sql_draft_document_invalid") from None
        row = SqlDraftRow(
            workspace_id=record.workspace_id,
            draft_id=record.draft_id,
            dashboard_id=record.dashboard_id,
            dashboard_revision=record.dashboard_revision,
            actor_id=record.actor_id,
            document_json=document,
            document_hash=hashlib.sha256(document.encode("utf-8")).hexdigest(),
            created_at=utc(record.created_at),
        )
        with transaction(self._sessions) as session:
            parent = session.scalar(
                select(DashboardRow)
                .where(
                    DashboardRow.workspace_id == record.workspace_id,
                    DashboardRow.dashboard_id == record.dashboard_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if parent is None:
                raise NotFound("dashboard_not_found")
            if (parent.workspace_id, parent.dashboard_id, parent.revision, parent.design_identity) != (
                record.workspace_id,
                record.dashboard_id,
                record.dashboard_revision,
                record.design_identity,
            ):
                raise Conflict("dashboard_revision_conflict")
            session.add(row)
            session.flush()
            audit(
                session,
                record.workspace_id,
                "dashboard_sql_draft",
                record.draft_id,
                record.actor_id,
                "dashboard.sql_draft_created",
                record.created_at,
                {"dashboard_id": record.dashboard_id, "dashboard_revision": record.dashboard_revision},
            )
        return _decode(row, record.workspace_id, record.draft_id)

    def get(self, workspace_id: str, draft_id: str) -> SqlDraftRecord:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(SqlDraftRow).where(SqlDraftRow.workspace_id == workspace_id, SqlDraftRow.draft_id == draft_id)
            )
            if row is None:
                raise NotFound("sql_draft_not_found")
            return _decode(row, workspace_id, draft_id)
