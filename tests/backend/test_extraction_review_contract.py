"""复核必须使用运行固定定义，并在整批校验完成前保持所有候选不变。"""
from copy import deepcopy

import pytest

from app.modules.knowledge_graph.models import ExtractionCandidate
from test_extraction_materialization_contract import execute, job_payload
from test_graph_property_contracts import EXTRACTIONS, definition, model_graph
from test_knowledge_graph import result


@pytest.mark.parametrize("kind", ["entity", "relation"])
@pytest.mark.parametrize("attribute,valid,invalid", [
    (definition("integer"), 1, {"value": "1"}),
    (definition("integer"), 1, {"value": True}),
    (definition("date"), "2024-02-29", {"value": "2025-02-29"}),
    (definition("datetime"), "2025-01-01T12:00Z", {"value": "2025-01-01"}),
    (definition("boolean"), True, {"value": 1}),
    (definition("string", enumValues=["合法"]), "合法", {"value": "非法"}),
    (definition("string", required=True), "合法", {}),
    (definition(), "合法", {"value": "合法", "unknown": "多余"}),
])
def test_invalid_accepted_properties_reject_entire_batch(client, administrator, kind, attribute, valid, invalid):
    model = model_graph(client, administrator, **{kind + "_attributes": [attribute]})
    job = result(client.post(EXTRACTIONS, headers=administrator, json=job_payload(model, kind=kind, value=valid)), 201)
    run_id, candidates = execute(client, administrator, job)
    bad = next(row for row in candidates if row["kind"] == kind and row["payload"]["properties"])
    good = next(row for row in candidates if row["id"] != bad["id"])
    changed = {**deepcopy(bad["payload"]), "properties": invalid}
    response = client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=administrator, json={"items": [
        {"candidateId": good["id"], "version": good["version"], "decision": "accepted"},
        {"candidateId": bad["id"], "version": bad["version"], "decision": "accepted", "payload": changed}]})
    assert response.status_code == 422, response.text
    after = result(client.get(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/candidates", headers=administrator))
    assert after == candidates


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_unchanged_invalid_original_is_rejected_but_can_be_declined(client, administrator, db, kind):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition("integer")]})
    job = result(client.post(EXTRACTIONS, headers=administrator, json=job_payload(model, kind=kind, value=1)), 201)
    run_id, candidates = execute(client, administrator, job)
    selected = next(row for row in candidates if row["kind"] == kind and row["payload"]["properties"])
    row = db.get(ExtractionCandidate, selected["id"])
    row.payload = {**row.payload, "properties": {"value": "旧非法值"}}
    db.commit()
    item = {"candidateId": row.id, "version": row.version, "decision": "accepted"}
    response = client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=administrator, json={"items": [item]})
    assert response.status_code == 422, response.text
    item["decision"] = "rejected"
    declined = result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=administrator,
        json={"items": [item]}))
    assert declined["items"][0]["payload"]["properties"] == {"value": "旧非法值"}


def test_review_uses_original_run_after_job_ontology_changes(client, administrator):
    original = model_graph(client, administrator, entity_attributes=[definition()])
    replacement = model_graph(client, administrator, entity_attributes=[definition("integer")])
    job = result(client.post(EXTRACTIONS, headers=administrator, json=job_payload(original)), 201)
    run_id, candidates = execute(client, administrator, job)
    current = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    payload = {**job_payload(replacement, value=1), "version": current["version"]}
    result(client.put(f"{EXTRACTIONS}/{job['id']}", headers=administrator, json=payload))
    result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=administrator,
        json={"items": [{"candidateId": row["id"], "version": row["version"], "decision": "accepted"}
            for row in candidates]}))
