"""大屏图片只读取有权限的历史结果，导出真实快照及显示范围。"""
from io import BytesIO
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.errors import PermissionErrorEx, ValidationError
from app.modules.openwebui.dashboard_image import dashboard_panels, render_snapshot, render_dashboard_image


def report(rows=None, query_id="query-1"):
    """构造一个带查询来源的保存组件。"""
    return SimpleNamespace(id="report-1", name="设备巡检", version=3, datasource_ids=["source-1"],
        content={"widgets": [{"componentName": "巡检状态", "data": rows if rows is not None else [{"状态": "未检", "数量": 3}],
                               "config": {"queryId": query_id}}]})


def test_export_uses_history_without_reexecuting_query(monkeypatch):
    from app.modules.analytics import queries
    calls = []

    def historical(ctx, query_id, permission):
        calls.append((query_id, permission))
        return SimpleNamespace(datasourceId="source-1", columns=[SimpleNamespace(columnName=name) for name in ["状态", "数量"]],
                               data=[{"状态": "未检", "数量": 3}], truncated=False)

    def no_query(*args, **kwargs):
        raise AssertionError("导出不得执行 SQL")

    monkeypatch.setattr(queries, "historical_result", historical)
    monkeypatch.setattr(queries, "execute_query", no_query)
    panels = dashboard_panels(SimpleNamespace(), report())
    assert calls == [("query-1", "analytics.export")]
    assert panels[0]["rows"] == [{"状态": "未检", "数量": 3}]
    data = render_snapshot("设备巡检", 3, panels)
    with Image.open(BytesIO(data)) as picture:
        assert picture.size[0] == 1600
        assert picture.getextrema()[0][0] != picture.getextrema()[0][1]


def test_untraceable_data_cannot_be_exported():
    with pytest.raises(ValidationError, match="没有查询来源"):
        dashboard_panels(SimpleNamespace(), report(query_id=""))


@pytest.mark.parametrize("source,rows", [("different-source", [{"状态": "未检", "数量": 3}]),
                                         ("source-1", [{"状态": "未检", "数量": 999}])])
def test_mismatched_saved_data_cannot_become_delivery_proof(monkeypatch, source, rows):
    from app.modules.analytics import queries
    monkeypatch.setattr(queries, "historical_result", lambda *args, **kwargs:
        SimpleNamespace(datasourceId=source, data=rows, columns=[], truncated=False))
    with pytest.raises(ValidationError, match="不一致"):
        dashboard_panels(SimpleNamespace(), report())


def test_empty_query_is_truthful_and_truncation_is_retained(monkeypatch):
    from app.modules.analytics import queries
    monkeypatch.setattr(queries, "historical_result", lambda *args, **kwargs:
        SimpleNamespace(datasourceId="source-1", data=[], columns=[], truncated=True))
    panels = dashboard_panels(SimpleNamespace(), report(rows=[]))
    assert panels[0]["rows"] == []
    assert panels[0]["truncated"] is True
    assert render_snapshot("空查询范围", 1, panels).startswith(b"\x89PNG")


def test_selected_scope_blocks_outside_dashboard_before_render(monkeypatch):
    from app.modules.business_ai import dashboard_actions
    from app.modules.business_ai import context
    from app.modules.reports import service
    ctx = SimpleNamespace(workspace_id="workspace", user_id="user")
    monkeypatch.setattr(context, "auth_context_from_tool", lambda *args: ctx)
    monkeypatch.setattr(dashboard_actions, "invocation", lambda *args: SimpleNamespace(id="run-1"))
    monkeypatch.setattr(service, "get_report", lambda *args: report())
    with pytest.raises(ValidationError, match="超出本次选择范围"):
        render_dashboard_image(ctx, {"selected_datasource_ids": ["different"]}, {"report_id": "report-1"})


@pytest.fixture
def inspection_case(monkeypatch):
    """保存执行、权限与存储均使用内存替身；禁止查询业务库或读取其它查询历史。"""
    from app.common import authorization, owned_records
    from app.modules.analytics import queries
    from app.modules.workflows import graph
    from app.modules.workflows.business_results import projected_report
    from app.modules.alerts.inspection_export import inspection_facts

    rows = [{"设备": "设备甲", "项目": "温度", "结果": "正常"},
            {"设备": "设备甲", "项目": "转速", "结果": "预警"},
            {"设备": "设备乙", "项目": "开机时间", "结果": "待确认"}]
    execution = SimpleNamespace(id="execution-1", workflow_id="workflow-1", run_id="workflow-run-1",
        revision=2, status="completed", created_at=datetime(2026, 9, 23, 9),
        snapshot={"name": "设备巡检流程", "domains": ["alert"], "graph": {"nodes": [
            {"id": "collect", "kind": "database-query", "title": "只读采集", "config": {"datasourceId": "source-1"}}
        ], "edges": []}}, result_json={"nodes": {"report": {"status": "completed", "output": {
            "schemaVersion": "workflow-business-report-v1", "kind": "alert", "data": rows}}}})
    facts = inspection_facts([projected_report(execution)])
    columns = ["设备", "检查项", "异常项", "待确认项"]
    values = [[row[name] for name in columns] for row in facts["devices"]]
    saved = SimpleNamespace(id="inspection-dashboard", name="设备巡检分析（报告快照）", version=1, datasource_ids=[],
        lineage={"entry": "inspection_evidence_export", "source": "saved_workflow_reports",
                 "sourceExecutionIds": [execution.id], "dataStatus": "saved_evidence"},
        content={"widgets": [{"id": "title", "component": "JText", "config": {"option": {"body": {"text": "巡检"}}}},
            {"id": "checks", "component": "JScrollBoard", "config": {"dataType": 1,
                "chartDataParsed": values, "chartData": json.dumps(values, ensure_ascii=False),
                "option": {"header": [{"label": name} for name in columns]}}}]})
    calls = {"permissions": [], "executions": [], "graph_checks": []}
    ctx = SimpleNamespace(workspace_id="workspace", user_id="user")

    def owned(actual_ctx, model, identity, permission):
        assert actual_ctx is ctx and model.__name__ == "WorkflowExecution"
        calls["executions"].append((identity, permission))
        assert identity == execution.id
        return execution

    def no_query(*args, **kwargs):
        raise AssertionError("巡检快照只读执行证据，不得运行 SQL、读取 QueryHistory 或重新执行工作流")

    monkeypatch.setattr(authorization, "require_permission", lambda actual_ctx, permission: calls["permissions"].append(permission))
    monkeypatch.setattr(owned_records, "owned_record", owned)
    monkeypatch.setattr(graph, "authorize_graph", lambda actual_ctx, data: calls["graph_checks"].append(data))
    monkeypatch.setattr(queries, "historical_result", no_query)
    monkeypatch.setattr(queries, "execute_query", no_query)
    return SimpleNamespace(ctx=ctx, report=saved, execution=execution, facts=facts, calls=calls)


def test_fixed_inspection_export_can_render_with_revalidated_saved_evidence(inspection_case):
    case = inspection_case
    panels = dashboard_panels(case.ctx, case.report, selected_datasource_ids={"source-1"})
    assert panels[0]["rows"] == case.facts["devices"]
    assert panels[0]["source_execution_ids"] == ["execution-1"]
    assert panels[0]["source_datasource_ids"] == ["source-1"]
    assert "不等于未巡检设备数" in panels[0]["scope_note"]
    assert case.calls["permissions"] == ["workflows.view", "alerts.view"]
    assert case.calls["executions"] == [("execution-1", "workflows.view")]
    assert len(case.calls["graph_checks"]) == 1
    data = render_snapshot(case.report.name, case.report.version, panels)
    with Image.open(BytesIO(data)) as image:
        assert image.size[0] == 1600
        assert image.getextrema()[0][0] != image.getextrema()[0][1]


@pytest.mark.parametrize("change", ["rows", "json_rows", "headers", "duplicate_widget", "query_id", "remote_type"])
def test_changed_inspection_snapshot_cannot_inherit_old_provenance(inspection_case, change):
    case = inspection_case
    case.report.content = deepcopy(case.report.content)
    widget = case.report.content["widgets"][1]
    config = widget["config"]
    if change == "rows":
        config["chartDataParsed"][0][1] = 999
    elif change == "json_rows":
        config["chartData"] = "[[999]]"
    elif change == "headers":
        config["option"]["header"][1]["label"] = "未巡检设备数"
    elif change == "duplicate_widget":
        case.report.content["widgets"].append(deepcopy(widget))
    elif change == "query_id":
        config["queryId"] = "invented-query"
    else:
        config["dataType"] = 3
    with pytest.raises(ValidationError):
        dashboard_panels(case.ctx, case.report)


@pytest.mark.parametrize("ids", [[], ["execution-1", "execution-1"], [None], "execution-1", [str(i) for i in range(21)]])
def test_incomplete_inspection_lineage_is_rejected_before_loading_sources(inspection_case, ids):
    case = inspection_case
    case.report.lineage["sourceExecutionIds"] = ids
    with pytest.raises(ValidationError, match="来源"):
        dashboard_panels(case.ctx, case.report)
    assert not case.calls["executions"]


def test_inspection_source_outside_current_selection_is_rejected(inspection_case):
    case = inspection_case
    with pytest.raises(ValidationError, match="超出本次选择范围"):
        dashboard_panels(case.ctx, case.report, selected_datasource_ids={"other-source"})


def test_failed_execution_cannot_be_an_inspection_snapshot_source(inspection_case):
    case = inspection_case
    case.execution.status = "failed"
    with pytest.raises(ValidationError, match="尚未完成"):
        dashboard_panels(case.ctx, case.report)


def test_missing_execution_graph_cannot_skip_resource_reauthorization(inspection_case):
    case = inspection_case
    case.execution.snapshot.pop("graph")
    with pytest.raises(ValidationError, match="资源图快照"):
        dashboard_panels(case.ctx, case.report)


def test_revoked_workflow_source_permission_blocks_inspection_export(inspection_case, monkeypatch):
    from app.modules.workflows import graph
    case = inspection_case

    def deny(*args, **kwargs):
        raise PermissionErrorEx("该数据源的查看权限已撤销")

    monkeypatch.setattr(graph, "authorize_graph", deny)
    with pytest.raises(PermissionErrorEx, match="已撤销"):
        dashboard_panels(case.ctx, case.report)


def test_inspection_renderer_persists_execution_proof_for_word_embedding(inspection_case, monkeypatch):
    from app.modules.artifact import service as artifacts
    from app.modules.business_ai import context, dashboard_actions
    from app.modules.reports import service as reports
    case = inspection_case
    created = {}

    def create(db, **kwargs):
        created.update(kwargs)
        return SimpleNamespace(id="image-artifact")

    case.ctx.db = SimpleNamespace()
    monkeypatch.setattr(context, "auth_context_from_tool", lambda tool: case.ctx)
    monkeypatch.setattr(dashboard_actions, "invocation", lambda *args: SimpleNamespace(id="chat-run"))
    monkeypatch.setattr(reports, "get_report", lambda *args: case.report)
    monkeypatch.setattr(artifacts, "create_artifact", create)
    monkeypatch.setattr(artifacts, "artifact_reference", lambda row: {"id": row.id})
    result = render_dashboard_image(case.ctx, {"selected_datasource_ids": ["source-1"]}, {"report_id": case.report.id})
    proof = created["metadata"]["delivery_evidence"]
    assert result.status == "success" and result.data["dashboard_id"] == case.report.id
    assert proof["producer"] == "server_renderer.v1" and proof["kind"] == "dashboard_image"
    assert proof["source_execution_ids"] == ["execution-1"] and proof["source_query_ids"] == []
    assert proof["render_mode"] == "saved_inspection_snapshot"
    assert "不等于未巡检设备数" in result.data["description"]
    assert created["data"].startswith(b"\x89PNG")


def test_live_identity_change_cannot_use_cached_dashboard_auth(monkeypatch):
    from app.modules.business_ai import context
    stale = SimpleNamespace(workspace_id="workspace", user_id="old-user")
    monkeypatch.setattr(context, "auth_context_from_tool", lambda *args: SimpleNamespace(workspace_id="workspace", user_id="new-user"))
    with pytest.raises(PermissionErrorEx, match="身份"):
        render_dashboard_image(stale, {}, {"report_id": "report-1"})
