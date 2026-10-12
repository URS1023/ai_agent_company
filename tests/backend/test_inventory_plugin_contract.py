"""新库存接口经过实际 DSH 技能选择、网关和回执链的隔离合同回归。"""
from types import SimpleNamespace

import pytest

from test_business_worker import prepare, worker_context
from test_openwebui_query_dashboard import dashboard_case

from app.business_worker import tools
from app.errors import ValidationError
from app.modules.business_ai.dashboard_actions import grounded_requirement, queried_datasource_ids
from app.providers.tool.gateway import ToolGateway


@pytest.mark.parametrize("skill_ids,expected", [
    ([], {"query_inventory_data", "export_query_spreadsheet", "export_query_presentation", "view_device_alerts"}),
    (["device-inspection"], {"query_inventory_data", "export_query_spreadsheet", "view_device_alerts"}),
    (["document-delivery"], {"query_inventory_data", "export_query_spreadsheet", "export_query_presentation"}),
    (["business-analysis"], {"query_inventory_data", "export_query_spreadsheet"}),
    (["dashboard-delivery"], {"query_inventory_data", "create_dashboard_from_queries", "render_chart_image"}),
])
def test_skill_selection_prepares_the_complete_applicable_delivery_chain(worker_context, skill_ids, expected):
    prepared = prepare(worker_context, "查看已选来源中的库存，使用查询结果完成交付", skill_ids=skill_ids)
    names = {tool["name"] for tool in prepared["tools"]}
    assert expected <= names
    if skill_ids == ["device-inspection"]:
        assert "delete_file" not in names
    assert "query_inventory_data" in prepared["system_prompt"]
    assert "export_query_presentation" in prepared["system_prompt"]


def test_selected_device_skill_executes_inventory_aliases_through_real_gateway(worker_context, monkeypatch):
    """只替换业务取数函数，技能范围、参数校验和回执落库仍走真实路径。"""
    from app.modules.analytics import inventory

    captured = []

    def query(ctx, payload):
        captured.append((ctx.workspace_id, payload))
        return SimpleNamespace(model_dump=lambda **kwargs: {
            "queryId": "synthetic-query", "success": True, "data": [], "totalCount": 0, "truncated": False})

    monkeypatch.setattr(inventory, "query_inventory", query)
    prepared = prepare(worker_context, "查看仓库备件库存", skill_ids=["device-inspection"])
    result = tools.execute(prepared["run_id"], "inventory-alias", 1, "query_inventory_data", {
        "datasourceId": "synthetic-source", "kind": "inventory", "warehouseId": "warehouse-1", "pageSize": 7})
    assert result["status"] == "success", result
    assert len(captured) == 1
    workspace, payload = captured[0]
    assert workspace == prepared["workspace_id"]
    assert payload.datasource_id == "synthetic-source" and payload.warehouse_id == "warehouse-1" and payload.page_size == 7
    unavailable = tools.execute(prepared["run_id"], "outside-skill", 2, "export_query_presentation", {"query_ids": ["synthetic-query"]})
    assert unavailable["error_code"] == "tool_not_available"


def test_inventory_receipt_provides_dashboard_source_and_retains_scope(dashboard_case):
    case = dashboard_case
    case.call.tool_name = "query_inventory_data"
    case.call.arguments_json = {"kind": "inventory", "datasource_id": case.source.id, "warehouse_id": "warehouse-1"}
    case.call.result_json = {"status": "success", "data": {"queryId": case.query.id,
        "datasourceId": case.source.id, "generatedSql": case.query.generated_sql,
        "truncated": True, "totalCount": 2, "analysis": "库存计量单位未知，不能合计",
        "statistics": {"quantityTotal": None, "scope": "returned_page"},
        "retrievalEvidence": {"mode": "deterministic_inventory"}}}
    case.ctx.db.commit()
    assert queried_datasource_ids(case.ctx, case.run) == [case.source.id]
    requirement = grounded_requirement(case.ctx, case.run, "使用这次库存结果生成大屏")
    assert '"truncated": true' in requirement
    assert '"quantityTotal": null' in requirement
    assert "库存计量单位未知，不能合计" in requirement and "warehouse-1" in requirement
    assert "deterministic_inventory" in requirement
    case.query.owner_user_id = "other-user"
    case.ctx.db.commit()
    assert queried_datasource_ids(case.ctx, case.run) == []


def test_inventory_validation_keeps_source_clarification_without_model_retry():
    result = ToolGateway._error_result("query_inventory_data", ValidationError(
        "需明确来源", clarification="请选择目标仓库的数据源", options=["源甲", "源乙"]))
    assert result.metadata["requires_user_input"] is True and result.metadata["retryable"] is False
    assert result.data["options"] == ["源甲", "源乙"]
