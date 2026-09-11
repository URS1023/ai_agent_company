import importlib
from unittest.mock import MagicMock, Mock

import pytest
from test_schedule_migration import inspector

from enterprise_platform.application.errors import Conflict, InvalidInput


@pytest.fixture
def migration():
    return importlib.import_module("enterprise_platform.persistence.migrate_sql_drafts")


def test_artifact_matches_sql_draft_model_and_contains_no_destructive_sql(migration):
    sql = migration.read_sql_drafts_sql()
    assert sql == migration.render_sql_drafts_sql()
    assert "PRIMARY KEY (workspace_id, draft_id)" in sql
    assert "public.enterprise_dashboard_sql_drafts" in sql
    assert "ix_sql_draft_dashboard" in sql
    assert "DROP " not in sql


def test_print_does_not_connect(migration, monkeypatch, capsys):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == migration.read_sql_drafts_sql()
    engine.assert_not_called()


def test_native_database_is_rejected_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_sql_drafts("postgresql+psycopg://u:p@host/dify", "dify")
    engine.assert_not_called()


def test_guard_requires_existing_dashboards_and_rejects_reapplication(migration):
    fake = inspector(migration)
    fake.get_table_names.return_value.remove("enterprise_dashboards")
    with pytest.raises(Conflict):
        migration.require_sql_draft_prerequisites(fake)
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_dashboard_sql_drafts")
    with pytest.raises(Conflict):
        migration.require_sql_draft_prerequisites(fake)


def test_apply_checks_schema_and_disposes_engine(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector(migration)))
    migration.apply_sql_drafts("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    assert any(
        "CREATE TABLE public.enterprise_dashboard_sql_drafts" in call.args[0]
        for call in connection.exec_driver_sql.call_args_list
    )
    engine.dispose.assert_called_once()
