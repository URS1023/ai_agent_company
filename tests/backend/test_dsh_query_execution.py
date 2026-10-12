"""验证 DSH 单一规划器的透明 SQL 执行；只使用临时 SQLite 与禁用模型桩。"""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import Run
from app.modules.analytics.models import QueryHistory
from app.modules.analytics.dsh_query_execution import required_scope
from app.errors import ValidationError
from app.providers.tool.gateway import ToolGateway
from test_data_profile import profile_fixture


SQL = ("SELECT id, amount, department FROM invoices WHERE isDeleted = 0 AND department = '设备' "
       "AND booked_at >= '2026-08-01' AND booked_at < '2026-09-01' ORDER BY id")


@pytest.fixture
def dsh_fixture(profile_fixture, db):
    fixture = profile_fixture
    run = db.get(Run, fixture.context["run_id"])
    run.created_at = datetime(2026, 9, 28, 0, 0)
    run.context_snapshot = {**run.context_snapshot, "chat_engine": "dsh", "execution_owner": "dsh-host",
                           "user_request": "查询2026年8月设备部门的记录，分别列明细和汇总，并生成Excel和数字大屏"}
    db.commit()
    return fixture


def execute(fixture, **args):
    context = args.pop("context", fixture.context)
    return ToolGateway(context["db"]).execute(context["workspace_id"], "query_business_data", {
        "datasource_id": fixture.source.id, "query": SQL, "mode": "sql", "task": "列出原始范围的记录明细",
        "session_id": "model-chosen-session", **args,
    }, context)


def test_dsh_executes_explicit_sql_without_second_model_and_keeps_server_session(dsh_fixture, db, monkeypatch):
    from app.modules.analytics import dsh_query_execution
    original_execute = dsh_query_execution.execute_query
    observed = []
    def observe(ctx, payload, **kwargs):
        observed.append((payload, kwargs["user_intent"]))
        return original_execute(ctx, payload, **kwargs)
    monkeypatch.setattr(dsh_query_execution, "execute_query", observe)
    result = execute(dsh_fixture)
    assert result.status == "success", result.error_summary
    assert result.data["data"] == [{"id": 2, "amount": 200, "department": "设备"}]
    assert result.metadata["requested_mode"] == result.metadata["executed_mode"] == "sql"
    assert result.metadata["planner_sql_replanned"] is False
    assert result.metadata["scope_validation"]["status"] == "verified"
    assert result.data["chartRecommendations"] == []
    assert observed[0][0].query == SQL and observed[0][0].mode == "sql"
    assert observed[0][1]["plannerTask"] == "列出原始范围的记录明细"
    assert "生成Excel和数字大屏" in observed[0][1]["currentRequest"]
    assert observed[0][1]["executionOwner"] == "dsh"
    history = db.get(QueryHistory, result.data["queryId"])
    assert history.query_text == SQL and history.query_mode == "sql"
    assert history.session_id == dsh_fixture.context["conversation_id"]
    assert len(dsh_fixture.calls) == 1


def test_compound_request_allows_distinct_detail_and_aggregate_steps(dsh_fixture):
    first = execute(dsh_fixture)
    sql = SQL.replace("id, amount, department", "COUNT(*) AS record_count, SUM(amount) AS amount_total").replace(" ORDER BY id", "")
    second = execute(dsh_fixture, query=sql, task="汇总同一月份同一部门的记录数量和金额")
    assert first.status == second.status == "success", second.error_summary
    assert second.data["data"] == [{"record_count": 1, "amount_total": 200}]
    assert first.data["queryId"] != second.data["queryId"] and len(dsh_fixture.calls) == 2


def test_repeated_same_step_reuses_snapshot_but_different_task_must_validate(dsh_fixture):
    first = execute(dsh_fixture)
    second = execute(dsh_fixture)
    assert second.status == "success" and second.metadata["same_turn_result_reused"]
    assert first.data["queryId"] == second.data["queryId"] and len(dsh_fixture.calls) == 1


@pytest.mark.parametrize("sql,reason", [
    ("SELECT * FROM invoices", "date_scope_missing_or_changed"),
    (SQL.replace("2026-08-01", "2026-01-01"), "date_scope_missing_or_changed"),
    (SQL.replace("department = '设备' AND ", ""), "department_scope_missing_or_changed"),
    (SQL.replace("department = '设备'", "department = '财务'"), "department_scope_missing_or_changed"),
    (SQL.replace(" ORDER BY id", " OR id = 1 ORDER BY id"), "date_scope_missing_or_changed"),
    ("SELECT * FROM (" + SQL + ") AS q", "unsupported_scope_shape"),
])
def test_missing_or_changed_scope_is_returned_to_same_agent_before_query(dsh_fixture, sql, reason):
    result = execute(dsh_fixture, query=sql)
    assert result.status == "failed" and result.error_code == "dsh_scope_validation"
    assert result.data["validation"]["reasonCode"] == reason
    assert result.metadata["recovery"] == "correct_input" and result.metadata["business_data_queried"] is False
    assert dsh_fixture.calls == []


def test_natural_language_and_implicit_overview_never_spawn_hidden_planner(dsh_fixture):
    result = execute(dsh_fixture, mode="natural_language", query="查询财务数据")
    assert result.status == "failed" and result.error_code == "dsh_explicit_query_required"
    assert result.data["validation"]["supportedModes"] == ["schema", "sql", "profile"]
    overview = execute(dsh_fixture, mode="overview", query="财务数据")
    assert overview.status == "failed" and overview.error_code == "dsh_overview_scope_unsupported"
    assert dsh_fixture.calls == []


def test_schema_exposes_server_scope_before_agent_writes_sql(dsh_fixture):
    result = execute(dsh_fixture, mode="schema", query="invoices明细")
    assert result.status == "success", result.error_summary
    assert result.data["executionScope"]["period"]["start"] == "2026-08-01"
    assert result.data["executionScope"]["period"]["endExclusive"] == "2026-09-01"
    assert result.data["executionScope"]["department"]["value"] == "设备"
    assert dsh_fixture.calls == []


def test_worker_marker_keeps_old_unmarked_snapshot_on_transparent_path(dsh_fixture, db):
    run = db.get(Run, dsh_fixture.context["run_id"])
    run.context_snapshot = {key: value for key, value in run.context_snapshot.items() if key not in {"chat_engine", "execution_owner"}}
    db.commit()
    result = execute(dsh_fixture, context={**dsh_fixture.context, "execution_owner": "dsh"})
    assert result.status == "success" and result.metadata["planner_sql_replanned"] is False


def test_last_month_is_anchored_to_server_run_date_not_database_clock(dsh_fixture, db):
    run = db.get(Run, dsh_fixture.context["run_id"])
    run.context_snapshot = {**run.context_snapshot, "user_request": "查询上个月设备部门明细和汇总"}
    db.commit()
    result = execute(dsh_fixture)
    assert result.status == "success", result.error_summary
    scope = result.metadata["scope_validation"]
    assert scope["referenceDate"] == "2026-09-28"
    assert scope["period"]["start"] == "2026-08-01" and scope["period"]["endExclusive"] == "2026-09-01"
    wrong = execute(dsh_fixture, query=SQL.replace("'2026-08-01'", "CURRENT_DATE"))
    assert wrong.status == "failed" and len(dsh_fixture.calls) == 1


def test_explicit_user_sql_wins_over_planner_sql(dsh_fixture, db):
    run = db.get(Run, dsh_fixture.context["run_id"])
    original = "SELECT id FROM invoices WHERE id = 1"
    run.context_snapshot = {**run.context_snapshot, "user_request": original}
    db.commit()
    result = execute(dsh_fixture, query="SELECT id FROM invoices", task="")
    assert result.status == "success" and result.data["data"] == [{"id": 1}]
    assert db.get(QueryHistory, result.data["queryId"]).query_text == original


def test_missing_task_has_actionable_contract_without_query(dsh_fixture):
    result = execute(dsh_fixture, task="")
    assert result.status == "failed" and result.error_code == "dsh_query_task_required"
    assert "task" in result.data["validation"]["required"] and dsh_fixture.calls == []


def test_finance_validation_details_are_preserved_for_agent_correction(dsh_fixture, monkeypatch):
    from app.modules.analytics import dsh_query_execution
    def reject(*args, **kwargs):
        raise ValidationError("应收不能替换为付款", semanticValidation={"reasonCode": "finance_direction_mismatch", "evidence": ["outbound_payment"]},
                              executionBlocked=True, businessDataQueried=False, canProveDataAbsence=False)
    monkeypatch.setattr(dsh_query_execution, "execute_query", reject)
    result = execute(dsh_fixture)
    assert result.status == "failed"
    assert result.data["validation"]["semanticValidation"]["reasonCode"] == "finance_direction_mismatch"
    assert result.data["validation"]["businessDataQueried"] is False
    assert dsh_fixture.calls == []


def test_model_cannot_replace_server_selected_source_or_conversation(dsh_fixture, db):
    run = db.get(Run, dsh_fixture.context["run_id"])
    run.context_snapshot = {**run.context_snapshot, "datasource_ids": ["another-source"]}
    db.commit()
    result = execute(dsh_fixture)
    assert result.status == "failed" and "服务端保存" in result.error_summary and dsh_fixture.calls == []


def test_ambiguous_compound_months_require_grounded_current_clause():
    intent = {"currentRequest": "查询2026年8月记录和2026年9月记录", "previousUserTurns": [], "referenceDate": "2026-09-28"}
    with pytest.raises(ValidationError, match="多个业务范围"):
        required_scope(intent, {})
    value = required_scope(intent, {"request_quote": "2026年8月记录"})
    assert value["period"]["start"] == "2026-08-01"
    with pytest.raises(ValidationError, match="逐字来自"):
        required_scope(intent, {"request_quote": "2026年7月记录"})


@pytest.mark.parametrize("dimension", ["按部门", "按照部门", "各部门", "每个部门", "所有部门"])
def test_department_grouping_is_not_an_equality_filter(dimension):
    request = f"查询资金支付汇总明细的全部未删除记录，给出记录数、合同金额、已付金额、本次申请支付金额的总计以及{dimension}汇总；再查库存，生成两份Excel和两份PPT"
    result = required_scope({"currentRequest": request, "referenceDate": "2026-09-29"}, {})
    assert result["department"] is None


def test_grouping_does_not_remove_explicit_department_filter():
    request = "查询2026年8月设备部门的记录，给出金额总计以及按部门汇总"
    result = required_scope({"currentRequest": request, "referenceDate": "2026-09-29"}, {})
    assert result["department"]["value"] == "设备"
    assert result["period"]["start"] == "2026-08-01"


@pytest.mark.parametrize("user_text", ["按部门：设备 查询金额", "按部门 = 设备 查询金额"])
def test_grouping_prefix_does_not_remove_named_department_assignment(user_text):
    result = required_scope({"currentRequest": user_text, "referenceDate": "2026-09-29"}, {})
    assert result["department"]["value"] == "设备"


def test_compound_department_summary_executes_and_keeps_all_groups(dsh_fixture, db):
    run = db.get(Run, dsh_fixture.context["run_id"])
    run.context_snapshot = {**run.context_snapshot, "user_request": "查询全部记录的金额总计以及按部门汇总；再查库存并生成Excel、PPT和数字大屏"}
    db.commit()
    total = execute(dsh_fixture, query="SELECT COUNT(*) AS record_count, SUM(amount) AS amount_total FROM invoices WHERE isDeleted = 0", task="全部未删除记录总计")
    grouped = execute(dsh_fixture, query="SELECT department, COUNT(*) AS record_count, SUM(amount) AS amount_total FROM invoices WHERE isDeleted = 0 GROUP BY department", task="按部门汇总")
    assert total.status == grouped.status == "success", grouped.error_summary
    assert total.data["data"][0]["record_count"] == sum(row["record_count"] for row in grouped.data["data"])
    assert len(dsh_fixture.calls) == 2


def test_unsupported_time_qualifier_cannot_silently_disappear():
    intent = {"currentRequest": "查询八月同比设备部门记录", "previousUserTurns": [], "referenceDate": "2026-09-28"}
    with pytest.raises(ValidationError, match="不能忽略"):
        required_scope(intent, {})


def test_same_run_steer_changes_month_but_keeps_original_department(dsh_fixture, db):
    from app.modules.analytics.query_intent import query_context
    from app.modules.business_ai.context import auth_context_from_tool
    run = db.get(Run, dsh_fixture.context["run_id"])
    initial = "查询2026年八月财务部门的记录"
    run.context_snapshot = {**run.context_snapshot, "submitted_content": initial, "user_request": "改成七月",
                           "steering_messages": [{"content": "另做Excel", "accepted": True},
                                                 {"content": "改成七月", "accepted": True}]}
    db.commit()
    intent = query_context(auth_context_from_tool(dsh_fixture.context), dsh_fixture.context, dsh_fixture.source.id, "明细")
    assert [item["request"] for item in intent["previousUserTurns"]] == [initial, "另做Excel"]
    assert intent["currentRequest"] == "改成七月"
    sql = SQL.replace("'设备'", "'财务'").replace("2026-08-01", "2026-07-01").replace("2026-09-01", "2026-08-01")
    result = execute(dsh_fixture, query=sql)
    assert result.status == "success", result.error_summary
    assert result.metadata["scope_validation"]["period"]["start"] == "2026-07-01"
    assert result.metadata["scope_validation"]["department"]["value"] == "财务"
    wrong = execute(dsh_fixture, query=SQL.replace("'设备'", "'财务'"))
    assert wrong.status == "failed" and wrong.data["validation"]["reasonCode"] == "date_scope_missing_or_changed"
    assert len(dsh_fixture.calls) == 1


@pytest.mark.parametrize("period_text,start,end", [
    ("最近3天", "2026-09-26", "2026-09-29"),
    ("过去三天", "2026-09-26", "2026-09-29"),
    ("最近三十一天", "2026-08-29", "2026-09-29"),
    ("最近1天", "2026-09-28", "2026-09-29"),
    ("本周", "2026-09-28", "2026-10-05"),
    ("上周", "2026-09-21", "2026-09-28"),
    ("本季度", "2026-07-01", "2026-10-01"),
    ("上一季度", "2026-04-01", "2026-07-01"),
    ("2026年第一季度", "2026-01-01", "2026-04-01"),
    ("去年第四季度", "2025-10-01", "2026-01-01"),
    ("2025年度", "2025-01-01", "2026-01-01"),
])
def test_common_calendar_periods_are_deterministic_and_keep_department(period_text, start, end):
    result = required_scope({"currentRequest": f"查询{period_text}天津部门记录", "referenceDate": "2026-09-28"}, {})
    assert result["period"]["start"] == start and result["period"]["endExclusive"] == end
    assert result["department"]["value"] == "天津"


@pytest.mark.parametrize("days", ["0", "367", "-1", "1.5", "999999"])
def test_relative_day_count_is_bounded_and_never_ignored(days):
    with pytest.raises(ValidationError, match="1–366"):
        required_scope({"currentRequest": f"查询最近{days}天记录", "referenceDate": "2026-09-28"}, {})


def test_current_quarter_and_last_month_cross_year_correctly():
    for request, start, end in [("上季度", "2025-10-01", "2026-01-01"), ("上个月", "2025-12-01", "2026-01-01")]:
        result = required_scope({"currentRequest": request, "referenceDate": "2026-01-02"}, {})
        assert (result["period"]["start"], result["period"]["endExclusive"]) == (start, end)


def test_recent_days_scope_executes_without_replanning_or_losing_department(dsh_fixture, db):
    run = db.get(Run, dsh_fixture.context["run_id"])
    run.context_snapshot = {**run.context_snapshot, "user_request": "查询最近3天设备部门记录并分别汇总和制作大屏"}
    db.commit()
    sql = SQL.replace("2026-08-01", "2026-09-26").replace("2026-09-01", "2026-09-29")
    result = execute(dsh_fixture, query=sql)
    assert result.status == "success", result.error_summary
    assert result.metadata["scope_validation"]["period"]["calendarBasis"] == "local_calendar_days_including_today"
    assert result.metadata["scope_validation"]["department"]["value"] == "设备"
    assert len(dsh_fixture.calls) == 1 and result.data["data"] == []
