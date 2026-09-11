"""Owned activation recovery and revocation, with no native network requests.

Read and revoke do not require a live device: deletion must not strand a grant
that its owner wants to revoke. Creation eligibility is a separate operation.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from .assessments import SpecificationRegistry
from .contracts import Binding, Identifier, Principal
from .errors import AccessDenied, Conflict, InvalidInput, NotFound, PersistenceError
from .service import BusinessService
from .workflow_activation_contracts import ActivationView, create_activation, revoke_activation
from .workflow_activation_ports import WorkflowActivationRepository
from .workflow_enrollment_contracts import StoredEnrollment
from .workflow_enrollment_ports import WorkflowEnrollmentRepository
from .workflow_plugin_profiles import WorkflowPluginProfileRegistry
from .workflow_provisioning_contracts import NativeUUID


class WorkflowActivationService:
    def __init__(
        self,
        repository: WorkflowActivationRepository,
        enrollment: WorkflowEnrollmentRepository,
        *,
        business: BusinessService,
        profiles: WorkflowPluginProfileRegistry,
        specifications: SpecificationRegistry,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository, self._enrollment = repository, enrollment
        self._business, self._profiles, self._specifications = business, profiles, specifications
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _identifier(value: str) -> None:
        try:
            TypeAdapter(NativeUUID).validate_python(value)
        except ValidationError:
            raise InvalidInput("invalid_activation_identifier") from None

    @staticmethod
    def _owned(principal: Principal, value: ActivationView) -> ActivationView:
        try:
            value = ActivationView.model_validate(value.model_dump())
        except ValidationError:
            raise PersistenceError("stored_activation_invalid") from None
        p = value.enrollment.provisioning
        if (p.workspace_id, p.actor_id) != (principal.workspace_id, principal.actor_id):
            raise AccessDenied("workflow_activation_scope_mismatch")
        return value

    async def _get(self, principal: Principal, activation_id: str) -> ActivationView:
        self._identifier(activation_id)
        value = self._owned(
            principal, await asyncio.to_thread(self._repository.get, principal.workspace_id, activation_id)
        )
        if value.id != activation_id:
            raise PersistenceError("stored_activation_identifier_mismatch")
        return value

    async def get(self, principal: Principal, activation_id: str) -> ActivationView:
        BusinessService.require(principal, "read")
        return await self._get(principal, activation_id)

    async def _load_enrollment(self, principal: Principal, enrollment_id: str) -> StoredEnrollment:
        self._identifier(enrollment_id)
        stored = await asyncio.to_thread(self._enrollment.get, principal.workspace_id, enrollment_id)
        try:
            stored = StoredEnrollment.model_validate(stored)
        except ValidationError:
            raise PersistenceError("stored_enrollment_invalid") from None
        p = stored.view.provisioning
        if (p.workspace_id, p.actor_id, stored.view.id) != (principal.workspace_id, principal.actor_id, enrollment_id):
            raise AccessDenied("workflow_activation_enrollment_scope_mismatch")
        return stored

    async def _find(self, principal: Principal, stored: StoredEnrollment) -> ActivationView | None:
        value = await asyncio.to_thread(self._repository.find_enrollment, principal.workspace_id, stored.view.id)
        if value is None:
            return None
        value = self._owned(principal, value)
        if value.enrollment != stored.view:
            raise Conflict("workflow_activation_enrollment_changed")
        return value

    async def find(self, principal: Principal, enrollment_id: str) -> ActivationView | None:
        BusinessService.require(principal, "read")
        return await self._find(principal, await self._load_enrollment(principal, enrollment_id))

    async def specifications(self, principal: Principal, enrollment_id: str) -> tuple[str, ...]:
        BusinessService.require(principal, "read")
        stored = await self._load_enrollment(principal, enrollment_id)
        return self._specifications.list_revisions(principal.workspace_id, stored.view.provisioning.scenario)

    async def start(self, principal: Principal, enrollment_id: str, *, specification_revision: str) -> ActivationView:
        """Build a pinned binding server-side; repository rechecks mutable inputs atomically."""
        BusinessService.require(principal, "manage")
        try:
            TypeAdapter(Identifier).validate_python(specification_revision)
        except ValidationError:
            raise InvalidInput("invalid_activation_specification") from None
        stored = await self._load_enrollment(principal, enrollment_id)
        existing = await self._find(principal, stored)
        if existing is not None:
            if existing.binding.specification_revision != specification_revision:
                raise Conflict("workflow_activation_specification_changed")
            return existing
        enrollment = stored.view
        p = enrollment.provisioning
        if enrollment.state != "token_stored" or enrollment.publication is None or enrollment.credential is None:
            raise Conflict("workflow_activation_token_required")
        device = await asyncio.to_thread(self._business.get_device, principal, p.device_id)
        if (device.workspace_id, device.id) != (principal.workspace_id, p.device_id):
            raise AccessDenied("workflow_activation_device_scope_mismatch")
        if device.deleted_at is not None:
            raise NotFound()
        spec = self._specifications.resolve(principal.workspace_id, p.scenario, specification_revision)
        if (spec.workspace_id, spec.scenario, spec.specification_revision) != (
            principal.workspace_id,
            p.scenario,
            specification_revision,
        ):
            raise Conflict("workflow_activation_specification_mismatch")
        try:
            old = Binding.model_validate(
                await asyncio.to_thread(self._business.get_binding, principal, p.device_id, p.scenario)
            )
        except NotFound:
            old = None
        if old is None:
            if p.expected_binding_revision is not None:
                raise Conflict("workflow_activation_binding_changed")
        elif (old.workspace_id, old.device_id, old.scenario, old.revision, old.active_run_id) != (
            principal.workspace_id,
            p.device_id,
            p.scenario,
            p.expected_binding_revision,
            None,
        ):
            raise Conflict("workflow_activation_binding_changed")
        now = self._clock()
        if old is not None and now < old.updated_at:
            raise Conflict("workflow_activation_invalid_time")
        binding = Binding(
            id=old.id if old else str(uuid4()),
            workspace_id=principal.workspace_id,
            device_id=p.device_id,
            scenario=p.scenario,
            revision=(p.expected_binding_revision or 0) + 1,
            app_id=p.app_id,
            workflow_id=UUID(enrollment.publication.workflow_id),
            specification_revision=specification_revision,
            secret_ref=enrollment.credential.secret_ref,
            source_id=p.source_id,
            source_revision=p.source_revision,
            read_id=p.read_id,
            read_revision=p.read_revision,
            created_at=old.created_at if old else now,
            updated_at=now,
        )
        profile = self._profiles.resolve_profile(
            principal.workspace_id, configuration_ref=p.config_ref, configuration_revision=p.config_revision
        )
        candidate = create_activation(
            enrollment, binding, profile, activation_id=str(uuid4()), actor_id=principal.actor_id, now=now
        )
        value = self._owned(
            principal, await asyncio.to_thread(self._repository.create, candidate, actor_id=principal.actor_id)
        )
        if value != candidate:
            raise PersistenceError("workflow_activation_creation_not_confirmed")
        return value

    async def revoke(self, principal: Principal, activation_id: str, *, expected_revision: int) -> ActivationView:
        BusinessService.require(principal, "manage")
        old = await self._get(principal, activation_id)
        revoke_activation(old, expected_revision=expected_revision, actor_id=principal.actor_id, now=old.updated_at)
        value = self._owned(
            principal,
            await asyncio.to_thread(
                self._repository.revoke,
                principal.workspace_id,
                activation_id,
                expected_revision=expected_revision,
                actor_id=principal.actor_id,
            ),
        )
        expected = revoke_activation(
            old, expected_revision=expected_revision, actor_id=principal.actor_id, now=value.updated_at
        )
        if value != expected:
            raise PersistenceError("workflow_activation_revocation_not_confirmed")
        return value
