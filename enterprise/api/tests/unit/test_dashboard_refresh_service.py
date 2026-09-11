import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, create_autospec

import pytest

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.dashboard_refresh_service import (
    DashboardQueryExecutor,
    DashboardRecord,
    DashboardRefreshService,
    DashboardRepository,
)
from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.domain import dashboard as d
from enterprise_platform.domain.data_sources import DataSourceError


def principal() -> Principal:
    return Principal(actor_id="actor-1", workspace_id="workspace-1", workspace_role="editor", display_name="Editor")


def record() -> DashboardRecord:
    design = d.DesignSnapshot(
        template_id="equipment",
        template_revision=1,
        design_revision=1,
        visual_json='{"style":"fixed"}',
        renderer_build_id="lynx",
        component_schema_version="1",
        slots=tuple(
            d.SlotContract(slot_id=slot, columns=(d.ColumnContract(name="value", kind="integer"),))
            for slot in ("a", "b")
        ),
    )
    bindings = tuple(
        d.ExecutionBinding.from_proposal(
            d.BindingProposal(slot_id=slot, query_ref=f"query-{slot}", field_map={"value": "count"}),
            query_revision="v1",
        )
        for slot in ("a", "b")
    )
    return DashboardRecord("workspace-1", "dashboard-1", 1, design, bindings, d.RefreshState(design.identity))


def result(binding: d.ExecutionBinding, value: int = 5) -> d.QueryResult:
    return d.QueryResult(binding, (d.QueryColumn(name="count", kind="integer"),), ({"count": value},))


def setup_service() -> tuple[DashboardRefreshService, object, object]:
    repository = create_autospec(DashboardRepository, instance=True)
    queries = create_autospec(DashboardQueryExecutor, instance=True)
    repository.get.return_value = record()
    repository.commit.side_effect = lambda **values: values["state"]
    queries.execute = AsyncMock(side_effect=lambda actor, binding: result(binding))
    return DashboardRefreshService(repository, queries, id_factory=lambda: "batch-1"), repository, queries


def test_refresh_commits_one_complete_batch_with_revision_fence_and_actor() -> None:
    service, repository, queries = setup_service()
    state = asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    assert state.status == "ready"
    assert len(state.current.slots) == 2
    assert queries.execute.await_count == 2
    repository.commit.assert_called_once_with(
        workspace_id="workspace-1",
        dashboard_id="dashboard-1",
        expected_revision=1,
        expected_design_identity=record().design.identity,
        expected_bindings_hash=d.binding_set_identity(record().bindings),
        state=state,
        actor_id="actor-1",
    )


def test_query_failure_keeps_entire_previous_batch_and_opaque_error() -> None:
    service, repository, queries = setup_service()
    previous = asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    repository.get.return_value = replace(record(), revision=2, state=previous)
    service = DashboardRefreshService(repository, queries, id_factory=lambda: "batch-2")
    queries.execute.side_effect = [result(record().bindings[0], 999), DataSourceError("timeout")]
    state = asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=2))
    assert state.status == "failed"
    assert state.current == previous.current
    assert state.failures == (d.SlotFailure("b", "source_query_failed"),)


@pytest.mark.parametrize("role", ["normal", "dataset_operator"])
def test_read_only_users_never_execute_queries(role: str) -> None:
    service, repository, queries = setup_service()
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.refresh(principal().model_copy(update={"workspace_role": role}), "dashboard-1", expected_revision=1)
        )
    repository.get.assert_not_called()
    queries.execute.assert_not_awaited()


@pytest.mark.parametrize("change", [{"workspace_id": "other"}, {"dashboard_id": "other"}])
def test_repository_scope_mismatch_stops_before_query(change: dict[str, str]) -> None:
    service, repository, queries = setup_service()
    repository.get.return_value = replace(record(), **change)
    with pytest.raises(AccessDenied):
        asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    queries.execute.assert_not_awaited()


def test_stale_revision_stops_before_query() -> None:
    service, repository, queries = setup_service()
    with pytest.raises(Conflict):
        asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=2))
    queries.execute.assert_not_awaited()
    repository.commit.assert_not_called()


def test_authorization_failure_is_not_recorded_as_a_successful_refresh() -> None:
    service, repository, queries = setup_service()
    queries.execute.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    repository.commit.assert_not_called()


def test_concurrent_commit_conflict_propagates_without_retrying_reads() -> None:
    service, repository, queries = setup_service()
    repository.commit.side_effect = Conflict()
    with pytest.raises(Conflict):
        asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    assert queries.execute.await_count == 2
    repository.commit.assert_called_once()


@pytest.mark.parametrize("kind", ["duplicate", "missing", "unknown", "field"])
def test_invalid_saved_binding_configuration_never_runs_queries(kind: str) -> None:
    service, repository, queries = setup_service()
    original = record()
    bindings = original.bindings
    if kind == "duplicate":
        bindings = (bindings[0], bindings[0])
    elif kind == "missing":
        bindings = bindings[:1]
    else:
        proposal = d.BindingProposal(
            slot_id="unknown" if kind == "unknown" else "a",
            query_ref="q",
            field_map={"wrong": "count"},
        )
        bindings = (d.ExecutionBinding.from_proposal(proposal, query_revision="v1"), bindings[1])
    repository.get.return_value = replace(original, bindings=bindings)
    from enterprise_platform.application.errors import InvalidInput

    with pytest.raises(InvalidInput):
        asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    queries.execute.assert_not_awaited()


def test_cancelled_refresh_does_not_commit_partial_results() -> None:
    service, repository, queries = setup_service()
    queries.execute.side_effect = [result(record().bindings[0]), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    repository.commit.assert_not_called()


def test_result_from_another_query_revision_fails_whole_first_batch() -> None:
    service, repository, queries = setup_service()
    wrong = replace(record().bindings[0], query_revision="wrong")
    queries.execute.side_effect = [result(wrong), result(record().bindings[1])]
    state = asyncio.run(service.refresh(principal(), "dashboard-1", expected_revision=1))
    assert state.status == "failed"
    assert state.current is None
    assert state.failures == (d.SlotFailure("a", "binding_conflict"),)
