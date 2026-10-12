"""显式 MySQL 平台回执窄迁移；离线计划与导入不读取部署配置。"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import getpass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
import time

from sqlalchemy import Column, MetaData, String, Table, create_engine, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateColumn, CreateIndex

from app.models import Base, RunEvent
from app.schema import (RECEIPT_INDEX, SCHEMA_VERSION, _contract_checksum,
                        _register_models, validate_receipt_schema)


FROM_VERSION = "20260917_32"
TO_VERSION = "20261004_33"
# 来源为 4f47d51 的完整真实模型注册；目标只增加可空 receipt_key，均为 154 张表。
FROM_CHECKSUM = "7700101ef2693dd853f84a0c724c916d45e9d6d8e83fe4aa5c071f2e1827b8e4"
TO_CHECKSUM = "097b9b907ad59a4c729f45670227fc4582806c2c17a6787da4ecb4b8061f7f7a"
SOURCE_COMMIT = "4f47d51eb2deb1de7e39dccd21310c9133e1da8e"
LOCK_SECONDS = 5
TOTAL_SECONDS = 120
CATALOG_LIMIT = 10000


class Rejected(RuntimeError):
    """明确的前置条件或结构冲突；不自动修复。"""


class MigrationFailed(RuntimeError):
    """携带脱敏结果，区分已确认步骤和无法确认的数据库结果。"""

    def __init__(self, report):
        self.report = report
        super().__init__("迁移未获完整成功确认；请保留日志并人工核验状态")


@dataclass(frozen=True)
class Target:
    """显式本机目标；服务归属和停写须由维护人员在执行前核实。"""

    host: str
    port: int
    database: str
    user: str
    server_hostname: str
    server_port: int

    def validate(self):
        try:
            local = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise Rejected("首版只允许已核实归属的本机数字回环目标")
        if not all(isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535
                   for value in (self.port, self.server_port)):
            raise Rejected("必须提供有效连接端口与预期服务端口")
        if not self.database or len(self.database) > 64 or "\x00" in self.database:
            raise Rejected("必须提供明确的平台库名")
        if not self.user or not self.server_hostname:
            raise Rejected("必须提供用户名和已核实的服务主机标识")


def expected_columns():
    """注册当前真实模型并核验冻结摘要，不接受未来模型冒用历史迁移。"""
    _register_models()
    if SCHEMA_VERSION != TO_VERSION or _contract_checksum() != TO_CHECKSUM:
        raise Rejected("当前模型不属于本迁移固定的 v33 契约")
    contract = {
        name: [(column.name, str(column.type), column.nullable) for column in table.columns
               if not (name == "run_events" and column.name == "receipt_key")]
        for name, table in sorted(Base.metadata.tables.items())
    }
    checksum = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()
    if checksum != FROM_CHECKSUM:
        raise Rejected("去除回执列后的真实模型不匹配固定 v32 契约")
    return {name: {item[0] for item in columns} for name, columns in contract.items()}


def _validate_server(snapshot, target):
    fingerprint = snapshot["server"]
    if (fingerprint["database"] != target.database or fingerprint["hostname"] != target.server_hostname
            or int(fingerprint["port"]) != target.server_port):
        raise Rejected("实际平台数据库或服务标识与显式目标不符")
    version = str(fingerprint["version"])
    match = re.match(r"^(8)\.(0|4)\.(\d+)", version)
    if "mariadb" in version.lower() or not match or (match[2] == "0" and int(match[3]) < 13):
        raise Rejected("本迁移仅实现 MySQL 8.0.13+ 或 8.4；其他服务族/版本须单独验证")


def plan(snapshot, target):
    """根据已取得的目录快照离线规划；不构造引擎、不连接或生成部署脚本。"""
    target.validate()
    _validate_server(snapshot, target)
    expected = expected_columns()
    actual = snapshot["columns"]
    for table, required in expected.items():
        present = {item["name"] for item in actual.get(table, [])}
        if not required.issubset(present):
            raise Rejected("既有平台缺少必需表或列：" + table)
    for name in ("run_events", "schema_migrations"):
        table = snapshot["tables"].get(name, {})
        if (str(table.get("engine", "")).lower() != "innodb" or table.get("type") != "BASE TABLE"
                or "partitioned" in str(table.get("options", "")).lower()):
            raise Rejected("事件与版本台账必须是未分区的 InnoDB 普通表")
    versions = snapshot["versions"]
    if len({row["version"] for row in versions}) != len(versions):
        raise Rejected("版本台账重复")
    by_version = {row["version"]: row["checksum"] for row in versions}
    if by_version.get(FROM_VERSION) != FROM_CHECKSUM:
        raise Rejected("来源版本或固定 checksum 不匹配")
    if any(name > FROM_VERSION and name != TO_VERSION for name in by_version):
        raise Rejected("存在本迁移不认识的较新版本")
    if TO_VERSION in by_version and by_version[TO_VERSION] != TO_CHECKSUM:
        raise Rejected("目标版本 checksum 冲突")
    columns = [item for item in actual["run_events"] if item["name"] == "receipt_key"]
    if len(columns) > 1:
        raise Rejected("回执列重复")
    if columns:
        column = columns[0]
        if (column["type"] != "varchar" or column["length"] != 64 or column["nullable"] is not True
                or column.get("default") is not None or column.get("extra") or column.get("generated")):
            raise Rejected("已存在回执列不是规范的可空 VARCHAR(64)")
    indexes = [item for item in snapshot["indexes"] if item["name"] == RECEIPT_INDEX]
    if len(indexes) > 1:
        raise Rejected("回执索引重复")
    if indexes:
        index = indexes[0]
        if (not columns or index.get("unique") is not True or index.get("columns") != ["receipt_key"]
                or index.get("prefixes") != [None] or index.get("expressions") != [None]
                or str(index.get("type", "")).upper() != "BTREE" or index.get("visible") is not True):
            raise Rejected("已存在回执索引不是完整普通唯一索引")
    if TO_VERSION in by_version and not (columns and indexes):
        raise Rejected("v33 已登记但回执结构缺失，不能自动修补")
    operations = ([] if columns else ["add_column"]) + ([] if indexes else ["create_index"])
    if TO_VERSION not in by_version:
        operations.append("register_version")
    return {"from_version": FROM_VERSION, "to_version": TO_VERSION, "operations": operations,
            "state": "complete" if not operations else "partial" if columns else "legacy"}


def compile_statements(dialect, database):
    """从真实 RunEvent 列/索引按方言编译；返回内存语句供显式连接执行。"""
    expected_columns()
    table = RunEvent.__table__.to_metadata(MetaData(), schema=database)
    column = str(CreateColumn(table.c.receipt_key).compile(dialect=dialect))
    if not column.rstrip().upper().endswith(" NULL"):
        column += " NULL"
    original = next(item for item in RunEvent.__table__.indexes if item.name == RECEIPT_INDEX)
    index = next(item for item in table.indexes
                 if item.unique == original.unique and list(item.columns.keys()) == list(original.columns.keys()))
    # SQLAlchemy 复制自动列索引时会把 schema 加进名字，发布合同要求保留原索引名。
    index.name = original.name
    return {"add_column": "ALTER TABLE " + dialect.identifier_preparer.format_table(table) + " ADD COLUMN " + column,
            "create_index": str(CreateIndex(index).compile(dialect=dialect))}


def _rows(connection, statement, parameters=None):
    return [dict(item) for item in connection.execute(text(statement), parameters or {}).mappings().all()]


def read_catalog(connection, target):
    """只读平台结构与版本台账；目录取数设固定上限，不读取业务记录。"""
    server = _rows(connection, "SELECT DATABASE() AS `database`, VERSION() AS version, @@hostname AS hostname, @@port AS port")[0]
    _validate_server({"server": server}, target)
    columns = _rows(connection,
        "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name, DATA_TYPE AS type, "
        "CHARACTER_MAXIMUM_LENGTH AS length, IS_NULLABLE AS nullable, COLUMN_DEFAULT AS `default`, "
        "EXTRA AS extra, GENERATION_EXPRESSION AS `generated` FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=:database LIMIT 10001", {"database": target.database})
    if len(columns) > CATALOG_LIMIT:
        raise Rejected("目录列数超过本窄迁移预算")
    grouped = {}
    for item in columns:
        item["nullable"] = item["nullable"] == "YES"
        grouped.setdefault(item.pop("table_name"), []).append(item)
    tables = _rows(connection,
        "SELECT TABLE_NAME AS name, ENGINE AS engine, TABLE_TYPE AS type, CREATE_OPTIONS AS options "
        "FROM information_schema.TABLES WHERE TABLE_SCHEMA=:database "
        "AND TABLE_NAME IN ('run_events','schema_migrations')", {"database": target.database})
    parts = _rows(connection,
        "SELECT INDEX_NAME AS name, NON_UNIQUE AS non_unique, COLUMN_NAME AS column_name, SUB_PART AS prefix, "
        "EXPRESSION AS expression, INDEX_TYPE AS type, IS_VISIBLE AS visible, SEQ_IN_INDEX AS position "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=:database AND TABLE_NAME='run_events' "
        "AND INDEX_NAME=:index_name ORDER BY SEQ_IN_INDEX", {"database": target.database, "index_name": RECEIPT_INDEX})
    indexes = []
    if parts:
        indexes.append({"name": RECEIPT_INDEX, "unique": all(item["non_unique"] == 0 for item in parts),
                        "columns": [item["column_name"] for item in parts], "prefixes": [item["prefix"] for item in parts],
                        "expressions": [item["expression"] for item in parts], "type": parts[0]["type"],
                        "visible": all(item["visible"] == "YES" for item in parts)})
    versions_table = Table("schema_migrations", MetaData(), Column("version", String), Column("checksum", String), schema=target.database)
    versions = [dict(row) for row in connection.execute(select(versions_table).limit(1001)).mappings().all()]
    if len(versions) > 1000:
        raise Rejected("版本记录超过本窄迁移预算")
    return {"server": server, "columns": grouped, "tables": {item.pop("name"): item for item in tables},
            "indexes": indexes, "versions": versions}


def _unknown(error):
    """只有服务器明确拒绝的有限错误可断言失败；传输或提交确认丢失保留未知。"""
    if isinstance(error, DBAPIError):
        code = getattr(error.orig, "args", (None,))[0]
        return error.connection_invalidated or code not in {1044, 1049, 1060, 1061, 1062, 1064, 1142, 1205}
    return not isinstance(error, Rejected)


def migrate(engine, target, record):
    """同连接串行迁移；MySQL DDL 不承诺整体事务，丢确认不重试。"""
    target.validate()
    expected_columns()
    if engine.dialect.name != "mysql" or engine.url.database != target.database:
        raise Rejected("必须传入显式目标的 MySQL 引擎")
    if (engine.url.host != target.host or engine.url.port != target.port or engine.url.username != target.user):
        raise Rejected("引擎连接参数与显式目标不符")
    report = {"status": "started", "database": target.database, "from_version": FROM_VERSION,
              "to_version": TO_VERSION, "confirmed_steps": [], "ddl_attempts": 0,
              "version_insert_attempts": 0, "committed": False, "phase": "connect"}
    record(dict(report))
    started = time.monotonic()
    connection = None
    locked = False
    unsafe_connection = False
    sql_inflight = False
    journal_failed = False

    def emit(kind):
        nonlocal journal_failed
        try:
            record({**report, "confirmed_steps": list(report["confirmed_steps"]), "event": kind})
        except Exception:
            journal_failed = True
            raise

    def check_budget():
        if time.monotonic() - started > TOTAL_SECONDS:
            raise Rejected("迁移总预算已到，停止后续语句；不自动扩大预算")

    try:
        connection = engine.connect()
        check_budget()
        report["phase"] = "target_check"
        server = _rows(connection, "SELECT DATABASE() AS `database`, VERSION() AS version, @@hostname AS hostname, @@port AS port")[0]
        _validate_server({"server": server}, target)
        report["phase"] = "lock"
        lock_name = "test5_schema_" + hashlib.sha256(target.database.encode()).hexdigest()[:32]
        acquired = connection.execute(text("SELECT GET_LOCK(:name, :seconds)"), {"name": lock_name, "seconds": LOCK_SECONDS}).scalar()
        if acquired != 1:
            raise Rejected("未取得平台结构锁，停止且不重试")
        locked = True
        connection.exec_driver_sql("SET SESSION lock_wait_timeout = 5")
        report["phase"] = "preflight"
        snapshot = read_catalog(connection, target)
        planned = plan(snapshot, target)
        emit("preflight_confirmed")
        connection.commit()
        statements = compile_statements(engine.dialect, target.database)
        for step in planned["operations"]:
            if step == "register_version":
                continue
            check_budget()
            report["phase"] = step
            # 先为本次写入意图编号；未结束的意图仍须核验，不能当作已发送或已提交证明。
            report["ddl_attempts"] += 1
            emit("operation_started")
            sql_inflight = True
            connection.exec_driver_sql(statements[step])
            sql_inflight = False
            report["confirmed_steps"].append(step)
            emit("operation_confirmed")
        check_budget()
        report["phase"] = "verify"
        final_snapshot = read_catalog(connection, target)
        remaining = plan(final_snapshot, target)["operations"]
        if any(step != "register_version" for step in remaining):
            raise Rejected("DDL 返回后实物回执结构仍未满足契约")
        # 迁移成功必须也能被真实启动校验器接受，不能另造较宽松的成功标准。
        validate_receipt_schema(connection)
        if "register_version" in planned["operations"]:
            check_budget()
            report["phase"] = "register_version"
            report["version_insert_attempts"] += 1
            emit("operation_started")
            table = Table("schema_migrations", MetaData(), Column("version", String), Column("checksum", String),
                          Column("applied_at"), schema=target.database)
            sql_inflight = True
            connection.execute(table.insert().values(version=TO_VERSION, checksum=TO_CHECKSUM, applied_at=datetime.utcnow()))
            report["phase"] = "commit_version"
            connection.commit()
            sql_inflight = False
            report["committed"] = True
            report["confirmed_steps"].append("register_version")
            emit("operation_confirmed")
        else:
            connection.rollback()
        report["phase"] = "release_lock"
        released = connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name}).scalar()
        if released != 1:
            raise Rejected("结构已核验但锁释放未获确认")
        locked = False
        connection.commit()
        connection.close()
        connection = None
        report["status"] = "applied" if planned["operations"] else "already_applied"
        report["phase"] = "finished"
        emit("finished")
        return report
    except BaseException as error:
        interrupted = not isinstance(error, Exception)
        unsafe_connection = sql_inflight and _unknown(error)
        if not journal_failed and isinstance(error, (DBAPIError, OSError, TimeoutError)) and _unknown(error):
            unsafe_connection = True
        report["status"] = ("evidence_failed" if journal_failed else "unknown" if unsafe_connection
                            else "interrupted" if interrupted else "partial" if report["confirmed_steps"] else "rejected")
        report["error_type"] = type(error).__name__
        if isinstance(error, Rejected):
            report["reason"] = str(error)
        if connection is not None:
            try:
                if unsafe_connection:
                    connection.invalidate()
                else:
                    connection.rollback()
                    if locked:
                        connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
                        connection.commit()
                        locked = False
            except Exception:
                report["cleanup_unconfirmed"] = True
                try:
                    connection.invalidate()
                except Exception:
                    pass
        try:
            emit("failed")
        except Exception:
            report["evidence_write_failed"] = True
        if interrupted and not unsafe_connection:
            # 尚未进入写入确认窗口的进程中断保留原异常，不编造未知写入或返回成功。
            raise
        raise MigrationFailed(report) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                # 已失败的连接关闭错误不能覆盖最初的未知/部分完成证据。
                report["close_unconfirmed"] = True


class Journal:
    """独占新建本机证据文件；每条记录刷盘，写入失败不得当作迁移成功。"""

    def __init__(self, path):
        path = Path(path)
        if not path.is_absolute() or str(path).startswith(("//", "\\\\")):
            raise Rejected("证据文件必须是本机绝对路径")
        self.file = path.open("x", encoding="utf-8", newline="\n")

    def __call__(self, record):
        import os
        self.file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self):
        self.file.close()


def main(argv=None):
    """凭据仅从不回显的终端或显式标准输入读取，禁止默认 URL 与种子入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "apply"])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--expected-server-hostname", required=True)
    parser.add_argument("--expected-server-port", required=True, type=int)
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--local-service-confirmed", action="store_true")
    parser.add_argument("--backup-reference")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args(argv)
    target = Target(args.host, args.port, args.database, args.user, args.expected_server_hostname, args.expected_server_port)
    journal = None
    engine = None
    try:
        target.validate()
        if args.mode == "plan":
            if not args.snapshot:
                raise Rejected("离线计划需要明确的目录快照")
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            print(json.dumps(plan(snapshot, target), ensure_ascii=False))
            return 0
        if not (args.maintenance_confirmed and args.local_service_confirmed and args.backup_reference and args.journal):
            raise Rejected("执行前必须明确确认停旧写入者、本机服务归属、备份引用和独立证据路径")
        expected_columns()
        journal = Journal(args.journal)
        journal({"event": "operator_attestation", "target": asdict(target), "backup_reference": args.backup_reference})
        if args.password_stdin:
            password = sys.stdin.readline(4097).rstrip("\r\n")
            if len(password) > 4096:
                raise Rejected("凭据输入超过上限")
        elif sys.stdin.isatty():
            password = getpass.getpass("数据库口令（不回显）：")
        else:
            raise Rejected("非交互执行必须显式选择 --password-stdin")
        url = URL.create("mysql+pymysql", username=target.user, password=password, host=target.host,
                         port=target.port, database=target.database, query={"charset": "utf8mb4"})
        engine = create_engine(url, poolclass=NullPool, pool_pre_ping=False, echo=False, hide_parameters=True,
                               connect_args={"connect_timeout": 5, "read_timeout": 30, "write_timeout": 5})
        result = migrate(engine, target, journal)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except MigrationFailed as error:
        print(json.dumps(error.report, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as error:
        # 第三方异常可能带连接参数或 SQL；控制台只输出受控原因或异常类别。
        print(json.dumps({"status": "rejected", "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, Rejected) else "迁移入口失败；未确认成功"}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
        if journal is not None:
            journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
