import importlib
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from test_schedule_migration import inspector

from enterprise_platform.application.errors import Conflict, InvalidInput, PersistenceError


@pytest.fixture
def migration():
    return importlib.import_module("enterprise_platform.persistence.migrate_sql_trials")


def test_artifact_matches_model_and_only_adds_trial_storage(migration):
    sql = migration.read_sql_trials_sql()
    assert sql == migration.render_sql_trials_sql()
    assert "PRIMARY KEY (workspace_id, trial_id)" in sql
    assert "public.enterprise_dashboard_sql_trials" in sql
    assert "ix_sql_trial_draft" in sql
    assert "DROP " not in sql
    assert "DELETE " not in sql
    assert "CREATE TABLE public.enterprise_dashboard_sql_drafts" not in sql


def test_print_does_not_connect(migration, monkeypatch, capsys):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == migration.read_sql_trials_sql()
    engine.assert_not_called()


def test_native_database_is_rejected_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_sql_trials("postgresql+psycopg://u:p@host/dify", "dify")
    engine.assert_not_called()


def test_missing_drafts_or_reapplication_is_rejected(migration):
    fake = inspector(migration)
    fake.get_table_names.return_value.remove("enterprise_dashboard_sql_drafts")
    with pytest.raises(Conflict):
        migration.require_sql_trial_prerequisites(fake)
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_dashboard_sql_trials")
    with pytest.raises(Conflict):
        migration.require_sql_trial_prerequisites(fake)


def test_apply_locks_checks_schema_and_disposes_engine(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector(migration)))
    migration.apply_sql_trials("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert "SET LOCAL lock_timeout = '10s'" in statements
    assert any("CREATE TABLE public.enterprise_dashboard_sql_trials" in statement for statement in statements)
    assert "pg_advisory_xact_lock" in str(connection.execute.call_args.args[0])
    engine.dispose.assert_called_once()


def test_actual_database_mismatch_stops_before_ddl(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "dify"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(InvalidInput):
        migration.apply_sql_trials("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    connection.exec_driver_sql.assert_not_called()
    engine.dispose.assert_called_once()


def test_driver_failure_is_opaque_and_disposes_engine(migration, monkeypatch):
    engine = MagicMock()
    engine.begin.return_value.__enter__.side_effect = SQLAlchemyError("private connection details")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(PersistenceError) as error:
        migration.apply_sql_trials("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    assert "private connection" not in str(error.value)
    engine.dispose.assert_called_once()
