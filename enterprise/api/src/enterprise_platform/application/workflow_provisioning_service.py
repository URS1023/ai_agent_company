"""One authenticated phase per advance, with durable ownership before native I/O.

Cancellation and failed final writes retain the repository claim. There is no
lease takeover, automatic POST retry, or executable/device-ready transition.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .contracts import Identifier, Page, Principal
from .errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput, NotFound, PersistenceError
from .service import BusinessService
from .workflow_plugin_profiles import PluginProfileChoice, WorkflowPluginProfileRegistry
from .workflow_provisioning_contracts import (
    BindCredentialCommand,
    BindCredentialReceipt,
    NativeUUID,
    PhaseCommand,
    PhaseOutcome,
    PrepareCredentialCommand,
    PrepareCredentialReceipt,
    ProvisioningView,
    PublishCommand,
    ReadDraftCommand,
    ReadDraftReceipt,
    RequestKey,
    Revision,
    StoredProvisioning,
    claim_provisioning,
    finish_provisioning,
    initialize_provisioning,
)
from .workflow_provisioning_execution import WorkflowProvisioningExecutor
from .workflow_provisioning_ports import WorkflowProvisioningRepository
from .workflow_setup_contracts import SetupView
from .workflow_setup_execution import NativeSetupSession
from .workflow_setup_service import WorkflowSetupService


class WorkflowProvisioningService:
    _repository: WorkflowProvisioningRepository
    _business: BusinessService
    _setups: WorkflowSetupService
    _executor: WorkflowProvisioningExecutor
    _clock: Callable[[], datetime]
    _profiles: WorkflowPluginProfileRegistry | None

    def __init__(
        self,
        repository: WorkflowProvisioningRepository,
        business: BusinessService,
        setups: WorkflowSetupService,
        executor: WorkflowProvisioningExecutor,
        *,
        clock: Callable[[], datetime] | None = None,
        profiles: WorkflowPluginProfileRegistry | None = None,
    ) -> None:
        self._repository, self._business, self._setups, self._executor = repository, business, setups, executor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._profiles = profiles

    @staticmethod
    def _owned(principal: Principal, stored: StoredProvisioning) -> StoredProvisioning:
        try:
            stored = StoredProvisioning.model_validate(stored)
        except ValidationError:
            raise PersistenceError("stored_provisioning_invalid") from None
        if (stored.view.workspace_id, stored.view.actor_id) != (principal.workspace_id, principal.actor_id):
            raise AccessDenied("workflow_provisioning_scope_mismatch")
        return stored

    async def _device(self, principal: Principal, device_id: str) -> None:
        device = await asyncio.to_thread(self._business.get_device, principal, device_id)
        if (device.workspace_id, device.id) != (principal.workspace_id, device_id):
            raise AccessDenied("workflow_provisioning_device_scope_mismatch")
        if device.deleted_at is not None:
            raise NotFound()

    async def _get(self, principal: Principal, provisioning_id: str) -> StoredProvisioning:
        try:
            TypeAdapter(NativeUUID).validate_python(provisioning_id)
        except ValidationError:
            raise InvalidInput("invalid_provisioning_identifier") from None
        stored = self._owned(
            principal, await asyncio.to_thread(self._repository.get, principal.workspace_id, provisioning_id)
        )
        if stored.view.id != provisioning_id:
            raise AccessDenied("workflow_provisioning_identifier_mismatch")
        await self._device(principal, stored.view.device_id)
        return stored

    async def get(self, principal: Principal, provisioning_id: str) -> ProvisioningView:
        BusinessService.require(principal, "read")
        return (await self._get(principal, provisioning_id)).view

    async def _setup(self, principal: Principal, setup_id: str) -> SetupView:
        try:
            TypeAdapter(Identifier).validate_python(setup_id)
        except ValidationError:
            raise InvalidInput("invalid_setup_identifier") from None
        setup = await self._setups.get(principal, setup_id)
        if (setup.workspace_id, setup.id) != (principal.workspace_id, setup_id):
            raise AccessDenied("workflow_provisioning_setup_scope_mismatch")
        await self._device(principal, setup.device_id)
        return setup

    async def list(
        self, principal: Principal, setup_id: str, *, offset: int = 0, limit: int = 20
    ) -> Page[ProvisioningView]:
        """Recover owned operations; actor filtering occurs before storage pagination."""
        BusinessService.require(principal, "read")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("invalid_pagination")
        setup = await self._setup(principal, setup_id)
        page = await asyncio.to_thread(
            self._repository.list,
            principal.workspace_id,
            setup_id=setup_id,
            actor_id=principal.actor_id,
            offset=offset,
            limit=limit,
        )
        if (page.offset, page.limit) != (offset, limit) or len(page.items) > limit:
            raise PersistenceError("workflow_provisioning_page_invalid")
        for view in page.items:
            if (view.workspace_id, view.actor_id, view.setup_id, view.device_id, view.app_id, view.scenario) != (
                principal.workspace_id,
                principal.actor_id,
                setup_id,
                setup.device_id,
                setup.app_id,
                setup.scenario,
            ):
                raise AccessDenied("workflow_provisioning_history_scope_mismatch")
        return page

    async def list_profiles(self, principal: Principal, setup_id: str) -> tuple[PluginProfileChoice, ...]:
        """Expose selectable labels only after checking the confirmed setup scope."""
        BusinessService.require(principal, "manage")
        setup = await self._setup(principal, setup_id)
        if setup.state != "draft_ready":
            raise Conflict("workflow_provisioning_draft_required")
        if self._profiles is None:
            raise DependencyUnavailable("workflow_provisioning_profiles_unavailable")
        return self._profiles.list_profiles(principal.workspace_id)

    async def start(
        self,
        principal: Principal,
        setup_id: str,
        *,
        config_ref: str,
        config_revision: int,
        request_key: str,
    ) -> ProvisioningView:
        BusinessService.require(principal, "manage")
        try:
            TypeAdapter(Identifier).validate_python(setup_id)
            TypeAdapter(Identifier).validate_python(config_ref)
            TypeAdapter(Revision).validate_python(config_revision)
            TypeAdapter(RequestKey).validate_python(request_key)
        except ValidationError:
            raise InvalidInput("invalid_provisioning_request") from None
        existing = await asyncio.to_thread(self._repository.find_request, principal.workspace_id, request_key)
        if existing is not None:
            existing = self._owned(principal, existing)
            if (
                existing.request_key,
                existing.view.setup_id,
                existing.view.config_ref,
                existing.view.config_revision,
            ) != (request_key, setup_id, config_ref, config_revision):
                raise Conflict("idempotency_key_reused")
            await self._device(principal, existing.view.device_id)
            return existing.view
        setup = await self._setups.get(principal, setup_id)
        if (setup.workspace_id, setup.id) != (principal.workspace_id, setup_id):
            raise AccessDenied("workflow_provisioning_setup_scope_mismatch")
        await self._device(principal, setup.device_id)
        initial = initialize_provisioning(
            setup,
            actor_id=principal.actor_id,
            operation_id=str(uuid4()),
            config_ref=config_ref,
            config_revision=config_revision,
            request_key=request_key,
            now=self._clock(),
        )
        created = self._owned(
            principal, await asyncio.to_thread(self._repository.create, initial, actor_id=principal.actor_id)
        )
        if created.request_key != initial.request_key or created.request_hash != initial.request_hash:
            raise Conflict("idempotency_key_reused")
        return created.view

    @staticmethod
    def _next(view: ProvisioningView) -> PhaseCommand:
        if view.phases and view.phases[-1].state == "queued":
            return view.phases[-1].command
        operation_id = str(uuid4())
        if not view.phases:
            return ReadDraftCommand(operation_id=operation_id, app_id=view.app_id)
        previous = view.phases[-1].receipt
        if isinstance(previous, ReadDraftReceipt):
            return PrepareCredentialCommand(
                operation_id=operation_id,
                app_id=view.app_id,
                config_ref=view.config_ref,
                config_revision=view.config_revision,
            )
        if isinstance(previous, PrepareCredentialReceipt):
            read = view.phases[0].receipt
            if not isinstance(read, ReadDraftReceipt):
                raise PersistenceError("workflow_provisioning_read_missing")
            return BindCredentialCommand(
                operation_id=operation_id,
                app_id=view.app_id,
                draft_id=read.draft_id,
                credential_id=previous.credential_id,
                expected_draft_hash=read.draft_hash,
            )
        if isinstance(previous, BindCredentialReceipt):
            return PublishCommand(
                operation_id=operation_id, app_id=view.app_id, expected_draft_hash=previous.draft_hash
            )
        raise Conflict("workflow_provisioning_not_claimable")

    async def advance(
        self, principal: Principal, session: NativeSetupSession, provisioning_id: str, *, expected_revision: int
    ) -> ProvisioningView:
        BusinessService.require(principal, "manage")
        stored = await self._get(principal, provisioning_id)
        if type(expected_revision) is not int or expected_revision != stored.view.revision:
            raise Conflict("workflow_provisioning_revision_conflict")
        if stored.view.state != "in_progress" or stored.claim_nonce is not None:
            raise Conflict("workflow_provisioning_not_claimable")
        command, nonce = self._next(stored.view), str(uuid4())
        claimed = self._owned(
            principal,
            await asyncio.to_thread(
                self._repository.claim,
                principal.workspace_id,
                provisioning_id,
                command=command,
                nonce=nonce,
                expected_revision=expected_revision,
                actor_id=principal.actor_id,
            ),
        )
        expected = claim_provisioning(
            stored,
            command=command,
            nonce=nonce,
            expected_revision=expected_revision,
            actor_id=principal.actor_id,
            now=claimed.view.updated_at,
        )
        if claimed != expected:
            raise PersistenceError("workflow_provisioning_claim_not_confirmed")
        try:
            outcome = PhaseOutcome.model_validate(await self._executor.execute(principal, session, command))
        except Exception:
            outcome = PhaseOutcome(state="uncertain", reason_code="workflow_provisioning_execution_unconfirmed")
        finished = self._owned(
            principal,
            await asyncio.to_thread(
                self._repository.finish,
                principal.workspace_id,
                provisioning_id,
                outcome=outcome,
                nonce=nonce,
                expected_revision=claimed.view.revision,
                actor_id=principal.actor_id,
            ),
        )
        expected = finish_provisioning(
            claimed,
            outcome=outcome,
            nonce=nonce,
            expected_revision=claimed.view.revision,
            actor_id=principal.actor_id,
            now=finished.view.updated_at,
        )
        if finished != expected:
            raise PersistenceError("workflow_provisioning_finish_not_confirmed")
        return finished.view
