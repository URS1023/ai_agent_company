"""Atomic provisioning journal claims; native I/O runs only after commit."""

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Identifier, Page
from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.application.workflow_provisioning_contracts import (
    PhaseCommand,
    PhaseOutcome,
    ProvisioningView,
    StoredProvisioning,
    claim_provisioning,
    finish_provisioning,
)

from .mapping import audit, cas, decode, lock_device, serialized, transaction, utc, utc_now, validate_page
from .workflow_provisioning_models import WorkflowProvisioningRow
from .workflow_setup_models import WorkflowSetupRow
from .workflow_setups import as_setup


def provisioning_row(value: StoredProvisioning) -> WorkflowProvisioningRow:
    value = StoredProvisioning.model_validate(value)
    view = value.view
    last = view.phases[-1] if view.phases else None
    return WorkflowProvisioningRow(
        **{key: item for key, item in view.model_dump().items() if key not in {"id", "phases"}},
        provisioning_id=view.id,
        request_key=value.request_key,
        request_hash=value.request_hash,
        claim_nonce=value.claim_nonce,
        active_phase=last.command.kind if last else None,
        phase_state=last.state if last else None,
        phase_count=len(view.phases),
        public_json=serialized(view),
    )


def as_provisioning(row: WorkflowProvisioningRow) -> StoredProvisioning:
    view = decode(ProvisioningView, row.public_json)
    try:
        value = StoredProvisioning(
            view=view, request_key=row.request_key, request_hash=row.request_hash, claim_nonce=row.claim_nonce
        )
        expected = provisioning_row(value)
        for column in WorkflowProvisioningRow.__table__.columns:
            key = column.key
            if key not in {"public_json", "created_at", "updated_at"} and getattr(row, key) != getattr(expected, key):
                raise PersistenceError("stored_provisioning_scope_invalid")
        if utc(row.created_at) != view.created_at or utc(row.updated_at) != view.updated_at:
            raise PersistenceError("stored_provisioning_timestamp_invalid")
        return value
    except (ValueError, TypeError, AttributeError):
        raise PersistenceError("stored_provisioning_internal_invalid") from None


def _get(session: Session, workspace_id: str, provisioning_id: str) -> StoredProvisioning:
    row = session.scalar(
        select(WorkflowProvisioningRow)
        .where(
            WorkflowProvisioningRow.workspace_id == workspace_id,
            WorkflowProvisioningRow.provisioning_id == provisioning_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("workflow_provisioning_not_found")
    return as_provisioning(row)


def _validate_setup(session: Session, value: StoredProvisioning) -> None:
    view = value.view
    row = session.scalar(
        select(WorkflowSetupRow)
        .where(
            WorkflowSetupRow.workspace_id == view.workspace_id,
            WorkflowSetupRow.setup_id == view.setup_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("workflow_setup_not_found")
    setup = as_setup(row).view
    if setup.state != "draft_ready" or setup.revision != view.setup_revision:
        raise Conflict("workflow_provisioning_setup_changed")
    for field in (
        "workspace_id",
        "app_id",
        "device_id",
        "scenario",
        "source_id",
        "source_revision",
        "read_id",
        "read_revision",
        "expected_source_revision",
        "expected_binding_revision",
    ):
        if getattr(setup, field) != getattr(view, field):
            raise Conflict("workflow_provisioning_setup_changed")
    if view.created_at < setup.updated_at:
        raise Conflict("workflow_provisioning_setup_changed")


def _audit(session: Session, value: StoredProvisioning) -> None:
    view = value.view
    phase = view.phases[-1] if view.phases else None
    audit(
        session,
        view.workspace_id,
        "workflow_provisioning",
        view.id,
        view.actor_id,
        "workflow_provisioning_" + (phase.state if phase else "created"),
        view.updated_at,
        {"revision": view.revision, "state": view.state, "phase": phase.command.kind if phase else None},
    )


def _write(session: Session, old: StoredProvisioning, value: StoredProvisioning) -> None:
    row = provisioning_row(value)
    cas(
        session,
        update(WorkflowProvisioningRow)
        .where(
            WorkflowProvisioningRow.workspace_id == old.view.workspace_id,
            WorkflowProvisioningRow.provisioning_id == old.view.id,
            WorkflowProvisioningRow.actor_id == old.view.actor_id,
            WorkflowProvisioningRow.revision == old.view.revision,
            WorkflowProvisioningRow.state == old.view.state,
            WorkflowProvisioningRow.claim_nonce == old.claim_nonce,
        )
        .values(
            state=row.state,
            revision=row.revision,
            active_phase=row.active_phase,
            phase_state=row.phase_state,
            phase_count=row.phase_count,
            claim_nonce=row.claim_nonce,
            updated_at=row.updated_at,
            public_json=row.public_json,
        ),
    )
    _audit(session, value)


class SqlAlchemyWorkflowProvisioningRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _replay(existing: StoredProvisioning, requested: StoredProvisioning) -> StoredProvisioning:
        if existing.request_hash != requested.request_hash or existing.view.actor_id != requested.view.actor_id:
            raise Conflict("idempotency_key_reused")
        return existing

    def create(self, provisioning: StoredProvisioning, *, actor_id: str) -> StoredProvisioning:
        value = StoredProvisioning.model_validate(provisioning)
        if value.view.revision != 1 or value.view.phases or value.view.actor_id != actor_id:
            raise Conflict("workflow_provisioning_initial_state_required")
        try:
            with transaction(self._sessions) as session:
                lock_device(session, value.view.workspace_id, value.view.device_id)
                _validate_setup(session, value)
                row = session.scalar(
                    select(WorkflowProvisioningRow).where(
                        WorkflowProvisioningRow.workspace_id == value.view.workspace_id,
                        WorkflowProvisioningRow.request_key == value.request_key,
                    )
                )
                if row is not None:
                    return self._replay(as_provisioning(row), value)
                session.add(provisioning_row(value))
                session.flush()
                _audit(session, value)
            return value
        except Conflict as error:
            if str(error) != "database_constraint_conflict":
                raise
            existing = self.find_request(value.view.workspace_id, value.request_key)
            if existing is None:
                raise
            return self._replay(existing, value)

    def get(self, workspace_id: str, provisioning_id: str) -> StoredProvisioning:
        with transaction(self._sessions) as session:
            return _get(session, workspace_id, provisioning_id)

    def find_request(self, workspace_id: str, request_key: str) -> StoredProvisioning | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(WorkflowProvisioningRow).where(
                    WorkflowProvisioningRow.workspace_id == workspace_id,
                    WorkflowProvisioningRow.request_key == request_key,
                )
            )
            return as_provisioning(row) if row is not None else None

    def list(
        self,
        workspace_id: str,
        *,
        setup_id: str | None = None,
        actor_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Page[ProvisioningView]:
        validate_page(offset, limit)
        if actor_id is not None:
            try:
                TypeAdapter(Identifier).validate_python(actor_id)
            except ValidationError:
                raise InvalidInput("invalid_provisioning_actor") from None
        with transaction(self._sessions) as session:
            query = select(WorkflowProvisioningRow).where(WorkflowProvisioningRow.workspace_id == workspace_id)
            if setup_id is not None:
                query = query.where(WorkflowProvisioningRow.setup_id == setup_id)
            if actor_id is not None:
                query = query.where(WorkflowProvisioningRow.actor_id == actor_id)
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(
                query.order_by(WorkflowProvisioningRow.created_at.desc(), WorkflowProvisioningRow.provisioning_id)
                .offset(offset)
                .limit(limit)
            ).all()
            return Page(items=tuple(as_provisioning(row).view for row in rows), total=total, offset=offset, limit=limit)

    def claim(
        self,
        workspace_id: str,
        provisioning_id: str,
        *,
        command: PhaseCommand,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredProvisioning:
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, provisioning_id)
            lock_device(session, workspace_id, old.view.device_id)
            _validate_setup(session, old)
            result = claim_provisioning(
                old, command=command, nonce=nonce, expected_revision=expected_revision, actor_id=actor_id, now=utc_now()
            )
            _write(session, old, result)
            return result

    def finish(
        self,
        workspace_id: str,
        provisioning_id: str,
        *,
        outcome: PhaseOutcome,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredProvisioning:
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, provisioning_id)
            result = finish_provisioning(
                old, outcome=outcome, nonce=nonce, expected_revision=expected_revision, actor_id=actor_id, now=utc_now()
            )
            _write(session, old, result)
            return result
