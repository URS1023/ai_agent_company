"""经隔离 HTTP 验证候选物化的内容、固定运行与事务契约。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.ids import new_id
from app.models import Workspace, WorkspaceMember
from app.modules.knowledge_graph.models import ExtractionCandidate, ExtractionRun, GraphEntity, GraphRelation, KnowledgeGraph
from test_graph_property_contracts import EXTRACTIONS, GRAPHS, ONTOLOGY, definition, entity, model_graph
from test_knowledge_graph import result


def job_payload(model, *, value="新值", kind="entity", records=None):
    """实体与关系采用同一结构化输入，只为待验证的一侧映射属性。"""
    return {
        "name": "候选内容契约", "mode": "structured", "ontologyId": model.ontology["id"],
        "records": records or [{"key": "S", "target": "T", "value": value}],
        "mappings": {"entityMappings": [
            {"conceptId": model.source["id"], "keyField": "key", "nameField": "key",
             "propertyFields": {"value": "value"} if kind == "entity" else {}},
            {"conceptId": model.target["id"], "keyField": "target", "nameField": "target"}],
            "relationMappings": [{"relationTypeId": model.relation["id"],
                "sourceConceptId": model.source["id"], "targetConceptId": model.target["id"],
                "sourceKeyField": "key", "targetKeyField": "target",
                "propertyFields": {"value": "value"} if kind == "relation" else {}}]},
    }


def execute(client, headers, job):
    execution = result(client.post(f"{EXTRACTIONS}/{job['id']}/execute", headers=headers,
        json={"version": job["version"]}))
    run_id = execution["run"]["id"]
    candidates = result(client.get(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/candidates", headers=headers))
    return run_id, candidates


def accepted_run(client, headers, model, *, value="新值", kind="entity", records=None):
    payload = job_payload(model, value=value, kind=kind, records=records)
    job = result(client.post(EXTRACTIONS, headers=headers, json=payload), 201)
    run_id, candidates = execute(client, headers, job)
    result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=headers,
        json={"items": [{"candidateId": row["id"], "version": row["version"], "decision": "accepted"}
            for row in candidates]}))
    return job, run_id, candidates


def materialize(client, headers, model, job, run_id, *, graph=None):
    target = graph or model.graph
    current = result(client.get(f"{GRAPHS}/{target['id']}", headers=headers))
    return client.post(f"{EXTRACTIONS}/{job['id']}/materialize", headers=headers,
        json={"runId": run_id, "graphId": target["id"], "graphVersion": current["version"]})


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_materialize_rejects_existing_different_confirmed_content(client, administrator, db, kind):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition()]})
    source = result(entity(client, administrator, model, "S", {"value": "旧值"} if kind == "entity" else {}), 201)
    target = result(entity(client, administrator, model, "T", target=True), 201)
    if kind == "relation":
        result(client.post(f"{GRAPHS}/{model.graph['id']}/relations", headers=administrator, json={
            "relationTypeId": model.relation["id"], "sourceEntityId": source["id"],
            "targetEntityId": target["id"], "properties": {"value": "旧值"}}), 201)
    job, run_id, _ = accepted_run(client, administrator, model, kind=kind)
    response = materialize(client, administrator, model, job, run_id)
    assert response.status_code == 409, response.text
    record = db.scalar(select(GraphEntity if kind == "entity" else GraphRelation).where(
        (GraphEntity.id == source["id"]) if kind == "entity" else (GraphRelation.graph_id == model.graph["id"])))
    assert record.properties == {"value": "旧值"}
    run = db.get(ExtractionRun, run_id, populate_existing=True)
    assert run.status == "review" and "materialization" not in run.summary


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_exact_replay_locks_run_then_graph_and_current_children(client, administrator, kind):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition("number")]})
    job, run_id, _ = accepted_run(client, administrator, model, kind=kind, value=7)
    first = result(materialize(client, administrator, model, job, run_id))
    locks = []

    def observe(state):
        if state.is_select and getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append((state.statement.column_descriptions[0].get("entity"),
                str(state.statement.compile(dialect=mysql.dialect())), state.execution_options.get("populate_existing")))

    event.listen(Session, "do_orm_execute", observe)
    try:
        second = result(materialize(client, administrator, model, job, run_id))
    finally:
        event.remove(Session, "do_orm_execute", observe)
    assert [row[0] for row in locks] == [ExtractionRun, KnowledgeGraph, ExtractionCandidate,
        GraphEntity, GraphEntity, GraphRelation]
    assert all("FOR UPDATE" in row[1] and row[2] for row in locks)
    assert second["result"] == {"graphId": model.graph["id"], "runId": run_id,
        "createdEntities": 0, "reusedEntities": 2, "createdRelations": 0, "reusedRelations": 1}
    assert first["result"]["createdEntities"] == 2


def test_run_uses_its_fixed_ontology_after_job_edit(client, administrator):
    original = model_graph(client, administrator, entity_attributes=[definition()])
    replacement = model_graph(client, administrator, entity_attributes=[definition("integer")])
    job, run_id, _ = accepted_run(client, administrator, original)
    current = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    payload = job_payload(replacement, value=1)
    payload["version"] = current["version"]
    result(client.put(f"{EXTRACTIONS}/{job['id']}", headers=administrator, json=payload))
    assert materialize(client, administrator, original, job, run_id).status_code == 200


def test_first_target_survives_job_edit_and_later_run(client, administrator):
    model = model_graph(client, administrator, entity_attributes=[definition()])
    graph2 = result(client.post(GRAPHS, headers=administrator,
        json={"name": "第二目标", "ontologyId": model.ontology["id"]}), 201)
    job, first_run, _ = accepted_run(client, administrator, model)
    result(materialize(client, administrator, model, job, first_run))
    current = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    payload = job_payload(model)
    payload["version"] = current["version"]
    edited = result(client.put(f"{EXTRACTIONS}/{job['id']}", headers=administrator, json=payload))
    result(materialize(client, administrator, model, job, first_run))
    current = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    second_run, candidates = execute(client, administrator, current)
    result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{second_run}/review", headers=administrator,
        json={"items": [{"candidateId": row["id"], "version": row["version"], "decision": "accepted"}
            for row in candidates]}))
    result(materialize(client, administrator, model, job, second_run, graph=graph2))
    assert materialize(client, administrator, model, job, first_run, graph=graph2).status_code == 409
    result(materialize(client, administrator, model, job, first_run))
    assert edited["targetGraphId"] is None


@pytest.mark.parametrize("failure", ["content", "commit"])
def test_materialization_rolls_back_actual_prior_inserts(client, administrator, db, failure):
    model = model_graph(client, administrator, entity_attributes=[definition()])
    old = result(entity(client, administrator, model, "S", {"value": "旧值"}), 201) if failure == "content" else None
    job, run_id, candidates = accepted_run(client, administrator, model, records=[
        {"key": "NEW", "target": "TN", "value": "新值"}, {"key": "S", "target": "T", "value": "新值"}])
    # 明确排序使新行确实先 flush；不能依赖同一时间戳下未定义的数据库顺序。
    now = datetime.utcnow()
    ordered = sorted(candidates, key=lambda row: row["payload"].get("businessKey") != "NEW")
    for position, row in enumerate(ordered):
        db.get(ExtractionCandidate, row["id"]).created_at = now + timedelta(seconds=position)
    db.commit()
    inserts = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into kg_graph_"):
            inserts.append(statement)

    def fail_commit(session):
        if any(isinstance(row, ExtractionRun) and row.status == "completed" for row in session.dirty):
            raise IntegrityError("隔离提交失败", {}, RuntimeError("受控故障"))

    event.listen(db.get_bind(), "after_cursor_execute", observe)
    if failure == "commit":
        event.listen(Session, "before_commit", fail_commit)
    try:
        response = materialize(client, administrator, model, job, run_id)
    finally:
        event.remove(db.get_bind(), "after_cursor_execute", observe)
        if failure == "commit":
            event.remove(Session, "before_commit", fail_commit)
    assert response.status_code == 409, response.text
    assert inserts, "必须实际写入后再失败，才证明事务回滚"
    assert db.scalar(select(func.count()).select_from(GraphEntity)) == (1 if old else 0)
    assert db.scalar(select(func.count()).select_from(GraphRelation)) == 0
    run = db.get(ExtractionRun, run_id, populate_existing=True)
    assert run.status == "review" and "materialization" not in run.summary


@pytest.mark.parametrize("missing", ["snapshot", "first_target", "malformed_summary"])
def test_ambiguous_historical_run_metadata_is_rejected(client, administrator, db, missing):
    model = model_graph(client, administrator, entity_attributes=[definition()])
    job, run_id, _ = accepted_run(client, administrator, model)
    if missing != "snapshot":
        result(materialize(client, administrator, model, job, run_id))
    run = db.get(ExtractionRun, run_id)
    if missing == "snapshot":
        run.input_snapshot = {}
    else:
        run.summary = [] if missing == "malformed_summary" else {}
    db.commit()
    assert materialize(client, administrator, model, job, run_id).status_code == (422 if missing == "snapshot" else 409)


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_legacy_accepted_invalid_payload_cannot_bypass_validation_by_reuse(client, administrator, db, kind):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition("integer")]})
    job, run_id, candidates = accepted_run(client, administrator, model, kind=kind, value=1)
    result(materialize(client, administrator, model, job, run_id))
    candidate = next(row for row in candidates if row["kind"] == kind and row["payload"]["properties"])
    row = db.get(ExtractionCandidate, candidate["id"])
    row.payload = {**row.payload, "properties": {"value": True}}
    # JSON 的脏值比较会把 True 与 1 视为相等，历史坏值夹具必须明确强制写入并核实。
    flag_modified(row, "payload")
    db.commit()
    db.refresh(row)
    assert row.payload["properties"]["value"] is True
    assert materialize(client, administrator, model, job, run_id).status_code == 422


@pytest.mark.parametrize("attribute,stored,proposed,expected", [
    (definition("number"), 1, 1.0, 200),
    (definition("datetime"), "2025-01-01T08:00+08:00", "2025-01-01T00:00Z", 200),
    (definition("json"), {"a": 1, "b": 2}, {"b": 2, "a": 1}, 200),
    (definition("json"), {"a": 1}, {"a": True}, 409),
    (definition(), None, "新增值", 409),
])
def test_reuse_compares_values_using_fixed_types(client, administrator, attribute, stored, proposed, expected):
    model = model_graph(client, administrator, entity_attributes=[attribute])
    result(entity(client, administrator, model, "S", {"value": stored}), 201)
    job, run_id, _ = accepted_run(client, administrator, model, value=proposed)
    response = materialize(client, administrator, model, job, run_id)
    assert response.status_code == expected, response.text


def test_review_and_materialization_keep_revision_when_same_ontology_is_republished(client, administrator):
    model = model_graph(client, administrator, entity_attributes=[definition()])
    job = result(client.post(EXTRACTIONS, headers=administrator, json=job_payload(model)), 201)
    run_id, candidates = execute(client, administrator, job)
    result(client.put(f"{ONTOLOGY}/{model.ontology['id']}/concepts/{model.source['id']}", headers=administrator,
        json={"code": "Source", "name": "源", "version": model.source["version"], "attributes": [definition("integer")]}))
    published = result(client.post(f"{ONTOLOGY}/{model.ontology['id']}/publish", headers=administrator,
        json={"version": model.ontology["version"]}))
    assert published["publishedRevision"] == 2
    graph2 = result(client.post(GRAPHS, headers=administrator,
        json={"name": "新版图谱", "ontologyId": model.ontology["id"]}), 201)
    current = result(client.get(f"{EXTRACTIONS}/{job['id']}", headers=administrator))
    edited = result(client.put(f"{EXTRACTIONS}/{job['id']}", headers=administrator,
        json={**job_payload(model, value=1), "version": current["version"]}))
    assert edited["ontologyRevision"] == 2
    result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=administrator,
        json={"items": [{"candidateId": row["id"], "version": row["version"], "decision": "accepted"}
            for row in candidates]}))
    assert materialize(client, administrator, model, job, run_id, graph=graph2).status_code == 422
    result(materialize(client, administrator, model, job, run_id))


@pytest.mark.parametrize("invalid", ["other_job_run", "foreign_graph", "stale_graph_version"])
def test_materialization_preserves_run_workspace_and_version_boundaries(client, administrator, db, invalid):
    model = model_graph(client, administrator, entity_attributes=[definition()])
    job, run_id, _ = accepted_run(client, administrator, model)
    graph = model.graph
    if invalid == "other_job_run":
        _, run_id, _ = accepted_run(client, administrator, model)
    elif invalid == "foreign_graph":
        original = db.get(Workspace, administrator["X-Workspace-Id"])
        owner = db.scalar(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == original.id))
        foreign = Workspace(id=new_id(), organization_id=original.organization_id,
            name="图谱隔离工作区", created_at=datetime.utcnow())
        db.add(foreign)
        db.add(WorkspaceMember(id=new_id(), workspace_id=foreign.id, user_id=owner, role="admin", created_at=datetime.utcnow()))
        db.commit()
        graph = model_graph(client, {**administrator, "X-Workspace-Id": foreign.id}).graph
    response = client.post(f"{EXTRACTIONS}/{job['id']}/materialize", headers=administrator,
        json={"runId": run_id, "graphId": graph["id"],
            "graphVersion": graph["version"] + (1 if invalid == "stale_graph_version" else 0)})
    assert response.status_code == (409 if invalid == "stale_graph_version" else 404), response.text
    assert db.scalar(select(func.count()).select_from(GraphEntity)) == 0
