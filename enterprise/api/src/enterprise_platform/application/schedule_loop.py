"""Explicit periodic host for bounded enqueue polls, never workflow dispatch.

The composition supplies fresh authenticated identity and a report sink. No
identity fallback, ambient credentials, schema changes, or background task starts
at import. Backoff is process-local; restart does not preserve failure history.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from .contracts import Contract, Identifier, Principal
from .errors import AccessDenied
from .schedule_polling import SchedulePoller, SchedulePollItem
from .service import BusinessService


class ScheduleLoopPolicy(Contract):
    interval_seconds: float = Field(default=5, ge=0.1, le=3600, allow_inf_nan=False)
    max_backoff_seconds: float = Field(default=60, ge=0.1, le=3600, allow_inf_nan=False)
    batch_limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.max_backoff_seconds < self.interval_seconds:
            raise ValueError("Backoff cap must not be shorter than normal cadence")
        return self


@dataclass(frozen=True, slots=True)
class ScheduleLoopReport:
    status: Literal["ok", "degraded", "error"]
    items: tuple[SchedulePollItem, ...] = ()


class ScheduleLoop:
    def __init__(
        self,
        poller: SchedulePoller,
        identity: Callable[[], Awaitable[Principal]],
        report: Callable[[ScheduleLoopReport], Awaitable[None]],
        *,
        workspace_id: str,
        actor_id: str,
        policy: ScheduleLoopPolicy,
    ) -> None:
        self._poller, self._identity, self._report = poller, identity, report
        self._workspace_id = TypeAdapter(Identifier).validate_python(workspace_id)
        self._actor_id = TypeAdapter(Identifier).validate_python(actor_id)
        self._policy = ScheduleLoopPolicy.model_validate(policy.model_dump())

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def run(self, stop: asyncio.Event) -> None:
        delay = self._policy.interval_seconds
        while not stop.is_set():
            try:
                principal = await self._identity()
                principal = Principal.model_validate(principal.model_dump())
                if (principal.workspace_id, principal.actor_id) != (self._workspace_id, self._actor_id):
                    raise AccessDenied("scheduler_identity_changed")
                BusinessService.require(principal, "run")
                if stop.is_set():
                    break
                items = await self._poller.poll(principal, limit=self._policy.batch_limit)
                failed = any(item.status in {"error", "denied", "conflict"} for item in items)
                outcome = ScheduleLoopReport("degraded" if failed else "ok", items)
            except Exception:
                failed = True
                outcome = ScheduleLoopReport("error")
            # Reporting failure terminates the host rather than silently losing
            # an enqueue receipt. Cancellation is never converted into a retry.
            await self._report(outcome)
            if stop.is_set():
                break
            if not failed:
                delay = self._policy.interval_seconds
            await self._wait(stop, delay)
            delay = min(delay * 2, self._policy.max_backoff_seconds) if failed else self._policy.interval_seconds
