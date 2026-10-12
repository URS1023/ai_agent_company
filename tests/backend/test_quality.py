"""Real quality standards, HTTP detectors, permanent judgments and manual reviews."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.modules.quality.models import QualityInspection
from app.modules.system.models import Role
from app.modules.system.permissions import MEMBER_PERMISSIONS
from app.models import DataSource
from test_analytics import source
from test_alerts import equipment
from test_collection import create_source
from test_system_foundation import activate_user, create_role, create_user
from test_workflows import create_flow, execute, graph, node, result
from test_writeback import target, write_node, rows

API = "/api/v1/quality"


def metric(client, headers, **kwargs):
    payload = {"name": "碳含量", "category": "component", "componentName": "C", "unit": "%", "standardMin": "0.10", "standardMax": "0.20", **kwargs}
    return result(client.post(API + "/metrics", headers=headers, json=payload), 201), payload


def template(client, headers, *metrics, **kwargs):
    payload = {"name": "材料质检", "metricIds": [item["id"] for item in metrics], **kwargs}
    return result(client.post(API + "/templates", headers=headers, json=payload), 201), payload


def inspection(client, headers, device, selected, observations=None, **kwargs):
    payload = {"templateId": selected["id"], "templateRevision": selected["version"], "deviceId": device["id"],
               "sampleId": "LOT-1", "requestKey": uuid4().hex, "observations": observations or [], **kwargs}
    return client.post(API + "/inspections", headers=headers, json=payload), payload


@pytest.fixture
def detector():
    state = {"body": {"items": [{"sampleId": "LOT-1", "imageRef": "file:original-inspection-1", "defects": [],
                "components": [{"componentName": "C", "measuredValue": "0.2000000000000000000001", "unit": "%"}]}]}, "status": 200}
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            raw = json.dumps(state["body"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (f"http://127.0.0.1:{server.server_port}", calls), state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def detection_flow(client, headers, detector, *, category="component"):
    remote, state = detector
    source, source_payload = create_source(client, headers, remote, allowAutomatic=True)
    device = equipment(client, headers, datasourceId=source["id"])
    values = {"category": "appearance", "standardMin": None, "standardMax": None} if category == "appearance" else {}
    selected_metric, metric_payload = metric(client, headers, **values)
    selected_template, _ = template(client, headers, selected_metric)
    detector_config = {"metricId": selected_metric["id"], "metricVersion": 1, "deviceId": device["id"], "datasourceId": source["id"], "datasourceVersion": source["version"]}
    evaluation = {"deviceId": device["id"], "templateId": selected_template["id"], "templateRevision": 1, "sampleId": "LOT-1"}
    flow = create_flow(client, headers, graph(node("detect", "qc-" + category, detector_config), node("evaluate", "qc-evaluate", evaluation)))
    return flow, device, selected_metric, selected_template, source, source_payload, metric_payload


def test_quality_catalog_and_typed_api_contract(client, administrator):
    for path in ('/quality', '/quality/metrics', '/quality/templates', '/quality/inspections'):
        assert client.get(path).status_code == 200
    for old, new in (('metric', '/quality/metrics'), ('template', '/quality/templates'), ('inspection', '/quality/inspections'), ('device', '/alerts/devices')):
        response = client.get('/qc/' + old, follow_redirects=False)
        assert response.status_code == 307 and response.headers['location'] == new
    assert client.get('/quality/unknown').status_code == 404 and client.get('/qc/unknown').status_code == 404
    assert "quality.review" not in MEMBER_PERMISSIONS and "quality.writeback" not in MEMBER_PERMISSIONS
    spec = client.app.openapi()
    operations = [op for path, methods in spec["paths"].items() if path.startswith(API) for method, op in methods.items() if method in {"get", "post", "put", "delete"}]
    assert len(operations) == 34
    assert API + "/products" in spec["paths"]
    assert API + "/concessions" in spec["paths"]
    assert all(op.get("summary") and op.get("description") and op["operationId"].startswith("quality_") for op in operations)
    assert "InspectionView" in spec["components"]["schemas"] and "MetricInput" in spec["components"]["schemas"]


@pytest.mark.parametrize("kwargs", [
    {"name": " "}, {"standardMin": "0.3", "standardMax": "0.1"}, {"standardMin": "NaN"},
    {"standardMin": "1e9999"}, {"standardMin": None, "standardMax": None}, {"componentName": " "},
    {"minimumConfidence": "1.1"}, {"standardMin": 0.1}, {"category": "unknown"},
])
def test_invalid_metric_standards_never_persist(client, administrator, kwargs):
    payload = {"name": "C", "category": "component", "componentName": "C", "standardMin": "0.1", "standardMax": "0.2", **kwargs}
    assert client.post(API + "/metrics", headers=administrator, json=payload).status_code == 422
    assert result(client.get(API + "/metrics", headers=administrator)) == []


def test_template_metric_standards_are_immutable_and_missing_never_passes(client, administrator):
    m, original = metric(client, administrator)
    t, template_payload = template(client, administrator, m)
    device = equipment(client, administrator)
    result(client.put(API + "/metrics/" + m["id"], headers=administrator, json={**original, "standardMax": "0.30", "version": 1}))
    old, _ = inspection(client, administrator, device, t, [{"metricId": m["id"], "value": "0.200000000000000000001", "unit": "%"}])
    assert result(old, 201)["outcome"] == "failed"
    t2 = result(client.put(API + "/templates/" + t["id"], headers=administrator, json={**template_payload, "version": 1}))
    new, _ = inspection(client, administrator, device, t2, [{"metricId": m["id"], "value": "0.200000000000000000001", "unit": "%"}])
    assert result(new, 201)["outcome"] == "passed"
    missing, _ = inspection(client, administrator, device, t2)
    assert result(missing, 201)["outcome"] == "incomplete"
    assert result(client.get(API + "/templates/" + t["id"] + "/revisions", headers=administrator))[1]["snapshot"]["metrics"][0]["standardMax"] == "0.20"
    assert client.put(API + "/metrics/" + m["id"], headers=administrator, json={**original, "version": 1}).status_code == 409


def test_duplicate_unknown_metrics_and_device_category_rejected(client, administrator):
    m, _ = metric(client, administrator)
    t, _ = template(client, administrator, m, deviceType="spectrometer")
    d = equipment(client, administrator)
    assert inspection(client, administrator, d, t)[0].status_code == 422
    d = equipment(client, administrator, code="SPEC-2", category="spectrometer")
    observation = {"metricId": m["id"], "value": "0.1", "unit": "%"}
    assert inspection(client, administrator, d, t, [observation, observation])[0].status_code == 422
    assert inspection(client, administrator, d, t, [{**observation, "metricId": "unknown"}])[0].status_code == 422


@pytest.mark.parametrize("defects,outcome", [(None, "incomplete"), ([], "passed"), ([{"defectType": "scratch", "confidence": "0.99", "severity": "severe"}], "failed"), ([{"defectType": "scratch", "confidence": "0.1", "severity": "severe"}], "review")])
def test_explicit_appearance_observation_and_confidence(client, administrator, defects, outcome):
    m, _ = metric(client, administrator, category="appearance", standardMin=None, standardMax=None)
    t, _ = template(client, administrator, m)
    response, _ = inspection(client, administrator, equipment(client, administrator), t,
        [{"metricId": m["id"], "imageRef": "file:original", "defects": defects}])
    assert result(response, 201)["outcome"] == outcome


def test_review_is_append_only_requires_permission_reason_and_version(client, administrator, db):
    m, _ = metric(client, administrator)
    t, _ = template(client, administrator, m)
    response, payload = inspection(client, administrator, equipment(client, administrator), t, [{"metricId": m["id"], "value": "0.3", "unit": "%"}], sampleId="<script>bad()</script>")
    report = result(response, 201)
    again = result(client.post(API + "/inspections", headers=administrator, json=payload), 201)
    assert again["id"] == report["id"] and len(db.scalars(select(QualityInspection)).all()) == 1
    assert client.post(API + "/inspections", headers=administrator, json={**payload, "sampleId": "other"}).status_code == 409
    path = API + "/inspections/" + report["id"]
    action = {"version": 1, "metricId": m["id"], "decision": "passed", "note": "经标准样品复测并记录原始仪器偏差"}
    role = create_role(client, administrator, permissions=["quality.view", "quality.edit", "devices.view"], scope="workspace")
    create_user(client, administrator, [role["id"]])
    member = activate_user(client)
    assert client.post(path + "/reviews", headers=member, json=action).status_code == 403
    assert client.post(path + "/reviews", headers=administrator, json={**action, "note": " "}).status_code == 422
    reviewed = result(client.post(path + "/reviews", headers=administrator, json=action))
    assert reviewed["outcome"] == "failed" and reviewed["effectiveOutcome"] == "passed"
    assert reviewed["results"] == report["results"] and reviewed["reviews"][0]["note"] == action["note"]
    assert client.post(path + "/reviews", headers=administrator, json=action).status_code == 409
    exported = client.get(path + "/export", headers=administrator)
    assert "&lt;script&gt;" in exported.text and "<script>" not in exported.text and action["note"] in exported.text
    assert client.delete(path, headers=administrator, params={"version": 2}).status_code == 200
    assert result(client.get(API + "/inspections", headers=administrator)) == []
    assert result(client.get(path, headers=administrator))["status"] == "archived"


@pytest.mark.parametrize("category,outcome", [("component", "failed"), ("appearance", "passed")])
def test_actual_http_detector_to_inspection_and_no_replay(client, administrator, detector, category, outcome):
    flow, *_ = detection_flow(client, administrator, detector, category=category)
    first = result(execute(client, administrator, flow, idempotencyKey="qc-http-once"))
    output = first["result"]["outputs"]["end"]
    assert output["outcome"] == outcome
    report = result(client.get(API + "/inspections/" + output["qualityInspectionId"], headers=administrator))
    assert report["results"][0]["provenance"]["mode"] == "detector"
    assert report["snapshot"]["detectionIds"] and report["executionId"] == first["id"]
    assert result(execute(client, administrator, flow, idempotencyKey="qc-http-once")) == first
    assert len(detector[0][1]) == 1
    assert client.get(API + "/inspections/" + report["id"] + "/export", headers=administrator).status_code == 200
    assert len(detector[0][1]) == 1


@pytest.mark.parametrize("bad", ["missing", "duplicated", "error", "wrong-sample", "no-defects"])
def test_detector_failure_never_becomes_empty_success(client, administrator, detector, bad):
    category = "appearance" if bad == "no-defects" else "component"
    flow, *_ = detection_flow(client, administrator, detector, category=category)
    row = detector[1]["body"]["items"][0]
    if bad == "missing": row["components"] = []
    if bad == "duplicated": row["components"] *= 2
    if bad == "error": detector[1]["status"] = 503
    if bad == "wrong-sample": row["sampleId"] = "OTHER"
    if bad == "no-defects": del row["defects"]
    assert execute(client, administrator, flow).status_code == 502
    assert result(client.get(API + "/inspections", headers=administrator)) == []
    assert len(detector[0][1]) == 1


def test_metric_and_source_versions_block_before_http(client, administrator, detector):
    flow, _, m, _, source, source_payload, original = detection_flow(client, administrator, detector)
    result(client.put(API + "/metrics/" + m["id"], headers=administrator, json={**original, "version": 1, "standardMax": "0.3"}))
    assert execute(client, administrator, flow).status_code == 422
    assert detector[0][1] == []


def test_unit_mismatch_and_source_permission_revocation(client, administrator, detector, db):
    flow, _, m, _, source, _, _ = detection_flow(client, administrator, detector)
    detector[1]["body"]["items"][0]["components"][0]["unit"] = "mg/L"
    first = result(execute(client, administrator, flow))
    report_id = first["result"]["outputs"]["end"]["qualityInspectionId"]
    assert result(client.get(API + "/inspections/" + report_id, headers=administrator))["outcome"] == "review"
    role = create_role(client, administrator, permissions=["quality.view", "workflows.view", "devices.view", "datasources.view"], scope="workspace")
    create_user(client, administrator, [role["id"]])
    member = activate_user(client)
    assert client.get(API + "/inspections/" + report_id, headers=member).status_code == 200
    saved = db.get(Role, role["id"])
    saved.permissions = [permission for permission in saved.permissions if permission != "datasources.view"]
    db.commit()
    assert client.get(API + "/inspections/" + report_id, headers=member).status_code == 403
    assert result(client.get(API + "/inspections", headers=member)) == []


def test_detector_source_configuration_change_blocks_before_http(client, administrator, detector, db):
    flow, _, _, _, selected, *_ = detection_flow(client, administrator, detector)
    source = db.get(DataSource, selected['id'])
    source.connection_config = {**source.connection_config, 'version': source.connection_config['version'] + 1}
    db.commit()
    assert execute(client, administrator, flow).status_code == 422
    assert detector[0][1] == []


def test_missing_quality_execution_permission_blocks_before_any_http(client, administrator, detector):
    flow, *_ = detection_flow(client, administrator, detector)
    role = create_role(client, administrator, permissions=['quality.view', 'workflows.view', 'workflows.edit', 'workflows.execute', 'devices.view', 'datasources.view'], scope='workspace')
    create_user(client, administrator, [role['id']])
    member = activate_user(client)
    assert execute(client, member, flow).status_code == 403
    assert detector[0][1] == []


def test_quality_report_writeback_preserves_report_and_requires_separate_grant(client, administrator, source, db):
    m, _ = metric(client, administrator)
    t, _ = template(client, administrator, m)
    device = equipment(client, administrator)
    selected, _ = target(client, administrator, source)
    evaluate = node('evaluate', 'qc-evaluate', {'deviceId': device['id'], 'templateId': t['id'], 'templateRevision': 1, 'sampleId': 'LOT-QC-WRITE'})
    write = write_node(selected, fields=[{'column': 'id', 'origin': 'literal', 'value': 50},
        {'column': 'amount', 'origin': 'literal', 'value': 1}, {'column': 'note', 'origin': 'input', 'path': '$.qualityInspectionId'}])
    flow = create_flow(client, administrator, graph(evaluate, write))
    payload = {'data': [{'metricId': m['id'], 'value': '0.3', 'unit': '%'}]}
    role = create_role(client, administrator, permissions=['quality.view', 'quality.execute', 'workflows.view', 'workflows.edit', 'workflows.execute', 'devices.view', 'datasources.view', 'datasources.writeback'], scope='workspace')
    create_user(client, administrator, [role['id']])
    member = activate_user(client)
    assert execute(client, member, flow, payload, confirmWrites=True).status_code == 403
    assert db.scalars(select(QualityInspection)).all() == []
    run = result(execute(client, administrator, flow, payload, confirmWrites=True))
    report_id = run['result']['outputs']['end']['qualityInspectionId']
    assert rows(source, administrator)[-1] == (50, 1, report_id)
    report = result(client.get(API + '/inspections/' + report_id, headers=administrator))
    assert report['outcome'] == 'failed' and report['writeOperations'][0]['status'] == 'succeeded'
    assert report_id in client.get(API + '/inspections/' + report_id + '/export', headers=administrator).text
