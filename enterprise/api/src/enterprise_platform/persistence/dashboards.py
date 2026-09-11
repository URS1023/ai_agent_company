"""Workspace-scoped dashboard storage with audited whole-document CAS updates."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.dashboard_binding_update import replace_dashboard_bindings
from enterprise_platform.application.dashboard_refresh_service import DashboardRecord
from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.domain import dashboard as d

from .dashboard_documents import decode_dashboard, encode_dashboard
from .dashboard_models import DashboardRow
from .mapping import audit, cas, transaction, utc_now, validate_revision


def _decode(row: DashboardRow) -> DashboardRecord:
    record = decode_dashboard(row.document_json)
    if (
        (record.workspace_id, record.dashboard_id, record.revision)
        != (row.workspace_id, row.dashboard_id, row.revision)
        or record.design.identity != row.design_identity
        or d.binding_set_identity(record.bindings) != row.bindings_hash
    ):
        raise PersistenceError("dashboard_document_mismatch")
    return record


def _row(session: Session, workspace_id: str, dashboard_id: str, *, lock: bool = False) -> DashboardRow:
    statement = select(DashboardRow).where(
        DashboardRow.workspace_id == workspace_id,
        DashboardRow.dashboard_id == dashboard_id,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(statement)
    if row is None:
        raise NotFound("dashboard_not_found")
    return row


class SqlAlchemyDashboardRepository:
    def __init__(self, sessions: sessionmaker[Session], *, clock: Callable[[], datetime] = utc_now) -> None:
        self._sessions, self._clock = sessions, clock

    def create(self, record: DashboardRecord, *, actor_id: str) -> DashboardRecord:
        if record.revision != 1 or record.state != d.RefreshState(record.design.identity):
            raise InvalidInput("dashboard_requires_initial_state")
        document = encode_dashboard(record)
        now = self._clock()
        with transaction(self._sessions) as session:
            session.add(
                DashboardRow(
                    workspace_id=record.workspace_id,
                    dashboard_id=record.dashboard_id,
                    revision=1,
                    design_identity=record.design.identity,
                    bindings_hash=d.binding_set_identity(record.bindings),
                    document_json=document,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            audit(
                session,
                record.workspace_id,
                "dashboard",
                record.dashboard_id,
                actor_id,
                "dashboard.created",
                now,
                {"revision": 1},
            )
        return decode_dashboard(document)

    def get(self, workspace_id: str, dashboard_id: str) -> DashboardRecord:
        with transaction(self._sessions) as session:
            return _decode(_row(session, workspace_id, dashboard_id))

    def save_bindings(
        self,
        workspace_id: str,
        dashboard_id: str,
        *,
        expected_revision: int,
        expected_design_identity: str,
        bindings: tuple[d.ExecutionBinding, ...],
        actor_id: str,
    ) -> DashboardRecord:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            old = _decode(_row(session, workspace_id, dashboard_id, lock=True))
            if (old.revision, old.design.identity) != (expected_revision, expected_design_identity):
                raise Conflict("dashboard_revision_conflict")
            updated = replace_dashboard_bindings(old, bindings)
            if updated == old:
                return old
            document = encode_dashboard(updated)
            now = self._clock()
            cas(
                session,
                update(DashboardRow)
                .where(
                    DashboardRow.workspace_id == workspace_id,
                    DashboardRow.dashboard_id == dashboard_id,
                    DashboardRow.revision == expected_revision,
                    DashboardRow.design_identity == expected_design_identity,
                )
                .values(
                    revision=updated.revision,
                    bindings_hash=d.binding_set_identity(bindings),
                    document_json=document,
                    updated_at=now,
                ),
            )
            audit(
                session,
                workspace_id,
                "dashboard",
                dashboard_id,
                actor_id,
                "dashboard.bindings_updated",
                now,
                {"revision": updated.revision, "binding_count": len(bindings)},
            )
        return decode_dashboard(document)

    def list(self, workspace_id: str, *, after: str | None, limit: int) -> tuple[DashboardRecord, ...]:
        """Bounded keyset traversal; concurrent inserts are visible on subsequent traversals."""
        if type(limit) is not int or not 1 <= limit <= 101:
            raise InvalidInput("invalid_dashboard_page")
        statement = select(DashboardRow).where(DashboardRow.workspace_id == workspace_id)
        if after is not None:
            statement = statement.where(DashboardRow.dashboard_id > after)
        statement = statement.order_by(DashboardRow.dashboard_id).limit(limit)
        with transaction(self._sessions) as session:
            return tuple(_decode(row) for row in session.scalars(statement))

    def commit(
        self,
        *,
        workspace_id: str,
        dashboard_id: str,
        expected_revision: int,
        expected_design_identity: str,
        expected_bindings_hash: str,
        state: d.RefreshState,
        actor_id: str,
    ) -> d.RefreshState:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            row = _row(session, workspace_id, dashboard_id, lock=True)
            old = _decode(row)
            if (row.revision, row.design_identity, row.bindings_hash) != (
                expected_revision,
                expected_design_identity,
                expected_bindings_hash,
            ):
                raise Conflict("dashboard_revision_conflict")
            if (
                state.design_identity != expected_design_identity
                or not state.last_attempt_id
                or state.last_attempt_id == old.state.last_attempt_id
                or state.status == "empty"
                or state.status == "failed"
                and state.current != old.state.current
                or state.status == "ready"
                and (
                    state.current is None
                    or state.current.batch_id != state.last_attempt_id
                    or state.current.bindings_hash != expected_bindings_hash
                )
            ):
                raise InvalidInput("invalid_dashboard_refresh_receipt")
            document = encode_dashboard(replace(old, revision=old.revision + 1, state=state))
            now = self._clock()
            cas(
                session,
                update(DashboardRow)
                .where(
                    DashboardRow.workspace_id == workspace_id,
                    DashboardRow.dashboard_id == dashboard_id,
                    DashboardRow.revision == expected_revision,
                    DashboardRow.design_identity == expected_design_identity,
                    DashboardRow.bindings_hash == expected_bindings_hash,
                )
                .values(revision=expected_revision + 1, document_json=document, updated_at=now),
            )
            audit(
                session,
                workspace_id,
                "dashboard",
                dashboard_id,
                actor_id,
                "dashboard.refreshed",
                now,
                {
                    "revision": expected_revision + 1,
                    "status": state.status,
                    "attempt_id": state.last_attempt_id,
                },
            )
        return decode_dashboard(document).state
