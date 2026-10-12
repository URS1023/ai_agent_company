"""Ordinary quality pages assemble real published workflows without raw graph editing."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from app.modules.quality.models import QualityWorkflowCreation
from app.modules.workflows.models import Workflow
from test_alerts import equipment
from test_collection import create_source
from test_quality import API, metric, template
from test_quality_images import dynamic_detector, upload
from test_workflows import result


def detector_source(client, headers, remote, category):
    body = {"sample": "${input.sampleId}"}
    if category == "appearance":
        body["image"] = "${input.imageBase64}"
    return create_source(client, headers, remote, method="POST", bodies=[body], allowAutomatic=True)[0]


def mixed_setup(client, headers, remote):
    component_source = detector_source(client, headers, remote, "component")
    appearance_source = detector_source(client, headers, remote, "appearance")
    device = equipment(client, headers, code="QC-MIXED-" + uuid4().hex[:8], datasourceId=component_source["id"],
                       qualityDatasourceIds=[appearance_source["id"]])
    component, _ = metric(client, headers)
    appearance, _ = metric(client, headers, name="外观划痕", category="appearance",
                           standardMin=None, standardMax=None)
    standard, _ = template(client, headers, component, appearance, name="混合材料检验")
    payload = {
        "requestKey": uuid4().hex,
        "name": "混合材料自动质检",
        "description": "普通页面创建；配置阶段不得调用检测服务。",
        "groups": [{
            "title": "一号检测工位",
            "deviceId": device["id"],
            "templateId": standard["id"],
            "templateRevision": standard["version"],
            "detectors": [
                {"metricId": component["id"], "metricVersion": component["version"],
                 "datasourceId": component_source["id"], "datasourceVersion": component_source["version"]},
                {"metricId": appearance["id"], "metricVersion": appearance["version"],
                 "datasourceId": appearance_source["id"], "datasourceVersion": appearance_source["version"]},
            ],
        }],
    }
    return payload, device, component_source, appearance_source


def test_mixed_detector_wizard_publishes_atomically_then_runs_real_services(
        client, administrator, dynamic_detector, db):
    remote, _ = dynamic_detector
    payload, device, component_source, appearance_source = mixed_setup(client, administrator, remote)
    created = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    assert not created["reused"] and created["workflow"]["status"] == "published"
    assert created["workflow"]["version"] == created["workflow"]["publishedRevision"] == 1
    assert len(created["runPlan"]["groups"]) == 1
    group = created["runPlan"]["groups"][0]
    assert group["title"] == "一号检测工位" and group["imageRequired"]
    assert {item["datasourceId"] for item in group["detectors"]} == {component_source["id"], appearance_source["id"]}
    assert [node["kind"] for node in created["workflow"]["graph"]["nodes"]] == [
        "start", "qc-component", "qc-appearance", "merge", "qc-evaluate", "end",
    ]
    assert not remote[1]
    loaded_device = result(client.get("/api/v1/alerts/devices/" + device["id"], headers=administrator))
    assert loaded_device["qualityDatasourceIds"] == [appearance_source["id"]]

    image = result(upload(client, administrator), 201)
    run_payload = {"version": 1, "publishedRevision": 1, "idempotencyKey": uuid4().hex,
                   "confirmWrites": False, "timeoutSeconds": 120,
                   "groups": [{"nodeId": group["nodeId"], "samples": [{"sampleId": "LOT-MIXED-1", "imageFileId": image["id"]}]}]}
    batch = result(client.post(API + "/workflows/" + created["workflow"]["id"] + "/runs",
                               headers=administrator, json=run_payload))
    assert batch["execution"]["status"] == "completed" and batch["samples"][0]["outcome"] == "failed"
    assert len(remote[1]) == 2

    repeated = result(client.post(API + "/workflows", headers=administrator, json=payload), 201)
    assert repeated["reused"] and repeated["workflow"]["id"] == created["workflow"]["id"]
    assert len(db.scalars(select(Workflow)).all()) == len(db.scalars(select(QualityWorkflowCreation)).all()) == 1
    changed = deepcopy(payload); changed["name"] = "另一份配置"
    assert client.post(API + "/workflows", headers=administrator, json=changed).status_code == 409


def test_wizard_preflight_rejects_unlinked_or_numeric_services_without_partial_draft(
        client, administrator, dynamic_detector, db):
    remote, _ = dynamic_detector
    payload, device, _, appearance_source = mixed_setup(client, administrator, remote)
    current = result(client.get("/api/v1/alerts/devices/" + device["id"], headers=administrator))
    current["qualityDatasourceIds"] = []
    editable = {key: value for key, value in current.items() if key not in {"id", "createTime", "updateTime"}}
    result(client.put("/api/v1/alerts/devices/" + device["id"], headers=administrator, json=editable))
    assert client.post(API + "/workflows", headers=administrator, json=payload).status_code == 422
    assert not db.scalars(select(Workflow)).all() and not db.scalars(select(QualityWorkflowCreation)).all()
    assert not remote[1]

    numeric, _ = metric(client, administrator, name="人工尺寸", category="numeric", componentName="")
    standard, _ = template(client, administrator, numeric, name="人工尺寸标准")
    numeric_payload = deepcopy(payload)
    numeric_payload["requestKey"] = uuid4().hex
    numeric_payload["groups"][0].update(templateId=standard["id"], templateRevision=1,
        detectors=[{"metricId": numeric["id"], "metricVersion": 1,
                    "datasourceId": appearance_source["id"], "datasourceVersion": 1}])
    assert client.post(API + "/workflows", headers=administrator, json=numeric_payload).status_code == 422
    assert not db.scalars(select(Workflow)).all()


def test_quality_workflow_creation_contract_is_documented(client, administrator):
    operation = client.app.openapi()["paths"][API + "/workflows"]["post"]
    assert operation["operationId"] == "quality_workflows_create"
    assert operation.get("summary") and operation.get("description")
    assert "QualityWorkflowInput" in client.app.openapi()["components"]["schemas"]
