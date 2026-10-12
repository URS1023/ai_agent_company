"""显式 SQLite 回执窄迁移；导入不连库、不使用默认配置、不执行种子初始化。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect, select

from app.schema import (RECEIPT_INDEX, SCHEMA_VERSION, SchemaMigration, _contract_checksum,
                        _register_models, validate_receipt_schema)
from app.models import Base


def migrate(engine):
    """停旧 Worker 后显式调用；单事务加列和索引，错误结构拒绝修补与登记。"""
    if (engine.dialect.name != "sqlite" or not engine.url.database or engine.url.database == ":memory:"
            or not Path(engine.url.database).is_absolute() or engine.url.database.startswith(("//", "\\\\"))):
        raise ValueError("本迁移只支持显式的本机 SQLite 文件")
    _register_models()
    with engine.connect() as connection:
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            inspector = inspect(connection)
            if not {"run_events", "schema_migrations"}.issubset(inspector.get_table_names()):
                raise RuntimeError("缺少既有事件或迁移台账，不能使用窄迁移")
            existing_tables = set(inspector.get_table_names())
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    raise RuntimeError("缺少既有表，不能登记回执迁移：" + table.name)
                actual = {item["name"] for item in inspector.get_columns(table.name)}
                expected = set(table.columns.keys()) - ({"receipt_key"} if table.name == "run_events" else set())
                if not expected.issubset(actual):
                    raise RuntimeError("既有结构不完整，不能登记回执迁移：" + table.name)
            columns = {item["name"] for item in inspector.get_columns("run_events")}
            if "receipt_key" not in columns:
                connection.exec_driver_sql("ALTER TABLE run_events ADD COLUMN receipt_key VARCHAR(64) NULL")
            indexes = inspect(connection).get_indexes("run_events")
            if not any(item["name"] == RECEIPT_INDEX for item in indexes):
                connection.exec_driver_sql(f"CREATE UNIQUE INDEX {RECEIPT_INDEX} ON run_events (receipt_key)")
            validate_receipt_schema(connection)
            checksum = _contract_checksum()
            table = SchemaMigration.__table__
            existing = connection.execute(select(table).where(table.c.version == SCHEMA_VERSION)).mappings().first()
            if existing and existing["checksum"] != checksum:
                raise RuntimeError("当前迁移版本摘要不一致")
            if not existing:
                connection.execute(table.insert().values(version=SCHEMA_VERSION, checksum=checksum, applied_at=datetime.utcnow()))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def main():
    """必须传已存在的绝对文件路径，不读取 DATABASE_URL 或自动创建空文件。"""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_file")
    path = Path(parser.parse_args().sqlite_file)
    if not path.is_absolute() or not path.is_file():
        parser.error("需要已存在的本机 SQLite 文件绝对路径")
    engine = create_engine("sqlite:///" + path.as_posix(), connect_args={"timeout": 5})
    try:
        migrate(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
