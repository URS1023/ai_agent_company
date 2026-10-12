"""Local-only browser fixture: real SQLite queries, deterministic model responses.

Run with the project venv, then run dashboard_generation.js against port 9146.
Each process creates a fresh isolated platform DB; no production sources load.

中文说明（大屏生成的浏览器夹具服务）：
用途：为 tests/ui/dashboard_generation.js 提供可复现环境——真实 SQLite 业务库 + 确定性模型响应，
每次进程启动都在 .implementation/dashboard-generation-20260914/ 下新建临时目录与平台库。
运行方式：用项目 venv 执行本文件（uvicorn 监听 127.0.0.1:9146），再运行对应的 JS 用例。
隔离与约束：engine_connect 事件钩子拒绝任何非 SQLite 连接；向量化被替换为空实现；
预置的源只允许 receivables/devices 两张表，且断言需求里不能出现 OEE（防止用例漂移）。
影响范围：只写临时目录内的文件与数据库，不加载任何生产数据源。
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".implementation" / "dashboard-generation-20260914"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
HERE = Path(tempfile.mkdtemp(prefix="fixture-", dir=ARTIFACTS))
os.environ.update(DATABASE_URL="sqlite:///" + (HERE / "platform.sqlite3").as_posix(),
                  APP_ENV="test", AUTO_MIGRATE="1", STORAGE_ROOT=str(HERE / "storage"),
                  SCHEDULER_ENABLED="0", BOOTSTRAP_LYNX_REPORTS="0",
                  BOOTSTRAP_ADMIN_PASSWORD="LocalDashboardFixture2026!")
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import event, select
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "engine_connect")
def local_only(connection):
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Only isolated SQLite connections are allowed in this fixture")


from app.main import create_app
from app.seed import bootstrap
from app.db import new_session
from app.common.deps import AuthContext
from app.models import User, Workspace
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.sources import create_source
from app.modules.analytics.schemas import SourceInput
from app.modules.reports import generation
from app.modules.runtime import agent
from app.providers import model

model.embed_texts = lambda texts, **kwargs: [[] for _ in texts]
app = create_app()
identity = bootstrap()
with new_session() as db:
    workspace = db.scalar(select(Workspace).order_by(Workspace.created_at))
    ctx = AuthContext(db=db, user=db.get(User, identity["admin_user_id"]), workspace=workspace, role="admin", token="fixture")
    database = managed_database_path(workspace.id, "finance-equipment.sqlite3", must_exist=False)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE receivables (id INTEGER, customer TEXT, amount INTEGER);
            INSERT INTO receivables VALUES (1, '客户甲', 120), (2, '客户乙', 80);
            CREATE TABLE devices (id INTEGER, name TEXT, status TEXT);
            INSERT INTO devices VALUES (1, '设备甲', '运行'), (2, '设备乙', '维修');
        """)
    source = create_source(ctx, SourceInput(name="本机财务设备验证", type="sqlite", databaseName=database.name,
        allowedTables=["receivables", "devices"], timeoutSeconds=5, maxRows=100))
    source_id = source.id

counts = {"designs": 0, "reviews": 0, "generationQueries": 0}
original_query = generation.execute_query


def record_query(ctx, payload, **kwargs):
    counts["generationQueries"] += 1
    (HERE / "counts.json").write_text(json.dumps(counts), encoding="utf-8")
    return original_query(ctx, payload, **kwargs)


def model_reply(profile, messages, **kwargs):
    context = json.loads(messages[1]["content"])
    assert "财务和设备数字大屏" in context["requirement"]
    assert "OEE" not in context["requirement"]
    if "candidates" in context:
        return {"content": json.dumps({"templateId": None, "reason": "按原需求生成财务与设备布局"})}
    if context.get("task") == "review_report_semantics":
        counts["reviews"] += 1
        complete = len(context["widgets"]) == 2
        (HERE / "counts.json").write_text(json.dumps(counts), encoding="utf-8")
        return {"content": json.dumps({"aligned": complete, "reason": "财务和设备均覆盖" if complete else "缺少设备板块",
            "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "真实应收金额及设备状态字段"}
                        for item in context["widgets"]]})}
    counts["designs"] += 1
    widgets = [
        {"id": "heading", "component": "JText", "componentName": "财务与设备总览", "x": 40, "y": 20, "w": 1300, "h": 70,
         "config": {"option": {"body": {"text": "财务与设备运营总览", "fontSize": 32, "color": "#ffffff"}}}},
        {"id": "finance", "component": "JStatsSummary", "componentName": "财务 · 应收金额", "x": 40, "y": 140, "w": 600, "h": 350,
         "config": {"option": {"title": {"text": "应收金额"}}},
         "query": {"datasourceId": source_id, "sql": "SELECT SUM(amount) AS total FROM receivables"}},
    ]
    if context.get("semanticReview"):
        widgets.append({"id": "equipment", "component": "JBar", "componentName": "设备 · 状态分布", "chartType": "bar",
            "x": 700, "y": 140, "w": 650, "h": 350, "config": {"option": {"title": {"text": "设备状态分布"}}},
            "query": {"datasourceId": source_id, "sql": "SELECT status, COUNT(*) AS total FROM devices GROUP BY status"}})
    return {"content": json.dumps({"title": "财务与设备运营总览", "widgets": widgets}, ensure_ascii=False)}


def chat_reply(db, run, messages, tools, temperature=0.2):
    if not any(item.get("role") == "tool" for item in messages):
        yield "final", {"content": "正在按已选数据生成财务与设备大屏。", "tool_calls": [{"id": "fixture-dashboard", "type": "function", "function": {
            "name": "create_business_dashboard", "arguments": json.dumps({
                "requirement": "近30天财务设备质检，OEE和利润率各50%", "datasource_ids": ["wrong-source"], "template_mode": "none"})}}], "usage": {}}
    else:
        yield "final", {"content": "已根据实际应收金额和设备状态生成财务与设备大屏，并保存为唯一草稿，可直接预览或继续编辑。", "tool_calls": [], "usage": {}}


generation.execute_query = record_query
model.chat_for_run = model_reply
agent._llm_stream = chat_reply
(ARTIFACTS / "active-fixture.json").write_text(json.dumps({"directory": str(HERE), "sourceId": source_id}), encoding="utf-8")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9146, log_level="warning")
