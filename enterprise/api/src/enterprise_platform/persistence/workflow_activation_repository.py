"""Commit a validated device binding and activation in one enterprise transaction."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.assessments import SpecificationRegistry
from enterprise_platform.application.contracts import BindingWrite
from enterprise_platform.application.errors import AccessDenied, Conflict, NotFound, PersistenceError
from enterprise_platform.application.workflow_activation_contracts import (
    ActivationView,
    create_activation,
    revoke_activation,
)
from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry

from .mapping import ACTIVE_STATES, as_binding, audit, cas, lock_device, serialized, transaction, utc_now
from .models import BindingRow, RunRow
from .source_models import SourceHeadRow, SourceVersionRow
from .sources import as_source
from .workflow_activation_mapping import activation_row, as_activation
from .workflow_activation_models import WorkflowActivationRow
from .workflow_credential_models import WorkflowCredentialRow
from .workflow_credentials import as_credential
from .workflow_enrollment_mapping import as_enrollment
from .workflow_enrollment_models import WorkflowEnrollmentRow


def _get(session: Session, workspace_id: str, activation_id: str, *, lock: bool = False) -> ActivationView:
    query = select(WorkflowActivationRow).where(
        WorkflowActivationRow.workspace_id == workspace_id,
        WorkflowActivationRow.activation_id == activation_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query)
    if row is None:
        raise NotFound("workflow_activation_not_found")
    value = as_activation(row)
    if (value.binding.workspace_id, value.id) != (workspace_id, activation_id):
        raise PersistenceError("stored_activation_scope_invalid")
    return value


class SqlAlchemyWorkflowActivationRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        profiles: WorkflowPluginProfileRegistry,
        specifications: SpecificationRegistry,
    ) -> None:
        self._sessions, self._profiles, self._specifications = sessions, profiles, specifications

    def get(self, workspace_id: str, activation_id: str) -> ActivationView:
        with transaction(self._sessions) as session:
            return _get(session, workspace_id, activation_id)

    def find_enrollment(self, workspace_id: str, enrollment_id: str) -> ActivationView | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(WorkflowActivationRow).where(
                    WorkflowActivationRow.workspace_id == workspace_id,
                    WorkflowActivationRow.enrollment_id == enrollment_id,
                )
            )
            if row is None:
                return None
            value = as_activation(row)
            if (value.binding.workspace_id, value.enrollment.id) != (workspace_id, enrollment_id):
                raise PersistenceError("stored_activation_scope_invalid")
            return value

    def revoke(self, workspace_id: str, activation_id: str, *, expected_revision: int, actor_id: str) -> ActivationView:
        """Revoke future grant resolution; leave bindings and in-flight runs intact."""
        with transaction(self._sessions) as session:
            old = _get(session, workspace_id, activation_id, lock=True)
            value = revoke_activation(old, expected_revision=expected_revision, actor_id=actor_id, now=utc_now())
            cas(
                session,
                update(WorkflowActivationRow)
                .where(
                    WorkflowActivationRow.workspace_id == workspace_id,
                    WorkflowActivationRow.activation_id == activation_id,
                    WorkflowActivationRow.revision == old.revision,
                    WorkflowActivationRow.active.is_(True),
                )
                .values(
                    public_json=serialized(value), revision=value.revision, active=False, updated_at=value.updated_at
                ),
            )
            audit(
                session,
                workspace_id,
                "workflow_activation",
                activation_id,
                actor_id,
                "workflow_activation_revoked",
                value.updated_at,
                {"revision": value.revision},
            )
            return value

    def create(self, activation: ActivationView, *, actor_id: str) -> ActivationView:
        value = ActivationView.model_validate(activation.model_dump())
        p = value.enrollment.provisioning
        if actor_id != p.actor_id:
            raise AccessDenied("workflow_activation_actor_mismatch")
        if not value.active or value.revision != 1:
            raise Conflict("workflow_activation_initial_state_required")
        profile = self._profiles.resolve_profile(
            p.workspace_id, configuration_ref=p.config_ref, configuration_revision=p.config_revision
        )
        if (
            create_activation(
                value.enrollment,
                value.binding,
                profile,
                activation_id=value.id,
                actor_id=actor_id,
                now=value.created_at,
            )
            != value
        ):
            raise Conflict("workflow_activation_profile_changed")
        spec = self._specifications.resolve(p.workspace_id, p.scenario, value.binding.specification_revision)
        if (spec.workspace_id, spec.scenario, spec.specification_revision) != (
            p.workspace_id,
            p.scenario,
            value.binding.specification_revision,
        ):
            raise Conflict("workflow_activation_specification_mismatch")
        with transaction(self._sessions) as session:
            lock_device(session, p.workspace_id, p.device_id)
            enrollment = session.scalar(
                select(WorkflowEnrollmentRow)
                .where(
                    WorkflowEnrollmentRow.workspace_id == p.workspace_id,
                    WorkflowEnrollmentRow.enrollment_id == value.enrollment.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if enrollment is None or as_enrollment(enrollment).view != value.enrollment:
                raise Conflict("workflow_activation_enrollment_changed")
            existing = session.scalar(
                select(WorkflowActivationRow).where(
                    WorkflowActivationRow.workspace_id == p.workspace_id,
                    WorkflowActivationRow.enrollment_id == value.enrollment.id,
                )
            )
            if existing is not None:
                raise Conflict("workflow_activation_already_exists")
            expected = value.enrollment.credential
            if expected is None:
                raise Conflict("workflow_activation_credential_required")
            credential = session.scalar(
                select(WorkflowCredentialRow)
                .where(
                    WorkflowCredentialRow.workspace_id == p.workspace_id,
                    WorkflowCredentialRow.app_id == p.app_id,
                    WorkflowCredentialRow.secret_ref == expected.secret_ref,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if credential is None or as_credential(credential)[0] != expected:
                raise Conflict("workflow_activation_credential_changed")
            head = session.scalar(
                select(SourceHeadRow)
                .where(
                    SourceHeadRow.workspace_id == p.workspace_id,
                    SourceHeadRow.source_id == p.source_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if head is None or (head.workspace_id, head.source_id, head.read_id, head.revision) != (
                p.workspace_id,
                p.source_id,
                p.read_id,
                p.expected_source_revision,
            ):
                raise Conflict("workflow_activation_source_changed")
            version = session.scalar(
                select(SourceVersionRow).where(
                    SourceVersionRow.workspace_id == p.workspace_id,
                    SourceVersionRow.source_id == p.source_id,
                    SourceVersionRow.revision == head.revision,
                )
            )
            if version is None:
                raise Conflict("workflow_activation_source_changed")
            source = as_source(version).view
            if (
                source.workspace_id,
                source.source_id,
                source.revision,
                source.source_revision,
                source.read_id,
                source.read_revision,
            ) != (
                p.workspace_id,
                p.source_id,
                p.expected_source_revision,
                p.source_revision,
                p.read_id,
                p.read_revision,
            ) or p.device_id not in source.device_ids:
                raise Conflict("workflow_activation_source_changed")
            outstanding = session.scalar(
                select(RunRow.run_id)
                .where(
                    RunRow.workspace_id == p.workspace_id,
                    RunRow.device_id == p.device_id,
                    RunRow.state.in_(ACTIVE_STATES),
                )
                .limit(1)
            )
            if outstanding is not None:
                raise Conflict("workflow_activation_device_busy")
            current = session.scalar(
                select(BindingRow)
                .where(
                    BindingRow.workspace_id == p.workspace_id,
                    BindingRow.device_id == p.device_id,
                    BindingRow.scenario == p.scenario,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            payload = serialized(
                BindingWrite.model_validate(
                    value.binding.model_dump(
                        exclude={
                            "id",
                            "workspace_id",
                            "device_id",
                            "scenario",
                            "revision",
                            "created_at",
                            "updated_at",
                            "active_run_id",
                        }
                    )
                )
            )
            if current is None:
                if p.expected_binding_revision is not None:
                    raise Conflict("workflow_activation_binding_changed")
                session.add(
                    BindingRow(
                        workspace_id=p.workspace_id,
                        binding_id=value.binding.id,
                        device_id=p.device_id,
                        scenario=p.scenario,
                        revision=1,
                        binding_json=payload,
                        active_run_id=None,
                        created_at=value.binding.created_at,
                        updated_at=value.binding.updated_at,
                    )
                )
            else:
                old = as_binding(current)
                if (
                    old.workspace_id,
                    old.device_id,
                    old.scenario,
                    old.id,
                    old.revision,
                    old.created_at,
                    old.active_run_id,
                ) != (
                    p.workspace_id,
                    p.device_id,
                    p.scenario,
                    value.binding.id,
                    p.expected_binding_revision,
                    value.binding.created_at,
                    None,
                ) or value.binding.updated_at < old.updated_at:
                    raise Conflict("workflow_activation_binding_changed")
                cas(
                    session,
                    update(BindingRow)
                    .where(
                        BindingRow.workspace_id == p.workspace_id,
                        BindingRow.binding_id == old.id,
                        BindingRow.revision == old.revision,
                        BindingRow.active_run_id.is_(None),
                    )
                    .values(binding_json=payload, revision=value.binding.revision, updated_at=value.binding.updated_at),
                )
            session.add(activation_row(value))
            session.flush()
            audit(
                session,
                p.workspace_id,
                "binding",
                value.binding.id,
                actor_id,
                "binding.created" if current is None else "binding.updated",
                value.binding.updated_at,
                {"revision": value.binding.revision},
            )
            audit(
                session,
                p.workspace_id,
                "workflow_activation",
                value.id,
                actor_id,
                "workflow_activation_created",
                value.created_at,
                {"revision": 1, "binding_id": value.binding.id},
            )
            return value
