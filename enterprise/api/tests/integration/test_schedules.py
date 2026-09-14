"""Explicitly enabled disposable real transactions for schedule ownership and audited lifecycle."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec

import pytest
from database_environment import ci_public_migration_enabled, database_tests_enabled
from sqlalchemy import create_engine, inspect, select
from test_repository import lane
from test_repository import repository as repository

from enterprise_platform.application.contracts import BindingWrite, Principal, RunSpec
from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.input_capture import ImmutableReadCatalog, ParameterDeclaration, RegisteredRead
from enterprise_platform.application.schedule_polling import SchedulePoller
from enterprise_platform.application.schedule_service import ScheduleAuthority, ScheduleService, ServiceGrant
from enterprise_platform.application.scheduling import (
    IntervalSchedule,
    ScheduleScanCursor,
    plan_tick,
    prepare_schedule_run,
)
from enterprise_platform.application.service import BusinessService
from enterprise_platform.domain.data_sources import DatabaseSourceConfig, SourceRef, SqlRead
from enterprise_platform.persistence.migrate import validate_target
from enterprise_platform.persistence.migrate_schedules import prerequisite_metadata
from enterprise_platform.persistence.migrate_sources import require_schema
from enterprise_platform.persistence.models import AuditEventRow, RunRow
from enterprise_platform.persistence.repository import SqlAlchemyRepository
from enterprise_platform.persistence.schedule_models import ScheduleBase, ScheduleRow
from enterprise_platform.persistence.schedules import SqlAlchemyScheduleRepository

type ScheduleFixture = tuple[SqlAlchemyScheduleRepository, SqlAlchemyRepository, IntervalSchedule]

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)


@pytest.fixture
def scheduled(
    repository: SqlAlchemyRepository,
) -> tuple[SqlAlchemyScheduleRepository, SqlAlchemyRepository, IntervalSchedule]:
    binding = lane(repository)
    payload = BindingWrite.model_validate(
        RunSpec.from_binding(binding).model_dump(
            exclude={"binding_id", "binding_revision", "device_id", "scenario", "parameters", "recompute_of"}
        )
        | {"manifest": {"input_keys": ["window_start", "window_end"]}}
    )
    binding = repository.put_binding(
        binding.workspace_id,
        binding.device_id,
        binding.scenario,
        payload,
        expected_revision=binding.revision,
        actor_id="admin",
    )
    now = datetime.now(UTC)
    with repository._sessions() as session:
        ScheduleBase.metadata.create_all(session.get_bind())
    value = IntervalSchedule(
        id="schedule-1",
        workspace_id=binding.workspace_id,
        revision=1,
        binding_id=binding.id,
        binding_revision=binding.revision,
        device_id=binding.device_id,
        scenario=binding.scenario,
        service_actor_id="service-actor",
        anchor_at=now,
        next_due_at=now,
        interval_seconds=60,
        window_seconds=300,
        grace_seconds=10,
        missed_policy="skip",
        enabled=False,
    )
    return SqlAlchemyScheduleRepository(repository._sessions, clock=lambda: now), repository, value


def test_same_binding_has_one_owner_and_enable_has_one_concurrent_winner(scheduled: ScheduleFixture) -> None:
    repo, base, value = scheduled

    def create(index: int) -> IntervalSchedule | None:
        try:
            return repo.create(value.model_copy(update={"id": f"schedule-{index}"}), actor_id="admin")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [v for v in pool.map(create, range(2)) if v is not None]
    assert len(winners) == 1
    saved = winners[0]
    assert not saved.enabled
    with pytest.raises(Conflict):
        repo.create(value.model_copy(update={"id": "another-owner"}), actor_id="admin")

    def enable(_: int) -> IntervalSchedule | None:
        try:
            return repo.set_enabled(value.workspace_id, saved.id, expected_revision=1, enabled=True, actor_id="admin")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(v is not None for v in pool.map(enable, range(2))) == 1
    assert repo.get(value.workspace_id, saved.id).revision == 2
    with base._sessions() as session:
        assert len(session.scalars(select(ScheduleRow)).all()) == 1
        events = session.scalars(select(AuditEventRow)).all()
        assert sum(e.event_type == "schedule_created" for e in events) == 1
        assert sum(e.event_type == "schedule_enabled" for e in events) == 1


def test_due_scan_filters_workspace_pause_and_future_cursor_without_consuming(scheduled: ScheduleFixture) -> None:
    repo, _, value = scheduled
    repo.create(value, actor_id="admin")
    assert repo.list_due(value.workspace_id, now=value.anchor_at) == ()
    enabled = repo.set_enabled(value.workspace_id, value.id, expected_revision=1, enabled=True, actor_id="admin")
    assert repo.list_due("other-workspace", now=value.anchor_at) == ()
    assert repo.list_due(value.workspace_id, now=value.anchor_at, service_actor_id="other-service") == ()
    assert repo.list_due(value.workspace_id, now=value.anchor_at - timedelta(microseconds=1)) == ()
    assert repo.list_due(value.workspace_id, now=value.anchor_at, limit=1) == (enabled,)
    assert (
        repo.list_due(
            value.workspace_id,
            now=value.anchor_at,
            after=ScheduleScanCursor(next_due_at=enabled.next_due_at, schedule_id=enabled.id),
        )
        == ()
    )
    assert repo.list_due(value.workspace_id, now=value.anchor_at, limit=1) == (enabled,)
    assert repo.get(value.workspace_id, value.id) == enabled


def test_create_audit_failure_rolls_back_ownership(scheduled: ScheduleFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    import enterprise_platform.persistence.schedules as module

    repo, base, value = scheduled

    def fail(*args: object, **kwargs: object) -> None:
        raise PersistenceError("injected_audit_failure")

    monkeypatch.setattr(module, "audit", fail)
    with pytest.raises(PersistenceError):
        repo.create(value, actor_id="admin")
    with base._sessions() as session:
        assert session.scalar(select(ScheduleRow)) is None


def test_pause_audit_failure_preserves_revision_enabled_state_and_cursor(
    scheduled: ScheduleFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import enterprise_platform.persistence.schedules as module

    repo, _, value = scheduled
    repo.create(value, actor_id="admin")
    enabled = repo.set_enabled(value.workspace_id, value.id, expected_revision=1, enabled=True, actor_id="admin")

    def fail(*args: object, **kwargs: object) -> None:
        raise PersistenceError("injected_audit_failure")

    monkeypatch.setattr(module, "audit", fail)
    with pytest.raises(PersistenceError):
        repo.set_enabled(value.workspace_id, value.id, expected_revision=2, enabled=False, actor_id="admin")
    assert repo.get(value.workspace_id, value.id) == enabled


def test_foreign_workspace_cannot_read_or_pause_schedule(scheduled: ScheduleFixture) -> None:
    repo, _, value = scheduled
    repo.create(value, actor_id="admin")
    with pytest.raises(NotFound):
        repo.get("foreign", value.id)
    with pytest.raises(NotFound):
        repo.set_enabled("foreign", value.id, expected_revision=1, enabled=True, actor_id="admin")
    assert repo.get(value.workspace_id, value.id) == value


def test_explicit_ci_0008_public_schema() -> None:
    if not ci_public_migration_enabled(os.environ, "ENTERPRISE_CI_SCHEDULE_MIGRATED_PUBLIC"):
        pytest.skip("Requires explicit CI 0008 migration")
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    assert url, "Explicit migration gate requires its dedicated database"
    metadata = prerequisite_metadata()
    for table in ScheduleBase.metadata.sorted_tables:
        table.to_metadata(metadata)
    assert len(metadata.tables) == 12
    engine = create_engine(validate_target(url, "enterprise_test"), hide_parameters=True)
    try:
        require_schema(inspect(engine), metadata, schema="public")
    finally:
        engine.dispose()


def prepared_tick(scheduled: ScheduleFixture) -> tuple[IntervalSchedule, RunSpec]:
    repo, base, value = scheduled
    repo.create(value, actor_id="admin")
    enabled = repo.set_enabled(value.workspace_id, value.id, expected_revision=1, enabled=True, actor_id="admin")
    occurrence = plan_tick(enabled, value.anchor_at).occurrence
    assert occurrence is not None
    binding = base.get_binding(value.workspace_id, value.device_id, value.scenario)
    spec = prepare_schedule_run(enabled, binding, value.anchor_at)
    assert spec is not None
    return enabled, spec


def test_tick_race_creates_one_run_and_advances_cursor_once(scheduled: ScheduleFixture) -> None:
    repo, base, _ = scheduled
    value, spec = prepared_tick(scheduled)

    def commit(_: int) -> bool:
        try:
            result = repo.commit_tick(value, spec)
            assert result.run is not None
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(commit, range(2))) == 1
    assert repo.get(value.workspace_id, value.id).next_due_at == plan_tick(value, value.anchor_at).next_due_at
    with base._sessions() as session:
        rows = session.scalars(select(RunRow)).all()
        assert len(rows) == 1 and rows[0].actor_id == value.service_actor_id
        events = session.scalars(select(AuditEventRow)).all()
        assert sum(e.event_type == "run.queued" for e in events) == 1
        assert sum(e.event_type == "schedule_tick_enqueued" for e in events) == 1


def test_service_tick_prepares_and_commits_real_run_without_duplicate_on_repoll(scheduled: ScheduleFixture) -> None:
    repo, base, value = scheduled
    enabled, _ = prepared_tick(scheduled)
    principal = Principal(
        workspace_id=value.workspace_id,
        actor_id=value.service_actor_id,
        workspace_role="editor",
        display_name="Scheduler",
    )
    authority = create_autospec(ScheduleAuthority, instance=True)
    authority.get_grant.return_value = ServiceGrant(
        principal=principal,
        device_ids=(value.device_id,),
        scenarios=(value.scenario,),
        enabled=True,
        expires_at=value.anchor_at + timedelta(hours=1),
    )
    authority.has_native_timer.return_value = False
    bound = base.get_binding(value.workspace_id, value.device_id, value.scenario)
    source = SourceRef(workspace_id=bound.workspace_id, source_id=bound.source_id, revision=bound.source_revision)
    registration = RegisteredRead(
        connection=DatabaseSourceConfig(
            source=source,
            dialect="postgresql",
            connection_url="postgresql://fixture:fixture@localhost/fixture",
            allowed_tables=frozenset({"measurements"}),
            read_only_role=True,
        ),
        read=SqlRead(
            source=source,
            read_id=bound.read_id,
            revision=bound.read_revision,
            sql=(
                "SELECT device, value FROM measurements WHERE device = :device "
                "AND at >= :window_start AND at < :window_end"
            ),
        ),
        device_ids=frozenset({bound.device_id}),
        device_parameter="device",
        device_column="device",
        parameters=tuple(
            ParameterDeclaration(input_key=key, parameter_name=key, kind="datetime")
            for key in ("window_start", "window_end")
        ),
    )
    service = ScheduleService(
        repo,
        BusinessService(base),
        authority,
        reads=ImmutableReadCatalog((registration,)),
        clock=lambda: value.anchor_at,
    )
    poller = SchedulePoller(repo, service, clock=lambda: value.anchor_at)
    first = asyncio.run(poller.poll(principal))
    second = asyncio.run(poller.poll(principal))
    assert len(first) == 1 and first[0].schedule_id == enabled.id and first[0].status == "queued"
    assert second == ()
    with base._sessions() as session:
        rows = session.scalars(select(RunRow)).all()
        assert len(rows) == 1 and rows[0].run_id == first[0].run_id


def test_tick_audit_failure_rolls_back_both_run_and_cursor(
    scheduled: ScheduleFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enterprise_platform.persistence.schedules as module

    repo, base, _ = scheduled
    value, spec = prepared_tick(scheduled)

    def fail(*args: object, **kwargs: object) -> None:
        raise PersistenceError("injected_schedule_audit_failure")

    monkeypatch.setattr(module, "audit", fail)
    with pytest.raises(PersistenceError):
        repo.commit_tick(value, spec)
    assert repo.get(value.workspace_id, value.id) == value
    with base._sessions() as session:
        assert session.scalar(select(RunRow)) is None
        assert not any(e.event_type == "run.queued" for e in session.scalars(select(AuditEventRow)))
