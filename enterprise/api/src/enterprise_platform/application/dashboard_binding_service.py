"""Authorize data bindings before revision-fenced persistence; never run source SQL.

Grant snapshots are rechecked before save, not a distributed revocation lock.
Execution independently reauthorizes current grants when a later refresh runs.
"""

import asyncio

from enterprise_platform.domain import dashboard as d

from .contracts import Principal
from .dashboard_binding_update import replace_dashboard_bindings
from .dashboard_query_execution import ApprovedDeviceDashboardRead, DeviceDashboardQueryRegistry
from .dashboard_refresh_service import DashboardRecord, DashboardRepository
from .errors import AccessDenied, Conflict, InvalidInput


class DashboardBindingService:
    def __init__(self, repository: DashboardRepository, registry: DeviceDashboardQueryRegistry) -> None:
        self._repository, self._registry = repository, registry

    async def save(
        self,
        principal: Principal,
        dashboard_id: str,
        *,
        expected_revision: int,
        expected_design_identity: str,
        bindings: tuple[d.ExecutionBinding, ...],
    ) -> DashboardRecord:
        if not principal.can("manage"):
            raise AccessDenied()
        if type(expected_revision) is not int or expected_revision < 1:
            raise InvalidInput("invalid_dashboard_revision")
        record = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        if (record.workspace_id, record.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        if (record.revision, record.design.identity) != (expected_revision, expected_design_identity):
            raise Conflict("dashboard_revision_conflict")
        replace_dashboard_bindings(record, bindings)
        plans: list[ApprovedDeviceDashboardRead] = []
        for binding in bindings:
            approved = await asyncio.to_thread(self._registry.resolve, principal, binding)
            if (
                approved.actor_id != principal.actor_id
                or approved.device.workspace_id != principal.workspace_id
                or approved.contract.binding.identity != binding.identity
            ):
                raise AccessDenied("dashboard_query_context_mismatch")
            try:
                d.validate_binding_columns(record.design, binding, approved.contract.columns)
            except d.DashboardError as error:
                raise InvalidInput(error.code) from None
            plans.append(approved)
        for binding, approved in zip(bindings, plans, strict=True):
            current = await asyncio.to_thread(self._registry.resolve, principal, binding)
            if current != approved:
                raise Conflict("dashboard_query_approval_changed")
        return await asyncio.to_thread(
            self._repository.save_bindings,
            principal.workspace_id,
            dashboard_id,
            expected_revision=expected_revision,
            expected_design_identity=expected_design_identity,
            bindings=bindings,
            actor_id=principal.actor_id,
        )
