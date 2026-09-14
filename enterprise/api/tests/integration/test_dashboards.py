"""Real dashboard transactions in explicitly enabled disposable enterprise databases."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import create_autospec, patch

import pytest
from database_environment import ci_public_migration_enabled, database_tests_enabled
from sqlalchemy import select
from test_repository import repository as repository

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.dashboard_contracts import DashboardCreateCommand
from enterprise_platform.application.dashboard_refresh_service import DashboardRecord, DashboardRefreshService
from enterprise_platform.application.dashboard_service import DashboardService, DashboardTemplates
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.domain import dashboard as d
from enterprise_platform.persistence.dashboard_models import DashboardBase
from enterprise_platform.persistence.dashboards import SqlAlchemyDashboardRepository
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.repository import SqlAlchemyRepository

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)


@pytest.fixture
def dashboards(repository: SqlAlchemyRepository) -> SqlAlchemyDashboardRepository:
    with repository._sessions() as session:
        DashboardBase.metadata.create_all(session.get_bind())
    return SqlAlchemyDashboardRepository(repository._sessions)


def initial(workspace: str = "w") -> DashboardRecord:
    design = d.DesignSnapshot("equipment", 1, 1, '{"style":"fixed"}', "lynx", "1")
    return DashboardRecord(workspace, "dashboard-1", 1, design, (), d.RefreshState(design.identity))


def test_public_schema_after_guarded_dashboard_migration() -> None:
    if not ci_public_migration_enabled(os.environ, "ENTERPRISE_CI_DASHBOARD_MIGRATED_PUBLIC"):
        pytest.skip("Requires the guarded CI public migration step")
    from sqlalchemy import create_engine, inspect

    from enterprise_platform.persistence.migrate import validate_target
    from enterprise_platform.persistence.migrate_dashboards import prerequisite_metadata
    from enterprise_platform.persistence.migrate_sources import require_schema

    url = validate_target(os.environ["ENTERPRISE_TEST_DATABASE_URL"], "enterprise_test")
    engine = create_engine(url, hide_parameters=True)
    try:
        metadata = prerequisite_metadata()
        for table in DashboardBase.metadata.sorted_tables:
            table.to_metadata(metadata)
        with engine.connect() as connection:
            require_schema(inspect(connection), metadata, schema="public")
    finally:
        engine.dispose()


def test_list_keyset_pages_are_workspace_scoped(dashboards: SqlAlchemyDashboardRepository) -> None:
    for workspace in ("w", "other"):
        for dashboard_id in ("dashboard-c", "dashboard-a", "dashboard-b"):
            dashboards.create(replace(initial(workspace), dashboard_id=dashboard_id), actor_id="actor-1")
    first = dashboards.list("w", after=None, limit=2)
    assert [record.dashboard_id for record in first] == ["dashboard-a", "dashboard-b"]
    assert all(record.workspace_id == "w" for record in first)
    last = dashboards.list("w", after=first[-1].dashboard_id, limit=2)
    assert [record.dashboard_id for record in last] == ["dashboard-c"]
    assert dashboards.list("w", after="dashboard-c", limit=2) == ()
    assert dashboards.list("missing", after=None, limit=2) == ()


def test_creation_retries_and_concurrency_commit_one_record_and_audit(
    dashboards: SqlAlchemyDashboardRepository,
    repository: SqlAlchemyRepository,
) -> None:
    templates = create_autospec(DashboardTemplates, instance=True)
    templates.get.return_value = initial().design
    service = DashboardService(dashboards, create_autospec(DashboardRefreshService), templates=templates)
    principal = Principal(actor_id="actor-1", workspace_id="w", workspace_role="editor", display_name="Editor")
    command = DashboardCreateCommand(
        template_id=initial().design.template_id, expected_design_identity=initial().design.identity
    )

    def create():
        return asyncio.run(service.create(principal, command, request_key="same-request"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert results[0].id == results[1].id == create().id
    assert len(dashboards.list("w", after=None, limit=10)) == 1
    with repository._sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.workspace_id == "w")).all()
        assert len(events) == 1


def test_rebinding_is_revision_fenced_audited_and_noop_preserves_record(
    dashboards: SqlAlchemyDashboardRepository,
    repository: SqlAlchemyRepository,
) -> None:
    design = d.DesignSnapshot(
        "equipment",
        1,
        1,
        '{"style":"fixed"}',
        "lynx",
        "1",
        slots=(d.SlotContract(slot_id="a", columns=(d.ColumnContract(name="value", kind="integer"),), required=False),),
    )
    original = replace(
        initial(),
        design=design,
        state=d.RefreshState(design.identity),
        name="Named screen",
        creation_request_hash="a" * 64,
    )
    dashboards.create(original, actor_id="actor-1")
    binding = d.ExecutionBinding.from_proposal(
        d.BindingProposal(slot_id="a", query_ref="count", field_map={"value": "count"}), query_revision="v1"
    )
    updated = dashboards.save_bindings(
        "w",
        original.dashboard_id,
        expected_revision=1,
        expected_design_identity=design.identity,
        bindings=(binding,),
        actor_id="actor-1",
    )
    assert updated.revision == 2 and updated.bindings == (binding,)
    same = dashboards.save_bindings(
        "w",
        original.dashboard_id,
        expected_revision=2,
        expected_design_identity=design.identity,
        bindings=(binding,),
        actor_id="actor-1",
    )
    assert same == updated
    with pytest.raises(Conflict):
        dashboards.save_bindings(
            "w",
            original.dashboard_id,
            expected_revision=1,
            expected_design_identity=design.identity,
            bindings=(),
            actor_id="actor-1",
        )
    cleared = dashboards.save_bindings(
        "w",
        original.dashboard_id,
        expected_revision=2,
        expected_design_identity=design.identity,
        bindings=(),
        actor_id="actor-1",
    )
    assert cleared.revision == 3 and not cleared.bindings
    assert cleared.design == design and cleared.name == original.name
    assert cleared.creation_request_hash == original.creation_request_hash
    with repository._sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.workspace_id == "w")).all()
        assert len(events) == 3


def commit(store: SqlAlchemyDashboardRepository, current: DashboardRecord, attempt: str) -> d.RefreshState:
    state = d.commit_refresh(
        current.design,
        current.bindings,
        current.state,
        d.RefreshBatch(
            attempt,
            current.design.identity,
            current.state.current.batch_id if current.state.current else None,
            (),
            (),
        ),
    )
    return store.commit(
        workspace_id=current.workspace_id,
        dashboard_id=current.dashboard_id,
        expected_revision=current.revision,
        expected_design_identity=current.design.identity,
        expected_bindings_hash=d.binding_set_identity(current.bindings),
        state=state,
        actor_id="actor-1",
    )


def test_create_refresh_and_audit_share_workspace_scope(
    dashboards: SqlAlchemyDashboardRepository, repository: SqlAlchemyRepository
) -> None:
    dashboards.create(initial(), actor_id="actor-1")
    dashboards.create(initial("other"), actor_id="actor-2")
    state = commit(dashboards, initial(), "batch-1")
    saved = dashboards.get("w", "dashboard-1")
    assert saved.revision == 2
    assert saved.state == state
    assert dashboards.get("other", "dashboard-1").revision == 1
    with pytest.raises(NotFound):
        dashboards.get("missing", "dashboard-1")
    with repository._sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.workspace_id == "w").order_by(AuditEventRow.sequence)
        ).all()
        assert [event.event_type for event in events] == ["dashboard.created", "dashboard.refreshed"]


def test_only_one_concurrent_refresh_commits(dashboards: SqlAlchemyDashboardRepository) -> None:
    current = dashboards.create(initial(), actor_id="actor-1")

    def attempt(name: str) -> str:
        try:
            commit(dashboards, current, name)
            return "committed"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ("batch-1", "batch-2")))
    assert sorted(outcomes) == ["committed", "conflict"]
    assert dashboards.get("w", "dashboard-1").revision == 2


def test_audit_failure_rolls_back_state_and_revision(dashboards: SqlAlchemyDashboardRepository) -> None:
    current = dashboards.create(initial(), actor_id="actor-1")
    with patch("enterprise_platform.persistence.dashboards.audit", side_effect=PersistenceError()):
        with pytest.raises(PersistenceError):
            commit(dashboards, current, "batch-1")
    assert dashboards.get("w", "dashboard-1") == current


def test_failure_receipt_increments_revision_and_keeps_last_good_batch(
    dashboards: SqlAlchemyDashboardRepository,
) -> None:
    dashboards.create(initial(), actor_id="actor-1")
    previous = commit(dashboards, initial(), "batch-1")
    current = dashboards.get("w", "dashboard-1")
    failed = replace(
        previous, status="failed", last_attempt_id="batch-2", failures=(d.SlotFailure("metric", "source_query_failed"),)
    )
    result = dashboards.commit(
        workspace_id="w",
        dashboard_id="dashboard-1",
        expected_revision=2,
        expected_design_identity=current.design.identity,
        expected_bindings_hash=d.binding_set_identity(current.bindings),
        state=failed,
        actor_id="actor-1",
    )
    assert result.current == previous.current
    assert dashboards.get("w", "dashboard-1").revision == 3
