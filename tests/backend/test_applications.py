"""Applications: immutable releases, authorized resources/audiences and traced launches.

中文说明：应用（发布包）契约：发布版本不可变、只引用已授权资源、按受众授权、每次启动都有可追踪记录。
验证行为：发布后启动会固定发布修订；不可发布未就绪或他人的绑定；受限受众与底层权限同时生效。
隔离方式：client 夹具（内存 SQLite + TestClient），真实走接口与落库，不连业务库。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.ids import new_id
from app.models import KnowledgeBase, Run, RunStep
from app.modules.applications.models import ApplicationLaunch, ApplicationVersion, BusinessApplication
from app.modules.reports.models import Report
from test_system_foundation import activate_user, create_role, create_user


API = "/api/v1/applications"


def test_workflow_application_executes_inputs_and_pins_release_revision(client, administrator, db):
    from test_workflows import create_flow, graph
    from app.modules.workflows.models import WorkflowExecution
    flow_graph = graph()
    flow_graph["nodes"][0]["config"] = {"variables": [
        {"variable": "query", "label": "问题", "type": "text-input", "required": True},
        {"variable": "limit", "label": "数量", "type": "number", "default": 10},
    ]}
    flow = create_flow(client, administrator, flow_graph)
    published_flow = result(client.post(f"/api/v1/workflows/{flow['id']}/publish", headers=administrator,
                                        json={"version": flow["version"]}))
    request = payload(flow["id"])
    request["kind"] = "automation"
    request["spec"]["bindings"][0]["type"] = "workflow"
    request["spec"]["inputSchema"] = {"type": "object", "properties": {}, "additionalProperties": False}
    app = result(client.post(API, headers=administrator, json=request), 201)
    released = result(client.post(f"{API}/{app['id']}/publish", headers=administrator,
                                  json={"version": app["recordVersion"], "releaseVersion": 1}))
    assert released["publishedSpec"]["inputSchema"]["properties"]["query"]["title"] == "问题"
    assert released["publishedSpec"]["inputSchema"]["required"] == ["query"]
    assert client.post(f"{API}/{app['id']}/launch", headers=administrator,
                       json={"input": {}, "idempotencyKey": "invalid-application-input"}).status_code == 422
    flow_graph["nodes"][0]["config"]["variables"][1]["default"] = 99
    changed = result(client.put(f"/api/v1/workflows/{flow['id']}", headers=administrator, json={
        "name": flow["name"], "domains": flow["domains"], "graph": flow_graph, "version": published_flow["version"],
    }))
    result(client.post(f"/api/v1/workflows/{flow['id']}/publish", headers=administrator, json={"version": changed["version"]}))
    launch_body = {"input": {"query": "真实应用参数"}, "idempotencyKey": "application-release-test"}
    launched = result(client.post(f"{API}/{app['id']}/launch", headers=administrator, json=launch_body))
    executed = db.scalar(select(WorkflowExecution).where(WorkflowExecution.run_id == launched["runId"]))
    assert executed.revision == published_flow["publishedRevision"]
    assert executed.input_json == {"query": "真实应用参数"}
    assert executed.result_json["outputs"]["end"] == {"query": "真实应用参数", "limit": 10}
    assert launched["status"] == "completed" and f"execution={executed.id}" in launched["launchPath"]
    repeated = result(client.post(f"{API}/{app['id']}/launch", headers=administrator, json=launch_body))
    assert repeated["id"] == launched["id"] and repeated["runId"] == launched["runId"]
    history = result(client.get(f"{API}/{app['id']}/launches", headers=administrator))
    assert len(history) == 1 and history[0]["status"] == "completed"


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def owner_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["user"]["id"]


def seed_resources(client, administrator, db, *, report_status="published"):
    owner = owner_id(client, administrator)
    report = Report(
        id=new_id(), workspace_id=administrator["X-Workspace-Id"], owner_user_id=owner,
        name="财务与设备运营大屏", description="正式业务大屏", datasource_ids=[], content={"widgets": []},
        status=report_status, template_mode="none", lineage={}, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    knowledge = KnowledgeBase(
        id=new_id(), workspace_id=administrator["X-Workspace-Id"], owner_user_id=owner,
        name="设备运维知识", description="设备运维规则", visibility="workspace", status="active",
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add_all([report, knowledge])
    db.commit()
    return report, knowledge


def payload(report_id, knowledge_id=None, *, code="finance-equipment", visibility="workspace", audience=None):
    bindings = [{
        "type": "dashboard", "id": report_id, "role": "entry",
        "purpose": "应用首页", "required": True,
    }]
    if knowledge_id:
        bindings.append({
            "type": "knowledge", "id": knowledge_id, "role": "dependency",
            "purpose": "指标说明", "required": True,
        })
    return {
        "code": code,
        "name": "财务与设备运营应用",
        "description": "统一查看财务和设备运营指标",
        "kind": "dashboard",
        "visibility": visibility,
        "spec": {
            "welcomeMessage": "查看财务与设备运营数据",
            "instructions": "使用已发布大屏，并保留知识口径来源。",
            "bindings": bindings,
            "audience": audience or {"mode": visibility, "roleIds": [], "userIds": []},
            "inputSchema": {
                "type": "object",
                "properties": {"period": {"type": "string"}},
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object", "properties": {}},
        },
    }


def test_application_release_versions_launch_trace_disable_and_archive(client, administrator, db):
    report, knowledge = seed_resources(client, administrator, db)
    catalog = result(client.get(API + "/catalog", headers=administrator))
    assert any(user["username"] == "workspace-admin" for user in catalog["users"])
    created = result(client.post(API, headers=administrator, json=payload(report.id, knowledge.id)), 201)
    assert created["status"] == "draft" and created["latestVersion"] == 1
    assert created["publishedVersion"] is None
    assert created["latestSpec"]["bindings"][0]["path"] == f"/report/view/{report.id}"

    published = result(client.post(
        API + f"/{created['id']}/publish", headers=administrator,
        json={"version": created["recordVersion"], "releaseVersion": 1},
    ))
    assert published["status"] == "published" and published["publishedVersion"] == 1

    launched = result(client.post(
        API + f"/{created['id']}/launch", headers=administrator,
        json={"channel": "ui", "input": {"period": "2026-08"}},
    ))
    assert launched["launchPath"] == f"/report/view/{report.id}"
    assert launched["manifest"]["applicationVersion"] == 1
    assert set(launched["manifest"]["requiredPermissions"]) == {"dashboards.view", "knowledge.view"}
    run = db.get(Run, launched["runId"])
    assert run.status == "completed" and run.model_profile == "application"
    step = db.scalar(select(RunStep).where(RunStep.run_id == run.id))
    assert step.status == "completed" and step.step_type == "application_launch"
    launch = db.scalar(select(ApplicationLaunch).where(ApplicationLaunch.run_id == run.id))
    assert launch.input_json == {"period": "2026-08"} and launch.application_version_id

    updated_payload = payload(report.id, knowledge.id)
    updated_payload["name"] = "财务与设备运营应用 2.0"
    updated_payload["version"] = published["recordVersion"] + 1  # launch increments the application record version
    updated = result(client.put(API + f"/{created['id']}", headers=administrator, json=updated_payload))
    assert updated["latestVersion"] == 2 and updated["latestVersionStatus"] == "draft"
    assert updated["publishedVersion"] == 1 and updated["status"] == "published"
    stale = client.put(API + f"/{created['id']}", headers=administrator, json=updated_payload)
    assert stale.status_code == 409

    released_v2 = result(client.post(
        API + f"/{created['id']}/publish", headers=administrator,
        json={"version": updated["recordVersion"], "releaseVersion": 2},
    ))
    assert released_v2["publishedVersion"] == 2
    db.expire_all()
    versions = db.scalars(select(ApplicationVersion).where(
        ApplicationVersion.application_id == created["id"],
    ).order_by(ApplicationVersion.version_number)).all()
    assert [item.status for item in versions] == ["retired", "published"]

    disabled = result(client.post(
        API + f"/{created['id']}/disable", headers=administrator,
        json={"version": released_v2["recordVersion"]},
    ))
    assert disabled["status"] == "disabled"
    assert client.post(
        API + f"/{created['id']}/launch", headers=administrator,
        json={"channel": "ui", "input": {}},
    ).status_code == 422
    archived = result(client.delete(
        API + f"/{created['id']}?version={disabled['recordVersion']}", headers=administrator,
    ))
    assert archived["deleted"] is True
    db.expire_all()
    assert db.get(BusinessApplication, created["id"]).status == "archived"


def test_application_publish_rejects_unready_or_foreign_binding(client, administrator, db):
    draft_report, _knowledge = seed_resources(client, administrator, db, report_status="draft")
    created = result(client.post(API, headers=administrator, json=payload(draft_report.id)), 201)
    response = client.post(
        API + f"/{created['id']}/publish", headers=administrator,
        json={"version": created["recordVersion"], "releaseVersion": 1},
    )
    assert response.status_code == 422 and "已发布" in response.text

    missing = payload("missing-resource-id", code="missing-binding")
    response = client.post(API, headers=administrator, json=missing)
    assert response.status_code == 404
    mismatch = payload(draft_report.id, code="bad-audience", visibility="workspace", audience={"mode": "private"})
    assert client.post(API, headers=administrator, json=mismatch).status_code == 422


def test_restricted_application_audience_and_underlying_permissions(client, administrator, db):
    report, _knowledge = seed_resources(client, administrator, db)
    allowed_role = create_role(client, administrator, code="app_allowed", permissions=[
        "home.view", "applications.view", "applications.execute", "dashboards.view",
    ], scope="workspace")
    outsider_role = create_role(client, administrator, code="app_outsider", permissions=[
        "home.view", "applications.view", "applications.execute", "dashboards.view",
    ], scope="workspace")
    no_dashboard_role = create_role(client, administrator, code="app_no_dashboard", permissions=[
        "home.view", "applications.view", "applications.execute",
    ], scope="workspace")
    create_user(client, administrator, [allowed_role["id"]], username="application-allowed")
    create_user(client, administrator, [outsider_role["id"]], username="application-outsider")
    create_user(client, administrator, [allowed_role["id"], no_dashboard_role["id"]], username="application-mixed")

    restricted = payload(
        report.id, code="restricted-dashboard", visibility="restricted",
        audience={"mode": "restricted", "roleIds": [allowed_role["id"]], "userIds": []},
    )
    created = result(client.post(API, headers=administrator, json=restricted), 201)
    result(client.post(
        API + f"/{created['id']}/publish", headers=administrator,
        json={"version": created["recordVersion"], "releaseVersion": 1},
    ))

    allowed = activate_user(client, username="application-allowed")
    launched = result(client.post(
        API + f"/{created['id']}/launch", headers=allowed, json={"channel": "ui", "input": {}},
    ))
    assert launched["launchPath"] == f"/report/view/{report.id}"

    outsider = activate_user(client, username="application-outsider")
    assert client.get(API + f"/{created['id']}", headers=outsider).status_code == 403
    listed = result(client.get(API, headers=outsider))
    assert all(item["id"] != created["id"] for item in listed["items"])

    catalog = result(client.get(API + "/catalog", headers=allowed))
    dashboards = next(group for group in catalog["groups"] if group["type"] == "dashboard")
    assert any(item["id"] == report.id and item["ready"] for item in dashboards["items"])
    assert catalog["roles"] == [] and catalog["users"] == []
