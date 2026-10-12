"""Provision missing variantorm tables required by imported Lynx reports.

The current Lynx report catalog contains two energy widgets backed by
``ai_dashboard_energy_demo``.  The authoritative table definition and rows are
already present in ``参照/variantorm.sql``, but some local variantorm instances
were created before that section was added.  This command copies only that
missing table.  It never drops, truncates, or overwrites an existing table.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm.attributes import flag_modified


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
REFERENCE_DUMP = ROOT / "参照" / "variantorm.sql"
TABLE_NAME = "ai_dashboard_energy_demo"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db import new_session  # noqa: E402
from app.models import DataSource  # noqa: E402
from app.modules.analytics.connections import readonly_connection, sql_engine  # noqa: E402
from app.modules.analytics.models import SourceMetadata  # noqa: E402


def arguments() -> argparse.Namespace:
    """解析参数：--workspace-id（必填）、--datasource-name（默认 variantorm）、
    --apply 才真正建表/补数据，缺省为只读 dry-run。"""
    parser = argparse.ArgumentParser(
        description="Add only the missing Lynx report support table to one variantorm datasource",
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--datasource-name", default="variantorm")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create and seed the missing table. Without this flag the command is read-only.",
    )
    return parser.parse_args()


def _source(db, workspace_id: str, datasource_name: str) -> DataSource:
    """按名称取该工作区唯一启用中的 MySQL 数据源；数量不为 1 或不是 MySQL 时直接 SystemExit。"""
    rows = db.scalars(select(DataSource).where(
        DataSource.workspace_id == workspace_id,
        DataSource.name == datasource_name,
        DataSource.status == "active",
    )).all()
    if len(rows) != 1:
        raise SystemExit(
            f"expected exactly one active {datasource_name!r} datasource in workspace; found {len(rows)}"
        )
    if rows[0].provider != "mysql":
        raise SystemExit("the Lynx support data command currently requires a MySQL datasource")
    return rows[0]


def _reference_statements() -> tuple[str, list[str]]:
    """从 参照/variantorm.sql 截取目标表的建表与插入语句。

    返回 (CREATE 语句, INSERT 列表)，并把 CREATE TABLE 改写为 IF NOT EXISTS、INSERT INTO 改写为
    INSERT IGNORE，保证重复执行不会破坏已有表或产生重复行；参考文件缺失该表段落时 SystemExit。
    """
    text = REFERENCE_DUMP.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"-- Table structure for {TABLE_NAME}\s+.*?"
        rf"(?=-- Table structure for |\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise SystemExit(f"reference dump does not contain {TABLE_NAME}")
    block = match.group(0)
    create_match = re.search(r"CREATE TABLE .*?;", block, re.DOTALL)
    create = create_match.group(0).rstrip(";").strip() if create_match else ""
    inserts = [
        item.rstrip(";").strip()
        for item in re.findall(
            rf"INSERT INTO `{TABLE_NAME}` .*?;",
            block,
            re.DOTALL,
        )
    ]
    if not create or not inserts:
        raise SystemExit(f"reference block for {TABLE_NAME} is incomplete")
    create = create.replace("CREATE TABLE `", "CREATE TABLE IF NOT EXISTS `", 1)
    inserts = [item.replace("INSERT INTO `", "INSERT IGNORE INTO `", 1) for item in inserts]
    return create, inserts


def _exists(db, source: DataSource) -> tuple[bool, int]:
    """用只读连接检查目标表是否存在及其行数，返回 (存在, 行数)。"""
    with readonly_connection(db, source) as connection:
        if not inspect(connection).has_table(TABLE_NAME):
            return False, 0
        count = int(connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM `{TABLE_NAME}`"
        ).scalar_one())
        return True, count


def main() -> int:
    """为目标工作区的 variantorm 数据源补齐参考报表依赖的支撑表（默认只读预览）。

    参数：--workspace-id/--datasource-name/--apply（见 arguments）。
    副作用（仅 --apply）：建表 + INSERT IGNORE 参考数据行、必要时把表加入 allowedTables，
    并删除该数据源的元数据缓存以强制下次重新同步；从不 DROP/TRUNCATE/覆盖已有表。
    结束时复查行数，未达到参考行数则抛 RuntimeError（不留下"看起来成功"的中间态）。
    前置条件：平台数据库可用（new_session），且目标数据源可连接。
    """
    args = arguments()
    create, inserts = _reference_statements()
    with new_session() as db:
        source = _source(db, args.workspace_id, args.datasource_name)
        exists, count = _exists(db, source)
        print(
            f"workspace={args.workspace_id} datasource={source.id} "
            f"table={TABLE_NAME} exists={str(exists).lower()} rows={count}"
        )
        if exists and count >= len(inserts):
            print("support rows already complete; existing table left unchanged")
            return 0
        if not args.apply:
            action = "create the table and seed" if not exists else "insert only missing seed rows up to"
            print(f"dry-run only; --apply will {action} {len(inserts)} reference rows")
            return 0

        with sql_engine(db, source, write=True) as engine:
            with engine.begin() as connection:
                connection.exec_driver_sql(create)
                for statement in inserts:
                    connection.exec_driver_sql(statement)

        allowed = list((source.connection_config or {}).get("allowedTables") or [])
        if allowed and TABLE_NAME not in allowed:
            source.connection_config = {
                **(source.connection_config or {}),
                "allowedTables": [*allowed, TABLE_NAME],
            }
            flag_modified(source, "connection_config")
        # Force one real metadata/semantic refresh on the next datasource use.
        db.execute(delete(SourceMetadata).where(
            SourceMetadata.workspace_id == args.workspace_id,
            SourceMetadata.datasource_id == source.id,
        ))
        db.commit()

        exists, count = _exists(db, source)
        if not exists or count < len(inserts):
            raise RuntimeError(
                f"support data invariant failed: exists={exists}, rows={count}, expected-at-least={len(inserts)}"
            )
        print(f"created table={TABLE_NAME} rows={count}; metadata refresh scheduled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
