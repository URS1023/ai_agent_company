"""Workspace-scoped schedule lifecycle, not a running scheduler.

Caller authorizes management commands and the service identity. Repository
mutations and audit events share one transaction. Pausing does not cancel a run
already claimed; resuming retains the cursor for the configured missed policy.
No dispatch, schema application or default service identity is provided here.
"""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import RunSpec, Scenario, canonical_hash
from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.application.scheduling import IntervalSchedule, ScheduleCommit, ScheduleScanCursor, plan_tick

from .mapping import as_binding, audit, binding_row, cas, lock_device, serialized, transaction, utc, utc_now
from .runs import RunOperations
from .schedule_mapping import as_schedule, schedule_row
from .schedule_models import ScheduleRow


def _get(session: Session, workspace_id: str, schedule_id: str, *, lock: bool = False) -> ScheduleRow:
    statement = select(ScheduleRow).where(
        ScheduleRow.workspace_id == workspace_id,
        ScheduleRow.schedule_id == schedule_id,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(statement)
    if row is None:
        raise NotFound("schedule_not_found")
    value = as_schedule(row)
    if (value.workspace_id, value.id) != (workspace_id, schedule_id):
        raise PersistenceError("stored_schedule_scope_invalid")
    return row


class SqlAlchemyScheduleRepository:
    def __init__(self, sessions: sessionmaker[Session], *, clock: Callable[[], datetime] = utc_now) -> None:
        self._sessions, self._clock = sessions, clock
        self._runs = RunOperations(sessions, clock=clock)

    def find_for_device(self, workspace_id: str, device_id: str, scenario: Scenario) -> IntervalSchedule | None:
        with transaction(self._sessions) as session:
            row = session.scalar(
                select(ScheduleRow).where(
                    ScheduleRow.workspace_id == workspace_id,
                    ScheduleRow.device_id == device_id,
                    ScheduleRow.scenario == scenario,
                )
            )
            if row is None:
                return None
            value = as_schedule(row)
            if (value.workspace_id, value.device_id, value.scenario) != (workspace_id, device_id, scenario):
                raise PersistenceError("stored_schedule_scope_invalid")
            return value

    def list_due(
        self,
        workspace_id: str,
        *,
        now: datetime,
        limit: int = 100,
        service_actor_id: str | None = None,
        after: ScheduleScanCursor | None = None,
    ) -> tuple[IntervalSchedule, ...]:
        """Read candidates only; commit_tick must still fence each returned snapshot.

        The worker supplies its configured workspace, never an unbounded global
        scan. Oldest cursors go first with a stable ID tie-breaker. Reading does
        not claim ownership, authorize execution, or advance any cursor.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise InvalidInput("schedule_scan_limit_invalid")
        if now.utcoffset() is None:
            raise InvalidInput("clock_requires_timezone")
        if after is not None:
            after = ScheduleScanCursor.model_validate(after.model_dump())
            if after.next_due_at > now:
                raise InvalidInput("schedule_scan_cursor_after_cutoff")
        with transaction(self._sessions) as session:
            statement = (
                select(ScheduleRow)
                .where(
                    ScheduleRow.workspace_id == workspace_id,
                    ScheduleRow.enabled.is_(True),
                    ScheduleRow.next_due_at <= now,
                )
                .order_by(ScheduleRow.next_due_at, ScheduleRow.schedule_id)
                .limit(limit)
            )
            if service_actor_id is not None:
                statement = statement.where(ScheduleRow.service_actor_id == service_actor_id)
            if after is not None:
                statement = statement.where(
                    or_(
                        ScheduleRow.next_due_at > after.next_due_at,
                        and_(ScheduleRow.next_due_at == after.next_due_at, ScheduleRow.schedule_id > after.schedule_id),
                    )
                )
            rows = session.scalars(statement).all()
            values = tuple(as_schedule(row) for row in rows)
            if any(
                value.workspace_id != workspace_id
                or not value.enabled
                or value.next_due_at > now
                or (service_actor_id is not None and value.service_actor_id != service_actor_id)
                or (after is not None and (value.next_due_at, value.id) <= after.key)
                for value in values
            ):
                raise PersistenceError("stored_due_schedule_invalid")
            return values

    def commit_tick(self, expected: IntervalSchedule, spec: RunSpec | None) -> ScheduleCommit:
        """Atomically advance a preauthorized schedule and enqueue one frozen window.

        Caller resolves current service authority before calling. No external I/O
        occurs inside this transaction. RunRow's unique scoped request key is the
        occurrence ledger; no second task queue is introduced. Conflict recovery
        must read the existing occurrence, never retry native workflow dispatch.
        """
        expected = IntervalSchedule.model_validate(expected.model_dump())
        spec = RunSpec.model_validate(spec.model_dump()) if spec is not None else None
        if spec is not None:
            allowed = spec.manifest.get("input_keys", [])
            if (
                not isinstance(allowed, list)
                or any(not isinstance(key, str) for key in allowed)
                or set(spec.parameters) - set(allowed)
                or any(key.startswith("enterprise_") for key in spec.parameters)
            ):
                raise InvalidInput("unregistered_schedule_parameters")
        with transaction(self._sessions) as session:
            lock_device(session, expected.workspace_id, expected.device_id)
            row = _get(session, expected.workspace_id, expected.id, lock=True)
            old = as_schedule(row)
            if old != expected:
                raise Conflict("schedule_snapshot_conflict")
            now = self._clock()
            if now.utcoffset() is None or now < utc(row.updated_at):
                raise Conflict("schedule_clock_regression")
            decision = plan_tick(old, now)
            occurrence = decision.occurrence
            if occurrence is not None:
                if spec is None or (
                    (spec.binding_id, spec.binding_revision, spec.device_id, spec.scenario)
                    != (old.binding_id, old.binding_revision, old.device_id, old.scenario)
                    or spec.recompute_of is not None
                    or spec.parameters.get("window_start") != occurrence.window_start.isoformat()
                    or spec.parameters.get("window_end") != occurrence.window_end.isoformat()
                ):
                    raise InvalidInput("schedule_run_window_mismatch")
            elif spec is not None:
                raise InvalidInput("schedule_run_not_due")
            if decision.next_due_at == old.next_due_at:
                return ScheduleCommit(old, None, 0)
            value = IntervalSchedule.model_validate(old.model_dump() | {"next_due_at": decision.next_due_at})
            cas(
                session,
                update(ScheduleRow)
                .where(
                    ScheduleRow.workspace_id == old.workspace_id,
                    ScheduleRow.schedule_id == old.id,
                    ScheduleRow.revision == old.revision,
                    ScheduleRow.next_due_at == old.next_due_at,
                    ScheduleRow.enabled.is_(True),
                )
                .values(next_due_at=value.next_due_at, public_json=serialized(value), updated_at=now),
            )
            run = None
            if occurrence is not None and spec is not None:
                digest = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": None})
                run = self._runs.enqueue_run_in_session(
                    session,
                    old.workspace_id,
                    occurrence.id,
                    digest,
                    spec,
                    None,
                    actor_id=old.service_actor_id,
                )
                if (
                    run.workspace_id != old.workspace_id
                    or run.actor_id != old.service_actor_id
                    or run.spec != spec
                    or run.payload_hash != digest
                    or run.request_key != occurrence.id
                ):
                    raise Conflict("schedule_run_receipt_mismatch")
            audit(
                session,
                old.workspace_id,
                "schedule",
                old.id,
                old.service_actor_id,
                "schedule_tick_enqueued" if run is not None else "schedule_ticks_skipped",
                now,
                {
                    "revision": old.revision,
                    "discarded_ticks": decision.discarded_ticks,
                    "next_due_at": value.next_due_at.isoformat(),
                },
                run_id=run.id if run is not None else None,
            )
            return ScheduleCommit(value, run, decision.discarded_ticks)

    def create(self, schedule: IntervalSchedule, *, actor_id: str) -> IntervalSchedule:
        """Register paused only; unique lane/binding constraints retain ownership on pause."""
        value = IntervalSchedule.model_validate(schedule.model_dump())
        if value.enabled or value.revision != 1 or value.next_due_at != value.anchor_at:
            raise Conflict("schedule_initial_state_invalid")
        with transaction(self._sessions) as session:
            lock_device(session, value.workspace_id, value.device_id)
            binding = as_binding(binding_row(session, value.workspace_id, value.binding_id))
            if (binding.workspace_id, binding.id, binding.revision, binding.device_id, binding.scenario) != (
                value.workspace_id,
                value.binding_id,
                value.binding_revision,
                value.device_id,
                value.scenario,
            ):
                raise Conflict("schedule_binding_conflict")
            now = self._clock()
            if now.utcoffset() is None or now < binding.updated_at:
                raise Conflict("schedule_clock_regression")
            session.add(schedule_row(value, now, now))
            audit(session, value.workspace_id, "schedule", value.id, actor_id, "schedule_created", now, {"revision": 1})
            return value

    def get(self, workspace_id: str, schedule_id: str) -> IntervalSchedule:
        with transaction(self._sessions) as session:
            return as_schedule(_get(session, workspace_id, schedule_id))

    def set_enabled(
        self,
        workspace_id: str,
        schedule_id: str,
        *,
        expected_revision: int,
        enabled: bool,
        actor_id: str,
    ) -> IntervalSchedule:
        with transaction(self._sessions) as session:
            row = _get(session, workspace_id, schedule_id, lock=True)
            old = as_schedule(row)
            if old.revision != expected_revision:
                raise Conflict("schedule_revision_conflict")
            if old.enabled == enabled:
                return old
            value = IntervalSchedule.model_validate(
                old.model_dump() | {"enabled": enabled, "revision": old.revision + 1}
            )
            now = self._clock()
            if now.utcoffset() is None or now < utc(row.updated_at):
                raise Conflict("schedule_clock_regression")
            cas(
                session,
                update(ScheduleRow)
                .where(
                    ScheduleRow.workspace_id == workspace_id,
                    ScheduleRow.schedule_id == schedule_id,
                    ScheduleRow.revision == old.revision,
                    ScheduleRow.next_due_at == old.next_due_at,
                )
                .values(revision=value.revision, enabled=value.enabled, public_json=serialized(value), updated_at=now),
            )
            audit(
                session,
                workspace_id,
                "schedule",
                schedule_id,
                actor_id,
                "schedule_enabled" if enabled else "schedule_paused",
                now,
                {"revision": value.revision},
            )
            return value
