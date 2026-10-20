"""临时SQLite验证07到08数据保留、唯一键及陌生结构拒绝。"""

import importlib.util
from datetime import datetime
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from agent_platform.modules.flows.models import WorkflowRun

ROOT = Path(__file__).resolve().parents[3]


def migration(number):
    path = next((ROOT / 'services/platform/migrations/versions').glob(f'20261021_{number}_*.py'))
    spec = importlib.util.spec_from_file_location('flow_migration_' + number, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply(connection, number):
    with Operations.context(MigrationContext.configure(connection)):
        migration(number).upgrade()


def insert_old(connection, *, id='old', **extra):
    values = {'id': id, 'workspace_id': 'ws', 'workflow_id': 'flow', 'status': 'succeeded',
        'input': '原输入', 'steps': [{'type': 'text', 'output': '原输出'}], 'output': '原输出',
        'created_by': 'user', 'started_at': datetime(2026, 10, 21), **extra}
    table = migration('08').definition()
    connection.execute(sa.insert(table).values(**values))


def test_upgrade_preserves_legacy_rows_and_matches_new_model(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'upgrade.sqlite3').as_posix())
    try:
        with engine.begin() as connection:
            apply(connection, '07')
            insert_old(connection)
            apply(connection, '08')
            row = connection.execute(sa.select(migration('08').definition())).mappings().one()
            assert row['output'] == '原输出' and all(row[name] is None for name in migration('08').NEW_COLUMNS)
            expected = sa.MetaData()
            WorkflowRun.__table__.to_metadata(expected)
            context = MigrationContext.configure(connection, opts={'compare_type': True,
                'include_object': lambda obj, name, type_, reflected, compare_to: type_ != 'table' or name == 'flow_runs'})
            assert compare_metadata(context, expected) == []
            apply(connection, '08')
    finally:
        engine.dispose()


def test_already_created_development_structure_is_verified_without_replacement(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'dev.sqlite3').as_posix())
    try:
        migration('08').definition().metadata.create_all(engine)
        with engine.begin() as connection:
            insert_old(connection)
            apply(connection, '08')
            assert connection.execute(sa.text('SELECT count(*) FROM flow_runs')).scalar_one() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize('drift', ['extra', 'wrong_type', 'missing_index'])
def test_schema_drift_refused_before_new_column_is_added(tmp_path, drift):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'drift.sqlite3').as_posix())
    try:
        with engine.begin() as connection:
            apply(connection, '07')
            if drift == 'extra':
                connection.execute(sa.text('ALTER TABLE flow_runs ADD COLUMN unexpected TEXT'))
            elif drift == 'missing_index':
                connection.execute(sa.text('DROP INDEX ix_flow_runs_workspace_id'))
            else:
                connection.execute(sa.text('ALTER TABLE flow_runs ADD COLUMN request_hash INTEGER'))
            with pytest.raises(RuntimeError, match='flow_reservation_'):
                apply(connection, '08')
            assert 'idempotency_key' not in {c['name'] for c in sa.inspect(connection).get_columns('flow_runs')}
    finally:
        engine.dispose()


def test_unique_key_is_scoped_and_null_legacy_keys_remain_compatible(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'unique.sqlite3').as_posix())
    try:
        with engine.begin() as connection:
            apply(connection, '07')
            apply(connection, '08')
            insert_old(connection, id='a')
            insert_old(connection, id='b')
            insert_old(connection, id='c', idempotency_key='intent')
            insert_old(connection, id='d', idempotency_key='intent', created_by='other-user')
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    insert_old(connection, id='e', idempotency_key='intent')
    finally:
        engine.dispose()


def test_duplicate_development_request_keys_are_rejected_before_constraint_creation(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'duplicates.sqlite3').as_posix())
    try:
        table = migration('08').definition()
        table.constraints.remove(next(item for item in table.constraints if isinstance(item, sa.UniqueConstraint)))
        table.metadata.create_all(engine)
        with engine.begin() as connection:
            insert_old(connection, id='a', idempotency_key='same')
            insert_old(connection, id='b', idempotency_key='same')
            with pytest.raises(RuntimeError, match='duplicate_request_key'):
                apply(connection, '08')
            assert sa.inspect(connection).get_unique_constraints('flow_runs') == []
    finally:
        engine.dispose()


def test_downgrade_preserves_old_result_fields(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'downgrade.sqlite3').as_posix())
    try:
        with engine.begin() as connection:
            apply(connection, '07')
            insert_old(connection)
            apply(connection, '08')
            with Operations.context(MigrationContext.configure(connection)):
                migration('08').downgrade()
            names = {column['name'] for column in sa.inspect(connection).get_columns('flow_runs')}
            assert not names.intersection(migration('08').NEW_COLUMNS)
            assert connection.execute(sa.text('SELECT output FROM flow_runs WHERE id=:id'), {'id': 'old'}).scalar_one() == '原输出'
    finally:
        engine.dispose()
