"""Schema-only checks: compile both dialects without opening database connections."""


def test_enterprise_metadata_is_independent_and_contains_only_business_tables() -> None:
    from enterprise_platform.persistence.models import Base

    assert set(Base.metadata.tables) == {
        "enterprise_devices",
        "enterprise_bindings",
        "enterprise_runs",
        "enterprise_audit_events",
    }
    assert all("workspace_id" in table.c for table in Base.metadata.tables.values())


def test_lane_request_and_dispatch_nonce_have_database_unique_constraints() -> None:
    from sqlalchemy import UniqueConstraint

    from enterprise_platform.persistence.models import BindingRow, RunRow

    bindings = {
        tuple(column.name for column in constraint.columns)
        for constraint in BindingRow.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    runs = {
        tuple(column.name for column in constraint.columns)
        for constraint in RunRow.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("workspace_id", "device_id", "scenario") in bindings
    assert ("workspace_id", "request_key") in runs
    assert ("workspace_id", "dispatch_nonce") in runs
    assert ("workspace_id", "run_id") in runs


def test_relationships_include_workspace_in_foreign_keys() -> None:
    from sqlalchemy import ForeignKeyConstraint

    from enterprise_platform.persistence.models import Base

    foreign_keys = [
        constraint
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert len(foreign_keys) >= 3
    assert all("workspace_id" in constraint.column_keys for constraint in foreign_keys)


def test_postgres_and_sqlite_ddl_compile_without_native_dify_dependencies() -> None:
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    from enterprise_platform.persistence.models import Base

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        for table in Base.metadata.sorted_tables:
            statement = str(CreateTable(table).compile(dialect=dialect))
            assert "CREATE TABLE enterprise_" in statement
            for index in table.indexes:
                assert "CREATE" in str(CreateIndex(index).compile(dialect=dialect))


def test_runs_have_separate_immutable_evidence_and_no_credential_columns() -> None:
    from enterprise_platform.persistence.models import Base, RunRow

    assert {"spec_json", "input_json", "payload_hash", "result_json", "result_digest"} <= set(RunRow.__table__.c.keys())
    forbidden = {"api_key", "access_token", "password", "secret_value"}
    assert not any(forbidden & set(table.c.keys()) for table in Base.metadata.tables.values())


def test_fresh_runs_capture_evidence_later_without_fabricating_an_empty_snapshot() -> None:
    from enterprise_platform.persistence.models import RunRow

    assert RunRow.__table__.c.input_json.nullable is True
    assert "input_digest" in RunRow.__table__.c
    assert "actor_id" in RunRow.__table__.c


def test_device_business_code_is_unique_within_its_workspace() -> None:
    from sqlalchemy import UniqueConstraint

    from enterprise_platform.persistence.models import DeviceRow

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in DeviceRow.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("workspace_id", "device_code") in unique_columns


def test_device_mapping_preserves_zero_code_and_restores_sqlite_utc() -> None:
    from datetime import UTC, datetime

    from enterprise_platform.persistence.mapping import as_device
    from enterprise_platform.persistence.models import DeviceRow

    row = DeviceRow(
        workspace_id="w",
        device_id="d",
        device_code="0001",
        revision=1,
        device_json='{"device_code":"0001","name":"Pump"}',
        deleted=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    result = as_device(row)
    assert result.device_code == "0001"
    assert result.created_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_repository_construction_has_no_database_connection_side_effect() -> None:
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from enterprise_platform.persistence.repository import SqlAlchemyRepository

    engine = create_engine("sqlite+pysqlite:///:memory:")
    connections: list[object] = []

    @event.listens_for(engine, "connect")
    def record_connect(connection: object, record: object) -> None:
        connections.append(connection)

    SqlAlchemyRepository(sessionmaker(engine))
    assert connections == []
    engine.dispose()


def test_invalid_page_bounds_fail_before_a_database_session_is_opened() -> None:
    import pytest

    from enterprise_platform.application.errors import InvalidInput
    from enterprise_platform.persistence.mapping import validate_page

    for offset, limit in ((-1, 1), (0, 0), (0, 101), (True, 1), (0, True)):
        with pytest.raises(InvalidInput):
            validate_page(offset, limit)


def test_run_write_lock_statement_is_tenant_scoped_without_executing_sql() -> None:
    from typing import cast
    from unittest.mock import Mock

    import pytest
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import Session

    from enterprise_platform.application.errors import NotFound
    from enterprise_platform.persistence.mapping import run_row

    stub = Mock(spec=Session)
    stub.scalar.return_value = None
    with pytest.raises(NotFound):
        run_row(cast(Session, stub), "workspace-a", "run-b", lock=True)
    compiled = stub.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "FOR UPDATE" in str(compiled)
    assert "enterprise_runs.workspace_id =" in str(compiled)
    assert "enterprise_runs.run_id =" in str(compiled)
    assert set(compiled.params.values()) == {"workspace-a", "run-b"}


def test_device_write_lock_guards_soft_deletion_without_executing_sql() -> None:
    from typing import cast
    from unittest.mock import Mock

    from sqlalchemy import CursorResult
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import Session

    from enterprise_platform.persistence.mapping import lock_device

    stub = Mock(spec=Session)
    result = Mock(spec=CursorResult)
    result.rowcount = 1
    stub.execute.return_value = result
    lock_device(cast(Session, stub), "workspace-a", "device-b")
    compiled = stub.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "enterprise_devices.workspace_id =" in str(compiled)
    assert "enterprise_devices.device_id =" in str(compiled)
    assert "enterprise_devices.deleted IS false" in str(compiled)


def test_cas_revision_is_a_positive_integer_not_boolean_coercion() -> None:
    import pytest

    from enterprise_platform.application.errors import InvalidInput
    from enterprise_platform.persistence.mapping import validate_revision

    validate_revision(1)
    for revision in (0, -1, True):
        with pytest.raises(InvalidInput):
            validate_revision(revision)


def test_recompute_checks_frozen_source_parameters_and_exact_snapshot_without_io() -> None:
    from uuid import UUID

    import pytest

    from enterprise_platform.application.contracts import RunSpec
    from enterprise_platform.application.errors import Conflict, InvalidState
    from enterprise_platform.persistence.mapping import serialized
    from enterprise_platform.persistence.models import RunRow
    from enterprise_platform.persistence.runs import verify_recompute

    original = RunSpec(
        app_id="app",
        workflow_id=UUID("00000000-0000-4000-8000-000000000001"),
        specification_revision="v1",
        secret_ref="secrets/app",
        source_id="source",
        source_revision="source-v1",
        read_id="read",
        read_revision="read-v1",
        binding_id="binding",
        binding_revision=1,
        device_id="device",
        scenario="alert",
        parameters={"sample": "0001"},
    )
    previous = RunRow(spec_json=serialized(original), input_json='{"value":"85"}')
    recompute = original.model_copy(update={"recompute_of": "previous", "specification_revision": "v2"})
    verify_recompute(recompute, '{"value":"85"}', previous)
    for field in ("device_id", "source_id", "source_revision", "read_id", "read_revision"):
        with pytest.raises(Conflict):
            verify_recompute(recompute.model_copy(update={field: "different"}), '{"value":"85"}', previous)
    with pytest.raises(Conflict):
        verify_recompute(recompute.model_copy(update={"parameters": {"sample": "0002"}}), '{"value":"85"}', previous)
    with pytest.raises(Conflict):
        verify_recompute(recompute, '{"value":"20"}', previous)
    with pytest.raises(InvalidState):
        verify_recompute(recompute, None, previous)


def test_initial_migration_artifact_matches_metadata_and_qualifies_public_schema() -> None:
    from enterprise_platform.persistence.migrate import read_initial_sql, render_initial_sql

    sql = read_initial_sql()
    assert sql == render_initial_sql()
    assert sql.count("CREATE TABLE public.enterprise_") == 4
    assert "SET LOCAL search_path TO public" in sql
    assert "DROP TABLE" not in sql


def test_migration_target_rejects_native_system_ambiguous_and_other_driver_databases() -> None:
    import pytest

    from enterprise_platform.application.errors import InvalidInput
    from enterprise_platform.persistence.migrate import validate_target

    url = "postgresql+psycopg://user:fixture-password@localhost/enterprise_business"
    assert validate_target(url, "enterprise_business").database == "enterprise_business"
    for database in ("dify", "postgres", "template1", "", "not_enterprise", "enterprise_business?options=x"):
        with pytest.raises(InvalidInput):
            validate_target(f"postgresql+psycopg://localhost/{database}", database)
    with pytest.raises(InvalidInput):
        validate_target(url, "enterprise_other")
    with pytest.raises(InvalidInput):
        validate_target("sqlite:///enterprise_business", "enterprise_business")


def test_initial_migration_rejects_any_existing_user_tables() -> None:
    import pytest

    from enterprise_platform.application.errors import Conflict
    from enterprise_platform.persistence.migrate import require_empty_database

    require_empty_database(set())
    for tables in ({"public.apps"}, {"other.tenants"}, {"public.enterprise_devices"}, {"custom.owned_elsewhere"}):
        with pytest.raises(Conflict):
            require_empty_database(tables)


def test_migration_and_application_settings_accept_the_same_dedicated_targets() -> None:
    import pytest
    from pydantic import SecretStr, ValidationError

    from enterprise_platform.application.errors import InvalidInput
    from enterprise_platform.bootstrap import Settings
    from enterprise_platform.persistence.migrate import validate_target

    for database, suffix, user, valid in (
        ("enterprise_business", "", "user@", True),
        ("enterprise_business", "?sslmode=require", "user@", True),
        ("enterprise_" + "a" * 48, "", "user@", True),
        ("enterprise_" + "a" * 49, "", "user@", False),
        ("enterprise_Business", "", "user@", False),
        ("enterprise_business", "?options=-csearch_path%3Dother", "user@", False),
        ("enterprise_business", "?dbname=dify", "user@", False),
        ("enterprise_business", "", "", False),
        ("dify", "", "user@", False),
    ):
        url = f"postgresql+psycopg://{user}localhost/{database}{suffix}"

        def settings(database_url: str) -> Settings:
            return Settings(
                database_url=SecretStr(database_url),
                dify_console_url="http://native.invalid/console/api",
                dify_service_url="http://native.invalid/v1",
                allowed_origins=("http://web.invalid",),
            )

        if valid:
            assert settings(url).database_url.get_secret_value() == url
            assert validate_target(url, database).database == database
        else:
            with pytest.raises(ValidationError):
                settings(url)
            with pytest.raises(InvalidInput):
                validate_target(url, database)


def test_invalid_native_run_identity_is_rejected_before_opening_a_session() -> None:
    import pytest
    from sqlalchemy.orm import sessionmaker

    from enterprise_platform.application.errors import InvalidInput
    from enterprise_platform.persistence.repository import SqlAlchemyRepository

    repository = SqlAlchemyRepository(sessionmaker())
    for native_id in ("../run", " run", "run?query", "r" * 129, "run\n"):
        with pytest.raises(InvalidInput, match="invalid_dify_run_id"):
            repository.mark_dispatched("workspace", "run", "nonce", native_id, actor_id="worker")


def test_uncertain_reason_changes_are_updates_not_retries() -> None:
    import pytest

    from enterprise_platform.application.errors import InvalidState
    from enterprise_platform.persistence.runs import uncertain_changes

    assert uncertain_changes("uncertain", "identity_mismatch", "reconcile_uncertain") == {
        "state": "uncertain",
        "reason_code": "reconcile_uncertain",
    }
    assert uncertain_changes("uncertain", "same", "same") is None
    for state in ("queued", "succeeded", "failed", "cancelled"):
        with pytest.raises(InvalidState):
            uncertain_changes(state, None, "reconcile_uncertain")


def test_reconciled_identity_restores_in_flight_status_without_reviving_terminal_runs() -> None:
    import pytest

    from enterprise_platform.application.errors import Conflict, InvalidState
    from enterprise_platform.persistence.runs import dispatched_changes

    assert dispatched_changes("uncertain", "native-1", "native-1") == {
        "state": "dispatched",
        "dify_run_id": "native-1",
        "reason_code": None,
    }
    for state in ("dispatched", "succeeded", "failed", "cancelled"):
        assert dispatched_changes(state, "native-1", "native-1") is None
    with pytest.raises(Conflict):
        dispatched_changes("uncertain", "native-1", "native-2")
    with pytest.raises(InvalidState):
        dispatched_changes("uncertain", None, "native-1")


def test_active_runs_freeze_device_read_scope_but_allow_presentation_edits() -> None:
    import pytest

    from enterprise_platform.application.contracts import DeviceCreate, DeviceUpdate
    from enterprise_platform.application.errors import InvalidState
    from enterprise_platform.persistence.repository import validate_device_scope_change

    current = DeviceCreate(device_code="0001", department="a", name="Pump")
    display_edit = DeviceUpdate(device_code="0001", department="a", name="Updated", description="Clarified")
    validate_device_scope_change(current, display_edit, has_active_runs=True)
    for field, value in (("device_code", "0002"), ("department", "b")):
        changed = display_edit.model_copy(update={field: value})
        with pytest.raises(InvalidState, match="device_scope_has_outstanding_runs"):
            validate_device_scope_change(current, changed, has_active_runs=True)
        validate_device_scope_change(current, changed, has_active_runs=False)
