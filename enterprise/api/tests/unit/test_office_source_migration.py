import importlib
from unittest.mock import Mock

import pytest
from test_schedule_migration import inspector

from enterprise_platform.application.errors import Conflict, InvalidInput


@pytest.fixture
def migration():
    return importlib.import_module("enterprise_platform.persistence.migrate_office_sources")


def test_only_three_new_tables_and_scoped_foreign_keys(migration):
    sql = migration.read_office_sources_sql()
    assert sql == migration.render_office_sources_sql()
    assert sql.count("CREATE TABLE") == 3
    assert "PRIMARY KEY (workspace_id, snapshot_id)" in sql
    assert "REFERENCES public.enterprise_office_sources (workspace_id, source_id)" in sql
    assert "DROP " not in sql and "DELETE " not in sql
    assert "CREATE TABLE public.enterprise_office_files" not in sql


def test_existing_four_office_tables_are_required(migration):
    fake = inspector(migration)
    assert len(migration.prerequisite_metadata().tables) == 21
    migration.require_office_sources_prerequisites(fake)
    fake.get_table_names.return_value.remove("enterprise_office_files")
    with pytest.raises(Conflict):
        migration.require_office_sources_prerequisites(fake)


def test_existing_target_tables_rejected(migration):
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_office_sources")
    with pytest.raises(Conflict):
        migration.require_office_sources_prerequisites(fake)


def test_print_only_never_connects(migration, monkeypatch, capsys):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == migration.read_office_sources_sql()
    engine.assert_not_called()


def test_native_target_rejected_before_connection(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_office_sources("postgresql+psycopg://u:p@host/dify", "dify")
    engine.assert_not_called()


def test_previous_office_artifact_drift_rejected(migration, monkeypatch):
    engine = Mock()
    monkeypatch.setattr(migration, "create_engine", engine)
    monkeypatch.setattr(migration.previous, "render_office_sql", lambda: "changed")
    with pytest.raises(InvalidInput, match="migration_artifact_mismatch"):
        migration.apply_office_sources("postgresql+psycopg://u:p@host/enterprise_test", "enterprise_test")
    engine.assert_not_called()


def test_snapshot_hash_reflection_only_ignores_lossless_text_cast():
    from enterprise_platform.persistence.migrate_sources import normalized_check

    expected = normalized_check("length(payload_hash) = 64")
    assert normalized_check("length((payload_hash)::text) = 64") == expected
    for changed in [
        "length(payload_hash::varchar(63)) = 64",
        "length(payload_hash::char(64)) = 64",
        "length(payload_hash::text) = 63",
        "length(other_column::text) = 64",
    ]:
        assert normalized_check(changed) != expected
