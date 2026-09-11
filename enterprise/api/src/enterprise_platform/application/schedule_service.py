"""Authorized schedule configuration and tick enqueue; no workflow dispatch.

The injected authority must resolve current service grants and inspect the exact
published binding for native timers, failing on uncertainty. Configuration checks
are not a durable cross-service ownership lease: enqueue must repeat authorization
and fence the binding/cursor atomically. This service is not wired by default.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, Self
from uuid import uuid4

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from .contracts import Binding, Contract, Device, Identifier, Principal, RunSpec, Scenario
from .errors import AccessDenied, Conflict, DependencyUnavailable, EnterpriseError, InvalidInput
from .input_capture import RegisteredReadRegistry
from .schedule_read_preflight import validate_schedule_read
from .scheduling import IntervalSchedule, ScheduleCommit, ScheduleScanCursor, plan_tick, prepare_schedule_run
from .service import BusinessService


class CreateSchedule(Contract):
    expected_binding_revision: int = Field(ge=1)
    service_actor_id: Identifier
    anchor_at: AwareDatetime
    interval_seconds: int = Field(ge=1, le=86400)
    window_seconds: int = Field(ge=1, le=604800)
    grace_seconds: int = Field(ge=0)
    missed_policy: Literal["skip", "coalesce"]

    @model_validator(mode="after")
    def validate_grace(self) -> Self:
        if self.grace_seconds >= self.interval_seconds:
            raise ValueError("Grace must be smaller than the interval")
        return self


class ScheduleSwitch(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    enabled: StrictBool


class ScheduleActorChoice(Contract):
    actor_id: Identifier
    display_name: str


class ServiceGrant(Contract):
    principal: Principal
    device_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=10000)
    scenarios: tuple[Scenario, ...] = Field(min_length=1, max_length=2)
    enabled: bool
    expires_at: AwareDatetime


class ScheduleAuthority(Protocol):
    def get_grant(self, workspace_id: str, actor_id: str) -> ServiceGrant | None: ...
    def has_native_timer(self, binding: Binding) -> bool: ...


class ScheduleRepository(Protocol):
    def find_for_device(self, workspace_id: str, device_id: str, scenario: Scenario) -> IntervalSchedule | None: ...

    def list_due(
        self,
        workspace_id: str,
        *,
        now: datetime,
        limit: int = 100,
        service_actor_id: str | None = None,
        after: ScheduleScanCursor | None = None,
    ) -> tuple[IntervalSchedule, ...]: ...
    def commit_tick(self, expected: IntervalSchedule, spec: RunSpec | None) -> ScheduleCommit: ...
    def create(self, schedule: IntervalSchedule, *, actor_id: str) -> IntervalSchedule: ...
    def get(self, workspace_id: str, schedule_id: str) -> IntervalSchedule: ...
    def set_enabled(
        self,
        workspace_id: str,
        schedule_id: str,
        *,
        expected_revision: int,
        enabled: bool,
        actor_id: str,
    ) -> IntervalSchedule: ...


class ScheduleService:
    def __init__(
        self,
        repository: ScheduleRepository,
        business: BusinessService,
        authority: ScheduleAuthority,
        *,
        reads: RegisteredReadRegistry,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        service_identity: Callable[[], Awaitable[Principal]] | None = None,
    ) -> None:
        self._repository, self._business, self._authority = repository, business, authority
        self._clock, self._id_factory = clock, id_factory
        self._reads = reads
        self._service_identity = service_identity

    async def actor_choices(
        self, principal: Principal, device_id: str, scenario: Scenario
    ) -> tuple[ScheduleActorChoice, ...]:
        """Discover the configured worker, not every grantee or execution readiness.

        Native owner/binding/source checks remain in create/enable/tick. This read
        does not start a worker or promise a live process; it rechecks its identity.
        """
        BusinessService.require(principal, "manage")
        device = await asyncio.to_thread(self._business.get_device, principal, device_id)
        device = Device.model_validate(device.model_dump())
        if (device.workspace_id, device.id) != (principal.workspace_id, device_id) or device.deleted_at is not None:
            raise AccessDenied("schedule_device_scope_invalid")
        if self._service_identity is None:
            raise DependencyUnavailable("schedule_identity_unavailable")
        try:
            worker = await self._service_identity()
        except EnterpriseError:
            raise DependencyUnavailable("schedule_identity_unavailable") from None
        worker = Principal.model_validate(worker.model_dump())
        if worker.workspace_id != principal.workspace_id or not worker.can("run"):
            return ()
        grant = await asyncio.to_thread(self._authority.get_grant, principal.workspace_id, worker.actor_id)
        if grant is None:
            return ()
        grant = ServiceGrant.model_validate(grant.model_dump())
        now = self._clock()
        if now.utcoffset() is None:
            raise InvalidInput("clock_requires_timezone")
        if (
            not grant.enabled
            or grant.expires_at <= now
            or grant.principal.workspace_id != principal.workspace_id
            or grant.principal.actor_id != worker.actor_id
            or not grant.principal.can("run")
            or device_id not in grant.device_ids
            or scenario not in grant.scenarios
        ):
            return ()
        return (ScheduleActorChoice(actor_id=worker.actor_id, display_name=worker.display_name),)

    def _authorize_service(self, binding: Binding, actor_id: str) -> None:
        grant = self._authority.get_grant(binding.workspace_id, actor_id)
        if grant is None:
            raise AccessDenied("schedule_service_grant_missing")
        grant = ServiceGrant.model_validate(grant.model_dump())
        now = self._clock()
        if now.utcoffset() is None:
            raise InvalidInput("clock_requires_timezone")
        if (
            not grant.enabled
            or grant.expires_at <= now
            or grant.principal.workspace_id != binding.workspace_id
            or grant.principal.actor_id != actor_id
            or not grant.principal.can("run")
            or binding.device_id not in grant.device_ids
            or binding.scenario not in grant.scenarios
        ):
            raise AccessDenied("schedule_service_scope_invalid")
        if self._authority.has_native_timer(binding) is not False:
            raise Conflict("schedule_native_timer_conflict")
        current = self._authority.get_grant(binding.workspace_id, actor_id)
        checked_at = self._clock()
        if checked_at.utcoffset() is None:
            raise InvalidInput("clock_requires_timezone")
        if (
            current is None
            or ServiceGrant.model_validate(current.model_dump()) != grant
            or grant.expires_at <= checked_at
        ):
            raise AccessDenied("schedule_service_grant_changed")

    async def _binding(self, principal: Principal, device_id: str, scenario: Scenario, revision: int) -> Binding:
        binding = await asyncio.to_thread(self._business.get_binding, principal, device_id, scenario)
        binding = Binding.model_validate(binding.model_dump())
        if (binding.workspace_id, binding.device_id, binding.scenario) != (principal.workspace_id, device_id, scenario):
            raise AccessDenied("schedule_binding_scope_invalid")
        if binding.revision != revision:
            raise Conflict("schedule_binding_revision_conflict")
        return binding

    async def create(
        self,
        principal: Principal,
        device_id: str,
        scenario: Scenario,
        payload: CreateSchedule,
    ) -> IntervalSchedule:
        BusinessService.require(principal, "manage")
        payload = CreateSchedule.model_validate(payload.model_dump())
        binding = await self._binding(principal, device_id, scenario, payload.expected_binding_revision)
        await asyncio.to_thread(self._authorize_service, binding, payload.service_actor_id)
        await asyncio.to_thread(validate_schedule_read, binding, self._reads)
        value = IntervalSchedule.model_validate(
            payload.model_dump(exclude={"expected_binding_revision"})
            | {
                "id": self._id_factory(),
                "workspace_id": principal.workspace_id,
                "device_id": device_id,
                "scenario": scenario,
                "binding_id": binding.id,
                "binding_revision": binding.revision,
                "revision": 1,
                "next_due_at": payload.anchor_at,
                "enabled": False,
            }
        )
        result = await asyncio.to_thread(self._repository.create, value, actor_id=principal.actor_id)
        result = IntervalSchedule.model_validate(result.model_dump())
        if result != value:
            raise Conflict("schedule_receipt_mismatch")
        return result

    async def find_for_device(
        self, principal: Principal, device_id: str, scenario: Scenario
    ) -> IntervalSchedule | None:
        BusinessService.require(principal, "read")
        device = await asyncio.to_thread(self._business.get_device, principal, device_id)
        device = Device.model_validate(device.model_dump())
        if (device.workspace_id, device.id) != (principal.workspace_id, device_id) or device.deleted_at is not None:
            raise AccessDenied("schedule_device_scope_invalid")
        result = await asyncio.to_thread(self._repository.find_for_device, principal.workspace_id, device_id, scenario)
        if result is None:
            return None
        result = IntervalSchedule.model_validate(result.model_dump())
        if (result.workspace_id, result.device_id, result.scenario) != (principal.workspace_id, device_id, scenario):
            raise AccessDenied("schedule_scope_invalid")
        return result

    async def get(self, principal: Principal, schedule_id: str) -> IntervalSchedule:
        BusinessService.require(principal, "read")
        result = await asyncio.to_thread(self._repository.get, principal.workspace_id, schedule_id)
        result = IntervalSchedule.model_validate(result.model_dump())
        if (result.workspace_id, result.id) != (principal.workspace_id, schedule_id):
            raise AccessDenied("schedule_scope_invalid")
        return result

    async def tick(self, principal: Principal, schedule_id: str) -> ScheduleCommit:
        """Prepare one tick for the exact service actor, then atomically enqueue.

        This is not a public manual-run endpoint. The caller must authenticate
        the service principal. Authority is refreshed immediately before commit;
        an external grant/native-owner lease still requires adapter-level fencing.
        Neither conflicts nor uncertain outcomes trigger a retry here.
        """
        BusinessService.require(principal, "run")
        old = await self.get(principal, schedule_id)
        if principal.actor_id != old.service_actor_id:
            raise AccessDenied("schedule_actor_mismatch")
        now = self._clock()
        decision = plan_tick(old, now)
        if decision.next_due_at == old.next_due_at:
            return ScheduleCommit(old, None, 0)
        binding = await self._binding(principal, old.device_id, old.scenario, old.binding_revision)
        spec = prepare_schedule_run(old, binding, now)
        await asyncio.to_thread(validate_schedule_read, binding, self._reads)
        await asyncio.to_thread(self._authorize_service, binding, old.service_actor_id)
        result = await asyncio.to_thread(self._repository.commit_tick, old, spec)
        saved = IntervalSchedule.model_validate(result.schedule.model_dump())
        if saved.model_dump(exclude={"next_due_at"}) != old.model_dump(exclude={"next_due_at"}):
            raise Conflict("schedule_receipt_mismatch")
        if saved.next_due_at <= old.next_due_at or result.discarded_ticks < 0:
            raise Conflict("schedule_tick_receipt_mismatch")
        if (result.run is None) != (spec is None):
            raise Conflict("schedule_run_receipt_mismatch")
        if result.run is not None and (
            result.run.workspace_id != old.workspace_id
            or result.run.actor_id != old.service_actor_id
            or result.run.spec != spec
            or decision.occurrence is None
            or result.run.request_key != decision.occurrence.id
        ):
            raise Conflict("schedule_run_receipt_mismatch")
        return result

    async def set_enabled(
        self,
        principal: Principal,
        schedule_id: str,
        *,
        expected_revision: int,
        enabled: bool,
    ) -> IntervalSchedule:
        BusinessService.require(principal, "manage")
        command = ScheduleSwitch(expected_revision=expected_revision, enabled=enabled)
        old = await self.get(principal, schedule_id)
        if old.revision != command.expected_revision:
            raise Conflict("schedule_revision_conflict")
        if command.enabled:
            binding = await self._binding(principal, old.device_id, old.scenario, old.binding_revision)
            if binding.id != old.binding_id:
                raise Conflict("schedule_binding_identity_conflict")
            await asyncio.to_thread(self._authorize_service, binding, old.service_actor_id)
            await asyncio.to_thread(validate_schedule_read, binding, self._reads)
        result = await asyncio.to_thread(
            self._repository.set_enabled,
            principal.workspace_id,
            schedule_id,
            expected_revision=command.expected_revision,
            enabled=command.enabled,
            actor_id=principal.actor_id,
        )
        result = IntervalSchedule.model_validate(result.model_dump())
        expected = old.model_copy(
            update={"enabled": command.enabled, "revision": old.revision + (old.enabled != command.enabled)}
        )
        if result != expected:
            raise Conflict("schedule_receipt_mismatch")
        return result
