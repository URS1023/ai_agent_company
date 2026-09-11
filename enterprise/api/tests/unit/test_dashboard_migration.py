import importlib
from unittest.mock import MagicMock, Mock

import pytest
from test_schedule_migration import inspector

from enterprise_platform.application.errors import Conflict, InvalidInput


@pytest.fixture
def migration():
    return importlib.import_module("enterprise_platform.persistence.migrate_dashboards")


def test_artifact_matches_scoped_table_model(migration):
    sql = migration.read_dashboards_sql()
    assert sql == migration.render_dashboards_sql()
    assert "PRIMARY KEY (workspace_id, dashboard_id)" in sql
    assert "public.enterprise_dashboards" in sql
    assert "revision > 0" in sql
    assert "DROP " not in sql


def test_print_sql_does_not_connect(migration, monkeypatch, capsys):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == migration.read_dashboards_sql()
    engine.assert_not_called()


def test_native_database_target_is_rejected_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_dashboards("postgresql+psycopg://u:p@host/dify", "dify")
    engine.assert_not_called()


def test_guard_requires_existing_schedule_schema(migration):
    assert "enterprise_schedules" in migration.prerequisite_metadata().tables
    fake = inspector(migration)
    fake.get_table_names.return_value.remove("enterprise_schedules")
    with pytest.raises(Conflict):
        migration.require_dashboard_prerequisites(fake)


def test_guarded_apply_checks_schema_and_disposes_engine(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    fake = inspector(migration)
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=fake))
    migration.apply_dashboards("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    sql = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert any("CREATE TABLE public.enterprise_dashboards" in statement for statement in sql)
    engine.dispose.assert_called_once()


def test_reapplication_is_rejected(migration):
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_dashboards")
    with pytest.raises(Conflict):
        migration.require_dashboard_prerequisites(fake)
