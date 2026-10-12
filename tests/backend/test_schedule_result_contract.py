"""隔离 SQLite 验证调度结果语义、归档停止派发与正式工作流输入契约。"""
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_schedule_workflow_versions import JOBS, make_job, publish
from test_workflows import API, create_flow, graph, node, result


@pytest.mark.parametrize("status", ["failed", "waiting", "cancelled", "running", "cancel_requested", "completed"])
def test_scheduler_preserves_workflow_business_status(client, administrator, monkeypatch, status):
    from app.modules.system_tools import schedules
    flow = publish(client, administrator, create_flow(client, administrator, graph()))
    job = make_job(client, administrator, flow).json()
    monkeypatch.setattr(schedules, "_dispatch", lambda *args: {
        "id": "child-execution", "status": status, "errorMessage": "真实工作流失败" if status == "failed" else ""})
    response = client.post(JOBS + f"/{job['id']}/run", headers=administrator,
                           json={"version": job["version"], "idempotencyKey": uuid4().hex})
    assert response.status_code == 200, response.text
    execution = response.json()
    assert execution["status"] == status
    assert bool(execution["completedAt"]) == (status in {"failed", "cancelled", "completed"})
    if status == "failed":
        assert execution["errorSummary"] == "真实工作流失败"


@pytest.mark.parametrize("scope", ["workspace", "organization"])
def test_archived_scope_pauses_due_job_before_dispatch(client, administrator, db, monkeypatch, scope):
    from app.models import Workspace
    from app.modules.system.models import WorkspaceLifecycle, OrganizationProfile
    from app.modules.system_tools.models import ScheduledJob, ScheduledJobRun
    from app.modules.system_tools import schedules
    created = client.post(JOBS, headers=administrator, json={
        "code": "isolated.archived", "name": "归档调度检查", "taskType": "health",
        "scheduleKind": "interval", "scheduleExpression": "3600", "timezone": "Asia/Shanghai", "status": "active"})
    assert created.status_code == 201, created.text
    job = db.get(ScheduledJob, created.json()["id"])
    workspace = db.get(Workspace, job.workspace_id)
    if scope == "workspace":
        lifecycle = db.scalar(select(WorkspaceLifecycle).where(WorkspaceLifecycle.workspace_id == workspace.id))
        lifecycle.status = "archived"
    else:
        profile = db.scalar(select(OrganizationProfile).where(OrganizationProfile.organization_id == workspace.organization_id))
        profile.status = "archived"
    job.next_run_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(schedules, "_dispatch", lambda *args: pytest.fail("归档范围不得派发业务动作"))
    response = schedules.run_due_jobs()
    assert response["items"][0]["status"] == "paused"
    db.expire_all()
    assert db.get(ScheduledJob, job.id).next_run_at is None
    assert db.scalars(select(ScheduledJobRun)).all() == []


def test_published_contract_ignores_draft_and_revision_list_limit(client, administrator, db):
    from app.modules.workflows.models import WorkflowRevision
    published_graph = graph(node("published", "template", {"template": "发布值"}))
    flow = publish(client, administrator, create_flow(client, administrator, published_graph))
    changed = deepcopy(flow["graph"])
    changed["nodes"][1]["config"]["template"] = "草稿值"
    edited = result(client.put(API + f"/{flow['id']}", headers=administrator, json={
        "version": flow["version"], "name": flow["name"], "domains": flow["domains"], "graph": changed}))
    source = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_id == flow["id"]))
    for revision in range(100, 201):
        db.add(WorkflowRevision(workspace_id=source.workspace_id, workflow_id=flow["id"], revision=revision,
                               snapshot=source.snapshot, reason="隔离分页夹具", owner_user_id=source.owner_user_id))
    db.commit()
    contract = result(client.get(API + f"/{flow['id']}/published-revision", headers=administrator))
    assert contract["revision"] == flow["publishedRevision"] and contract["version"] == edited["version"]
    assert contract["graph"]["nodes"][1]["config"]["template"] == "发布值"
    unpublished = create_flow(client, administrator, graph())
    assert client.get(API + f"/{unpublished['id']}/published-revision", headers=administrator).status_code == 422
