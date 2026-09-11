from unittest.mock import create_autospec

import pytest
from test_dashboard_query_execution import actor, plan

from enterprise_platform.application.dashboard_query_registry import (
    DashboardQueryApproval,
    DashboardQueryApprovalStore,
    RegisteredDeviceDashboardQueries,
)
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.input_capture import RegisteredReadRegistry
from enterprise_platform.application.source_service import DeviceLookup


def setup_registry():
    expected = plan()
    binding = expected.contract.binding
    approval = DashboardQueryApproval(
        workspace_id="workspace-1",
        query_ref=binding.proposal.query_ref,
        query_revision=binding.query_revision,
        source=expected.contract.source,
        read_id=expected.contract.read_id,
        read_revision=expected.contract.read_revision,
        device_id=expected.device.id,
        columns=expected.contract.columns,
        actor_ids=frozenset({"actor-1"}),
        enabled=True,
    )
    approvals = create_autospec(DashboardQueryApprovalStore, instance=True)
    approvals.get.return_value = approval
    reads = create_autospec(RegisteredReadRegistry, instance=True)
    reads.resolve.return_value = expected.registration
    devices = create_autospec(DeviceLookup, instance=True)
    devices.get_device.return_value = expected.device
    return RegisteredDeviceDashboardQueries(approvals, reads, devices), approvals, reads, devices, expected


def test_discovery_projects_only_query_columns_and_user_parameter_contracts() -> None:
    from enterprise_platform.application.input_capture import ParameterDeclaration

    registry, approvals, reads, _, expected = setup_registry()
    approvals.list_for_actor.return_value = (approvals.get.return_value,)
    reads.resolve.return_value = expected.registration.model_copy(
        update={
            "parameters": (ParameterDeclaration(input_key="row_limit", parameter_name="limit", kind="integer"),),
        }
    )

    choices = registry.discover(actor())

    assert len(choices) == 1
    choice = choices[0].model_dump(mode="json")
    assert set(choice) == {"query_ref", "query_revision", "device_id", "columns", "parameters"}
    assert choice["query_ref"] == "query-1"
    assert choice["parameters"] == [{"input_key": "row_limit", "kind": "integer", "nullable": False}]
    assert choice["columns"] == [item.model_dump(mode="json") for item in expected.contract.columns]
    approvals.list_for_actor.assert_called_once_with("workspace-1", "actor-1")


def test_discovery_requires_management_before_reading_authority() -> None:
    registry, approvals, reads, _, _ = setup_registry()
    with pytest.raises(AccessDenied):
        registry.discover(actor().model_copy(update={"workspace_role": "normal"}))
    approvals.list_for_actor.assert_not_called()
    reads.resolve.assert_not_called()


@pytest.mark.parametrize("case", ["revoked", "foreign", "device_scope", "read_revision", "changed"])
def test_discovery_omits_unavailable_or_changed_candidates(case: str) -> None:
    registry, approvals, reads, devices, expected = setup_registry()
    approval = approvals.get.return_value
    if case == "revoked":
        approval = approval.model_copy(update={"enabled": False})
    elif case == "foreign":
        approval = approval.model_copy(update={"workspace_id": "other"})
    elif case == "device_scope":
        devices.get_device.return_value = expected.device.model_copy(update={"workspace_id": "other"})
    elif case == "read_revision":
        reads.resolve.return_value = expected.registration.model_copy(
            update={
                "read": expected.registration.read.model_copy(update={"revision": "changed"}),
            }
        )
    else:
        approvals.get.return_value = approval.model_copy(update={"enabled": False})
    approvals.list_for_actor.return_value = (approval,)

    assert registry.discover(actor()) == ()


def test_discovery_surfaces_dependency_failure_instead_of_empty_success() -> None:
    from enterprise_platform.application.errors import DependencyUnavailable

    registry, approvals, reads, _, _ = setup_registry()
    approvals.list_for_actor.return_value = (approvals.get.return_value,)
    reads.resolve.side_effect = DependencyUnavailable()
    with pytest.raises(DependencyUnavailable):
        registry.discover(actor())


def test_resolves_exact_saved_source_and_derives_protected_device_parameters() -> None:
    registry, approvals, reads, devices, expected = setup_registry()
    assert registry.resolve(actor(), expected.contract.binding) == expected
    reads.resolve.assert_called_once_with("workspace-1", "source-1", "source-rev-1", "read-1", "read-rev-1")
    devices.get_device.assert_called_once_with("workspace-1", "device-1")


@pytest.mark.parametrize(
    "changes", [{"enabled": False}, {"actor_ids": frozenset({"other"})}, {"workspace_id": "other"}]
)
def test_missing_current_grant_stops_before_source_or_device_lookup(changes) -> None:
    registry, approvals, reads, devices, expected = setup_registry()
    approvals.get.return_value = approvals.get.return_value.model_copy(update=changes)
    with pytest.raises(AccessDenied):
        registry.resolve(actor(), expected.contract.binding)
    reads.resolve.assert_not_called()
    devices.get_device.assert_not_called()


def test_wrong_query_revision_stops_before_source_lookup() -> None:
    registry, approvals, reads, devices, expected = setup_registry()
    approvals.get.return_value = approvals.get.return_value.model_copy(update={"query_revision": "other"})
    with pytest.raises(Conflict):
        registry.resolve(actor(), expected.contract.binding)
    reads.resolve.assert_not_called()


def test_binding_cannot_override_device_parameter() -> None:
    from enterprise_platform.domain.dashboard import BindingProposal, ExecutionBinding

    registry, approvals, reads, devices, expected = setup_registry()
    proposal = expected.contract.binding.proposal
    changed = BindingProposal(
        slot_id=proposal.slot_id,
        query_ref=proposal.query_ref,
        field_map=proposal.field_map,
        parameters={"device": "other"},
    )
    binding = ExecutionBinding.from_proposal(changed, query_revision=expected.contract.binding.query_revision)
    with pytest.raises(InvalidInput):
        registry.resolve(actor(), binding)


def test_declared_parameters_use_existing_typed_binding_without_scope_override() -> None:
    from dataclasses import replace

    from enterprise_platform.application.input_capture import ParameterDeclaration
    from enterprise_platform.domain.dashboard import BindingProposal, ExecutionBinding

    registry, approvals, reads, devices, expected = setup_registry()
    reads.resolve.return_value = expected.registration.model_copy(
        update={
            "read": expected.registration.read.model_copy(update={"parameter_names": frozenset({"device", "limit"})}),
            "parameters": (ParameterDeclaration(input_key="row_limit", parameter_name="limit", kind="integer"),),
        }
    )
    proposal = expected.contract.binding.proposal
    approved = BindingProposal(
        slot_id=proposal.slot_id,
        query_ref=proposal.query_ref,
        field_map=proposal.field_map,
        parameters={"row_limit": 20},
    )
    binding = ExecutionBinding.from_proposal(approved, query_revision=expected.contract.binding.query_revision)
    resolved = registry.resolve(actor(), binding)
    assert resolved.parameters == (("device", "device-1"), ("limit", 20))
    invalid = approved.model_copy(update={"parameters": {"row_limit": True}})
    with pytest.raises(InvalidInput):
        registry.resolve(actor(), replace(binding, proposal_json=invalid.model_dump_json()))


def test_concrete_registry_rechecks_revocation_in_executor() -> None:
    import asyncio
    from dataclasses import replace

    from test_dashboard_query_capture import captured

    from enterprise_platform.application.dashboard_query_execution import DeviceDashboardQueryExecutor
    from enterprise_platform.application.input_capture import SourceReaderPort

    registry, approvals, reads, devices, expected = setup_registry()
    reader = create_autospec(SourceReaderPort, instance=True)

    async def read(entry, parameters):
        approvals.get.return_value = approvals.get.return_value.model_copy(update={"enabled": False})
        return replace(captured(), read_fingerprint=expected.contract.read_fingerprint)

    reader.read.side_effect = read
    with pytest.raises(AccessDenied):
        asyncio.run(DeviceDashboardQueryExecutor(registry, reader).execute(actor(), expected.contract.binding))
    reader.read.assert_awaited_once()
