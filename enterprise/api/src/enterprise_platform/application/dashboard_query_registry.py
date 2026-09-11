"""Resolve approved query references against existing versioned sources and devices.

Approval storage is an injected current-state authority; AI/browser inputs never
supply connection credentials, source revisions or protected scope parameters.
"""

from typing import Protocol

from pydantic import Field

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.domain.dashboard import ExecutionBinding, QueryColumn
from enterprise_platform.domain.data_sources import Policy, SourceRef

from .contracts import Contract, Device, Identifier, Principal
from .dashboard_query_capture import CapturedQueryContract
from .dashboard_query_execution import ApprovedDeviceDashboardRead
from .errors import AccessDenied, Conflict, InvalidInput, NotFound
from .input_capture import ParameterKind, RegisteredRead, RegisteredReadRegistry, bind_registered_parameters
from .source_service import DeviceLookup


class DashboardQueryApproval(Policy):
    workspace_id: Identifier
    query_ref: Identifier
    query_revision: Identifier
    source: SourceRef
    read_id: Identifier
    read_revision: Identifier
    device_id: Identifier
    columns: tuple[QueryColumn, ...] = Field(min_length=1)
    actor_ids: frozenset[Identifier] = Field(min_length=1)
    enabled: bool


class DashboardQueryApprovalStore(Protocol):
    def list_for_actor(self, workspace_id: str, actor_id: str) -> tuple[DashboardQueryApproval, ...]:
        """Discover current enabled grants scoped to this workspace and actor."""
        ...

    def get(self, workspace_id: str, query_ref: str, query_revision: str) -> DashboardQueryApproval:
        """Read current approval including revocation, not a cached authorization snapshot."""
        ...


class DashboardQueryParameter(Contract):
    input_key: Identifier
    kind: ParameterKind
    nullable: bool


class DashboardQueryChoice(Contract):
    query_ref: Identifier
    query_revision: Identifier
    device_id: Identifier
    columns: tuple[QueryColumn, ...]
    parameters: tuple[DashboardQueryParameter, ...]


class DashboardQueryDiscovery(Protocol):
    def discover(self, principal: Principal) -> tuple[DashboardQueryChoice, ...]: ...


class RegisteredDeviceDashboardQueries:
    def __init__(
        self,
        approvals: DashboardQueryApprovalStore,
        reads: RegisteredReadRegistry,
        devices: DeviceLookup,
    ) -> None:
        self._approvals, self._reads, self._devices = approvals, reads, devices

    def discover(self, principal: Principal) -> tuple[DashboardQueryChoice, ...]:
        """List current configuration choices, not execution grants or source results."""
        if not principal.can("manage"):
            raise AccessDenied()
        choices: list[DashboardQueryChoice] = []
        for approval in self._approvals.list_for_actor(principal.workspace_id, principal.actor_id):
            try:
                self._validate_approval(principal, approval)
                entry, device = self._registration(principal, approval)
                current = self._approvals.get(principal.workspace_id, approval.query_ref, approval.query_revision)
                if current != approval:
                    continue
            except (AccessDenied, Conflict, InvalidInput, NotFound):
                continue
            choices.append(
                DashboardQueryChoice(
                    query_ref=approval.query_ref,
                    query_revision=approval.query_revision,
                    device_id=device.id,
                    columns=approval.columns,
                    parameters=tuple(
                        DashboardQueryParameter(
                            input_key=item.input_key,
                            kind=item.kind,
                            nullable=item.nullable,
                        )
                        for item in entry.parameters
                    ),
                )
            )
        return tuple(choices)

    def resolve(self, principal: Principal, binding: ExecutionBinding) -> ApprovedDeviceDashboardRead:
        if not principal.can("run"):
            raise AccessDenied()
        proposal = binding.proposal
        approval = self._approvals.get(principal.workspace_id, proposal.query_ref, binding.query_revision)
        self._validate_approval(principal, approval)
        if (approval.query_ref, approval.query_revision) != (proposal.query_ref, binding.query_revision):
            raise Conflict("dashboard_query_revision_mismatch")
        if set(proposal.field_map.values()) - {column.name for column in approval.columns}:
            raise InvalidInput("dashboard_query_column_mismatch")
        entry, device = self._registration(principal, approval)
        _, values = bind_registered_parameters(device, entry, proposal.parameters)
        parameters = tuple(sorted(values.items()))
        return ApprovedDeviceDashboardRead(
            principal.actor_id,
            device,
            entry,
            CapturedQueryContract(
                binding,
                approval.source,
                approval.read_id,
                approval.read_revision,
                read_fingerprint(entry.read, parameters),
                approval.columns,
            ),
            parameters,
        )

    @staticmethod
    def _validate_approval(principal: Principal, approval: DashboardQueryApproval) -> None:
        if (
            not approval.enabled
            or principal.actor_id not in approval.actor_ids
            or approval.workspace_id != principal.workspace_id
            or approval.source.workspace_id != principal.workspace_id
        ):
            raise AccessDenied("dashboard_query_not_approved")
        columns = {column.name for column in approval.columns}
        if len(columns) != len(approval.columns):
            raise InvalidInput("dashboard_query_column_mismatch")

    def _registration(self, principal: Principal, approval: DashboardQueryApproval) -> tuple[RegisteredRead, Device]:
        entry = self._reads.resolve(
            principal.workspace_id,
            approval.source.source_id,
            approval.source.revision,
            approval.read_id,
            approval.read_revision,
        )
        if (entry.read.source, entry.read.read_id, entry.read.revision) != (
            approval.source,
            approval.read_id,
            approval.read_revision,
        ):
            raise Conflict("dashboard_query_source_mismatch")
        device = self._devices.get_device(principal.workspace_id, approval.device_id)
        if (
            (device.workspace_id, device.id) != (principal.workspace_id, approval.device_id)
            or device.deleted_at is not None
            or device.id not in entry.device_ids
        ):
            raise AccessDenied("dashboard_query_device_unavailable")
        return entry, device
