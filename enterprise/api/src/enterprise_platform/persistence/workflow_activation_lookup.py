"""Read live activation dependencies before deriving a version-scoped execution key."""

import hmac
import re
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Binding
from enterprise_platform.application.errors import EnterpriseError
from enterprise_platform.application.managed_execution import ActiveExecutionKey
from enterprise_platform.application.workflow_activation_contracts import ActivationView
from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry, derive_execution_key
from enterprise_platform.application.workflow_provisioning_contracts import NativeUUID, PrepareCredentialReceipt
from enterprise_platform.application.workflow_publication_read import NativePublicationMetadata

from .mapping import as_binding, transaction
from .models import BindingRow, DeviceRow
from .workflow_activation_mapping import as_activation
from .workflow_activation_models import WorkflowActivationRow
from .workflow_credential_models import WorkflowCredentialRow
from .workflow_credentials import as_credential
from .workflow_enrollment_mapping import as_enrollment
from .workflow_enrollment_models import WorkflowEnrollmentRow


class SqlAlchemyActiveExecutionKeyLookup:
    def __init__(self, sessions: sessionmaker[Session], profiles: WorkflowPluginProfileRegistry) -> None:
        self._sessions, self._profiles = sessions, profiles

    def resolve(self, key_id: str, *, workflow_id: UUID) -> ActiveExecutionKey | None:
        if re.fullmatch(r"ep-[0-9a-f]{64}", key_id) is None or not isinstance(workflow_id, UUID) or not workflow_id.int:
            return None
        try:
            with transaction(self._sessions) as session:
                return self._resolve(session, key_id, workflow_id)
        except (EnterpriseError, ValueError, TypeError):
            return None

    def find_registration(
        self, workspace_id: str, app_id: str, *, workflow_id: UUID, expected_binding: Binding | None = None
    ) -> NativePublicationMetadata | None:
        """Return only public registration fields, never the derived key or vault token.

        Intended for a server-authenticated native bridge, not browser discovery.
        The initial receipt must still match when all live dependencies are checked.
        Scheduling additionally pins the full binding snapshot; only its transient
        active_run_id may differ. Missing data never falls back to latest workflow.
        """
        try:
            TypeAdapter(NativeUUID).validate_python(workspace_id)
            TypeAdapter(NativeUUID).validate_python(app_id)
            if not isinstance(workflow_id, UUID) or not workflow_id.int:
                return None
            if expected_binding is not None:
                expected_binding = Binding.model_validate(expected_binding.model_dump())
                if (expected_binding.workspace_id, expected_binding.app_id, expected_binding.workflow_id) != (
                    workspace_id,
                    app_id,
                    workflow_id,
                ):
                    return None
            with transaction(self._sessions) as session:
                statement = (
                    select(WorkflowActivationRow)
                    .where(
                        WorkflowActivationRow.workspace_id == workspace_id,
                        WorkflowActivationRow.app_id == app_id,
                        WorkflowActivationRow.workflow_id == str(workflow_id),
                        WorkflowActivationRow.active.is_(True),
                    )
                    .execution_options(populate_existing=True)
                )
                if expected_binding is not None:
                    statement = statement.where(
                        WorkflowActivationRow.binding_id == expected_binding.id,
                        WorkflowActivationRow.binding_revision == expected_binding.revision,
                    )
                row = session.scalar(statement)
                if row is None:
                    return None
                value = as_activation(row)
                if expected_binding is not None and value.binding.model_dump(exclude={"active_run_id"}) != (
                    expected_binding.model_dump(exclude={"active_run_id"})
                ):
                    return None
                if not value.active or (
                    value.binding.workspace_id,
                    value.binding.app_id,
                    value.binding.workflow_id,
                ) != (workspace_id, app_id, workflow_id):
                    return None
                if self._resolve(session, value.key_id, workflow_id, expected_activation=value) is None:
                    return None
                return value.enrollment.publication
        except (EnterpriseError, ValueError, TypeError):
            return None

    def _resolve(
        self, session: Session, key_id: str, workflow_id: UUID, *, expected_activation: ActivationView | None = None
    ) -> ActiveExecutionKey | None:
        row = session.scalar(
            select(WorkflowActivationRow)
            .where(
                WorkflowActivationRow.key_id == key_id,
                WorkflowActivationRow.workflow_id == str(workflow_id),
                WorkflowActivationRow.active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        activation = as_activation(row)
        if expected_activation is not None and activation != expected_activation:
            return None
        if not activation.active or activation.key_id != key_id or activation.binding.workflow_id != workflow_id:
            return None
        p = activation.enrollment.provisioning
        enrollment = session.scalar(
            select(WorkflowEnrollmentRow)
            .where(
                WorkflowEnrollmentRow.workspace_id == p.workspace_id,
                WorkflowEnrollmentRow.enrollment_id == activation.enrollment.id,
            )
            .execution_options(populate_existing=True)
        )
        if enrollment is None or as_enrollment(enrollment).view != activation.enrollment:
            return None
        binding = session.scalar(
            select(BindingRow)
            .where(
                BindingRow.workspace_id == p.workspace_id,
                BindingRow.binding_id == activation.binding.id,
            )
            .execution_options(populate_existing=True)
        )
        if binding is None:
            return None
        # Run ownership changes active_run_id without changing the binding revision.
        if as_binding(binding).model_dump(exclude={"active_run_id"}) != activation.binding.model_dump(
            exclude={"active_run_id"}
        ):
            return None
        expected = activation.enrollment.credential
        if expected is None:
            return None
        credential = session.scalar(
            select(WorkflowCredentialRow)
            .where(
                WorkflowCredentialRow.workspace_id == p.workspace_id,
                WorkflowCredentialRow.app_id == p.app_id,
                WorkflowCredentialRow.secret_ref == expected.secret_ref,
                WorkflowCredentialRow.active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        if credential is None or as_credential(credential)[0] != expected:
            return None
        device = session.scalar(
            select(DeviceRow)
            .where(
                DeviceRow.workspace_id == p.workspace_id,
                DeviceRow.device_id == p.device_id,
                DeviceRow.deleted.is_(False),
            )
            .execution_options(populate_existing=True)
        )
        if device is None or device.deleted or (device.workspace_id, device.device_id) != (p.workspace_id, p.device_id):
            return None
        profile = self._profiles.resolve_profile(
            p.workspace_id, configuration_ref=p.config_ref, configuration_revision=p.config_revision
        )
        prepared = p.phases[1].receipt
        if (
            not isinstance(prepared, PrepareCredentialReceipt)
            or profile.expected_plugin_unique_identifier != prepared.plugin_unique_identifier
        ):
            return None
        key = derive_execution_key(profile, app_id=p.app_id, node_ids=frozenset({"assessment"}))
        if not hmac.compare_digest(key.key_id, key_id):
            return None
        return ActiveExecutionKey(key=key, workflow_id=workflow_id)
