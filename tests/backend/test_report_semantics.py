"""Executable SQL must not silently change the requested business meaning."""
import json

import pytest
from sqlalchemy import select

from app.models import Run
from app.modules.reports.models import ReportGeneration
from app.providers import model
from test_analytics import source
from test_reports import REPORTS, create_custom_layout_template, fake_generated


@pytest.mark.parametrize("verdict", [
    "aligned", "wrong_dimension", "wrong_metric", "wrong_title", "missing_requirement",
    "missing_widget", "unknown_widget", "duplicate_widget", "string_boolean", "missing_reason", "empty",
])
@pytest.mark.parametrize("template_mode", ["none", "manual"])
def test_final_semantic_review_controls_generation_status(
    client, administrator, source, monkeypatch, db, verdict, template_mode,
):
    template = create_custom_layout_template(client, administrator) if template_mode == "manual" else None
    candidate = fake_generated(source["id"])
    if verdict == "wrong_dimension":
        candidate["widgets"][0]["query"]["sql"] = "SELECT id AS department, SUM(amount) AS total FROM sales GROUP BY id"
    elif verdict == "wrong_metric":
        candidate["widgets"][0]["query"]["sql"] = "SELECT department, COUNT(*) AS total FROM sales GROUP BY department"
    elif verdict == "wrong_title" and not template:
        candidate["widgets"][0]["config"]["option"]["title"] = "地区销售金额"
    calls = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if len(calls) == 1:
            if template:
                return {"content": json.dumps({"title": "部门金额", "bindings": [{
                    "widgetId": item["id"], "datasourceId": source["id"],
                    "sql": candidate["widgets"][0]["query"]["sql"],
                } for item in context["templateSlots"]]})}
            return {"content": json.dumps(candidate)}
        assert context["task"] == "review_report_semantics"
        assert "部门" in context["requirement"]
        assert [item["id"] for item in context["sources"]] == [source["id"]]
        assert "GROUP BY" in context["widgets"][0]["query"]["sql"].upper()
        assert "department" in context["widgets"][0]["query"]["sql"].lower()
        assert "data" not in context["widgets"][0]  # No query results sent to the reviewer.
        review = {"aligned": True, "reason": "部门字段与金额汇总覆盖需求", "widgets": [{
            "widgetId": item["widgetId"], "aligned": True, "reason": "department 分组、SUM(amount) 汇总",
        } for item in context["widgets"]]}
        if verdict in {"wrong_dimension", "wrong_metric", "wrong_title"}:
            review["widgets"][0].update(aligned=False, reason="统计维度或指标标题不符合原始需求")
        elif verdict == "missing_requirement":
            review.update(aligned=False, reason="缺少用户指定的部门汇总")
        elif verdict == "missing_widget":
            review["widgets"] = []
        elif verdict == "unknown_widget":
            review["widgets"][0]["widgetId"] = "not-requested"
        elif verdict == "duplicate_widget":
            review["widgets"] *= 2
        elif verdict == "string_boolean":
            review["aligned"] = "true"
        elif verdict == "missing_reason":
            review["widgets"][0]["reason"] = ""
        elif verdict == "empty":
            review = {}
        return {"content": json.dumps(review)}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "按部门统计销售金额", "templateMode": template_mode,
        "templateId": template["id"] if template else None, "datasourceId": source["id"],
    })
    assert len(calls) == 2, (response.text, calls)
    record = db.scalar(select(ReportGeneration))
    run = db.get(Run, record.usage["runId"])
    if verdict == "aligned":
        assert response.status_code == 200, response.text
        assert record.status == run.status == "completed"
        assert response.json()["data"]["semanticReview"]["aligned"] is True
        assert record.usage["calls"][-1]["mode"] == "semantic_review"
    else:
        assert response.status_code in {422, 502}, response.text
        assert record.status == run.status == "failed"
        assert record.result_json["success"] is False
        assert record.result_json["stage"] == "semantic_review"
        assert "semanticReview" not in record.result_json
        assert not any(event["phase"] == "completed" for event in record.events)
        if verdict in {"wrong_dimension", "wrong_metric", "wrong_title", "missing_requirement"}:
            assert record.usage["semanticReview"]


def test_reviewer_sees_final_repaired_sql_and_rejects_metric_substitution(
    client, administrator, source, monkeypatch, db,
):
    candidate = fake_generated(source["id"])
    candidate["widgets"][0]["query"]["sql"] = "SELECT missing_amount FROM sales"
    calls = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if len(calls) == 1:
            return {"content": json.dumps(candidate)}
        if len(calls) == 2:
            return {"content": json.dumps({"datasourceId": source["id"],
                                          "sql": "SELECT department, COUNT(*) AS total FROM sales GROUP BY department"})}
        assert "COUNT(*)" in context["widgets"][0]["query"]["sql"]
        assert "missing_amount" not in context["widgets"][0]["query"]["sql"]
        return {"content": json.dumps({"aligned": False, "reason": "不能用订单数代替销售金额", "widgets": [{
            "widgetId": context["widgets"][0]["widgetId"], "aligned": False, "reason": "需要 SUM(amount)，实际使用 COUNT(*)",
        }]})}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "按部门统计销售金额", "templateMode": "none", "datasourceId": source["id"],
    })
    assert response.status_code == 422, response.text
    assert "不能用订单数代替销售金额" in response.json()["message"]
    assert len(calls) == 3
    record = db.scalar(select(ReportGeneration))
    assert record.status == "failed"
    assert record.result_json["success"] is False
    assert record.result_json["stage"] == "semantic_review"


def test_reviewer_receives_final_pie_bindings_without_query_rows(
    client, administrator, source, monkeypatch,
):
    candidate = fake_generated(source["id"])
    chart = candidate["widgets"][0]
    chart.update(component="JPie", componentName="部门金额占比")
    chart["config"]["option"]["title"] = "部门金额占比"
    # The candidate still says bar; review must see the final rendered pie.
    calls = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if len(calls) == 1:
            return {"content": json.dumps(candidate)}
        binding = context["widgets"][0]
        return {"content": json.dumps({
            "aligned": True, "reason": "部门金额构成由实际饼图系列表达", "widgets": [{
                "widgetId": binding["widgetId"], "aligned": True,
                "reason": "SUM(amount) 按 department 汇总，各部门除以同系列总额表达构成占比",
            }],
        })}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": "按部门展示全部销售金额的构成占比饼图", "templateMode": "none",
        "datasourceId": source["id"],
    })
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    binding = calls[1]["widgets"][0]
    assert binding["visualization"]["component"] == "JPie"
    assert binding["visualization"]["series"][0]["type"] == "pie"
    assert binding["visualization"]["series"][0]["encode"] == {
        "itemName": "department", "value": "total",
    }
    assert "SUM(" in binding["query"]["sql"] and "amount" in binding["query"]["sql"]
    assert "data" not in binding
    assert "data" not in binding["visualization"]["series"][0]
    assert "dataset" not in binding["visualization"]
    result = response.json()["data"]
    assert result["semanticReview"]["aligned"] is True
    assert result["widgets"][0]["config"]["option"]["series"][0]["data"]
