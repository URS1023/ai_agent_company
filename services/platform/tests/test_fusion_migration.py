"""新临时SQLite验证六表迁移及开发结构兼容，不连接任何在用库。"""

import importlib.util
from pathlib import Path

from alembic import op
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]


def migration():
    spec = importlib.util.spec_from_file_location('fusion_migration', ROOT / 'services/platform/migrations/versions/20261021_07_融合对话知识与流程.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        migration().upgrade()


def test_new_migration_exactly_matches_frozen_seven_six_tables(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'fresh.sqlite3').as_posix())
    try:
        with engine.begin() as connection:
            upgrade(connection)
            tables = migration().definitions()
            assert set(sa.inspect(connection).get_table_names()) == {table.name for table in tables}
            expected = sa.MetaData()
            for table in tables:
                table.to_metadata(expected)
            assert compare_metadata(MigrationContext.configure(connection, opts={'compare_type': True}), expected) == []
            upgrade(connection)
    finally:
        engine.dispose()


def test_existing_development_tables_only_gain_missing_evidence_and_keep_data(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'development.sqlite3').as_posix())
    try:
        tables = migration().definitions()
        tables[0].metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(sa.text('ALTER TABLE flow_runs DROP COLUMN knowledge_sources'))
            connection.execute(sa.text("INSERT INTO kb_spaces(id,workspace_id,name,description,created_by,created_at,updated_at) VALUES ('space','ws','原资料','说明','u','2026-10-21','2026-10-21')"))
            upgrade(connection)
            assert 'knowledge_sources' in {item['name'] for item in sa.inspect(connection).get_columns('flow_runs')}
            assert connection.execute(sa.text('SELECT name FROM kb_spaces WHERE id=:id'), {'id': 'space'}).scalar_one() == '原资料'
    finally:
        engine.dispose()


@pytest.mark.parametrize('drift', ['extra_column', 'column_type', 'missing_index'])
def test_unknown_development_schema_is_rejected_before_adding_other_columns(tmp_path, drift):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'unknown.sqlite3').as_posix())
    try:
        tables = migration().definitions()
        tables[0].metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(sa.text('ALTER TABLE flow_runs DROP COLUMN knowledge_sources'))
            if drift == 'extra_column':
                connection.execute(sa.text('ALTER TABLE kb_spaces ADD COLUMN other_data TEXT'))
            elif drift == 'missing_index':
                connection.execute(sa.text('DROP INDEX ix_kb_spaces_workspace_id'))
            else:
                connection.execute(sa.text('DROP TABLE kb_spaces'))
                connection.execute(sa.text('CREATE TABLE kb_spaces(id VARCHAR(64) NOT NULL PRIMARY KEY, workspace_id VARCHAR(64) NOT NULL, name INTEGER NOT NULL, description TEXT NOT NULL, created_by VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)'))
            with pytest.raises(RuntimeError, match='fusion_migration_'):
                upgrade(connection)
            assert 'knowledge_sources' not in {item['name'] for item in sa.inspect(connection).get_columns('flow_runs')}
    finally:
        engine.dispose()
