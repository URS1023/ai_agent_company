"""受审恢复仅用新临时文件与内存连接替身，不触发真实 MySQL。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demo_data import recover_mysql as recovery
from tools.demo_data.package import Plan
from test_demo_data_import import MemoryDatabase, MemoryConnection, MemoryCursor, csv_asset, runner


class RecoveryDatabase(MemoryDatabase):
    def __init__(self):
        super().__init__()
        self.old_connection_running = False
        self.change_checkpoint = False

    def connect(self, **kwargs):
        connection = RecoveryConnection(self, kwargs)
        self.connections.append(connection)
        return connection


class RecoveryConnection(MemoryConnection):
    def thread_id(self):
        return 84

    def cursor(self):
        return RecoveryCursor(self)


class RecoveryCursor(MemoryCursor):
    def execute(self, sql, parameters=()):
        database, state = self.connection.database, self.connection.transaction
        target = re.search(r"`([^`]+)`\.`([^`]+)`", sql)
        key = tuple(target.groups()) if target else None
        if "information_schema.PROCESSLIST" in sql:
            database.calls.append((sql, parameters))
            self.rows([{"ID": parameters[0]}] if database.old_connection_running else [], ("ID",))
            return 1
        if sql.startswith("SELECT *") and key[1] == "aic_demo_batches":
            database.calls.append((sql, parameters))
            rows = []
            for (db, identifier, number), row in sorted(state["batches"].items()):
                if db == key[0] and identifier == parameters[0]:
                    rows.append({"import_id": identifier, "batch_no": number, "first_source_row": row["first"],
                        "last_source_row": row["last"], "row_count": row["row_count"], "content_sha256": row["sha"], "committed_at": row["at"]})
            self.rows(rows[:parameters[1]], ("batch_no",))
            return 1
        if sql.startswith("SELECT `") and " BETWEEN " in sql:
            database.calls.append((sql, parameters))
            names = [name for name, _ in database.metadata[key]["columns"]]
            identifier, first, last, limit = parameters
            values = sorted((row for row in state["data"].get(key, []) if row[0] == identifier and first <= row[1] <= last), key=lambda row: row[1])[:limit]
            self.rows([dict(zip(names, row)) for row in values], names)
            return 1
        if sql.startswith("SELECT *") and sql.endswith("FOR UPDATE") and database.change_checkpoint:
            state["imports"][(key[0], parameters[0])]["committed_rows"] += 1
            database.change_checkpoint = False
        if sql.startswith("UPDATE") and "SET status='running'" in sql:
            database.calls.append((sql, parameters))
            row = state["imports"][(key[0], parameters[0])]
            if row["status"] != "failed" or row["committed_rows"] != parameters[1]:
                return 0
            row["status"] = "running"
            return 1
        if sql.startswith("UPDATE") and "SET committed_rows" in sql and len(parameters) == 3:
            database.calls.append((sql, parameters))
            row = state["imports"][(key[0], parameters[1])]
            if row["committed_rows"] != parameters[2] or row["status"] != "running":
                return 0
            row["committed_rows"] = parameters[0]
            return 1
        return super().execute(sql, parameters)


def prepare(tmp_path, *, combined=False):
    database = RecoveryDatabase()
    if combined:
        assets = []
        for name, count in (("first", 1), ("partial", 1001), ("future", 2)):
            folder = tmp_path / name
            folder.mkdir()
            _, asset = csv_asset(folder, count)
            assets.append(replace(asset, source=f"{name}/{asset.source}"))
        plan = Plan(tmp_path, tuple(assets))
        database.fail_insert = 3
    else:
        plan, _ = csv_asset(tmp_path, 1001)
        database.fail_insert = 2
    source_plan = tmp_path / "plan.json"
    source_plan.write_text(json.dumps(plan.public()), encoding="utf-8")
    original = runner(plan, tmp_path, database, "original.json").run()
    assert original["status"] == "failed"
    database.fail_insert = None
    candidate, original = recovery.recovery_plan(plan, source_plan, tmp_path / "original.json")
    review = {"kind": "ai_agent_company-demo-import-stop-review-v1",
              "original_receipt_sha256": candidate["original_receipt_sha256"],
              "plan_sha256": candidate["original_plan_sha256"],
              "recovery_plan_sha256": candidate["recovery_plan_sha256"],
              "connection_id": candidate["original_connection_id"], "connection_stopped": True,
              "importer_stopped": True, "resume_authorized": True,
              "checked_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": "纯测试操作员"}
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    return database, plan, source_plan, candidate, original, review_path


def worker(tmp_path, prepared):
    database, plan, _, candidate, original, review = prepared
    return recovery.RecoveryImporter(plan, original_receipt_file=tmp_path / "original.json",
        review_file=review, candidate=candidate, original=original, proof_file=tmp_path / "proof.json",
        username="demo-root", password="secret-must-not-persist", receipt_file=tmp_path / "recovery.json",
        connection_factory=database.connect,
        identity_guard=lambda *_: {"server_uuid": "owned-local-instance", "data_directory": str(tmp_path), "port": 3306},
        connected_guard=lambda cursor, _: cursor.execute("SELECT identity"))


def test_full_reviewed_recovery_preserves_success_resumes_one_batch_and_imports_future(tmp_path):
    prepared = prepare(tmp_path, combined=True)
    database, plan, _, candidate, _, _ = prepared
    before = {key: list(rows) for key, rows in database.committed["data"].items()}
    result = worker(tmp_path, prepared).run()
    assert result["status"] == "succeeded", result
    assert result["plan_sha256"] == plan.public()["plan_sha256"]
    assert result["recovery_budget"] == 1800 and result["recovery"]["stop_rechecked"] is True
    assert result["rows_committed_this_run"] == 3 and result["connections"] == 1
    assert [row["status"] for row in result["assets"]] == ["already_succeeded", "succeeded", "succeeded"]
    assert [row["committed_rows"] for row in result["assets"]] == [1, 1001, 2]
    for key, original in before.items():
        assert database.committed["data"][key][:len(original)] == original
    assert len(result["assets"]) == len(plan.assets)
    assert "secret-must-not-persist" not in (tmp_path / "recovery.json").read_text(encoding="utf-8")
    assert candidate["total_timeout_seconds"] == 1800


@pytest.mark.parametrize("corrupt,code", [
    ("data", "recovery_data_hash_mismatch"),
    ("hash", "recovery_batch_hash_mismatch"),
    ("gap", "recovery_batches_invalid"),
    ("count", "recovery_count_mismatch"),
])
def test_unknown_or_tampered_commit_never_writes_new_rows(tmp_path, corrupt, code):
    prepared = prepare(tmp_path)
    database, plan = prepared[:2]
    asset = plan.assets[0]
    if corrupt == "data":
        rows = database.committed["data"][(asset.database, asset.table)]
        rows[0] = (*rows[0][:-1], "changed")
    elif corrupt == "hash":
        next(iter(database.committed["batches"].values()))["sha"] = "0" * 64
    elif corrupt == "gap":
        key = next(iter(database.committed["batches"]))
        database.committed["batches"][(key[0], key[1], 2)] = database.committed["batches"].pop(key)
    else:
        database.committed["imports"][(asset.database, asset.import_id)]["committed_rows"] = 999
    count = database.inserts
    result = worker(tmp_path, prepared).run()
    assert result["status"] == "failed" and result["error"]["code"] == code
    assert database.inserts == count


def test_original_connection_presence_blocks_before_source_readback(tmp_path):
    prepared = prepare(tmp_path)
    database = prepared[0]
    database.old_connection_running = True
    before = len(database.calls)
    result = worker(tmp_path, prepared).run()
    assert result["error"]["code"] == "original_connection_running"
    assert "information_schema.PROCESSLIST" in database.calls[-1][0]
    assert not any(sql.startswith(("INSERT", "UPDATE", "CREATE")) for sql, _ in database.calls[before:])


def test_changed_checkpoint_after_readback_is_locked_and_rejected(tmp_path):
    prepared = prepare(tmp_path)
    database = prepared[0]
    database.change_checkpoint = True
    before = database.inserts
    result = worker(tmp_path, prepared).run()
    assert result["error"]["code"] == "recovery_checkpoint_changed"
    assert database.inserts == before


@pytest.mark.parametrize("change", [
    {"resume_authorized": False}, {"connection_stopped": "true"},
    {"importer_stopped": False}, {"original_receipt_sha256": "changed"},
    {"checked_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()},
])
def test_review_requires_exact_fresh_stop_and_authorization(tmp_path, change):
    prepared = prepare(tmp_path)
    database, _, _, candidate, _, review_path = prepared
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review.update(change)
    review_path.write_text(json.dumps(review), encoding="utf-8")
    connections = len(database.connections)
    with pytest.raises(recovery.ImportFailure):
        worker(tmp_path, prepared)
    assert len(database.connections) == connections


def test_changed_original_receipt_prevents_opening_new_connection(tmp_path):
    prepared = prepare(tmp_path)
    database = prepared[0]
    instance = worker(tmp_path, prepared)
    original = tmp_path / "original.json"
    original.write_text(original.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    connections = len(database.connections)
    result = instance.run()
    assert result["error"]["code"] == "original_receipt_changed"
    assert len(database.connections) == connections


def test_default_cli_prepares_candidate_only_and_never_connects(tmp_path, monkeypatch, capsys):
    prepared = prepare(tmp_path)
    database, plan, source_plan = prepared[:3]
    output = tmp_path / "candidate.json"
    monkeypatch.setattr(recovery, "build_plan", lambda _: plan)
    monkeypatch.setattr(recovery, "RecoveryImporter", lambda *_args, **_kwargs: pytest.fail("default must not connect"))
    monkeypatch.setattr(sys, "argv", ["recover", "--source-plan-file", str(source_plan),
        "--original-receipt", str(tmp_path / "original.json"), "--plan-output", str(output)])
    before = len(database.connections)
    assert recovery.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["requires_reviewed_stop"] is True and result["total_timeout_seconds"] == 1800
    assert len(database.connections) == before


def test_canonical_driver_types_preserve_null_empty_and_large_integer():
    names = ("id", "value", "missing", "empty", "precise")
    expected = [(2**60, 1.25, None, "", recovery.Decimal("1.2300"))]
    rows = [{"id": recovery.Decimal(2**60), "value": recovery.Decimal("1.25"), "missing": None,
             "empty": "", "precise": recovery.Decimal("1.2300")}]
    assert recovery.canonical_rows(rows, expected, names) == expected
    rows[0]["missing"] = ""
    with pytest.raises(recovery.ImportFailure):
        recovery.canonical_rows(rows, expected, names)
