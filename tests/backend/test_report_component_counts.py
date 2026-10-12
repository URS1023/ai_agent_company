"""Respect explicit layout size, including decorative components."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from app.errors import ValidationError
from app.modules.reports.generation_constraints import explicit_component_count
from app.modules.reports.models import ReportGeneration
from app.providers import model
from test_analytics import source
from test_reports import REPORTS, create_custom_layout_template, fake_generated


@pytest.mark.parametrize("text,count,scope", [
    ("只生成总金额、地区柱状图、占比图和排名表四个组件，使用 sales.region 和 amount。", 4, "all"),
    ("仅生成 4 个数据组件，可以另加标题。", 4, "data"),
    ("生成两个组件", 2, "all"),
    ("只生成十二个组件", 12, "all"),
    ("总共二十五个组件", 25, "all"),
    ("生成一百个组件", 100, "all"),
    ("Only generate four widgets", 4, "all"),
    ("Exactly 4 data components", 4, "data"),
    ("只生成四个组件，不要额外标题", 4, "all"),
    ("统计2026-09-01的数据，生成4个组件，每个组件绑定真实数据", 4, "all"),
    ("至少显示前十名销售额，只生成四个组件", 4, "all"),
    ("只要四个组件", 4, "all"),
    ("上次只生成四个组件，这次生成两个组件", 2, "all"),
])
def test_explicit_counts(text, count, scope):
    result = explicit_component_count(text)
    assert result.as_dict() == {"count": count, "scope": scope}


@pytest.mark.parametrize("text", [
    "查询销售金额前四名", "显示最近30天数据", "最多生成4个组件", "至少生成4个组件",
    "不要生成4个组件", "只生成3到5个组件", "每个地区生成4个组件", "Only show top 4 regions",
    "不要只生成四个组件，要完整的部门经营大屏",
    "原模板只有四个组件，请按新需求重新生成",
    "之前只生成四个组件，请按新需求设计",
])
def test_metric_limits_ranges_and_per_group_counts_are_not_exact_layout_counts(text):
    assert explicit_component_count(text) is None


@pytest.mark.parametrize("text", ["只生成101个组件", "生成零个组件", "只生成四个组件；总共五个组件"])
def test_invalid_or_conflicting_count_is_reported(text):
    with pytest.raises(ValidationError):
        explicit_component_count(text)


@pytest.mark.parametrize("scope,expected_calls", [("all", 0), ("data", 1)])
def test_review_enforces_explicit_scope_before_model_can_reinterpret_it(monkeypatch, scope, expected_calls):
    from app.modules.reports import generation

    widgets = [
        {"id": "metric", "component": "JStatsSummary", "generatedSql": "SELECT SUM(amount) FROM sales",
         "query": {"sql": "SELECT SUM(amount) FROM sales"}},
        {"id": "title", "component": "JText", "componentName": "额外标题"},
    ]
    calls = []

    def approve(ctx, system, context):
        calls.append(context)
        return {"aligned": True, "reason": "指标正确", "widgets": [
            {"widgetId": "metric", "aligned": True, "reason": "金额汇总"},
        ]}, {}

    monkeypatch.setattr(generation, "_call_model", approve)
    requirement = f"只生成一个{'数据' if scope == 'data' else ''}组件"
    if scope == "all":
        with pytest.raises(ValidationError, match="实际 2 个"):
            generation._review_semantics(None, requirement, {}, [], widgets, "金额")
    else:
        review, _ = generation._review_semantics(None, requirement, {}, [], widgets, "金额")
        assert review["aligned"] is True
        assert calls[0]["componentCountConstraint"] == {"count": 1, "scope": "data"}
    assert len(calls) == expected_calls


def _candidate(source_id, data_count, with_decoration=True):
    candidate = fake_generated(source_id)
    base = candidate["widgets"][0]
    candidate["widgets"] = [{**deepcopy(base), "id": f"chart-{index}"} for index in range(data_count)]
    if with_decoration:
        candidate["widgets"].append({"id": "extra-title", "component": "JText", "componentName": "额外标题",
                                     "x": 20, "y": 20, "w": 600, "h": 40,
                                     "config": {"option": {"body": {"text": "部门金额"}}}})
    return candidate


@pytest.mark.parametrize("scope,repair_ok", [("all", True), ("all", False), ("data", True)])
def test_generation_repairs_or_rejects_count_and_reviews_decorations(
    client, administrator, source, monkeypatch, db, scope, repair_ok,
):
    initial = _candidate(source["id"], 2)
    contexts = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        contexts.append(context)
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({"aligned": True, "reason": "布局与金额需求一致", "widgets": [
                {"widgetId": item["widgetId"], "aligned": True, "reason": "按部门汇总销售金额"}
                for item in context["widgets"]
            ]})}
        if context.get("repairInstruction") and repair_ok:
            return {"content": json.dumps(_candidate(source["id"], 2, with_decoration=False))}
        return {"content": json.dumps(initial)}

    monkeypatch.setattr(model, "chat_for_run", respond)
    requirement = f"只生成两个{'数据' if scope == 'data' else ''}组件，按部门统计销售金额。"
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": requirement, "templateMode": "none", "datasourceId": source["id"],
    })
    record = db.scalar(select(ReportGeneration))
    assert contexts[0]["componentCountConstraint"] == {"count": 2, "scope": scope}
    repairs = [item for item in contexts if item.get("repairInstruction")]
    assert len(repairs) == (1 if scope == "all" else 0)
    if not repair_ok:
        assert response.status_code == 502, response.text
        assert record.status == "failed" and record.result_json == {}
        assert not any(item.get("task") == "review_report_semantics" for item in contexts)
        return
    assert response.status_code == 200, response.text
    expected = 2 if scope == "all" else 3
    assert len(response.json()["data"]["widgets"]) == expected
    assert len(contexts[-1]["layoutInventory"]) == expected
    assert sum(item["hasDataQuery"] for item in contexts[-1]["layoutInventory"]) == 2
    assert record.status == "completed"


def test_manual_template_count_conflict_does_not_modify_template(
    client, administrator, source, monkeypatch,
):
    template = create_custom_layout_template(client, administrator)
    count = len(template["content"]["widgets"]) + 1
    called = []
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: called.append(True))
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": f"按部门统计销售金额，只生成{count}个组件", "templateMode": "manual",
        "templateId": template["id"], "datasourceId": source["id"],
    })
    assert response.status_code == 422, response.text
    assert "模板" in response.json()["message"] and "不符" in response.json()["message"]
    assert not called


def test_automatic_template_conflict_keeps_explicit_count_with_visual_reference(
    client, administrator, source, monkeypatch,
):
    template = create_custom_layout_template(client, administrator)
    count = len(template["content"]["widgets"]) + 1
    calls = []

    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if "candidates" in context:
            return {"content": json.dumps({"templateId": template["id"], "reason": "部门金额模板"})}
        if context.get("task") == "review_report_semantics":
            return {"content": json.dumps({"aligned": True, "reason": "单组件覆盖需求", "widgets": [
                {"widgetId": item["widgetId"], "aligned": True, "reason": "部门金额"}
                for item in context["widgets"]
            ]})}
        return {"content": json.dumps(_candidate(source["id"], count, with_decoration=False))}

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(REPORTS + "/screen/ai-generate", headers=administrator, json={
        "theme": f"按部门汇总销售金额，只生成{count}个组件", "templateMode": "auto",
        "datasourceId": source["id"],
    })
    assert response.status_code == 200, response.text
    assert calls[1]["templateSlots"] == [] and calls[1]["templateId"] is None
    result = response.json()["data"]
    # A compatible visual reference may survive; its original slot count must not.
    if result["templateId"]:
        assert result["templateAdapted"] is True
    assert len(result["widgets"]) == count
