"""验证大屏沿用本轮查询来源、输入校验与执行失败的边界；只使用隔离 SQLite。"""
from types import SimpleNamespace
from datetime import datetime, timezone

from sqlalchemy import select

from app.errors import ValidationError
from app.models import AuthIdentity, DataSource, User
from app.ids import new_id
from app.modules.analytics.models import QueryHistory
from app.modules.business_ai import service
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.business_ai.dashboard_actions import queried_datasource_ids, dashboard_failure_metadata
from app.modules.business_ai.models import BusinessActionReceipt
from app.modules.runtime import service as runtime
from app.providers.tool.gateway import ToolGateway, build_context
from test_analytics import source


API = "/api/v1/ai/business-actions/dashboards/drafts"


def _run(client, headers, db):
    user = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin")).user_id
    cid = client.post("/api/chat/conversations", headers=headers, json={}).json()["id"]
    run = runtime.create_run(db, workspace_id=headers["X-Workspace-Id"], conversation_id=cid,
        user_id=user, context_snapshot={"user_request": "分析设备巡检并导出文档、生成数字大屏"})
    db.commit()
    ctx = auth_context_from_tool({"db": db, "workspace_id": run.workspace_id, "user_id": user})
    return ctx, run


def _receipt(db, ctx, run, source_id, *, status="success"):
    query = QueryHistory(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        datasource_id=source_id, query_text="设备巡检", status=status, result_json={})
    db.add(query)
    db.flush()
    call = runtime.record_tool_call(db, workspace_id=ctx.workspace_id, run_id=run.id,
        step_id=None, tool_version_id=None, tool_name="query_business_data",
        arguments={"datasource_id": source_id, "query": "设备巡检"})
    runtime.finish_tool_call(db, call, {"status": status, "data": {"queryId": query.id}},
        error_code="query_failed" if status != "success" else None)
    db.commit()
    return query, call


def test_dashboard_inherits_only_current_run_verified_source(client, administrator, source, db, monkeypatch):
    ctx, run = _run(client, administrator, db)
    _receipt(db, ctx, run, source["id"])
    captured = []
    def generate(_ctx, payload, **kwargs):
        captured.append(payload)
        return SimpleNamespace(model_dump=lambda **kw: {"datasourceIds": payload.datasource_ids})
    monkeypatch.setattr(service, "create_dashboard_draft", generate)
    context = build_context(db=db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
        run_id=run.id, selected_datasource_ids=[])
    result = ToolGateway(db).execute(ctx.workspace_id, "create_business_dashboard",
        {"requirement": "分析并生成大屏", "template_mode": "none"}, context)
    assert result.status == "success"
    assert captured[0].datasource_ids == [source["id"]]
    assert "前一步已验证的查询依据" in captured[0].requirement
    assert "分析设备巡检并导出文档" in captured[0].requirement
    other = runtime.create_run(db, workspace_id=ctx.workspace_id, conversation_id=run.conversation_id,
        user_id=ctx.user_id, context_snapshot={"user_request": "另一项任务"})
    db.commit()
    assert queried_datasource_ids(ctx, other) == []


def test_dashboard_does_not_inherit_failed_or_foreign_query(client, administrator, source, db):
    ctx, run = _run(client, administrator, db)
    query, call = _receipt(db, ctx, run, source["id"])
    query.status = "failed"
    db.commit()
    assert queried_datasource_ids(ctx, run) == []
    query.status = "success"
    now = datetime.now(timezone.utc)
    other = User(id=new_id(), display_name="隔离测试其他用户", status="active", created_at=now, updated_at=now)
    db.add(other)
    db.flush()
    query.owner_user_id = other.id
    db.commit()
    assert queried_datasource_ids(ctx, run) == []
    query.owner_user_id = ctx.user_id
    db.commit()
    _receipt(db, ctx, run, source["id"], status="failed")
    assert queried_datasource_ids(ctx, run) == []



def test_dashboard_does_not_guess_between_multiple_queried_sources(client, administrator, source, db):
    ctx, run = _run(client, administrator, db)
    _receipt(db, ctx, run, source["id"])
    now = datetime.now(timezone.utc)
    other = DataSource(id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        name="另一合成来源", provider="sqlite", connection_config={}, created_at=now, updated_at=now)
    db.add(other)
    db.flush()
    _receipt(db, ctx, run, other.id)
    assert queried_datasource_ids(ctx, run) == []


def test_missing_source_does_not_consume_key_and_corrected_input_can_start(client, administrator, source, db, monkeypatch):
    original = service.list_analytics_sources
    monkeypatch.setattr(service, "list_analytics_sources", lambda *a, **kw: [
        *original(*a, **kw), SimpleNamespace(id="second-authorized-source", name="另一授权来源")])
    payload = {"requestKey": "correct-source-with-same-key", "requirement": "设备巡检数字大屏", "templateMode": "none"}
    response = client.post(API, headers=administrator, json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["requires_user_input"] and body["execution_started"] is False
    assert db.scalar(select(BusinessActionReceipt.id)) is None
    started = []
    def fail_after_start(*args, **kwargs):
        started.append(1)
        raise ValidationError("合成生成失败，不执行业务查询")
    monkeypatch.setattr(service.report_generation, "generate", fail_after_start)
    corrected = {**payload, "datasourceIds": [source["id"]]}
    response = client.post(API, headers=administrator, json=corrected)
    assert response.status_code == 422 and started == [1]
    receipt = db.scalar(select(BusinessActionReceipt))
    assert receipt.status == "failed"
    repeated = client.post(API, headers=administrator, json=corrected)
    assert repeated.status_code == 409 and started == [1]


def test_dashboard_failure_guidance_preserves_other_deliveries_and_database_stop():
    missing = ValidationError("请选择来源", recovery="select_datasource", requires_user_input=True,
        execution_started=False, retryable=True, auto_retry=False)
    metadata = dashboard_failure_metadata(missing)
    assert metadata["requires_user_input"] and metadata["auto_retry"] is False
    assert "其他" in metadata["guidance"] and "本轮修复已结束" not in metadata["guidance"]
    failed = dashboard_failure_metadata(ValidationError("数据库超时", retryable=False))
    assert failed["retryable"] is False and "停止后续查询" in failed["guidance"]
