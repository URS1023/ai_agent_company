"""Authenticated single-step enrollment; committed claims precede native I/O."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .contracts import Principal
from .errors import AccessDenied, Conflict, InvalidInput, NotFound, PersistenceError
from .service import BusinessService
from .workflow_enrollment_contracts import (
    EnrollmentPhase,
    EnrollmentView,
    StoredEnrollment,
    claim_enrollment,
    finish_enrollment_token,
    finish_enrollment_verification,
    initialize_enrollment,
)
from .workflow_enrollment_execution import EnrollmentExecutionResult, WorkflowEnrollmentExecutor
from .workflow_enrollment_ports import WorkflowEnrollmentRepository
from .workflow_provisioning_contracts import NativeUUID, ProvisioningView
from .workflow_provisioning_service import WorkflowProvisioningService
from .workflow_setup_execution import NativeSetupSession


class WorkflowEnrollmentService:
    def __init__(
        self,
        repository: WorkflowEnrollmentRepository,
        business: BusinessService,
        provisioning: WorkflowProvisioningService,
        executor: WorkflowEnrollmentExecutor,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository, self._business, self._provisioning, self._executor = (
            repository,
            business,
            provisioning,
            executor,
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _identifier(value: str) -> None:
        try:
            TypeAdapter(NativeUUID).validate_python(value)
        except ValidationError:
            raise InvalidInput("invalid_enrollment_identifier") from None

    @staticmethod
    def _owned(principal: Principal, stored: StoredEnrollment) -> StoredEnrollment:
        try:
            stored = StoredEnrollment.model_validate(stored)
        except ValidationError:
            raise PersistenceError("stored_enrollment_invalid") from None
        if (stored.view.provisioning.workspace_id, stored.view.provisioning.actor_id) != (
            principal.workspace_id,
            principal.actor_id,
        ):
            raise AccessDenied("workflow_enrollment_scope_mismatch")
        return stored

    async def _device(self, principal: Principal, device_id: str) -> None:
        device = await asyncio.to_thread(self._business.get_device, principal, device_id)
        if (device.workspace_id, device.id) != (principal.workspace_id, device_id):
            raise AccessDenied("workflow_enrollment_device_scope_mismatch")
        if device.deleted_at is not None:
            raise NotFound()

    async def _get(self, principal: Principal, enrollment_id: str) -> StoredEnrollment:
        self._identifier(enrollment_id)
        stored = self._owned(
            principal, await asyncio.to_thread(self._repository.get, principal.workspace_id, enrollment_id)
        )
        if stored.view.id != enrollment_id:
            raise AccessDenied("workflow_enrollment_identifier_mismatch")
        await self._device(principal, stored.view.provisioning.device_id)
        return stored

    async def get(self, principal: Principal, enrollment_id: str) -> EnrollmentView:
        BusinessService.require(principal, "read")
        return (await self._get(principal, enrollment_id)).view

    async def start(self, principal: Principal, provisioning_id: str) -> EnrollmentView:
        BusinessService.require(principal, "manage")
        self._identifier(provisioning_id)
        provisioning = ProvisioningView.model_validate(await self._provisioning.get(principal, provisioning_id))
        if (provisioning.id, provisioning.workspace_id, provisioning.actor_id) != (
            provisioning_id,
            principal.workspace_id,
            principal.actor_id,
        ):
            raise AccessDenied("workflow_enrollment_provisioning_scope_mismatch")
        await self._device(principal, provisioning.device_id)
        existing = await asyncio.to_thread(self._repository.find_provisioning, principal.workspace_id, provisioning_id)
        if existing is not None:
            existing = self._owned(principal, existing)
            if existing.view.provisioning != provisioning:
                raise Conflict("workflow_enrollment_provisioning_changed")
            return existing.view
        value = initialize_enrollment(
            provisioning, enrollment_id=str(uuid4()), secret_ref=str(uuid4()), now=self._clock()
        )
        stored = self._owned(
            principal, await asyncio.to_thread(self._repository.create, value, actor_id=principal.actor_id)
        )
        if stored.view.provisioning != provisioning:
            raise PersistenceError("workflow_enrollment_creation_not_confirmed")
        return stored.view

    async def find(self, principal: Principal, provisioning_id: str) -> EnrollmentView | None:
        """Restore an existing enrollment without creating or advancing anything."""
        BusinessService.require(principal, "read")
        self._identifier(provisioning_id)
        provisioning = ProvisioningView.model_validate(await self._provisioning.get(principal, provisioning_id))
        if (provisioning.id, provisioning.workspace_id, provisioning.actor_id) != (
            provisioning_id,
            principal.workspace_id,
            principal.actor_id,
        ):
            raise AccessDenied("workflow_enrollment_provisioning_scope_mismatch")
        await self._device(principal, provisioning.device_id)
        stored = await asyncio.to_thread(self._repository.find_provisioning, principal.workspace_id, provisioning_id)
        if stored is None:
            return None
        stored = self._owned(principal, stored)
        if stored.view.provisioning != provisioning:
            raise Conflict("workflow_enrollment_provisioning_changed")
        return stored.view

    async def advance(
        self,
        principal: Principal,
        session: NativeSetupSession,
        enrollment_id: str,
        *,
        expected_revision: int,
    ) -> EnrollmentView:
        BusinessService.require(principal, "manage")
        old = await self._get(principal, enrollment_id)
        if type(expected_revision) is not int or expected_revision != old.view.revision:
            raise Conflict("workflow_enrollment_revision_conflict")
        phase: EnrollmentPhase
        if old.view.state == "pending_verification":
            phase = "verify_publication"
        elif old.view.state == "verified":
            phase = "issue_token"
        else:
            raise Conflict("workflow_enrollment_not_claimable")
        nonce = str(uuid4())
        claimed = self._owned(
            principal,
            await asyncio.to_thread(
                self._repository.claim,
                principal.workspace_id,
                enrollment_id,
                phase=phase,
                nonce=nonce,
                expected_revision=expected_revision,
                actor_id=principal.actor_id,
            ),
        )
        expected = claim_enrollment(
            old,
            phase=phase,
            nonce=nonce,
            expected_revision=expected_revision,
            actor_id=principal.actor_id,
            now=claimed.view.updated_at,
        )
        if claimed != expected:
            raise PersistenceError("workflow_enrollment_claim_not_confirmed")
        try:
            outcome = EnrollmentExecutionResult.model_validate(
                await self._executor.execute(principal, session, claimed.view)
            )
        except Exception:
            outcome = EnrollmentExecutionResult(
                state="rejected" if phase == "verify_publication" else "uncertain",
                reason_code="workflow_enrollment_execution_unconfirmed",
            )
        # CancelledError is not caught: durable claims block another native POST.
        if phase == "verify_publication":
            if outcome.state not in {"verified", "rejected"}:
                raise PersistenceError("workflow_enrollment_outcome_phase_mismatch")
            finished = await asyncio.to_thread(
                self._repository.finish_verification,
                principal.workspace_id,
                enrollment_id,
                publication=outcome.publication,
                reason_code=outcome.reason_code,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
            )
            expected = finish_enrollment_verification(
                claimed,
                publication=outcome.publication,
                reason_code=outcome.reason_code,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
                now=finished.view.updated_at,
            )
        elif outcome.state == "issued" and outcome.issued is not None:
            issued = outcome.issued
            if (issued.workspace_id, issued.app_id) != (principal.workspace_id, claimed.view.provisioning.app_id):
                raise PersistenceError("workflow_enrollment_token_scope_mismatch")
            finished = await asyncio.to_thread(
                self._repository.store_issued_token,
                principal.workspace_id,
                enrollment_id,
                issued=issued,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
            )
            expected = finish_enrollment_token(
                claimed,
                state="token_stored",
                native_token_id=issued.token_id,
                credential=finished.view.credential,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
                now=finished.view.updated_at,
            )
        elif outcome.state in {"rejected", "uncertain"} and outcome.reason_code is not None:
            failure_state: Literal["rejected", "uncertain"] = "rejected" if outcome.state == "rejected" else "uncertain"
            finished = await asyncio.to_thread(
                self._repository.finish_token_failure,
                principal.workspace_id,
                enrollment_id,
                state=failure_state,
                reason_code=outcome.reason_code,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
            )
            expected = finish_enrollment_token(
                claimed,
                state=failure_state,
                reason_code=outcome.reason_code,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
                now=finished.view.updated_at,
            )
        else:
            raise PersistenceError("workflow_enrollment_outcome_phase_mismatch")
        finished = self._owned(principal, finished)
        if finished != expected:
            raise PersistenceError("workflow_enrollment_finish_not_confirmed")
        return finished.view
