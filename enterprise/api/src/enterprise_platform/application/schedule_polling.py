"""One bounded scheduler poll; no hidden loop, workflow dispatch, or retries.

A host supplies an authenticated service principal and owns poll cadence and
failure backoff. Every candidate is reread and authorized by ScheduleService;
scanning never grants authority. Cancellation propagates to the host.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from .contracts import Principal
from .errors import AccessDenied, Conflict, InvalidInput, PersistenceError
from .schedule_service import ScheduleRepository, ScheduleService
from .scheduling import IntervalSchedule, ScheduleScanCursor
from .service import BusinessService


@dataclass(frozen=True, slots=True)
class SchedulePollItem:
    schedule_id: str
    status: Literal["queued", "skipped", "idle", "conflict", "denied", "error"]
    run_id: str | None = None
    discarded_ticks: int = 0


@dataclass(frozen=True, slots=True)
class _Sweep:
    workspace_id: str
    actor_id: str
    cutoff: datetime
    after: ScheduleScanCursor


class SchedulePoller:
    def __init__(
        self,
        repository: ScheduleRepository,
        service: ScheduleService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository, self._service, self._clock = repository, service, clock
        self._sweep: _Sweep | None = None
        self._lock = asyncio.Lock()

    async def poll(self, principal: Principal, *, limit: int = 100) -> tuple[SchedulePollItem, ...]:
        async with self._lock:
            return await self._poll(principal, limit=limit)

    async def _poll(self, principal: Principal, *, limit: int) -> tuple[SchedulePollItem, ...]:
        BusinessService.require(principal, "run")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise InvalidInput("schedule_scan_limit_invalid")
        now = self._clock()
        if now.utcoffset() is None:
            raise InvalidInput("clock_requires_timezone")
        after = None
        if (
            self._sweep is not None
            and (self._sweep.workspace_id, self._sweep.actor_id) == (principal.workspace_id, principal.actor_id)
            and now >= self._sweep.cutoff
        ):
            now, after = self._sweep.cutoff, self._sweep.after
        candidates = await asyncio.to_thread(
            self._repository.list_due,
            principal.workspace_id,
            now=now,
            limit=limit,
            service_actor_id=principal.actor_id,
            after=after,
        )
        candidates = tuple(IntervalSchedule.model_validate(item.model_dump()) for item in candidates)
        keys = [(item.next_due_at, item.id) for item in candidates]
        if (
            len(candidates) > limit
            or len({item.id for item in candidates}) != len(candidates)
            or keys != sorted(keys)
            or (after is not None and any(key <= after.key for key in keys))
            or any(
                item.workspace_id != principal.workspace_id
                or item.service_actor_id != principal.actor_id
                or not item.enabled
                or item.next_due_at > now
                for item in candidates
            )
        ):
            raise PersistenceError("schedule_scan_receipt_invalid")
        outcomes: list[SchedulePollItem] = []
        for candidate in candidates:
            try:
                result = await self._service.tick(principal, candidate.id)
                outcomes.append(
                    SchedulePollItem(
                        candidate.id,
                        "queued" if result.run is not None else "skipped" if result.discarded_ticks else "idle",
                        result.run.id if result.run is not None else None,
                        result.discarded_ticks,
                    )
                )
            except AccessDenied:
                outcomes.append(SchedulePollItem(candidate.id, "denied"))
            except Conflict:
                outcomes.append(SchedulePollItem(candidate.id, "conflict"))
            except Exception:
                # A commit may have succeeded before its connection failed. Report
                # an opaque error, not "not enqueued", and never retry this tick.
                outcomes.append(SchedulePollItem(candidate.id, "error"))
        # Move past failures too, so they cannot occupy the first page forever.
        # The cutoff stays fixed through a sweep; newly due work joins the next
        # sweep. This cursor is process-local and never changes schedule state.
        self._sweep = (
            _Sweep(
                principal.workspace_id,
                principal.actor_id,
                now,
                ScheduleScanCursor(next_due_at=candidates[-1].next_due_at, schedule_id=candidates[-1].id),
            )
            if len(candidates) == limit
            else None
        )
        return tuple(outcomes)
