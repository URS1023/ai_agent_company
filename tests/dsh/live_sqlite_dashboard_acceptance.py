"""真实 OpenCode 模型的单轮大屏验收；业务和平台数据均为一次性 SQLite。默认只检查合成夹具。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from urllib.parse import urlsplit
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from real_platform_smoke import Host, ROOT

PROMPT = "统计2026年8月财务应收，按客户和日期汇总并做成数字大屏。仅统计未删除记录，保留真实应收金额。"
COMPOUND_PROMPT = "查询财务数据，做成数字大屏并给我一份erp系统的介绍ppt"
ROWS = [(1, "甲客户", "2026-08-01", "1000.25", 0), (2, "甲客户", "2026-08-01", "250.00", 0),
        (3, "乙客户", "2026-08-10", "500.50", 0), (4, "甲客户", "2026-08-20", "99.25", 0),
        (5, "甲客户", "2026-07-31", "9000.00", 0), (6, "乙客户", "2026-09-01", "8000.00", 0),
        (7, "甲客户", "2026-08-12", "7000.00", 1)]
EXPECTED = [("乙客户", "2026-08-10", "500.50"), ("甲客户", "2026-08-01", "1250.25"), ("甲客户", "2026-08-20", "99.25")]
TABLE = "accounts_receivable"
METADATA = [{"tableName": TABLE, "tableComment": "客户应收账款明细，仅表示应收；不包含付款或已收金额。",
             "financeCategory": "receivable", "columns": [
                 {"columnName": "id", "dataType": "INTEGER", "comment": "明细唯一标识", "isPrimaryKey": True},
                 {"columnName": "customer_name", "dataType": "TEXT", "comment": "客户名称"},
                 {"columnName": "receivable_date", "dataType": "DATE", "comment": "应收确认日期，YYYY-MM-DD"},
                 {"columnName": "receivable_amount", "dataType": "NUMERIC", "comment": "应收金额，人民币元，原币金额可直接求和", "financeCategory": "receivable"},
                 {"columnName": "is_deleted", "dataType": "INTEGER", "comment": "逻辑删除标记，0未删除，1已删除"},
             ]}]


def make_database(path):
    """创建七条合成应收记录，不读取或复制任何在用业务数据。"""
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE accounts_receivable (id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, receivable_date TEXT NOT NULL, receivable_amount NUMERIC NOT NULL, is_deleted INTEGER NOT NULL)")
        db.executemany("INSERT INTO accounts_receivable VALUES (?,?,?,?,?)", ROWS)
        db.commit()
        actual = db.execute("SELECT customer_name, receivable_date, SUM(receivable_amount) FROM accounts_receivable WHERE receivable_date >= '2026-08-01' AND receivable_date < '2026-09-01' AND is_deleted = 0 GROUP BY customer_name, receivable_date ORDER BY customer_name, receivable_date").fetchall()
    normalized = [(customer, date, format(Decimal(str(amount)).quantize(Decimal(".01")), "f")) for customer, date, amount in actual]
    assert normalized == EXPECTED
    assert sum(Decimal(row[2]) for row in normalized) == Decimal("1850.00")


def seed_platform(path):
    """只在指定临时平台登记唯一 SQLite 数据源和明确的应收字段元数据。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    directory = path.parent.resolve()
    assert directory.is_relative_to(Path(tempfile.gettempdir()).resolve())
    sys.path.insert(0, str(ROOT / "backend"))
    from app.db import get_engine, new_session
    from app.ids import new_id
    from app.models import DataSource
    from app.modules.analytics.connections import managed_database_path
    from app.modules.analytics.models import SourceMetadata
    from sqlalchemy import select
    engine = get_engine()
    assert engine.url.get_backend_name() == "sqlite" and Path(engine.url.database).resolve().is_relative_to(directory)
    filename = "synthetic_receivables.sqlite"
    target = managed_database_path(value["workspace_id"], filename, must_exist=False)
    assert target.resolve().is_relative_to(directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    make_database(target)
    with new_session() as db:
        assert not db.scalars(select(DataSource)).all(), "验收库不得带入任何已配置数据源"
        now = datetime.now(timezone.utc)
        source = DataSource(id=new_id(), workspace_id=value["workspace_id"], owner_user_id=value["user_id"],
            name="合成客户应收验收库", provider="sqlite", status="active", created_at=now, updated_at=now,
            connection_config={"version": 1, "databaseName": filename, "description": "2026年客户应收合成测试数据，金额单位人民币元，is_deleted=0有效。",
                               "allowedTables": [TABLE], "allowedColumns": {TABLE: [column["columnName"] for column in METADATA[0]["columns"]]},
                               "maxRows": 30, "timeoutSeconds": 3})
        db.add(source)
        db.flush()
        db.add(SourceMetadata(workspace_id=source.workspace_id, owner_user_id=source.owner_user_id,
            datasource_id=source.id, source_version=1, status="ready", tables_json=METADATA))
        db.commit()
        identity = source.id
    engine.dispose()
    print(json.dumps({"source_id": identity}, ensure_ascii=False))


def normalized_groups(rows):
    """容许中文输出别名，但必须同时有客户、日期和正确应收金额，不接受单个碰巧相等的合计。"""
    result = []
    for row in rows:
        customers = [value for value in row.values() if isinstance(value, str) and value in {"甲客户", "乙客户"}]
        dates = [str(value)[:10] for value in row.values() if re.match(r"^2026-08-\d{2}", str(value))]
        amounts = [value for key, value in row.items() if re.search(r"amount|金额|应收", key, re.I)
                   and not re.search(r"date|日期|count|笔数|数量", key, re.I)]
        if len(customers) != 1 or len(dates) != 1 or len(amounts) != 1:
            return []
        try:
            amount = format(Decimal(str(amounts[0])).quantize(Decimal(".01")), "f")
        except Exception:
            return []
        result.append((customers[0], dates[0], amount))
    return sorted(result)


def audit(directory, run_id, output=None, *, compound=False):
    """直接核对临时平台保存的查询与大屏，不执行任何新业务查询。"""
    path = directory / "platform.sqlite"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        assert all(row["provider"] == "sqlite" for row in db.execute("SELECT provider FROM data_sources"))
        queries = {row["id"]: dict(row) for row in db.execute("SELECT id,status,query_mode,generated_sql,result_json,execution_ms FROM analytics_query_history")}
        boards = [dict(row) for row in db.execute("SELECT id,name,status,version,width,height,background_color,content,lineage,template_id,template_mode FROM report_documents")]
    checked = []
    query_info = []
    for row in queries.values():
        data = json.loads(row["result_json"])
        query_info.append({"query_id": row["id"], "status": row["status"], "mode": row["query_mode"], "sql": row["generated_sql"],
                           "execution_ms": row["execution_ms"], "truncated": data.get("truncated"),
                           "grouped_amounts_exact": row["status"] == "success" and normalized_groups(data.get("data") or []) == EXPECTED})
    for board in boards:
        lineage, content = json.loads(board["lineage"]), json.loads(board["content"])
        if lineage.get("invocationRunId") != run_id:
            continue
        widgets = []
        for widget in content.get("widgets") or []:
            config = widget.get("config") or {}
            query = queries.get(config.get("queryId"))
            if not query:
                continue
            data = json.loads(query["result_json"])
            widgets.append({"query_id": query["id"], "snapshot_exact": config.get("chartDataParsed") == data.get("data"),
                            "grouped_amounts_exact": normalized_groups(data.get("data") or []) == EXPECTED,
                            "component": widget.get("component"), "mapping": config.get("snapshotBinding") or config.get("dataMapping") or {},
                            "headers": (config.get("option") or {}).get("header") or []})
        # 复合场景未指定月份，必须覆盖全部六条有效记录，不能套用八月验收基准。
        complete_amounts = False
        for widget in widgets:
            data = json.loads(queries[widget["query_id"]]["result_json"])
            rows = data.get("data") or []
            keys = {key for row in rows for key in row if re.search(r"amount|receivable|payable|balance|金额|应收", key, re.I)
                    and not re.search(r"date|日期|count|笔数|数量", key, re.I)}
            if compound:
                if widget["component"] in {"JScrollBoard", "JTable"}:
                    visible_fields = {column.get("key") for column in widget["headers"] if isinstance(column, dict)}
                else:
                    visible_fields = {widget["mapping"].get("valueField")}
                keys &= visible_fields
            for key in keys:
                try:
                    complete_amounts |= sum(Decimal(str(row[key])) for row in rows) == Decimal("18850.00")
                except (KeyError, ValueError, ArithmeticError):
                    pass
        expected_verified = complete_amounts if compound else any(item["grouped_amounts_exact"] for item in widgets)
        template_verified = not compound or (board["template_mode"] == "manual" and board["template_id"] == "lynx-source-template-41")
        checked.append({"report_id": board["id"], "name": board["name"], "status": board["status"], "widgets": widgets,
                        "complete_amounts": complete_amounts, "template_verified": template_verified,
                        "valid": board["status"] in {"draft", "published"} and bool(widgets)
                                 and all(item["snapshot_exact"] for item in widgets) and expected_verified and template_verified})
        if output:
            snapshot = output.parent / (output.stem + "-report-" + board["id"] + ".json")
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps({"report": {"id": board["id"], "reportName": board["name"],
                "version": board["version"], "width": board["width"], "height": board["height"],
                "backgroundColor": board["background_color"], "content": content, "lineage": lineage, "refreshSeconds": 0,
                "templateMode": board["template_mode"], "templateId": board["template_id"]},
                "expected_groups": None if compound else EXPECTED}, ensure_ascii=False, indent=2), encoding="utf-8")
            checked[-1]["preview_snapshot"] = str(snapshot)
    return {"queries": query_info, "reports": checked, "expected_grouped_amounts": None if compound else EXPECTED,
            "expected_total": "18850.00" if compound else "1850.00",
            "passed": any(board["valid"] for board in checked)}


def run_model(args):
    """单条真实模型任务，按交付复杂度限次限时；全程仅使用一次性 SQLite。"""
    from live_model_proxy import LiveModelProxy
    prompt = COMPOUND_PROMPT if args.compound else PROMPT
    request_limit, time_limit = (24, 360) if args.compound else (12, 240)
    report = {"scope": "真实opencode ds4.1flash；平台/业务均为临时SQLite合成记录；不连接szh_new或任何远程数据库",
              "prompt": prompt, "status": "running", "phases": [], "tools": [], "model_requests_limit": request_limit}
    started = time.monotonic()
    proxy = None
    try:
        with tempfile.TemporaryDirectory(prefix="test5-live-sqlite-dashboard-") as temporary, ExitStack() as cleanup:
            directory = Path(temporary)
            proxy = cleanup.enter_context(LiveModelProxy.from_opencode(args.opencode_config, model="deepseek-v4.1-flash",
                max_requests=request_limit, total_timeout=time_limit, request_timeout=60))
            host = Host(directory, urlsplit(proxy.base_url).port, env_overrides={"AGNES_CHAT_MODEL": proxy.model,
                "AGNES_BASE_URL": proxy.base_url, "VISION_ENABLED": "0", "EMBEDDING_ENABLED": "0"})
            cleanup.callback(host.close)
            host.ready()
            host.login()
            identity = host.json("GET", "/api/auth/me")
            seed = directory / "seed.json"
            seed.write_text(json.dumps({"workspace_id": host.http.headers["X-Workspace-Id"], "user_id": identity["user"]["id"]}), encoding="utf-8")
            seeded = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--seed", str(seed)],
                                    cwd=ROOT, env=host.env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            if seeded.returncode:
                raise RuntimeError("隔离业务夹具登记失败；请先运行 --check-fixture 并检查本机测试环境")
            source_id = json.loads(seeded.stdout.strip().splitlines()[-1])["source_id"]
            host.json("POST", "/api/models/profiles", status=201, json={"profile_name": "fixture", "provider": "openai-compatible",
                "model": proxy.model, "endpoint": proxy.base_url, "api_key": "local-proxy-only", "capabilities": ["chat", "tools"],
                "runtime_options": {"protocol": "openai-completions", "thinking": "disabled",
                    "context_window": 131072 if args.compound else 32768, "max_output_tokens": 16384 if args.compound else 4096}})
            cid = host.json("POST", "/api/chat/conversations", status=201, json={"title": "合成应收数字大屏验收"})["id"]
            report["phases"].append({"phase": "fixture_ready", "seconds": round(time.monotonic() - started, 3)})
            run_started = time.monotonic()
            selection = {"dashboard_template_mode": "manual", "dashboard_template_id": "lynx-source-template-41"} if args.compound else {}
            receipt = host.send(cid, prompt, "sqlite-ar-dashboard-one", datasource_ids=[source_id],
                skill_ids=[] if args.compound else ["business-analysis", "data-access", "dashboard-delivery"], **selection)
            run_id = receipt["run_id"]
            report["run_id"] = run_id
            try:
                with host.request("GET", f"/api/chat/runs/{run_id}/events", stream=True) as response:
                    event = ""
                    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                        elapsed = time.monotonic() - run_started
                        if elapsed > time_limit or proxy.summary()["errors"]:
                            raise TimeoutError("模型异常或本轮总时间预算已到，停止验收")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line[5:].strip())
                            if event in {"tool.started", "tool.completed", "question.pending", "run.completed", "run.failed", "run.cancelled", "done"}:
                                report["phases"].append({"phase": event, "seconds": round(elapsed, 3), "tool": data.get("tool_name")})
                            if event in {"tool.completed", "tool.failed"}:
                                report["tools"].append({"name": data.get("tool_name"), "status": data.get("status", "failed"),
                                                        "result": data.get("result"), "error": data.get("error")})
                            if event == "question.pending":
                                raise RuntimeError("模型要求补充已明确的夹具范围，记录为未完成；不自动代答")
                            if event == "done":
                                report["business_status"] = data.get("status")
                                break
            except BaseException:
                host.json("POST", f"/api/chat/runs/{run_id}/cancel", json={})
                report["audit"] = audit(directory, run_id, args.output, compound=args.compound)
                report["model_requests"] = proxy.records
                raise
            report["audit"] = audit(directory, run_id, args.output, compound=args.compound)
            detail = host.json("GET", f"/api/chat/conversations/{cid}")
            report["answer"] = next((message.get("content", "") for message in reversed(detail["messages"]) if message["role"] == "assistant"), "")
            report["deliveries"] = [{key: item.get(key) for key in ("type", "status", "report_id", "artifact_id", "validation_error")}
                                     for item in detail["projections"]["deliveries"]]
            presentation_ok = not args.compound
            for delivery in detail["projections"]["deliveries"]:
                if delivery.get("type") != "presentation" or delivery.get("status") != "ready":
                    continue
                downloaded = host.request("GET", delivery["download_url"])
                downloaded.raise_for_status()
                with ZipFile(BytesIO(downloaded.content)) as archive:
                    slides = [name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
                    texts = [" ".join(ET.fromstring(archive.read(name)).itertext()) for name in slides]
                    introduction = " ".join(texts)
                    presentation_ok = (len(slides) >= 3 and all(text.strip() for text in texts) and "ERP" in introduction.upper()
                                       and sum(topic in introduction for topic in ("采购", "销售", "库存", "财务", "生产")) >= 3)
                    report["presentation_check"] = {"slides": len(slides), "bytes": len(downloaded.content), "passed": presentation_ok}
            native_files = list(directory.rglob("session.v4.jsonl"))
            native_events = [json.loads(line) for path in native_files for line in path.read_text(encoding="utf-8").splitlines()]
            report["compactions"] = sum(item["type"] == "compaction/start" for item in native_events)
            report["run_seconds"] = round(time.monotonic() - run_started, 3)
            passed = report["business_status"] == "completed" and report["audit"]["passed"] and presentation_ok
            report["status"] = "passed" if passed else "failed"
    except BaseException as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:300])
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        if proxy:
            report["model"] = proxy.summary()
            report["model_requests"] = proxy.records
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true", help="显式运行一条真实模型任务；仅在本次获准后使用")
    parser.add_argument("--check-fixture", action="store_true", help="只验证本机合成数据和基准，不读取模型凭据或联网")
    parser.add_argument("--compound", action="store_true", help="复现财务大屏与ERP介绍PPT单句任务，并保留手选模板41")
    parser.add_argument("--opencode-config", type=Path, default=Path.home() / ".config/opencode/opencode.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".implementation/live-sqlite-dashboard-result.json")
    parser.add_argument("--seed", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.seed:
        seed_platform(args.seed)
        return 0
    if not args.run_model:
        with tempfile.TemporaryDirectory(prefix="test5-ar-fixture-") as temporary:
            make_database(Path(temporary) / "business.sqlite")
        print(json.dumps({"status": "fixture_passed", "business_rows": len(ROWS), "expected": None if args.compound else EXPECTED,
                          "expected_total": "18850.00" if args.compound else "1850.00", "model_calls": 0, "network_calls": 0}, ensure_ascii=False))
        return 0
    return run_model(args)


if __name__ == "__main__":
    raise SystemExit(main())
