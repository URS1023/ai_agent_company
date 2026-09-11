"""Pure activation receipts; creation is not proof of a committed device binding.

Persistence must atomically validate and write the binding plus activation. Runtime
resolution must also check live activation, credential and binding revisions.
No signing secret or plaintext Service API token belongs in these receipts.
"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, StrictBool, model_validator

from .contracts import Binding, Contract
from .errors import AccessDenied, Conflict
from .workflow_enrollment_contracts import EnrollmentView
from .workflow_plugin_profiles import PluginCredentialProfile, derive_execution_key
from .workflow_provisioning_contracts import NativeUUID, PrepareCredentialReceipt, Revision


class ActivationView(Contract):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True, revalidate_instances="always"
    )
    id: NativeUUID
    enrollment: EnrollmentView
    binding: Binding
    key_id: str = Field(pattern=r"^ep-[0-9a-f]{64}$")
    revision: Revision
    active: StrictBool
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        enrollment = EnrollmentView.model_validate(self.enrollment.model_dump())
        binding = Binding.model_validate(self.binding.model_dump())
        if enrollment.state != "token_stored" or enrollment.publication is None or enrollment.credential is None:
            raise ValueError("Stored credential and publication required")
        p = enrollment.provisioning
        if (
            binding.workspace_id,
            binding.app_id,
            binding.device_id,
            binding.scenario,
            binding.workflow_id,
            binding.secret_ref,
            binding.source_id,
            binding.source_revision,
            binding.read_id,
            binding.read_revision,
            binding.revision,
            binding.active_run_id,
        ) != (
            p.workspace_id,
            p.app_id,
            p.device_id,
            p.scenario,
            UUID(enrollment.publication.workflow_id),
            enrollment.credential.secret_ref,
            p.source_id,
            p.source_revision,
            p.read_id,
            p.read_revision,
            (p.expected_binding_revision or 0) + 1,
            None,
        ):
            raise ValueError("Exact idle binding transition required")
        if not enrollment.updated_at <= binding.updated_at <= self.created_at <= self.updated_at:
            raise ValueError("Invalid activation chronology")
        if binding.created_at > binding.updated_at:
            raise ValueError("Invalid binding chronology")
        if (self.active and self.revision != 1) or (not self.active and self.revision != 2):
            raise ValueError("Activation may only be created then revoked")
        return self


def create_activation(
    enrollment: EnrollmentView,
    binding: Binding,
    profile: PluginCredentialProfile,
    *,
    activation_id: str,
    actor_id: str,
    now: datetime,
) -> ActivationView:
    enrollment = EnrollmentView.model_validate(enrollment.model_dump())
    p = enrollment.provisioning
    if actor_id != p.actor_id:
        raise AccessDenied("workflow_activation_actor_mismatch")
    if enrollment.state != "token_stored":
        raise Conflict("workflow_activation_token_required")
    prepared = p.phases[1].receipt
    if not isinstance(prepared, PrepareCredentialReceipt) or (
        profile.workspace_id,
        profile.config_ref,
        profile.config_revision,
        profile.expected_plugin_unique_identifier,
    ) != (p.workspace_id, p.config_ref, p.config_revision, prepared.plugin_unique_identifier):
        raise Conflict("workflow_activation_profile_mismatch")
    key = derive_execution_key(profile, app_id=p.app_id, node_ids=frozenset({"assessment"}))
    return ActivationView(
        id=activation_id,
        enrollment=enrollment,
        binding=binding,
        key_id=key.key_id,
        revision=1,
        active=True,
        created_at=now,
        updated_at=now,
    )


def revoke_activation(
    value: ActivationView,
    *,
    expected_revision: int,
    actor_id: str,
    now: datetime,
) -> ActivationView:
    value = ActivationView.model_validate(value.model_dump())
    if actor_id != value.enrollment.provisioning.actor_id:
        raise AccessDenied("workflow_activation_actor_mismatch")
    if type(expected_revision) is not int or expected_revision != value.revision or not value.active:
        raise Conflict("workflow_activation_revision_conflict")
    if now.tzinfo is None or now < value.updated_at:
        raise Conflict("workflow_activation_invalid_time")
    return ActivationView.model_validate(value.model_dump() | {"active": False, "revision": 2, "updated_at": now})
