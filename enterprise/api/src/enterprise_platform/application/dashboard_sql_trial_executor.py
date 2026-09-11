"""Read-only trial lane using current registered source credentials and saved SQL.

The trial service supplies current schema/template validation. This adapter separately
rechecks the stored draft and execution grant, never substitutes a device's registered
SQL, and clamps execution budgets to the source policy. It does not publish results.
"""

import asyncio
import time
from threading import Event

from enterprise_platform.adapters.data_sources import DatabaseSourceReader
from enterprise_platform.domain.data_sources import (
    DatabaseSourceConfig,
    DataSourceError,
    FrozenRows,
    ReadLimits,
    SqlRead,
)

from .contracts import Principal, canonical_hash
from .dashboard_sql_drafts import SqlDraftRepository
from .dashboard_sql_trial import SqlTrialAuthorizer, SqlTrialPermit
from .errors import AccessDenied, Conflict
from .input_capture import RegisteredReadRegistry
from .source_contracts import DbSourceView
from .source_ports import SourceRepository


class RegisteredSqlTrialExecutor:
    def __init__(
        self,
        sources: SourceRepository,
        reads: RegisteredReadRegistry,
        drafts: SqlDraftRepository,
        authorizer: SqlTrialAuthorizer,
    ) -> None:
        self._sources, self._reads, self._drafts, self._authorizer = sources, reads, drafts, authorizer

    def _check(self, principal: Principal, permit: SqlTrialPermit, read: SqlRead) -> None:
        if (
            not principal.can("manage")
            or not principal.can("run")
            or permit.actor_id != principal.actor_id
            or permit.workspace_id != principal.workspace_id
            or permit.source.workspace_id != principal.workspace_id
            or read.source != permit.source
            or read.read_id != permit.draft_id
            or read.revision != permit.draft_hash
        ):
            raise AccessDenied()
        draft = self._drafts.get(principal.workspace_id, permit.draft_id)
        slot = next((slot for slot in draft.proposals.slots if slot.slot_id == permit.slot_id), None)
        if (
            draft.workspace_id != principal.workspace_id
            or draft.draft_id != permit.draft_id
            or draft.source != permit.source
            or canonical_hash(draft.model_dump(mode="json")) != permit.draft_hash
            or slot is None
            or slot.sql != read.sql
        ):
            raise AccessDenied("sql_trial_read_mismatch")
        if self._authorizer.authorize(principal, draft, permit.slot_id) != permit:
            raise Conflict("sql_trial_grant_changed")

    async def execute(self, principal: Principal, permit: SqlTrialPermit, read: SqlRead) -> FrozenRows:
        cancelled = Event()
        deadline = time.monotonic() + permit.limits.timeout_seconds

        def check_time() -> None:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise DataSourceError("timeout")

        def execute() -> FrozenRows:
            self._check(principal, permit, read)
            view = self._sources.get(principal.workspace_id, permit.source.source_id).view
            if (view.workspace_id, view.source_id, view.source_revision) != (
                principal.workspace_id,
                permit.source.source_id,
                permit.source.revision,
            ) or not isinstance(view.connection, DbSourceView):
                raise AccessDenied("sql_trial_source_mismatch")
            entry = self._reads.resolve(
                principal.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision
            )
            config = entry.connection
            if (
                not isinstance(config, DatabaseSourceConfig)
                or config.source != permit.source
                or entry.read.source != permit.source
                or (entry.read.read_id, entry.read.revision) != (view.read_id, view.read_revision)
                or config.dialect != view.connection.dialect
                or config.allowed_tables != frozenset(view.connection.allowed_tables)
            ):
                raise AccessDenied("sql_trial_registration_mismatch")
            limits = ReadLimits(
                max_rows=min(config.limits.max_rows, permit.limits.max_rows),
                max_bytes=min(config.limits.max_bytes, permit.limits.max_bytes),
                max_pages=min(config.limits.max_pages, permit.limits.max_pages),
                timeout_seconds=min(config.limits.timeout_seconds, permit.limits.timeout_seconds),
            )
            self._check(principal, permit, read)
            check_time()
            reader = DatabaseSourceReader(config.model_copy(update={"limits": limits}))
            try:
                check_time()
                capture = reader.read(read, {}, deadline=deadline, plan_budget=permit.plan_budget)
            finally:
                reader.close()
            check_time()
            self._check(principal, permit, read)
            if self._sources.get(principal.workspace_id, permit.source.source_id).view != view:
                raise Conflict("sql_trial_source_changed")
            return capture

        try:
            return await asyncio.to_thread(execute)
        except asyncio.CancelledError:
            cancelled.set()
            raise
