"""隔离验证单表日期覆盖诊断，不连接在用平台、业务库或真实模型。"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.ids import new_id
from app.errors import ToolExecutionError, ValidationError
from app.models import AuthIdentity, DataSource, ToolCall
from app.modules.analytics import data_profile, metadata, queries
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.models import QueryHistory, SourceMetadata
from app.modules.analytics.sql_safety import authorize_sql
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.chat_artifacts.chart_renderer import _receipt_contains, load_authorized_query
from app.modules.runtime import service as runtime
from app.modules.runtime.task_control import TaskControl, current
from app.providers import model
from app.providers.tool.gateway import ToolGateway, build_context


@pytest.fixture
def profile_fixture(client, administrator, db, monkeypatch, tmp_path):
    """仅建立临时 SQLite 表，日期、部门、逻辑删除和空值用于区分诊断与业务口径。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    workspace = administrator["X-Workspace-Id"]
    path = managed_database_path(workspace, "coverage-fixture.sqlite3", must_exist=False)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, booked_at DATETIME, "
                           "amount DECIMAL, department TEXT, isDeleted INTEGER)")
        connection.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?)", [
            (1, "2026-01-28 09:00:00", 100, "财务", 0),
            (2, "2026-08-03 08:00:00", 200, "设备", 0),
            (3, None, 300, "财务", 0),
            (4, "2026-09-12 10:00:00", 9000, "设备", 1),
        ])
        connection.execute("CREATE TABLE empty_dates (id INTEGER PRIMARY KEY, booked_at DATE)")
        connection.execute("CREATE TABLE text_flags (id INTEGER PRIMARY KEY, booked_at DATE, isDeleted VARCHAR(5))")
        connection.executemany("INSERT INTO text_flags VALUES (?, ?, ?)", [
            (1, "2026-08-01", "false"), (2, "2026-09-01", "true"),
        ])
        connection.execute("CREATE TABLE boolean_flags (id INTEGER PRIMARY KEY, booked_at DATE, isDeleted BOOLEAN)")
        connection.executemany("INSERT INTO boolean_flags VALUES (?, ?, ?)", [
            (1, "2026-08-01", False), (2, "2026-09-01", True),
        ])
        connection.execute("CREATE TABLE restricted_data (booked_at DATE)")
    created = client.post("/api/v1/analytics/datasources", headers=administrator, json={
        "name": "覆盖诊断隔离库", "type": "sqlite", "databaseName": path.name,
        "allowedTables": ["invoices", "empty_dates", "text_flags", "boolean_flags"],
    })
    assert created.status_code == 201, created.text
    source = db.get(DataSource, created.json()["data"]["id"])
    user = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin")).user_id
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    original = "查询2026年8月设备部门的记录明细并做数字大屏"

    def new_context():
        run = runtime.create_run(db, workspace_id=workspace, conversation_id=cid, user_id=user,
            context_snapshot={"user_request": original, "datasource_ids": [source.id]})
        db.commit()
        context = build_context(db=db, workspace_id=workspace, user_id=user, run_id=run.id,
                                selected_datasource_ids=[source.id])
        context["conversation_id"] = cid
        return context

    context = new_context()
    calls = []
    original_execute_read = queries.execute_read

    def observe_query(*args, **kwargs):
        calls.append(args[2])
        return original_execute_read(*args, **kwargs)

    monkeypatch.setattr(queries, "execute_read", observe_query)
    monkeypatch.setattr(metadata, "inspect_sql_source", lambda *a, **kw: pytest.fail("诊断不能重新同步元数据"))
    monkeypatch.setattr(model, "chat_for_run", lambda *a, **kw: pytest.fail("诊断不能调用真实或桩模型"))
    control = TaskControl()
    token = current.set(control)
    try:
        yield SimpleNamespace(source=source, context=context, new_context=new_context, calls=calls,
                              control=control, original=original, path=path)
    finally:
        current.reset(token)


def profile(fixture, **overrides):
    """通过真实工具网关执行，复用模型实际可见的参数契约。"""
    context = overrides.pop("context", fixture.context)
    return ToolGateway(context["db"]).execute(context["workspace_id"], "query_business_data", {
        "datasource_id": fixture.source.id, "mode": "profile", "query": "核对该表全部日期的覆盖范围",
        "table_name": "invoices", "date_column": "booked_at", **overrides,
    }, context)


def test_profile_is_one_fixed_all_date_query_with_distinct_evidence(profile_fixture, db):
    fixture = profile_fixture
    result = profile(fixture, query="SELECT SUM(amount) FROM invoices", max_rows=999)
    assert result.status == "success", result.error_summary
    data = result.data
    assert data["resultType"] == "data_coverage_profile" and data["evidenceKind"] == "coverage_only"
    assert "queryId" not in data and "data" not in data
    assert data["profile"] == {"recordCount": 3, "nullDateCount": 1,
                               "earliestDate": "2026-01-28 09:00:00", "latestDate": "2026-08-03 08:00:00"}
    assert data["scope"]["dateRange"] == "all_dates"
    assert data["scope"]["businessFiltersApplied"] is False
    assert data["scope"]["financialMetrics"] is False
    assert data["scope"]["metricSemantics"] == "record_coverage_not_account_balances"
    assert data["scope"]["filters"] == [{"column": "isDeleted", "operator": "=", "value": 0}]
    assert len(fixture.calls) == 1 and fixture.calls[0].max_rows == 1
    executed = data["executedSql"]
    assert "SUM" not in executed and "amount" not in executed and "department" not in executed
    assert "2026-08" not in executed and executed.endswith("LIMIT 1")
    history = db.get(QueryHistory, data["profileQueryId"])
    assert history.status == "success" and history.query_mode == "profile" and history.session_id == ""
    assert history.generated_sql == executed
    assert set(history.model_usage_json) <= {"businessSemantics"}
    assert history.result_json["evidenceKind"] == "coverage_only"
    assert history.result_json["queryType"] == "profile"
    assert history.result_json["retrievalEvidence"] == data["scope"]
    assert history.result_json["chartRecommendations"] == []


def test_empty_table_is_empty_coverage_not_zero_financial_balance(profile_fixture):
    result = profile(profile_fixture, table_name="empty_dates")
    assert result.status == "success", result.error_summary
    assert result.data["profile"] == {"recordCount": 0, "nullDateCount": 0,
                                      "earliestDate": None, "latestDate": None}
    assert result.data["scope"]["rowScope"] == "all_authorized_table_records"
    assert result.data["scope"]["financialMetrics"] is False
    assert "不能说金额为0" in result.data["guidance"]


def test_same_turn_reuses_success_but_other_turn_does_not(profile_fixture, db):
    fixture = profile_fixture
    first = profile(fixture)
    second = profile(fixture)
    assert first.status == second.status == "success"
    assert first.data["profileQueryId"] == second.data["profileQueryId"]
    assert first.data["reused"] is False and second.data["reused"] is True
    assert len(fixture.calls) == 1
    second.data["profile"]["recordCount"] = -1
    assert profile(fixture).data["profile"]["recordCount"] == 3
    other_turn = profile(fixture, context=fixture.new_context())
    assert other_turn.status == "success" and other_turn.data["reused"] is False
    assert other_turn.data["profileQueryId"] != first.data["profileQueryId"]
    assert len(fixture.calls) == 2 and len(db.scalars(select(QueryHistory)).all()) == 2


def test_cache_rechecks_source_version_and_current_source_status(profile_fixture, db):
    fixture = profile_fixture
    first = profile(fixture)
    fixture.source.status = "disabled"
    db.commit()
    disabled = profile(fixture)
    assert disabled.status == "failed" and "停用" in disabled.error_summary
    assert len(fixture.calls) == 1
    fixture.source.status = "active"
    fixture.source.connection_config = {**fixture.source.connection_config, "version": 2}
    db.commit()
    stale = profile(fixture)
    assert stale.status == "failed" and "当前版本" in stale.error_summary
    assert len(fixture.calls) == 1
    record = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == fixture.source.id))
    record.source_version = 2
    db.commit()
    renewed = profile(fixture)
    assert renewed.status == "success" and renewed.data["profileQueryId"] != first.data["profileQueryId"]
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("metadata_state", ["missing", "pending", "failed", "stale"])
def test_unready_metadata_stops_without_query_or_sync(profile_fixture, db, metadata_state):
    fixture = profile_fixture
    record = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == fixture.source.id))
    if metadata_state == "missing":
        db.delete(record)
    elif metadata_state == "stale":
        record.source_version += 1
    else:
        record.status = metadata_state
    db.commit()
    result = profile(fixture)
    assert result.status == "failed" and "已同步的授权元数据" in result.error_summary
    assert fixture.calls == [] and db.scalars(select(QueryHistory)).all() == []


@pytest.mark.parametrize("args", [
    {"table_name": ""}, {"date_column": ""}, {"table_name": "restricted_data"},
    {"table_name": "invoices; SELECT * FROM restricted_data"},
    {"date_column": "amount"}, {"date_column": "department"},
    {"date_column": "booked_at) FROM restricted_data --"},
])
def test_invalid_or_unauthorized_profile_columns_never_query(profile_fixture, args):
    result = profile(profile_fixture, **args)
    assert result.status == "failed" and profile_fixture.calls == []


def test_http_source_profile_never_collects(profile_fixture, db, monkeypatch):
    from app.modules.collection import execution
    fixture = profile_fixture
    fixture.source.provider = "http"
    db.commit()
    monkeypatch.setattr(execution, "collect", lambda *a, **kw: pytest.fail("覆盖诊断不能调用 HTTP/API"))
    result = profile(fixture)
    assert result.status == "failed" and "关系型数据库" in result.error_summary
    assert fixture.calls == []


def test_profile_failure_is_not_retried_or_cached(profile_fixture, db, monkeypatch):
    fixture = profile_fixture
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise ToolExecutionError("隔离测试：数据库资源异常，停止查询")
    monkeypatch.setattr(queries, "execute_read", fail)
    result = profile(fixture)
    assert result.status == "failed" and "资源异常" in result.error_summary
    assert len(calls) == 1 and fixture.control.query_receipts == {}
    histories = db.scalars(select(QueryHistory)).all()
    assert len(histories) == 1 and histories[0].status == "failed"


def test_profile_cannot_be_reused_as_chat_financial_query(profile_fixture, db):
    fixture = profile_fixture
    result = profile(fixture)
    assert result.status == "success", result.error_summary
    call = ToolCall(id=new_id(), workspace_id=fixture.context["workspace_id"], run_id=fixture.context["run_id"],
                    tool_name="query_business_data", status="completed", arguments_json={"mode": "profile"},
                    result_json={"status": "success", "data": result.data})
    db.add(call)
    db.commit()
    profile_id = result.data["profileQueryId"]
    assert not _receipt_contains(call, profile_id)
    ctx = auth_context_from_tool(fixture.context)
    with pytest.raises(ValidationError, match="不能据此生成图表"):
        load_authorized_query(ctx, fixture.context, profile_id)


def test_business_query_after_profile_keeps_original_month_and_department(profile_fixture, db, monkeypatch):
    fixture = profile_fixture
    assert profile(fixture).status == "success"
    observed = []
    def respond(profile_config, messages, **kwargs):
        observed.append(json.loads(messages[1]["content"]))
        return {"content": json.dumps({"sql": "SELECT id, amount, department FROM invoices "
            "WHERE isDeleted = 0 AND department = '设备' "
            "AND booked_at >= '2026-08-01' AND booked_at < '2026-09-01' ORDER BY id"})}
    monkeypatch.setattr(model, "chat_for_run", respond)
    result = ToolGateway(db).execute(fixture.context["workspace_id"], "query_business_data", {
        "datasource_id": fixture.source.id, "mode": "sql", "query": "SELECT * FROM invoices",
    }, fixture.context)
    assert result.status == "success", result.error_summary
    assert result.data["data"] == [{"id": 2, "amount": 200, "department": "设备"}]
    assert len(observed) == 1 and observed[0]["question"] == fixture.original
    assert observed[0]["userIntent"]["currentRequest"] == fixture.original
    assert result.metadata["planner_sql_replanned"] is True
    assert result.metadata["query_purpose"] == "business_result"
    assert "generatedSql" in result.metadata["guidance"]
    history = db.get(QueryHistory, result.data["queryId"])
    assert history.query_text == fixture.original and history.session_id == fixture.context["conversation_id"]
    assert history.query_mode == "natural_language" and len(fixture.calls) == 2


@pytest.mark.parametrize("provider,date_type,deleted_type", [
    ("mysql", "DATETIME(6)", "TINYINT"), ("postgres", "TIMESTAMP WITH TIME ZONE", "BOOLEAN"),
    ("sqlite", "DATE", "INTEGER"), ("mysql", "DATETIME", "BIT(1)"),
])
def test_profile_fixed_sql_passes_existing_dialect_authorizer(provider, date_type, deleted_type):
    tables = [{"tableName": "invoice_events", "columns": [
        {"columnName": "booked_at", "dataType": date_type},
        {"columnName": "is_deleted", "dataType": deleted_type},
        {"columnName": "amount", "dataType": "DECIMAL"},
    ]}]
    sql, scope = data_profile.build_profile_query(tables, "invoice_events", "booked_at", provider=provider)
    authorized = authorize_sql(sql, provider=provider, tables=tables, max_rows=1)
    assert authorized.max_rows == 1 and authorized.sql.endswith("LIMIT 1")
    assert "SUM" not in sql and scope["queryBudget"] == 1
    assert scope["filters"][0]["value"] == (False if deleted_type == "BOOLEAN" else 0)


def test_ambiguous_deleted_column_is_rejected_before_query():
    tables = [{"tableName": "events", "columns": [
        {"columnName": "booked_at", "dataType": "DATE"},
        {"columnName": "is_deleted", "dataType": "INTEGER"},
        {"columnName": "isDeleted", "dataType": "INTEGER"},
    ]}]
    with pytest.raises(ValidationError, match="多个删除标记"):
        data_profile.build_profile_query(tables, "events", "booked_at", provider="sqlite")


def test_profile_rejects_stored_text_true_false_without_guessing_zero(profile_fixture, db):
    result = profile(profile_fixture, table_name="text_flags")
    assert result.status == "failed" and "删除标记口径未知" in result.error_summary
    assert profile_fixture.calls == []
    assert profile_fixture.control.query_receipts == {} and db.scalars(select(QueryHistory)).all() == []


def test_profile_rejects_unknown_delete_flag_metadata_without_query(profile_fixture, db):
    record = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == profile_fixture.source.id))
    tables = json.loads(json.dumps(record.tables_json))
    for table in tables:
        if table["tableName"] == "invoices":
            next(column for column in table["columns"] if column["columnName"] == "isDeleted")["dataType"] = "UNKNOWN"
    record.tables_json = tables
    db.commit()
    result = profile(profile_fixture)
    assert result.status == "failed" and "删除标记口径未知" in result.error_summary
    assert profile_fixture.calls == [] and db.scalars(select(QueryHistory)).all() == []


@pytest.mark.parametrize("flag", [2, -1, None, "false", "true"])
def test_profile_rejects_non_binary_values_in_integer_delete_flag(profile_fixture, db, flag):
    fixture = profile_fixture
    with sqlite3.connect(fixture.path) as connection:
        connection.execute("UPDATE invoices SET isDeleted = ? WHERE id = 1", (flag,))
    result = profile(fixture)
    assert result.status == "failed" and "删除标记口径未知" in result.error_summary
    assert len(fixture.calls) == 1 and fixture.control.query_receipts == {}
    history = db.scalar(select(QueryHistory))
    assert history.status == "failed" and history.query_mode == "profile"
    assert history.result_json["success"] is False
    assert history.result_json["retrievalEvidence"]["rowScope"] == "unknown_deletion_semantics"


def test_profile_boolean_flag_accepts_actual_boolean_and_rejects_text(profile_fixture, db):
    fixture = profile_fixture
    valid = profile(fixture, table_name="boolean_flags")
    assert valid.status == "success", valid.error_summary
    assert valid.data["profile"] == {"recordCount": 1, "nullDateCount": 0,
                                    "earliestDate": "2026-08-01", "latestDate": "2026-08-01"}
    with sqlite3.connect(fixture.path) as connection:
        connection.execute("UPDATE boolean_flags SET isDeleted = 'false' WHERE id = 1")
    invalid = profile(fixture, table_name="boolean_flags", context=fixture.new_context())
    assert invalid.status == "failed" and "删除标记口径未知" in invalid.error_summary
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("data_type", ["VARCHAR(5)", "TEXT", "", "UNKNOWN", "BIT(2)", "BIT", "REAL", "NOT_BOOL"])
def test_unknown_delete_flag_types_are_rejected_without_sql(data_type):
    tables = [{"tableName": "events", "columns": [
        {"columnName": "booked_at", "dataType": "DATE"},
        {"columnName": "isDeleted", "dataType": data_type},
    ]}]
    with pytest.raises(ValidationError, match="删除标记口径未知"):
        data_profile.build_profile_query(tables, "events", "booked_at", provider="mysql")


def test_non_binary_declared_enum_is_rejected_without_sql():
    tables = [{"tableName": "events", "columns": [
        {"columnName": "booked_at", "dataType": "DATE"},
        {"columnName": "isDeleted", "dataType": "INTEGER", "enumValues": [
            {"value": 1, "label": "正常"}, {"value": 2, "label": "已删除"},
        ]},
    ]}]
    with pytest.raises(ValidationError, match="删除标记口径未知"):
        data_profile.build_profile_query(tables, "events", "booked_at", provider="sqlite")
