"""原生模型参数别名归一化回归；不启动服务、不访问数据库。"""
from app.providers.tool.gateway import _normalize_model_arguments


def test_document_spec_alias_is_normalized_before_schema_validation():
    """模型使用通用 spec 仍能进入正式 document_spec 字段。"""
    assert _normalize_model_arguments("render_document", {"spec": {"title": "报告"}, "format": "docx"}) == {
        "document_spec": {"title": "报告"}, "format": "docx"
    }


def test_chart_and_dashboard_camel_case_aliases_are_normalized():
    """图表和大屏的标识字段统一为网关 schema 的 snake_case。"""
    assert _normalize_model_arguments("render_chart_image", {"queryId": "q", "chartType": "pie"}) == {
        "query_id": "q", "chart_type": "pie"
    }
    assert _normalize_model_arguments("render_dashboard_image", {"reportId": "r"}) == {"report_id": "r"}


def test_saved_query_ids_can_flow_from_receipt_into_ppt_or_dashboard():
    """回执中的 queryIds 别名不能让已完成取数后的交付停在参数校验。"""
    for name in ("export_query_presentation", "create_dashboard_from_queries"):
        assert _normalize_model_arguments(name, {"queryIds": ["q1", "q2"], "title": "已保存数据"}) == {
            "query_ids": ["q1", "q2"], "title": "已保存数据"}
