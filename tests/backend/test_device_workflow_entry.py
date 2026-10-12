"""A device opens one stable editable workflow per business domain.

中文说明：每台设备在每个业务域只打开一份稳定的可编辑流程：首次打开创建草稿、重复打开复用；
预警与质检使用各自稳定的流程；未在该设备启用的域被拒绝；入口搜索支持编码/位置/分类；
历史草稿按固定设备被修复一次；不允许使用其它设备的策略。
隔离方式：client 夹具（内存 SQLite + TestClient），不连业务库与外部模型。
"""
from copy import deepcopy

from app.db import new_session
from app.modules.workflows.models import DeviceWorkflowBinding, Workflow


def create_device(client, headers, *, domains=None, name="空压机 A", code="DEVICE-A"):
    response = client.post("/api/v1/alerts/devices", headers=headers, json={
        "code": code,
        "name": name,
        "category": "动力设备",
        "model": "",
        "manufacturer": "",
        "location": "一号车间",
        "protocol": "HTTP",
        "datasourceId": None,
        "qualityDatasourceIds": [],
        "domains": domains or ["alert", "quality"],
        "description": "",
        "status": "active",
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


def open_entry(client, headers, device_id, domain):
    return client.post("/api/v1/workflows/device-entry", headers=headers, json={
        "deviceId": device_id,
        "domain": domain,
    })


def test_first_open_creates_draft_and_repeat_reuses_it(client, administrator):
    device = create_device(client, administrator)

    first = open_entry(client, administrator, device["id"], "alert")
    assert first.status_code == 200, first.text
    created = first.json()["data"]
    assert created["created"] is True
    assert created["provider"] == "platform"
    assert created["providerAppId"] is None
    assert created["editorPath"] == f'/alerts/devices/{device["id"]}/workflow'
    assert created["workflow"]["status"] == "draft"
    assert created["workflow"]["publishedRevision"] is None
    assert created["workflow"]["domains"] == ["alert"]
    assert [node["kind"] for node in created["workflow"]["graph"]["nodes"]] == [
        "start", "alert-evaluate", "end",
    ]
    alert_node = created["workflow"]["graph"]["nodes"][1]
    assert alert_node["config"]["deviceId"] == device["id"]

    repeated = open_entry(client, administrator, device["id"], "alert")
    assert repeated.status_code == 200, repeated.text
    reused = repeated.json()["data"]
    assert reused["created"] is False
    assert reused["bindingId"] == created["bindingId"]
    assert reused["workflow"]["id"] == created["workflow"]["id"]

    with new_session() as session:
        assert session.query(DeviceWorkflowBinding).count() == 1
        assert session.query(Workflow).filter(Workflow.name.contains("空压机 A")).count() == 1


def test_alert_and_quality_have_separate_stable_workflows(client, administrator):
    device = create_device(client, administrator)
    alert = open_entry(client, administrator, device["id"], "alert").json()["data"]
    quality = open_entry(client, administrator, device["id"], "quality").json()["data"]

    assert alert["bindingId"] != quality["bindingId"]
    assert alert["workflow"]["id"] != quality["workflow"]["id"]
    assert quality["workflow"]["domains"] == ["quality"]
    assert [node["kind"] for node in quality["workflow"]["graph"]["nodes"]] == [
        "start", "qc-evaluate", "end",
    ]
    quality_node = quality["workflow"]["graph"]["nodes"][1]
    assert quality_node["config"]["deviceId"] == device["id"]


def test_entry_rejects_a_domain_not_enabled_on_device(client, administrator):
    device = create_device(client, administrator, domains=["alert"])

    response = open_entry(client, administrator, device["id"], "quality")

    assert response.status_code == 422
    assert "尚未启用质量检查用途" in response.json()["message"]
    with new_session() as session:
        assert session.query(DeviceWorkflowBinding).count() == 0
        assert session.query(Workflow).filter(Workflow.name.contains("空压机 A")).count() == 0


def test_device_entry_search_accepts_code_location_and_category(client, administrator):
    create_device(client, administrator)

    for keyword in ("DEVICE-A", "一号车间", "动力设备"):
        response = client.get("/api/v1/alerts/devices", headers=administrator, params={"keyword": keyword})
        assert response.status_code == 200, response.text
        assert [item["code"] for item in response.json()["data"]] == ["DEVICE-A"]


def test_existing_device_draft_is_repaired_once_with_fixed_device(client, administrator):
    device = create_device(client, administrator)
    created = open_entry(client, administrator, device["id"], "alert").json()["data"]

    with new_session() as session:
        workflow = session.get(Workflow, created["workflow"]["id"])
        graph = deepcopy(workflow.graph)
        graph["nodes"][1]["config"].pop("deviceId")
        workflow.graph = graph
        session.commit()
        legacy_version = workflow.version

    repaired = open_entry(client, administrator, device["id"], "alert").json()["data"]
    assert repaired["workflow"]["version"] == legacy_version + 1
    assert repaired["workflow"]["graph"]["nodes"][1]["config"]["deviceId"] == device["id"]

    repeated = open_entry(client, administrator, device["id"], "alert").json()["data"]
    assert repeated["workflow"]["version"] == repaired["workflow"]["version"]


def test_device_workflow_rejects_policy_from_another_device(client, administrator):
    selected = create_device(client, administrator)
    other = create_device(client, administrator, name="空压机 B", code="DEVICE-B")
    policy_response = client.post("/api/v1/alerts/rules", headers=administrator, json={
        "name": "B 设备温度预警",
        "deviceId": other["id"],
        "rules": [{
            "id": "temperature-high",
            "name": "温度过高",
            "severity": "warning",
            "message": "温度超过上限",
            "expression": {"op": "gte", "args": [{"field": "temperature"}, {"value": 80}]},
        }],
    })
    assert policy_response.status_code == 201, policy_response.text
    policy = policy_response.json()["data"]
    entry = open_entry(client, administrator, selected["id"], "alert").json()["data"]
    workflow = entry["workflow"]
    graph = deepcopy(workflow["graph"])
    graph["nodes"][1]["config"] = {
        "deviceId": selected["id"],
        "ruleId": policy["id"],
        "ruleRevision": policy["version"],
    }

    response = client.put(
        f'/api/v1/workflows/{workflow["id"]}',
        headers=administrator,
        json={
            "name": workflow["name"],
            "description": workflow["description"],
            "domains": workflow["domains"],
            "graph": graph,
            "version": workflow["version"],
        },
    )
    assert response.status_code == 422
    assert "预警策略与设备工作流绑定的设备不一致" in response.json()["message"]
