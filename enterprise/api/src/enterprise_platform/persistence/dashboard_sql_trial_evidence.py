"""Append-only trial documents; caller authorization remains an application concern."""

import hashlib
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, and_, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from enterprise_platform.application.contracts import canonical_json
from enterprise_platform.application.dashboard_sql_trial_evidence import (
    SqlTrialEvidence,
    SqlTrialSummary,
    validate_trial_evidence,
)
from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError

from .dashboard_models import DashboardRow
from .dashboard_sql_drafts import SqlDraftRow
from .dashboard_sql_drafts import _decode as decode_draft
from .mapping import audit, transaction, utc


class SqlTrialEvidenceBase(DeclarativeBase):
    pass


class SqlTrialEvidenceRow(SqlTrialEvidenceBase):
    __tablename__ = "enterprise_dashboard_sql_trials"
    __table_args__ = (
        Index("ix_sql_trial_draft", "workspace_id", "dashboard_id", "draft_id", "recorded_at", "trial_id"),
    )

    workspace_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    trial_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String(256), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _decode(row: SqlTrialEvidenceRow, workspace_id: str, trial_id: str) -> SqlTrialEvidence:
    try:
        content = row.document_json.encode("utf-8")
        if len(content) > 600 * 1024 or hashlib.sha256(content).hexdigest() != row.document_hash:
            raise ValueError("Evidence document mismatch")
        item = SqlTrialEvidence.model_validate_json(content)
        if (
            (row.workspace_id, row.trial_id) != (workspace_id, trial_id)
            or (item.result.workspace_id, item.trial_id) != (workspace_id, trial_id)
            or (item.result.dashboard_id, item.result.draft_id, item.actor_id, item.recorded_at)
            != (row.dashboard_id, row.draft_id, row.actor_id, utc(row.recorded_at))
        ):
            raise ValueError("Evidence context mismatch")
        return item
    except (ValueError, TypeError, RecursionError):
        raise PersistenceError("sql_trial_evidence_invalid") from None


class SqlAlchemySqlTrialEvidenceRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(self, evidence: SqlTrialEvidence) -> SqlTrialEvidence:
        try:
            document = canonical_json(evidence.model_dump(mode="json"))
            if len(document.encode("utf-8")) > 600 * 1024:
                raise ValueError("Evidence too large")
            evidence = SqlTrialEvidence.model_validate_json(document)
        except (ValueError, TypeError, RecursionError):
            raise InvalidInput("sql_trial_evidence_invalid") from None
        result = evidence.result
        row = SqlTrialEvidenceRow(
            workspace_id=result.workspace_id,
            trial_id=evidence.trial_id,
            dashboard_id=result.dashboard_id,
            draft_id=result.draft_id,
            actor_id=evidence.actor_id,
            recorded_at=utc(evidence.recorded_at),
            document_json=document,
            document_hash=hashlib.sha256(document.encode("utf-8")).hexdigest(),
        )
        with transaction(self._sessions) as session:
            parent = session.scalar(
                select(DashboardRow)
                .where(
                    DashboardRow.workspace_id == result.workspace_id,
                    DashboardRow.dashboard_id == result.dashboard_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if parent is None:
                raise NotFound("dashboard_not_found")
            if (parent.workspace_id, parent.dashboard_id, parent.revision, parent.design_identity) != (
                result.workspace_id,
                result.dashboard_id,
                result.dashboard_revision,
                result.design_identity,
            ):
                raise Conflict("dashboard_revision_conflict")
            stored_draft = session.scalar(
                select(SqlDraftRow).where(
                    SqlDraftRow.workspace_id == result.workspace_id,
                    SqlDraftRow.draft_id == result.draft_id,
                )
            )
            if stored_draft is None:
                raise NotFound("sql_draft_not_found")
            validate_trial_evidence(evidence, decode_draft(stored_draft, result.workspace_id, result.draft_id))
            session.add(row)
            session.flush()
            audit(
                session,
                result.workspace_id,
                "dashboard_sql_trial",
                evidence.trial_id,
                evidence.actor_id,
                "dashboard.sql_trial_recorded",
                evidence.recorded_at,
                {
                    "dashboard_id": result.dashboard_id,
                    "draft_id": result.draft_id,
                    "slot_id": result.slot_id,
                    "dashboard_revision": result.dashboard_revision,
                },
            )
        return _decode(row, result.workspace_id, evidence.trial_id)

    def get(self, workspace_id: str, trial_id: str) -> SqlTrialEvidence:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(SqlTrialEvidenceRow).where(
                    SqlTrialEvidenceRow.workspace_id == workspace_id,
                    SqlTrialEvidenceRow.trial_id == trial_id,
                )
            )
            if row is None:
                raise NotFound("sql_trial_not_found")
            return _decode(row, workspace_id, trial_id)

    def list(
        self, workspace_id: str, dashboard_id: str, draft_id: str, *, after: str | None = None, limit: int = 20
    ) -> tuple[SqlTrialSummary, ...]:
        if type(limit) is not int or not 1 <= limit <= 101:
            raise InvalidInput("sql_trial_page_limit")
        row = SqlTrialEvidenceRow
        scope = (row.workspace_id == workspace_id, row.dashboard_id == dashboard_id, row.draft_id == draft_id)
        statement = select(
            row.workspace_id, row.dashboard_id, row.draft_id, row.trial_id, row.actor_id, row.recorded_at
        ).where(*scope)
        with transaction(self._sessions) as session:
            if after is not None:
                recorded_at = session.scalar(select(row.recorded_at).where(*scope, row.trial_id == after))
                if recorded_at is None:
                    raise NotFound("sql_trial_cursor_not_found")
                statement = statement.where(
                    or_(row.recorded_at < recorded_at, and_(row.recorded_at == recorded_at, row.trial_id < after))
                )
            records = (
                session.execute(statement.order_by(row.recorded_at.desc(), row.trial_id.desc()).limit(limit))
                .mappings()
                .all()
            )
            items: list[SqlTrialSummary] = []
            try:
                for record in records:
                    item = SqlTrialSummary.model_validate({**record, "recorded_at": utc(record["recorded_at"])})
                    if (item.workspace_id, item.dashboard_id, item.draft_id) != (workspace_id, dashboard_id, draft_id):
                        raise ValueError("Trial summary scope mismatch")
                    items.append(item)
            except (ValueError, TypeError):
                raise PersistenceError("sql_trial_summary_invalid") from None
            return tuple(items)
