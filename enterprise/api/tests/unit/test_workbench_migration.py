import importlib
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from test_schedule_migration import inspector

from enterprise_platform.application.errors import Conflict, InvalidInput, PersistenceError


@pytest.fixture
def migration():
    return importlib.import_module("enterprise_platform.persistence.migrate_chat_messages")


def test_sql_artifact_matches_model_and_adds_only_message_ledger(migration):
    sql = migration.read_chat_messages_sql()
    assert sql == migration.render_chat_messages_sql()
    assert sql.count("CREATE TABLE") == 1
    assert "public.enterprise_chat_send_intents" in sql
    assert "PRIMARY KEY (workspace_id, actor_id, installed_app_id, branch_id, client_message_id)" in sql
    assert "ck_chat_send_revision" in sql and "ck_chat_send_status" in sql
    assert "DROP " not in sql and "DELETE " not in sql


def test_print_sql_has_no_connection(migration, monkeypatch, capsys):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == migration.read_chat_messages_sql()
    engine.assert_not_called()


def test_native_database_is_rejected_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_chat_messages("postgresql+psycopg://u:p@host/dify", "dify")
    engine.assert_not_called()


def test_missing_previous_table_and_repeat_apply_are_rejected(migration):
    fake = inspector(migration)
    migration.require_chat_prerequisites(fake)
    fake.get_table_names.return_value.remove("enterprise_dashboard_sql_trials")
    with pytest.raises(Conflict):
        migration.require_chat_prerequisites(fake)
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_chat_send_intents")
    with pytest.raises(Conflict):
        migration.require_chat_prerequisites(fake)


def test_apply_checks_target_locks_and_disposes(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector(migration)))
    migration.apply_chat_messages("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements[:3] == [
        "SET LOCAL lock_timeout = '10s'",
        "SET LOCAL statement_timeout = '30s'",
        "SET LOCAL search_path TO public",
    ]
    assert tuple(statements[3:]) == migration.chat_messages_statements()
    assert "pg_advisory_xact_lock" in str(connection.execute.call_args.args[0])
    engine.dispose.assert_called_once()


def test_actual_database_mismatch_stops_before_ddl(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "dify"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(InvalidInput):
        migration.apply_chat_messages("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    connection.exec_driver_sql.assert_not_called()
    engine.dispose.assert_called_once()


def test_artifact_mismatch_stops_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    monkeypatch.setattr(migration, "render_chat_messages_sql", lambda: "wrong artifact")
    with pytest.raises(InvalidInput, match="migration_artifact_mismatch"):
        migration.apply_chat_messages("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    engine.assert_not_called()


def test_driver_error_is_opaque(migration, monkeypatch):
    engine = MagicMock()
    engine.begin.return_value.__enter__.side_effect = SQLAlchemyError("private details")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(PersistenceError, match="^enterprise_chat_migration_failed$"):
        migration.apply_chat_messages("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    engine.dispose.assert_called_once()


def test_cli_rejects_nonenterprise_url_variable(migration, capsys):
    assert migration.main(["--apply", "--url-env", "DATABASE_URL", "--expected-database", "enterprise_test"]) == 1
    assert capsys.readouterr().err == "invalid_input\n"
