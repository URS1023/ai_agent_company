"""Lynx-style component materialization uses isolated SQLite and deterministic models."""
import json

import pytest

from app.modules.reports import generation
from app.modules.reports.generation_requirements import financial_measure_columns, table_scope_score
from app.providers import model
from test_dashboard_requirement_integrity import ORIGINAL, combined_source, widgets

pytestmark = pytest.mark.usefixtures("approved_report_semantics")


def test_finance_selection_prefers_payment_amounts_over_attachment_archives():
    attachments = [{"tableName": f"t_caiwulirunbaobiao_{i}", "tableComment": "财务成本利润报表", "columns": [
        {"columnName": "c_baobiaofujian", "comment": "报表附件", "dataType": "LONGTEXT"},
    ]} for i in range(30)]
    payments = {"tableName": "t_fundpaymentapprovalform", "tableComment": "资金支付审批表", "columns": [
        {"columnName": "c_requestPament", "comment": "本次申请支付金额", "dataType": "DECIMAL(18,6)"},
        {"columnName": "c_contractAmount", "comment": "合同金额", "dataType": "DECIMAL(18,6)"},
        {"columnName": "c_costUnit", "comment": "费用单位", "dataType": "SMALLINT"},
        {"columnName": "paymentStatus", "comment": "支付状态", "dataType": "INTEGER"},
    ]}
    equipment = [{"tableName": f"device_inspection_{i}", "tableComment": "设备巡检", "columns": [
        {"columnName": "id", "dataType": "INTEGER"},
    ]} for i in range(30)]
    compact = generation._compact_schemas([{"id": "local", "tables": [*attachments, *equipment, payments]}], ORIGINAL)[0]
    selected = {t["tableName"]: t for t in compact["tables"]}
    assert selected[payments["tableName"]]["financialMeasureFields"] == ["c_requestPament", "c_contractAmount"]
    assert table_scope_score(payments, "finance") > table_scope_score(attachments[0], "finance")
    assert any(name.startswith("device_inspection_") for name in selected)
    assert len(selected) <= 24
    assert financial_measure_columns(attachments[0]) == []


@pytest.mark.parametrize("format", ["missing_sql", "top_level", "config", "infer_single_source"])
def test_widget_bindings_normalize_or_materialize_without_restarting_screen(
    client, administrator, combined_source, monkeypatch, format,
):
    planned = widgets(combined_source["id"])
    sql = planned[1]["query"]["sql"]
    if format == "missing_sql":
        planned[1].pop("query")
    elif format == "top_level":
        planned[1].update(planned[1].pop("query"))
    elif format == "config":
        planned[1]["config"].update(planned[1].pop("query"))
    else:
        planned[1]["query"].pop("datasourceId")
    for i, component in enumerate(["JAmbientGlow", "JDataConstellation", "JFlowRibbon", "JSignalRail"]):
        planned.append({"id": f"decoration-{i}", "component": component, "x": 0, "y": 0, "w": 1920, "h": 1080, "config": {}})
    contexts, queries = [], []
    execute = generation.execute_query

    def query(ctx, payload, **kwargs):
        queries.append(payload.query)
        return execute(ctx, payload, **kwargs)

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        contexts.append(context)
        if context.get("task") == "materialize_report_widget":
            assert context["widget"]["id"] == "equipment"
            assert context["requirement"] == ORIGINAL
            assert [s["id"] for s in context["sources"]] == [combined_source["id"]]
            return {"content": json.dumps({"sql": sql, "parameters": {}})}
        return {"content": json.dumps({"title": "财务与设备", "widgets": planned})}

    monkeypatch.setattr(model, "chat_for_run", reply)
    monkeypatch.setattr(generation, "execute_query", query)
    response = client.post("/api/v1/reports/screen/ai-generate", headers=administrator, json={
        "theme": ORIGINAL, "templateMode": "none", "datasourceId": combined_source["id"],
    })
    assert response.status_code == 200, response.text
    result = {w["id"]: w for w in response.json()["data"]["widgets"]}
    assert result["finance"]["data"] == [{"total": 200}]
    assert {row["status"]: row["total"] for row in result["equipment"]["data"]} == {"运行": 1, "维修": 1}
    assert queries[1] == sql
    assert result["equipment"]["query"]["sql"] == result["equipment"]["generatedSql"]
    assert len(queries) == 2
    assert len(contexts) == (2 if format == "missing_sql" else 1)
    assert all(not result[f"decoration-{i}"].get("generatedSql") for i in range(4))


def test_missing_scope_can_supply_a_planned_widget_before_its_sql_is_materialized(
    client, administrator, combined_source, monkeypatch,
):
    planned = widgets(combined_source["id"])
    planned[0]["businessScope"], planned[1]["businessScope"] = "finance", "equipment"
    equipment_sql = planned[1].pop("query")["sql"]
    stages = []
    template = client.get("/api/v1/report-templates/lynx-source-template-48", headers=administrator).json()["data"]
    monkeypatch.setattr(generation, "list_templates", lambda ctx: [template])

    def reply(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        if "candidates" in context:
            return {"content": json.dumps({"templateId": None})}
        stages.append(context.get("task", "scope_repair" if context.get("repairInstruction") else "design"))
        if context.get("task") == "materialize_report_widget":
            return {"content": json.dumps({"sql": equipment_sql})}
        chosen = planned[1:] if context.get("repairInstruction") else planned[:1]
        return {"content": json.dumps({"title": "财务设备", "widgets": chosen})}

    monkeypatch.setattr(model, "chat_for_run", reply)
    response = client.post("/api/v1/reports/screen/ai-generate", headers=administrator, json={
        "theme": ORIGINAL, "templateMode": "auto",
        "datasourceId": combined_source["id"],
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert stages == ["design", "scope_repair", "materialize_report_widget"]
    assert {w["id"] for w in result["widgets"] if w.get("generatedSql")} == {"finance", "equipment"}
    assert result["templateId"] == "lynx-source-template-48" and result["templateAdapted"]
