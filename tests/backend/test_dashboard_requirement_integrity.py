"""Local SQLite + deterministic model: repair a combined dashboard without changing the user's goal.

中文说明：内存 SQLite + 确定性模型：合并需求的大屏在修复后仍保持用户原始目标。
验证行为：对话只保存一份草稿、失败修复不重复查询、元数据与固定模板同时保留两个范围、
自动参考模板只修缺失范围、模型返回非 JSON 时的一次修复、手动模板保留未绑定组件并按需求补标题与指标名。
隔离方式：monkeypatch + client 夹具（内存 SQLite），模型被替换为确定性响应，不连业务库。
"""
import json
import sqlite3

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import AuthIdentity, ToolCall
from app.modules.analytics.connections import managed_database_path
from app.modules.business_ai.models import BusinessActionReceipt
from app.modules.reports import generation
from app.modules.reports.models import Report, ReportGeneration
from app.modules.reports.generation_requirements import compatible_auto_templates
from app.modules.runtime import agent
from app.providers import model
from app.providers.tool.gateway import ToolGateway, build_context
from test_reports import create_custom_layout_template

ORIGINAL = "生成财务和设备数字大屏，使用输入区已选择的真实数据与模板策略，并保存为唯一草稿。"


@pytest.fixture
def combined_source(client, administrator, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(model, "embed_texts", lambda texts, **kwargs: [[] for _ in texts])
    path = managed_database_path(administrator["X-Workspace-Id"], "combined.sqlite3", must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE receivables (id INTEGER, customer TEXT, amount INTEGER)")
        connection.executemany("INSERT INTO receivables VALUES (?, ?, ?)", [(1, "客户甲", 120), (2, "客户乙", 80)])
        connection.execute("CREATE TABLE devices (id INTEGER, name TEXT, status TEXT)")
        connection.executemany("INSERT INTO devices VALUES (?, ?, ?)", [(1, "设备甲", "运行"), (2, "设备乙", "维修")])
    response = client.post("/api/v1/analytics/datasources", headers=administrator, json={
        "name": "本机财务设备验证", "type": "sqlite", "databaseName": path.name,
        "allowedTables": ["receivables", "devices"],
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


def widgets(source_id):
    return [
        {"id": "finance", "component": "JStatsSummary", "componentName": "财务 · 应收金额",
         "x": 40, "y": 80, "w": 560, "h": 250, "config": {},
         "query": {"datasourceId": source_id, "sql": "SELECT SUM(amount) AS total FROM receivables"}},
        {"id": "equipment", "component": "JBar", "componentName": "设备 · 状态分布", "chartType": "bar",
         "x": 680, "y": 80, "w": 680, "h": 380, "config": {},
         "query": {"datasourceId": source_id, "sql": "SELECT status, COUNT(*) AS total FROM devices GROUP BY status"}},
    ]


@pytest.fixture(autouse=True)
def isolated_followups(monkeypatch):
    monkeypatch.setattr(agent, '_generate_followups', lambda *args: ([], ''))


def test_combined_chat_preserves_original_repairs_once_and_saves_unique_draft(
    client, administrator, combined_source, db, monkeypatch,
):
    incompatible = create_custom_layout_template(client, administrator, "质检与设备看板")
    contexts, sql_calls, stream_calls = [], [], []
    execute = generation.execute_query

    def record_query(ctx, query, **kwargs):
        sql_calls.append(query.query)
        return execute(ctx, query, **kwargs)

    monkeypatch.setattr(generation, "execute_query", record_query)

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        contexts.append(context)
        assert context["requirement"] == ORIGINAL
        assert "OEE" not in context["requirement"] and "近30天" not in context["requirement"]
        if "candidates" in context:
            assert incompatible["id"] not in {item["id"] for item in context["candidates"]}
            # Even an out-of-catalog choice cannot force the incompatible layout.
            return {"content": json.dumps({"templateId": incompatible["id"], "reason": "错误选择"})}
        if context.get("task") == "review_report_semantics":
            complete = len(context["widgets"]) == 2
            return {"content": json.dumps({"aligned": complete, "reason": "财务和设备均覆盖" if complete else "缺少设备板块",
                "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "实际表字段支撑"} for item in context["widgets"]]})}
        assert context["templateSlots"] == []
        result = widgets(combined_source["id"])
        if "repairInstruction" not in context:
            result = result[:1]
        else:
            assert "chartDataParsed" not in json.dumps(context["previousCandidate"])
            assert "data" not in context["previousCandidate"]["widgets"][0]
            result = result[1:]
        return {"content": json.dumps({"title": "财务与设备", "widgets": result})}

    def chat_reply(_db, run, messages, tools, temperature=0.2):
        stream_calls.append(1)
        if len(stream_calls) == 1:
            yield "final", {"content": "", "tool_calls": [{"id": "combined-request", "type": "function", "function": {
                "name": "create_business_dashboard", "arguments": json.dumps({
                    "requirement": "近30天财务、设备、质检，OEE和利润率各占50%", "template_mode": "none",
                    "datasource_ids": ["model-picked-other-source"],
                })}}], "usage": {}, "model": "local-chat-fixture"}
        else:
            yield "final", {"content": "已生成唯一财务与设备草稿。", "tool_calls": [], "usage": {}, "model": "local-chat-fixture"}

    monkeypatch.setattr(model, "chat_for_run", reply)
    monkeypatch.setattr(agent, "_llm_stream", chat_reply)
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json={
        "content": ORIGINAL, "datasource_ids": [combined_source["id"]], "dashboard_template_mode": "auto",
    })
    assert response.status_code == 200, response.text
    reports = db.scalars(select(Report)).all()
    assert len(reports) == 1, response.text
    assert reports[0].status == "draft" and reports[0].lineage["templateReference"]["adapted"]
    assert reports[0].lineage["customerRequirement"]["objective"] == ORIGINAL
    record = db.scalar(select(ReportGeneration))
    assert record.status == "completed"
    assert len([w for w in record.result_json["widgets"] if w.get("generatedSql")]) == 2
    assert len(sql_calls) == 2, "Unchanged finance query must be reused during the single layout repair"
    receipt = db.scalar(select(BusinessActionReceipt))
    assert receipt.status == "completed"
    assert receipt.invocation_run_id
    # Retrying after success reuses the saved report even if the model changes its text.
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin"))
    tool_context = build_context(db=db, workspace_id=administrator["X-Workspace-Id"], user_id=identity.user_id,
        run_id=receipt.invocation_run_id, selected_datasource_ids=[combined_source["id"]], selected_dashboard_template_mode="auto")
    repeated = ToolGateway(db).execute(administrator["X-Workspace-Id"], "create_business_dashboard",
        {"requirement": "又改成质检", "template_mode": "auto"}, tool_context)
    assert repeated.status == "success" and repeated.data["reused"] is True
    assert len(db.scalars(select(Report)).all()) == 1 and len(sql_calls) == 2


def test_metadata_and_fixed_templates_keep_both_requested_scopes():
    equipment_tables = [{"tableName": f"device_inspection_{index}", "tableComment": "设备巡检保养维修",
                         "columns": [{"columnName": "id", "dataType": "INTEGER"}]} for index in range(40)]
    finance = {"tableName": "receivables", "tableComment": "应收账款", "columns": [{"columnName": "amount", "dataType": "DECIMAL"}]}
    compact = generation._compact_schemas([{"id": "local", "tables": [*equipment_tables, finance]}], ORIGINAL)[0]
    assert len(compact["tables"]) <= 24 and "receivables" in {item["tableName"] for item in compact["tables"]}
    incompatible = {"id": "wrong", "name": "质检与设备看板", "description": "能适配财务设备全业务", "content": {"widgets": []}}
    compatible = {"id": "right", "name": "财务与设备", "content": {"widgets": []}}
    assert compatible_auto_templates(ORIGINAL, [incompatible, compatible]) == [compatible]


@pytest.mark.parametrize("failure", ["semantic", "binding"])
def test_failed_repair_stops_same_chat_run_without_repeat_queries(
    client, administrator, combined_source, db, monkeypatch, failure,
):
    designs, reviews, queries, chat_calls = [], [], [], []
    execute = generation.execute_query

    def query(ctx, payload, **kwargs):
        queries.append(payload.query)
        return execute(ctx, payload, **kwargs)

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        assert context["requirement"] == ORIGINAL
        if "candidates" in context:
            return {"content": json.dumps({"templateId": None})}
        if context.get("task") == "materialize_report_widget":
            return {"content": json.dumps({"datasourceId": "unauthorized", "sql": "SELECT COUNT(*) FROM devices"})}
        if context.get("task") == "review_report_semantics":
            reviews.append(context)
            return {"content": json.dumps({"aligned": False, "reason": "缺少设备状态检查", "widgets": [
                {"widgetId": item["widgetId"], "aligned": True, "reason": "应收金额来自真实字段"}
                for item in context["widgets"]]})}
        designs.append(context)
        candidate = widgets(combined_source["id"])
        if failure == "binding":
            candidate = candidate[:1]
            candidate[0].pop("query")
        return {"content": json.dumps({"title": "财务与设备", "widgets": candidate})}

    def chat_reply(_db, run, messages, tools, temperature=0.2):
        chat_calls.append(1)
        if len(chat_calls) == 1:
            calls = [{"id": "first", "type": "function", "function": {
                "name": "create_business_dashboard", "arguments": json.dumps({"requirement": ORIGINAL})}}]
            if failure == "binding":
                calls.append({"id": "extra-query", "type": "function", "function": {
                    "name": "query_business_data", "arguments": json.dumps({"query": "SELECT COUNT(*) FROM devices", "mode": "sql"})}})
            yield "final", {"content": "", "tool_calls": calls, "usage": {}}
        else:
            assert "create_business_dashboard" not in {agent._tool_name(tool) for tool in tools}
            assert "query_business_data" not in {agent._tool_name(tool) for tool in tools}
            call_index = next(index for index, item in enumerate(messages) if item.get("tool_calls"))
            assert messages[call_index + 1]["role"] == "tool"
            assert json.loads(messages[call_index + 1]["content"])["metadata"]["retryable"] is False
            yield "final", {"content": "设备状态信息仍未满足要求，未保存草稿。", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(generation, "execute_query", query)
    monkeypatch.setattr(model, "chat_for_run", reply)
    monkeypatch.setattr(agent, "_llm_stream", chat_reply)
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json={
        "content": ORIGINAL, "datasource_ids": [combined_source["id"]], "dashboard_template_mode": "none",
    })
    assert response.status_code == 200 and "未完成" in response.text
    receipt = db.scalar(select(BusinessActionReceipt))
    assert receipt.status == "failed" and receipt.result_json["retryable"] is False
    expected_counts = (2, 2, 2) if failure == "semantic" else (1, 0, 0)
    assert (len(designs), len(reviews), len(queries)) == expected_counts
    assert db.scalar(select(Report)) is None
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin"))
    context = build_context(db=db, workspace_id=administrator["X-Workspace-Id"], user_id=identity.user_id,
        run_id=receipt.invocation_run_id, selected_datasource_ids=[combined_source["id"]], selected_dashboard_template_mode="none")
    result = ToolGateway(db).execute(administrator["X-Workspace-Id"], "create_business_dashboard",
        {"requirement": "改成销售质检大屏"}, context)
    assert result.status == "failed" and result.metadata.get("retryable") is False, result.to_dict()
    assert (len(designs), len(reviews), len(queries)) == expected_counts
    assert len(db.scalars(select(ReportGeneration)).all()) == 1
    if failure == "binding":
        extra_query = db.scalar(select(ToolCall).where(ToolCall.tool_name == "query_business_data"))
        assert extra_query is None or extra_query.error_code == "dashboard_generation_finished"


def test_auto_template_keeps_reference_when_business_slots_need_adaptation(
    client, administrator, combined_source, monkeypatch,
):
    template = create_custom_layout_template(client, administrator, "财务和设备大屏")
    designs = []

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if "candidates" in context:
            return {"content": json.dumps({"templateId": template["id"]})}
        if context.get("task") == "review_report_semantics":
            complete = len(context["widgets"]) == 2
            return {"content": json.dumps({"aligned": complete, "reason": "两板块已覆盖" if complete else "固定槽位不包含设备",
                "widgets": [{"widgetId": item["widgetId"], "aligned": complete, "reason": "检查实际指标"}
                            for item in context["widgets"]]})}
        designs.append(context)
        assert context["templateId"] is None and context["templateSlots"] == []
        assert context["templateReference"]["id"] == template["id"]
        return {"content": json.dumps({"title": "财务与设备", "widgets": widgets(combined_source["id"])})}

    monkeypatch.setattr(model, "chat_for_run", reply)
    response = client.post("/api/v1/reports/screen/ai-generate", headers=administrator, json={
        "theme": ORIGINAL, "templateMode": "auto", "datasourceId": combined_source["id"],
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["templateId"] == template["id"] and len(result["widgets"]) == 2
    assert result["templateReference"]["id"] == template["id"] and result["templateAdapted"]
    assert result["semanticReview"]["aligned"] is True


def test_automatic_reference_adapts_finance_and_repairs_only_missing_scope_then_saves_once(
    client, administrator, combined_source, db, monkeypatch,
):
    template_id = "lynx-source-template-48"
    template = client.get(f"/api/v1/report-templates/{template_id}", headers=administrator).json()["data"]
    monkeypatch.setattr(generation, "list_templates", lambda ctx: [template])
    designs, queries = [], []
    execute = generation.execute_query

    def query(ctx, payload, **kwargs):
        queries.append(payload.query)
        return execute(ctx, payload, **kwargs)

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        assert context["requirement"] == ORIGINAL
        if "candidates" in context:
            return {"content": json.dumps({"templateId": None})}
        if context.get("task") == "review_report_semantics":
            assert len(context["widgets"]) == 2
            return {"content": json.dumps({"aligned": True, "reason": "财务应收与设备状态都有真实查询", "widgets": [
                {"widgetId": w["widgetId"], "aligned": True, "reason": "实际字段支持"} for w in context["widgets"]]})}
        designs.append(context)
        assert context["templateId"] is None and context["templateSlots"] == []
        assert context["templateReference"]["id"] == template_id
        assert "chartData" not in json.dumps(context["templateReference"])
        if len(designs) == 1:
            assert {s["scope"] for s in context["requiredSections"]} == {"finance", "equipment"}
            result = widgets(combined_source["id"])[1:]
        else:
            assert [s["scope"] for s in context["requiredSections"]] == ["finance"]
            result = widgets(combined_source["id"])[:1]
        return {"content": json.dumps({"title": "财务与设备大屏", "widgets": result})}

    monkeypatch.setattr(model, "chat_for_run", reply)
    monkeypatch.setattr(generation, "execute_query", query)
    payload = {"requirement": ORIGINAL, "datasourceIds": [combined_source["id"]],
               "templateMode": "auto", "requestKey": "reference-repair-once"}
    response = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert response.status_code in {200, 201}, response.text
    assert len(designs) == 2 and len(queries) == 2
    report = db.scalar(select(Report))
    assert report is not None and report.template_id == template_id and report.status == "draft"
    assert report.lineage["layoutCustomized"] and report.lineage["templateReference"]["id"] == template_id
    assert report.content["canvas"]["theme"] == template["content"]["canvas"]["theme"]
    assert report.content["canvas"]["styleLocked"] is False
    data_widgets = [w for w in report.content["widgets"] if w.get("generatedSql")]
    assert {w["id"] for w in data_widgets} == {"finance", "equipment"}
    assert all(w.get("dataStatus") == "verified" for w in data_widgets)
    assert client.get(f"/api/v1/report-templates/{template_id}", headers=administrator).json()["data"]["content"] == template["content"]
    repeated = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert repeated.status_code in {200, 201} and len(queries) == 2
    assert len(db.scalars(select(Report)).all()) == 1


def test_tool_payload_for_message_preserves_dashboard_draft_fields():
    from app.modules.runtime.agent import _tool_payload_for_message, _tool_payload_for_model
    from app.modules.business_ai.schemas import DashboardDraftView, BusinessActionLink
    from app.providers.tool.base import ToolResult

    view = DashboardDraftView(
        receiptId="rec-123", reportId="rep-456", generationId="gen-789",
        generationRunId="run-001", invocationRunId="inv-002",
        title="财务和设备数字大屏", version=1,
        templateMode="auto", templateId="lynx-48",
        datasourceIds=["ds-1"], datasourceNames=["szh_new"],
        queryIds=[f"q{i}" for i in range(11)],
        widgetCount=14, previewPath="/report/view/rep-456",
        actions=[BusinessActionLink(label="交互预览", href="/report/view/rep-456", kind="primary")],
        provenance={
            "source": "真实授权数据源与逐组件验证查询",
            "templateReference": {"id": "lynx-48", "name": "生产监控中心数据可视化"},
            "schemaSelection": [{"table": f"tbl_{i}", "columns": ["col"] * 20} for i in range(15)],
            "customerRequirement": {"req": "x" * 2000},
        },
        reused=False,
    )
    result = ToolResult(status="success", data=view.model_dump(mode="json"))
    msg_payload = _tool_payload_for_message(result)
    data = msg_payload["data"]
    assert "message_preview" not in data, "Dashboard card data must not be collapsed into message_preview"
    assert data["resultType"] == "dashboard"
    assert data["title"] == "财务和设备数字大屏"
    assert data["widgetCount"] == 14
    assert len(data["queryIds"]) == 11
    assert data["datasourceNames"] == ["szh_new"]
    assert data["reportId"] == "rep-456"
    assert data["previewPath"] == "/report/view/rep-456"
    assert len(data["actions"]) == 1

    model_payload = _tool_payload_for_model(result)
    assert model_payload["data"]["widgetCount"] == 14
    assert model_payload["data"]["title"] == "财务和设备数字大屏"


def test_chat_agent_honors_user_explicit_adaptive_layout_with_retained_template(
    client, administrator, combined_source, db, monkeypatch,
):
    stream_calls = []

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if "candidates" in context:
            return {"content": json.dumps({"templateId": None})}
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({
                "aligned": True, "reason": "财务与设备板块均覆盖",
                "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "真实数据"} for item in context["widgets"]]
            })}
        return {"content": json.dumps({"title": "财务与设备综合大屏", "widgets": widgets(combined_source["id"])})}

    def chat_reply(_db, run, messages, tools, temperature=0.2):
        stream_calls.append(1)
        # Verify that the system prompt informed the model of the template and the policy
        sys_msg = next(m["content"] for m in messages if m.get("role") == "system")
        assert "lynx-source-template-37" in sys_msg
        assert "auto" in sys_msg
        if len(stream_calls) == 1:
            yield "final", {"content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {
                "name": "create_business_dashboard", "arguments": json.dumps({
                    "requirement": ORIGINAL, "template_mode": "auto",
                    "datasource_ids": [combined_source["id"]],
                })}}], "usage": {}, "model": "local-chat-fixture"}
        else:
            yield "final", {"content": "已参考模板风格生成综合大屏。", "tool_calls": [], "usage": {}, "model": "local-chat-fixture"}

    monkeypatch.setattr(model, "chat_for_run", reply)
    monkeypatch.setattr(agent, "_llm_stream", chat_reply)
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json={
        "content": ORIGINAL + "采用自适应布局，参考已选模板风格。",
        "datasource_ids": [combined_source["id"]],
        "dashboard_template_mode": "manual",
        "dashboard_template_id": "lynx-source-template-37",
    })
    assert response.status_code == 200, response.text
    reports = db.scalars(select(Report)).all()
    assert len(reports) == 1, "Dashboard draft must be created successfully"
    assert reports[0].status == "draft"
    data_widgets = [w for w in reports[0].content["widgets"] if w.get("generatedSql")]
    assert len(data_widgets) == 2
    assert {w["id"] for w in data_widgets} == {"finance", "equipment"}


def test_manual_template_preserves_empty_unbound_components(
    client, administrator, combined_source, db, monkeypatch,
):
    from app.modules.reports.models import ReportTemplate
    template = create_custom_layout_template(client, administrator, "多槽位业务模板")
    widgets_list = template["content"]["widgets"]
    extra_widget = {
        "id": "w2_extra", "component": "JBar", "componentName": "未匹配预留槽位",
        "x": 600, "y": 80, "w": 400, "h": 300,
        "config": {"option": {"series": [{"type": "bar", "data": [10, 20, 30]}]}},
    }
    widgets_list.append(extra_widget)
    template["content"]["canvas"]["styleLocked"] = True
    t_row = db.scalar(select(ReportTemplate).where(ReportTemplate.id == template["id"]))
    t_row.content = template["content"]
    db.commit()

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({
                "aligned": True, "reason": "已绑定财务指标真实有效，空槽位合规预留",
                "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "真实数据"} for item in context["widgets"]]
            })}
        first_id = widgets_list[0]["id"]
        return {"content": json.dumps({
            "title": "部分绑定大屏",
            "bindings": [{
                "widgetId": first_id,
                "datasourceId": combined_source["id"],
                "sql": "SELECT SUM(amount) AS total FROM receivables",
                "parameters": {},
            }]
        })}

    monkeypatch.setattr(model, "chat_for_run", reply)
    payload = {
        "requirement": "生成财务看板",
        "datasourceIds": [combined_source["id"]],
        "templateMode": "manual",
        "templateId": template["id"],
        "requestKey": "partial-slots-empty-preserved",
        "width": 1280, "height": 720,
    }
    response = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert response.status_code in {200, 201}, response.text
    reports = db.scalars(select(Report)).all()
    assert len(reports) == 1
    report = reports[0]
    first_w = next(w for w in report.content["widgets"] if w["id"] == widgets_list[0]["id"])
    empty_w = next(w for w in report.content["widgets"] if w["id"] == "w2_extra")
    assert first_w["dataStatus"] == "verified"
    assert first_w.get("generatedSql") is not None
    assert empty_w["dataStatus"] == "unbound"
    assert empty_w.get("query") is None
    assert empty_w["config"]["chartData"] == "[]"
    assert empty_w["config"]["chartDataParsed"] == []


def test_manual_template_query_failure_gracefully_degrades_to_empty_slot(
    client, administrator, combined_source, db, monkeypatch,
):
    from app.errors import ToolExecutionError
    from app.modules.reports.models import ReportTemplate
    template = create_custom_layout_template(client, administrator, "容错降级模板")
    widgets_list = template["content"]["widgets"]
    extra_widget = {
        "id": "w2_failing", "component": "JBar", "componentName": "超时慢查询槽位",
        "x": 600, "y": 80, "w": 400, "h": 300,
        "config": {"option": {"series": [{"type": "bar", "data": []}]}},
    }
    widgets_list.append(extra_widget)
    template["content"]["canvas"]["styleLocked"] = True
    t_row = db.scalar(select(ReportTemplate).where(ReportTemplate.id == template["id"]))
    t_row.content = template["content"]
    db.commit()

    real_execute = generation.execute_query

    def fail_failing_query(ctx, query_input, **kwargs):
        if "FAIL_ME" in query_input.query:
            raise ToolExecutionError("连接或查询超时，请检查网络、查询范围与超时设置")
        return real_execute(ctx, query_input, **kwargs)

    monkeypatch.setattr(generation, "execute_query", fail_failing_query)

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({
                "aligned": True, "reason": "已验证组件数据真实有效，失败槽位合规置空",
                "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "真实数据"} for item in context["widgets"]]
            })}
        if "bindingError" in context:
            # Repair attempt also fails
            return {"content": json.dumps({
                "sql": "SELECT FAIL_ME_AGAIN FROM nonexistent",
                "parameters": {},
            })}
        first_id = widgets_list[0]["id"]
        return {"content": json.dumps({
            "title": "容错降级大屏",
            "bindings": [
                {
                    "widgetId": first_id,
                    "datasourceId": combined_source["id"],
                    "sql": "SELECT SUM(amount) AS total FROM receivables",
                    "parameters": {},
                },
                {
                    "widgetId": "w2_failing",
                    "datasourceId": combined_source["id"],
                    "sql": "SELECT FAIL_ME FROM nonexistent",
                    "parameters": {},
                },
            ]
        })}

    monkeypatch.setattr(model, "chat_for_run", reply)
    payload = {
        "requirement": "生成财务看板",
        "datasourceIds": [combined_source["id"]],
        "templateMode": "manual",
        "templateId": template["id"],
        "requestKey": "failing-slot-degrades-to-empty",
        "width": 1280, "height": 720,
    }
    response = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert response.status_code in {200, 201}, response.text
    reports = db.scalars(select(Report)).all()
    assert len(reports) == 1
    report = reports[0]
    first_w = next(w for w in report.content["widgets"] if w["id"] == widgets_list[0]["id"])
    failing_w = next(w for w in report.content["widgets"] if w["id"] == "w2_failing")
    assert first_w["dataStatus"] == "verified"
    assert first_w.get("generatedSql") is not None
    assert failing_w["dataStatus"] == "unbound"
    assert failing_w.get("query") is None
    assert failing_w["config"]["chartData"] == "[]"
    assert failing_w["config"]["chartDataParsed"] == []


def test_manual_template_adopts_requirement_title_and_widget_metric_names(
    client, administrator, combined_source, monkeypatch, db,
):
    template = create_custom_layout_template(client, administrator, "企业年度数据看板")

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({
                "aligned": True,
                "reason": "指标符合要求",
                "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "通过"} for item in context.get("widgets", [])]
            })}
        return {"content": json.dumps({
            "title": "财务和设备数字大屏",
            "bindings": [
                {
                    "widgetId": template["content"]["widgets"][0]["id"],
                    "metricName": "财务支出趋势",
                    "datasourceId": combined_source["id"],
                    "sql": "SELECT '2026-01' AS `category`, 100 AS `amount`",
                    "parameters": {},
                },
            ]
        })}

    monkeypatch.setattr(model, "chat_for_run", reply)
    payload = {
        "requirement": "生成财务和设备数字大屏，使用输入区已选择的真实数据与模板策略，并保存为唯一草稿。",
        "datasourceIds": [combined_source["id"]],
        "templateMode": "manual",
        "templateId": template["id"],
        "requestKey": "customer-title-requirement",
        "width": 1280, "height": 720,
    }
    response = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert response.status_code in {200, 201}, response.text
    reports = db.scalars(select(Report)).all()
    assert len(reports) == 1
    report = reports[0]
    assert report.name == "财务和设备数字大屏"
    first_w = next(w for w in report.content["widgets"] if w["id"] == template["content"]["widgets"][0]["id"])
    assert first_w.get("metricName") == "财务支出趋势"


def test_ring_chart_top5_query_auto_harmonizes_metric_name(
    client, administrator, combined_source, monkeypatch, db,
):
    from app.modules.reports.models import ReportTemplate
    template = create_custom_layout_template(client, administrator, "质量管理大屏")
    widgets_list = template["content"]["widgets"]
    ring_widget = {
        "id": "ring-1", "component": "JRing", "componentName": "质量结构占比",
        "title": "质量结构占比", "x": 800, "y": 96, "w": 400, "h": 300,
        "config": {"option": {"title": {"text": "质量结构占比"}}},
    }
    widgets_list.append(ring_widget)
    template["content"]["canvas"]["styleLocked"] = True
    t_row = db.scalar(select(ReportTemplate).where(ReportTemplate.id == template["id"]))
    t_row.content = template["content"]
    db.commit()

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({
                "aligned": True,
                "reason": "已验证组件口径与SQL一致",
                "widgets": [{"widgetId": item["widgetId"], "aligned": True, "reason": "通过"} for item in context.get("widgets", [])]
            })}
        return {"content": json.dumps({
            "title": "财务和设备数字大屏",
            "bindings": [
                {
                    "widgetId": "ring-1",
                    "metricName": "占比",
                    "datasourceId": combined_source["id"],
                    "sql": "SELECT customer, SUM(amount) AS total FROM receivables GROUP BY customer ORDER BY total DESC LIMIT 5",
                    "parameters": {},
                },
            ]
        })}

    monkeypatch.setattr(model, "chat_for_run", reply)
    payload = {
        "requirement": "生成财务和设备数字大屏，使用输入区已选择的真实数据与模板策略，并保存为唯一草稿。",
        "datasourceIds": [combined_source["id"]],
        "templateMode": "manual",
        "templateId": template["id"],
        "requestKey": "ring-top5-harmonize-test",
        "width": 1280, "height": 720,
    }
    response = client.post("/api/v1/ai/business-actions/dashboards/drafts", headers=administrator, json=payload)
    assert response.status_code in {200, 201}, response.text
    reports = db.scalars(select(Report)).all()
    assert len(reports) == 1
    report = reports[0]
    ring_w = next(w for w in report.content["widgets"] if w["id"] == "ring-1")
    assert "TOP5" in ring_w.get("metricName", "")
    assert "客户" in ring_w.get("metricName", "")
