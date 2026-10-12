"""Six serial reads on verified local szh_new; no retries or remote targets."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest.mock import patch

from benchmark_local_szh import DATABASE, HOST, PORT, Probe, available_memory, local_listener, require_local

ROOT = Path(__file__).resolve().parents[1]


def main():
    """验证本地只读连接池行为：一个物理连接、两次 checkout/checkin、串行 3 条业务查询且结果一致。

    参数：--output-dir（必填，目录必须不存在，避免覆盖历史证据）；结果写入该目录的 verification.json。
    前置条件：仅本机 szh_new（校验监听地址、@@hostname、datadir 盘符与库名），
    凭证只从平台配置读取并在本进程内注入，不新建/修改数据源。
    约束：禁止重连（第二次连接尝试即失败）、单语句超过 2 秒即中止、总预算 30 秒、语句上限 64 条、
    结果行数上限 20，并带内存/磁盘下限看门狗；EXPLAIN 估算扫描超过 2000 行直接停止。
    副作用：只读（EXPLAIN + SELECT），失败时会取消本进程自己的连接、绝不改动业务数据。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"target": {"host": HOST, "port": PORT, "database": DATABASE}, "status": "not_started",
              "connectionAttempts": 0, "physicalConnections": 0, "checkouts": 0, "checkins": 0,
              "businessQueries": 0, "statements": [], "concurrency": 1, "budgetSeconds": 30}
    deadline = time.monotonic() + 30
    watchdog = Probe(out)
    active = threading.Event()
    label = "driver_initialization"

    def save():
        (out / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def guard():
        if watchdog.failed or time.monotonic() >= deadline:
            raise RuntimeError("Stopped: previous failure or elapsed budget")
        if available_memory() < 768 * 1024**2 or shutil.disk_usage(ROOT.anchor).free < 3 * 1024**3:
            raise RuntimeError("Stopped: local resource floor")
        if len(report["statements"]) >= 64:
            raise RuntimeError("Stopped: statement budget")

    sys.path.insert(0, str(ROOT / "backend"))
    import pymysql
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    from app.config import settings
    from app.models import DataSource
    from app.modules.analytics import connections
    from app.modules.analytics.connection_pool import read_batch, pool_status, dispose_read_pools

    class AuditedCursor(pymysql.cursors.Cursor):
        def execute(self, sql, parameters=None):
            guard()
            entry = {"label": label, "sqlSha256": hashlib.sha256(sql.encode()).hexdigest(), "status": "started"}
            report["statements"].append(entry)
            save()
            started = time.monotonic()
            try:
                result = super().execute(sql, parameters)
                if time.monotonic() - started > 2:
                    raise RuntimeError("Statement exceeded stop threshold")
                entry.update(status="ok", durationMs=round((time.monotonic() - started) * 1000, 3))
                return result
            except BaseException as exc:
                watchdog.failed = True
                entry.update(status="failed", errorType=type(exc).__name__)
                raise
            finally:
                save()

    try:
        report["listener"] = local_listener()
        guard()
        url = make_url(settings.DATABASE_URL)
        require_local(url.host, url.port or 3306, DATABASE)
        if url.get_backend_name() != "mysql":
            raise RuntimeError("Local credential source must be MySQL")
        watchdog.credentials = (url.username, url.password)
        source = DataSource(id="local-pool-verification", workspace_id="local-verification", provider="mysql", status="active",
                            connection_config={"host": HOST, "port": PORT, "databaseName": DATABASE,
                                               "username": url.username, "timeoutSeconds": 2})
        # Inject only this process's local credentials, without creating/updating a source.
        def password(_db, row):
            if row is not source:
                raise RuntimeError("Unexpected data source")
            require_local(row.connection_config["host"], row.connection_config["port"], row.connection_config["databaseName"])
            return url.password or ""

        with patch.object(connections, "connection_password", password), connections.sql_engine(None, source) as engine:
            @event.listens_for(engine, "do_connect", retval=True)
            def connect(_dialect, _record, positional, keywords):
                guard()
                require_local(keywords.get("host"), keywords.get("port"), keywords.get("database"))
                report["connectionAttempts"] += 1
                # Pre-ping is allowed; reconnect/retry in this verification is not.
                if report["connectionAttempts"] > 1:
                    raise RuntimeError("Stopped: reconnect is forbidden during this verification")
                connection = pymysql.connect(*positional, **{**keywords, "cursorclass": AuditedCursor})
                report["physicalConnections"] += 1
                watchdog.connection_id = connection.thread_id()
                if connection._sock.getpeername()[0] != HOST:
                    connection.close()
                    raise RuntimeError("Unexpected socket peer")
                report["connectionId"] = watchdog.connection_id
                return connection

            @event.listens_for(engine, "checkout")
            def checkout(*_args):
                report["checkouts"] += 1

            @event.listens_for(engine, "checkin")
            def checkin(*_args):
                report["checkins"] += 1

            def fetch(connection, sql, parameters=()):
                guard()
                active.set()
                timer = threading.Timer(2, watchdog.cancel, args=(active, len(report["statements"]) + 1))
                timer.start()
                try:
                    with connection.exec_driver_sql(sql, parameters) as cursor:
                        rows = [dict(row) for row in cursor.mappings().fetchmany(21)]
                    if len(rows) > 20 or watchdog.failed:
                        raise RuntimeError("Stopped: row cap or query watchdog")
                    return rows
                finally:
                    active.clear()
                    timer.cancel()
                    timer.join()

            queries = [
                ("device", "SELECT deviceManagementId,c_deviceName FROM t_devicemanagement WHERE deviceManagementId=%s AND isDeleted=0 LIMIT 5",
                 ("0001567-1080e120bada4139bac3a9e29443b539",)),
                ("quality_template", "SELECT qualityInspectionTemplateId,c_templateName FROM t_qualityinspectiontemplate WHERE qualityInspectionTemplateId=%s AND isDeleted=0 LIMIT 5",
                 ("0001328-d73088c806e744b4823f016db00af4f1",)),
                ("quality_requirements", "SELECT qITemplateDetailsId,c_judgeRule,c_standardMin,c_standardMax FROM t_qitemplatedetails WHERE mdQualityInspectionTemplateId=%s AND isDeleted=0 LIMIT 10",
                 ("0001328-d73088c806e744b4823f016db00af4f1",)),
            ]
            outputs = []
            with read_batch():
                with connections.readonly_connection(None, source) as connection:
                    label = "identity"
                    identity = fetch(connection, "SELECT @@hostname AS hostname,@@version AS version,@@datadir AS datadir,DATABASE() AS db")[0]
                    if identity["hostname"].lower() != os.environ["COMPUTERNAME"].lower() or identity["db"] != DATABASE:
                        raise RuntimeError("Database identity is not local szh_new")
                    if Path(identity["datadir"]).drive.lower() != ROOT.drive.lower():
                        raise RuntimeError("Unexpected local data directory")
                    report["identity"] = identity
                    tables = fetch(connection, "SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('t_devicemanagement','t_qualityinspectiontemplate','t_qitemplatedetails')")
                    if len(tables) != 3 or any(t["ENGINE"] != "InnoDB" or t["TABLE_TYPE"] != "BASE TABLE" for t in tables):
                        raise RuntimeError("Only verified local InnoDB base tables are permitted")
                for name, sql, parameters in queries:
                    label = "transaction_setup"
                    with connections.readonly_connection(None, source) as connection:
                        label = "explain_" + name
                        plan = fetch(connection, "EXPLAIN " + sql, parameters)
                        if any(int(row.get("rows") or 0) > 2000 for row in plan):
                            raise RuntimeError("Estimated scan exceeds small verification scope")
                        label = "business_" + name
                        outputs.append(fetch(connection, sql, parameters))
                        report["businessQueries"] += 1
                report["firstBatchCheckouts"] = report["checkouts"]
            assert report["checkouts"] == report["checkins"] == 1
            with read_batch():
                for index, (name, sql, parameters) in enumerate(queries):
                    label = "transaction_setup"
                    with connections.readonly_connection(None, source) as connection:
                        label = "business_" + name
                        rows = fetch(connection, sql, parameters)
                        report["businessQueries"] += 1
                        if Counter(json.dumps(r, sort_keys=True, default=str) for r in rows) != Counter(json.dumps(r, sort_keys=True, default=str) for r in outputs[index]):
                            raise RuntimeError("Repeated read result differs")
            report["returnedRows"] = [len(rows) for rows in outputs]
            report["poolAfterReturn"] = pool_status(source)
            assert report["physicalConnections"] == 1 and report["checkouts"] == report["checkins"] == 2
            report["status"] = "passed"
    except BaseException as exc:
        report.update(status="stopped", errorType=type(exc).__name__)
        # Once connected, cancellation targets only our own recorded connection ID.
        if watchdog.connection_id and watchdog.failed and not watchdog.report["cancellations"]:
            active.set()
            watchdog.cancel(active, len(report["statements"]))
            active.clear()
        raise
    finally:
        dispose_read_pools()
        report["cancellations"] = watchdog.report["cancellations"]
        report["cancellationConnectionAttempts"] = watchdog.report["connectionAttempts"]
        save()
    print(json.dumps({key: report[key] for key in ("status", "physicalConnections", "checkouts", "checkins", "businessQueries", "returnedRows")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
