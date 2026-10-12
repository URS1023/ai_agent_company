"""Real equipment-policy-workflow evidence, authorization, versions and reviews.

中文说明：设备预警主用例：真实规则/建议版本、授权与数据范围、问题处理的追加式记录与报告原始性。
验证行为：设备配置非法被拒；HTTP 业务节点真实落库且幂等；规则版本不重写已固定策略；
处理历史只追加、报告保持原始；空/正常/非法定位可区分；下游失败不把报告标成完成。
隔离方式：client 夹具（内存 SQLite + TestClient）+ 真实 SQL/HTTP 执行路径，不连业务库、不调用外部模型。
"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import DataSource
from app.modules.alerts.models import AlertProblem, AlertReport
from app.modules.system.models import Role
from test_analytics import source
from test_collection import create_source, upstream
from test_system_foundation import activate_user, create_role, create_user
from test_workflows import create_flow, execute, graph, node, result

API = "/api/v1/alerts"


def test_business_pages_and_legacy_report_routes(client):
    for page in ("", "/devices", "/points", "/advice", "/rules", "/reports", "/problems"):
        assert client.get("/alerts" + page).status_code == 200
    for old, target in (("report", "reports"), ("problem", "problems"), ("rule", "rules")):
        response = client.get("/alert/" + old, follow_redirects=False)
        assert response.status_code == 307 and response.headers["location"] == "/alerts/" + target
    assert client.get("/alerts/missing-page").status_code == 404


def equipment(client, headers, **values):
    payload = {"code": "COMP-01", "name": "压缩机", **values}
    return result(client.post(API + "/devices", headers=headers, json=payload), 201)


def policy(client, headers, device, **values):
    payload = {"name": "温度预警", "deviceId": device["id"], "recordKeyField": "device",
               "rules": [{"id": "high", "name": "读数超限", "message": "检查冷却系统", "severity": "critical",
                          "expression": {"op": "gte", "args": [{"field": "amount"}, {"value": "100"}]}}], **values}
    return result(client.post(API + "/rules", headers=headers, json=payload), 201), payload


def business_node(rule):
    return node("alert", "alert-evaluate", {"ruleId": rule["id"], "ruleRevision": rule["version"]})


def test_equipment_point_and_expert_lifecycle_no_source_requests(client, administrator, upstream):
    source, _ = create_source(client, administrator, upstream)
    device = equipment(client, administrator, datasourceId=source["id"])
    assert client.post(API + "/devices", headers=administrator, json={"code": "COMP-01", "name": "重复"}).status_code == 409
    point = result(client.post(API + "/points", headers=administrator, json={"deviceId": device["id"], "code": "T1", "name": "温度", "fieldName": "amount", "unit": "℃", "frequencyMinutes": 5}), 201)
    assert point["frequencyMinutes"] == 5
    assert len(result(client.get(API + "/points", headers=administrator, params={"deviceId": device["id"]}))) == 1
    other = equipment(client, administrator, code="OTHER")
    assert client.put(API + "/points/" + point["id"], headers=administrator, json={"deviceId": other["id"], "code": "T1", "name": "温度", "fieldName": "amount", "version": point["version"]}).status_code == 422
    advice = result(client.post(API + "/advice", headers=administrator, json={"name": "高负载", "operatingCondition": "持续运行", "responsePlan": "检查循环水", "planShort": "检查水路"}), 201)
    assert advice["responsePlan"] == "检查循环水"
    updated = result(client.put(API + "/devices/" + device["id"], headers=administrator, json={"code": device["code"], "name": "新版设备名", "version": device["version"]}))
    assert updated["version"] == 2
    assert client.put(API + "/devices/" + device["id"], headers=administrator, json={"code": device["code"], "name": "旧名", "version": 1}).status_code == 409
    assert upstream[1] == []
    assert client.delete(API + "/devices/" + device["id"], headers=administrator, params={"version": 2}).status_code == 200
    assert client.get(API + "/devices/" + device["id"], headers=administrator).status_code == 404
    assert result(client.get(API + "/points", headers=administrator)) == []


@pytest.mark.parametrize("payload", [{"code": " ", "name": "x"}, {"code": "x", "name": " "}, {"code": "x", "name": "设备", "status": "online"}, {"code": "x", "name": "设备", "password": "plaintext"}])
def test_invalid_device_configuration_is_rejected(client, administrator, payload):
    assert client.post(API + "/devices", headers=administrator, json=payload).status_code == 422


def test_true_http_business_node_persists_report_problem_and_idempotent_trace(client, administrator, upstream, db):
    source, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    device = equipment(client, administrator, datasourceId=source["id"])
    rule, _ = policy(client, administrator, device)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": source["id"]}), business_node(rule)))
    first = result(execute(client, administrator, flow, idempotencyKey="alert-once-01"))
    output = first["result"]["outputs"]["end"]
    assert output["outcome"] == "issues" and output["alertRuleRevision"] == 1
    report_id = output["alertReportId"]
    report = result(client.get(API + "/reports/" + report_id, headers=administrator))
    assert report["executionStatus"] == "completed" and report["snapshot"]["device"]["code"] == "COMP-01"
    assert report["result"]["data"][0]["amount"] == "123.450"
    problems = result(client.get(API + "/problems", headers=administrator, params={"reportId": report_id}))
    assert len(problems) == 1 and problems[0]["recordKey"] == "A" and problems[0]["status"] == "open"
    second = result(execute(client, administrator, flow, idempotencyKey="alert-once-01"))
    assert first == second and len(upstream[1]) == 1
    assert len(db.scalars(select(AlertReport)).all()) == len(db.scalars(select(AlertProblem)).all()) == 1
    exported = client.get(API + "/reports/" + report_id + "/export", headers=administrator)
    assert exported.status_code == 200 and "检查冷却系统" in exported.text and "sandbox" in exported.headers["content-security-policy"]
    assert len(upstream[1]) == 1


def test_rule_and_advice_versions_do_not_rewrite_pinned_policy(client, administrator):
    device = equipment(client, administrator)
    advice = result(client.post(API + "/advice", headers=administrator, json={"name": "专家预案", "responsePlan": "原始预案"}), 201)
    rule, payload = policy(client, administrator, device, adviceId=advice["id"])
    flow = create_flow(client, administrator, graph(business_node(rule)))
    result(client.put(API + "/advice/" + advice["id"], headers=administrator, json={"name": "专家预案", "responsePlan": "新版预案", "version": 1}))
    newer = deepcopy(payload)
    newer["rules"][0]["expression"]["args"][1]["value"] = "1000"
    result(client.put(API + "/rules/" + rule["id"], headers=administrator, json={**newer, "version": 1}))
    execution = result(execute(client, administrator, flow, {"data": [{"device": "A", "amount": 123}]}))
    report = result(client.get(API + "/reports/" + execution["result"]["outputs"]["end"]["alertReportId"], headers=administrator))
    assert report["outcome"] == "issues" and report["ruleRevision"] == 1
    assert report["snapshot"]["rule"]["advice"]["responsePlan"] == "原始预案"
    revisions = result(client.get(API + "/rules/" + rule["id"] + "/revisions", headers=administrator))
    assert [item["revision"] for item in revisions] == [2, 1]
    assert revisions[0]["snapshot"]["advice"]["responsePlan"] == "新版预案"
    assert client.put(API + "/rules/" + rule["id"], headers=administrator, json={**payload, "version": 1}).status_code == 409


def test_problem_handling_is_append_only_and_report_remains_original(client, administrator):
    rule, _ = policy(client, administrator, equipment(client, administrator))
    flow = create_flow(client, administrator, graph(business_node(rule)))
    execution = result(execute(client, administrator, flow, {"data": [{"device": "<script>alert(1)</script>", "amount": 200}]}))
    report_id = execution["result"]["outputs"]["end"]["alertReportId"]
    problem = result(client.get(API + "/problems", headers=administrator))[0]
    action = {"version": 1, "status": "resolved", "note": "已检查冷却系统"}
    resolved = result(client.post(API + "/problems/" + problem["id"] + "/actions", headers=administrator, json=action))
    assert resolved["version"] == 2 and resolved["message"] == problem["message"]
    assert client.post(API + "/problems/" + problem["id"] + "/actions", headers=administrator, json=action).status_code == 409
    assert client.post(API + "/problems/" + problem["id"] + "/actions", headers=administrator, json={"version": 2, "status": "ignored", "note": " "}).status_code == 422
    result(client.post(API + "/problems/" + problem["id"] + "/actions", headers=administrator, json={"version": 2, "status": "open", "note": "复检仍异常"}))
    events = result(client.get(API + "/problems/" + problem["id"] + "/actions", headers=administrator))
    assert [event["status"] for event in events] == ["resolved", "open"]
    report = result(client.get(API + "/reports/" + report_id, headers=administrator))
    assert report["summary"]["issues"] == 1 and report["outcome"] == "issues"
    html = client.get(API + "/reports/" + report_id + "/export", headers=administrator).text
    assert "<script>" not in html and "&lt;script&gt;" in html


@pytest.mark.parametrize("rows,outcome,code", [([], "no_data", 200), ([{"device": "A", "amount": 1}], "normal", 200), ([{"amount": 200}], None, 502)])
def test_empty_normal_and_invalid_locator_are_distinct(client, administrator, db, rows, outcome, code):
    rule, _ = policy(client, administrator, equipment(client, administrator))
    flow = create_flow(client, administrator, graph(business_node(rule)))
    execution = result(execute(client, administrator, flow, {"data": rows}), code)
    reports = db.scalars(select(AlertReport)).all()
    if outcome:
        assert reports[0].outcome == outcome
        if outcome == "no_data":
            assert execution["result"]["outputs"]["end"]["passed"] is None
    else:
        assert reports == [] and db.scalars(select(AlertProblem)).all() == []


def test_downstream_failure_does_not_mislabel_report_as_complete(client, administrator):
    rule, _ = policy(client, administrator, equipment(client, administrator))
    flow = create_flow(client, administrator, graph(business_node(rule), node("bad", "arithmetic", {"fields": [{"name": "bad", "formula": "amount / 0"}]})))
    execution = result(execute(client, administrator, flow, {"data": [{"device": "A", "amount": 200}]}), 502)
    reports = result(client.get(API + "/reports", headers=administrator))
    assert len(reports) == 1 and reports[0]["outcome"] == "issues" and reports[0]["executionStatus"] == "failed"
    assert reports[0]["executionId"] == execution["id"]


def test_business_permission_denied_before_http_and_source_revocation_hides_history(client, administrator, upstream, db):
    source, _ = create_source(client, administrator, upstream, allowAutomatic=True)
    device = equipment(client, administrator, datasourceId=source["id"])
    rule, _ = policy(client, administrator, device)
    flow = create_flow(client, administrator, graph(node("http", "http-request", {"datasourceId": source["id"]}), business_node(rule)))
    role = create_role(client, administrator, permissions=["home.view", "devices.view", "alerts.view", "datasources.view", "workflows.view", "workflows.execute", "workflows.edit"], scope="workspace")
    create_user(client, administrator, [role["id"]])
    reader = activate_user(client)
    assert execute(client, reader, flow).status_code == 403 and upstream[1] == []
    saved = db.get(Role, role["id"])
    saved.permissions = [*saved.permissions, "alerts.execute"]
    db.commit()
    execution = result(execute(client, reader, flow))
    report_id = execution["result"]["outputs"]["end"]["alertReportId"]
    assert client.get(API + "/reports/" + report_id, headers=reader).status_code == 200
    saved.permissions = [item for item in saved.permissions if item != "datasources.view"]
    db.commit()
    assert client.get(API + "/reports/" + report_id, headers=reader).status_code == 403
    assert result(client.get(API + "/reports", headers=reader)) == []
    assert len(upstream[1]) == 1


def test_actual_database_alert_and_disabled_rule_stops_before_query(client, administrator, source):
    device = equipment(client, administrator, datasourceId=source["id"])
    rule, payload = policy(client, administrator, device, recordKeyField="department")
    flow = create_flow(client, administrator, graph(node("sql", "database-query", {"datasourceId": source["id"], "query": "SELECT department, amount FROM sales WHERE amount IS NOT NULL"}), business_node(rule)))
    execution = result(execute(client, administrator, flow))
    assert execution["result"]["outputs"]["end"]["summary"]["issues"] == 4
    result(client.put(API + "/rules/" + rule["id"], headers=administrator, json={**payload, "status": "inactive", "version": 1}))
    assert execute(client, administrator, flow).status_code == 422
    assert len(result(client.get(API + "/reports", headers=administrator))) == 1


def test_self_scoped_device_permission_does_not_expand_through_alert_role(client, administrator):
    device = equipment(client, administrator)
    broad = create_role(client, administrator, "alerts_all", permissions=["alerts.view", "alerts.create"], scope="workspace")
    own = create_role(client, administrator, "own_devices", permissions=["devices.view", "devices.create"], scope="self")
    create_user(client, administrator, [broad["id"], own["id"]])
    reader = activate_user(client)
    assert result(client.get(API + "/devices", headers=reader)) == []
    assert client.get(API + "/devices/" + device["id"], headers=reader).status_code == 404
    assert client.post(API + "/rules", headers=reader, json={"name": "不应读取", "deviceId": device["id"], "rules": [{"id": "x", "name": "x", "expression": {"value": True}}]}).status_code == 404


def test_alert_endpoints_registered_documented_and_additive_schema(client, administrator):
    assert "modules.alerts.router" in client.get("/api/ready").json()["modules"]
    operations = [operation for path, methods in client.app.openapi()["paths"].items() if path.startswith(API) for operation in methods.values()]
    assert len(operations) == 32
    assert {"equipment_device_catalogue", "business_card_image_upload", "business_card_image_remove",
            "business_card_image_content"} <= {operation["operationId"] for operation in operations}
    assert all(operation.get("summary") and operation.get("description") for operation in operations)
    assert client.get(API + "/devices").status_code == 401
