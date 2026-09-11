import importlib
from unittest.mock import MagicMock, Mock

import pytest
from test_schedule_migration import inspector

from enterprise_platform.application.errors import Conflict, InvalidInput


@pytest.fixture
def migration():
    return importlib.import_module("enterprise_platform.persistence.migrate_office")


def test_office_sql_adds_only_four_scoped_tables(migration):
    sql = migration.read_office_sql()
    assert sql == migration.render_office_sql()
    assert sql.count("CREATE TABLE") == 4
    assert "PRIMARY KEY (workspace_id, file_id, actor_id, request_id)" in sql
    assert "FOREIGN KEY(workspace_id, file_id, result_revision)" in sql
    assert "REFERENCES public.enterprise_office_revisions (workspace_id, file_id, revision)" in sql
    assert "CREATE TABLE public.enterprise_chat" not in sql
    assert "DROP " not in sql and "DELETE " not in sql
    assert all(line == line.rstrip() for line in sql.splitlines())


def test_print_only_never_connects(migration, monkeypatch, capsys):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == migration.read_office_sql()
    engine.assert_not_called()


def test_missing_branch_prerequisite_and_existing_office_are_rejected(migration):
    fake = inspector(migration)
    migration.require_office_prerequisites(fake)
    assert len(migration.prerequisite_metadata().tables) == 17
    fake.get_table_names.return_value.remove("enterprise_chat_branches")
    with pytest.raises(Conflict):
        migration.require_office_prerequisites(fake)
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_office_files")
    with pytest.raises(Conflict):
        migration.require_office_prerequisites(fake)


def test_native_target_is_rejected_without_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_office("postgresql+psycopg://u:p@host/dify", "dify")
    engine.assert_not_called()


def test_apply_checks_actual_database_and_disposes(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector(migration)))
    migration.apply_office("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    sql = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert tuple(sql[3:]) == migration.office_statements()
    assert "pg_advisory_xact_lock" in str(connection.execute.call_args.args[0])
    engine.dispose.assert_called_once()


def test_changed_previous_artifact_is_rejected_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    monkeypatch.setattr(migration.previous, "render_chat_branches_sql", lambda: "changed")
    with pytest.raises(InvalidInput, match="migration_artifact_mismatch"):
        migration.apply_office("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    engine.assert_not_called()


def test_ddl_render_does_not_mutate_office_models(migration):
    from enterprise_platform.persistence.office_models import OfficeBase

    before = {name: set(table.constraints) for name, table in OfficeBase.metadata.tables.items()}
    assert migration.office_statements() == migration.office_statements()
    assert before == {name: set(table.constraints) for name, table in OfficeBase.metadata.tables.items()}
    assert all(table.schema is None for table in OfficeBase.metadata.tables.values())


@pytest.mark.parametrize("column", ["document_hash", "command_hash"])
def test_office_hash_length_accepts_only_lossless_postgresql_reflection(column: str) -> None:
    from enterprise_platform.persistence.migrate_sources import normalized_check

    expected = normalized_check(f"length({column}) = 64")
    assert normalized_check(f"length(({column})::text) = 64") == expected
    for changed in [
        f"length(({column})::varchar(63)) = 64",
        f"length(({column})::char(64)) = 64",
        f"length(({column})::text) = 63",
        "length((other_column)::text) = 64",
    ]:
        assert normalized_check(changed) != expected
    assert normalized_check("length(other_column::text) = 64") != normalized_check("length(other_column) = 64")
    assert normalized_check(f"lower({column}::text) = 'x'") != normalized_check(f"lower({column}) = 'x'")
