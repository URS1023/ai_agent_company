"""Deterministic interval planning for the existing device/scenario run ledger.

This module performs no dispatch, authorization or persistence. An adapter must
commit the schedule revision/cursor and existing run ledger atomically using a
freshly authorized, device-scoped service identity, before workflow dispatch.
Only one scheduling owner may hold a binding; native timers must not also own it.

Intervals are elapsed UTC time, not local calendar recurrences. Windows are
[start, end); coalescing chooses the latest fixed window, not all missed data.
Planning never loops over an outage backlog or retries an uncertain native run.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .contracts import Binding, Contract, Identifier, JsonObject, Run, RunSpec, Scenario, canonical_hash
from .errors import Conflict, InvalidInput


class ScheduleScanCursor(Contract):
    next_due_at: AwareDatetime
    schedule_id: Identifier

    @property
    def key(self) -> tuple[datetime, str]:
        return self.next_due_at, self.schedule_id


class IntervalSchedule(Contract):
    id: Identifier
    workspace_id: Identifier
    revision: int = Field(ge=1)
    binding_id: Identifier
    binding_revision: int = Field(ge=1)
    device_id: Identifier
    scenario: Scenario
    service_actor_id: Identifier
    anchor_at: AwareDatetime
    next_due_at: AwareDatetime
    interval_seconds: int = Field(ge=1, le=86400)
    window_seconds: int = Field(ge=1, le=604800)
    grace_seconds: int = Field(ge=0)
    missed_policy: Literal["skip", "coalesce"]
    enabled: bool = True

    @field_validator("anchor_at", "next_due_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_cursor(self) -> Self:
        step = timedelta(seconds=self.interval_seconds)
        offset = self.next_due_at - self.anchor_at
        if offset < timedelta(0) or offset % step != timedelta(0):
            raise ValueError("Schedule cursor must align with its anchor")
        if self.grace_seconds >= self.interval_seconds:
            raise ValueError("Grace must be smaller than the interval")
        return self


@dataclass(frozen=True, slots=True)
class ScheduledOccurrence:
    id: str
    schedule: IntervalSchedule
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    next_due_at: datetime
    discarded_ticks: int
    occurrence: ScheduledOccurrence | None


@dataclass(frozen=True, slots=True)
class ScheduleCommit:
    schedule: IntervalSchedule
    run: Run | None
    discarded_ticks: int


def prepare_schedule_run(
    schedule: IntervalSchedule,
    binding: Binding,
    now: datetime,
    *,
    parameters: JsonObject | None = None,
) -> RunSpec | None:
    """Freeze only registered inputs; time windows are owned by the planner.

    This pure preparation does not resolve credentials, read source data, or
    authorize execution. The atomic repository recomputes the window before
    enqueue; crossing an interval while preparing therefore causes a conflict.
    """
    schedule = IntervalSchedule.model_validate(schedule.model_dump())
    binding = Binding.model_validate(binding.model_dump())
    if (binding.workspace_id, binding.id, binding.revision, binding.device_id, binding.scenario) != (
        schedule.workspace_id,
        schedule.binding_id,
        schedule.binding_revision,
        schedule.device_id,
        schedule.scenario,
    ):
        raise Conflict("schedule_binding_snapshot_mismatch")
    occurrence = plan_tick(schedule, now).occurrence
    if occurrence is None:
        return None
    allowed = binding.manifest.get("input_keys", [])
    window_keys = {"window_start", "window_end"}
    if (
        not isinstance(allowed, list)
        or any(not isinstance(key, str) for key in allowed)
        or not window_keys.issubset(allowed)
    ):
        raise InvalidInput("schedule_window_inputs_not_registered")
    inputs = dict(parameters or {})
    if set(inputs) & window_keys or set(inputs) - set(allowed) or any(key.startswith("enterprise_") for key in inputs):
        raise InvalidInput("unregistered_schedule_parameters")
    inputs.update(window_start=occurrence.window_start.isoformat(), window_end=occurrence.window_end.isoformat())
    try:
        return RunSpec.model_validate(RunSpec.from_binding(binding).model_dump() | {"parameters": inputs})
    except ValueError:
        raise InvalidInput("invalid_schedule_parameters") from None


def plan_tick(schedule: IntervalSchedule, now: datetime) -> ScheduleDecision:
    """Plan at most one occurrence; caller commits the cursor with its run atomically.

    Skip ignores the latest due tick if its inclusive grace has elapsed. Coalesce
    emits that latest tick regardless of lateness. Both discard earlier due ticks.
    A paused/early poll leaves the cursor intact; resume applies the same policy.
    Repeated polls before a durable commit return the same occurrence identity.
    """
    schedule = IntervalSchedule.model_validate(schedule.model_dump())
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Scheduler clock must be timezone aware")
    now = now.astimezone(UTC)
    if not schedule.enabled or now < schedule.next_due_at:
        return ScheduleDecision(schedule.next_due_at, 0, None)

    step = timedelta(seconds=schedule.interval_seconds)
    missed = (now - schedule.next_due_at) // step
    due = schedule.next_due_at + missed * step
    try:
        next_due = due + step
        window_start = due - timedelta(seconds=schedule.window_seconds)
    except OverflowError:
        raise ValueError("Schedule window exceeds supported datetime range") from None
    if schedule.missed_policy == "skip" and now - due > timedelta(seconds=schedule.grace_seconds):
        return ScheduleDecision(next_due, missed + 1, None)

    identity = canonical_hash(
        {
            "workspace_id": schedule.workspace_id,
            "schedule_id": schedule.id,
            "schedule_revision": schedule.revision,
            "binding_id": schedule.binding_id,
            "binding_revision": schedule.binding_revision,
            "due_at": due.isoformat(),
        }
    )
    occurrence = ScheduledOccurrence(f"scheduled-{identity}", schedule, window_start, due)
    return ScheduleDecision(next_due, missed, occurrence)
