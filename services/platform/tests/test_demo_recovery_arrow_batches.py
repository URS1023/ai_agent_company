"""一个小型真实Parquet与内存驱动验证恢复；计划、收据和数据库都不落盘。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demo_data import package as package
from tools.demo_data import recover_mysql as recovery
from test_demo_data_import import runner
from test_demo_mysql_recovery import RecoveryDatabase


@pytest.fixture(scope="module")
def dictionary_source(tmp_path_factory):
    """全组只写这一份不足10KB的来源；不同字典块保留原Arrow批边界。"""
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    folder = tmp_path_factory.mktemp("arrow-recovery-source")
    path = folder / "dictionary.parquet"
    schema = pa.schema([pa.field("设备编号", pa.dictionary(pa.int32(), pa.string())),
                        pa.field("测值", pa.int64())])
    with pq.ParquetWriter(path, schema) as writer:
        for index in range(3):
            writer.write_table(pa.Table.from_arrays([
                pa.array(["SP-JZ-001"] * 500).dictionary_encode(),
                pa.array(list(range(index * 500, (index + 1) * 500)), type=pa.int64()),
            ], schema=schema))
    assert path.stat().st_size < 10_000
    asset = package.Asset(path.name, package.file_sha256(path), path.stat().st_size,
        "parquet", "szh_new_food", "aic_demo_food_telemetry", 1500,
        tuple(package._arrow_column(field) for field in schema), "SP-JZ-001")
    return package.Plan(folder, (asset,)), asset


def prepared(tmp_path, monkeypatch, source, *, complete=False, budget_changes=None):
    """仅构造受审内存材料；真实文件只有模块级Parquet，不创建凭据或JSON收据。"""
    plan, asset = source
    database = RecoveryDatabase()
    database.fail_insert = None if complete else 3
    original_runner = runner(plan, tmp_path, database, "original.json")
    original_runner.save_receipt = lambda: None
    original = deepcopy(original_runner.run())
    assert original["status"] == ("succeeded" if complete else "failed")
    if complete:
        # 模拟来源已成功提交、整体收据尚未收尾时退出；不能重新写该成功来源。
        original["status"] = "running"
    database.fail_insert = None
    source_plan = tmp_path / "plan.json"
    original_file, review_file = tmp_path / "original.json", tmp_path / "review.json"
    materials = {source_plan: plan.public(), original_file: original}

    def load(path):
        value = materials[Path(path)]
        return deepcopy(value), package.digest_json(value)

    monkeypatch.setattr(recovery, "_load", load)
    candidate, original = recovery.recovery_plan(plan, source_plan, original_file)
    # 小fixture单来源的理论预算是2批；显式受审3批模拟真实多来源计划的全局余量。
    # 运行期间不能自行改预算；下面另验证不足时确实拒绝。
    candidate["maximum_readback_batches"] = 3
    candidate.update(budget_changes or {})
    candidate["recovery_plan_sha256"] = package.digest_json({
        key: value for key, value in candidate.items() if key != "recovery_plan_sha256"})
    materials[review_file] = {"kind": "ai_agent_company-demo-import-stop-review-v1",
        "original_receipt_sha256": candidate["original_receipt_sha256"],
        "plan_sha256": candidate["original_plan_sha256"],
        "recovery_plan_sha256": candidate["recovery_plan_sha256"],
        "connection_id": candidate["original_connection_id"], "connection_stopped": True,
        "importer_stopped": True, "resume_authorized": True,
        "checked_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": "内存回归复核"}
    worker = recovery.RecoveryImporter(plan, original_receipt_file=original_file,
        review_file=review_file, candidate=candidate, original=original,
        proof_file=tmp_path / "proof.json", username="fixture-user", password="fixture-only",
        receipt_file=tmp_path / "recovery.json", connection_factory=database.connect,
        identity_guard=lambda *_: {"server_uuid": "owned-local-instance", "data_directory": str(tmp_path), "port": 3306},
        connected_guard=lambda cursor, _: cursor.execute("SELECT identity"))
    worker.save_receipt = lambda: None
    return database, worker, candidate, asset


def test_dictionary_parquet_keeps_three_original_batches_and_row_hashes(dictionary_source):
    plan, asset = dictionary_source
    first = list(package.batches(plan, asset))
    second = list(package.batches(plan, asset))
    assert asset.public()["batch_count"] == 2
    assert [len(rows) for rows, _ in first] == [500, 500, 500]
    assert [(rows[0][1], rows[-1][1]) for rows, _ in first] == [(1, 500), (501, 1000), (1001, 1500)]
    assert [row[1] for rows, _ in first for row in rows] == list(range(1, 1501))
    assert first == second and all(package.digest_json(rows) == checksum for rows, checksum in first)


def test_complete_three_batch_source_is_verified_without_duplicate_writes(tmp_path, monkeypatch, dictionary_source):
    database, worker, candidate, asset = prepared(tmp_path, monkeypatch, dictionary_source, complete=True)
    before, inserts, frozen = deepcopy(database.committed), database.inserts, deepcopy(candidate)
    result = worker.run()
    assert result["status"] == "succeeded", result
    assert result["rows_committed_this_run"] == 0 and database.inserts == inserts
    assert database.committed == before and candidate == frozen
    assert result["readback_rows"] == 1500 and result["readback_batches"] == 3
    assert worker.source_batch_counts[asset.import_id] == result["source_batch_counts"][asset.import_id] == 3
    assert result["verified_sources"][0]["batch_count"] == 3
    assert result["remaining_work"]["batches"] == 0


def test_partial_source_resumes_only_the_original_third_batch(tmp_path, monkeypatch, dictionary_source):
    database, worker, candidate, asset = prepared(tmp_path, monkeypatch, dictionary_source)
    old_rows = deepcopy(database.committed["data"][(asset.database, asset.table)])
    old_batches, inserts, frozen = deepcopy(database.committed["batches"]), database.inserts, deepcopy(candidate)
    result = worker.run()
    assert result["status"] == "succeeded", result
    assert result["rows_committed_this_run"] == 500 and database.inserts == inserts + 1
    rows = database.committed["data"][(asset.database, asset.table)]
    assert rows[:1000] == old_rows and [row[1] for row in rows] == list(range(1, 1501))
    assert all(database.committed["batches"][key] == value for key, value in old_batches.items())
    expected = list(package.batches(worker.plan, asset))
    assert [database.committed["batches"][(asset.database, asset.import_id, number)]["sha"]
        for number in range(1, 4)] == [checksum for _, checksum in expected]
    assert result["readback_rows"] == 1000 and result["readback_batches"] == 2
    assert result["remaining_work"]["batches"] == 1 and candidate == frozen


@pytest.mark.parametrize("corrupt,code", [("gap", "recovery_batches_invalid"),
    ("extra", "recovery_batches_invalid"), ("hash", "recovery_batch_hash_mismatch")])
def test_dictionary_boundaries_do_not_weaken_ledger_checks(tmp_path, monkeypatch, dictionary_source, corrupt, code):
    database, worker, _, asset = prepared(tmp_path, monkeypatch, dictionary_source, complete=True)
    key = (asset.database, asset.import_id, 3)
    if corrupt == "gap":
        database.committed["batches"][(asset.database, asset.import_id, 4)] = database.committed["batches"].pop(key)
    elif corrupt == "extra":
        database.committed["batches"][(asset.database, asset.import_id, 4)] = deepcopy(database.committed["batches"][key])
    else:
        database.committed["batches"][key]["sha"] = "0" * 64
    before, inserts, start = deepcopy(database.committed), database.inserts, len(database.calls)
    result = worker.run()
    assert result["status"] == "failed" and result["error"]["code"] == code
    assert database.inserts == inserts and database.committed == before
    assert not any(sql.startswith(("INSERT", "UPDATE", "CREATE")) for sql, _ in database.calls[start:])


@pytest.mark.parametrize("limit", [{"maximum_readback_batches": 1}, {"maximum_readback_rows": 999}])
def test_reviewed_readback_limit_is_not_silently_expanded(tmp_path, monkeypatch, dictionary_source, limit):
    database, worker, candidate, _ = prepared(tmp_path, monkeypatch, dictionary_source, budget_changes=limit)
    before, inserts, frozen = deepcopy(database.committed), database.inserts, deepcopy(candidate)
    result = worker.run()
    assert result["status"] == "failed" and result["error"]["code"] == "recovery_readback_budget_exceeded"
    assert database.inserts == inserts and database.committed == before and candidate == frozen
    assert result["readback_batches"] == 1 and result["readback_rows"] == 500


def test_insufficient_remaining_commit_budget_stops_before_any_resume_write(tmp_path, monkeypatch, dictionary_source):
    database, worker, candidate, _ = prepared(tmp_path, monkeypatch, dictionary_source,
        budget_changes={"commit_budget": 1})
    before, inserts, start, frozen = deepcopy(database.committed), database.inserts, len(database.calls), deepcopy(candidate)
    result = worker.run()
    assert result["status"] == "failed" and result["error"]["code"] == "recovery_write_budget_exceeded"
    assert result["commits"] == 1 and result["readback_rows"] == 1000
    assert database.inserts == inserts and database.committed == before and candidate == frozen
    assert not any(sql.startswith(("INSERT", "UPDATE", "CREATE")) for sql, _ in database.calls[start:])
