"""隔离复现复合任务选定模板后已有查询仍无法交付大屏的问题。"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.errors import ValidationError
from app.models import ToolCall
from app.modules.analytics.models import QueryHistory
from app.modules.chat_artifacts.query_dashboard import create_dashboard_from_queries
from app.modules.reports.content import builtin_templates
from app.modules.reports.models import Report
from app.modules.reports.template_lock import assert_visual_unchanged
from test_openwebui_query_dashboard import dashboard_case


def _queries(case):
    """与故障会话同形的三份汇总和三份分组，金额均为合成数。"""
    case.ctx.db.delete(case.call)
    rows = [
        [{"payment_count": 12, "contract_amount_total": "300.00", "paid_amount_total": "180.00"}],
        [{"month": "2026-08", "payment_count": 8, "contract_amount_total": "200.00"},
         {"month": "2026-09", "payment_count": 4, "contract_amount_total": "100.00"}],
        [{"department": "部门编码甲", "payment_count": 7, "contract_amount_total": "170.00"},
         {"department": "部门编码乙", "payment_count": 5, "contract_amount_total": "130.00"}],
        [{"contract_count": 4, "contract_amount_total": "500.00", "contract_remaining_total": None}],
        [{"contract_count": 6, "contract_amount_total": "400.00", "settled_amount_total": "0.00"}],
        [{"month": "2026-03", "contract_count": 2, "contract_amount_total": "100.00"},
         {"month": "2026-07", "contract_count": 4, "contract_amount_total": "300.00"}],
    ]
    ids = []
    for i, data in enumerate(rows):
        identity = f"snapshot-query-{i}"
        ids.append(identity)
        case.ctx.db.add(QueryHistory(id=identity, workspace_id="workspace", owner_user_id="user",
            run_id=case.query.run_id, datasource_id="source", datasource_version=1, status="success",
            query_mode="sql", query_text="SELECT 合成结果", generated_sql="SELECT amount FROM synthetic",
            created_at=case.query.created_at, result_json={"queryId": identity, "success": True,
                "data": data, "columns": [{"columnName": k} for k in data[0]], "truncated": False,
                "totalCount": len(data)}))
        case.ctx.db.add(ToolCall(id=f"snapshot-call-{i}", workspace_id="workspace", run_id=case.run.id,
            tool_name="query_business_data", status="success", arguments_json={"mode": "sql"},
            result_json={"status": "success", "data": {"queryId": identity}}))
    case.run.context_snapshot = {**case.run.context_snapshot, "chat_engine": "dsh",
        "user_request": "查询财务数据，做成数字大屏并给我一份ERP系统的介绍PPT",
        "dashboard_template_mode": "manual", "dashboard_template_id": "lynx-source-template-41"}
    case.ctx.db.commit()
    return ids


def test_composite_task_binds_six_verified_queries_to_selected_template_without_model_or_sql(dashboard_case):
    case = dashboard_case
    identities = _queries(case)
    args = {"query_ids": identities, "title": "财务资金与合同数字大屏",
            "bindings": [{"query_id": identity, "value_field": "contract_amount_total"} for identity in identities]}
    result = create_dashboard_from_queries(case.ctx, case.tool, args)
    report = case.ctx.db.get(Report, result.data["reportId"])
    template = next(t for t in builtin_templates() if t["id"] == "lynx-source-template-41")
    assert report.template_id == template["id"]
    assert report.refresh_seconds == 0
    assert_visual_unchanged(template["content"], report.content)
    bound = [w for w in report.content["widgets"] if w.get("dataStatus") == "verified"]
    assert len(bound) == 8
    assert set(w["config"]["queryId"] for w in bound) == set(identities)
    for widget in bound:
        query = case.ctx.db.get(QueryHistory, widget["config"]["queryId"])
        assert widget["data"] == widget["config"]["chartDataParsed"] == query.result_json["data"]
        assert widget["config"]["dataType"] == 1 and widget["config"]["snapshotOnly"] is True
        assert widget["config"]["snapshotBinding"]["valueField"] in {"contract_amount_total", "paid_amount_total", "payment_count"}
        assert widget["config"]["snapshotBinding"]["unit"] == ""
    title = next(w for w in report.content["widgets"] if w["id"] == "showcase-12-title")
    assert title["metricName"] == args["title"]
    repeated = create_dashboard_from_queries(case.ctx, case.tool, args)
    assert repeated.data["reused"] and repeated.data["reportId"] == report.id
    from app.business_worker.dashboard_deliveries import dashboard_deliveries
    assert dashboard_deliveries(case.ctx.db, case.run, [case.run])[0]["status"] == "ready"


def test_dsh_create_business_dashboard_reuses_snapshots_before_failed_legacy_receipt(dashboard_case, monkeypatch):
    from app.modules.business_ai.models import BusinessActionReceipt
    from app.providers.tool.builtin_provider import _business_action

    case = dashboard_case
    identities = _queries(case)
    case.ctx.db.add(BusinessActionReceipt(workspace_id="workspace", owner_user_id="user",
        action="dashboard.generate", invocation_run_id=case.run.id,
        request_key=f"chat_{case.run.id}_dashboard", request_hash="legacy", status="failed",
        error_message="模板缺少查询，尚无可验证的业务指标", result_json={"retryable": False}))
    case.ctx.db.commit()
    result = _business_action("create_business_dashboard", {"requirement": "财务数据大屏", "query_ids": identities,
        "bindings": [{"query_id": identity, "value_field": "contract_amount_total"} for identity in identities]},
                              {**case.tool, "execution_owner": "dsh"})
    assert result.status == "success"
    assert result.data["queryIds"] == identities
    assert result.data["templateId"] == "lynx-source-template-41"


def test_capacity_and_profile_fail_before_any_report_is_saved(dashboard_case):
    case = dashboard_case
    identities = _queries(case)
    for identity in identities:
        query = case.ctx.db.get(QueryHistory, identity)
        query.result_json = {**deepcopy(query.result_json), "data": [{"amount": 1}],
                             "columns": [{"columnName": "amount"}]}
    case.ctx.db.commit()
    with pytest.raises(ValidationError, match="兼容数据槽不足"):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities})
    assert not case.ctx.db.scalars(select(Report)).all()
    case.ctx.db.get(QueryHistory, identities[0]).query_mode = "profile"
    case.ctx.db.commit()
    with pytest.raises(ValidationError, match="覆盖诊断"):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[:1]})
    assert not case.ctx.db.scalars(select(Report)).all()


@pytest.mark.parametrize("field", ["total_receivable", "total_payable", "outstanding_balance"])
def test_financial_value_is_displayed_instead_of_record_count(dashboard_case, field):
    case = dashboard_case
    identities = _queries(case)
    query = case.ctx.db.get(QueryHistory, identities[0])
    query.result_json = {**query.result_json, "data": [{"record_count": 6, field: "18850.00"}],
                         "columns": [{"columnName": "record_count"}, {"columnName": field}]}
    case.ctx.db.commit()
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[:1]})
    report = case.ctx.db.get(Report, result.data["reportId"])
    widget = max((w for w in report.content["widgets"] if w.get("dataStatus") == "verified"),
                 key=lambda w: w["w"] * w["h"])
    assert widget["config"]["snapshotBinding"]["valueField"] == field


def test_explicit_binding_rejects_text_instead_of_rendering_it_as_a_metric(dashboard_case):
    case = dashboard_case
    identities = _queries(case)
    with pytest.raises(ValidationError, match="显式数值字段"):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": [identities[2]],
            "bindings": [{"query_id": identities[2], "value_field": "department"}]})
    assert not case.ctx.db.scalars(select(Report)).all()


@pytest.mark.parametrize("tool_name", ["create_dashboard_from_queries", "create_business_dashboard"])
def test_gateway_accepts_explicit_amount_binding_and_renders_the_requested_field(dashboard_case, tool_name):
    from app.providers.tool.gateway import ToolGateway

    case = dashboard_case
    identities = _queries(case)
    args = {"query_ids": identities[:1], "title": "资金支付大屏",
            "bindings": [{"query_id": identities[0], "value_field": "paid_amount_total"}]}
    if tool_name == "create_business_dashboard":
        args["requirement"] = "查询财务数据做成大屏"
    result = ToolGateway(case.ctx.db).execute(case.ctx.workspace_id, tool_name, args,
                                             {**case.tool, "execution_owner": "dsh"})
    assert result.status == "success", result.to_dict()
    report = case.ctx.db.get(Report, result.data["reportId"])
    widget = max((w for w in report.content["widgets"] if w.get("dataStatus") == "verified"),
                 key=lambda w: w["w"] * w["h"])
    assert widget["config"]["snapshotBinding"]["valueField"] == "paid_amount_total"


def _platform_queries(case):
    """复现小画布模板的五项已汇总数值和部门分组，金额全部为合成数据。"""
    identities = _queries(case)[:2]
    totals = {"record_count": 79, "contract_amount": "3600.50", "paid_amount": "1300.10",
              "unpaid_amount": "2200.40", "request_amount": "3100.20"}
    captions = {"record_count": "记录数", "contract_amount": "合同金额（元）", "paid_amount": "已付金额（元）",
                "unpaid_amount": "未付金额（元）", "request_amount": "本次申请金额（元）"}
    for index, identity in enumerate(identities):
        query = case.ctx.db.get(QueryHistory, identity)
        data = [totals] if index == 0 else [dict(department=code, **{key: value for key, value in totals.items()})
                                           for code in ("原始部门编码甲", None)]
        query.generated_sql = "SELECT count(*) AS record_count FROM synthetic" if index == 0 else "SELECT department FROM synthetic GROUP BY department"
        query.result_json = {**query.result_json, "data": data,
                             "columns": [{"columnName": key, "comment": captions.get(key, "部门")}
                                         for key in data[0]], "totalCount": len(data)}
    case.run.context_snapshot = {**case.run.context_snapshot, "dashboard_template_id": "lynx-source-template-43"}
    case.ctx.db.commit()
    return identities


def test_small_platform_template_displays_all_five_totals_and_prioritizes_the_large_primary_chart(dashboard_case):
    case = dashboard_case
    identities = _platform_queries(case)
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities, "title": "资金支付大屏",
        "bindings": [{"query_id": identities[1], "value_field": "unpaid_amount", "label_field": "department"}]})
    report = case.ctx.db.get(Report, result.data["reportId"])
    template = next(t for t in builtin_templates() if t["id"] == "lynx-source-template-43")
    assert_visual_unchanged(template["content"], report.content)
    bound = [w for w in report.content["widgets"] if w.get("dataStatus") == "verified"]
    metrics = [w for w in bound if w["component"] == "JStatsSummary"]
    assert {w["config"]["snapshotBinding"]["valueField"] for w in metrics} == {
        "record_count", "contract_amount", "paid_amount", "unpaid_amount", "request_amount"}
    assert len(metrics) == 5
    main = next(w for w in bound if w["id"] == "showcase-14-widget-9")
    assert main["config"]["snapshotBinding"]["valueField"] == "unpaid_amount"
    for widget in bound:
        query = case.ctx.db.get(QueryHistory, widget["config"]["queryId"])
        assert widget["data"] == widget["config"]["chartDataParsed"] == query.result_json["data"]
        if widget["component"] == "JScrollBoard":
            assert len(widget["config"]["snapshotBinding"]["columns"]) <= 2
        if widget["component"] == "JStatsSummary" and widget["config"]["snapshotBinding"]["valueField"] != "record_count":
            assert widget["config"]["snapshotBinding"]["unit"] == "元"
            assert "（元）" not in widget["metricName"]
    assert len(bound) == 10
    assert all(not item["unshownFields"] for item in result.data["fieldCoverage"])
    assert result.data["quality"]["coverageComplete"] is True
    assert {scope["returnedRows"] for scope in result.data["scopes"] if scope["queryId"] == identities[0]} == {1}
    assert {scope["returnedRows"] for scope in result.data["scopes"] if scope["queryId"] == identities[1]} == {2}


def test_detail_rows_never_populate_total_cards_and_truncated_rows_remain_tabular(dashboard_case):
    case = dashboard_case
    identities = _platform_queries(case)
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[1:]})
    report = case.ctx.db.get(Report, result.data["reportId"])
    assert not any(w.get("dataStatus") == "verified" and w["component"] == "JStatsSummary" for w in report.content["widgets"])
    query = case.ctx.db.get(QueryHistory, identities[1])
    query.result_json = {**query.result_json, "truncated": True}
    case.ctx.db.commit()
    with pytest.raises(ValidationError, match="兼容数据槽不足") as rejected:
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[1:]})
    assert rejected.value.extra["required_table_columns"] == 6
    assert max(table["readable_columns"] for table in rejected.value.extra["available_tables"]) == 2
    case.run.context_snapshot = {**case.run.context_snapshot, "dashboard_template_id": "lynx-source-template-42"}
    case.ctx.db.commit()
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[1:]})
    report = case.ctx.db.get(Report, result.data["reportId"])
    bound = [w for w in report.content["widgets"] if w.get("dataStatus") == "verified"]
    assert len(bound) == 1 and bound[0]["component"] == "JScrollBoard"
    assert bound[0]["config"]["truncated"] is True


def test_null_amount_remains_unknown_and_unsupported_fields_are_reported(dashboard_case):
    case = dashboard_case
    identities = _platform_queries(case)
    query = case.ctx.db.get(QueryHistory, identities[0])
    query.result_json = {**deepcopy(query.result_json), "data": [{**query.result_json["data"][0], "paid_amount": None}]}
    case.ctx.db.commit()
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[:1],
        "bindings": [{"query_id": identities[0], "value_field": "paid_amount"}]})
    report = case.ctx.db.get(Report, result.data["reportId"])
    widget = next(w for w in report.content["widgets"] if w.get("config", {}).get("snapshotBinding", {}).get("valueField") == "paid_amount")
    assert widget["data"][0]["paid_amount"] is None
    # 模板41只有四个指标槽且没有表格，默认五个独立总计必须在保存前报出缺项。
    case.run.context_snapshot = {**case.run.context_snapshot, "dashboard_template_id": "lynx-source-template-41"}
    case.ctx.db.commit()
    before = len(case.ctx.db.scalars(select(Report)).all())
    with pytest.raises(ValidationError, match="无法完整展示") as rejected:
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[:1]})
    assert rejected.value.extra["missing_fields"] == ["record_count"]
    assert rejected.value.extra["execution_started"] is False
    assert len(case.ctx.db.scalars(select(Report)).all()) == before
    explicit = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[:1],
        "bindings": [{"query_id": identities[0], "value_field": "contract_amount"}]})
    assert explicit.data["quality"]["coverageComplete"] is True
    assert explicit.data["quality"]["queries"][0]["requiredFields"] == ["contract_amount"]


def test_reuse_reports_saved_display_coverage_instead_of_new_unsaved_candidate(dashboard_case, monkeypatch):
    import app.modules.chat_artifacts.template_query_snapshot as binding_module

    case = dashboard_case
    identities = _platform_queries(case)
    args = {"query_ids": identities, "title": "已保存的大屏"}
    first = create_dashboard_from_queries(case.ctx, case.tool, args)
    report = case.ctx.db.get(Report, first.data["reportId"])
    monkeypatch.setattr(binding_module, "template_snapshot_content", lambda *params: (report.content, []))
    repeated = create_dashboard_from_queries(case.ctx, case.tool, args)
    assert repeated.data["reused"]
    assert repeated.data["scopes"] == first.data["scopes"]
    assert repeated.data["fieldCoverage"] == first.data["fieldCoverage"]


@pytest.mark.parametrize("title", ["应收应付大屏", "财务" * 98 + "应收应付"])
def test_dashboard_qualifies_unverified_receivable_title_using_saved_financial_evidence(dashboard_case, title):
    case = dashboard_case
    identities = _platform_queries(case)
    for identity in identities:
        case.ctx.db.get(QueryHistory, identity).model_usage_json = {"businessSemantics": {
            "method": "authorized_metadata_ast", "status": "validated", "actualCategories": ["payable"],
            "monetaryFields": [{"table": "synthetic", "column": "unpaid_amount", "categories": ["payable"]}]}}
    case.ctx.db.commit()
    args = {"query_ids": identities, "title": title}
    result = create_dashboard_from_queries(case.ctx, case.tool, args)
    report = case.ctx.db.get(Report, result.data["reportId"])
    assert "应收未验证" in report.name and "应收" in report.description
    assert len(report.name) <= 200
    assert result.data["quality"]["financialScope"]["unverified_title_categories"] == ["receivable"]
    assert result.data["scopeNotes"] and "不等于金额为零" in result.data["scopeNotes"][0]
    repeated = create_dashboard_from_queries(case.ctx, case.tool, args)
    assert repeated.data["reused"] and repeated.data["title"] == report.name


@pytest.mark.parametrize("value", ["1e10000", "1_000"])
def test_browser_unrepresentable_numeric_values_fail_before_report_save(dashboard_case, value):
    case = dashboard_case
    identities = _platform_queries(case)
    query = case.ctx.db.get(QueryHistory, identities[0])
    result = deepcopy(query.result_json)
    result["data"][0]["contract_amount"] = value
    next(column for column in result["columns"] if column["columnName"] == "contract_amount")["dataType"] = "decimal"
    query.result_json = result
    case.ctx.db.commit()
    with pytest.raises(ValidationError, match="声明为数值") as rejected:
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": identities[:1]})
    assert rejected.value.extra["invalid_numeric_fields"] == ["contract_amount"]
    assert rejected.value.extra["execution_started"] is False
    assert not case.ctx.db.scalars(select(Report)).all()
