"""仅核验本轮临时SQLite的版本升级、开发库接管和目标边界。"""
from pathlib import Path
import sys

import pytest
from sqlalchemy import text

from agent_platform.core.database import Database
from agent_platform.core.settings import PROJECT_ROOT, Settings

sys.path.insert(0, str(PROJECT_ROOT))
from tools.manage_database import manage


def test_fresh_migration_is_repeatable_and_production_can_open(tmp_path):
    settings = Settings(data_dir=tmp_path / 'owned-platform')
    assert manage('upgrade', settings)['ready']
    assert manage('upgrade', settings)['revision'] == '20261021_10'
    assert manage('check', settings)['ready']
    production = Database(Settings(data_dir=settings.data_dir, environment='production'))
    try:
        production.initialize()
    finally:
        production.dispose()


def test_development_schema_requires_explicit_adoption(tmp_path):
    settings = Settings(data_dir=tmp_path / 'owned-platform')
    database = Database(settings)
    try:
        database.initialize()
        with pytest.raises(RuntimeError, match='未登记版本'):
            manage('upgrade', settings)
        assert manage('adopt', settings)['ready']
    finally:
        database.dispose()


def test_drifted_development_schema_is_not_silently_stamped(tmp_path):
    settings = Settings(data_dir=tmp_path / 'owned-platform')
    database = Database(settings)
    try:
        database.initialize()
        with database.engine.begin() as connection:
            connection.execute(text('CREATE TABLE unexpected_business_data (id INTEGER PRIMARY KEY)'))
        with pytest.raises(RuntimeError, match='结构与当前模型不同'):
            manage('adopt', settings)
    finally:
        database.dispose()


def test_migration_tool_refuses_nonlocal_and_outside_targets(tmp_path):
    with pytest.raises(RuntimeError, match='不连接外部'):
        manage('check', Settings(data_dir=tmp_path, database_url='postgresql://unapproved.invalid/db'))
    with pytest.raises(RuntimeError, match='必须位于'):
        manage('check', Settings(data_dir=tmp_path / 'owned', database_url='sqlite:///' + str(tmp_path / 'old.sqlite3')))


def test_manufacturing_upgrade_preserves_existing_device_records(tmp_path):
    """从已发布版本升级只加关联表，原设备内容保留，重复升级没有额外副作用。"""
    from alembic import command
    from sqlalchemy import inspect
    from tools.manage_database import config

    settings = Settings(data_dir=tmp_path / 'owned-upgrade')
    database = Database(settings)
    try:
        with database.engine.begin() as connection:
            command.upgrade(config(connection), '20261020_01')
            connection.execute(text(
                "INSERT INTO manufacturing_devices "
                "(id, workspace_id, revision, created_at, updated_at, archived, name, code, description, device_type, location, status) "
                "VALUES ('preserved-device', 'local-test', 1, '2026-10-20', '2026-10-20', 0, '原设备', 'P-01', '', 'pump', '', 'active')"
            ))
        assert manage('upgrade', settings)['ready']
        with database.engine.connect() as connection:
            assert connection.execute(text(
                "SELECT name FROM manufacturing_devices WHERE id='preserved-device'"
            )).scalar_one() == '原设备'
            tables = set(inspect(connection).get_table_names())
            assert {'manufacturing_reinspections', 'manufacturing_quality_cases',
                    'manufacturing_work_orders', 'manufacturing_quality_categories',
                    'manufacturing_energy_analyses',
                    'operation_support_resources', 'operation_support_revisions',
                    'service_client_certificates', 'service_client_assertions',
                    'service_certificate_token_bindings',
                    'data_row_policies', 'data_query_access_snapshots'} <= tables
        assert manage('upgrade', settings)['revision'] == '20261021_10'
    finally:
        database.dispose()
