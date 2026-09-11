from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from enterprise_platform.application.errors import Conflict, InvalidInput
from enterprise_platform.persistence import migrate_workflow_setups as migration
from enterprise_platform.persistence.migrate_sources import require_initial_schema


def inspector():
    metadata = migration.prerequisite_metadata()
    result = Mock()
    result.get_schema_names.return_value = ["public"]
    result.get_table_names.return_value = list(metadata.tables)
    result.get_view_names.return_value = []
    result.get_materialized_view_names.return_value = []
    result.get_columns.side_effect = lambda name, **kw: [
        dict(
            name=c.name,
            type=c.type,
            nullable=c.nullable,
            default=f"nextval('{name}_sequence_seq'::regclass)" if c.name == "sequence" else None,
        )
        for c in metadata.tables[name].c
    ]
    result.get_pk_constraint.side_effect = lambda name, **kw: dict(
        constrained_columns=list(metadata.tables[name].primary_key.columns.keys())
    )
    result.get_foreign_keys.side_effect = lambda name, **kw: [
        dict(
            name=f.name,
            constrained_columns=f.column_keys,
            referred_schema="public",
            referred_table=f.referred_table.name,
            referred_columns=[e.column.name for e in f.elements],
        )
        for f in metadata.tables[name].foreign_key_constraints
    ]
    result.get_unique_constraints.side_effect = lambda name, **kw: [
        dict(name=c.name, column_names=list(c.columns.keys()))
        for c in metadata.tables[name].constraints
        if isinstance(c, UniqueConstraint)
    ]
    result.get_indexes.side_effect = lambda name, **kw: [
        dict(name=i.name, column_names=list(i.columns.keys()), unique=bool(i.unique))
        for i in metadata.tables[name].indexes
    ]
    result.get_check_constraints.side_effect = lambda name, **kw: [
        dict(name=c.name, sqltext=str(c.sqltext))
        for c in metadata.tables[name].constraints
        if isinstance(c, CheckConstraint)
    ]
    return result


def test_requires_exactly_verified_six_tables_and_old_wrapper_still_requires_four():
    assert len(migration.prerequisite_metadata().tables) == 6
    migration.require_setup_prerequisites(inspector())
    with pytest.raises(Conflict):
        require_initial_schema(inspector())


@pytest.mark.parametrize("change", ["missing_source", "existing_setup", "extra", "source_constraint"])
def test_prerequisite_drift_rejected(change):
    fake = inspector()
    if change == "missing_source":
        fake.get_table_names.return_value.remove("enterprise_source_versions")
    elif change == "source_constraint":
        original = fake.get_check_constraints.side_effect
        fake.get_check_constraints.side_effect = lambda name, **kw: (
            [] if name == "enterprise_source_versions" else original(name, **kw)
        )
    else:
        fake.get_table_names.return_value.append("enterprise_workflow_setups" if change == "existing_setup" else "apps")
    with pytest.raises(Conflict):
        migration.require_setup_prerequisites(fake)


def test_artifact_stable_explicit_and_print_has_no_engine(monkeypatch, capsys):
    sql = migration.read_workflow_setups_sql()
    assert sql == migration.render_workflow_setups_sql() == migration.render_workflow_setups_sql()
    assert "CREATE TABLE public.enterprise_workflow_setups" in sql
    assert "CREATE INDEX ix_workflow_setup_device_scenario ON public.enterprise_workflow_setups" in sql
    assert "BEGIN;\nSET LOCAL search_path TO public;" in sql and sql.endswith("COMMIT;\n")
    assert "IF NOT EXISTS" not in sql and "DROP " not in sql
    monkeypatch.setattr(migration, "create_engine", Mock(side_effect=AssertionError("no DB")))
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == sql


def test_wrong_target_before_engine(monkeypatch):
    engine = Mock(side_effect=AssertionError("no DB"))
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_workflow_setups("postgresql+psycopg://user:secret@host/dify", "enterprise_test")
    engine.assert_not_called()


def test_actual_database_mismatch_before_ddl(monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "other"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(InvalidInput):
        migration.apply_workflow_setups("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    connection.exec_driver_sql.assert_not_called()
    engine.dispose.assert_called_once()


def test_apply_is_locked_and_transactional_after_schema_guard(monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector()))
    migration.apply_workflow_setups("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    assert "1162761296" in str(connection.execute.call_args.args[0])
    sql = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert sql[:3] == [
        "SET LOCAL lock_timeout = '10s'",
        "SET LOCAL statement_timeout = '30s'",
        "SET LOCAL search_path TO public",
    ]
    assert sql[3:] == list(migration.workflow_setup_statements())


def test_mismatched_bundle_rejected(monkeypatch):
    package = Mock()
    package.joinpath.return_value.read_text.return_value = "changed artifact"
    monkeypatch.setattr(migration, "files", Mock(return_value=package))
    with pytest.raises(InvalidInput, match="migration_artifact_mismatch"):
        migration.read_workflow_setups_sql()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--apply"],
        ["--apply", "--url-env", "DATABASE_URL", "--expected-database", "enterprise_test"],
        ["--private-token"],
    ],
)
def test_cli_requires_explicit_enterprise_target_and_hides_arguments(arguments, capsys):
    assert migration.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.err == "invalid_input\n" and not captured.out
