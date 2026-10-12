"""One bounded read-only local szh_new probe, with no deployment DB access.

Default: inspect a primary-key query plan only. --execute also runs exactly one
bounded business SELECT through the application's real result adapter. No model,
saved datasource, workflow or global database setting is changed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".implementation/local_szh_acceptance"
STOP = OUT / "STOP_AFTER_DATABASE_ERROR.json"


def api_acceptance(report, settings):
    """Seed only an ephemeral platform; execute the real authorized HTTP path."""
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.db import get_engine, new_session
    from app.ids import new_id
    from app.main import create_app
    from app.models import DataSource, Run
    from app.modules.analytics.models import QueryHistory, SourceMetadata
    from app.modules.analytics.connection_pool import dispose_read_pools
    from app.modules.secret.service import create_secret
    assert get_engine().url.get_backend_name() == "sqlite"
    sample = report["sample"]
    table, column = sample["table"], sample["column"]
    metadata = [{"tableName": table, "columns": [{"columnName": column, "dataType": sample["dataType"], "primaryKey": True}]}]
    try:
        report["phase"] = "isolated_api_bootstrap"
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            report["phase"] = "isolated_login"
            logged = client.post("/api/auth/login", json={"username": "workspace-admin", "password": settings.BOOTSTRAP_ADMIN_PASSWORD})
            assert logged.status_code == 200, "Isolated login failed"
            identity = logged.json()
            workspace_id = identity["workspaces"][0]["id"]
            headers = {"Authorization": "Bearer " + identity["token"], "X-Workspace-Id": workspace_id}
            user_id = client.get("/api/auth/me", headers=headers).json()["user"]["id"]
            source_id = new_id()
            with new_session() as db:
                secret = create_secret(db, workspace_id=workspace_id, owner_user_id=user_id,
                                       name="isolated-local-read", value=settings.MYSQL_PASSWORD, provider="mysql")
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                source = DataSource(id=source_id, workspace_id=workspace_id, owner_user_id=user_id,
                    name="本机限量只读验收", provider="mysql", status="active", visibility="workspace",
                    secret_id=secret.id, created_at=now, updated_at=now, connection_config={
                        "version": 1, "host": "127.0.0.1", "port": 3306, "databaseName": "szh_new",
                        "username": settings.MYSQL_USER, "timeoutSeconds": 3, "maxRows": 1,
                        "allowedTables": [table], "allowedColumns": {table: [column]},
                    })
                db.add(source)
                db.flush()
                db.add(SourceMetadata(workspace_id=workspace_id, owner_user_id=user_id, datasource_id=source.id,
                    source_version=1, status="ready", tables_json=metadata))
                db.commit()
            connections_before = report["connections"]
            report["phase"] = "api_rejection_boundaries"
            denied = client.post("/api/v1/analytics/query", headers=headers, json={
                "datasourceId": source_id, "query": "SELECT * FROM company_agent.users", "mode": "sql", "maxRows": 1,
            })
            assert denied.status_code == 422 and report["connections"] == connections_before
            unauthenticated = client.post("/api/v1/analytics/query", json={
                "datasourceId": source_id, "query": sample["sql"], "mode": "sql", "maxRows": 1,
            })
            assert unauthenticated.status_code == 401 and report["connections"] == connections_before
            report["businessSelects"] += 1
            report["phase"] = "api_business_read"
            response = client.post("/api/v1/analytics/query", headers=headers, json={
                "datasourceId": source_id, "query": sample["sql"], "mode": "sql", "maxRows": 1,
                "needAnalysis": False, "needChart": False,
            })
            assert response.status_code == 200, "Read API did not succeed; inspect sanitized command evidence"
            result = response.json()["data"]
            assert result["totalCount"] == len(result["data"]) == 1 and result["truncated"] is False
            assert result["columns"][0]["columnName"] == "sample_key"
            with new_session() as db:
                history = db.get(QueryHistory, result["queryId"])
                run = db.get(Run, result["runId"])
                assert history.status == "success" and run.status == "completed"
            before_history = report["connections"]
            report["phase"] = "api_history_restore"
            restored = client.get("/api/v1/analytics/history/" + result["queryId"], headers=headers)
            assert restored.status_code == 200 and restored.json()["data"]["data"] == result["data"]
            assert report["connections"] == before_history
            report["apiResult"] = {"statusCode": response.status_code, "rowCount": result["totalCount"],
                "columns": result["columns"], "truncated": result["truncated"], "historyStatus": "success",
                "runStatus": "completed", "historyRestoredWithoutQuery": True, "crossDatabaseRejectedWithoutConnection": True,
                "unauthenticatedRejectedWithoutConnection": True, "valuesRecorded": False,
                "platform": "temporary SQLite; encrypted credentials and all sample results removed on exit",
                "metadata": "single-column metadata from the verified local plan; no automatic full synchronization"}
    finally:
        dispose_read_pools()
        get_engine().dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--api", action="store_true", help="One real query through an isolated platform HTTP API")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if STOP.exists():
        raise SystemExit("Previous database timeout/disconnect requires review and explicit authorization before further testing")
    if args.api and args.execute:
        parser.error("Choose either adapter execution or API execution, not both")
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "mode": "api" if args.api else "execute" if args.execute else "plan",
              "budget": {"testConnections": 2 if args.api else 1, "diagnosticConnectionsOnFailure": 1,
                         "commands": 32 if args.api else 20, "businessSelects": int(args.execute or args.api), "rows": 1,
                         "queryTimeoutSeconds": 3, "totalSeconds": 20},
              "connections": 0, "commands": [], "businessSelects": 0, "status": "not_started"}
    verification = subprocess.run(["pwsh", "-NoProfile", "-File", str(ROOT / "scripts/verify_local_mysql.ps1")],
                                  capture_output=True, text=True, check=True, timeout=10)
    report["target"] = json.loads(verification.stdout)
    platform_dir = None
    if args.api:
        platform_dir = tempfile.TemporaryDirectory(prefix="test5-local-szh-api-")
        os.environ.update(DATABASE_URL="sqlite:///" + (Path(platform_dir.name) / "platform.sqlite3").as_posix(),
                          STORAGE_ROOT=str(Path(platform_dir.name) / "storage"), APP_ENV="test", AUTO_MIGRATE="1",
                          SCHEDULER_ENABLED="0", BOOTSTRAP_LYNX_REPORTS="0",
                          SECRET_MASTER_KEY=secrets.token_urlsafe(48), BOOTSTRAP_ADMIN_PASSWORD=secrets.token_urlsafe(24))
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import settings
    if settings.MYSQL_HOST not in {"127.0.0.1", "localhost", "::1"} or int(settings.MYSQL_PORT) != 3306:
        raise SystemExit("Configured credentials target is not the verified local service; no connection attempted")
    import pymysql
    original_connect = pymysql.connect
    connection_type = pymysql.connections.Connection
    original_query = connection_type.query
    original_init = connection_type.__init__
    started = time.monotonic()
    connection_ids = []
    socket_connect = socket.socket.connect
    socket_connect_ex = socket.socket.connect_ex
    socketpair = socket.socketpair
    internal_pipe = threading.local()
    def check_address(address):
        # On Windows asyncio's internal socketpair uses an ephemeral loopback
        # TCP connection. Permit only that guarded construction, not arbitrary
        # loopback HTTP services or database proxies.
        if getattr(internal_pipe, "building", False) and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return
        if not isinstance(address, tuple) or address[:2] != ("127.0.0.1", 3306):
            raise AssertionError("Only the verified local database network endpoint is permitted")
    def make_internal_pair(*arguments, **options):
        internal_pipe.building = True
        try:
            return socketpair(*arguments, **options)
        finally:
            internal_pipe.building = False
    def local_only(sock, address):
        check_address(address)
        return socket_connect(sock, address)
    def local_only_ex(sock, address):
        check_address(address)
        return socket_connect_ex(sock, address)
    socket.socket.connect = local_only
    socket.socket.connect_ex = local_only_ex
    socket.socketpair = make_internal_pair

    def note_database_error(error):
        code = error.args[0] if error.args and isinstance(error.args[0], int) else None
        failure = {"type": type(error).__name__, "code": code}
        report.setdefault("databaseFailures", []).append(failure)
        if code in {2006, 2013, 2014, 3024, 1205} or isinstance(error, (TimeoutError, ConnectionError)) or "timed out" in str(error).lower():
            STOP.write_text(json.dumps({"at": report["startedAt"], "failure": failure,
                                       "connectionIds": connection_ids}, indent=2), encoding="utf-8")

    def guarded_init(connection, *arguments, **options):
        if arguments or options.get("host") != "127.0.0.1" or options.get("port", 3306) != 3306 or options.get("database", options.get("db")) != "szh_new":
            raise AssertionError("Only verified 127.0.0.1:3306/szh_new is permitted")
        if report["connections"] >= report["budget"]["testConnections"]:
            raise AssertionError("Connection budget exhausted; retries are forbidden")
        report["connections"] += 1
        try:
            original_init(connection, **options)
        except Exception as error:
            note_database_error(error)
            raise
        connection_ids.append(connection.thread_id())

    def measured_query(connection, sql, *arguments, **options):
        if len(report["commands"]) >= report["budget"]["commands"] or time.monotonic() - started >= 20:
            raise AssertionError("Probe workload budget exhausted")
        statement = sql.decode("utf-8", "replace") if isinstance(sql, bytes) else sql
        event = {"number": len(report["commands"]) + 1, "verb": statement.split()[0].upper(), "status": "started"}
        report["commands"].append(event)
        before = time.monotonic()
        try:
            value = original_query(connection, sql, *arguments, **options)
            event["status"] = "ok"
            return value
        except Exception as error:
            event["status"] = "failed"
            note_database_error(error)
            raise
        finally:
            event["milliseconds"] = round((time.monotonic() - before) * 1000, 2)

    connection_type.__init__ = guarded_init
    connection_type.query = measured_query
    try:
        from app.models import DataSource
        from app.modules.analytics.connections import readonly_connection
        from app.modules.analytics import execution
        from app.modules.analytics.sql_safety import authorize_sql, driver_query
        source = DataSource(workspace_id="isolated-local-read", provider="mysql", status="preview", connection_config={
            "host": "127.0.0.1", "port": 3306, "databaseName": "szh_new",
            "username": settings.MYSQL_USER, "timeoutSeconds": 3, "maxRows": 1,
        })
        with readonly_connection(None, source, password_override=settings.MYSQL_PASSWORD) as connection:
            identity = connection.exec_driver_sql("SELECT DATABASE(), @@hostname, @@port, @@version").one()
            if identity[0] != "szh_new" or identity[1].casefold() != report["target"]["machine"].casefold() or identity[2] != 3306:
                raise AssertionError("Connected server identity differs from the verified local service")
            report["server"] = {"database": identity[0], "hostname": identity[1], "port": identity[2], "version": identity[3]}
            selected = connection.exec_driver_sql(
                "SELECT t.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, t.TABLE_ROWS "
                "FROM information_schema.TABLES t JOIN information_schema.COLUMNS c "
                "ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME "
                "WHERE t.TABLE_SCHEMA=%s AND t.TABLE_TYPE='BASE TABLE' AND c.COLUMN_KEY='PRI' "
                "AND t.TABLE_ROWS BETWEEN 1 AND 1000 ORDER BY t.TABLE_ROWS,t.TABLE_NAME,c.ORDINAL_POSITION LIMIT 1",
                ("szh_new",),
            ).first()
            if selected is None:
                raise AssertionError("No small primary-key table found; do not expand the workload automatically")
            table, column, datatype, estimate = selected
            quote = lambda identifier: "`" + identifier.replace("`", "``") + "`"
            metadata = [{"tableName": table, "columns": [{"columnName": column, "dataType": datatype, "primaryKey": True}]}]
            query = authorize_sql(f"SELECT {quote(column)} AS sample_key FROM {quote(table)} ORDER BY {quote(column)} LIMIT 1",
                                  provider="mysql", tables=metadata, max_rows=1, database="szh_new")
            sql, parameters = driver_query(query, "mysql")
            plan = [dict(row) for row in connection.exec_driver_sql("EXPLAIN " + sql, parameters).mappings().all()]
            report["sample"] = {"table": table, "column": column, "dataType": datatype, "estimatedTableRows": estimate,
                                "sql": sql, "plan": plan}
            if not plan or any((row.get("rows") or 0) > 1000 or row.get("type") == "ALL" for row in plan):
                raise AssertionError("Plan exceeds the small indexed-read budget; no business query executed")
            report["status"] = "plan_passed"
            if args.execute:
                @contextmanager
                def shared_connection(*_arguments, **_options):
                    yield connection
                previous = execution.readonly_connection
                execution.readonly_connection = shared_connection
                try:
                    report["businessSelects"] += 1
                    rows, columns, truncated = execution.execute_read(None, source, query, metadata)
                finally:
                    execution.readonly_connection = previous
                assert len(rows) <= 1 and not truncated
                assert [item["columnName"] for item in columns] == ["sample_key"]
                report["result"] = {"rowCount": len(rows), "columns": columns, "truncated": truncated,
                                    "valuesRecorded": False, "adapter": "analytics.execution.execute_read"}
                report["status"] = "passed"
        if args.api:
            api_acceptance(report, settings)
            report["status"] = "passed"
    except Exception as exc:
        # Never print provider messages: they may contain a username or SQL.
        report["status"] = "failed"
        chain, error = [], exc
        while error is not None and len(chain) < 8:
            code = error.args[0] if error.args and isinstance(error.args[0], int) else None
            chain.append({"type": type(error).__name__, "code": code})
            error = error.__cause__ or error.__context__
        report["failure"] = chain
        if any(item["code"] in {2006, 2013, 2014, 3024, 1205} or item["type"] in {"TimeoutError", "TimeoutException"} for item in chain):
            STOP.write_text(json.dumps({"at": report["startedAt"], "failure": chain, "connectionIds": connection_ids}, indent=2), encoding="utf-8")
    finally:
        connection_type.__init__ = original_init
        connection_type.query = original_query
        socket.socket.connect = socket_connect
        socket.socket.connect_ex = socket_connect_ex
        socket.socketpair = socketpair
        report["elapsedSeconds"] = round(time.monotonic() - started, 3)
        # A failed connection is closed by readonly_connection. Check only its
        # thread IDs; this reserved diagnostic never repeats the business query.
        if report["status"] == "failed" and connection_ids:
            try:
                with original_connect(host="127.0.0.1", port=3306, database="szh_new", user=settings.MYSQL_USER,
                                      password=settings.MYSQL_PASSWORD, connect_timeout=3, read_timeout=3,
                                      write_timeout=3, autocommit=True, local_infile=False) as diagnostic:
                    with diagnostic.cursor() as cursor:
                        condition = "SELECT ID,COMMAND,STATE FROM information_schema.PROCESSLIST WHERE ID IN (" + ",".join(["%s"] * len(connection_ids)) + ")"
                        cursor.execute(condition, tuple(connection_ids))
                        remaining = cursor.fetchall()
                        report["diagnostic"] = {"connections": 1, "queries": 1, "remaining": remaining}
                        active = [row[0] for row in remaining if row[1] != "Sleep"]
                        if active:
                            for identifier in active:
                                cursor.execute("KILL QUERY " + str(int(identifier)))
                            cursor.execute(condition, tuple(connection_ids))
                            report["diagnostic"].update(queries=2 + len(active), remaining=cursor.fetchall())
            except Exception as diagnostic_error:
                report["diagnostic"] = {"errorType": type(diagnostic_error).__name__, "remainingUnverified": True}
        if platform_dir:
            platform_dir.cleanup()
        output = OUT / ("api.json" if args.api else "execute.json" if args.execute else "plan.json")
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(output), "connections": report["connections"],
                          "commands": len(report["commands"]), "businessSelects": report["businessSelects"],
                          "elapsedSeconds": report["elapsedSeconds"]}, ensure_ascii=False))
    return int(report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
