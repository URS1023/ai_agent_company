"""在内存 SQLite 验证查询快照大屏的真实保存、复用、权限与数据范围。"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.common.deps import AuthContext
from app.errors import AppError, PermissionErrorEx, ValidationError
from app.models import Base, DataSource, Run, ToolCall, User, Workspace, WorkspaceMember
from app.modules.analytics.models import QueryHistory
from app.modules.openwebui.query_dashboard import create_dashboard_from_queries, snapshot_content
from app.modules.openwebui.resume import resume_snapshot
from app.modules.reports.models import Report, ReportRevision


@pytest.fixture
def dashboard_case(monkeypatch):
    """只保存平台元数据；任何执行业务 SQL 或调用模型的路径立即令测试失败。"""
    from app.modules.analytics import queries
    from app.providers import model

    def no_execution(*args, **kwargs):
        raise AssertionError("快照大屏不得重新执行业务查询或模型")

    monkeypatch.setattr(queries, "execute_query", no_execution)
    monkeypatch.setattr(model, "chat", no_execution)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 23, 9)
    with Session(engine) as db:
        user = User(id="user", display_name="合成验收用户", status="active", created_at=now, updated_at=now)
        workspace = Workspace(id="workspace", organization_id="org", name="合成工作区", created_at=now)
        member = WorkspaceMember(id="member", workspace_id="workspace", user_id="user", role="admin", created_at=now)
        source = DataSource(id="source", workspace_id="workspace", owner_user_id="user", provider="mysql",
                            name="仅元数据，无连接配置", connection_config={"version": 1},
                            status="active", created_at=now, updated_at=now)
        run = Run(id="chat-run", workspace_id="workspace", started_by_user_id="user", conversation_id="chat",
                  status="running", created_at=now,
                  context_snapshot={"user_request": "分析巡检并生成大屏", "user_message_id": "message",
                                    "datasource_ids": ["source"], "openwebui": {"chat_id": "native"}})
        analytic_run = Run(id="analytics-run", workspace_id="workspace", started_by_user_id="user", conversation_id="other",
                           status="completed", created_at=now)
        query = QueryHistory(id="query", workspace_id="workspace", owner_user_id="user", run_id=analytic_run.id,
                             datasource_id=source.id, query_text="近期已巡检和未巡检设备数", query_mode="natural_language",
                             generated_sql="SELECT state, COUNT(*) AS count FROM synthetic_inspections GROUP BY state",
                             datasource_version=1, status="success", created_at=now,
                             result_json={"queryId": "query", "success": True, "truncated": False, "totalCount": 2,
                                          "columns": [{"columnName": "状态"}, {"columnName": "设备数"}],
                                          "data": [{"状态": "已巡检", "设备数": 15}, {"状态": "未巡检", "设备数": 5}]})
        call = ToolCall(id="query-call", workspace_id="workspace", run_id=run.id, tool_name="query_business_data",
                        status="completed", arguments_json={}, result_json={"status": "success", "data": {"queryId": query.id}},
                        completed_at=now)
        db.add_all([user, workspace, member, source, run, analytic_run, query, call])
        db.commit()
        ctx = AuthContext(db=db, user=user, workspace=workspace, role="admin", token="")
        tool = {"db": db, "workspace_id": "workspace", "user_id": "user", "run_id": "chat-run",
                "selected_datasource_ids": ["source"]}
        yield SimpleNamespace(ctx=ctx, tool=tool, run=run, query=query, call=call, source=source, member=member)
    engine.dispose()


def test_real_report_and_revision_preserve_all_query_data_without_refresh(dashboard_case):
    case = dashboard_case
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": ["query"], "title": "设备巡检大屏"})
    assert result.status == "success"
    row = case.ctx.db.get(Report, result.data["reportId"])
    assert row is not None and row.status == "draft" and row.refresh_seconds == 0
    assert result.data["href"] == f"/report/view/{row.id}?workspace_id=workspace"
    assert row.lineage["generationMode"] == "saved_query_snapshot"
    assert row.lineage["queryIds"] == row.lineage["sourceQueryIds"] == ["query"]
    assert row.lineage["invocationRunId"] == "chat-run"
    data_widgets = [widget for widget in row.content["widgets"] if widget.get("dataStatus") == "verified"]
    assert len(data_widgets) == 1
    widget = data_widgets[0]
    assert widget["data"] == widget["config"]["chartDataParsed"] == case.query.result_json["data"]
    assert widget["config"]["queryId"] == "query"
    assert widget["query"]["sql"] == case.query.generated_sql
    assert widget["config"]["dataType"] == 1
    assert "宋体" in row.content["widgets"][0]["config"]["option"]["body"]["fontFamily"]
    assert len(case.ctx.db.scalars(select(ReportRevision)).all()) == 1


@pytest.mark.parametrize("field_count, expected_width, expected_panels", [(2, 912, 2), (10, 1848, 2), (35, 1848, 6)])
def test_snapshot_tables_choose_readable_columns_and_split_wide_results(dashboard_case, field_count, expected_width, expected_panels):
    case = dashboard_case
    rows = [{"真实标识": "合成甲", **{f"字段{index}": None if index == 1 else index for index in range(1, field_count)}}]
    queries = [SimpleNamespace(id=f"wide-{index}", datasource_id="source", query_text="宽结果", query_mode="sql",
        generated_sql="SELECT * FROM synthetic", created_at=case.query.created_at,
        result_json={"data": rows, "columns": [{"columnName": key} for key in rows[0]], "truncated": True}) for index in range(2)]
    content, scopes = snapshot_content("宽结果大屏", queries)
    tables = [widget for widget in content["widgets"] if widget["component"] == "JScrollBoard"]
    assert len(tables) == expected_panels
    for widget in tables:
        assert widget["w"] == expected_width
        assert widget["data"] == widget["config"]["chartDataParsed"] == rows
        projection = widget["config"]["snapshotBinding"]["columns"]
        assert len(projection) <= widget["w"] // 120
        assert projection[0] == "真实标识"
        assert widget["config"]["truncated"] is True
    for query in queries:
        assert {field for scope in scopes if scope["queryId"] == query.id for field in scope["displayedFields"]} == set(rows[0])


@pytest.mark.parametrize("oversized", ["panels", "bytes"])
def test_snapshot_layout_budget_rejects_before_copying_oversized_results(dashboard_case, oversized):
    query = dashboard_case.query
    row = {f"列{index}": index for index in range(500)} if oversized == "panels" else {"长文本": "x" * (5 * 1024 * 1024)}
    query.result_json = {"data": [row], "columns": [{"columnName": key} for key in row], "truncated": False}
    with pytest.raises(ValidationError) as rejected:
        snapshot_content("超大结果", [query])
    assert rejected.value.extra["execution_started"] is False
    assert rejected.value.extra["stage"] == ("layout_budget" if oversized == "panels" else "snapshot_budget")


def test_same_run_and_payload_reuses_saved_report_even_if_run_receipt_was_lost(dashboard_case):
    case = dashboard_case
    args = {"query_ids": ["query"], "title": "设备巡检大屏"}
    first = create_dashboard_from_queries(case.ctx, case.tool, args)
    snapshot = dict(case.run.context_snapshot)
    snapshot.pop("query_dashboard_receipts", None)
    case.run.context_snapshot = snapshot
    case.ctx.db.commit()
    second = create_dashboard_from_queries(case.ctx, case.tool, args)
    assert first.data["reportId"] == second.data["reportId"]
    assert second.data["reused"] is True
    assert len(case.ctx.db.scalars(select(Report)).all()) == 1
    assert len(case.ctx.db.scalars(select(ReportRevision)).all()) == 1


def test_deleted_snapshot_is_not_recreated_by_repeat_tool_call(dashboard_case):
    case = dashboard_case
    args = {"query_ids": ["query"]}
    first = create_dashboard_from_queries(case.ctx, case.tool, args)
    case.ctx.db.get(Report, first.data["reportId"]).status = "deleted"
    case.ctx.db.commit()
    with pytest.raises(ValidationError, match="已被删除"):
        create_dashboard_from_queries(case.ctx, case.tool, args)
    assert len(case.ctx.db.scalars(select(Report)).all()) == 1


@pytest.mark.parametrize("change", [
    {"query_ids": []}, {"query_ids": "query"}, {"query_ids": ["query", "query"]},
    {"query_ids": [str(i) for i in range(9)]}, {"query_ids": [None]}, {"title": " "}, {"title": "长" * 201},
])
def test_invalid_inputs_do_not_create_partial_report(dashboard_case, change):
    case = dashboard_case
    with pytest.raises(ValidationError):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": ["query"], **change})
    assert not case.ctx.db.scalars(select(Report)).all()


@pytest.mark.parametrize("change", ["receipt", "owner", "workspace", "source", "source_version", "source_disabled", "tool_scope"])
def test_query_ownership_success_scope_and_current_source_are_enforced(dashboard_case, change):
    case = dashboard_case
    if change == "receipt":
        case.call.status = "failed"
    elif change == "owner":
        case.query.owner_user_id = "someone"
    elif change == "workspace":
        case.query.workspace_id = "other"
    elif change == "source":
        case.query.datasource_id = "outside-scope"
    elif change == "source_version":
        case.source.connection_config = {"version": 2}
    elif change == "source_disabled":
        case.source.status = "disabled"
    else:
        case.tool["selected_datasource_ids"] = ["outside-scope"]
    case.ctx.db.commit()
    with pytest.raises(AppError):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": ["query"]})
    assert not case.ctx.db.scalars(select(Report)).all()


def test_revoked_dashboard_permission_is_not_restored_from_cached_context(dashboard_case):
    from app.common.authorization import require_permission
    from app.modules.system.models import Role, UserRole

    case = dashboard_case
    require_permission(case.ctx, "dashboards.create")
    case.member.role = "member"
    case.ctx.db.add(Role(id="reader", workspace_id="workspace", code="reader", name="只读",
                         permissions=["analytics.view", "datasources.view"], data_scope="self"))
    case.ctx.db.add(UserRole(workspace_id="workspace", user_id="user", role_id="reader"))
    case.ctx.db.commit()
    with pytest.raises(PermissionErrorEx):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": ["query"]})
    assert not case.ctx.db.scalars(select(Report)).all()


def test_explicit_resume_uses_previous_query_receipt_without_new_query(dashboard_case):
    case = dashboard_case
    case.run.status = "failed"
    snapshot = {"datasource_ids": ["source"], **resume_snapshot(case.ctx.db, case.run)}
    next_run = Run(id="next-chat-run", workspace_id="workspace", started_by_user_id="user", conversation_id="chat",
                   status="running", created_at=case.run.created_at + timedelta(minutes=1), context_snapshot=snapshot)
    case.ctx.db.add(next_run)
    case.ctx.db.commit()
    tool = {**case.tool, "run_id": next_run.id}
    result = create_dashboard_from_queries(case.ctx, tool, {"query_ids": ["query"]})
    row = case.ctx.db.get(Report, result.data["reportId"])
    assert row.lineage["invocationRunId"] == next_run.id
    assert row.lineage["queryIds"] == ["query"]
    assert len(case.ctx.db.scalars(select(ToolCall)).all()) == 1


def test_explicit_resume_accepts_legacy_camel_case_dashboard_receipt(dashboard_case):
    """旧回执使用 queryIds/reportName 时，继续任务仍复用已保存的大屏。"""
    case = dashboard_case
    first_args = {"query_ids": ["query"], "title": "设备巡检快照"}
    first = create_dashboard_from_queries(case.ctx, case.tool, first_args)
    legacy_args = {"queryIds": ["query"], "reportName": "设备巡检快照"}
    continue_after_dashboard(case, legacy_args, first, "continued-legacy-alias")
    resumed = create_dashboard_from_queries(case.ctx, case.tool, first_args)
    assert resumed.data["reportId"] == first.data["reportId"]
    assert resumed.data["reused"] is True


def test_explicit_template_is_not_silently_replaced(dashboard_case):
    case = dashboard_case
    case.run.context_snapshot = {**case.run.context_snapshot, "dashboard_template_mode": "manual", "dashboard_template_id": "fixed"}
    case.ctx.db.commit()
    with pytest.raises(AppError, match="记录不存在"):
        create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": ["query"]})


def test_truncated_and_empty_snapshots_keep_their_true_scope(dashboard_case):
    case = dashboard_case
    for rows, truncated in [([{"状态": "未巡检", "设备数": 5}], True), ([], False)]:
        case.query.result_json = {**deepcopy(case.query.result_json), "data": rows, "truncated": truncated}
        content, scopes = snapshot_content("设备巡检", [case.query])
        assert scopes[0]["returnedRows"] == len(rows)
        assert scopes[0]["truncated"] is truncated
        widget = next(widget for widget in content["widgets"] if widget.get("dataStatus") == "verified")
        assert widget["data"] == rows
        if truncated:
            assert "已截断" in str(content)


def test_multiple_results_preserve_separate_sources_without_adding_totals(dashboard_case):
    case = dashboard_case
    other = SimpleNamespace(id="other", query_text="另一项查询", query_mode="natural_language", datasource_id="source",
                            generated_sql="SELECT amount FROM synthetic", created_at=case.query.created_at,
                            result_json={"data": [{"金额": 19}], "truncated": False,
                                         "columns": [{"columnName": "金额", "comment": "金额（元）"}]})
    content, scopes = snapshot_content("设备与费用", [case.query, other])
    widgets = [widget for widget in content["widgets"] if widget.get("dataStatus") == "verified"]
    assert [widget["config"]["queryId"] for widget in widgets] == ["query", "other"]
    assert widgets[1]["data"] == [{"金额": 19}]
    assert len(scopes) == 2


def continue_after_dashboard(case, args, result, run_id):
    """记录真实大屏回执并模拟用户明确继续，资源仍由新一轮选择值决定。"""
    from app.modules.runtime import service as runtime

    previous = case.run
    call = runtime.record_tool_call(case.ctx.db, workspace_id=case.ctx.workspace_id, run_id=previous.id,
                                   step_id=None, tool_name="create_dashboard_from_queries", tool_version_id=None, arguments=args)
    runtime.finish_tool_call(case.ctx.db, call, result.to_dict())
    previous.status = "failed"
    case.ctx.db.commit()
    snapshot = {"datasource_ids": ["source"], **resume_snapshot(case.ctx.db, previous)}
    case.run = Run(id=run_id, workspace_id="workspace", started_by_user_id="user", conversation_id="chat",
                   status="running", created_at=previous.created_at + timedelta(minutes=1), context_snapshot=snapshot)
    case.ctx.db.add(case.run)
    case.ctx.db.commit()
    case.tool = {**case.tool, "run_id": case.run.id}


def test_repeated_explicit_continuation_reuses_the_authorized_ancestor_dashboard(dashboard_case):
    case = dashboard_case
    args = {"query_ids": ["query"], "title": "设备巡检快照"}
    first = create_dashboard_from_queries(case.ctx, case.tool, args)
    continue_after_dashboard(case, args, first, "continued-once")
    second = create_dashboard_from_queries(case.ctx, case.tool, args)
    continue_after_dashboard(case, args, second, "continued-twice")
    third = create_dashboard_from_queries(case.ctx, case.tool, args)
    assert first.data["reportId"] == second.data["reportId"] == third.data["reportId"]
    assert second.data["reused"] is True and third.data["reused"] is True
    assert first.data["version"] == second.data["version"] == third.data["version"]
    assert first.data["version"] >= 1
    assert len(case.ctx.db.scalars(select(Report)).all()) == 1
    assert len(case.ctx.db.scalars(select(ReportRevision)).all()) == 1


@pytest.mark.parametrize("mutation", ["deleted", "edited", "owner", "lineage"])
def test_continuation_never_recreates_or_overwrites_changed_dashboard(dashboard_case, mutation):
    from app.modules.reports import service
    from app.modules.reports.schemas import ReportInput

    case = dashboard_case
    args = {"query_ids": ["query"], "title": "设备巡检快照"}
    first = create_dashboard_from_queries(case.ctx, case.tool, args)
    continue_after_dashboard(case, args, first, "continued-once")
    report = case.ctx.db.get(Report, first.data["reportId"])
    if mutation == "deleted":
        report.status = "deleted"
    elif mutation == "edited":
        service.save_report(case.ctx, ReportInput(reportName="用户后来修改的名称", version=report.version), report.id)
    elif mutation == "owner":
        report.owner_user_id = "someone"
    else:
        report.lineage = {**report.lineage, "requestFingerprint": "changed"}
    case.ctx.db.commit()
    revisions_before = len(case.ctx.db.scalars(select(ReportRevision)).all())
    with pytest.raises(AppError):
        create_dashboard_from_queries(case.ctx, case.tool, args)
    assert len(case.ctx.db.scalars(select(Report)).all()) == 1
    assert len(case.ctx.db.scalars(select(ReportRevision)).all()) == revisions_before


@pytest.mark.parametrize("change", [{"datasource_ids": ["other"]}, {"file_ids": ["new-file"]}])
def test_changed_resume_resource_scope_cannot_reuse_old_query_or_dashboard(dashboard_case, change):
    case = dashboard_case
    args = {"query_ids": ["query"], "title": "设备巡检快照"}
    first = create_dashboard_from_queries(case.ctx, case.tool, args)
    continue_after_dashboard(case, args, first, "changed-resources")
    case.run.context_snapshot = {**case.run.context_snapshot, **change}
    case.ctx.db.commit()
    with pytest.raises(AppError):
        create_dashboard_from_queries(case.ctx, case.tool, args)
    assert len(case.ctx.db.scalars(select(Report)).all()) == 1
    assert len(case.ctx.db.scalars(select(ReportRevision)).all()) == 1
