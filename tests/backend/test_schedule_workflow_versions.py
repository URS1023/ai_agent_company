"""Scheduled execution keeps its published graph across draft saves/releases."""
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_workflows import API as FLOWS, create_flow, graph, node, result

JOBS = "/api/v1/system-tools/jobs"


def make_job(client, headers, flow, **payload_changes):
    return client.post(JOBS, headers=headers, json={
        "code": "workflow." + uuid4().hex[:10], "name": "固定发布版本调度", "taskType": "workflow",
        "targetId": flow["id"], "scheduleKind": "interval", "scheduleExpression": "3600",
        "timezone": "Asia/Shanghai", "status": "paused",
        "payload": {"version": flow["version"], "mode": "published", "inputs": {}, **payload_changes},
    })


def publish(client, headers, flow):
    return result(client.post(FLOWS + f"/{flow['id']}/publish", headers=headers, json={"version": flow["version"]}))


@pytest.mark.parametrize("republish", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_schedule_keeps_published_revision_after_workflow_changes(client, administrator, db, republish, legacy):
    from app.modules.system_tools.models import ScheduledJob
    flow = publish(client, administrator, create_flow(client, administrator,
                   graph(node("message", "template", {"template": "已发布原版本"}))))
    response = make_job(client, administrator, flow)
    assert response.status_code == 201, response.text
    job = response.json()
    if legacy:
        from app.modules.workflows.models import WorkflowRevision
        saved = db.get(ScheduledJob, job["id"])
        saved.payload = {key: value for key, value in saved.payload.items() if key != "publishedRevision"}
        release = db.scalar(select(WorkflowRevision).where(
            WorkflowRevision.workflow_id == flow["id"], WorkflowRevision.revision == flow["publishedRevision"],
        ))
        release.snapshot = {key: value for key, value in release.snapshot.items() if key != "published"}
        db.commit()
        job["version"] = saved.version
    changed = deepcopy(flow["graph"])
    changed["nodes"][1]["config"]["template"] = "新版本内容"
    updated = result(client.put(FLOWS + f"/{flow['id']}", headers=administrator, json={
        "version": flow["version"], "name": flow["name"], "domains": flow["domains"], "graph": changed,
    }))
    if republish:
        publish(client, administrator, updated)
    ran = client.post(JOBS + f"/{job['id']}/run", headers=administrator,
                      json={"version": job["version"], "idempotencyKey": uuid4().hex})
    assert ran.status_code == 200, ran.text
    data = ran.json()
    assert data["status"] == "completed", data
    execution = data["output"]
    assert execution["revision"] == flow["publishedRevision"]
    assert execution["result"]["outputs"]["end"] == "已发布原版本"


@pytest.mark.parametrize("override", [
    {"mode": "draft"}, {"version": 1}, {"publishedRevision": 1},
    {"publishedRevision": True}, {"publishedRevision": "2"}, {"timeoutSeconds": 0},
])
def test_new_schedule_rejects_draft_or_stale_binding(client, administrator, override):
    flow = publish(client, administrator, create_flow(client, administrator, graph()))
    response = make_job(client, administrator, flow, **override)
    assert response.status_code in {409, 422}, response.text


def test_schedule_created_after_draft_edit_binds_release_and_respects_disable(client, administrator, db):
    from app.modules.workflows.models import WorkflowExecution
    flow = publish(client, administrator, create_flow(client, administrator, graph()))
    edited = result(client.put(FLOWS + f"/{flow['id']}", headers=administrator, json={
        "version": flow["version"], "name": "已保存的新草稿", "domains": flow["domains"], "graph": flow["graph"],
    }))
    response = make_job(client, administrator, edited)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["payload"]["version"] == edited["version"]
    assert job["payload"]["publishedRevision"] == flow["publishedRevision"]
    result(client.post(FLOWS + f"/{flow['id']}/disable", headers=administrator,
                       json={"version": edited["version"]}))
    ran = client.post(JOBS + f"/{job['id']}/run", headers=administrator,
                      json={"version": job["version"], "idempotencyKey": uuid4().hex})
    assert ran.status_code == 200, ran.text
    assert ran.json()["status"] == "failed"
    assert "停用" in ran.json()["errorSummary"]
    assert db.scalars(select(WorkflowExecution)).all() == []


def test_due_poll_executes_pinned_release_once_after_edit(client, administrator, db):
    from app.modules.system_tools.models import ScheduledJob
    from app.modules.system_tools.schedules import run_due_jobs
    from app.modules.workflows.models import WorkflowExecution
    flow = publish(client, administrator, create_flow(client, administrator,
                   graph(node("message", "template", {"template": "轮询仍运行发布版本"}))))
    response = make_job(client, administrator, flow)
    assert response.status_code == 201, response.text
    job = response.json()
    changed = deepcopy(flow["graph"])
    changed["nodes"][1]["config"]["template"] = "新草稿不应运行"
    result(client.put(FLOWS + f"/{flow['id']}", headers=administrator, json={
        "version": flow["version"], "name": flow["name"], "domains": flow["domains"], "graph": changed,
    }))
    now = datetime.utcnow()
    saved = db.get(ScheduledJob, job["id"])
    saved.status, saved.next_run_at = "active", now - timedelta(seconds=1)
    db.commit()
    polled = run_due_jobs(now=now)
    assert polled["count"] == 1, polled
    ran = polled["items"][0]
    assert ran["status"] == "completed", ran
    assert ran["triggerType"] == "schedule"
    assert ran["output"]["revision"] == flow["publishedRevision"]
    assert ran["output"]["result"]["outputs"]["end"] == "轮询仍运行发布版本"
    assert run_due_jobs(now=now)["count"] == 0
    db.expire_all()
    assert len(db.scalars(select(WorkflowExecution)).all()) == 1
