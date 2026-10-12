"""Operations center: scoped traces, redaction, audit review and safe recovery.

中文说明：运维中心：按范围的执行轨迹、脱敏、审计复核与安全恢复。
隔离方式：client 夹具（内存 SQLite + TestClient），不连真实业务库、不调用模型。
边界：恢复只处理有证据的孤儿任务，等待中/执行中的记录保留。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.ids import new_id
from app.models import (
    AuditLog,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    ModelCall,
    Run,
    RunEvent,
    RunStep,
    ToolCall,
    Workspace,
)
from app.modules.applications.models import ApplicationLaunch, ApplicationVersion, BusinessApplication
from app.modules.knowledge_graph.models import BotDebugRun, BusinessBot
from test_system_foundation import activate_user, create_role, create_user


API = "/api/v1/operations"
_EVENT_ID = 0


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["user"]["id"]


def seed_run(db, workspace_id, owner_id, *, status="completed", stale=False, secret=False):
    global _EVENT_ID
    _EVENT_ID += 1
    now = datetime.utcnow()
    started = now - timedelta(hours=1) if stale else now - timedelta(seconds=4)
    completed = None if status in {"created", "running", "waiting"} else now
    run = Run(
        id=new_id(), workspace_id=workspace_id, conversation_id="",
        started_by_user_id=owner_id, status=status, model_profile="operations-test",
        context_snapshot={
            "password": "do-not-return",
            "nested": {"access_token": "hidden", "normal": "retained"},
        } if secret else {},
        model_usage={"inputTokens": 12}, cost=0.02,
        created_at=started, started_at=started, completed_at=completed,
    )
    db.add(run)
    db.flush()
    db.add(RunStep(
        id=new_id(), workspace_id=workspace_id, run_id=run.id, sequence=1,
        step_type="tool", name="读取设备", status="completed",
        input_json={"password": "step-secret"} if secret else {},
        output_json={"status": "ok"}, retry_count=0,
        started_at=started, completed_at=started + timedelta(seconds=1),
    ))
    db.add(ToolCall(
        id=new_id(), workspace_id=workspace_id, run_id=run.id,
        tool_name="read_device", arguments_json={"authorization": "Bearer hidden"} if secret else {},
        result_json={"value": 42}, status="completed",
        started_at=started, completed_at=started + timedelta(seconds=1),
    ))
    db.add(ModelCall(
        id=new_id(), workspace_id=workspace_id, run_id=run.id,
        provider="local", model="test-model", status="completed",
        usage_json={"tokens": 8}, metadata_json={"api_key": "hidden"} if secret else {},
        started_at=started, completed_at=started + timedelta(seconds=1),
    ))
    db.add(RunEvent(
        id=_EVENT_ID, workspace_id=workspace_id, run_id=run.id, event_type="run.completed",
        payload={"token": "hidden", "state": "done"} if secret else {"state": "done"},
        created_at=started + timedelta(seconds=1), sequence=1,
    ))
    db.flush()
    return run


def test_operations_run_scope_detail_and_sensitive_trace_redaction(client, administrator, db):
    workspace_id = administrator["X-Workspace-Id"]
    administrator_id = user_id(client, administrator)
    role = create_role(client, administrator, "operations_self", [
        "home.view", "operations.view", "runs.view",
    ], "self")
    user = create_user(client, administrator, [role["id"]], username="operations-self")
    headers = activate_user(client, "operations-self")
    own_run = seed_run(db, workspace_id, user["id"], secret=True)
    foreign_run = seed_run(db, workspace_id, administrator_id)
    db.commit()

    listed = result(client.get(API + "/runs", headers=headers))
    assert listed["total"] == 1 and listed["items"][0]["id"] == own_run.id
    assert listed["items"][0]["stepCount"] == 1
    assert client.get(API + f"/runs/{foreign_run.id}", headers=headers).status_code == 404

    detail = result(client.get(API + f"/runs/{own_run.id}", headers=headers))
    assert detail["context"]["password"] == "***"
    assert detail["context"]["nested"] == {"access_token": "***", "normal": "retained"}
    assert detail["steps"][0]["input"]["password"] == "***"
    assert detail["toolCalls"][0]["arguments"]["authorization"] == "***"
    assert detail["modelCalls"][0]["metadata"]["api_key"] == "***"
    assert detail["events"][0]["payload"]["token"] == "***"
    summary = result(client.get(API + "/summary", headers=headers))
    assert summary["service"]["database"] == "healthy"
    assert summary["runs"]["total"] == 1 and summary["canViewAudit"] is False


def test_operations_audit_scope_filters_and_actor_labels(client, administrator, db):
    workspace_id = administrator["X-Workspace-Id"]
    administrator_id = user_id(client, administrator)
    db.add_all([
        AuditLog(
            id=new_id(), workspace_id=workspace_id, actor_user_id=administrator_id,
            action="application.publish", resource_type="applications", resource_id="app-123",
            run_id=None, ip_address="127.0.0.1", metadata_json={"token": "hidden", "version": 1},
            created_at=datetime.utcnow(),
        ),
        AuditLog(
            id=new_id(), workspace_id=workspace_id, actor_user_id=None,
            action="system.tick", resource_type="runtime", resource_id=None,
            run_id=None, ip_address=None, metadata_json={}, created_at=datetime.utcnow(),
        ),
    ])
    db.commit()
    logs = result(client.get(API + "/audit?keyword=app-123", headers=administrator))
    assert logs["total"] == 1
    assert logs["items"][0]["actor"]["username"] == "workspace-admin"
    assert logs["items"][0]["metadata"] == {"token": "***", "version": 1}
    assert result(client.get(API + "/audit?action=system.tick", headers=administrator))["total"] == 1


def test_recovery_is_dry_run_by_default_and_never_crosses_workspace(client, administrator, db):
    import socket
    workspace_id = administrator["X-Workspace-Id"]
    administrator_id = user_id(client, administrator)
    local = seed_run(db, workspace_id, administrator_id, status="running", stale=True)
    local.context_snapshot = {"chatLease": {"host": socket.gethostname(), "runId": local.id}}
    current_workspace = db.get(Workspace, workspace_id)
    foreign_workspace = Workspace(
        id=new_id(), organization_id=current_workspace.organization_id,
        name="隔离工作区", created_at=datetime.utcnow(),
    )
    db.add(foreign_workspace)
    db.flush()
    foreign = seed_run(db, foreign_workspace.id, administrator_id, status="running", stale=True)
    db.commit()

    preview = result(client.post(API + "/runs/recover", headers=administrator, json={
        "thresholdSeconds": 60, "dryRun": True,
    }))
    assert preview["dryRun"] is True and [item["runId"] for item in preview["items"]] == [local.id]
    db.expire_all()
    assert db.get(Run, local.id).status == "running" and db.get(Run, foreign.id).status == "running"

    recovered = result(client.post(API + "/runs/recover", headers=administrator, json={
        "thresholdSeconds": 60, "dryRun": False,
    }))
    assert recovered["count"] == 1 and recovered["dryRun"] is False
    db.expire_all()
    assert db.get(Run, local.id).status == "failed"
    assert db.get(Run, foreign.id).status == "running"
    assert any(row.action == "operations.run.recover" for row in db.query(AuditLog).all())


def test_recovery_requires_both_operation_and_run_edit_permissions(client, administrator, db):
    role = create_role(client, administrator, "operations_readonly", [
        "home.view", "operations.view", "runs.view",
    ], "self")
    user = create_user(client, administrator, [role["id"]], username="operations-readonly")
    headers = activate_user(client, "operations-readonly")
    run = seed_run(db, administrator["X-Workspace-Id"], user["id"], status="running", stale=True)
    db.commit()
    denied = client.post(API + "/runs/recover", headers=headers, json={
        "thresholdSeconds": 60, "dryRun": False,
    })
    assert denied.status_code == 403
    db.expire_all()
    assert db.get(Run, run.id).status == "running"


def test_business_operations_use_real_scoped_records_and_complete_series(client, administrator, db):
    """Every dashboard returns contiguous categories and equally sized real data series."""
    workspace_id = administrator["X-Workspace-Id"]
    administrator_id = user_id(client, administrator)
    now = datetime.utcnow()

    knowledge = KnowledgeBase(
        id=new_id(), workspace_id=workspace_id, owner_user_id=administrator_id,
        name="设备故障知识", description="运营看板测试", visibility="workspace", status="active",
        created_at=now, updated_at=now,
    )
    document = KnowledgeDocument(
        id=new_id(), workspace_id=workspace_id, knowledge_base_id=knowledge.id,
        file_id=new_id(), status="failed", parser_version="v1", error_summary="文档格式不受支持",
        created_at=now, updated_at=now,
    )
    chunk = KnowledgeChunk(
        id=new_id(), workspace_id=workspace_id, knowledge_base_id=knowledge.id,
        document_id=document.id, ordinal=1, text="轴承温度超过阈值", embedding_status="failed",
        created_at=now,
    )
    bot = BusinessBot(
        workspace_id=workspace_id, owner_user_id=administrator_id, name="设备运维 Bot",
        description="", bot_type="knowledge", instructions="仅使用设备知识", status="published",
        published_revision=1,
    )
    db.add_all([knowledge, document, chunk, bot])
    db.flush()
    bot_run = BotDebugRun(
        workspace_id=workspace_id, owner_user_id=administrator_id, bot_id=bot.id,
        bot_revision=1, channel="debug", mode="retrieval", query="异常原因",
        answer="", references=[], status="failed", error_message="模型暂不可用", duration_ms=120,
    )
    application = BusinessApplication(
        workspace_id=workspace_id, owner_user_id=administrator_id, code="operations-dashboard",
        name="设备运营应用", description="", kind="dashboard", visibility="workspace",
        status="published", launch_count=1,
    )
    db.add_all([bot_run, application])
    db.flush()
    version = ApplicationVersion(
        workspace_id=workspace_id, owner_user_id=administrator_id,
        application_id=application.id, version_number=1, status="published", spec_json={},
    )
    run = Run(
        id=new_id(), workspace_id=workspace_id, conversation_id="",
        started_by_user_id=administrator_id, status="failed", model_profile="application",
        created_at=now, started_at=now, completed_at=now,
    )
    db.add_all([version, run])
    db.flush()
    launch = ApplicationLaunch(
        workspace_id=workspace_id, owner_user_id=administrator_id,
        application_id=application.id, application_version_id=version.id, run_id=run.id,
        channel="ui", input_json={}, manifest_json={}, launch_path="/applications", status="failed",
    )
    db.add(launch)
    db.commit()

    for domain in ("knowledge", "bots", "applications"):
        dashboard = result(client.get(
            API + "/business", headers=administrator, params={"domain": domain, "days": 7},
        ))
        assert dashboard["domain"] == domain and dashboard["days"] == 7
        assert len(dashboard["trend"]["categories"]) == 7
        assert len(set(dashboard["trend"]["categories"])) == 7
        assert dashboard["trend"]["series"]
        assert all(len(series["data"]) == 7 for series in dashboard["trend"]["series"])
        assert len(dashboard["metrics"]) == 4
        assert dashboard["topItems"]

    knowledge_dashboard = result(client.get(
        API + "/business", headers=administrator, params={"domain": "knowledge", "days": 7},
    ))
    assert knowledge_dashboard["metrics"][3]["value"] == 2
    assert knowledge_dashboard["issues"][0]["detail"] == "文档格式不受支持"
    bot_dashboard = result(client.get(
        API + "/business", headers=administrator, params={"domain": "bots", "days": 7},
    ))
    assert bot_dashboard["metrics"][3]["value"] == 100
    assert bot_dashboard["issues"][0]["detail"] == "模型暂不可用"
    application_dashboard = result(client.get(
        API + "/business", headers=administrator, params={"domain": "applications", "days": 7},
    ))
    assert application_dashboard["metrics"][3]["value"] == 1
    assert application_dashboard["issues"][0]["path"] == f"/operations?id={run.id}"
    filtered = result(client.get(API + "/business", headers=administrator, params={
        "domain": "applications", "objectId": application.id, "days": 7,
    }))
    assert filtered["objectId"] == application.id
    assert [item["id"] for item in filtered["topItems"]] == [application.id]
    assert any(item["id"] == application.id for item in filtered["availableObjects"])
    assert client.get(API + "/business", headers=administrator, params={
        "domain": "applications", "objectId": new_id(), "days": 7,
    }).status_code == 404


def test_business_operations_require_selected_domain_permission(client, administrator):
    role = create_role(client, administrator, "operations_without_bot", [
        "home.view", "operations.view", "knowledge.view",
    ], "self")
    create_user(client, administrator, [role["id"]], username="operations-no-bot")
    headers = activate_user(client, "operations-no-bot")
    assert client.get(
        API + "/business", headers=headers, params={"domain": "knowledge", "days": 7},
    ).status_code == 200
    assert client.get(
        API + "/business", headers=headers, params={"domain": "bots", "days": 7},
    ).status_code == 403
