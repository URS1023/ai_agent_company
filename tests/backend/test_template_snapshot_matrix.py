"""离线跨全部内置模板核验字段覆盖和可读容量，禁止连接数据库或调用模型。"""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.errors import ValidationError
from app.modules.chat_artifacts.template_query_snapshot import template_snapshot_content, _table_capacity
from app.modules.reports.content import builtin_templates
from app.modules.reports.template_lock import assert_visual_unchanged


TEMPLATES = builtin_templates()
MEASURES = ["temperature", "rpm", "reject_count", "pressure", "humidity"]


def _query(kind):
    """合成字段故意不使用金额命名，确保预检不局限于财务案例。"""
    values = dict(zip(MEASURES, [21.5, 1200, 3, 29.4, 65.2]))
    row = {"deviceId": 9876543210, **values}
    if kind == "unknown":
        row = {"deviceId": 9876543210, **{field: None for field in MEASURES}}
    grouped = kind in {"grouped", "truncated", "empty", "explicit"}
    rows = [dict(workcell="合成工位甲", **row), dict(workcell=None, **row)] if grouped else [row]
    columns = [{"columnName": field, "dataType": "varchar" if field == "workcell" else "decimal",
                "primaryKey": field == "deviceId"} for field in rows[0]]
    return SimpleNamespace(id="matrix-query", datasource_id="synthetic-source", query_mode="sql",
        generated_sql="SELECT temperature FROM synthetic", model_usage_json={}, created_at=datetime(2026, 9, 29),
        result_json={"data": [] if kind == "empty" else rows, "columns": columns, "truncated": kind == "truncated"})


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda item: item["id"])
@pytest.mark.parametrize("kind", ["summary", "grouped", "unknown", "truncated", "empty", "explicit"])
def test_every_builtin_template_either_covers_required_fields_or_rejects_before_delivery(template, kind):
    query = _query(kind)
    before_template, before_result = deepcopy(template), deepcopy(query.result_json)
    ctx = SimpleNamespace(workspace_id="synthetic", db=SimpleNamespace(scalar=lambda statement: None))
    bindings = [{"query_id": query.id, "value_field": "pressure", "label_field": "workcell"}] if kind == "explicit" else None
    try:
        content, scopes = template_snapshot_content(ctx, "合成设备指标", [query], template, bindings)
    except ValidationError as rejected:
        if template["id"] in {"lynx-source-template-37", "lynx-source-template-42"} or (
                template["id"] == "lynx-source-template-43" and kind in {"summary", "unknown", "explicit"}):
            pytest.fail(f"已知可容纳的模板意外拒绝：{template['id']} / {kind}: {rejected.message}")
        assert rejected.extra["execution_started"] is False
        assert rejected.extra["recovery"] == "correct_input"
        assert rejected.extra["query_id"] == query.id
        assert rejected.extra.get("missing_fields") or rejected.extra.get("compatible_components")
        assert template == before_template and query.result_json == before_result
        return
    assert template == before_template and query.result_json == before_result
    assert_visual_unchanged(template["content"], content)
    bound = [widget for widget in content["widgets"] if widget.get("dataStatus") == "verified"]
    assert bound
    actual_fields = set()
    for widget in bound:
        config = widget["config"]
        assert config["chartDataParsed"] == widget["data"] == query.result_json["data"]
        assert config["dataType"] == 1 and config["snapshotOnly"] is True
        assert config["truncated"] == query.result_json["truncated"]
        binding = config["snapshotBinding"]
        if widget["component"] in {"JTable", "JScrollBoard"}:
            displayed = binding.get("columns") or [column["columnName"] for column in query.result_json["columns"]]
            assert len(displayed) <= _table_capacity(widget, content["canvas"]["width"])
        else:
            displayed = list(filter(None, [binding.get("valueField"), binding.get("labelField")]))
            assert binding.get("valueField") != "deviceId"
            assert kind not in {"truncated", "empty"}
        actual_fields.update(displayed)
        scope = next(scope for scope in scopes if scope["widgetId"] == widget["id"])
        assert set(scope["displayedFields"]) == set(displayed)
    required = {"pressure", "workcell"} if kind == "explicit" else set(MEASURES)
    if kind in {"grouped", "truncated", "empty"}:
        required.add("workcell")
    assert required <= actual_fields
    for scope in scopes:
        assert set(scope["requiredFields"]) <= actual_fields


def test_matrix_includes_both_small_and_large_canvas_contracts():
    """避免模板目录变化后矩阵退化成只有一种画布尺寸。"""
    widths = {template["content"]["canvas"]["width"] for template in TEMPLATES}
    assert min(widths) <= 652 and max(widths) >= 1920
    assert len(widths) >= 10


def test_explicit_value_can_use_a_two_column_table_without_forcing_all_optional_columns():
    template = deepcopy(next(item for item in TEMPLATES if item["id"] == "lynx-source-template-43"))
    table = next(widget for widget in template["content"]["widgets"] if widget["component"] == "JScrollBoard")
    template["content"]["widgets"] = [table]
    query = _query("explicit")
    ctx = SimpleNamespace(workspace_id="synthetic", db=SimpleNamespace(scalar=lambda statement: None))
    content, scopes = template_snapshot_content(ctx, "压力明细", [query], template,
        [{"query_id": query.id, "value_field": "pressure", "label_field": "workcell"}])
    assert content["widgets"][0]["config"]["snapshotBinding"]["columns"] == ["workcell", "pressure"]
    assert scopes[0]["requiredFields"] == ["workcell", "pressure"]
    with pytest.raises(ValidationError, match="兼容数据槽不足"):
        template_snapshot_content(ctx, "全部设备指标", [query], template)


def test_wide_table_expands_existing_projection_to_preserve_all_default_measures():
    template = next(item for item in TEMPLATES if item["id"] == "lynx-source-template-42")
    query = _query("summary")
    ctx = SimpleNamespace(workspace_id="synthetic", db=SimpleNamespace(scalar=lambda statement: None))
    content, scopes = template_snapshot_content(ctx, "全部设备指标", [query], template)
    table = next(widget for widget in content["widgets"] if widget["component"] == "JScrollBoard")
    assert table["config"]["snapshotBinding"]["columns"] == list(query.result_json["data"][0])
    assert len(table["config"]["snapshotBinding"]["columns"]) <= _table_capacity(table, content["canvas"]["width"])
    assert set(MEASURES) <= {field for scope in scopes for field in scope["displayedFields"]}


@pytest.mark.parametrize("change", [{"visible": False}, {"x": -1}, {"x": 651}, {"y": 367}, {"w": 0}])
def test_hidden_or_outside_slots_are_rejected_before_any_binding(change):
    template = deepcopy(next(item for item in TEMPLATES if item["id"] == "lynx-source-template-43"))
    metric = next(widget for widget in template["content"]["widgets"] if widget["id"] == "showcase-14-widget-4")
    metric.update(change)
    template["content"]["widgets"] = [metric]
    query = _query("summary")
    ctx = SimpleNamespace(workspace_id="synthetic", db=SimpleNamespace(scalar=lambda statement: None))
    with pytest.raises(ValidationError, match="兼容数据槽不足") as rejected:
        template_snapshot_content(ctx, "压力", [query], template,
                                  [{"query_id": query.id, "value_field": "pressure"}])
    assert rejected.value.extra["execution_started"] is False


def test_hidden_primary_card_falls_back_to_visible_widgets_without_losing_fields():
    template = deepcopy(next(item for item in TEMPLATES if item["id"] == "lynx-source-template-43"))
    next(widget for widget in template["content"]["widgets"] if widget["id"] == "showcase-14-widget-4")["visible"] = False
    query = _query("summary")
    ctx = SimpleNamespace(workspace_id="synthetic", db=SimpleNamespace(scalar=lambda statement: None))
    content, scopes = template_snapshot_content(ctx, "设备指标", [query], template)
    hidden = next(widget for widget in content["widgets"] if widget["id"] == "showcase-14-widget-4")
    assert hidden.get("dataStatus") != "verified"
    assert set(MEASURES) <= {field for scope in scopes for field in scope["displayedFields"]}


def test_invalid_declared_numeric_field_cannot_silently_leave_default_required_scope():
    template = next(item for item in TEMPLATES if item["id"] == "lynx-source-template-43")
    query = _query("grouped")
    query.result_json["data"][1]["pressure"] = "N/A"
    before = deepcopy(query.result_json)
    ctx = SimpleNamespace(workspace_id="synthetic", db=SimpleNamespace(scalar=lambda statement: None))
    with pytest.raises(ValidationError, match="声明为数值") as rejected:
        template_snapshot_content(ctx, "全部设备指标", [query], template)
    assert rejected.value.extra["invalid_numeric_fields"] == ["pressure"]
    assert rejected.value.extra["execution_started"] is False
    assert query.result_json == before
    # 明确只选另一个有效指标时，无须把未请求的异常指标扩进本次展示范围。
    _, scopes = template_snapshot_content(ctx, "只看转速", [query], template,
        [{"query_id": query.id, "value_field": "rpm", "label_field": "workcell"}])
    assert all("pressure" not in scope["requiredFields"] for scope in scopes)
