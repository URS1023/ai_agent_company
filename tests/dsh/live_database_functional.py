"""本机数据库功能验收入口：先检查目录，再用有界只读查询建立独立基准。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from live_baseline import (
    ROOT, _verify_local_service, _credentials, _open_connection, _json_value,
    _failure, _database_interrupted, _diagnose,
)


OUTPUT = ROOT / ".implementation/full-functional-20260929"
STOP = OUTPUT / "STOP_AFTER_DATABASE_ERROR.json"
SQL_CASES = {
    "finance_total": "SELECT COUNT(*) AS record_count,SUM(c_contractAmount) AS contract_amount,SUM(c_pamented) AS paid_amount,SUM(c_unpayment) AS unpaid_amount,SUM(c_requestPament) AS requested_amount FROM t_summaryoffundpayments WHERE isDeleted=0",
    "finance_department": "SELECT c_bumen AS department_id,COUNT(*) AS record_count,SUM(c_contractAmount) AS contract_amount,SUM(c_pamented) AS paid_amount,SUM(c_unpayment) AS unpaid_amount,SUM(c_requestPament) AS requested_amount FROM t_summaryoffundpayments WHERE isDeleted=0 GROUP BY c_bumen ORDER BY c_bumen LIMIT 20",
    "inventory_sample": "SELECT s.spInventoryId AS stock_id,m.c_productName AS material_name,s.c_productModel AS stock_model,s.c_availableStock AS quantity,s.c_unit AS unit_code,m.c_safetyStock AS safety_stock,w.c_warehouseName AS warehouse_name,s.c_locationId AS location_id,s.c_batchNo AS batch FROM t_spinventory s LEFT JOIN t_sparepartsinfor m ON m.sparePartsInforId=s.c_productId AND m.isDeleted=0 LEFT JOIN t_basicinformationofthewarehouse w ON w.basicInformationoftheWarehouseId=s.c_warehouseId AND w.isDeleted=0 WHERE s.isDeleted=0 ORDER BY s.autoId LIMIT 10",
}


def write(name, value):
    """证据保存在私有目录，终端只输出状态和规模。"""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(_json_value(value), ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def measured_cursor(stage, budget=18):
    """单连接、串行、有时间上限；资源异常仅诊断本连接并停止本轮。"""
    if STOP.exists():
        raise RuntimeError("本轮已有数据库异常，停止后续测试")
    _verify_local_service(10)
    user, password = _credentials()
    events, started, connection = [], time.monotonic(), None

    def on_command(sql):
        if len(events) >= budget or time.monotonic() - started > 30:
            raise RuntimeError("本阶段已达工作量预算")
        event = {"sql": str(sql), "status": "started"}
        events.append(event)
        return time.monotonic(), event

    try:
        connection = _open_connection(user, password, 3, on_command)
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=2500")
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            yield cursor
    except Exception as error:
        if _database_interrupted(error):
            write(STOP.name, {"stage": stage, "error": _failure(error), "at": datetime.now(timezone.utc).isoformat()})
            write("database-diagnostic.json", _diagnose(user, password, connection.thread_id() if connection else None))
        raise
    finally:
        if connection:
            connection.close()
        write(stage + "-audit.json", {"connections": 1, "commands": events, "seconds": round(time.monotonic() - started, 3)})


def complete_catalog():
    """小范围目录检查成功后补齐字段目录，只读取系统元数据。"""
    schema = json.loads((OUTPUT / "database-schema.json").read_text(encoding="utf-8"))
    with measured_cursor("catalog", 4) as cursor:
        cursor.execute("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,COLUMN_COMMENT,IS_NULLABLE,COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 15000", ("szh_new",))
        columns = cursor.fetchall()
        if len(columns) >= 15000:
            raise RuntimeError("字段目录达到预算上限，不继续扩量")
    schema["columns"] = columns
    schema["full_columns"] = True
    write("database-schema.json", schema)
    print(json.dumps({"stage": "catalog", "tables": len(schema["tables"]), "columns": len(columns), "business_selects": 0}))


def query_plans():
    """只解释三个固定查询，先提供扫描规模供审阅，不执行业务取数。"""
    plans = {}
    with measured_cursor("plans", 6) as cursor:
        for name, sql in SQL_CASES.items():
            cursor.execute("EXPLAIN " + sql)
            plans[name] = {"sql": sql, "plan": cursor.fetchall()}
    write("database-plans.json", plans)
    print(json.dumps({name: value["plan"] for name, value in plans.items()}, ensure_ascii=False, default=str))


def query_baseline():
    """仅执行已审阅的三个小查询，完整保留金额精度、重复行及空值。"""
    plans = json.loads((OUTPUT / "database-plans.json").read_text(encoding="utf-8"))
    for name, sql in SQL_CASES.items():
        assert plans[name]["sql"] == sql
        plan = plans[name]["plan"]
        if any(row.get("rows") is None or int(row["rows"]) > 1200 for row in plan):
            raise RuntimeError("执行计划超过本轮每节点1200行预算")
        if sum(int(row["rows"]) for row in plan) > 2500:
            raise RuntimeError("执行计划超过本轮总扫描2500行预算")
    values = {}
    with measured_cursor("baseline", 6) as cursor:
        for name, sql in SQL_CASES.items():
            start = time.monotonic()
            cursor.execute(sql)
            rows = cursor.fetchall()
            values[name] = {"sql": sql, "rows": rows, "seconds": round(time.monotonic() - start, 6)}
    write("database-baseline.json", values)
    print(json.dumps({name: {"rows": len(value["rows"]), "seconds": value["seconds"]} for name, value in values.items()}))


def saved_metadata():
    """复用当前本地数据源的真实关系声明，只读平台元数据，不改数据源绑定。"""
    source_id = "01M24QGNNN404ZF3A9XB48FRY5"
    selected = ["t_summaryoffundpayments", "t_spinventory", "t_sparepartsinfor",
                "t_basicinformationofthewarehouse", "t_warehouselocationinformation"]
    output = {}
    queries = [
        ("source", "SELECT provider,connection_config FROM company_agent.data_sources WHERE id=%s LIMIT 1", (source_id,)),
        ("metadata", "SELECT source_version,tables_json FROM company_agent.analytics_source_metadata WHERE datasource_id=%s LIMIT 1", (source_id,)),
        ("semantics", "SELECT table_name,relations_json FROM company_agent.analytics_source_semantic_documents WHERE datasource_id=%s AND table_name IN (" + ",".join(["%s"] * len(selected)) + ") LIMIT 6", (source_id, *selected)),
    ]
    with measured_cursor("platform-metadata", 9) as cursor:
        for name, sql, parameters in queries:
            cursor.execute("EXPLAIN " + sql, parameters)
            plan = cursor.fetchall()
            if any(int(row.get("rows") or 0) > 1000 for row in plan):
                raise RuntimeError("平台元数据读取计划超过1000行预算")
            cursor.execute(sql, parameters)
            output[name] = cursor.fetchall()
            if name == "source":
                config = output[name][0]["connection_config"]
                config = json.loads(config) if isinstance(config, str) else config
                assert output[name][0]["provider"] == "mysql"
                assert config["host"] in {"127.0.0.1", "localhost", "::1"} and int(config["port"]) == 3306 and config["databaseName"] == "szh_new"
    raw = output["metadata"][0]["tables_json"]
    tables = json.loads(raw) if isinstance(raw, str) else raw
    by_name = {table["tableName"]: table for table in tables}
    for row in output["semantics"]:
        raw = row["relations_json"]
        by_name[row["table_name"]]["declaredRelations"] = json.loads(raw) if isinstance(raw, str) else raw
    metadata = [by_name[name] for name in selected]
    write("authorized-test-metadata.json", metadata)
    print(json.dumps({"stage": "platform_metadata", "source_tables": len(tables), "test_tables": len(metadata),
                      "relation_counts": {table["tableName"]: len(table.get("declaredRelations") or []) for table in metadata}}, ensure_ascii=False))


def inspect_database():
    """仅获取表目录及财务、库存、设备、质检候选字段，不读取业务行。"""
    if STOP.exists():
        raise RuntimeError("本轮已有数据库异常，停止后续测试")
    target = _verify_local_service(10)
    write("database-target.json", target)
    user, password = _credentials()
    commands, started, connection = [], time.monotonic(), None

    def on_command(sql):
        if len(commands) >= 12 or time.monotonic() - started > 25:
            raise RuntimeError("目录检查已达工作量预算")
        item = {"sql": str(sql), "status": "started"}
        commands.append(item)
        return time.monotonic(), item

    try:
        connection = _open_connection(user, password, 3, on_command)
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=2500")
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("SELECT TABLE_NAME,TABLE_COMMENT,TABLE_ROWS,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME LIMIT 500", ("szh_new",))
            tables = cursor.fetchall()
            candidates = [table["TABLE_NAME"] for table in tables if any(word in str(table["TABLE_COMMENT"]) for word in
                ("应收", "应付", "付款", "收款", "合同", "订单", "库存", "仓库", "物料", "设备管理", "验收", "质检"))]
            slots = ",".join(["%s"] * len(candidates))
            cursor.execute("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,COLUMN_COMMENT,IS_NULLABLE,COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN (" + slots + ") ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 2500", ("szh_new", *candidates))
            columns = cursor.fetchall()
            if len(tables) == 500 or len(columns) == 2500:
                raise RuntimeError("目录达到读取上限，不能声称完整元数据")
        write("database-schema.json", {"target": target, "tables": tables, "candidate_tables": candidates, "columns": columns})
        print(json.dumps({"status": "inspected", "tables": len(tables), "candidate_tables": len(candidates), "candidate_columns": len(columns), "connections": 1, "commands": len(commands)}, ensure_ascii=False))
    except Exception as error:
        if _database_interrupted(error):
            write(STOP.name, {"stage": "metadata", "error": _failure(error), "at": datetime.now(timezone.utc).isoformat()})
            write("database-diagnostic.json", _diagnose(user, password, connection.thread_id() if connection else None))
        raise
    finally:
        if connection:
            connection.close()
        write("database-inspection-audit.json", {"connections": 1, "commands": commands, "seconds": round(time.monotonic() - started, 3)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["inspect", "catalog", "plans", "baseline", "saved-metadata"])
    args = parser.parse_args()
    if args.stage == "inspect":
        inspect_database()
    elif args.stage == "catalog":
        complete_catalog()
    elif args.stage == "plans":
        query_plans()
    elif args.stage == "baseline":
        query_baseline()
    elif args.stage == "saved-metadata":
        saved_metadata()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
