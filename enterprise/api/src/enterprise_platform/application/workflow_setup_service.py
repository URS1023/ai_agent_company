"""Durable draft creation: freeze before I/O and never repeat an ambiguous import.

Only the queued-to-importing CAS owner may call the native importer. Cancellation
leaves that durable claim intact. Replays use the frozen command, not moving source
or binding heads; importing and final states require explicit future reconciliation.
"""

import asyncio
import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .contracts import Device, Identifier, Page, Principal, Scenario, canonical_hash
from .errors import AccessDenied, Conflict, InvalidInput, NotFound, PersistenceError
from .service import BusinessService
from .source_ports import SourceManagement
from .workflow_setup_contracts import SetupRequest, SetupView
from .workflow_setup_execution import (
    NativeImportOutcome,
    NativeSetupSession,
    SetupImportRejected,
    WorkflowDraftImporter,
)
from .workflow_setup_ports import StoredSetup, WorkflowSetupRepository


class WorkflowSetupService:
    _repository: WorkflowSetupRepository
    _business: BusinessService
    _sources: SourceManagement
    _importer: WorkflowDraftImporter
    _clock: Callable[[], datetime]
    _id: Callable[[], str]

    def __init__(
        self,
        repository: WorkflowSetupRepository,
        business: BusinessService,
        sources: SourceManagement,
        importer: WorkflowDraftImporter,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository, self._business, self._sources, self._importer = repository, business, sources, importer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id = id_factory or (lambda: str(uuid4()))

    @staticmethod
    def _identifier(value: str) -> str:
        try:
            return TypeAdapter(Identifier).validate_python(value)
        except ValidationError:
            raise InvalidInput("invalid_workflow_setup_identifier") from None

    @staticmethod
    def _scenario(value: Scenario) -> Scenario:
        try:
            return TypeAdapter(Scenario).validate_python(value)
        except ValidationError:
            raise InvalidInput("invalid_workflow_setup_scenario") from None

    async def _device(self, principal: Principal, device_id: str) -> Device:
        device = await asyncio.to_thread(self._business.get_device, principal, device_id)
        if (device.workspace_id, device.id) != (principal.workspace_id, device_id):
            raise AccessDenied("workflow_setup_device_scope_mismatch")
        if device.deleted_at is not None:
            raise NotFound()
        return device

    @staticmethod
    def _replay(principal: Principal, setup: StoredSetup, request_hash: str) -> None:
        if setup.view.workspace_id != principal.workspace_id:
            raise AccessDenied()
        if setup.actor_id != principal.actor_id or not hmac.compare_digest(setup.request_hash, request_hash):
            raise Conflict("idempotency_key_reused")

    async def start(
        self,
        principal: Principal,
        session: NativeSetupSession,
        device_id: str,
        scenario: Scenario,
        payload: SetupRequest,
        request_key: str,
    ) -> SetupView:
        BusinessService.require(principal, "manage")
        device_id, scenario = self._identifier(device_id), self._scenario(scenario)
        if not isinstance(request_key, str) or re.fullmatch(r"[\x21-\x7e]{1,128}", request_key) is None:
            raise InvalidInput("invalid_idempotency_key")
        try:
            payload = SetupRequest.model_validate(payload.model_dump())
        except (ValidationError, AttributeError):
            raise InvalidInput("invalid_workflow_setup_request") from None
        request_hash = canonical_hash(
            {
                "actor_id": principal.actor_id,
                "device_id": device_id,
                "scenario": scenario,
                "command": payload.model_dump(mode="json"),
            }
        )
        stored = await asyncio.to_thread(self._repository.find_request, principal.workspace_id, request_key)
        if stored is None:
            try:
                stored = await self._create(principal, device_id, scenario, payload, request_key, request_hash)
            except Conflict:
                # Concurrent creation can commit while a mutable-head check runs.
                stored = await asyncio.to_thread(self._repository.find_request, principal.workspace_id, request_key)
                if stored is None:
                    raise
        self._replay(principal, stored, request_hash)
        if (stored.view.device_id, stored.view.scenario) != (device_id, scenario):
            raise AccessDenied()
        await self._device(principal, device_id)
        return await self._import(principal, session, stored)

    async def _create(
        self,
        principal: Principal,
        device_id: str,
        scenario: Scenario,
        payload: SetupRequest,
        request_key: str,
        request_hash: str,
    ) -> StoredSetup:
        device = await self._device(principal, device_id)
        source = await asyncio.to_thread(self._sources.get_source, principal, payload.source_id)
        if (source.workspace_id, source.source_id) != (principal.workspace_id, payload.source_id):
            raise AccessDenied("workflow_setup_source_scope_mismatch")
        if device_id not in source.device_ids:
            raise AccessDenied("workflow_setup_source_device_mismatch")
        if source.revision != payload.expected_source_revision:
            raise Conflict("workflow_setup_source_revision_changed")
        try:
            binding = await asyncio.to_thread(self._business.get_binding, principal, device_id, scenario)
        except NotFound:
            binding_revision = None
        else:
            if (binding.workspace_id, binding.device_id, binding.scenario) != (
                principal.workspace_id,
                device_id,
                scenario,
            ):
                raise AccessDenied("workflow_setup_binding_scope_mismatch")
            binding_revision = binding.revision
        if binding_revision != payload.expected_binding_revision:
            raise Conflict("workflow_setup_binding_revision_changed")
        now = self._clock()
        suffix = " - Alert workflow" if scenario == "alert" else " - Quality workflow"
        view = SetupView(
            id=self._id(),
            workspace_id=principal.workspace_id,
            device_id=device_id,
            scenario=scenario,
            source_id=source.source_id,
            source_revision=source.source_revision,
            read_id=source.read_id,
            read_revision=source.read_revision,
            expected_source_revision=payload.expected_source_revision,
            expected_binding_revision=payload.expected_binding_revision,
            revision=1,
            state="queued",
            name=device.name[: 200 - len(suffix)] + suffix,
            created_at=now,
            updated_at=now,
        )
        return await asyncio.to_thread(
            self._repository.create,
            StoredSetup(view, principal.actor_id, request_key, request_hash),
            actor_id=principal.actor_id,
        )

    async def _import(self, principal: Principal, session: NativeSetupSession, stored: StoredSetup) -> SetupView:
        if stored.view.state != "queued":
            return stored.view
        nonce = str(uuid4())
        try:
            claimed = await asyncio.to_thread(
                self._repository.claim_import,
                principal.workspace_id,
                stored.view.id,
                expected_revision=stored.view.revision,
                nonce=nonce,
                actor_id=principal.actor_id,
            )
        except Conflict:
            return await self.get(principal, stored.view.id)
        if claimed.import_nonce != nonce or claimed.view.state != "importing":
            raise PersistenceError("workflow_setup_claim_not_confirmed")
        self._replay(principal, claimed, stored.request_hash)
        try:
            outcome = await self._importer.import_default(
                principal,
                session,
                setup_id=claimed.view.id,
                scenario=claimed.view.scenario,
                name=claimed.view.name,
            )
            # Validate protocol doubles/adapters before allowing an invalid final write.
            SetupView.model_validate(
                claimed.view.model_dump()
                | {
                    "state": outcome.state,
                    "app_id": outcome.app_id,
                    "import_id": outcome.import_id,
                    "reason_code": outcome.reason_code,
                }
            )
            if outcome.state not in {"draft_ready", "confirmation_required", "failed", "uncertain"}:
                raise ValueError("Invalid final state")
        except SetupImportRejected:
            outcome = NativeImportOutcome("failed", reason_code="native_workflow_setup_unavailable")
        except Exception:
            outcome = NativeImportOutcome("uncertain", reason_code="native_workflow_setup_uncertain")
        finished = await asyncio.to_thread(
            self._repository.finish_import,
            principal.workspace_id,
            stored.view.id,
            nonce=nonce,
            state=outcome.state,
            app_id=outcome.app_id,
            import_id=outcome.import_id,
            reason_code=outcome.reason_code,
            actor_id=principal.actor_id,
        )
        self._replay(principal, finished, stored.request_hash)
        if finished.view.state != outcome.state or finished.view.id != stored.view.id:
            raise PersistenceError("workflow_setup_outcome_not_confirmed")
        return finished.view

    async def get(self, principal: Principal, setup_id: str) -> SetupView:
        BusinessService.require(principal, "read")
        stored = await asyncio.to_thread(self._repository.get, principal.workspace_id, self._identifier(setup_id))
        if stored.view.workspace_id != principal.workspace_id or stored.view.id != setup_id:
            raise AccessDenied()
        await self._device(principal, stored.view.device_id)
        return stored.view

    async def list(
        self,
        principal: Principal,
        device_id: str,
        scenario: Scenario,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> Page[SetupView]:
        BusinessService.require(principal, "read")
        device_id, scenario = self._identifier(device_id), self._scenario(scenario)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("invalid_pagination")
        await self._device(principal, device_id)
        page = await asyncio.to_thread(
            self._repository.list,
            principal.workspace_id,
            device_id=device_id,
            scenario=scenario,
            offset=offset,
            limit=limit,
        )
        for view in page.items:
            if (view.workspace_id, view.device_id, view.scenario) != (principal.workspace_id, device_id, scenario):
                raise AccessDenied()
        return page
