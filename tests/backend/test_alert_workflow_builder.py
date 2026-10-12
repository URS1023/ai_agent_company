"""Ordinary alert pages publish a complete, version-pinned workflow without raw graph editing.

中文说明：验证普通预警页面（HTTP/数据库向导）能在不手写图 JSON 的前提下发布完整流程，
且发布版本被固定（含调度跑的是已发布版本），字段/来源过期时拒绝并保持无半成品数据。
隔离方式：client 夹具（内存 SQLite + TestClient），按真实向导接口走完整链路。
边界：来源过期、规则字段缺失时要拒绝而不是产生部分记录。
"""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.modules.alerts.models import AlertWorkflowCreation
from app.modules.analytics.models import QueryHistory
from app.modules.system_tools.models import InboxMessage, ScheduledJob
from app.modules.workflows.models import Workflow, WorkflowRevision
from test_alerts import API, equipment, policy
from test_analytics import source
from test_collection import create_source, upstream
from test_workflows import result


def point(client, headers, device, code, name, field, point_type="number"):
    return result(client.post(API + "/points", headers=headers, json={
        "deviceId": device["id"], "code": code, "name": name,
        "fieldName": field, "pointType": point_type,
    }), 201)


def http_setup(client, headers, upstream, **source_overrides):
    source, _ = create_source(client, headers, upstream, allowAutomatic=True, **source_overrides, fields=[
        {"name": "device", "path": "$.device", "type": "string", "required": True},
        {"name": "amount", "path": "$.amount", "type": "decimal", "required": True},
    ])
    device = equipment(client, headers, code="ALERT-WIZARD-" + uuid4().hex[:8], datasourceId=source["id"])
    device_point = point(client, headers, device, "KEY", "设备标识", "device", "string")
    amount_point = point(client, headers, device, "TEMP", "温度", "amount")
    rule, _ = policy(client, headers, device, rules=[{
        "id": "double-high", "name": "折算值超限", "message": "检查设备负荷", "severity": "critical",
        "expression": {"op": "gte", "args": [{"field": "doubled"}, {"value": 200}]},
    }])
    payload = {
        "requestKey": uuid4().hex,
        "name": "压缩机自动预警",
        "description": "普通业务页一次配置并发布。",
        "ruleId": rule["id"], "ruleRevision": rule["version"],
        "source": {"datasourceId": source["id"], "datasourceVersion": source["version"]},
        "pointIds": [device_point["id"], amount_point["id"]],
        "calculations": [{"name": "doubled", "formula": "amount * 2"}],
        "notification": {"audience": "executor", "outcomes": ["issues", "no_data"], "minimumSeverity": "warning"},
        "schedule": {"scheduleKind": "interval", "scheduleExpression": "3600", "timezone": "Asia/Shanghai", "status": "paused"},
    }
    return payload, source


@pytest.mark.parametrize("confirmation", ["omitted", None, False, "false", "true", 0, 1])
def test_post_wizard_requires_explicit_strict_confirmation_before_creating_records(
        client, administrator, upstream, db, confirmation):
    """自动采集许可不代替 HTTP 副作用确认，拒绝时不留流程或调度半成品。"""
    payload, _ = http_setup(client, administrator, upstream, method="POST", bodies=[{}])
    if confirmation != "omitted":
        payload["confirmHttpWrites"] = confirmation
    response = client.post(API + "/workflows", headers=administrator, json=payload)
    assert response.status_code == 422, response.text
    for model in (Workflow, WorkflowRevision, AlertWorkflowCreation, ScheduledJob):
        assert db.scalars(select(model)).all() == []
    assert upstream[1] == []


def test_confirmed_post_wizard_pins_consent_and_executes_without_database_write_permission(
        client, administrator, upstream, db):
    """独立 HTTP 许可保存到固定节点版本，运行级数据库写许可保持关闭。"""
    payload, source = http_setup(client, administrator, upstream, method="POST", bodies=[{}])
    payload["confirmHttpWrites"] = True
    created = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    expected = {"datasourceId": source["id"], "datasourceVersion": source["version"], "confirmWrites": True}
    assert created["workflow"]["graph"]["nodes"][1]["config"] == expected
    revision = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_id == created["workflow"]["id"]))
    assert revision.snapshot["graph"]["nodes"][1]["config"] == expected
    scheduled = db.get(ScheduledJob, created["schedule"]["id"])
    assert scheduled.payload["confirmWrites"] is False
    assert upstream[1] == []
    execution = result(client.post(
        "/api/v1/workflows/" + created["workflow"]["id"] + "/runs", headers=administrator,
        json={"version": 1, "mode": "published", "idempotencyKey": uuid4().hex,
              "inputs": {}, "confirmWrites": False, "timeoutSeconds": 120},
    ))
    assert execution["status"] == "completed", execution
    assert execution["result"]["outputs"]["end"]["outcome"] == "issues"
    assert len(upstream[1]) == 1 and upstream[1][0]["body"] == {}
    revoked = deepcopy(payload); revoked["confirmHttpWrites"] = False
    assert client.post(API + "/workflows", headers=administrator, json=revoked).status_code == 409


def test_http_wizard_publishes_complete_flow_then_generates_report_problem_and_inbox(
        client, administrator, upstream, db):
    payload, source = http_setup(client, administrator, upstream)
    created = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    assert created["workflow"]["status"] == "published"
    assert created["workflow"]["version"] == created["workflow"]["publishedRevision"] == 1
    assert created["selectedFields"] == ["device", "amount"]
    assert [item["kind"] for item in created["workflow"]["graph"]["nodes"]] == [
        "start", "http-request", "jsonpath-extract", "arithmetic", "alert-evaluate", "alert-notify", "end",
    ]
    source_config = created["workflow"]["graph"]["nodes"][1]["config"]
    assert source_config == {"datasourceId": source["id"], "datasourceVersion": source["version"]}
    assert created["schedule"]["status"] == "paused" and created["schedule"]["targetId"] == created["workflow"]["id"]
    assert upstream[1] == []

    execution = result(client.post(
        "/api/v1/workflows/" + created["workflow"]["id"] + "/runs", headers=administrator,
        json={"version": 1, "mode": "published", "idempotencyKey": uuid4().hex,
              "inputs": {}, "confirmWrites": False, "timeoutSeconds": 120},
    ))
    output = execution["result"]["outputs"]["end"]
    assert output["outcome"] == "issues" and output["data"][0]["doubled"] == "246.900"
    assert output["notification"]["sent"] and output["notification"]["recipientCount"] == 1
    inbox = client.get("/api/v1/system-tools/inbox", headers=administrator).json()
    message = next(item for item in inbox["items"] if item["messageType"] == "alert")
    assert output["alertReportId"] in message["body"] and message["severity"] == "critical"
    assert len(upstream[1]) == 1

    repeated = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    assert repeated["reused"] and repeated["workflow"]["id"] == created["workflow"]["id"]
    assert len(db.scalars(select(Workflow)).all()) == 1
    assert len(db.scalars(select(AlertWorkflowCreation)).all()) == 1
    assert len(db.scalars(select(ScheduledJob)).all()) == 1
    assert len(db.scalars(select(InboxMessage).where(InboxMessage.message_type == "alert")).all()) == 1
    changed = deepcopy(payload); changed["name"] = "另一个流程"
    assert client.post(API + "/workflows", headers=administrator, json=changed).status_code == 409


def test_database_wizard_validates_metadata_without_running_rows_then_executes_real_query(
        client, administrator, source, db):
    device = equipment(client, administrator, code="ALERT-DB-" + uuid4().hex[:8], datasourceId=source["id"])
    department = point(client, administrator, device, "DEPT", "部门", "department", "string")
    amount = point(client, administrator, device, "AMOUNT", "金额", "amount")
    rule, _ = policy(client, administrator, device, recordKeyField="department")
    payload = {
        "requestKey": uuid4().hex, "name": "数据库设备预警", "ruleId": rule["id"], "ruleRevision": 1,
        "source": {"datasourceId": source["id"], "datasourceVersion": source["version"],
                   "query": "SELECT department, amount FROM sales WHERE amount IS NOT NULL", "maxRows": 100},
        "pointIds": [department["id"], amount["id"]],
    }
    created = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    assert [node["kind"] for node in created["workflow"]["graph"]["nodes"]] == [
        "start", "database-query", "jsonpath-extract", "alert-evaluate", "end",
    ]
    assert db.scalars(select(QueryHistory)).all() == []
    execution = result(client.post(
        "/api/v1/workflows/" + created["workflow"]["id"] + "/runs", headers=administrator,
        json={"version": 1, "mode": "published", "idempotencyKey": uuid4().hex, "inputs": {}},
    ))
    assert execution["result"]["outputs"]["end"]["summary"]["issues"] == 4
    assert len(db.scalars(select(QueryHistory)).all()) == 1


def test_wizard_schedule_executes_pinned_flow_after_draft_save(client, administrator, upstream):
    payload, _ = http_setup(client, administrator, upstream)
    created = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    flow = created["workflow"]
    jobs = client.get("/api/v1/system-tools/jobs", headers=administrator).json()["items"]
    job = next(item for item in jobs if item["id"] == created["schedule"]["id"])
    assert job["payload"]["publishedRevision"] == 1
    result(client.put("/api/v1/workflows/" + flow["id"], headers=administrator, json={
        "version": flow["version"], "name": "尚未发布的新草稿", "domains": flow["domains"],
        "graph": flow["graph"], "description": flow["description"],
    }))
    response = client.post("/api/v1/system-tools/jobs/" + job["id"] + "/run", headers=administrator,
                           json={"version": job["version"], "idempotencyKey": uuid4().hex})
    assert response.status_code == 200, response.text
    ran = response.json()
    assert ran["status"] == "completed", ran
    assert ran["output"]["revision"] == 1
    output = ran["output"]["result"]["outputs"]["end"]
    assert output["outcome"] == "issues" and output["notification"]["sent"]
    assert len(upstream[1]) == 1


def test_wizard_rejects_stale_source_and_missing_rule_field_without_partial_records(
        client, administrator, upstream, db):
    payload, _ = http_setup(client, administrator, upstream)
    stale = deepcopy(payload); stale["source"]["datasourceVersion"] = 999
    assert client.post(API + "/workflows", headers=administrator, json=stale).status_code == 409
    missing = deepcopy(payload); missing["requestKey"] = uuid4().hex; missing["pointIds"] = missing["pointIds"][:1]
    assert client.post(API + "/workflows", headers=administrator, json=missing).status_code == 422
    assert db.scalars(select(Workflow)).all() == []
    assert db.scalars(select(AlertWorkflowCreation)).all() == []
    assert db.scalars(select(ScheduledJob)).all() == []
    assert upstream[1] == []


def test_alert_workflow_contract_is_documented(client, administrator):
    operation = client.app.openapi()["paths"][API + "/workflows"]["post"]
    assert operation["operationId"] == "alerts_workflows_create"
    assert operation.get("summary") and operation.get("description")
    assert "AlertWorkflowInput" in client.app.openapi()["components"]["schemas"]
