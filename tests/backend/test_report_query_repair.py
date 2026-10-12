"""Generation repairs must preserve the requested metric, never invent a substitute."""
import json

import pytest
from sqlalchemy import select

from app.modules.reports.models import ReportGeneration
from app.providers import model
from test_analytics import source
from test_reports import REPORTS, create_custom_layout_template, fake_generated

pytestmark = pytest.mark.usefixtures("approved_report_semantics")


@pytest.mark.parametrize("repair", ["valid", "unchanged", "other_source", "still_invalid", "clarification"])
def test_query_repair_keeps_metric_and_authorized_source(client, administrator, source, monkeypatch, db, repair):
    candidate = fake_generated(source["id"])
    original_sql = "SELECT department, SUM(missing_amount) AS total FROM sales GROUP BY department"
    candidate["widgets"][0]["query"]["sql"] = original_sql
    calls = []

    def respond(profile, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": json.dumps(candidate)}
        context = json.loads(messages[1]["content"])
        assert context["widget"]["name"] == "部门金额"
        assert context["originalQuery"]["sql"] == original_sql
        assert [item["id"] for item in context["sources"]] == [source["id"]]
        result = {"datasourceId": source["id"], "sql": original_sql.replace("missing_amount", "amount")}
        if repair == "unchanged":
            result["sql"] = original_sql
        elif repair == "other_source":
            result["datasourceId"] = "unauthorized-source"
        elif repair == "still_invalid":
            result["sql"] = original_sql.replace("missing_amount", "another_missing_field")
        elif repair == "clarification":
            result = {"clarification": "缺少金额字段，不能用记录数代替部门金额"}
        return {"content": json.dumps(result)}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "按部门统计销售金额", "templateMode": "none", "datasourceId": source["id"],
    })
    record = db.scalar(select(ReportGeneration))
    if repair == "valid":
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        chart = result["widgets"][0]
        assert chart["componentName"] == "部门金额"
        assert chart["query"]["sql"] == chart["generatedSql"] == chart["config"]["sql"]
        assert "missing_amount" not in chart["query"]["sql"]
        assert result["queryCorrections"][0]["mode"] == "model_repair"
        assert chart["config"]["option"]["series"][0]["data"] != [99999999]
        assert record.status == "completed"
    else:
        assert response.status_code in {422, 502}, response.text
        assert record.status == "failed" and record.result_json == {}
        if repair == "clarification":
            assert "缺少金额字段" in response.json()["message"]
    assert len(calls) <= 4


@pytest.mark.parametrize("can_repair", [False, True])
def test_missing_template_binding_is_not_replaced_with_random_count(client, administrator, source, monkeypatch, db, can_repair):
    template = create_custom_layout_template(client, administrator, "部门金额验收模板")
    calls = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        bindings = []
        if can_repair and len(calls) == 2:
            assert context["missingWidgetIds"]
            bindings = [{"widgetId": identity, "datasourceId": source["id"],
                         "sql": "SELECT department, SUM(amount) AS total FROM sales GROUP BY department"}
                        for identity in context["missingWidgetIds"]]
        return {"content": json.dumps({"title": "部门金额", "bindings": bindings})}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "部门金额", "templateMode": "manual", "templateId": template["id"],
        "datasourceId": source["id"],
    })
    record = db.scalar(select(ReportGeneration))
    assert len(calls) == 2
    if can_repair:
        assert response.status_code == 200, response.text
        assert record.status == "completed"
        assert {row["department"]: row["total"] for row in response.json()["data"]["widgets"][0]["data"]} == {
            "设备": 900, "财务": 200,
        }
    else:
        assert response.status_code == 502, response.text
        assert "缺少查询" in response.json()["message"]
        assert record.status == "failed" and record.result_json == {}
        assert not any(event["phase"] == "query" for event in record.events)
