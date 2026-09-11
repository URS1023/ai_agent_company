"""Enrollment persistence primitives; no native HTTP is performed in transactions."""

from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.workflow_credential_contracts import CredentialView
from enterprise_platform.application.workflow_credential_ports import CredentialCipher
from enterprise_platform.application.workflow_enrollment_contracts import (
    EnrollmentPhase,
    StoredEnrollment,
    claim_enrollment,
    finish_enrollment_token,
    finish_enrollment_verification,
)
from enterprise_platform.application.workflow_publication_read import NativePublicationMetadata
from enterprise_platform.application.workflow_token_execution import NativeWorkflowTokenOutcome

from .mapping import audit, cas, lock_device, transaction, utc_now
from .workflow_credential_models import WorkflowCredentialRow
from .workflow_credentials import credential_row
from .workflow_enrollment_mapping import as_enrollment, enrollment_row
from .workflow_enrollment_models import WorkflowEnrollmentRow
from .workflow_provisioning import as_provisioning
from .workflow_provisioning_models import WorkflowProvisioningRow
from .workflow_setup_models import WorkflowSetupRow
from .workflow_setups import as_setup


def _validate_dependencies(session: Session, value: StoredEnrollment) -> None:
    expected = value.view.provisioning
    lock_device(session, expected.workspace_id, expected.device_id)
    row = session.scalar(
        select(WorkflowProvisioningRow)
        .where(
            WorkflowProvisioningRow.workspace_id == expected.workspace_id,
            WorkflowProvisioningRow.provisioning_id == expected.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or as_provisioning(row).view != expected:
        raise Conflict("workflow_enrollment_provisioning_changed")
    setup_row = session.scalar(
        select(WorkflowSetupRow)
        .where(
            WorkflowSetupRow.workspace_id == expected.workspace_id,
            WorkflowSetupRow.setup_id == expected.setup_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if setup_row is None:
        raise Conflict("workflow_enrollment_setup_changed")
    setup = as_setup(setup_row).view
    if setup.state != "draft_ready" or setup.revision != expected.setup_revision:
        raise Conflict("workflow_enrollment_setup_changed")
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
        if getattr(setup, field) != getattr(expected, field):
            raise Conflict("workflow_enrollment_setup_changed")


def _get(session: Session, workspace_id: str, enrollment_id: str) -> StoredEnrollment:
    row = session.scalar(
        select(WorkflowEnrollmentRow)
        .where(
            WorkflowEnrollmentRow.workspace_id == workspace_id,
            WorkflowEnrollmentRow.enrollment_id == enrollment_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("workflow_enrollment_not_found")
    value = as_enrollment(row)
    if (value.view.provisioning.workspace_id, value.view.id) != (workspace_id, enrollment_id):
        raise PersistenceError("stored_enrollment_scope_invalid")
    return value


def _write(session: Session, old: StoredEnrollment, value: StoredEnrollment) -> None:
    row = enrollment_row(value)
    cas(
        session,
        update(WorkflowEnrollmentRow)
        .where(
            WorkflowEnrollmentRow.workspace_id == old.view.provisioning.workspace_id,
            WorkflowEnrollmentRow.enrollment_id == old.view.id,
            WorkflowEnrollmentRow.actor_id == old.view.provisioning.actor_id,
            WorkflowEnrollmentRow.revision == old.view.revision,
            WorkflowEnrollmentRow.state == old.view.state,
            WorkflowEnrollmentRow.claim_nonce == old.claim_nonce,
        )
        .values(
            state=row.state,
            revision=row.revision,
            claim_nonce=row.claim_nonce,
            updated_at=row.updated_at,
            public_json=row.public_json,
        ),
    )
    audit(
        session,
        row.workspace_id,
        "workflow_enrollment",
        row.enrollment_id,
        row.actor_id,
        "workflow_enrollment_" + row.state,
        row.updated_at,
        {"state": row.state, "revision": row.revision},
    )


class SqlAlchemyWorkflowEnrollmentRepository:
    """One transaction per operation; callers invoke native I/O after claim returns."""

    def __init__(self, sessions: sessionmaker[Session], cipher: CredentialCipher) -> None:
        self._sessions = sessions
        self._cipher = cipher

    def create(self, enrollment: StoredEnrollment, *, actor_id: str) -> StoredEnrollment:
        value = StoredEnrollment.model_validate(enrollment)
        if (
            value.view.state != "pending_verification"
            or value.view.revision != 1
            or value.view.provisioning.actor_id != actor_id
        ):
            raise Conflict("workflow_enrollment_initial_state_required")
        with transaction(self._sessions) as session:
            _validate_dependencies(session, value)
            existing = session.scalar(
                select(WorkflowEnrollmentRow).where(
                    WorkflowEnrollmentRow.workspace_id == value.view.provisioning.workspace_id,
                    WorkflowEnrollmentRow.provisioning_id == value.view.provisioning.id,
                )
            )
            if existing is not None:
                stored = as_enrollment(existing)
                if stored.view.provisioning != value.view.provisioning:
                    raise Conflict("workflow_enrollment_provisioning_changed")
                return stored
            row = enrollment_row(value)
            session.add(row)
            session.flush()
            audit(
                session,
                row.workspace_id,
                "workflow_enrollment",
                row.enrollment_id,
                actor_id,
                "workflow_enrollment_created",
                row.created_at,
                {"state": row.state, "revision": 1},
            )
            return value

    def get(self, workspace_id: str, enrollment_id: str) -> StoredEnrollment:
        with transaction(self._sessions) as session:
            return _get(session, workspace_id, enrollment_id)

    def find_provisioning(self, workspace_id: str, provisioning_id: str) -> StoredEnrollment | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(WorkflowEnrollmentRow)
                .where(
                    WorkflowEnrollmentRow.workspace_id == workspace_id,
                    WorkflowEnrollmentRow.provisioning_id == provisioning_id,
                )
                .execution_options(populate_existing=True)
            )
            if row is None:
                return None
            value = as_enrollment(row)
            if (value.view.provisioning.workspace_id, value.view.provisioning.id) != (workspace_id, provisioning_id):
                raise PersistenceError("stored_enrollment_scope_invalid")
            return value

    def claim(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        phase: EnrollmentPhase,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment:
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, enrollment_id)
            result = claim_enrollment(
                old, phase=phase, nonce=nonce, expected_revision=expected_revision, actor_id=actor_id, now=utc_now()
            )
            _validate_dependencies(session, old)
            _write(session, old, result)
            return result

    def finish_verification(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        publication: NativePublicationMetadata | None = None,
        reason_code: str | None = None,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment:
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, enrollment_id)
            result = finish_enrollment_verification(
                old,
                publication=publication,
                reason_code=reason_code,
                nonce=nonce,
                expected_revision=expected_revision,
                actor_id=actor_id,
                now=utc_now(),
            )
            _write(session, old, result)
            return result

    def finish_token_failure(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        state: Literal["rejected", "uncertain"],
        reason_code: str,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment:
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, enrollment_id)
            result = finish_enrollment_token(
                old,
                state=state,
                reason_code=reason_code,
                nonce=nonce,
                expected_revision=expected_revision,
                actor_id=actor_id,
                now=utc_now(),
            )
            _write(session, old, result)
            return result

    def store_issued_token(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        issued: NativeWorkflowTokenOutcome,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment:
        issued = NativeWorkflowTokenOutcome.model_validate(issued)
        if issued.state != "issued" or issued.token is None or issued.token_id is None:
            raise Conflict("workflow_enrollment_confirmed_token_required")
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, enrollment_id)
            provisioning = old.view.provisioning
            if (issued.workspace_id, issued.app_id) != (workspace_id, provisioning.app_id):
                raise Conflict("workflow_enrollment_token_scope_mismatch")
            now = utc_now()
            credential = CredentialView(
                workspace_id=workspace_id,
                app_id=provisioning.app_id,
                secret_ref=old.secret_ref,
                revision=1,
                active=True,
                created_at=now,
                updated_at=now,
            )
            result = finish_enrollment_token(
                old,
                state="token_stored",
                native_token_id=issued.token_id,
                credential=credential,
                nonce=nonce,
                expected_revision=expected_revision,
                actor_id=actor_id,
                now=now,
            )
            existing = session.scalar(
                select(WorkflowCredentialRow).where(
                    WorkflowCredentialRow.workspace_id == workspace_id,
                    WorkflowCredentialRow.app_id == provisioning.app_id,
                    WorkflowCredentialRow.secret_ref == old.secret_ref,
                )
            )
            if existing is not None:
                raise Conflict("workflow_enrollment_credential_already_exists")
            sealed = self._cipher.seal(credential, issued.token)
            session.add(credential_row(credential, sealed))
            session.flush()
            _write(session, old, result)
            audit(
                session,
                workspace_id,
                "workflow_credential",
                credential.secret_ref,
                actor_id,
                "workflow_credential_registered",
                now,
                {"app_id": credential.app_id, "revision": 1, "active": True},
            )
            return result
