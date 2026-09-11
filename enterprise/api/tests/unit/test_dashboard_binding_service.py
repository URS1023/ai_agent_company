import asyncio
from dataclasses import replace
from unittest.mock import create_autospec, patch

import pytest
from test_dashboard_query_execution import plan
from test_dashboard_refresh_service import principal, record

from enterprise_platform.application.dashboard_binding_service import DashboardBindingService
from enterprise_platform.application.dashboard_binding_update import replace_dashboard_bindings
from enterprise_platform.application.dashboard_query_execution import DeviceDashboardQueryRegistry
from enterprise_platform.application.dashboard_refresh_service import DashboardRepository
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.domain import dashboard as d


def setup():
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.return_value = record()
    repository.save_bindings.side_effect = lambda *args, **kwargs: replace_dashboard_bindings(
        record(), kwargs["bindings"]
    )
    registry = create_autospec(DeviceDashboardQueryRegistry, instance=True)
    approved = plan()
    registry.resolve.side_effect = lambda actor, binding: replace(
        approved,
        contract=replace(approved.contract, binding=binding, columns=(d.QueryColumn(name="count", kind="integer"),)),
    )
    return DashboardBindingService(repository, registry), repository, registry


def save(service, bindings=None, actor=None, revision=1):
    return asyncio.run(
        service.save(
            actor or principal(),
            "dashboard-1",
            expected_revision=revision,
            expected_design_identity=record().design.identity,
            bindings=record().bindings if bindings is None else bindings,
        )
    )


def test_binding_save_rechecks_current_query_grants_and_uses_revision_fence() -> None:
    service, repository, registry = setup()
    saved = save(service)
    assert saved == record()
    assert registry.resolve.call_count == 4
    repository.save_bindings.assert_called_once_with(
        "workspace-1",
        "dashboard-1",
        expected_revision=1,
        expected_design_identity=record().design.identity,
        bindings=record().bindings,
        actor_id="actor-1",
    )


def test_stale_revision_and_read_only_actor_never_resolve_or_save() -> None:
    service, repository, registry = setup()
    with pytest.raises(Conflict):
        save(service, revision=2)
    with pytest.raises(AccessDenied):
        save(service, actor=principal().model_copy(update={"workspace_role": "normal"}))
    registry.resolve.assert_not_called()
    repository.save_bindings.assert_not_called()


def test_invalid_slot_mapping_is_rejected_before_authority_io() -> None:
    service, repository, registry = setup()
    invalid = d.ExecutionBinding.from_proposal(
        d.BindingProposal(slot_id="unknown", query_ref="q", field_map={"value": "count"}), query_revision="v1"
    )
    with pytest.raises(InvalidInput):
        save(service, (invalid,))
    registry.resolve.assert_not_called()
    repository.save_bindings.assert_not_called()


def test_metadata_type_mismatch_does_not_save_or_invent_a_query_result() -> None:
    service, repository, registry = setup()
    approved = plan()
    registry.resolve.side_effect = lambda actor, binding: replace(
        approved,
        contract=replace(approved.contract, binding=binding, columns=(d.QueryColumn(name="count", kind="string"),)),
    )
    with pytest.raises(InvalidInput):
        save(service)
    repository.save_bindings.assert_not_called()


def test_revocation_during_final_recheck_prevents_commit() -> None:
    service, repository, registry = setup()
    resolve = registry.resolve.side_effect
    calls = 0

    def revoked(actor, binding):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AccessDenied()
        return resolve(actor, binding)

    registry.resolve.side_effect = revoked
    with pytest.raises(AccessDenied):
        save(service)
    repository.save_bindings.assert_not_called()


def test_unbinding_all_slots_requires_manage_but_no_query_execution() -> None:
    service, repository, registry = setup()
    saved = save(service, ())
    assert saved.bindings == () and saved.state.status == "empty"
    registry.resolve.assert_not_called()


def test_real_file_grant_and_registry_are_checked_without_http_execution(tmp_path) -> None:
    from test_dashboard_query_approvals import write
    from test_dashboard_query_registry import setup_registry

    from enterprise_platform.adapters.dashboard_query_approvals import FileDashboardQueryApprovals
    from enterprise_platform.application.dashboard_query_registry import RegisteredDeviceDashboardQueries

    _, _, reads, devices, expected = setup_registry()
    path = tmp_path / "approvals.json"
    write(path)
    registry = RegisteredDeviceDashboardQueries(FileDashboardQueryApprovals(path), reads, devices)
    old = record()
    design = replace(
        old.design,
        slots=(
            d.SlotContract(
                slot_id="metric",
                columns=(
                    d.ColumnContract(name="name", kind="string"),
                    d.ColumnContract(name="value", kind="decimal", unit="%"),
                ),
            ),
        ),
    )
    old = replace(old, design=design, bindings=(), state=d.RefreshState(design.identity))
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.return_value = old
    repository.save_bindings.side_effect = lambda *args, **kwargs: replace_dashboard_bindings(old, kwargs["bindings"])
    service = DashboardBindingService(repository, registry)

    def save_current():
        return asyncio.run(
            service.save(
                principal(),
                old.dashboard_id,
                expected_revision=1,
                expected_design_identity=design.identity,
                bindings=(expected.contract.binding,),
            )
        )

    with patch("httpx.AsyncClient.send", side_effect=AssertionError("Binding save must not execute HTTP")):
        assert save_current().bindings == (expected.contract.binding,)
        repository.save_bindings.reset_mock()
        write(path, enabled=False)
        with pytest.raises(AccessDenied):
            save_current()
        repository.save_bindings.assert_not_called()
