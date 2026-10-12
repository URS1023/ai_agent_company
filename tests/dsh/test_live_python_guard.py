"""真实验收保护的离线回归；所有 MySQL 连接和结果均为内存假对象。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


GUARD_PATH = Path(__file__).parent / "live_python_guard" / "sitecustomize.py"
SPEC = importlib.util.spec_from_file_location("live_python_guard_under_test", GUARD_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeDatabaseError(Exception):
    """模拟驱动携带敏感信息的异常，确认审计不会记录原始文字。"""


class FakeConnection:
    """无需套接字的 PyMySQL 协议替身，保留真实 query/command 调用关系。"""

    next_id = 101
    log = []
    plan = {"query_block": {"table": {"table_name": "devices", "access_type": "ref",
                                      "rows_examined_per_scan": 12,
                                      "attached_condition": "secret-business-value"}}}
    failure = None
    delay = 0
    active = 0
    peak = 0
    diagnostic_rows = ()

    def __init__(self, *, host="127.0.0.1", port=3306, database="szh_new", user="fake-user",
                 password="fake-password", charset="utf8mb4", **kwargs):
        self.host, self.port, self.db = host, port, database
        self.user, self.password, self.charset = user, password, charset
        self.unix_socket = kwargs.get("unix_socket")
        self.client_flag = 65536
        self._sock = None
        self.identifier = type(self).next_id
        type(self).next_id += 1
        self._result = SimpleNamespace(rows=())
        self.connect()

    def thread_id(self):
        return self.identifier

    def connect(self, sock=None):
        self._execute_command(3, "SET NAMES utf8mb4")

    def query(self, sql, unbuffered=False):
        self._execute_command(3, sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            self._result = SimpleNamespace(rows=((json.dumps(type(self).plan),),))
        elif sql.startswith("SELECT ID, COMMAND, TIME FROM information_schema.PROCESSLIST"):
            self._result = SimpleNamespace(rows=type(self).diagnostic_rows)
        elif sql.startswith("KILL QUERY"):
            target = int(sql.split()[-1])
            type(self).diagnostic_rows = tuple(row for row in type(self).diagnostic_rows if row[0] != target)
            self._result = SimpleNamespace(rows=())
        elif sql.upper().startswith("SELECT") and " FROM " in sql.upper():
            if type(self).failure is not None:
                error, type(self).failure = type(self).failure, None
                raise error
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
            try:
                time.sleep(type(self).delay)
                self._result = SimpleNamespace(rows=(("secret-result-value",),))
            finally:
                type(self).active -= 1
        else:
            self._result = SimpleNamespace(rows=())
        return len(self._result.rows)

    def _execute_command(self, command, sql):
        type(self).log.append((self.identifier, sql))

    def _read_packet(self):
        return None

    def ping(self, reconnect=False):
        self._execute_command(14, "")

    def close(self):
        return None


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="test5-guard-offline-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        config = MODULE.GuardConfig(frozenset({"devices", "orders"}), self.directory / "audit.jsonl",
                                   self.directory / "STOP.json")
        self.guard = MODULE.LiveDatabaseGuard(config)
        self.connection_class = type("IsolatedFakeConnection", (FakeConnection,), {
            "log": [], "next_id": 101, "failure": None, "delay": 0, "active": 0, "peak": 0,
            "diagnostic_rows": (), "plan": FakeConnection.plan,
        })
        import pymysql.connections
        self.class_patch = patch.object(pymysql.connections, "Connection", self.connection_class)
        self.class_patch.start()
        self.addCleanup(self.class_patch.stop)
        self.guard.install_pymysql()

    def connection(self, **kwargs):
        return self.connection_class(**kwargs)

    def records(self):
        return [json.loads(line) for line in self.guard.config.audit_path.read_text(encoding="utf-8").splitlines()]

    def test_business_select_explains_first_and_does_not_log_values(self):
        connection = self.connection()
        connection.query("SELECT id FROM devices WHERE status='literal-secret'")
        queries = [sql for _, sql in self.connection_class.log]
        self.assertEqual(queries[-2], "EXPLAIN FORMAT=JSON SELECT id FROM devices WHERE status='literal-secret'")
        self.assertEqual(queries[-1], "SELECT id FROM devices WHERE status='literal-secret'")
        self.assertEqual(self.guard.business_selects, 1)
        self.assertEqual(connection._result.rows, (("secret-result-value",),))
        audit = self.guard.config.audit_path.read_text(encoding="utf-8")
        for secret in ("literal-secret", "secret-business-value", "secret-result-value", "fake-password", "fake-user"):
            self.assertNotIn(secret, audit)
        self.assertTrue(any(row["event"] == "query_plan" for row in self.records()))

    def test_only_exact_database_target_is_accepted(self):
        for kwargs in ({"host": "localhost"}, {"host": "192.168.1.2"}, {"port": 3307},
                       {"database": "mysql"}, {"unix_socket": "local-pipe"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(MODULE.GuardError):
                self.connection(**kwargs)
        self.assertEqual(self.guard.connections, 0)
        self.assertEqual(self.connection_class.log, [])

    def test_write_global_settings_multistatement_and_unsafe_functions_are_rejected(self):
        connection = self.connection()
        count = len(self.connection_class.log)
        statements = ["DELETE FROM devices", "UPDATE devices SET id=1", "SET GLOBAL sql_mode=''",
                      "SET autocommit=1; SELECT * FROM devices", "COMMIT", "SHOW TABLES",
                      "SELECT SLEEP(10) FROM devices", "SELECT GET_LOCK('lock',3) FROM devices",
                      "SELECT id FROM devices INTO OUTFILE 'file'", "SELECT * FROM devices FOR UPDATE",
                      "SELECT @x := 1 FROM devices", "SELECT /*+ MAX_EXECUTION_TIME(5000) */ * FROM devices",
                      "SELECT /*!50000 SLEEP(10) */ id FROM devices"]
        for sql in statements:
            with self.subTest(sql=sql), self.assertRaises(MODULE.GuardError):
                connection.query(sql)
        self.assertEqual(len(self.connection_class.log), count)

    def test_subqueries_cte_and_database_qualifiers_obey_table_scope(self):
        good = ["SELECT * FROM szh_new.devices", "WITH d AS (SELECT * FROM devices) SELECT * FROM d",
                "SELECT * FROM devices WHERE id IN (SELECT id FROM orders)"]
        bad = ["SELECT * FROM other.devices", "SELECT * FROM devices WHERE id IN (SELECT id FROM secrets)",
               "WITH secrets AS (SELECT * FROM secrets) SELECT * FROM secrets",
               "WITH RECURSIVE d AS (SELECT * FROM devices) SELECT * FROM d"]
        for sql in good:
            with self.subTest(sql=sql):
                self.assertTrue(self.guard.inspect_select(sql))
        for sql in bad:
            with self.subTest(sql=sql), self.assertRaises(MODULE.GuardError):
                self.guard.inspect_select(sql)

    def test_large_or_unverifiable_plan_never_executes_business_sql(self):
        connection = self.connection()
        for table in ({"table_name": "devices", "access_type": "ALL", "rows_examined_per_scan": 51},
                      {"table_name": "devices", "access_type": "ref", "rows_examined_per_scan": 3001},
                      {"table_name": "devices", "access_type": "ref"}):
            self.connection_class.plan = {"query_block": {"table": table}}
            with self.subTest(table=table), self.assertRaises(MODULE.GuardError):
                connection.query("SELECT * FROM devices")
        self.assertFalse(any(sql == "SELECT * FROM devices" for _, sql in self.connection_class.log))

    def test_full_scan_up_to_fifty_and_index_scan_up_to_three_thousand_are_accepted(self):
        connection = self.connection()
        for access, rows in (("ALL", 50), ("ref", 3000)):
            self.connection_class.plan = {"query_block": {"table": {
                "table_name": "devices", "access_type": access, "rows_examined_per_scan": rows}}}
            connection.query("SELECT * FROM devices")
        self.assertEqual(self.guard.business_selects, 2)

    def test_select_connection_and_command_budgets_are_enforced(self):
        connection = self.connection()
        for _ in range(6):
            connection.query("SELECT * FROM devices")
        with self.assertRaisesRegex(MODULE.GuardError, "select_budget"):
            connection.query("SELECT * FROM devices")
        for _ in range(11):
            self.connection()
        with self.assertRaisesRegex(MODULE.GuardError, "connection_budget"):
            self.connection()
        self.guard.commands = self.guard.MAX_COMMANDS - self.guard.DIAGNOSTIC_RESERVE
        with self.assertRaisesRegex(MODULE.GuardError, "command_budget"):
            connection.query("SELECT 1")
        self.assertEqual(self.guard.connections, 12)
        self.assertEqual(self.guard.business_selects, 6)

    def test_reviewed_four_table_join_is_explicit_exact_and_single_use(self):
        plan = {"query_block": {"nested_loop": [
            {"table": {"table_name": alias, "access_type": access, "rows_examined_per_scan": rows}}
            for alias, access, rows in (("d", "const", 1), ("i", "ALL", 1606),
                                        ("p", "eq_ref", 1), ("r", "ALL", 46))]}}
        raw = json.dumps(plan)
        tables = list(self.guard.REVIEWED_TABLES)
        with patch.dict(os.environ, {"LIVE_REVIEWED_SMALL_JOIN": "0"}):
            with self.assertRaisesRegex(MODULE.GuardError, "scan_budget"):
                self.guard.inspect_plan(raw, tables)
        with patch.dict(os.environ, {"LIVE_REVIEWED_SMALL_JOIN": "1"}):
            with self.assertRaisesRegex(MODULE.GuardError, "scan_budget"):
                self.guard.inspect_plan(raw, ["devices"])
            wrong = json.loads(raw)
            wrong["query_block"]["nested_loop"][0]["table"]["table_name"] = "wrong"
            with self.assertRaisesRegex(MODULE.GuardError, "scan_budget"):
                self.guard.inspect_plan(json.dumps(wrong), tables)
            large = json.loads(raw)
            large["query_block"]["nested_loop"][1]["table"]["rows_examined_per_scan"] = 2001
            with self.assertRaisesRegex(MODULE.GuardError, "scan_budget"):
                self.guard.inspect_plan(json.dumps(large), tables)
            self.assertEqual(len(self.guard.inspect_plan(raw, tables)), 4)
            with self.assertRaisesRegex(MODULE.GuardError, "single_execution"):
                self.guard.inspect_plan(raw, tables)
        self.assertEqual(self.guard.reviewed_join_executions, 1)

    def test_initialization_controls_and_application_timeout_are_bounded(self):
        connection = self.connection()
        for sql in ("SELECT VERSION()", "SELECT DATABASE()", "SELECT @@sql_mode", "SELECT @@lower_case_table_names",
                    "SET TRANSACTION READ ONLY", "START TRANSACTION READ ONLY", "ROLLBACK",
                    "SET SESSION MAX_EXECUTION_TIME=15000"):
            connection.query(sql)
        self.assertEqual(self.connection_class.log[-1][1], "SET SESSION MAX_EXECUTION_TIME=3000")
        self.assertEqual(self.guard.business_selects, 0)
        self.assertEqual((connection.connect_timeout, connection._read_timeout, connection._write_timeout), (3, 3, 3))
        with self.assertRaises(MODULE.GuardError):
            connection.connect()
        with self.assertRaises(MODULE.GuardError):
            connection.query("SELECT * FROM devices", unbuffered=True)

    def test_disconnect_stops_all_future_database_work_and_diagnoses_only_owned_ids(self):
        connection = self.connection()
        self.connection_class.diagnostic_rows = ((101, "Query", 4), (999, "Query", 1))
        self.connection_class.failure = FakeDatabaseError(2013, "fake-password and sensitive raw SQL")
        with self.assertRaises(FakeDatabaseError):
            connection.query("SELECT * FROM devices")
        self.assertTrue(self.guard.config.stop_path.exists())
        self.assertEqual(self.guard.diagnostic_connections, 1)
        queries = [sql for _, sql in self.connection_class.log]
        self.assertIn("KILL QUERY 101", queries)
        self.assertNotIn("KILL QUERY 999", queries)
        count = len(queries)
        for operation in (lambda: connection.query("SELECT 1"), self.connection,
                          lambda: connection.ping(reconnect=True)):
            with self.assertRaises(MODULE.GuardError):
                operation()
        self.assertEqual(len(self.connection_class.log), count)
        self.assertNotIn("sensitive raw SQL", self.guard.config.audit_path.read_text(encoding="utf-8"))

    def test_resource_error_stops_without_retry(self):
        """排序内存等资源故障同样必须终止本轮，不等待下次查询才发现。"""
        connection = self.connection()
        self.connection_class.failure = FakeDatabaseError(1038, "sort memory")
        with self.assertRaises(FakeDatabaseError):
            connection.query("SELECT * FROM devices")
        self.assertTrue(self.guard.config.stop_path.exists())
        with self.assertRaises(MODULE.GuardError):
            connection.query("SELECT * FROM devices")

    def test_reviewed_single_table_scan_does_not_expand_other_tables(self):
        """只放行当前审阅表及行数预算，不改变未知表的扫描限制。"""
        plan = json.dumps({"query_block": {"table": {"table_name": "d", "access_type": "ALL", "rows_examined_per_scan": 113}}})
        with self.assertRaises(MODULE.GuardError):
            self.guard.inspect_plan(plan, ["devices"])
        self.guard.reviewed_scan_limits = {"devices": 150}
        self.assertEqual(self.guard.inspect_plan(plan, ["devices"])[0]["rows"], 113)
        with self.assertRaises(MODULE.GuardError):
            self.guard.inspect_plan(plan, ["orders"])
        with self.assertRaises(MODULE.GuardError):
            self.guard.inspect_plan(plan.replace("113", "151"), ["devices"])

    def test_absolute_deadline_stops_even_a_stream_of_fast_packets(self):
        connection = self.connection()
        self.guard.QUERY_SECONDS = 0.04
        self.connection_class.delay = 0.07
        with self.assertRaises(MODULE.GuardError):
            connection.query("SELECT * FROM devices")
        self.assertTrue(self.guard.stopped.is_set())
        self.assertTrue(self.guard.config.stop_path.exists())
        self.assertEqual(self.guard.diagnostic_connections, 1)

    def test_selects_from_multiple_threads_are_serial(self):
        connections = [self.connection(), self.connection(), self.connection()]
        self.connection_class.delay = 0.015
        failures = []

        def query(connection):
            try:
                connection.query("SELECT * FROM devices")
            except Exception as error:
                failures.append(type(error).__name__)

        threads = [threading.Thread(target=query, args=(connection,)) for connection in connections]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(failures, [])
        self.assertEqual(self.connection_class.peak, 1)

    def test_network_policy_preserves_numeric_loopback_except_active_ports_and_raw_mysql(self):
        for address in (("127.0.0.1", 3307), ("::1", 8900)):
            self.guard.check_address(address)
        for address in (("127.0.0.1", 8011), ("127.0.0.1", 8012), ("127.0.0.1", 3306),
                        ("192.168.1.8", 80), ("example.invalid", 80), ("localhost", 80), "local-pipe"):
            with self.subTest(address=address), self.assertRaises(MODULE.GuardError):
                self.guard.check_address(address)

    def test_sqlite_paths_and_environment_fail_closed(self):
        MODULE.temporary_sqlite_path(":memory:")
        MODULE.temporary_sqlite_path(self.directory / "platform.sqlite")
        with self.assertRaises(MODULE.GuardError):
            MODULE.temporary_sqlite_path(Path.cwd() / "storage" / "platform.sqlite")
        env = {"LIVE_ALLOWED_TABLES": '["devices"]', "LIVE_DB_AUDIT_PATH": str(self.directory / "audit.jsonl"),
               "LIVE_DB_STOP_PATH": str(self.directory / "STOP.json"),
               "DATABASE_URL": "sqlite:///" + (self.directory / "platform.sqlite").as_posix()}
        with patch.dict(os.environ, env):
            self.assertEqual(MODULE.GuardConfig.from_env().allowed_tables, frozenset({"devices"}))
        with patch.dict(os.environ, {**env, "DATABASE_URL": "mysql+pymysql://hidden"}):
            with self.assertRaises(MODULE.GuardError):
                MODULE.GuardConfig.from_env()

    def test_activated_subprocess_installs_without_opening_database(self):
        env = os.environ.copy()
        env.update(LIVE_DSH_ACCEPTANCE="1", LIVE_ALLOWED_TABLES='["devices"]',
                   LIVE_DB_AUDIT_PATH=str(self.directory / "startup.jsonl"),
                   LIVE_DB_STOP_PATH=str(self.directory / "STOP.json"),
                   DATABASE_URL="sqlite:///:memory:", PYTHONPATH=str(GUARD_PATH.parent))
        script = """
import os, socket, sqlite3, pymysql, psycopg
from sitecustomize import GuardError
assert os.environ['LIVE_DB_GUARD_READY'] == '1'
assert sqlite3.connect(':memory:').execute('SELECT 1').fetchone() == (1,)
a, b = socket.socketpair()
a.close()
b.close()
assert pymysql.connections.Connection.query.__module__ == 'sitecustomize'
for operation in (
    lambda: pymysql.connect(host='invalid.invalid', database='szh_new'),
    lambda: pymysql.Connection(host='127.0.0.1', database='other'),
    lambda: pymysql.connections.Connection(host='127.0.0.1', port=3307, database='szh_new'),
    lambda: psycopg.connect(''),
    lambda: psycopg.Connection.connect(''),
    lambda: psycopg.AsyncConnection.connect(''),
    lambda: socket.getaddrinfo('external.invalid', 443),
    lambda: socket.gethostbyname('external.invalid'),
    lambda: socket.gethostbyaddr('192.168.1.5'),
):
    try:
        operation()
    except GuardError:
        pass
    else:
        raise AssertionError('expected pre-network rejection')
print('guard-ready')
"""
        result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "guard-ready")
        records = [json.loads(line) for line in (self.directory / "startup.jsonl").read_text().splitlines()]
        self.assertEqual([row["event"] for row in records], ["guard_ready"])

    def test_invalid_activated_configuration_exits_before_application_code(self):
        env = os.environ.copy()
        env.update(LIVE_DSH_ACCEPTANCE="1", LIVE_ALLOWED_TABLES="[]", PYTHONPATH=str(GUARD_PATH.parent))
        result = subprocess.run([sys.executable, "-c", "print('application-should-not-run')"], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 78)
        self.assertNotIn("application-should-not-run", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
