"""临时文件 SQLite 窄迁移：旧行保留、重复运行与实际唯一约束。"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.business_worker.receipt_migration import migrate
from app.models import Base, RunEvent
from app.schema import SCHEMA_VERSION, SchemaMigration, _register_models, initialize_schema


def old_file(tmp_path):
    """从当前完整元数据去掉新列，建立可关闭重开的旧版临时文件。"""
    _register_models()
    path = tmp_path / "old.sqlite"
    engine = create_engine("sqlite:///" + path.as_posix(), connect_args={"timeout": 5})
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_run_events_receipt_key")
        connection.exec_driver_sql("ALTER TABLE run_events DROP COLUMN receipt_key")
        connection.exec_driver_sql("INSERT INTO run_events (workspace_id,run_id,event_type,payload,created_at,sequence) VALUES ('w','r','chat.stream','{\"event\":\"legacy\"}','2026-09-01 01:02:03',7)")
        connection.execute(SchemaMigration.__table__.insert().values(version="20260917_32", checksum="legacy-fixed", applied_at=datetime(2026, 9, 1)))
    return engine, path


def test_file_migration_is_repeatable_and_preserves_legacy_rows(tmp_path, monkeypatch):
    engine, path = old_file(tmp_path)
    with engine.connect() as connection:
        old_event = tuple(connection.exec_driver_sql("SELECT * FROM run_events").one())
        old_version = tuple(connection.exec_driver_sql("SELECT * FROM schema_migrations").one())
    migrate(engine)
    with engine.connect() as connection:
        first_versions = [tuple(row) for row in connection.exec_driver_sql("SELECT * FROM schema_migrations ORDER BY version")]
    migrate(engine)
    engine.dispose()
    engine = create_engine("sqlite:///" + path.as_posix(), connect_args={"timeout": 5})
    try:
        with engine.connect() as connection:
            event = tuple(connection.exec_driver_sql("SELECT * FROM run_events").one())
            versions = [tuple(row) for row in connection.exec_driver_sql("SELECT * FROM schema_migrations ORDER BY version")]
            assert event[:-1] == old_event and event[-1] is None
            assert versions == first_versions and versions[0] == old_version
        from app.config import settings
        monkeypatch.setattr(settings, "AUTO_MIGRATE", False)
        initialize_schema(engine)
        values = dict(workspace_id="w", run_id="r", event_type="chat.stream", payload={}, created_at=datetime.utcnow(), sequence=8)
        with engine.begin() as connection:
            connection.execute(RunEvent.__table__.insert(), [{**values, "receipt_key": None}, {**values, "receipt_key": None}, {**values, "receipt_key": "a" * 64}])
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(RunEvent.__table__.insert().values(**values, receipt_key="a" * 64))
    finally:
        engine.dispose()


@pytest.mark.parametrize("index_sql", [
    "CREATE INDEX ix_run_events_receipt_key ON run_events (receipt_key)",
    "CREATE UNIQUE INDEX ix_run_events_receipt_key ON run_events (receipt_key,run_id)",
    "CREATE UNIQUE INDEX ix_run_events_receipt_key ON run_events (receipt_key) WHERE run_id='r'",
])
def test_wrong_receipt_index_cannot_register_new_version(tmp_path, index_sql):
    engine, _ = old_file(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE run_events ADD COLUMN receipt_key VARCHAR(64) NULL")
            connection.exec_driver_sql(index_sql)
        with pytest.raises(RuntimeError, match="唯一索引"):
            migrate(engine)
        with engine.connect() as connection:
            assert connection.execute(select(SchemaMigration.version).where(SchemaMigration.version == SCHEMA_VERSION)).first() is None
    finally:
        engine.dispose()


def test_partial_migration_adds_only_missing_index(tmp_path):
    engine, _ = old_file(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE run_events ADD COLUMN receipt_key VARCHAR(64) NULL")
        migrate(engine)
        assert next(item for item in inspect(engine).get_indexes("run_events") if item["name"] == "ix_run_events_receipt_key")["unique"]
    finally:
        engine.dispose()


def test_normal_startup_does_not_migrate_existing_file(tmp_path, monkeypatch):
    engine, _ = old_file(tmp_path)
    try:
        from app.config import settings
        monkeypatch.setattr(settings, "AUTO_MIGRATE", False)
        with pytest.raises(RuntimeError, match="结构不匹配"):
            initialize_schema(engine)
        assert "receipt_key" not in {item["name"] for item in inspect(engine).get_columns("run_events")}
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM schema_migrations").scalar() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("definition", ["VARCHAR(32) NULL", "INTEGER NULL", "VARCHAR(64) NOT NULL DEFAULT ''"])
def test_wrong_receipt_column_is_rejected_without_version(tmp_path, definition):
    engine, _ = old_file(tmp_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE run_events ADD COLUMN receipt_key " + definition)
        with pytest.raises(RuntimeError, match="回执列结构"):
            migrate(engine)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM schema_migrations").scalar() == 1
    finally:
        engine.dispose()
