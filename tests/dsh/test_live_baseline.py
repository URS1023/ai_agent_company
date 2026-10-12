"""使用纯假连接验证只读基准的预算与停止行为，不访问任何数据库。"""
from __future__ import annotations

from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("live_baseline", Path(__file__).with_name("live_baseline.py"))
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)
FIXTURE_DEVICE_ID = "fixture-device"


class FakeConnection:
    """只解释本测试所需的有限 SQL，任何未预期语句立即报错。"""

    def __init__(self, on_command, *, bad_plan=False, business_error=False, empty=False, diagnostic=False, reviewed_plan=False):
        self.on_command = on_command
        self.bad_plan = bad_plan
        self.business_error = business_error
        self.empty = empty
        self.diagnostic = diagnostic
        self.reviewed_plan = reviewed_plan
        self.statements = []
        self.rows = []
        self.description = None
        self.closed = False
        self.diagnostic_reads = 0

    def thread_id(self):
        return 123

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, sql, parameters=None):
        _, event = self.on_command(sql)
        self.statements.append((sql, parameters))
        self.description = None if sql.startswith(("SET ", "KILL ")) else ("rows",)
        self.rows = []
        if sql.startswith(("SET ", "KILL ")):
            pass
        elif "information_schema.PROCESSLIST" in sql:
            self.diagnostic_reads += 1
            self.rows = [{"ID": 123, "COMMAND": "Query", "TIME": 0, "STATE": "executing"}] if self.diagnostic_reads == 1 else []
        elif sql.startswith("SELECT DATABASE()"):
            self.rows = [{"database_name": "szh_new", "hostname": "fixture-machine", "port": 3306,
                          "connection_id": 123, "version": "8.0-fixture", "datadir": "fixture-only"}]
        elif "information_schema.TABLES" in sql:
            self.rows = [
                {"TABLE_NAME": table, "TABLE_TYPE": "BASE TABLE", "ENGINE": "InnoDB", "TABLE_ROWS": 20,
                 "TABLE_COMMENT": "", "COLUMN_NAME": column, "COLUMN_TYPE": "varchar(64)",
                 "COLUMN_COMMENT": "", "IS_NULLABLE": "YES", "COLUMN_KEY": "PRI" if column.endswith("Id") else ""}
                for table, columns in baseline.REQUIRED_COLUMNS.items() for column in sorted(columns)
            ]
        elif sql.startswith("EXPLAIN "):
            self.rows = [{"table": alias, "type": "ref", "key": "fixture_index", "rows": 1} for alias in ("d", "i", "p", "r")]
            if self.bad_plan:
                self.rows[2].update(type="ALL", key=None)
            if self.reviewed_plan:
                self.rows[1].update(type="ALL", key=None, rows=1606)
                self.rows[3].update(type="ALL", key=None, rows=46)
        elif sql == baseline.DRIVER_SQL:
            if self.business_error:
                event["status"] = "failed"
                raise TimeoutError("假连接超时，不存在真实查询")
            self.rows = [] if self.empty else [{
                "device_id": FIXTURE_DEVICE_ID, "device_name": "离线测试设备", "plan_id": "fixture-plan",
                "plan_name": "假数据计划", "requirement_id": "fixture-requirement", "point_name": "测试点位",
                "cycle_days": Decimal("30.00"), "required_oil": None, "required_amount": "测试量", "required_method": "测试方法",
            }]
        else:
            raise AssertionError("假连接拒绝未声明 SQL")
        event["status"] = "ok"

    def fetchmany(self, count):
        return self.rows[:count]

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class LiveBaselineOfflineTests(unittest.TestCase):
    """不使用平台配置或网络，独立校验固定查询的执行约束。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="offline-live-baseline-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.output = self.directory / "evidence"
        self.stop = self.directory / "STOP.json"
        self.connections = []
        self.patches = [
            patch.dict(os.environ, {"LIVE_BASELINE_DEVICE_ID": ""}),
            patch.object(baseline, "STOP", self.stop),
            patch.object(baseline, "_credentials", return_value=("fixture-user", "fixture-secret")),
            patch.object(baseline, "_verify_local_service", return_value={"machine": "fixture-machine"}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_baseline(self, *, device_id=FIXTURE_DEVICE_ID, accept_reviewed_small_join=False, **options):
        def connect(user, password, seconds, on_command):
            self.assertEqual((user, password), ("fixture-user", "fixture-secret"))
            self.assertLessEqual(seconds, 3)
            diagnostic = bool(self.connections)
            connection = FakeConnection(on_command, diagnostic=diagnostic, **({} if diagnostic else options))
            self.connections.append(connection)
            return connection
        with patch.object(baseline, "_open_connection", side_effect=connect):
            return baseline.prepare_baseline(self.output, device_id=device_id,
                                             accept_reviewed_small_join=accept_reviewed_small_join)

    def test_success_one_connection_one_business_query_and_private_metadata(self):
        result = self.run_baseline()
        self.assertEqual((result["status"], result["connections"], result["business_selects"]), ("passed", 1, 1))
        self.assertEqual(len(self.connections), 1)
        statements = [statement for statement, _ in self.connections[0].statements]
        self.assertEqual(statements.count(baseline.DRIVER_SQL), 1)
        self.assertLess(statements.index("EXPLAIN " + baseline.DRIVER_SQL), statements.index(baseline.DRIVER_SQL))
        self.assertEqual(len(result["metadata"]), 4)
        self.assertEqual(result["golden_rows"][0]["cycle_days"], "30.00")
        self.assertIsNone(result["golden_rows"][0]["required_oil"])
        self.assertFalse(self.stop.exists())
        self.assertTrue(self.connections[0].closed)
        for file in ("baseline.json", "metadata.json", "plan.json", "golden.json", "baseline.sql"):
            text = (self.output / file).read_text(encoding="utf-8")
            self.assertNotIn("fixture-secret", text)
        self.assertGreaterEqual(result["timings"]["total_milliseconds"], 0)

    def test_unbounded_plan_stops_before_select(self):
        with self.assertRaises(baseline.BaselineStopped):
            self.run_baseline(bad_plan=True)
        result = json.loads((self.output / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(result["business_selects"], 0)
        self.assertFalse(self.stop.exists())
        self.assertNotIn(baseline.DRIVER_SQL, [sql for sql, _ in self.connections[0].statements])
        self.assertNotIn("diagnostics", result)
        self.assertEqual(len(self.connections), 1)

    def test_timeout_diagnoses_only_original_id_without_retry(self):
        with self.assertRaises(baseline.BaselineStopped):
            self.run_baseline(business_error=True)
        self.assertEqual(len(self.connections), 2)
        result = json.loads((self.output / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(result["business_selects"], 1)
        self.assertTrue(result["diagnostics"]["killed_query"])
        self.assertFalse(result["diagnostics"]["remaining_unverified"])
        diagnosis = self.connections[1].statements
        self.assertEqual(len(diagnosis), 3)
        self.assertEqual(diagnosis[1], ("KILL QUERY 123", None))
        self.assertEqual([parameters for sql, parameters in diagnosis if "PROCESSLIST" in sql], [(123,), (123,)])
        self.assertTrue(self.stop.exists())

    def test_empty_sample_does_not_choose_another_device(self):
        with self.assertRaises(baseline.BaselineStopped):
            self.run_baseline(empty=True)
        business = [(sql, params) for connection in self.connections for sql, params in connection.statements if sql == baseline.DRIVER_SQL]
        self.assertEqual(business, [(baseline.DRIVER_SQL, {"device_id": FIXTURE_DEVICE_ID})])
        self.assertFalse(self.stop.exists())
        self.assertEqual(len(self.connections), 1)

    def test_existing_stop_forbids_all_connections(self):
        self.stop.write_text("{}", encoding="utf-8")
        with self.assertRaises(baseline.BaselineStopped):
            self.run_baseline()
        self.assertEqual(self.connections, [])

    def test_missing_device_id_rejects_before_service_or_connection(self):
        with patch.object(baseline, "_open_connection") as connect, patch.object(baseline, "_verify_local_service") as verify:
            with self.assertRaisesRegex(baseline.BaselineStopped, "--device-id"):
                baseline.prepare_baseline(self.output)
            connect.assert_not_called()
            verify.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.stop.exists())

    def test_device_id_can_be_explicitly_supplied_by_environment(self):
        with patch.dict(os.environ, {"LIVE_BASELINE_DEVICE_ID": FIXTURE_DEVICE_ID}):
            result = self.run_baseline(device_id=None)
        self.assertEqual(result["parameters"], {"device_id": FIXTURE_DEVICE_ID})

    def test_existing_output_forbids_implicit_repeat(self):
        self.run_baseline()
        count = len(self.connections)
        with self.assertRaises(baseline.BaselineStopped):
            self.run_baseline()
        self.assertEqual(len(self.connections), count)

    def test_large_join_estimate_is_rejected(self):
        plan = [{"table": alias, "type": "ref", "key": "fixture", "rows": 100} for alias in ("d", "i", "p", "r")]
        with self.assertRaises(baseline.BaselineStopped):
            baseline._validate_plan(plan)

    def test_reviewed_small_scan_requires_explicit_opt_in(self):
        with self.assertRaises(baseline.BaselineStopped):
            self.run_baseline(reviewed_plan=True)
        result = json.loads((self.output / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(result["business_selects"], 0)
        self.assertFalse(self.stop.exists())

    def test_reviewed_small_scan_executes_once_and_preserves_strict_rejection(self):
        result = self.run_baseline(reviewed_plan=True, accept_reviewed_small_join=True)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["business_selects"], 1)
        validation = result["plan_validation"]
        self.assertFalse(validation["strict_policy"]["accepted"])
        self.assertEqual(validation["scan_total_estimate"], 1652)
        self.assertEqual(validation["scan_estimates"], {"i": 1606, "r": 46})
        self.assertEqual(len(self.connections), 1)

    def test_reviewed_policy_still_rejects_larger_or_other_scans(self):
        for alias, estimate in (("i", 2001), ("r", 51), ("d", 1), ("p", 1)):
            with self.subTest(alias=alias, estimate=estimate):
                plan = [{"table": name, "type": "ref", "key": "fixture", "rows": 1} for name in ("d", "i", "p", "r")]
                next(row for row in plan if row["table"] == alias).update(type="ALL", key=None, rows=estimate)
                with self.assertRaises(baseline.BaselineStopped):
                    baseline._validate_plan(plan, accept_reviewed_small_join=True)


if __name__ == "__main__":
    unittest.main()
