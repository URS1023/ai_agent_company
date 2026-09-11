"""Atomic audited draft imports; owned outcomes survive device soft deletion.

Creation and claim require live devices. Finalization only records an existing
import outcome, never starts another native operation or creates a binding.
"""

from dataclasses import replace

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Page, Scenario
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.workflow_setup_contracts import SetupFinalState, SetupView
from enterprise_platform.application.workflow_setup_ports import StoredSetup

from .mapping import (
    audit,
    cas,
    decode,
    lock_device,
    serialized,
    transaction,
    utc,
    utc_now,
    validate_page,
    validate_revision,
)
from .workflow_setup_models import WorkflowSetupRow


def setup_row(setup: StoredSetup) -> WorkflowSetupRow:
    view = setup.view
    return WorkflowSetupRow(
        **{key: value for key, value in view.model_dump().items() if key != "id"},
        setup_id=view.id,
        actor_id=setup.actor_id,
        request_key=setup.request_key,
        request_hash=setup.request_hash,
        import_nonce=setup.import_nonce,
        public_json=serialized(view),
    )


def as_setup(row: WorkflowSetupRow) -> StoredSetup:
    view = decode(SetupView, row.public_json)
    try:
        expected = setup_row(StoredSetup(view, row.actor_id, row.request_key, row.request_hash, row.import_nonce))
    except (ValueError, TypeError):
        raise PersistenceError("stored_setup_internal_invalid") from None
    for column in WorkflowSetupRow.__table__.columns:
        key = column.key
        if key in {"public_json", "created_at", "updated_at"}:
            continue
        if getattr(row, key) != getattr(expected, key):
            raise PersistenceError("stored_setup_scope_invalid")
    if utc(row.created_at) != view.created_at or utc(row.updated_at) != view.updated_at:
        raise PersistenceError("stored_setup_timestamp_invalid")
    return StoredSetup(view, row.actor_id, row.request_key, row.request_hash, row.import_nonce)


def _get(session: Session, workspace_id: str, setup_id: str) -> StoredSetup:
    row = session.scalar(
        select(WorkflowSetupRow)
        .where(WorkflowSetupRow.workspace_id == workspace_id, WorkflowSetupRow.setup_id == setup_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("workflow_setup_not_found")
    return as_setup(row)


def _audit(session: Session, setup: StoredSetup, actor_id: str) -> None:
    view = setup.view
    audit(
        session,
        view.workspace_id,
        "workflow_setup",
        view.id,
        actor_id,
        "workflow_setup_" + view.state,
        view.updated_at,
        {"revision": view.revision, "state": view.state},
    )


class SqlAlchemyWorkflowSetupRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def find_request(self, workspace_id: str, request_key: str) -> StoredSetup | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(WorkflowSetupRow).where(
                    WorkflowSetupRow.workspace_id == workspace_id, WorkflowSetupRow.request_key == request_key
                )
            )
            return as_setup(row) if row is not None else None

    def create(self, setup: StoredSetup, *, actor_id: str) -> StoredSetup:
        if setup.view.revision != 1 or setup.view.state != "queued" or setup.actor_id != actor_id:
            raise Conflict("workflow_setup_initial_state_required")
        try:
            with transaction(self._sessions) as session:
                lock_device(session, setup.view.workspace_id, setup.view.device_id)
                row = session.scalar(
                    select(WorkflowSetupRow).where(
                        WorkflowSetupRow.workspace_id == setup.view.workspace_id,
                        WorkflowSetupRow.request_key == setup.request_key,
                    )
                )
                if row is not None:
                    return self._replay(as_setup(row), setup)
                session.add(setup_row(setup))
                session.flush()
                _audit(session, setup, actor_id)
            return setup
        except Conflict as error:
            if str(error) != "database_constraint_conflict":
                raise
            existing = self.find_request(setup.view.workspace_id, setup.request_key)
            if existing is None:
                raise
            return self._replay(existing, setup)

    @staticmethod
    def _replay(existing: StoredSetup, requested: StoredSetup) -> StoredSetup:
        if existing.request_hash != requested.request_hash:
            raise Conflict("idempotency_key_reused")
        return existing

    def get(self, workspace_id: str, setup_id: str) -> StoredSetup:
        with transaction(self._sessions) as session:
            return _get(session, workspace_id, setup_id)

    def list(
        self, workspace_id: str, *, device_id: str, scenario: Scenario, offset: int, limit: int
    ) -> Page[SetupView]:
        validate_page(offset, limit)
        with transaction(self._sessions) as session:
            query = select(WorkflowSetupRow).where(
                WorkflowSetupRow.workspace_id == workspace_id,
                WorkflowSetupRow.device_id == device_id,
                WorkflowSetupRow.scenario == scenario,
            )
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(
                query.order_by(WorkflowSetupRow.created_at.desc(), WorkflowSetupRow.setup_id)
                .offset(offset)
                .limit(limit)
            ).all()
            return Page(items=tuple(as_setup(row).view for row in rows), total=total, offset=offset, limit=limit)

    def claim_import(
        self, workspace_id: str, setup_id: str, *, expected_revision: int, nonce: str, actor_id: str
    ) -> StoredSetup:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, setup_id)
            lock_device(session, workspace_id, old.view.device_id)
            if old.view.state != "queued" or old.view.revision != expected_revision:
                raise Conflict("workflow_setup_not_claimable")
            view = SetupView.model_validate(
                old.view.model_dump()
                | {"state": "importing", "revision": old.view.revision + 1, "updated_at": utc_now()}
            )
            result = replace(old, view=view, import_nonce=nonce)
            cas(
                session,
                update(WorkflowSetupRow)
                .where(
                    WorkflowSetupRow.workspace_id == workspace_id,
                    WorkflowSetupRow.setup_id == setup_id,
                    WorkflowSetupRow.revision == expected_revision,
                    WorkflowSetupRow.state == "queued",
                )
                .values(
                    state=view.state,
                    revision=view.revision,
                    updated_at=view.updated_at,
                    public_json=serialized(view),
                    import_nonce=nonce,
                ),
            )
            _audit(session, result, actor_id)
            return result

    def finish_import(
        self,
        workspace_id: str,
        setup_id: str,
        *,
        nonce: str,
        state: SetupFinalState,
        app_id: str | None,
        import_id: str | None,
        reason_code: str | None,
        actor_id: str,
    ) -> StoredSetup:
        if state not in {"draft_ready", "confirmation_required", "uncertain", "failed"}:
            raise Conflict("workflow_setup_final_state_required")
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, setup_id)
            if old.view.state != "importing" or old.import_nonce != nonce:
                raise Conflict("workflow_setup_import_ownership_lost")
            view = SetupView.model_validate(
                old.view.model_dump()
                | {
                    "state": state,
                    "app_id": app_id,
                    "import_id": import_id,
                    "reason_code": reason_code,
                    "revision": old.view.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            result = replace(old, view=view, import_nonce=None)
            cas(
                session,
                update(WorkflowSetupRow)
                .where(
                    WorkflowSetupRow.workspace_id == workspace_id,
                    WorkflowSetupRow.setup_id == setup_id,
                    WorkflowSetupRow.state == "importing",
                    WorkflowSetupRow.import_nonce == nonce,
                    WorkflowSetupRow.revision == old.view.revision,
                )
                .values(
                    state=state,
                    app_id=app_id,
                    import_id=import_id,
                    reason_code=reason_code,
                    revision=view.revision,
                    updated_at=view.updated_at,
                    public_json=serialized(view),
                    import_nonce=None,
                ),
            )
            _audit(session, result, actor_id)
            return result
