"""将已审查的合成包全量导入本机两库的新表；失败不自动重试。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from threading import Event, Timer
from time import monotonic

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "services/platform") not in sys.path:
    sys.path.insert(0, str(REPO / "services/platform"))

from tools.demo_data.package import (
    Asset, BATCH_SIZE, Column, DATABASES, META_COLUMNS, OWNER, PACKAGE_NAME,
    PackageError, Plan, batches, build_plan, digest_json,
)


class ImportFailure(RuntimeError):
    """只携带可公开的失败原因，不复制驱动异常中的 SQL 或参数。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def failure_diagnostics(error: Exception) -> dict:
    """保留异常种类和位置以便离线定位，绝不收集异常消息、SQL、参数或局部变量。"""
    result = {"exception_type": type(error).__module__ + "." + type(error).__name__}
    if error.args and type(error.args[0]) is int and 0 <= error.args[0] <= 65535:
        result["numeric_code"] = error.args[0]
    frames = []
    trace = error.__traceback__
    while trace is not None:
        frames.append({"file": Path(trace.tb_frame.f_code.co_filename).name,
                       "function": trace.tb_frame.f_code.co_name, "line": trace.tb_lineno})
        trace = trace.tb_next
    result["frames"] = frames[-8:]
    return result


def validate_runtime_dependencies(plan: Plan):
    """在打开数据库前加载时间转换依赖，避免逐值查找及运行期间安装依赖。"""
    if any(asset.format == "parquet" for asset in plan.assets):
        try:
            import pyarrow.lib
            import pytz

            # 实际加载完整固定时区路径，不能只用 find_spec 判断文件是否已出现。
            zone = pyarrow.lib.string_to_tzinfo("+08:00")
            if zone.utcoffset(None) != pytz.FixedOffset(480).utcoffset(None):
                raise ValueError("offset_mismatch")
        except (ImportError, AttributeError, ValueError) as exc:
            raise ImportFailure("runtime_dependency_invalid", "Parquet时间转换依赖不完整，请先安装演示导入依赖") from exc


def quote(identifier: str) -> str:
    if not identifier or len(identifier) > 64 or "\0" in identifier:
        raise ImportFailure("identifier_invalid", "导入表或字段名无效")
    return "`" + identifier.replace("`", "``") + "`"


def qualified(database: str, table: str) -> str:
    if database not in DATABASES or not table.startswith("aic_demo_"):
        raise ImportFailure("target_invalid", "只能导入本次两库的项目专用表")
    return f"{quote(database)}.{quote(table)}"


def _column(name: str, sql_type: str) -> Column:
    return Column(name, sql_type, "import-ledger-v1")


IMPORT_COLUMNS = (
    _column("import_id", "CHAR(64)"), _column("source_path", "LONGTEXT"),
    _column("source_sha256", "CHAR(64)"), _column("table_name", "VARCHAR(64)"),
    _column("expected_rows", "BIGINT"), _column("committed_rows", "BIGINT"),
    _column("status", "VARCHAR(20)"), _column("started_at", "VARCHAR(40)"),
    _column("finished_at", "VARCHAR(40)"),
)
BATCH_COLUMNS = (
    _column("import_id", "CHAR(64)"), _column("batch_no", "BIGINT"),
    _column("first_source_row", "BIGINT"), _column("last_source_row", "BIGINT"),
    _column("row_count", "BIGINT"), _column("content_sha256", "CHAR(64)"),
    _column("committed_at", "VARCHAR(40)"),
)
DATA_META = (
    _column(META_COLUMNS[0], "CHAR(64)"), _column(META_COLUMNS[1], "BIGINT"),
    _column(META_COLUMNS[2], "VARCHAR(20)"),
)


class ControlledCursor:
    """每次驱动调用使用独立短时限；总时限到达会中断本连接。"""

    def __init__(self, cursor, runner):
        self.raw, self.runner = cursor, runner

    def execute(self, sql, parameters=None):
        self.runner.receipt["statements"] += 1
        if self.runner.receipt["statements"] > self.runner.budgets["sql_statement_budget"]:
            raise ImportFailure("statement_budget_exceeded", "导入语句预算已用尽")
        return self.runner.call(lambda: self.raw.execute(sql, parameters) if parameters is not None else self.raw.execute(sql))

    def fetchone(self):
        return self.raw.fetchone()

    def fetchall(self):
        return self.raw.fetchall()

    @property
    def description(self):
        return self.raw.description


class Importer:
    """先核服务与实例，再核新表归属；每批数据和账本在同一事务提交。

    旧成功来源只核对账本和目标行数。旧失败或未知状态一律拒绝续灌。
    驱动异常后不再派发 SQL，保留连接标识供部署者检查是否仍有在途语句。
    """

    def __init__(self, plan: Plan, *, proof_file: Path, username: str, password: str,
                 receipt_file: Path, connection_factory=None, identity_guard=None,
                 connected_guard=None):
        self.plan, self.proof_file = plan, proof_file
        self.username, self.password = username, password
        self.receipt_file = receipt_file
        self.connection_factory = connection_factory
        self.identity_guard, self.connected_guard = identity_guard, connected_guard
        self.budgets = plan.public()
        self.connection = self.cursor = None
        self.deadline = None
        self.started = None
        self.interrupted = Event()
        self.receipt = {"kind": "ai_agent_company-demo-import-receipt-v1", "status": "prepared",
                        "plan_sha256": self.budgets["plan_sha256"], "source_kind": "synthetic",
                        "connections": 0, "statements": 0, "commits": 0,
                        "rows_committed_this_run": 0, "assets": [], "started_at": None,
                        "requires_query_stop_check": False, "phase": "prepared"}

    def save_receipt(self):
        """单写者原子替换本次收据；没有密码、SQL 参数或真实业务数据。"""
        self.receipt_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.receipt_file.with_name(self.receipt_file.name + ".writing")
        temporary.write_text(json.dumps(self.receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.receipt_file)

    def _interrupt(self):
        self.interrupted.set()
        connection = self.connection
        if connection is not None:
            try:
                # 直接关闭本人套接字，不向正在执行的连接并发发送 COM_QUIT。
                owned_socket = getattr(connection, "_sock", None)
                if owned_socket is not None:
                    try:
                        owned_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                force_close = getattr(connection, "_force_close", None)
                if force_close:
                    force_close()
                else:
                    connection.close()
            except Exception:
                pass

    def call(self, function):
        remaining = self.deadline - monotonic()
        if self.interrupted.is_set() or remaining <= 0:
            self._interrupt()
            raise ImportFailure("import_timeout", "导入时限已到，已停止本连接")
        timer = Timer(min(5.0, remaining), self._interrupt)
        timer.daemon = True
        timer.start()
        self.receipt["in_flight"] = True
        try:
            result = function()
            if self.interrupted.is_set():
                raise ImportFailure("statement_timeout", "语句超时，已停止本连接")
            return result
        finally:
            timer.cancel()
            self.receipt["in_flight"] = self.interrupted.is_set()

    def commit(self):
        self.receipt["commits"] += 1
        if self.receipt["commits"] > self.budgets["commit_budget"]:
            raise ImportFailure("commit_budget_exceeded", "导入提交预算已用尽")
        self.call(self.connection.commit)

    def query(self, sql, parameters=()):
        if sql.lstrip().upper().startswith("SELECT") and "information_schema." not in sql:
            self.cursor.execute("EXPLAIN FORMAT=JSON " + sql, parameters)
            row = self.cursor.fetchone()
            try:
                plan = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                self._read_plan(plan)
            except ImportFailure:
                raise
            except Exception as exc:
                raise ImportFailure("read_plan_unknown", "无法确认读取计划，已停止导入") from exc
        self.cursor.execute(sql, parameters)
        names = [column[0] for column in self.cursor.description]
        return [dict(zip(names, row)) for row in self.cursor.fetchall()]

    def _read_plan(self, plan):
        """沿用平台计划估算；只额外接受 MySQL 明确优化掉的常量查询。"""
        from agent_platform.modules.data.connectors import plan_cost

        tables, messages = [], []
        def walk(node):
            if isinstance(node, dict):
                if "table_name" in node:
                    tables.append(node)
                if isinstance(node.get("message"), str):
                    messages.append(node["message"].strip().casefold())
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(plan)
        constant_messages = {"no tables used", "select tables optimized away", "no matching row in const table", "impossible where"}
        if not tables and messages and all(message in constant_messages for message in messages):
            return
        try:
            plan_cost(plan)
        except Exception as exc:
            raise ImportFailure("read_plan_budget_exceeded", "读取预计行数超出预算或无法核实") from exc
        for table in tables:
            access = str(table.get("access_type", "")).casefold()
            if access not in {"system", "const", "eq_ref", "ref", "range", "index"} or (access not in {"system", "const"} and not table.get("key")):
                raise ImportFailure("read_plan_index_required", "读取未使用明确索引，已停止导入")
        if not tables:
            raise ImportFailure("read_plan_unknown", "读取计划没有可核实的表或常量")

    def _guard(self):
        if self.identity_guard is None or self.connected_guard is None:
            from agent_platform.core.local_mysql import verified_local_mysql, verify_connected_mysql
            self.identity_guard = self.identity_guard or verified_local_mysql
            self.connected_guard = self.connected_guard or verify_connected_mysql
        server = None
        for database in DATABASES:
            current = self.identity_guard(str(self.proof_file), "mysql", {
                "host": "127.0.0.1", "port": 3306, "database": database})
            if not current or not current.get("server_uuid") or not current.get("data_directory"):
                raise ImportFailure("local_database_unverified", "本机数据库归属尚未核实")
            if server and current != server:
                raise ImportFailure("local_database_changed", "两库服务归属发生变化")
            server = current
        return server

    def _connect(self):
        server = self._guard()
        self.receipt["phase"] = "connect"
        factory = self.connection_factory
        if factory is None:
            import pymysql
            factory = pymysql.connect
        self.receipt["connections"] += 1
        def connect():
            self.connection = factory(host="127.0.0.1", port=3306,
                user=self.username, password=self.password, charset="utf8mb4", autocommit=False,
                connect_timeout=5, read_timeout=5, write_timeout=5, local_infile=False)
            if self.interrupted.is_set():
                self._interrupt()
            return self.connection
        self.call(connect)
        self.password = ""
        thread_id = getattr(self.connection, "thread_id", None)
        self.receipt["connection_id"] = thread_id() if thread_id else None
        self.cursor = ControlledCursor(self.connection.cursor(), self)
        self.connected_guard(self.cursor, server)
        self.receipt["instance"] = {key: server[key] for key in ("hostname", "port", "server_uuid", "data_directory") if key in server}
        self.cursor.execute("SET SESSION max_execution_time=5000, innodb_lock_wait_timeout=5, lock_wait_timeout=5")
        schemas = self.query("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN (%s,%s)", DATABASES)
        if {row["SCHEMA_NAME"] for row in schemas} != set(DATABASES):
            raise ImportFailure("target_schema_missing", "本次两个目标库不完整")

    def ensure_table(self, database, table, columns, comment, *, primary, extras=()):
        """未带准确项目及结构标记的同名对象只报冲突，绝不覆盖或改表。"""
        target = qualified(database, table)
        records = self.query("SELECT TABLE_COMMENT,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (database, table))
        if records:
            if len(records) != 1 or records[0]["TABLE_TYPE"] != "BASE TABLE" or records[0]["TABLE_COMMENT"] != comment:
                raise ImportFailure("table_ownership_conflict", f"同名表未通过项目归属核验：{database}.{table}")
            actual = self.query("SELECT COLUMN_NAME,COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (database, table))
            normalize = lambda value: str(value).lower().replace(" ", "").replace("bigint(20)", "bigint")
            if [(row["COLUMN_NAME"], normalize(row["COLUMN_TYPE"])) for row in actual] != [(column.name, normalize(column.sql_type)) for column in columns]:
                raise ImportFailure("table_schema_conflict", f"已登记表结构发生变化：{database}.{table}")
            return
        definitions = []
        for column in columns:
            nullable = " NOT NULL" if column.name in primary or column.name == "__aic_source_kind" else " NULL"
            definitions.append(f"{quote(column.name)} {column.sql_type}{nullable}")
        definitions.append("PRIMARY KEY (" + ",".join(quote(name) for name in primary) + ")")
        definitions.extend(extras)
        self.cursor.execute(f"CREATE TABLE {target} ({','.join(definitions)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT=%s", (comment,))

    def _tables(self):
        self.receipt["phase"] = "table_ownership"
        for database in DATABASES:
            self.ensure_table(database, "aic_demo_imports", IMPORT_COLUMNS,
                f"{OWNER};kind=imports;schema=1", primary=("import_id",))
            self.ensure_table(database, "aic_demo_batches", BATCH_COLUMNS,
                f"{OWNER};kind=batches;schema=1", primary=("import_id", "batch_no"))
        tables = {}
        for asset in self.plan.assets:
            key = (asset.database, asset.table)
            if key in tables:
                if tables[key] != asset.schema_hash:
                    raise ImportFailure("source_schema_conflict", "同一目标表的源字段定义不一致")
                continue
            tables[key] = asset.schema_hash
            names = {column.name for column in asset.columns}
            extras = ()
            if {"设备编号", "采集时间"} <= names:
                extras = ("KEY `ix_device_time` (`设备编号`,`采集时间`)",)
            self.ensure_table(asset.database, asset.table, (*DATA_META, *asset.columns),
                asset.comment, primary=META_COLUMNS[:2], extras=extras)
        self.commit()

    def _count(self, asset):
        result = self.query(f"SELECT COUNT(*) AS n FROM {qualified(asset.database, asset.table)} WHERE `__aic_import_id`=%s", (asset.import_id,))
        return int(result[0]["n"])

    def _existing(self, asset):
        ledger = qualified(asset.database, "aic_demo_imports")
        records = self.query(f"SELECT * FROM {ledger} WHERE import_id=%s", (asset.import_id,))
        if not records:
            # 没有账本但目标存在同键数据时，也不能将未知数据算作本次导入。
            if self._count(asset):
                raise ImportFailure("untracked_rows", "发现没有来源账本的数据，已停止导入")
            return False
        row = records[0]
        if (row.get("status") != "succeeded" or row.get("source_sha256") != asset.source_sha256
                or row.get("table_name") != asset.table or int(row.get("expected_rows", -1)) != asset.row_count
                or int(row.get("committed_rows", -1)) != asset.row_count):
            raise ImportFailure("previous_import_incomplete", "发现失败或未完成来源，请人工核对，禁止自动续灌")
        batch_rows = self.query(f"SELECT COUNT(*) AS batch_count,COALESCE(SUM(row_count),0) AS n FROM {qualified(asset.database, 'aic_demo_batches')} WHERE import_id=%s", (asset.import_id,))
        if (self._count(asset) != asset.row_count or int(batch_rows[0]["n"]) != asset.row_count
                or int(batch_rows[0]["batch_count"]) != (asset.row_count + BATCH_SIZE - 1) // BATCH_SIZE):
            raise ImportFailure("successful_import_changed", "已成功来源的数据或批次账本发生变化")
        return True

    def _import(self, asset, *, already_succeeded=False):
        self.receipt["phase"] = "asset_preflight"
        state = {"import_id": asset.import_id, "database": asset.database, "table": asset.table,
                 "source": asset.source, "source_sha256": asset.source_sha256,
                 "expected_rows": asset.row_count, "committed_rows": 0, "status": "checking"}
        self.receipt["assets"].append(state)
        if already_succeeded:
            state.update(status="already_succeeded", committed_rows=asset.row_count)
            self.save_receipt()
            return
        ledger = qualified(asset.database, "aic_demo_imports")
        self.cursor.execute(f"INSERT INTO {ledger} (import_id,source_path,source_sha256,table_name,expected_rows,committed_rows,status,started_at) VALUES (%s,%s,%s,%s,%s,0,'running',%s)",
            (asset.import_id, asset.source, asset.source_sha256, asset.table, asset.row_count, utc_now()))
        self.commit()
        self.receipt["phase"] = "import_batch"
        state["status"] = "running"
        names = (*META_COLUMNS, *(column.name for column in asset.columns))
        target = qualified(asset.database, asset.table)
        total = 0
        for number, (rows, content_sha) in enumerate(batches(self.plan, asset), start=1):
            if not rows or len(rows) > BATCH_SIZE:
                raise ImportFailure("batch_size_invalid", "来源批次超出已审查预算")
            placeholders = "(" + ",".join(["%s"] * len(names)) + ")"
            sql = f"INSERT INTO {target} ({','.join(quote(name) for name in names)}) VALUES " + ",".join([placeholders] * len(rows))
            self.cursor.execute(sql, tuple(value for row in rows for value in row))
            self.cursor.execute(f"INSERT INTO {qualified(asset.database, 'aic_demo_batches')} (import_id,batch_no,first_source_row,last_source_row,row_count,content_sha256,committed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (asset.import_id, number, rows[0][1], rows[-1][1], len(rows), content_sha, utc_now()))
            total += len(rows)
            self.cursor.execute(f"UPDATE {ledger} SET committed_rows=%s WHERE import_id=%s AND status='running'", (total, asset.import_id))
            self.commit()
            state["committed_rows"] = total
            self.receipt["rows_committed_this_run"] += len(rows)
            self.save_receipt()
        if total != asset.row_count or self._count(asset) != asset.row_count:
            raise ImportFailure("row_count_mismatch", "导入行数与源文件不一致")
        self.cursor.execute(f"UPDATE {ledger} SET status='succeeded',finished_at=%s WHERE import_id=%s AND status='running'", (utc_now(), asset.import_id))
        self.commit()
        state["status"] = "succeeded"
        self.save_receipt()

    def run(self):
        if self.receipt_file.exists():
            raise ImportFailure("receipt_exists", "本次收据已存在，请保留原记录并选择新收据路径")
        self.started = monotonic()
        self.deadline = self.started + self.budgets["total_timeout_seconds"]
        self.receipt.update(status="running", started_at=utc_now())
        self.save_receipt()
        try:
            self.receipt["phase"] = "runtime_preflight"
            validate_runtime_dependencies(self.plan)
            self._connect()
            self._tables()
            # 先检查所有历史状态，再写第一条新来源，防半途发现旧失败后继续灌。
            prior = {asset.import_id: self._existing(asset) for asset in self.plan.assets}
            for asset in self.plan.assets:
                self._import(asset, already_succeeded=prior[asset.import_id])
            self.receipt.update(status="succeeded", phase="finished")
        except Exception as exc:
            code = getattr(exc, "code", "")
            if not isinstance(code, str) or not code:
                code = "source_invalid" if isinstance(exc, PackageError) else "import_failed"
            self.receipt.update(status="failed", error={"code": code,
                "message": str(exc) if isinstance(exc, (ImportFailure, PackageError)) else "导入失败，已停止；请核对本机日志与连接状态"},
                diagnostics=failure_diagnostics(exc),
                requires_query_stop_check=self.connection is not None)
            # 不记录 SQL/参数/驱动原始错误，不为失败连接再发回滚或失败标记语句。
            if self.connection is not None:
                self._interrupt()
        finally:
            self.password = ""
            if self.connection is not None:
                try:
                    force_close = getattr(self.connection, "_force_close", None)
                    force_close() if force_close else self.connection.close()
                except Exception:
                    pass
            self.receipt.update(finished_at=utc_now(), elapsed_seconds=round(monotonic() - self.started, 3))
            self.save_receipt()
        return self.receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=REPO / PACKAGE_NAME)
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--username", default=os.environ.get("AIC_DEMO_MYSQL_USER", ""))
    parser.add_argument("--password-env", default="AIC_DEMO_MYSQL_PASSWORD")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(args.package_root)
        public = plan.public()
        if not args.execute:
            if args.plan_output:
                args.plan_output.parent.mkdir(parents=True, exist_ok=True)
                with args.plan_output.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(public, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(public, ensure_ascii=False))
            return 0
        if not args.plan_file or not args.proof or not args.username:
            raise ImportFailure("execution_arguments_missing", "执行需要已审查计划、实例证明和用户名")
        expected = json.loads(args.plan_file.read_text(encoding="utf-8"))
        if expected != public:
            raise ImportFailure("plan_changed", "源文件或导入计划已变化，请重新核对计划")
        password = os.environ.get(args.password_env) or getpass.getpass("本机MySQL密码：")
        receipt_path = args.receipt or REPO / ".local/mysql-demo" / ("import-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".json")
        result = Importer(plan, proof_file=args.proof, username=args.username, password=password,
                          receipt_file=receipt_path).run()
        password = ""
        print(json.dumps({"status": result["status"], "receipt": str(receipt_path),
            "rows_committed_this_run": result["rows_committed_this_run"],
            "connections": result["connections"], "statements": result["statements"],
            "error": result.get("error"), "requires_query_stop_check": result["requires_query_stop_check"]}, ensure_ascii=False))
        return 0 if result["status"] == "succeeded" else 1
    except Exception as exc:
        code = exc.code if isinstance(exc, ImportFailure) else "plan_invalid"
        print(json.dumps({"status": "failed", "error": {"code": code,
            "message": str(exc) if isinstance(exc, (ImportFailure, PackageError)) else "计划或本地文件核查失败"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
