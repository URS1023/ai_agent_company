"""生成本机 szh_new 单场景只读基准；导入模块不会连接数据库。"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STOP = ROOT / ".implementation/local_szh_acceptance/STOP_AFTER_DATABASE_ERROR.json"
HOST, PORT, DATABASE = "127.0.0.1", 3306, "szh_new"
REQUIRED_COLUMNS = {
    "t_devicemanagement": {"deviceManagementId", "c_deviceName", "isDeleted"},
    "t_inspectiondevice": {"c_device", "mdDInspectionMaintenancePlanId", "isDeleted"},
    "t_dinspectionmaintenanceplan": {
        "dInspectionMaintenancePlanId", "c_planName", "c_jihualeixing", "isDeleted",
    },
    "t_xunjianjihuarunhuabaoyang": {
        "xunjianjihuarunhuabaoyangId", "mdDInspectionMaintenancePlanId", "isDeleted",
        "c_runhuadianweimingcheng", "c_runhuazhouqi", "c_runhuayoupaihao",
        "c_runhualiang", "c_runhuafangshi",
    },
}
SQL = """SELECT /*+ MAX_EXECUTION_TIME(3000) */
    d.deviceManagementId AS device_id,
    d.c_deviceName AS device_name,
    p.dInspectionMaintenancePlanId AS plan_id,
    p.c_planName AS plan_name,
    r.xunjianjihuarunhuabaoyangId AS requirement_id,
    r.c_runhuadianweimingcheng AS point_name,
    r.c_runhuazhouqi AS cycle_days,
    r.c_runhuayoupaihao AS required_oil,
    r.c_runhualiang AS required_amount,
    r.c_runhuafangshi AS required_method
FROM t_devicemanagement AS d
JOIN t_inspectiondevice AS i
  ON i.c_device = d.deviceManagementId AND i.isDeleted = 0
JOIN t_dinspectionmaintenanceplan AS p
  ON p.dInspectionMaintenancePlanId = i.mdDInspectionMaintenancePlanId
 AND p.isDeleted = 0 AND p.c_jihualeixing = 2
JOIN t_xunjianjihuarunhuabaoyang AS r
  ON r.mdDInspectionMaintenancePlanId = p.dInspectionMaintenancePlanId
 AND r.isDeleted = 0
WHERE d.deviceManagementId = :device_id AND d.isDeleted = 0
ORDER BY p.dInspectionMaintenancePlanId, r.xunjianjihuarunhuabaoyangId
LIMIT 10"""
DRIVER_SQL = SQL.replace(":device_id", "%(device_id)s")


class BaselineStopped(RuntimeError):
    """基准已停止，后续测试必须先审查 STOP 记录。"""


def _json_value(value: Any) -> Any:
    """保留空值和小数精度，使原始 SQL 与接口结果可独立比较。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    """私有目录保存证据，不在终端打印业务明细或凭据。"""
    path.write_text(json.dumps(_json_value(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _failure(error: BaseException) -> dict[str, Any]:
    """数据库错误文本可能含连接信息，只保留类型及数字错误码。"""
    code = error.args[0] if error.args and isinstance(error.args[0], int) else None
    return {"type": type(error).__name__, "code": code}


def _database_interrupted(error: BaseException) -> bool:
    """只有实际数据库超时、断连或资源故障才触发全局停止标记。"""
    code = _failure(error)["code"]
    return (isinstance(error, (TimeoutError, ConnectionError))
            or code in {2006, 2013, 2014, 3024, 1205, 1040, 1041, 1037, 1038, 1114, 1206})


def _verify_local_service(timeout: float) -> dict[str, Any]:
    """确认监听进程及 MySQL80 服务归属，拒绝本机代理或隧道。"""
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(ROOT / "scripts/verify_local_mysql.ps1")],
        capture_output=True, text=True, check=True, timeout=timeout,
    )
    target = json.loads(result.stdout)
    if (target.get("host"), target.get("port"), target.get("database")) != (HOST, PORT, DATABASE):
        raise BaselineStopped("本机服务核验目标不一致")
    if str(target.get("listenerProcess", "")).lower() != "mysqld.exe" or not target.get("machine"):
        raise BaselineStopped("未取得本机 MySQL 服务归属证据")
    return target


def _credentials() -> tuple[str, str]:
    """只借用本机登录凭据，绝不读取历史数据源配置作为备用目标。"""
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.config import settings

    if settings.MYSQL_HOST not in {HOST, "localhost", "::1"} or int(settings.MYSQL_PORT) != PORT:
        raise BaselineStopped("当前凭据配置并非已核验的本机端口")
    return settings.MYSQL_USER, settings.MYSQL_PASSWORD


def _open_connection(user: str, password: str, seconds: float, on_command: Any) -> Any:
    """拦截驱动初始化 SQL，一并记录命令次数，不允许驱动自动重连。"""
    import pymysql

    class MeasuredConnection(pymysql.connections.Connection):
        """仅本次连接使用的命令计量器，不修改全局驱动。"""

        def query(self, sql: Any, unbuffered: bool = False) -> Any:
            started, event = on_command(sql)
            try:
                result = super().query(sql, unbuffered=unbuffered)
                event["status"] = "ok"
                return result
            except Exception as error:
                event["status"] = "failed"
                event["failure"] = _failure(error)
                raise
            finally:
                event["milliseconds"] = round((time.monotonic() - started) * 1000, 3)

    return MeasuredConnection(
        host=HOST, port=PORT, database=DATABASE, user=user, password=password,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        connect_timeout=seconds, read_timeout=seconds, write_timeout=seconds,
    )


def _metadata(execute: Any) -> list[dict[str, Any]]:
    """只读取固定四表所需列及主键，不同步全库，也不采样业务行。"""
    conditions, parameters = [], [DATABASE]
    for table, columns in REQUIRED_COLUMNS.items():
        names = sorted(columns)
        placeholders = ",".join(["%s"] * len(names))
        conditions.append(f"(t.TABLE_NAME=%s AND (c.COLUMN_KEY='PRI' OR c.COLUMN_NAME IN ({placeholders})))")
        parameters.extend([table, *names])
    rows = execute(
        "metadata",
        "SELECT t.TABLE_NAME, t.TABLE_TYPE, t.ENGINE, t.TABLE_ROWS, t.TABLE_COMMENT, "
        "c.COLUMN_NAME, c.COLUMN_TYPE, c.COLUMN_COMMENT, c.IS_NULLABLE, c.COLUMN_KEY "
        "FROM information_schema.TABLES t JOIN information_schema.COLUMNS c "
        "ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME "
        "WHERE t.TABLE_SCHEMA=%s AND (" + " OR ".join(conditions) + ") "
        "ORDER BY t.TABLE_NAME,c.ORDINAL_POSITION LIMIT 129",
        tuple(parameters), max_rows=128,
    )
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["TABLE_NAME"]
        if name not in REQUIRED_COLUMNS or row["TABLE_TYPE"] != "BASE TABLE" or row["ENGINE"] != "InnoDB":
            raise BaselineStopped("基准只接受固定四张 InnoDB 业务表")
        table = by_name.setdefault(name, {
            "tableName": name, "tableComment": row.get("TABLE_COMMENT") or "", "tableType": "TABLE",
            "rowCount": row.get("TABLE_ROWS"), "rowCountEstimated": True, "columns": [], "foreignKeys": [],
        })
        table["columns"].append({
            "columnName": row["COLUMN_NAME"], "dataType": row["COLUMN_TYPE"],
            "comment": row.get("COLUMN_COMMENT") or "", "nullable": row["IS_NULLABLE"] == "YES",
            "primaryKey": row["COLUMN_KEY"] == "PRI",
        })
    for name, required in REQUIRED_COLUMNS.items():
        present = {column["columnName"] for column in by_name.get(name, {}).get("columns", [])}
        if not required.issubset(present):
            raise BaselineStopped("固定场景所需表列缺失，不扩大元数据读取范围")
    return [by_name[name] for name in REQUIRED_COLUMNS]


def _validate_plan(plan: list[dict[str, Any]], *, accept_reviewed_small_join: bool = False) -> dict[str, Any]:
    """默认拒绝全表扫描；显式开启时仅放行已审阅场景的固定小表扫描。"""
    aliases = {"d", "i", "p", "r"}
    if len(plan) != 4 or {row.get("table") for row in plan} != aliases:
        raise BaselineStopped("执行计划缺少固定四表，可能无此设备；不改查其他设备")
    if accept_reviewed_small_join:
        try:
            strict_result = _validate_plan(plan)
        except BaselineStopped as error:
            strict_result = {"accepted": False, "reason": str(error)}
        scans = {}
        for row in plan:
            alias, estimate = row.get("table"), row.get("rows")
            if estimate is None or int(estimate) < 0:
                raise BaselineStopped("已审阅计划仍要求每个节点给出有效行数估计")
            estimate = int(estimate)
            if row.get("type") == "ALL":
                limit = {"i": 2000, "r": 50}.get(alias)
                if limit is None or estimate > limit:
                    raise BaselineStopped("扫描超出已审阅的 i≤2000、r≤50 范围")
                scans[alias] = estimate
            elif row.get("type") not in {"const", "ref", "eq_ref"} or not row.get("key") or estimate > 1000:
                raise BaselineStopped("已审阅的小表例外之外仍必须使用有界索引查找")
        if sum(scans.values()) > 2100:
            raise BaselineStopped("已审阅表的扫描估计总数超过 2100")
        return {
            "accepted": True, "policy": "explicit_reviewed_small_join", "strict_policy": strict_result,
            "scan_estimates": scans, "scan_total_estimate": sum(scans.values()),
            "scan_limits": {"i": 2000, "r": 50, "total": 2100},
            "reviewed_reference": {
                "path": ".implementation/live_dsh_acceptance/20260926-opencode/baseline/plan.json",
                "observed_rows": {"d": 1, "p": 1, "r": 46, "i": 1606},
                "reason": "主代理审阅首次 EXPLAIN 后，允许固定同一设备、同一 SQL 的两张小关联表扫描；不改索引或扩大场景",
            },
        }
    product = 1
    for row in plan:
        estimate = row.get("rows")
        if estimate is None or int(estimate) < 0 or int(estimate) > 1000:
            raise BaselineStopped("单节点执行计划估计超过 1000 行预算")
        if row.get("type") not in {"system", "const", "eq_ref", "ref", "range"} or not row.get("key"):
            raise BaselineStopped("执行计划未使用小范围索引查找")
        product *= max(1, int(estimate))
        if product > 10000:
            raise BaselineStopped("联接候选行估计超过 10000 行预算")
    return {"accepted": True, "estimated_join_candidates_upper_bound": product, "max_rows_per_node": 1000}


def _diagnose(user: str, password: str, connection_id: int | None) -> dict[str, Any]:
    """失败后只用一个专用连接查看、必要时停止本次查询，绝不重试业务查询。"""
    result: dict[str, Any] = {"connections": 0, "commands": [], "connection_id": connection_id, "remaining_unverified": True}
    if not connection_id:
        result["reason"] = "未取得原连接 ID，禁止查找或停止其他连接"
        return result
    started = time.monotonic()
    connection = None

    def command(sql: Any) -> tuple[float, dict[str, Any]]:
        remaining = 9 - (time.monotonic() - started)
        if remaining <= 0 or len(result["commands"]) >= 7:
            raise BaselineStopped("诊断预算用尽")
        if connection is not None:
            connection._read_timeout = min(3, remaining)
            connection._write_timeout = min(3, remaining)
        event = {"verb": str(sql).split()[0].upper(), "status": "started"}
        result["commands"].append(event)
        return time.monotonic(), event

    try:
        result["connections"] = 1
        connection = _open_connection(user, password, 3, command)
        query = "SELECT ID,COMMAND,TIME,STATE FROM information_schema.PROCESSLIST WHERE ID=%s"
        with connection.cursor() as cursor:
            cursor.execute(query, (connection_id,))
            before = cursor.fetchall()
            result["before"] = before
            if before and before[0]["COMMAND"] != "Sleep":
                cursor.execute("KILL QUERY " + str(int(connection_id)))
                result["killed_query"] = True
            cursor.execute(query, (connection_id,))
            after = cursor.fetchall()
            result["after"] = after
            result["remaining_unverified"] = bool(after and after[0]["COMMAND"] != "Sleep")
    except Exception as error:
        result["failure"] = _failure(error)
    finally:
        if connection is not None:
            connection.close()
        result["milliseconds"] = round((time.monotonic() - started) * 1000, 3)
    return result


def prepare_baseline(output_dir: str | Path, *, device_id: str | None = None,
                     accept_reviewed_small_join: bool = False) -> dict[str, Any]:
    """核验本机目标并生成独立基准；任何失败停止本次执行，数据库异常另存 STOP。"""
    device_id = device_id if device_id is not None else os.environ.get("LIVE_BASELINE_DEVICE_ID")
    if not isinstance(device_id, str) or not device_id.strip():
        raise BaselineStopped("必须显式指定 --device-id 或 LIVE_BASELINE_DEVICE_ID；尚未核验服务或连接数据库")
    device_id = device_id.strip()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if STOP.exists():
        raise BaselineStopped("已有 STOP 记录；必须先处理异常并取得继续授权")
    if (output / "baseline.json").exists():
        raise BaselineStopped("输出目录已有基准，不覆盖或隐式重复执行")
    started = time.monotonic()
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(), "status": "started", "phase": "verify_service",
        "scenario": "指定设备的润滑保养要求（不推断实际完成情况）",
        "target": {"host": HOST, "port": PORT, "database": DATABASE},
        "limits": {"test_connections": 1, "diagnostic_connections_on_failure": 1, "business_selects": 1,
                   "metadata_tables": 4, "business_rows": 10, "metadata_rows": 128, "commands": 16,
                   "sql_timeout_seconds": 3, "test_total_seconds": 20, "failure_diagnostics_seconds": 9},
        "connections": 0, "business_selects": 0, "commands": [], "timings": {},
        "parameters": {"device_id": device_id}, "sql": SQL,
        "accept_reviewed_small_join": accept_reviewed_small_join,
    }
    connection = None
    credentials = None
    connection_id = None

    def remaining() -> float:
        budget = 20 - (time.monotonic() - started)
        if budget <= 0:
            raise BaselineStopped("基准总时间预算用尽，不再发出后续 SQL")
        return budget

    def on_command(sql: Any) -> tuple[float, dict[str, Any]]:
        remaining()
        if len(report["commands"]) >= 16:
            raise BaselineStopped("命令预算用尽")
        statement = sql.decode("utf-8", "replace") if isinstance(sql, bytes) else str(sql)
        event = {"number": len(report["commands"]) + 1, "phase": report["phase"],
                 "verb": statement.split()[0].upper(), "status": "started"}
        report["commands"].append(event)
        return time.monotonic(), event

    def execute(phase: str, sql: str, parameters: Any = None, *, max_rows: int = 20) -> list[dict[str, Any]]:
        report["phase"] = phase
        seconds = min(3, remaining())
        # PyMySQL 每次读包均使用该时限；服务端 MAX_EXECUTION_TIME 同时限制 SELECT 执行。
        connection._read_timeout = seconds
        connection._write_timeout = seconds
        before = time.monotonic()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                rows = list(cursor.fetchmany(max_rows + 1)) if cursor.description else []
                if len(rows) > max_rows:
                    raise BaselineStopped("结果行数超出当前阶段预算")
        except Exception as error:
            if _database_interrupted(error):
                report["database_interrupted"] = True
            raise
        report["timings"][phase + "_milliseconds"] = round((time.monotonic() - before) * 1000, 3)
        remaining()
        return rows

    try:
        report["service"] = _verify_local_service(min(8, remaining()))
        credentials = _credentials()
        report["phase"] = "connect"
        report["connections"] = 1
        try:
            connection = _open_connection(*credentials, min(3, remaining()), on_command)
        except Exception as error:
            if _database_interrupted(error):
                report["database_interrupted"] = True
            raise
        connection_id = int(connection.thread_id())
        report["connection_id"] = connection_id
        execute("session_timeout", "SET SESSION MAX_EXECUTION_TIME=3000")
        execute("session_readonly", "SET SESSION TRANSACTION READ ONLY")
        execute("session_lock_timeout", "SET SESSION lock_wait_timeout=3")
        execute("session_innodb_timeout", "SET SESSION innodb_lock_wait_timeout=3")
        identity = execute("identity", "SELECT DATABASE() AS database_name,@@hostname AS hostname,"
                           "@@port AS port,@@version AS version,CONNECTION_ID() AS connection_id,@@datadir AS datadir", max_rows=1)[0]
        report["identity"] = identity
        if (identity["database_name"] != DATABASE or int(identity["port"]) != PORT
                or identity["hostname"].casefold() != report["service"]["machine"].casefold()
                or int(identity["connection_id"]) != connection_id):
            raise BaselineStopped("实际数据库身份与已核验的本机服务不一致")
        report["metadata"] = _metadata(execute)
        _write_json(output / "metadata.json", report["metadata"])
        report["plan"] = execute("explain", "EXPLAIN " + DRIVER_SQL, report["parameters"], max_rows=4)
        _write_json(output / "plan.json", report["plan"])
        report["plan_validation"] = _validate_plan(report["plan"], accept_reviewed_small_join=accept_reviewed_small_join)
        report["business_selects"] = 1
        rows = execute("business_select", DRIVER_SQL, report["parameters"], max_rows=10)
        if not rows:
            raise BaselineStopped("当前设备没有匹配的润滑保养要求；不更换设备或扩大查询")
        if any(row["device_id"] != device_id for row in rows):
            raise BaselineStopped("基准返回了固定设备之外的数据")
        golden = _json_value(rows)
        encoded = [json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in golden]
        report["golden_rows"] = golden
        report["row_count"] = len(golden)
        report["golden_hash"] = hashlib.sha256("\n".join(sorted(encoded)).encode("utf-8")).hexdigest()
        report["ordered_hash"] = hashlib.sha256("\n".join(encoded).encode("utf-8")).hexdigest()
        report["hash_contract"] = "每行键排序的紧凑 JSON，保留重复行及空值；Decimal 转字符串；golden_hash 再按行排序"
        report["status"] = "passed"
        report["phase"] = "completed"
        _write_json(output / "golden.json", {key: report[key] for key in ("parameters", "golden_rows", "row_count", "golden_hash", "ordered_hash", "hash_contract")})
        (output / "baseline.sql").write_text(SQL + "\n", encoding="utf-8")
        return report
    except Exception as error:
        report["status"] = "stopped"
        report["failure"] = _failure(error)
        # 自定义边界错误不含凭据，可保留明确的停止原因以便人工核验。
        if isinstance(error, BaselineStopped):
            report["reason"] = str(error)
        interrupted = report.get("database_interrupted", False)
        if interrupted:
            STOP.parent.mkdir(parents=True, exist_ok=True)
            _write_json(STOP, {"at": report["started_at"], "failure": report["failure"], "phase": report["phase"],
                               "connectionIds": [connection_id] if connection_id else [], "report": str(output / "baseline.json")})
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
            connection = None
        if interrupted and credentials and connection_id:
            report["diagnostics"] = _diagnose(*credentials, connection_id)
        note = "，数据库异常另见统一 STOP 文件" if interrupted else ""
        raise BaselineStopped("本次基准已停止，参见私有 baseline.json" + note) from None
    finally:
        if connection is not None:
            connection.close()
        report["timings"]["total_milliseconds"] = round((time.monotonic() - started) * 1000, 3)
        _write_json(output / "baseline.json", report)


def main() -> int:
    """命令行只输出状态、耗时和证据位置，不输出业务结果或凭据。"""
    parser = argparse.ArgumentParser(description="执行一次固定设备的本机只读基准")
    parser.add_argument("--output", required=True, type=Path, help="保存私有基准证据的目录")
    parser.add_argument("--device-id", help="明确指定设备标识；省略时仅读取 LIVE_BASELINE_DEVICE_ID，不自动选择设备")
    parser.add_argument("--accept-reviewed-small-join", action="store_true",
                        help="显式接受已审阅的 i≤2000、r≤50、总扫描≤2100 的固定场景计划")
    args = parser.parse_args()
    try:
        report = prepare_baseline(args.output, device_id=args.device_id,
                                  accept_reviewed_small_join=args.accept_reviewed_small_join)
    except BaselineStopped as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "rows": report["row_count"],
                      "elapsed_ms": report["timings"]["total_milliseconds"],
                      "report": str(args.output.resolve() / "baseline.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
