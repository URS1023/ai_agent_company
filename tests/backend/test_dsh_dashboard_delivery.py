"""大屏交付必须有可预览的保存对象和真实查询快照；测试只使用内存 SQLite。"""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from test_business_worker import worker_context, prepare, request
from app.business_worker import chat, tasks
from app.ids import new_id
from app.models import DataSource, Message, Run, User
from app.modules.analytics.models import QueryHistory
from app.modules.reports.models import Report
from test_openwebui_query_dashboard import dashboard_case


def saved_dashboard(db, run_id, *, rows=None):
    """保存权威查询与大屏，不触发数据源连接。"""
    run = db.get(Run, run_id)
    source = DataSource(id=new_id(), workspace_id=run.workspace_id, owner_user_id=run.started_by_user_id,
                        provider="mysql", name="隔离元数据", connection_config={}, status="active",
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(source)
    db.flush()
    rows = [{"应收金额": "125.00"}] if rows is None else rows
    query = QueryHistory(id=new_id(), workspace_id=run.workspace_id, owner_user_id=run.started_by_user_id,
        datasource_id=source.id, query_text="上个月应收金额", generated_sql="SELECT amount FROM receivable",
        status="success", query_mode="sql", result_json={"data": rows, "columns": [{"columnName": "应收金额"}], "truncated": False})
    db.add(query)
    db.flush()
    report = Report(id=new_id(), workspace_id=run.workspace_id, owner_user_id=run.started_by_user_id,
        name="应收大屏", datasource_ids=[query.datasource_id], status="draft",
        content={"canvas": {"width": 1920, "height": 1080}, "widgets": [{"component": "JScrollBoard", "w": 900, "h": 500,
            "config": {"queryId": query.id, "dataStatus": "verified", "chartDataParsed": deepcopy(rows), "truncated": False}}]},
        lineage={"invocationRunId": run.id, "queryIds": [query.id]})
    db.add(report)
    db.commit()
    return query, report


@pytest.mark.parametrize("text", ["财务上个月的应收做成数字大屏", "生成应收数字大屏", "创建财务看板"])
def test_dashboard_requirement_recognizes_actual_creation(text):
    assert tasks.requirements(text) == [{"type": "dashboard", "count": 1}]


def test_design_only_follows_explicit_changes_but_keeps_plain_style_edits():
    from app.business_worker.dashboard_deliveries import _design_only
    initial = {"submitted_content": "制作大屏，只做布局设计", "user_request": "调整成蓝色",
               "steering_messages": [{"content": "调整成蓝色"}]}
    assert _design_only([SimpleNamespace(context_snapshot=initial)])
    assert not _design_only([SimpleNamespace(context_snapshot={**initial, "user_request": "加入真实应收金额",
        "steering_messages": initial["steering_messages"] + [{"content": "加入真实应收金额"}]})])


def test_claim_without_report_is_partial_and_native_turn_still_completed(worker_context):
    first = prepare(worker_context, "财务上个月的应收做成数字大屏")
    settled = chat.settle(first["run_id"], "completed", "大屏已经完成")
    assert settled["native_status"] == "completed" and settled["status"] == "partial"
    assert settled["task"]["missing_deliveries"] == [{"type": "dashboard", "count": 1, "missing": 1}]
    assert not settled["deliveries"]


@pytest.mark.parametrize("rows", [[{"应收金额": "125.00"}], [], [{"应收金额": 0}]])
def test_saved_query_report_is_real_delivery_including_empty_and_zero(worker_context, db, rows):
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"], rows=rows)
    settled = chat.settle(first["run_id"], "completed", "已保存")
    assert settled["status"] == "completed", settled
    delivery = settled["deliveries"][0]
    assert delivery["report_id"] == report.id and delivery["type"] == "dashboard"
    assert "download_url" not in delivery and "artifact_id" not in delivery
    assert delivery["scopes"][0]["empty"] is (not rows)
    db.expire_all()
    message = db.get(Message, settled["message_id"])
    assert any(part["type"] == "business_result" for part in message.parts_json)
    assert not any(part["type"] == "artifact" for part in message.parts_json)


@pytest.mark.parametrize("fault", ["failed_query", "profile", "mismatch", "archived", "wrong_owner", "wrong_scope", "missing_range", "no_snapshot", "no_metrics", "off_canvas"])
def test_invalid_report_never_completes_business_delivery(worker_context, db, fault):
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"])
    if fault == "failed_query": query.status = "failed"
    elif fault == "profile": query.query_mode = "profile"
    elif fault == "mismatch": query.result_json = {**query.result_json, "data": [{"应收金额": "999.00"}]}
    elif fault == "archived": report.status = "archived"
    elif fault == "wrong_owner":
        db.add(User(id="other-user", display_name="其他用户", status="active", created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
        db.flush()
        query.owner_user_id = "other-user"
    elif fault == "wrong_scope": report.datasource_ids = ["other-source"]
    elif fault == "missing_range": query.result_json = {"data": [{"应收金额": "125.00"}]}
    else:
        content = deepcopy(report.content)
        if fault == "no_snapshot": content["widgets"][0]["config"].pop("chartDataParsed")
        elif fault == "off_canvas": content["widgets"][0]["x"] = 999999
        else: content["widgets"] = [{"component": "JText", "config": {"text": "已完成"}}]
        report.content = content
    db.commit()
    settled = chat.settle(first["run_id"], "completed", "已保存")
    assert settled["status"] == "partial" and settled["native_status"] == "completed"
    assert settled["deliveries"][0]["status"] == "invalid"


def test_pure_design_does_not_require_business_query(worker_context, db):
    first = prepare(worker_context, "制作大屏，只做布局设计，不需要真实数据")
    query, report = saved_dashboard(db, first["run_id"])
    report.content = {"widgets": [{"component": "JText", "w": 800, "h": 100, "config": {"text": "模板标题"}}]}
    report.datasource_ids = []
    db.commit()
    assert chat.settle(first["run_id"], "completed", "布局已保存")["status"] == "completed"


def test_template_reference_does_not_waive_real_receivables_requirement(worker_context, db):
    first = prepare(worker_context, "参考模板生成应收金额大屏")
    query, report = saved_dashboard(db, first["run_id"])
    report.content = {"widgets": [{"component": "JText", "w": 800, "h": 100, "config": {"text": "模板标题"}}]}
    db.commit()
    result = chat.settle(first["run_id"], "completed", "大屏已完成")
    assert result["status"] == "partial"
    assert result["content"].startswith("大屏交付核验未通过")


def test_revision_task_cannot_count_unchanged_previous_dashboard(worker_context, db):
    first = prepare(worker_context, "生成应收数字大屏")
    saved_dashboard(db, first["run_id"])
    assert chat.settle(first["run_id"], "completed", "已保存")["status"] == "completed"
    changed = prepare(worker_context, "把这个图修改为新的标题", task_id=first["task"]["task_id"])
    result = chat.settle(changed["run_id"], "completed", "已经修改")
    assert result["status"] == "partial" and result["task"]["requires_new_delivery"]
    assert result["deliveries"][0]["source_run_id"] == first["run_id"]


def test_deleted_report_is_missing_on_history_without_stale_preview(worker_context, db):
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"])
    assert chat.settle(first["run_id"], "completed", "已保存")["status"] == "completed"
    db.delete(report)
    db.commit()
    headers, cid = worker_context
    history = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)
    delivery = history["projections"]["deliveries"][0]
    assert delivery["status"] == "missing" and not delivery["preview_url"]


def test_native_todos_questions_survive_refresh_and_cancel_closes_form(worker_context):
    first = prepare(worker_context, "查询财务")
    chat.append_event(first["run_id"], "run.started")
    chat.append_event(first["run_id"], "todo.updated", {"todos": [{"content": "核对指标", "status": "in_progress"}]})
    chat.append_event(first["run_id"], "question.pending", {"question_id": "native-question", "questions": [{"id": "metric", "question": "应收口径？"}]})
    headers, cid = worker_context
    detail = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)
    assert detail["projections"]["pending_questions"][0]["question_id"] == "native-question"
    assert detail["projections"]["todos"][0]["status"] == "in_progress"
    assert any(part["type"] == "native_todos" for part in detail["messages"][-1]["parts"])
    chat.settle(first["run_id"], "cancelled")
    assert not chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)["projections"]["pending_questions"]


def test_real_snapshot_creator_satisfies_task_without_artifact_or_requery(dashboard_case):
    """复用真实保存工具，确认验收契约没有与实际组件格式脱节。"""
    from app.modules.chat_artifacts.query_dashboard import create_dashboard_from_queries
    case = dashboard_case
    case.run.context_snapshot = {**case.run.context_snapshot,
        "business_task": {"required_deliveries": [{"type": "dashboard", "count": 1}]}}
    case.ctx.db.commit()
    result = create_dashboard_from_queries(case.ctx, case.tool, {"query_ids": [case.query.id], "title": "真实查询大屏"})
    status, task, produced = tasks.verify(case.ctx.db, case.run, "completed")
    assert status == "completed" and not task["missing_deliveries"]
    assert produced[0]["report_id"] == result.data["reportId"]
    assert produced[0]["scopes"][0]["returnedRows"] == 2


@pytest.mark.parametrize("binding", [{"valueField": "不存在的余额"}, {"columns": ["不存在的部门"]}, {"columns": "应收金额"}])
def test_saved_rows_do_not_validate_nonexistent_display_binding(worker_context, db, binding):
    """保存真实行不能掩盖实际画面绑定了不存在的字段。"""
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"])
    content = deepcopy(report.content)
    content["widgets"][0]["config"]["snapshotBinding"] = binding
    report.content = content
    db.commit()
    result = chat.settle(first["run_id"], "completed", "已完成")
    assert result["status"] == "partial"
    assert result["deliveries"][0]["status"] == "invalid"


@pytest.mark.parametrize("binding,value", [({}, "125.00"), ({"valueField": "应收金额"}, "1e10000"),
                                         ({"valueField": "应收金额"}, "1_000")])
def test_metric_requires_displayable_explicit_value(worker_context, db, binding, value):
    """空映射和浏览器无法表示的超大数值不能作为完成的大屏指标。"""
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"], rows=[{"应收金额": value}])
    content = deepcopy(report.content)
    content["widgets"][0]["component"] = "JStatsSummary"
    content["widgets"][0]["config"]["snapshotBinding"] = binding
    report.content = content
    db.commit()
    result = chat.settle(first["run_id"], "completed", "已完成")
    assert result["status"] == "partial"
    assert result["deliveries"][0]["status"] == "invalid"


def test_hidden_query_component_cannot_complete_dashboard(worker_context, db):
    """真实数据被全部隐藏时不能仍将大屏标记为完成。"""
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"])
    content = deepcopy(report.content)
    content["widgets"][0]["visible"] = False
    report.content = content
    db.commit()
    assert chat.settle(first["run_id"], "completed", "已完成")["status"] == "partial"


def test_delivery_rechecks_required_fields_after_widget_edit(worker_context, db):
    """后续编辑缩减表格投影后，交付必须重新核对实际显示列。"""
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"], rows=[{"应收金额": "125.00", "已收金额": "50.00"}])
    content = deepcopy(report.content)
    content["widgets"][0]["config"]["snapshotBinding"] = {"valueField": "应收金额", "columns": ["应收金额"]}
    report.content = content
    report.lineage = {**report.lineage, "queryScopes": [{"queryId": query.id, "requiredFields": ["应收金额", "已收金额"]}]}
    db.commit()
    result = chat.settle(first["run_id"], "completed", "两个金额都已显示")
    assert result["status"] == "partial"
    assert "已收金额" in result["deliveries"][0]["validation_error"]


@pytest.mark.parametrize("component", ["JText", "UnknownWidget", "JStatsSummary"])
def test_snapshot_mapping_must_match_actual_component_renderer(worker_context, db, component):
    """文字不渲染查询值，指标不渲染表格列，均不能冒充必需字段已显示。"""
    first = prepare(worker_context, "生成应收数字大屏")
    query, report = saved_dashboard(db, first["run_id"], rows=[{"应收金额": "125.00", "已收金额": "50.00"}])
    content = deepcopy(report.content)
    content["widgets"][0]["component"] = component
    content["widgets"][0]["config"]["snapshotBinding"] = {"valueField": "应收金额", "columns": ["应收金额", "已收金额"]}
    report.content = content
    report.lineage = {**report.lineage, "queryScopes": [{"queryId": query.id, "requiredFields": ["应收金额", "已收金额"]}]}
    db.commit()
    result = chat.settle(first["run_id"], "completed", "已完成")
    assert result["status"] == "partial"
    assert result["deliveries"][0]["status"] == "invalid"
