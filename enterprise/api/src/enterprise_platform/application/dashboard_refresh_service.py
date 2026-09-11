"""Whole-batch dashboard refresh; persistence must enforce the documented CAS.

Query adapters resolve current source authorization and immutable query revisions.
The service is not wired to HTTP until those adapters and durable storage exist.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from enterprise_platform.domain import dashboard as d
from enterprise_platform.domain.data_sources import DataSourceError

from .contracts import Principal
from .errors import AccessDenied, Conflict, InvalidInput


@dataclass(frozen=True, slots=True)
class DashboardRecord:
    workspace_id: str
    dashboard_id: str
    revision: int
    design: d.DesignSnapshot
    bindings: tuple[d.ExecutionBinding, ...]
    state: d.RefreshState
    name: str = ""
    creation_request_hash: str = ""


class DashboardRepository(Protocol):
    def create(self, record: DashboardRecord, *, actor_id: str) -> DashboardRecord: ...

    def save_bindings(
        self,
        workspace_id: str,
        dashboard_id: str,
        *,
        expected_revision: int,
        expected_design_identity: str,
        bindings: tuple[d.ExecutionBinding, ...],
        actor_id: str,
    ) -> DashboardRecord: ...

    def list(self, workspace_id: str, *, after: str | None, limit: int) -> tuple[DashboardRecord, ...]: ...

    def get(self, workspace_id: str, dashboard_id: str) -> DashboardRecord: ...

    def commit(
        self,
        *,
        workspace_id: str,
        dashboard_id: str,
        expected_revision: int,
        expected_design_identity: str,
        expected_bindings_hash: str,
        state: d.RefreshState,
        actor_id: str,
    ) -> d.RefreshState:
        """Atomically compare revision/design/bindings; store state + audit and increment revision.

        Failed attempts also increment revision so concurrent failure receipts cannot
        overwrite a later attempt. Conflicts raise Conflict without writing anything.
        """
        ...


class DashboardQueryExecutor(Protocol):
    async def execute(self, principal: Principal, binding: d.ExecutionBinding) -> d.QueryResult:
        """Authorize source access and execute exactly the captured binding, without SQL from the browser."""
        ...


class DashboardRefreshService:
    def __init__(
        self,
        repository: DashboardRepository,
        queries: DashboardQueryExecutor,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository, self._queries = repository, queries
        self._id = id_factory or (lambda: str(uuid4()))

    async def refresh(self, principal: Principal, dashboard_id: str, *, expected_revision: int) -> d.RefreshState:
        if not principal.can("run"):
            raise AccessDenied()
        if not dashboard_id.strip() or type(expected_revision) is not int or expected_revision < 1:
            raise InvalidInput("invalid_dashboard_revision")
        record = self._repository.get(principal.workspace_id, dashboard_id)
        if (record.workspace_id, record.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        if record.revision != expected_revision or record.state.design_identity != record.design.identity:
            raise Conflict("dashboard_revision_conflict")
        slots = {slot.slot_id: slot for slot in record.design.slots}
        bound = {binding.slot_id for binding in record.bindings}
        if (
            len(bound) != len(record.bindings)
            or bound - slots.keys()
            or any(slot.required and slot.slot_id not in bound for slot in record.design.slots)
        ):
            raise InvalidInput("invalid_dashboard_bindings")
        for binding in record.bindings:
            columns = slots[binding.slot_id].columns
            mapped = set(binding.proposal.field_map)
            if mapped - {column.name for column in columns} or any(
                column.required and column.name not in mapped for column in columns
            ):
                raise InvalidInput("invalid_dashboard_bindings")
        batch_id = self._id()
        if not batch_id.strip() or record.state.current and batch_id == record.state.current.batch_id:
            raise InvalidInput("invalid_dashboard_batch")
        outcomes: list[d.SlotSuccess | d.SlotFailure] = []
        for binding in record.bindings:
            try:
                result = await self._queries.execute(principal, binding)
                outcomes.append(d.SlotSuccess(binding.slot_id, result))
            except DataSourceError:
                outcomes.append(d.SlotFailure(binding.slot_id, "source_query_failed"))
            except d.DashboardError as error:
                outcomes.append(d.SlotFailure(binding.slot_id, error.code))
        state = d.commit_refresh(
            record.design,
            record.bindings,
            record.state,
            d.RefreshBatch(
                batch_id,
                record.design.identity,
                record.state.current.batch_id if record.state.current else None,
                record.bindings,
                tuple(outcomes),
            ),
        )
        return self._repository.commit(
            workspace_id=principal.workspace_id,
            dashboard_id=dashboard_id,
            expected_revision=expected_revision,
            expected_design_identity=record.design.identity,
            expected_bindings_hash=d.binding_set_identity(record.bindings),
            state=state,
            actor_id=principal.actor_id,
        )
