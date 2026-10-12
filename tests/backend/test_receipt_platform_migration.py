"""显式平台回执迁移的离线合同；禁止真实数据库、部署配置与模型连接。"""
from __future__ import annotations

import os
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import String
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError

from app.business_worker import receipt_platform_migration as migration
from app import schema


ROOT = Path(__file__).resolve().parents[2]


def test_receipt_migration_import_does_not_load_deployment_config():
    """真实既有迁移模块的导入不得触发配置读取或默认引擎构造。"""
    script = r'''
import importlib.abc
import socket
import sys
import sqlalchemy

class RejectDeploymentImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.config", "app.db"}:
            raise AssertionError("显式迁移导入了部署配置或默认引擎: " + fullname)

def forbidden(*args, **kwargs):
    raise AssertionError("离线导入禁止引擎构造和网络连接")

sys.meta_path.insert(0, RejectDeploymentImport())
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
sqlalchemy.create_engine = forbidden
import app.business_worker.receipt_migration
import app.business_worker.receipt_platform_migration as migration
migration.expected_columns()
assert "app.config" not in sys.modules and "app.db" not in sys.modules
print("真实迁移模块导入成功，未加载部署配置或构造引擎")
'''
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, "真实迁移导入边界失败，详情见原始子进程输出"


TARGET = migration.Target("127.0.0.1", 3306, "receipt_fixture", "fixture_user", "fixture-server", 3306)


def old_snapshot():
    """用真实注册模型列集合建立目录桩；来源摘要使用已证明的固定发布值。"""
    return {
        "server": {"database": TARGET.database, "version": "8.0.46", "hostname": TARGET.server_hostname, "port": 3306},
        "columns": {name: [{"name": column} for column in sorted(columns)]
                    for name, columns in migration.expected_columns().items()},
        "tables": {name: {"engine": "InnoDB", "type": "BASE TABLE", "options": ""}
                   for name in ("run_events", "schema_migrations")},
        "indexes": [],
        "versions": [{"version": migration.FROM_VERSION, "checksum": migration.FROM_CHECKSUM}],
    }


def receipt_column():
    return {"name": "receipt_key", "type": "varchar", "length": 64, "nullable": True,
            "default": None, "extra": "", "generated": ""}


def receipt_index():
    return {"name": migration.RECEIPT_INDEX, "unique": True, "columns": ["receipt_key"],
            "prefixes": [None], "expressions": [None], "type": "BTREE", "visible": True}


def add_receipt(snapshot, *, index=True, version=False):
    snapshot["columns"]["run_events"].append(receipt_column())
    if index:
        snapshot["indexes"].append(receipt_index())
    if version:
        snapshot["versions"].append({"version": migration.TO_VERSION, "checksum": migration.TO_CHECKSUM})
    return snapshot


class Result:
    """执行器返回值桩；不替换迁移状态机或真实启动校验器。"""

    def __init__(self, rows=(), scalar=None):
        self.rows = rows
        self.value = scalar

    def scalar(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.rows


class ControlledConnection:
    """模拟 MySQL DDL 隐式持久化和版本事务，注入确认丢失但不连接服务。"""

    def __init__(self, snapshot, fault=None):
        self.snapshot = snapshot
        self.fault = fault
        self.calls = []
        self.pending = None
        self.invalidated = False
        self.closed = False

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append(("execute", sql, deepcopy(parameters)))
        if "DATABASE()" in sql:
            if self.fault == "lost_read":
                raise OperationalError(sql, {}, Exception(2013, "lost"), connection_invalidated=True)
            return Result([dict(self.snapshot["server"])])
        if "GET_LOCK" in sql:
            return Result(scalar=0 if self.fault == "locked" else 1)
        if "RELEASE_LOCK" in sql:
            return Result(scalar=1)
        if sql.startswith("INSERT"):
            values = statement.compile().params
            self.pending = {"version": values["version"], "checksum": values["checksum"]}
            return Result()
        raise AssertionError("未声明的连接调用：" + sql)

    def exec_driver_sql(self, sql):
        self.calls.append(("driver", sql))
        if sql.startswith("SET SESSION"):
            return Result()
        if sql.startswith("ALTER TABLE"):
            self.snapshot["columns"]["run_events"].append(receipt_column())
            if self.fault == "interrupt_add_ack":
                raise KeyboardInterrupt("模拟列已持久化后等待确认时中断")
            if self.fault == "lost_add_ack":
                raise OperationalError(sql, {}, Exception(2013, "lost"), connection_invalidated=True)
            return Result()
        if sql.startswith("CREATE UNIQUE INDEX"):
            if self.fault == "index_duplicate":
                raise IntegrityError(sql, {}, Exception(1062, "duplicate"))
            self.snapshot["indexes"].append(receipt_index())
            if self.fault == "lost_index_ack":
                raise OperationalError(sql, {}, Exception(2013, "lost"), connection_invalidated=True)
            return Result()
        raise AssertionError("未声明的结构调用：" + sql)

    def commit(self):
        self.calls.append(("commit",))
        if self.pending is not None:
            self.snapshot["versions"].append(self.pending)
            self.pending = None
            if self.fault == "interrupt_commit_ack":
                raise KeyboardInterrupt("模拟版本已提交后等待确认时中断")
            if self.fault == "lost_commit_ack":
                raise OperationalError("COMMIT", {}, Exception(2013, "lost"), connection_invalidated=True)

    def rollback(self):
        self.calls.append(("rollback",))
        self.pending = None

    def invalidate(self):
        self.calls.append(("invalidate",))
        self.invalidated = True

    def close(self):
        self.calls.append(("close",))
        self.closed = True


def controlled_engine(monkeypatch, snapshot, fault=None):
    connection = ControlledConnection(snapshot, fault)
    engine = SimpleNamespace(dialect=mysql.dialect(),
        url=URL.create("mysql+pymysql", host=TARGET.host, port=TARGET.port, database=TARGET.database, username=TARGET.user),
        connect=lambda: connection)
    monkeypatch.setattr(migration, "read_catalog", lambda conn, target: deepcopy(conn.snapshot))

    def inspector(conn):
        assert conn is connection
        return SimpleNamespace(
            get_columns=lambda name: [{"name": item["name"], "type": String(item.get("length", 26)), "nullable": item.get("nullable", False)}
                                      for item in snapshot["columns"]["run_events"]],
            get_indexes=lambda name: [{"name": item["name"], "unique": item["unique"], "column_names": item["columns"]}
                                      for item in snapshot["indexes"]],
        )

    monkeypatch.setattr(schema, "inspect", inspector)
    return engine, connection


def test_fixed_release_checksums_are_real_registered_model_contracts():
    expected = migration.expected_columns()
    assert len(expected) == 154
    assert schema._contract_checksum() == migration.TO_CHECKSUM
    assert "receipt_key" not in expected["run_events"]
    assert migration.FROM_CHECKSUM != "legacy-fixed"


@pytest.mark.parametrize("state,steps", [
    ("legacy", ["add_column", "create_index", "register_version"]),
    ("column", ["create_index", "register_version"]),
    ("index", ["register_version"]),
    ("complete", []),
])
def test_offline_plan_recognizes_only_safe_recovery_states(state, steps):
    snapshot = old_snapshot()
    if state != "legacy":
        add_receipt(snapshot, index=state != "column", version=state == "complete")
    assert migration.plan(snapshot, TARGET)["operations"] == steps


@pytest.mark.parametrize("corrupt", ["old_checksum", "target_checksum", "newer", "missing_old_table", "wrong_server", "wrong_database", "mariadb", "partitioned", "wrong_column", "column_default", "prefix", "non_unique", "expression", "registered_missing_index"])
def test_conflicts_are_rejected_before_first_ddl(monkeypatch, corrupt):
    snapshot = old_snapshot()
    if corrupt == "old_checksum": snapshot["versions"][0]["checksum"] = "legacy-fixed"
    elif corrupt == "target_checksum": snapshot["versions"].append({"version": migration.TO_VERSION, "checksum": "wrong"})
    elif corrupt == "newer": snapshot["versions"].append({"version": "20261005_34", "checksum": "newer"})
    elif corrupt == "missing_old_table": snapshot["columns"].pop("users")
    elif corrupt == "wrong_server": snapshot["server"]["hostname"] = "elsewhere"
    elif corrupt == "wrong_database": snapshot["server"]["database"] = "szh_new"
    elif corrupt == "mariadb": snapshot["server"]["version"] = "8.0.46-MariaDB"
    elif corrupt == "partitioned": snapshot["tables"]["run_events"]["options"] = "partitioned"
    else:
        add_receipt(snapshot)
        column = snapshot["columns"]["run_events"][-1]
        index = snapshot["indexes"][0]
        if corrupt == "wrong_column": column["length"] = 32
        elif corrupt == "column_default": column["default"] = ""
        elif corrupt == "prefix": index["prefixes"] = [16]
        elif corrupt == "non_unique": index["unique"] = False
        elif corrupt == "expression": index["expressions"] = ["lower(receipt_key)"]
        elif corrupt == "registered_missing_index":
            snapshot["indexes"] = []
            snapshot["versions"].append({"version": migration.TO_VERSION, "checksum": migration.TO_CHECKSUM})
    engine, connection = controlled_engine(monkeypatch, snapshot)
    events = []
    with pytest.raises(migration.MigrationFailed) as raised:
        migration.migrate(engine, TARGET, events.append)
    assert raised.value.report["ddl_attempts"] == 0
    assert raised.value.report["version_insert_attempts"] == 0
    assert not any(call[0] == "driver" and call[1].startswith(("ALTER", "CREATE")) for call in connection.calls)
    assert connection.closed


def test_mysql_success_and_repeat_preserve_prior_versions_and_use_same_lock(monkeypatch):
    snapshot = old_snapshot()
    old = deepcopy(snapshot["versions"])
    snapshot["legacy_event_rows"] = [{"id": 1, "payload": {"legacy": "原样保留"}}]
    engine, connection = controlled_engine(monkeypatch, snapshot)
    events = []
    try:
        result = migration.migrate(engine, TARGET, events.append)
    except migration.MigrationFailed as error:
        # 本用例只有固定内存桩，记录真实被包装错误，避免诊断时丢失阶段和原因。
        print(json.dumps(error.report, ensure_ascii=False))
        if error.__context__ is not None:
            raise error.__context__
        raise
    assert result["status"] == "applied" and result["ddl_attempts"] == 2
    assert result["version_insert_attempts"] == 1 and result["committed"]
    assert snapshot["versions"][0] == old[0]
    assert snapshot["legacy_event_rows"] == [{"id": 1, "payload": {"legacy": "原样保留"}}]
    expected_lock = "test5_schema_" + hashlib.sha256(TARGET.database.encode()).hexdigest()[:32]
    locks = [call for call in connection.calls if call[0] == "execute" and "GET_LOCK" in call[1]]
    assert len(locks) == 1 and locks[0][2] == {"name": expected_lock, "seconds": 5}
    before = deepcopy(snapshot)
    engine2, connection2 = controlled_engine(monkeypatch, snapshot)
    result2 = migration.migrate(engine2, TARGET, events.append)
    assert result2["status"] == "already_applied" and result2["ddl_attempts"] == 0
    assert result2["version_insert_attempts"] == 0 and snapshot == before
    assert connection2.closed


def test_mysql_index_failure_preserves_committed_column_and_allows_later_explicit_recovery(monkeypatch):
    snapshot = old_snapshot()
    engine, connection = controlled_engine(monkeypatch, snapshot, "index_duplicate")
    with pytest.raises(migration.MigrationFailed) as raised:
        migration.migrate(engine, TARGET, lambda record: None)
    report = raised.value.report
    assert report["status"] == "partial" and report["confirmed_steps"] == ["add_column"]
    assert report["ddl_attempts"] == 2 and report["version_insert_attempts"] == 0
    assert not snapshot["indexes"] and len(snapshot["versions"]) == 1
    assert migration.plan(snapshot, TARGET)["operations"] == ["create_index", "register_version"]
    assert len([call for call in connection.calls if call[0] == "driver" and call[1].startswith("CREATE")]) == 1


@pytest.mark.parametrize("fault,confirmed,db_version", [
    ("lost_add_ack", [], False),
    ("lost_index_ack", ["add_column"], False),
    ("lost_commit_ack", ["add_column", "create_index"], True),
    ("lost_read", [], False),
])
def test_ack_loss_is_unknown_without_retry_or_followup_sql(monkeypatch, fault, confirmed, db_version):
    snapshot = old_snapshot()
    engine, connection = controlled_engine(monkeypatch, snapshot, fault)
    events = []
    with pytest.raises(migration.MigrationFailed) as raised:
        migration.migrate(engine, TARGET, events.append)
    report = raised.value.report
    assert report["status"] == "unknown" and report["confirmed_steps"] == confirmed
    assert connection.invalidated and connection.closed
    assert connection.calls[-2:] == [("invalidate",), ("close",)]
    assert not any(call[0] == "execute" and "RELEASE_LOCK" in call[1] for call in connection.calls)
    assert any(row["version"] == migration.TO_VERSION for row in snapshot["versions"]) is db_version
    assert events[-1]["status"] == "unknown"


def test_lock_refusal_does_not_attempt_ddl_or_unlock_another_owner(monkeypatch):
    engine, connection = controlled_engine(monkeypatch, old_snapshot(), "locked")
    with pytest.raises(migration.MigrationFailed) as raised:
        migration.migrate(engine, TARGET, lambda record: None)
    assert raised.value.report["ddl_attempts"] == 0
    assert not any(call[0] == "execute" and "RELEASE_LOCK" in call[1] for call in connection.calls)


def test_journal_failure_after_commit_is_not_reported_as_success(monkeypatch):
    snapshot = old_snapshot()
    engine, _ = controlled_engine(monkeypatch, snapshot)

    def broken_journal(record):
        if record["committed"]:
            raise OSError("模拟证据磁盘故障")

    with pytest.raises(migration.MigrationFailed) as raised:
        migration.migrate(engine, TARGET, broken_journal)
    assert raised.value.report["status"] == "evidence_failed"
    assert raised.value.report["committed"] and raised.value.report["evidence_write_failed"]
    assert any(row["version"] == migration.TO_VERSION for row in snapshot["versions"])


@pytest.mark.parametrize("dialect", [mysql.dialect(), postgresql.dialect()])
def test_real_contract_ddl_compilation_quotes_database_and_preserves_null_uniqueness(dialect):
    statements = migration.compile_statements(dialect, 'quoted"`schema')
    assert "VARCHAR(64) NULL" in statements["add_column"]
    assert "CREATE UNIQUE INDEX" in statements["create_index"]
    assert "ix_run_events_receipt_key" in statements["create_index"]
    assert "receipt_key" in statements["create_index"]
    assert "DROP" not in " ".join(statements.values())
    assert "CONCURRENTLY" not in statements["create_index"]
    assert dialect.identifier_preparer.quote_schema('quoted"`schema') in statements["add_column"]


def test_offline_cli_plan_never_constructs_an_engine(tmp_path, monkeypatch, capsys):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(old_snapshot()), encoding="utf-8")
    monkeypatch.setattr(migration, "create_engine", lambda *a, **k: pytest.fail("离线计划不能构造引擎"))
    args = ["plan", "--snapshot", str(path), "--host", TARGET.host, "--port", str(TARGET.port),
            "--database", TARGET.database, "--user", TARGET.user, "--expected-server-hostname", TARGET.server_hostname,
            "--expected-server-port", str(TARGET.server_port)]
    assert migration.main(args) == 0
    assert json.loads(capsys.readouterr().out)["operations"] == ["add_column", "create_index", "register_version"]


def test_journal_is_exclusive_and_records_complete_json(tmp_path):
    path = tmp_path / "receipt.jsonl"
    journal = migration.Journal(path)
    journal({"status": "started", "内容": "固定证据"})
    journal.close()
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        migration.Journal(path)
    assert path.read_bytes() == original
    assert json.loads(path.read_text(encoding="utf-8"))["内容"] == "固定证据"


def test_read_catalog_adapter_only_reads_bounded_metadata_and_version_rows():
    snapshot = old_snapshot()
    queries = []

    class CatalogConnection:
        """向真实目录读取适配器提供驱动行形状，不替代转换及预算逻辑。"""

        def execute(self, statement, parameters=None):
            sql = str(statement)
            queries.append((sql, parameters))
            if "DATABASE()" in sql:
                return Result([snapshot["server"]])
            if "information_schema.COLUMNS" in sql:
                return Result([{"table_name": name, "name": column["name"], "type": "varchar", "length": 26,
                                "nullable": "NO", "default": None, "extra": "", "generated": ""}
                               for name, columns in snapshot["columns"].items() for column in columns])
            if "information_schema.TABLES" in sql:
                return Result([{"name": name, **value} for name, value in snapshot["tables"].items()])
            if "information_schema.STATISTICS" in sql:
                return Result([])
            if "schema_migrations" in sql:
                return Result(snapshot["versions"])
            raise AssertionError("意外 SQL：" + sql)

    result = migration.read_catalog(CatalogConnection(), TARGET)
    assert len(queries) == 5
    assert "LIMIT 10001" in queries[1][0]
    assert all(column["nullable"] is False for values in result["columns"].values() for column in values)
    assert migration.plan(result, TARGET)["operations"] == ["add_column", "create_index", "register_version"]


def test_unknown_commit_is_written_to_actual_journal_without_a_success_line(tmp_path, monkeypatch):
    engine, _ = controlled_engine(monkeypatch, old_snapshot(), "lost_commit_ack")
    path = tmp_path / "ack-lost.jsonl"
    journal = migration.Journal(path)
    try:
        with pytest.raises(migration.MigrationFailed):
            migration.migrate(engine, TARGET, journal)
    finally:
        journal.close()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["status"] == "unknown"
    assert records[-1]["phase"] == "commit_version"
    assert records[-1]["confirmed_steps"] == ["add_column", "create_index"]
    assert not any(row["status"] in {"applied", "already_applied"} for row in records)


def test_apply_cli_does_not_echo_password_or_driver_exception(tmp_path, monkeypatch, capsys):
    secret = "isolated-password-never-log"
    journal = tmp_path / "apply.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(secret + "\n"))

    def failing_engine(url, **options):
        assert url.password == secret
        assert options["pool_pre_ping"] is False and options["connect_args"]["read_timeout"] == 30
        raise RuntimeError("driver accidentally echoed " + secret)

    monkeypatch.setattr(migration, "create_engine", failing_engine)
    args = ["apply", "--host", TARGET.host, "--port", str(TARGET.port), "--database", TARGET.database,
            "--user", TARGET.user, "--expected-server-hostname", TARGET.server_hostname, "--expected-server-port", "3306",
            "--maintenance-confirmed", "--local-service-confirmed", "--backup-reference", "fixture-backup",
            "--journal", str(journal), "--password-stdin"]
    assert migration.main(args) == 1
    capture = capsys.readouterr()
    assert secret not in capture.out + capture.err + journal.read_text(encoding="utf-8")


@pytest.mark.parametrize("fault", ["interrupt_add_ack", "interrupt_commit_ack"])
def test_keyboard_interrupt_after_database_effect_is_unknown(tmp_path, monkeypatch, fault):
    """真实迁移状态机等待确认时中断，效果已发生也不能缺失未知终态。"""
    snapshot = old_snapshot()
    engine, connection = controlled_engine(monkeypatch, snapshot, fault)
    path = tmp_path / (fault + ".jsonl")
    journal = migration.Journal(path)
    observed = None
    try:
        migration.migrate(engine, TARGET, journal)
    except BaseException as error:
        observed = error
    finally:
        journal.close()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    effect_present = (any(row["version"] == migration.TO_VERSION for row in snapshot["versions"])
                      if fault == "interrupt_commit_ack" else
                      any(column["name"] == "receipt_key" for column in snapshot["columns"]["run_events"]))
    print(json.dumps({"fault": fault, "effect_present": effect_present, "error_type": type(observed).__name__,
                      "records": records, "connection_calls": connection.calls}, ensure_ascii=False, default=str))
    assert effect_present, "负对照必须先发生数据库效果，再丢确认"
    assert isinstance(observed, migration.MigrationFailed), "进程中断未经过未知结果处理"
    assert observed.report["status"] == "unknown" and records[-1]["status"] == "unknown"
    assert connection.calls[-2:] == [("invalidate",), ("close",)]
    assert connection.invalidated and connection.closed
    assert not any(call[0] in {"rollback"} or call[0] == "execute" and "RELEASE_LOCK" in call[1]
                   for call in connection.calls)
    started = [record for record in records if record.get("event") == "operation_started"]
    assert started and started[-1]["ddl_attempts"] >= 1
    if fault == "interrupt_commit_ack":
        assert started[-1]["version_insert_attempts"] == 1


def test_keyboard_interrupt_before_write_keeps_original_interrupt(monkeypatch):
    """写入窗口外中断必须继续终止进程，不误称未知写入或吞成成功。"""
    snapshot = old_snapshot()
    engine, connection = controlled_engine(monkeypatch, snapshot)
    records = []

    def interrupt_before_write(record):
        records.append(deepcopy(record))
        if record.get("event") == "preflight_confirmed":
            raise KeyboardInterrupt("模拟写入前人工取消")

    with pytest.raises(KeyboardInterrupt, match="写入前人工取消"):
        migration.migrate(engine, TARGET, interrupt_before_write)
    assert records[-1]["status"] == "interrupted"
    assert records[-1]["ddl_attempts"] == records[-1]["version_insert_attempts"] == 0
    assert not connection.invalidated and connection.closed
    assert not any(call[0] == "driver" and call[1].startswith(("ALTER", "CREATE")) for call in connection.calls)
