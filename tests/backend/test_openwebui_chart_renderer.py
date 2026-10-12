"""可信图表的数值、来源授权和中文图片回归；不连接业务库或调用模型。"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_ENV"] = "test"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.errors import PermissionErrorEx, ValidationError
from app.modules.openwebui import chart_renderer as renderer


def result(rows=None, **changes):
    """提供具有明确完整性标记的合成查询结果。"""
    rows = rows if rows is not None else [{"状态": "已巡检", "设备数": 15}, {"状态": "未巡检", "设备数": 5}]
    return {"queryId": "query", "data": rows, "totalCount": len(rows), "truncated": False, "success": True, **changes}


def arguments(**changes):
    return {"query_id": "query", "chart_type": "pie", "label_column": "状态", "value_column": "设备数", **changes}


def receipt(**changes):
    return SimpleNamespace(tool_name="query_business_data", status="completed",
        result_json={"status": "success", "data": {"queryId": "query", "success": True}}, **changes)


def test_preserves_exact_query_values_without_model_supplied_data():
    spec = renderer.chart_data(result(), arguments(data=[{"状态": "伪造", "设备数": 999}]))
    assert spec["labels"] == ["已巡检", "未巡检"]
    assert spec["values"] == [15, 5]
    assert spec["source_columns"] == ["状态", "设备数"]


def test_single_row_aggregated_fields_are_explicit():
    spec = renderer.chart_data(result([{"已巡检": "15", "未巡检": "5"}]),
        {"chart_type": "pie", "value_columns": ["已巡检", "未巡检"]})
    assert spec["labels"] == ["已巡检", "未巡检"]
    assert spec["values"] == [15, 5]
    assert spec["data_mode"] == "single_row_metrics"


@pytest.mark.parametrize("value", [None, True, False, "NaN", "Infinity", float("nan"), float("inf"), {}, [], "未知", ""])
def test_invalid_numeric_values_are_rejected_instead_of_filled(value):
    with pytest.raises(ValidationError):
        renderer.chart_data(result([{"状态": "正常", "设备数": value}]), arguments())


@pytest.mark.parametrize("changes", [{"truncated": True}, {"truncated": None}, {"totalCount": 100}])
def test_incomplete_or_inconsistent_results_are_rejected(changes):
    with pytest.raises(ValidationError):
        renderer.chart_data(result(**changes), arguments())


@pytest.mark.parametrize("rows", [[], [{"状态": "正常", "设备数": 0}], [{"状态": "正常", "设备数": -1}],
    [{"状态": "正常", "设备数": 1}, {"状态": "正常", "设备数": 2}],
    [{"状态": None, "设备数": 1}], [{"状态": "正常", "数量": 1}]])
def test_ambiguous_or_impossible_pie_chart_is_not_fabricated(rows):
    with pytest.raises(ValidationError):
        renderer.chart_data(result(rows), arguments())


def test_bar_accepts_legitimate_zero_and_negative_values():
    spec = renderer.chart_data(result([{"状态": "变化", "设备数": -1}, {"状态": "不变", "设备数": 0}]), arguments(chart_type="bar"))
    assert spec["values"] == [-1, 0]


def test_pie_total_must_remain_finite():
    with pytest.raises(ValidationError, match="有限值"):
        renderer.chart_data(result([{"状态": "甲", "设备数": "1e308"}, {"状态": "乙", "设备数": "1e308"}]), arguments())


def test_large_pie_does_not_silently_drop_categories():
    with pytest.raises(ValidationError, match="分类太多"):
        renderer.chart_data(result([{"状态": str(index), "设备数": 1} for index in range(17)]), arguments())


@pytest.mark.parametrize("changes", [
    {"label_column": "不存在"}, {"value_column": "不存在"}, {"label_column": "设备数"},
    {"chart_type": "scatter"}, {"value_columns": ["已巡检", "未巡检"]},
])
def test_column_and_mode_errors_return_actionable_validation(changes):
    with pytest.raises(ValidationError):
        renderer.chart_data(result(), arguments(**changes))


@pytest.mark.parametrize("chart_type", ["pie", "bar", "line"])
def test_real_chinese_image_has_visible_content(chart_type):
    spec = renderer.chart_data(result(), arguments(chart_type=chart_type, title="近期设备巡检状态"))
    data = renderer.render_png(spec)
    with Image.open(BytesIO(data)) as picture:
        assert picture.format == "PNG"
        assert picture.width >= 1000 and picture.height >= 800
        extrema = picture.convert("RGB").getextrema()
        assert all(maximum - minimum > 100 for minimum, maximum in extrema)


def test_receipt_accepts_only_the_matching_successful_query():
    call = receipt()
    assert renderer._receipt_contains(call, "query")
    assert not renderer._receipt_contains(call, "other-query")
    call.status = "failed"
    assert not renderer._receipt_contains(call, "query")
    call.status = "completed"
    call.result_json["status"] = "failed"
    assert not renderer._receipt_contains(call, "query")


def test_multi_query_receipt_does_not_reuse_its_failed_item():
    call = receipt()
    call.result_json["data"] = {"results": [{"queryId": "query", "success": False}, {"queryId": "other", "success": True}]}
    assert not renderer._receipt_contains(call, "query")
    assert renderer._receipt_contains(call, "other")


@pytest.fixture
def authorized(monkeypatch):
    """模拟平台授权层和持久回执，测试过程没有任何数据库连接。"""
    from app.common import owned_records
    from app.modules.analytics import connections, sources
    from app.modules.business_ai import context
    from app.modules.openwebui import resume

    run = SimpleNamespace(id="chat-run", workspace_id="workspace", started_by_user_id="user", context_snapshot={"datasource_ids": ["source"]})
    query = SimpleNamespace(id="query", workspace_id="workspace", owner_user_id="user", status="success",
        datasource_id="source", datasource_version=1, result_json=result(), run_id="independent-analytics-run")
    calls = [receipt()]
    inherited = []
    db = SimpleNamespace(get=lambda model, identity: run if identity == run.id else None,
                         scalars=lambda expression: SimpleNamespace(all=lambda: calls))
    ctx = SimpleNamespace(db=db, workspace_id="workspace", user_id="user")
    permissions = []
    monkeypatch.setattr(context, "auth_context_from_tool", lambda execution: ctx)
    monkeypatch.setattr(owned_records, "owned_record", lambda actor, model, identity, permission: permissions.append(permission) or query)
    monkeypatch.setattr(sources, "get_source", lambda actor, identity, **kwargs: SimpleNamespace(id=identity))
    monkeypatch.setattr(connections, "source_version", lambda source: 1)
    monkeypatch.setattr(resume, "validated_resume_calls", lambda database, invocation: inherited)
    return SimpleNamespace(ctx=ctx, context={"run_id": run.id}, run=run, query=query,
                           calls=calls, inherited=inherited, permissions=permissions)


def test_validated_query_uses_chat_receipt_and_export_permission(authorized):
    row = renderer.load_authorized_query(authorized.ctx, authorized.context, "query")
    assert row is authorized.query
    assert row.run_id != authorized.run.id
    assert authorized.permissions == ["analytics.export"]


def test_loader_supports_read_permission_for_dashboard_reuse(authorized):
    renderer.load_authorized_query(authorized.ctx, authorized.context, "query", permission="analytics.view")
    assert authorized.permissions == ["analytics.view"]


def test_unrelated_history_cannot_be_guessed(authorized):
    authorized.calls.clear()
    with pytest.raises(ValidationError, match="本轮"):
        renderer.load_authorized_query(authorized.ctx, authorized.context, "query")
    assert not authorized.permissions


def test_explicit_validated_resume_receipt_is_accepted(authorized):
    authorized.inherited.extend(authorized.calls)
    authorized.calls.clear()
    assert renderer.load_authorized_query(authorized.ctx, authorized.context, "query") is authorized.query


@pytest.mark.parametrize(("field", "value"), [("workspace_id", "other"), ("owner_user_id", "other"),
    ("status", "failed"), ("datasource_id", "other"), ("datasource_version", 2)])
def test_ownership_current_scope_and_source_version_are_enforced(authorized, field, value):
    setattr(authorized.query, field, value)
    with pytest.raises((PermissionErrorEx, ValidationError)):
        renderer.load_authorized_query(authorized.ctx, authorized.context, "query")


def test_revoked_source_permission_is_not_bypassed(authorized, monkeypatch):
    from app.modules.analytics import sources

    def revoked(*args, **kwargs):
        raise PermissionErrorEx("数据源权限已撤销")

    monkeypatch.setattr(sources, "get_source", revoked)
    with pytest.raises(PermissionErrorEx, match="撤销"):
        renderer.load_authorized_query(authorized.ctx, authorized.context, "query")


def test_created_artifact_contains_server_evidence(authorized, monkeypatch):
    from app.modules.artifact import service

    saved = {}

    def create(database, **fields):
        saved.update(fields)
        return SimpleNamespace(id="chart-artifact")

    monkeypatch.setattr(service, "create_artifact", create)
    monkeypatch.setattr(service, "artifact_reference", lambda artifact: {"id": artifact.id})
    tool_result = renderer.render_chart_image(authorized.ctx, authorized.context, arguments())
    assert tool_result.status == "success"
    assert tool_result.artifacts == [{"id": "chart-artifact"}]
    assert saved["data"].startswith(b"\x89PNG")
    assert saved["metadata"]["tool"] == "render_chart_image"
    evidence = saved["metadata"]["delivery_evidence"]
    assert evidence["producer"] == "server_renderer.v1"
    assert evidence["source_query_id"] == "query"
    assert evidence["chart_types"] == ["pie"]
    assert evidence["scope"] == "saved_query_result"
    assert saved["run_id"] == "chat-run"
