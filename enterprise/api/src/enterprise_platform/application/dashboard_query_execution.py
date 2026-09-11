"""Execute approved device-scoped dashboard reads through existing source readers.

This adapter is one query lane, not a restriction on future aggregate dashboards.
The injected registry must resolve current query grants and immutable revisions,
derive parameters from the approved query, and return a server-owned device record.
No registry or HTTP endpoint is implicitly enabled by constructing this executor.
"""

from dataclasses import dataclass
from typing import Protocol

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.domain.dashboard import ExecutionBinding, QueryResult
from enterprise_platform.domain.data_sources import SourceValue

from .contracts import Device, Principal
from .dashboard_query_capture import CapturedQueryContract, map_query_capture
from .errors import AccessDenied, Conflict
from .input_capture import RegisteredRead, SourceReaderPort


@dataclass(frozen=True, slots=True)
class ApprovedDeviceDashboardRead:
    actor_id: str
    device: Device
    registration: RegisteredRead
    contract: CapturedQueryContract
    parameters: tuple[tuple[str, SourceValue], ...]


class DeviceDashboardQueryRegistry(Protocol):
    def resolve(self, principal: Principal, binding: ExecutionBinding) -> ApprovedDeviceDashboardRead:
        """Authorize this actor/query/source/device now; resolve exact immutable query version."""
        ...


def _validate(principal: Principal, binding: ExecutionBinding, approved: ApprovedDeviceDashboardRead) -> None:
    device, entry, expected = approved.device, approved.registration, approved.contract
    if (
        approved.actor_id != principal.actor_id
        or device.workspace_id != principal.workspace_id
        or device.deleted_at is not None
        or device.id not in entry.device_ids
        or expected.source.workspace_id != principal.workspace_id
    ):
        raise AccessDenied("dashboard_query_scope_mismatch")
    if (
        expected.binding.identity != binding.identity
        or entry.read.source != expected.source
        or entry.read.read_id != expected.read_id
        or entry.read.revision != expected.read_revision
    ):
        raise Conflict("dashboard_query_revision_mismatch")
    parameters = dict(approved.parameters)
    scope = device.id if entry.scope_attribute == "id" else device.device_code
    names = {entry.device_parameter} | {item.parameter_name for item in entry.parameters}
    if entry.department_parameter:
        names.add(entry.department_parameter)
        if parameters.get(entry.department_parameter) != device.department:
            raise AccessDenied("dashboard_query_scope_mismatch")
    if (
        len(parameters) != len(approved.parameters)
        or set(parameters) != names
        or parameters.get(entry.device_parameter) != scope
        or read_fingerprint(entry.read, tuple(sorted(parameters.items()))) != expected.read_fingerprint
    ):
        raise AccessDenied("dashboard_query_parameters_mismatch")


class DeviceDashboardQueryExecutor:
    def __init__(self, registry: DeviceDashboardQueryRegistry, reader: SourceReaderPort) -> None:
        self._registry, self._reader = registry, reader

    async def execute(self, principal: Principal, binding: ExecutionBinding) -> QueryResult:
        if not principal.can("run"):
            raise AccessDenied()
        approved = self._registry.resolve(principal, binding)
        _validate(principal, binding, approved)
        capture = await self._reader.read(approved.registration, dict(approved.parameters))
        entry, device = approved.registration, approved.device
        scope = device.id if entry.scope_attribute == "id" else device.device_code
        if entry.device_column not in capture.columns or any(
            row[capture.columns.index(entry.device_column)] != scope for row in capture.rows
        ):
            raise AccessDenied("source_row_device_scope_mismatch")
        result = map_query_capture(approved.contract, capture)
        current = self._registry.resolve(principal, binding)
        _validate(principal, binding, current)
        if current != approved:
            raise Conflict("dashboard_query_approval_changed")
        return result
