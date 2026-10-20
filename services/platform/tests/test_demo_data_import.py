"""只用新临时文件与内存驱动替身验证导入，不连接任何数据库。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import json
from pathlib import Path
import re
import sys
from time import monotonic

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demo_data import package as pkg
from tools.demo_data.import_mysql import Importer, ImportFailure, quote


def csv_asset(tmp_path, count=3):
    path = tmp_path / "samples.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["设备编号", "采集时间", "轴承温度_C"])
        for index in range(count):
            writer.writerow(["SP-JZ-001", f"2026-10-19T10:{index % 60:02}:00+08:00", "1.000000000000000001"])
    columns = tuple(pkg.Column(name, pkg._text_type(name), "source-text") for name in ["设备编号", "采集时间", "轴承温度_C"])
    asset = pkg.Asset(path.name, pkg.file_sha256(path), path.stat().st_size, "csv", "szh_new_food",
                      "aic_demo_inspections", count, columns)
    return pkg.Plan(tmp_path, (asset,)), asset


class MemoryDatabase:
    """模拟新表及事务提交，不使用 SQLite 或真实 MySQL。"""

    def __init__(self):
        self.metadata, self.committed = {}, {"imports": {}, "batches": {}, "data": {}}
        self.calls, self.connections = [], []
        self.fail_insert = None
        self.inserts = 0

    def connect(self, **kwargs):
        connection = MemoryConnection(self, kwargs)
        self.connections.append(connection)
        return connection


class MemoryConnection:
    def __init__(self, database, kwargs):
        self.database, self.kwargs = database, kwargs
        self.transaction = deepcopy(database.committed)
        self.closed = False

    def thread_id(self):
        return 42

    def cursor(self):
        return MemoryCursor(self)

    def commit(self):
        assert not self.closed
        self.database.committed = deepcopy(self.transaction)

    def _force_close(self):
        self.closed = True

    close = _force_close


class MemoryCursor:
    def __init__(self, connection):
        self.connection, self.description, self.result = connection, [], []

    def rows(self, records, names=()):
        names = tuple(records[0]) if records else tuple(names)
        self.description = [(name,) for name in names]
        self.result = [tuple(record[name] for name in names) for record in records]

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def execute(self, sql, parameters=()):
        assert not self.connection.closed
        database, state = self.connection.database, self.connection.transaction
        database.calls.append((sql, parameters))
        target = re.search(r"`([^`]+)`\.`([^`]+)`", sql)
        key = tuple(target.groups()) if target else None
        if sql == "SELECT identity":
            self.rows([{"ok": True}])
        elif sql.startswith("EXPLAIN FORMAT=JSON"):
            self.rows([{"EXPLAIN": json.dumps({"query_block": {"table": {"table_name": key[1], "access_type": "ref", "key": "PRIMARY", "rows_examined_per_scan": 1001}}})}])
        elif sql.startswith("SET SESSION"):
            self.rows([])
        elif "information_schema.SCHEMATA" in sql:
            self.rows([{"SCHEMA_NAME": name} for name in pkg.DATABASES])
        elif "information_schema.TABLES" in sql:
            entry = database.metadata.get(tuple(parameters))
            self.rows([{"TABLE_COMMENT": entry["comment"], "TABLE_TYPE": "BASE TABLE"}] if entry else [], ("TABLE_COMMENT", "TABLE_TYPE"))
        elif "information_schema.COLUMNS" in sql:
            self.rows([{"COLUMN_NAME": name, "COLUMN_TYPE": kind.lower()} for name, kind in database.metadata[tuple(parameters)]["columns"]])
        elif sql.startswith("CREATE TABLE"):
            assert key not in database.metadata
            columns = re.findall(r"`([^`]+)`\s+([A-Z]+(?:\(\d+(?:,\d+)?\))?)\s+(?:NOT NULL|NULL)", sql)
            database.metadata[key] = {"comment": parameters[0], "columns": columns}
            self.rows([])
        elif sql.startswith("SELECT *"):
            value = state["imports"].get((key[0], parameters[0]))
            self.rows([value] if value else [], ("import_id",))
        elif sql.startswith("SELECT COUNT(*) AS batch_count"):
            rows = [row for (db, import_id, _), row in state["batches"].items() if db == key[0] and import_id == parameters[0]]
            self.rows([{"batch_count": len(rows), "n": sum(row["row_count"] for row in rows)}])
        elif sql.startswith("SELECT COUNT(*)"):
            self.rows([{"n": sum(row[0] == parameters[0] for row in state["data"].get(key, []))}])
        elif sql.startswith("INSERT INTO") and key[1] == "aic_demo_imports":
            import_id, source_path, source_sha, table, expected, started = parameters
            state["imports"][(key[0], import_id)] = {"import_id": import_id, "source_path": source_path,
                "source_sha256": source_sha, "table_name": table, "expected_rows": expected,
                "committed_rows": 0, "status": "running", "started_at": started, "finished_at": None}
        elif sql.startswith("INSERT INTO") and key[1] == "aic_demo_batches":
            import_id, number, first, last, count, sha, at = parameters
            state["batches"][(key[0], import_id, number)] = {"first": first, "last": last, "row_count": count, "sha": sha, "at": at}
        elif sql.startswith("INSERT INTO"):
            database.inserts += 1
            if database.fail_insert == database.inserts:
                raise RuntimeError("敏感驱动原文 password=do-not-print")
            names = re.findall(r"`([^`]+)`", sql.split(" VALUES ")[0])[2:]
            width = len(names)
            assert len(parameters) % width == 0
            state["data"].setdefault(key, []).extend(tuple(parameters[index:index + width]) for index in range(0, len(parameters), width))
        elif sql.startswith("UPDATE") and "SET committed_rows" in sql:
            state["imports"][(key[0], parameters[1])]["committed_rows"] = parameters[0]
        elif sql.startswith("UPDATE") and "SET status='succeeded'" in sql:
            state["imports"][(key[0], parameters[1])].update(status="succeeded", finished_at=parameters[0])
        else:
            raise AssertionError(f"测试出现未登记SQL类型：{sql[:80]}")
        return 1


def runner(plan, tmp_path, database, name="receipt.json", **kwargs):
    def identity(_proof, provider, target):
        assert provider == "mysql"
        assert target["host"] == "127.0.0.1" and target["port"] == 3306
        assert target["database"] in pkg.DATABASES
        return {"server_uuid": "owned-local-instance", "data_directory": str(tmp_path), "port": 3306}

    return Importer(plan, proof_file=tmp_path / "proof.json", username="test-user", password="secret-in-memory",
        receipt_file=tmp_path / name, connection_factory=database.connect,
        identity_guard=identity, connected_guard=lambda cursor, _: cursor.execute("SELECT identity"), **kwargs)


def test_complete_batches_commit_and_success_is_idempotent(tmp_path):
    plan, asset = csv_asset(tmp_path, 1001)
    database = MemoryDatabase()
    first = runner(plan, tmp_path, database).run()
    assert first["status"] == "succeeded", first
    assert first["connections"] == 1 and first["rows_committed_this_run"] == 1001
    rows = database.committed["data"][(asset.database, asset.table)]
    assert len(rows) == 1001 and [row[1] for row in rows] == list(range(1, 1002))
    assert rows[0][-1] == "1.000000000000000001" and rows[0][2] == "synthetic"
    assert len(database.committed["batches"]) == 2
    assert database.connections[0].kwargs["local_infile"] is False
    assert database.connections[0].kwargs["read_timeout"] == 5
    insert_count = database.inserts
    second = runner(plan, tmp_path, database, "second.json").run()
    assert second["status"] == "succeeded" and second["assets"][0]["status"] == "already_succeeded"
    assert second["rows_committed_this_run"] == 0 and database.inserts == insert_count
    assert "secret-in-memory" not in (tmp_path / "receipt.json").read_text(encoding="utf-8")


def test_failed_second_batch_stops_without_sql_retry_or_automatic_resume(tmp_path):
    plan, asset = csv_asset(tmp_path, 1001)
    database = MemoryDatabase()
    database.fail_insert = 2
    result = runner(plan, tmp_path, database).run()
    assert result["status"] == "failed" and result["requires_query_stop_check"] is True
    assert result["rows_committed_this_run"] == 1000
    assert database.calls[-1][0].startswith(f"INSERT INTO `{asset.database}`.`{asset.table}`")
    assert len(database.committed["data"][(asset.database, asset.table)]) == 1000
    assert database.committed["imports"][(asset.database, asset.import_id)]["status"] == "running"
    assert "do-not-print" not in (tmp_path / "receipt.json").read_text(encoding="utf-8")
    before = database.inserts
    resumed = runner(plan, tmp_path, database, "second.json").run()
    assert resumed["error"]["code"] == "previous_import_incomplete"
    assert database.inserts == before


def test_unowned_same_name_table_is_preserved(tmp_path):
    plan, asset = csv_asset(tmp_path)
    database = MemoryDatabase()
    database.metadata[(asset.database, asset.table)] = {"comment": "原企业表", "columns": []}
    result = runner(plan, tmp_path, database).run()
    assert result["error"]["code"] == "table_ownership_conflict"
    assert database.metadata[(asset.database, asset.table)]["comment"] == "原企业表"
    assert database.inserts == 0
    assert not any(re.search(r"\b(DROP|TRUNCATE|ALTER|REPLACE)\b", sql) for sql, _ in database.calls)


def test_changed_source_fails_before_data_insert(tmp_path):
    plan, asset = csv_asset(tmp_path)
    (tmp_path / asset.source).write_text("changed", encoding="utf-8")
    database = MemoryDatabase()
    result = runner(plan, tmp_path, database).run()
    assert result["error"]["code"] == "source_invalid" and database.inserts == 0


def test_unknown_local_identity_never_opens_connection(tmp_path):
    plan, _ = csv_asset(tmp_path)
    database = MemoryDatabase()
    worker = runner(plan, tmp_path, database)
    worker.identity_guard = lambda *_: None
    assert worker.run()["error"]["code"] == "local_database_unverified"
    assert database.connections == []


def test_expired_deadline_interrupts_without_next_operation(tmp_path):
    plan, _ = csv_asset(tmp_path)
    worker = runner(plan, tmp_path, MemoryDatabase())
    worker.deadline = monotonic() - 1
    calls = []
    with pytest.raises(ImportFailure, match="时限"):
        worker.call(lambda: calls.append("not dispatched"))
    assert calls == [] and worker.interrupted.is_set()


def test_reads_require_known_small_index_plan_or_explicit_mysql_constant(tmp_path):
    plan, _ = csv_asset(tmp_path)
    worker = runner(plan, tmp_path, MemoryDatabase())
    worker._read_plan({"query_block": {"message": "Select tables optimized away"}})
    worker._read_plan({"query_block": {"table": {"table_name": "aic_demo_imports", "access_type": "const", "rows_examined_per_scan": 1}}})
    with pytest.raises(ImportFailure, match="索引"):
        worker._read_plan({"table": {"table_name": "aic_demo_data", "access_type": "ALL", "rows": 3}})
    with pytest.raises(ImportFailure, match="预算"):
        worker._read_plan({"table": {"table_name": "aic_demo_data", "access_type": "ref", "key": "PRIMARY", "rows": 500001}})
    with pytest.raises(ImportFailure):
        worker._read_plan({"query_block": {"message": "unrecognized optimization"}})


def test_source_partition_preserves_original_line_numbers(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text("设备编号,记录时间\nSP-JZ-001,2026-10-19T10:00:00+08:00\nHG-LXB-001,2026-10-19T10:00:00+08:00\nSP-JZ-002,2026-10-19T11:00:00+08:00\n", encoding="utf-8")
    columns = tuple(pkg.Column(name, "LONGTEXT", "source-text") for name in ["设备编号", "记录时间"])
    asset = pkg.Asset(path.name, pkg.file_sha256(path), path.stat().st_size, "csv", "szh_new_food", "aic_demo_events", 2, columns)
    rows = list(pkg.batches(pkg.Plan(tmp_path, (asset,)), asset))[0][0]
    assert [row[1] for row in rows] == [1, 3]
    assert rows[0][-1].endswith("+08:00")


def test_parquet_stream_keeps_all_columns_nulls_timezone_and_numeric_types(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    stamp = datetime(2026, 10, 19, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    table = pa.table({"设备编号": ["SP-JZ-001", "SP-JZ-001"], "采集时间": [stamp, stamp + timedelta(minutes=1)],
                      "测值": pa.array([1.25, None], type=pa.float32()), "精确值": pa.array([Decimal("1.23"), None], type=pa.decimal128(8, 2))})
    path = tmp_path / "data.parquet"
    pq.write_table(table, path)
    columns = tuple(pkg._arrow_column(field) for field in table.schema)
    asset = pkg.Asset(path.name, pkg.file_sha256(path), path.stat().st_size, "parquet", "szh_new_food", "aic_demo_food_telemetry", 2, columns, "SP-JZ-001")
    rows, checksum = list(pkg.batches(pkg.Plan(tmp_path, (asset,)), asset))[0]
    assert len(rows) == 2 and len(rows[0]) == 7 and len(checksum) == 64
    assert rows[0][4] == "2026-10-19T10:00:00+08:00"
    assert rows[0][5:] == (1.25, Decimal("1.23")) and rows[1][5:] == (None, None)
    assert columns[-1].sql_type == "DECIMAL(8,2)"


def test_plan_checks_actual_parquet_rows_and_targets_without_database(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    monkeypatch.setattr(pkg, "EXPECTED_DEVICE_ROWS", 2)
    (tmp_path / "数据规模.json").write_text(json.dumps({"设备数": 9, "监测总行数": 18}), encoding="utf-8")
    for device in pkg.DEVICE_IDS:
        columns = {"设备编号": [device, device], "采集时间": ["2026-10-19T10:00:00+08:00"] * 2}
        fields = 57 if device.startswith("FZ-") else 56
        columns.update({f"测值{index}": [1.0, 2.0] for index in range(fields - 2)})
        path = tmp_path / f"监测数据/{device}/监测.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(pa.table(columns), path)
    for relative, _ in pkg.SMALL_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".csv":
            path.write_text("设备编号,名称\nSP-JZ-001,食品\nHG-LXB-001,化工\n", encoding="utf-8")
        else:
            path.write_text(json.dumps({"设备编号": "SP-JZ-001", "名称": "食品"}, ensure_ascii=False) + "\n" + json.dumps({"设备编号": "HG-LXB-001", "名称": "化工"}, ensure_ascii=False) + "\n", encoding="utf-8")
    plan = pkg.build_plan(tmp_path)
    assert len(plan.assets) == 21 and plan.public()["rows"] == 30
    assert json.loads(json.dumps(plan.public())) == plan.public()
    assert len(plan.public()["tables"]) == 15
    assert {a.database for a in plan.assets if a.device_id and a.device_id.startswith("SP-")} == {"szh_new_food"}
    path = tmp_path / "监测数据/SP-JZ-001/监测.parquet"
    pq.write_table(pa.table({"设备编号": ["SP-JZ-001"]}), path)
    with pytest.raises(pkg.PackageError, match="行列数"):
        pkg.build_plan(tmp_path)


def test_identifier_escape_and_reserved_fields_are_not_executable(tmp_path):
    assert quote("字段`名") == "`字段``名`"
    with pytest.raises(pkg.PackageError):
        pkg._identifier("__aic_import_id")
    with pytest.raises(ImportFailure):
        quote("bad\0name")
    with pytest.raises(pkg.PackageError):
        pkg.database_for_device("UNKNOWN-001")
    outside = tmp_path.parent / "outside-source.txt"
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(pkg.PackageError):
        pkg.source_path(tmp_path, "../outside-source.txt")
