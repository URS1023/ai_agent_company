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
    assert manage('upgrade', settings)['revision'] == '20261020_01'
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
