"""Real transactional tests run by explicit opt-in against disposable databases.

Never read Dify database configuration. PostgreSQL uses a newly generated test schema;
SQLite uses a temporary file. Local PostgreSQL requires a dedicated loopback test target.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from database_environment import database_tests_enabled, local_database_connect_args

from enterprise_platform.application.contracts import (
    BindingWrite,
    BusinessResult,
    DeviceCreate,
    DeviceUpdate,
    RunSpec,
    canonical_hash,
)
from enterprise_platform.application.errors import Conflict, EnterpriseError, InvalidInput, InvalidState, NotFound

if TYPE_CHECKING:
    from pathlib import Path

    from enterprise_platform.application.contracts import Binding, JsonObject, Run
    from enterprise_platform.persistence.repository import SqlAlchemyRepository

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)


def test_device_search_filters_database_before_pagination_and_count(repository: SqlAlchemyRepository) -> None:
    for code, name, department in [
        ("0001", "温度仪表", "生产一部"),
        ("0002", "温度 Gauge", "生产一部"),
        ("0003", "温度仪表", "生产一部扩展"),
        ("0004", "压力仪表", "生产一部"),
    ]:
        repository.create_device("w", DeviceCreate(device_code=code, name=name, department=department), actor_id="a")
    repository.create_device(
        "other", DeviceCreate(device_code="0001", name="温度仪表", department="生产一部"), actor_id="a"
    )
    removed = repository.create_device(
        "w", DeviceCreate(device_code="0099", name="温度仪表", department="生产一部"), actor_id="a"
    )
    repository.delete_device("w", removed.id, expected_revision=1, actor_id="a")

    result = repository.list_devices("w", q=" 温度 ", department=" 生产一部 ", offset=1, limit=1)

    assert result.total == 2
    assert [item.device_code for item in result.items] == ["0002"]
    assert repository.list_devices("w", q="0001").items[0].device_code == "0001"
    assert repository.list_devices("w", q="gAuGe").total == 1
    assert repository.list_devices("w", q="温度", department="生产一部", offset=99, limit=1).total == 2
    assert repository.list_devices("w", q="不存在").total == 0
    assert repository.list_devices("w", q="  ", department="\t").total == 4


def test_device_search_treats_percent_underscore_and_slash_as_literal_text(repository: SqlAlchemyRepository) -> None:
    records = [
        ("000%_/", "literal marker", "生产%_/"),
        ("000xyz", "wildcard decoy", "生产xyz"),
        ("plain", "测量%_/值", "生产%_/"),
        ("another", "测量ABC值", "生产%_/"),
    ]
    for code, name, department in records:
        repository.create_device("w", DeviceCreate(device_code=code, name=name, department=department), actor_id="a")
    assert repository.list_devices("w", q="000%_/").total == 1
    assert repository.list_devices("w", q="测量%_/").total == 1
    assert repository.list_devices("w", q="%_/").total == 2
    assert repository.list_devices("w", department="生产%_/").total == 3
    assert repository.list_devices("w", q="' OR 1=1 --").total == 0


@pytest.fixture(params=["sqlite", "postgresql"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SqlAlchemyRepository]:
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.schema import CreateSchema, DropSchema

    from enterprise_platform.persistence.models import Base
    from enterprise_platform.persistence.repository import SqlAlchemyRepository

    postgres = request.param == "postgresql"
    url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL") if postgres else f"sqlite+pysqlite:///{tmp_path / 'test.db'}"
    if not url:
        pytest.skip("Dedicated ENTERPRISE_TEST_DATABASE_URL is not configured")
    connect_args = local_database_connect_args(url) if postgres and os.environ.get("CI") != "true" else {}
    engine = create_engine(url, connect_args=connect_args)
    schema = f"enterprise_test_{uuid4().hex}" if postgres else None
    if schema:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    else:
        from sqlite3 import Connection

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection: Connection, connection_record: object) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

        scoped_engine = engine
    try:
        Base.metadata.create_all(scoped_engine)
        yield SqlAlchemyRepository(sessionmaker(scoped_engine, expire_on_commit=False))
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def lane(repository: SqlAlchemyRepository, workspace: str = "w", code: str = "0001") -> Binding:
    device = repository.create_device(workspace, DeviceCreate(device_code=code, name="Pump"), actor_id="actor")
    return repository.put_binding(
        workspace,
        device.id,
        "alert",
        BindingWrite(
            app_id="app",
            workflow_id=UUID("00000000-0000-4000-8000-000000000001"),
            specification_revision="spec-1",
            secret_ref="secrets/app-v1",
            source_id="existing-records",
            source_revision="source-v1",
            read_id="read-1",
            read_revision="read-1",
        ),
        expected_revision=None,
        actor_id="actor",
    )


def enqueue(
    repository: SqlAlchemyRepository, binding: Binding, key: str = "req-1", snapshot: JsonObject | None = None
) -> Run:
    spec = RunSpec.from_binding(binding)
    digest = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": snapshot})
    return repository.enqueue_run(binding.workspace_id, key, digest, spec, snapshot, actor_id="actor")


def test_device_crud_scope_pagination_revision_and_soft_delete(repository: SqlAlchemyRepository) -> None:
    first = repository.create_device("a", DeviceCreate(device_code="0001", name="Original"), actor_id="actor")
    repository.create_device("b", DeviceCreate(device_code="0001", name="Other tenant"), actor_id="actor")
    assert first.device_code == "0001"
    with pytest.raises(NotFound):
        repository.get_device("b", first.id)
    updated = repository.update_device(
        "a", first.id, DeviceUpdate(device_code="0001", name="Revised"), expected_revision=1, actor_id="actor"
    )
    assert updated.revision == 2
    with pytest.raises(Conflict):
        repository.update_device(
            "a", first.id, DeviceUpdate(device_code="0001", name="Stale"), expected_revision=1, actor_id="actor"
        )
    assert repository.list_devices("a", offset=0, limit=1).items == (updated,)
    repository.delete_device("a", first.id, expected_revision=2, actor_id="actor")
    assert repository.list_devices("a").total == 0
    with pytest.raises(NotFound):
        repository.get_device("a", first.id)


@pytest.mark.parametrize("state", ["queued", "claimed", "dispatched", "uncertain"])
def test_outstanding_runs_protect_device_code_and_department_not_display_fields(
    repository: SqlAlchemyRepository, state: str
) -> None:
    binding = lane(repository)
    run = enqueue(repository, binding)
    if state != "queued":
        repository.claim_run("w", run.id, "nonce", actor_id="worker")
    if state == "dispatched":
        repository.mark_dispatched("w", run.id, "nonce", "native-1", actor_id="worker")
    if state == "uncertain":
        repository.mark_uncertain("w", run.id, "nonce", "timeout", actor_id="worker")
    for command in (
        DeviceUpdate(device_code="0002", name="Pump"),
        DeviceUpdate(device_code="0001", name="Pump", department="other"),
    ):
        with pytest.raises(InvalidState, match="device_scope_has_outstanding_runs"):
            repository.update_device("w", binding.device_id, command, expected_revision=1, actor_id="actor")
        assert repository.get_device("w", binding.device_id).revision == 1
    display = DeviceUpdate(device_code="0001", name="Renamed", description="Updated label")
    assert (
        repository.update_device("w", binding.device_id, display, expected_revision=1, actor_id="actor").revision == 2
    )


def test_enqueuing_transaction_blocks_concurrent_device_scope_change(repository: SqlAlchemyRepository) -> None:
    from enterprise_platform.persistence.repository import SqlAlchemyRepository

    binding = lane(repository)
    entered, release = Event(), Event()

    def held_run_id() -> str:
        entered.set()
        assert release.wait(10)
        return str(uuid4())

    writer = SqlAlchemyRepository(repository._sessions, id_factory=held_run_id)

    def change_scope() -> None:
        assert entered.wait(10)
        release.set()
        with pytest.raises(InvalidState, match="device_scope_has_outstanding_runs"):
            repository.update_device(
                "w",
                binding.device_id,
                DeviceUpdate(device_code="0002", name="Pump"),
                expected_revision=1,
                actor_id="actor",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        queued = executor.submit(enqueue, writer, binding)
        changed = executor.submit(change_scope)
        assert queued.result(timeout=15).status == "queued"
        changed.result(timeout=15)
    assert repository.get_device("w", binding.device_id).device_code == "0001"


def test_binding_lane_is_stable_and_revision_checked(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    definition = BindingWrite.model_validate(binding.model_dump(include=set(BindingWrite.model_fields)))
    with pytest.raises(Conflict):
        repository.put_binding("w", binding.device_id, "alert", definition, expected_revision=None, actor_id="actor")
    changed = repository.put_binding("w", binding.device_id, "alert", definition, expected_revision=1, actor_id="actor")
    assert changed.id == binding.id and changed.revision == 2
    assert repository.get_binding("w", binding.device_id, "alert") == changed
    with pytest.raises(NotFound):
        repository.get_binding("other", binding.device_id, "alert")


def test_request_key_reuses_only_the_identical_payload(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    first = enqueue(repository, binding)
    assert enqueue(repository, binding).id == first.id
    with pytest.raises(Conflict):
        enqueue(repository, binding, snapshot={"different": True})
    spec = RunSpec.from_binding(binding)
    with pytest.raises(InvalidInput):
        repository.enqueue_run("w", "forged", first.payload_hash, spec, {"different": True}, actor_id="actor")
    assert repository.list_runs("w", binding.device_id, "alert").total == 1


def test_concurrent_identical_requests_create_one_run(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: enqueue(repository, binding).id, range(2)))
    assert outcomes[0] == outcomes[1]
    assert repository.list_runs("w", binding.device_id, "alert").total == 1


def test_claim_is_fifo_once_only_and_single_lane(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    first, second = enqueue(repository, binding), enqueue(repository, binding, "req-2")
    with pytest.raises(InvalidState):
        repository.claim_run("w", second.id, "nonce-2", actor_id="worker")
    assert repository.claim_run("w", first.id, "nonce-1", actor_id="worker").status == "claimed"
    with pytest.raises(InvalidState):
        repository.claim_run("w", first.id, "nonce-1", actor_id="worker")
    with pytest.raises(InvalidState):
        repository.claim_run("w", second.id, "nonce-2", actor_id="worker")
    other = enqueue(repository, lane(repository, code="0002"), "other-request")
    assert repository.claim_run("w", other.id, "other-nonce", actor_id="worker").status == "claimed"


def test_input_capture_is_immutable_and_keeps_original_request_idempotence(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    run = enqueue(repository, binding)
    assert run.input_snapshot is None
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    captured = repository.capture_input("w", run.id, "nonce", {"temperature": "85.00001"}, actor_id="reader")
    assert captured.input_snapshot == {"temperature": "85.00001"}
    assert enqueue(repository, binding).id == run.id
    assert repository.capture_input("w", run.id, "nonce", captured.input_snapshot, actor_id="reader") == captured
    with pytest.raises(Conflict):
        repository.capture_input("w", run.id, "nonce", {"temperature": "12"}, actor_id="reader")


def test_uncertain_execution_is_not_automatically_redispatched(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    run = enqueue(repository, binding)
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    repository.mark_uncertain("w", run.id, "nonce", "upstream_timeout", actor_id="worker")
    assert repository.get_binding("w", binding.device_id, "alert").active_run_id == run.id
    with pytest.raises(InvalidState):
        repository.claim_run("w", run.id, "new-nonce", actor_id="worker")
    assert repository.get_run("w", run.id).status == "uncertain"


def test_reconciliation_updates_reason_and_recovers_same_execution_without_releasing_lane(
    repository: SqlAlchemyRepository,
) -> None:
    binding = lane(repository)
    run = enqueue(repository, binding)
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    repository.mark_dispatched("w", run.id, "nonce", "native-1", actor_id="worker")
    repository.mark_uncertain("w", run.id, "nonce", "identity_mismatch", actor_id="worker")
    updated = repository.mark_uncertain("w", run.id, "nonce", "reconcile_uncertain", actor_id="worker")
    assert updated.reason_code == "reconcile_uncertain"
    restored = repository.mark_dispatched("w", run.id, "nonce", "native-1", actor_id="worker")
    assert restored.status == "dispatched" and restored.reason_code is None
    assert repository.get_binding("w", binding.device_id, "alert").active_run_id == run.id
    assert [event.action for event in repository.list_events("w", run.id)] == [
        "run.queued",
        "run.claimed",
        "run.dispatched",
        "run.uncertain",
        "run.uncertain",
        "run.dispatched",
    ]
    failed = repository.fail_run("w", run.id, "nonce", "workflow_failed", actor_id="worker")
    assert repository.mark_dispatched("w", run.id, "nonce", "native-1", actor_id="worker") == failed
    assert repository.get_binding("w", binding.device_id, "alert").active_run_id is None


def test_business_completion_requires_capture_and_never_overwrites_a_report(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    run = enqueue(repository, binding)
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    result = BusinessResult(scenario="alert", conclusion="normal", complete=True)
    digest = canonical_hash(result.model_dump(mode="json"))
    with pytest.raises(InvalidState):
        repository.complete_run("w", run.id, "nonce", result, digest, actor_id="callback")
    repository.capture_input("w", run.id, "nonce", {"temperature": "20"}, actor_id="reader")
    complete = repository.complete_run("w", run.id, "nonce", result, digest, actor_id="callback")
    assert complete.status == "succeeded"
    assert repository.complete_run("w", run.id, "nonce", result, digest, actor_id="callback") == complete
    changed = BusinessResult(scenario="alert", conclusion="issues", complete=True)
    with pytest.raises(Conflict):
        repository.complete_run(
            "w", run.id, "nonce", changed, canonical_hash(changed.model_dump(mode="json")), actor_id="callback"
        )
    assert repository.get_binding("w", binding.device_id, "alert").active_run_id is None
    assert repository.get_run("w", run.id).result == result


def test_failure_releases_lane_without_fabricating_a_business_verdict(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    run = enqueue(repository, binding)
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    failed = repository.fail_run("w", run.id, "nonce", "workflow_failed", actor_id="worker")
    assert failed.status == "failed" and failed.result is None
    assert repository.fail_run("w", run.id, "nonce", "workflow_failed", actor_id="worker") == failed
    with pytest.raises(Conflict):
        repository.fail_run("w", run.id, "nonce", "another_reason", actor_id="worker")
    second = enqueue(repository, binding, "req-2")
    assert repository.claim_run("w", second.id, "nonce-2", actor_id="worker").status == "claimed"


def test_cross_tenant_or_wrong_nonce_cannot_change_runs_or_read_events(repository: SqlAlchemyRepository) -> None:
    run = enqueue(repository, lane(repository))
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    with pytest.raises(NotFound):
        repository.get_run("other", run.id)
    with pytest.raises(NotFound):
        repository.list_events("other", run.id)
    with pytest.raises(Conflict):
        repository.capture_input("w", run.id, "wrong", {}, actor_id="reader")
    assert repository.get_run("w", run.id).input_snapshot is None


def test_audit_events_are_append_only_and_duplicate_callbacks_add_no_events(repository: SqlAlchemyRepository) -> None:
    run = enqueue(repository, lane(repository))
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    repository.mark_dispatched("w", run.id, "nonce", "dify-run", actor_id="worker")
    repository.fail_run("w", run.id, "nonce", "workflow_failed", actor_id="worker")
    before = repository.list_events("w", run.id)
    repository.fail_run("w", run.id, "nonce", "workflow_failed", actor_id="worker")
    assert repository.list_events("w", run.id) == before
    assert [item.action for item in before] == ["run.queued", "run.claimed", "run.dispatched", "run.failed"]
    assert repository.list_events("w", run.id, after_sequence=before[0].sequence) == before[1:]


def test_concurrent_workers_never_both_claim_a_lane(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    runs = (enqueue(repository, binding), enqueue(repository, binding, "req-2"))

    def claim(run: Run) -> str:
        try:
            return repository.claim_run("w", run.id, f"nonce-{run.id}", actor_id="worker").id
        except EnterpriseError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, runs))
    assert results == [runs[0].id, "blocked"]


def test_concurrent_same_result_callbacks_return_one_immutable_report(repository: SqlAlchemyRepository) -> None:
    run = enqueue(repository, lane(repository), snapshot={"temperature": "20"})
    repository.claim_run("w", run.id, "nonce", actor_id="worker")
    result = BusinessResult(scenario="alert", conclusion="normal", complete=True)
    digest = canonical_hash(result.model_dump(mode="json"))

    def finish(_: int) -> Run:
        return repository.complete_run("w", run.id, "nonce", result, digest, actor_id="callback")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(finish, range(2)))
    assert results[0] == results[1]
    assert sum(event.action == "run.succeeded" for event in repository.list_events("w", run.id)) == 1


def test_recompute_ancestry_keeps_snapshot_and_rejects_changed_evidence(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    first = enqueue(repository, binding, snapshot={"temperature": "85"})
    spec = RunSpec.from_binding(binding).model_copy(update={"recompute_of": first.id})
    snapshot = first.input_snapshot
    digest = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": snapshot})
    second = repository.enqueue_run("w", "recompute", digest, spec, snapshot, actor_id="actor")
    assert second.spec.recompute_of == first.id
    assert second.input_snapshot == first.input_snapshot
    changed: JsonObject = {"temperature": "20"}
    changed_digest = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": changed})
    with pytest.raises(Conflict):
        repository.enqueue_run("w", "changed-recompute", changed_digest, spec, changed, actor_id="actor")


def test_concurrent_delete_and_enqueue_never_leave_work_for_a_deleted_device(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)

    def action(delete: bool) -> None:
        try:
            if delete:
                repository.delete_device("w", binding.device_id, expected_revision=1, actor_id="actor")
            else:
                enqueue(repository, binding)
        except (NotFound, InvalidState):
            return

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(action, (True, False)))
    try:
        repository.get_device("w", binding.device_id)
    except NotFound:
        assert repository.list_runs("w", binding.device_id, "alert").total == 0
    else:
        assert repository.list_runs("w", binding.device_id, "alert").total == 1


@pytest.mark.skipif(
    os.environ.get("ENTERPRISE_CI_MIGRATED_PUBLIC") != "1", reason="Requires explicitly migrated disposable CI database"
)
def test_reviewed_migration_supports_production_public_schema_crud() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from enterprise_platform.persistence.migrate import validate_target
    from enterprise_platform.persistence.repository import SqlAlchemyRepository

    configured_url = os.environ.get("ENTERPRISE_TEST_DATABASE_URL")
    assert configured_url, "Dedicated ENTERPRISE_TEST_DATABASE_URL is required"
    url = validate_target(configured_url, "enterprise_test")
    engine = create_engine(url, hide_parameters=True).execution_options(schema_translate_map={None: "public"})
    workspace = f"migration-smoke-{uuid4().hex}"
    try:
        repository = SqlAlchemyRepository(sessionmaker(engine, expire_on_commit=False))
        created = repository.create_device(
            workspace, DeviceCreate(device_code="0001", name="Migration smoke"), actor_id="ci-migration-smoke"
        )
        assert repository.get_device(workspace, created.id) == created
        updated = repository.update_device(
            workspace,
            created.id,
            DeviceUpdate(device_code="0001", name="Migration verified"),
            expected_revision=created.revision,
            actor_id="ci-migration-smoke",
        )
        assert repository.list_devices(workspace).items == (updated,)
        repository.delete_device(
            workspace, created.id, expected_revision=updated.revision, actor_id="ci-migration-smoke"
        )
        assert repository.list_devices(workspace).total == 0
        with pytest.raises(NotFound):
            repository.get_device(workspace, created.id)
    finally:
        engine.dispose()
