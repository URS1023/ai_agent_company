"""经真实 HTTP 验证固定本体属性；仅使用隔离 SQLite，不连接业务库或模型。"""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from app.ids import new_id
from app.models import Workspace, WorkspaceMember
from app.modules.knowledge_graph.models import (
    ExtractionRun, GraphEntity, GraphRelation, KnowledgeGraph, OntologyRevision,
)
from test_knowledge_graph import result


ONTOLOGY = "/api/v1/ontologies"
GRAPHS = "/api/v1/graphs"
EXTRACTIONS = "/api/v1/extractions"


def definition(kind="string", **values):
    return {"name": "value", "label": "业务属性", "type": kind, **values}


def model_graph(client, headers, *, entity_attributes=None, relation_attributes=None, target_attributes=None):
    """通过管理契约发布最小本体，让图谱实际固定本体修订。"""
    ontology = result(client.post(ONTOLOGY, headers=headers, json={"name": "属性 " + new_id()}), 201)
    source = result(client.post(f"{ONTOLOGY}/{ontology['id']}/concepts", headers=headers,
        json={"code": "Source", "name": "源", "attributes": entity_attributes or []}), 201)
    target = result(client.post(f"{ONTOLOGY}/{ontology['id']}/concepts", headers=headers,
        json={"code": "Target", "name": "目标", "attributes": target_attributes or []}), 201)
    relation = result(client.post(f"{ONTOLOGY}/{ontology['id']}/relation-types", headers=headers,
        json={"code": "CONNECTS", "name": "连接", "sourceConceptId": source["id"],
            "targetConceptId": target["id"], "attributes": relation_attributes or []}), 201)
    ontology = result(client.post(f"{ONTOLOGY}/{ontology['id']}/publish", headers=headers,
        json={"version": ontology["version"]}))
    graph = result(client.post(GRAPHS, headers=headers,
        json={"name": "属性图 " + new_id(), "ontologyId": ontology["id"]}), 201)
    return SimpleNamespace(ontology=ontology, source=source, target=target, relation=relation, graph=graph)


def entity(client, headers, model, key, properties=None, *, target=False):
    return client.post(f"{GRAPHS}/{model.graph['id']}/entities", headers=headers,
        json={"conceptId": (model.target if target else model.source)["id"],
            "businessKey": key, "name": key, "properties": properties or {}})


def write_property(client, headers, model, kind, value):
    if kind == "entity":
        return entity(client, headers, model, new_id(), {"value": value})
    source = result(entity(client, headers, model, new_id()), 201)
    target = result(entity(client, headers, model, new_id(), target=True), 201)
    return client.post(f"{GRAPHS}/{model.graph['id']}/relations", headers=headers,
        json={"relationTypeId": model.relation["id"], "sourceEntityId": source["id"],
            "targetEntityId": target["id"], "properties": {"value": value}})


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_unique_attribute_rejects_create_and_update_but_allows_self(client, administrator, kind):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition(unique=True)]})
    first = result(write_property(client, administrator, model, kind, "SAME"), 201)
    duplicate = write_property(client, administrator, model, kind, "SAME")
    assert duplicate.status_code == 409, duplicate.text
    second = result(write_property(client, administrator, model, kind, "OTHER"), 201)
    resource = "entities" if kind == "entity" else "relations"
    payload = ({"conceptId": model.source["id"], "businessKey": second["businessKey"], "name": second["name"]}
        if kind == "entity" else {"relationTypeId": model.relation["id"],
            "sourceEntityId": second["sourceEntityId"], "targetEntityId": second["targetEntityId"]})
    payload.update(version=second["version"], properties={"value": "SAME"})
    rejected = client.put(f"{GRAPHS}/{model.graph['id']}/{resource}/{second['id']}",
        headers=administrator, json=payload)
    assert rejected.status_code == 409, rejected.text
    rows = result(client.get(f"{GRAPHS}/{model.graph['id']}/{resource}", headers=administrator))
    unchanged = next(item for item in rows if item["id"] == second["id"])
    assert unchanged["properties"] == {"value": "OTHER"} and unchanged["version"] == second["version"]
    payload["properties"] = {"value": "OTHER"}
    result(client.put(f"{GRAPHS}/{model.graph['id']}/{resource}/{second['id']}",
        headers=administrator, json=payload))
    assert first["properties"] == {"value": "SAME"}


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_materialization_unique_conflict_rolls_back_prior_flushed_rows(client, administrator, db, kind):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition(unique=True)]})
    job = result(client.post(EXTRACTIONS, headers=administrator, json={
        "name": "批内唯一冲突", "mode": "structured", "ontologyId": model.ontology["id"],
        "records": [{"key": str(index), "target": "T" + str(index), "value": "SAME"} for index in range(2)],
        "mappings": {"entityMappings": [
            {"conceptId": model.source["id"], "keyField": "key", "nameField": "key",
                "propertyFields": {"value": "value"} if kind == "entity" else {}},
            {"conceptId": model.target["id"], "keyField": "target", "nameField": "target"}],
            "relationMappings": [{"relationTypeId": model.relation["id"],
                "sourceConceptId": model.source["id"], "targetConceptId": model.target["id"],
                "sourceKeyField": "key", "targetKeyField": "target",
                "propertyFields": {"value": "value"} if kind == "relation" else {}}]},
    }), 201)
    execution = result(client.post(f"{EXTRACTIONS}/{job['id']}/execute", headers=administrator,
        json={"version": job["version"]}))
    run_id = execution["run"]["id"]
    candidates = result(client.get(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/candidates", headers=administrator))
    result(client.post(f"{EXTRACTIONS}/{job['id']}/runs/{run_id}/review", headers=administrator,
        json={"items": [{"candidateId": item["id"], "version": item["version"], "decision": "accepted"}
            for item in candidates]}))
    inserts = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into kg_graph_"):
            inserts.append(statement.lower())

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", observe)
    try:
        response = client.post(f"{EXTRACTIONS}/{job['id']}/materialize", headers=administrator,
            json={"runId": run_id, "graphId": model.graph["id"], "graphVersion": model.graph["version"]})
    finally:
        event.remove(engine, "after_cursor_execute", observe)
    assert response.status_code == 409, response.text
    table = "kg_graph_entities" if kind == "entity" else "kg_graph_relations"
    assert any(table in statement for statement in inserts), "冲突前必须已有真实 flush，才能证明整批回滚"
    assert db.scalar(select(func.count()).select_from(GraphEntity)) == 0
    assert db.scalar(select(func.count()).select_from(GraphRelation)) == 0
    run = db.get(ExtractionRun, run_id, populate_existing=True)
    assert run.status == "review" and "materialization" not in run.summary
    after = result(client.get(f"{GRAPHS}/{model.graph['id']}", headers=administrator))
    assert after["version"] == model.graph["version"] and after["updatedAt"] == model.graph["updatedAt"]


@pytest.mark.parametrize("kind", ["entity", "relation"])
@pytest.mark.parametrize("attribute_type,valid,invalid", [
    ("date", "2024-02-29", "2025-02-29"),
    ("date", "2024-02-29", "不是日期"),
    ("datetime", "2025-01-01T12:30:00Z", "2025-01-01T25:00:00"),
    ("datetime", "2025-01-01T12:30", "2025-01-01"),
    ("datetime", "2025-01-01T12:30:00+08:00", "2025-01-01T12:30:00+08:60"),
])
def test_date_attributes_validate_calendar_and_time(client, administrator, kind, attribute_type, valid, invalid):
    model = model_graph(client, administrator, **{kind + "_attributes": [definition(attribute_type)]})
    result(write_property(client, administrator, model, kind, valid), 201)
    response = write_property(client, administrator, model, kind, invalid)
    assert response.status_code == 422, response.text
    resource = "entities" if kind == "entity" else "relations"
    rows = result(client.get(f"{GRAPHS}/{model.graph['id']}/{resource}", headers=administrator))
    assert len(rows) == 1 and rows[0]["properties"] == {"value": valid}


@pytest.mark.parametrize("attribute_type,value,legacy,wrong,outside", [
    ("integer", 1, "1", True, 2),
    ("number", 1.5, "1.5", "1.5", 2.5),
    ("boolean", True, "true", 1, False),
])
@pytest.mark.parametrize("old_snapshot", [False, True])
def test_native_and_legacy_typed_enums_keep_business_value_types(
    client, administrator, db, attribute_type, value, legacy, wrong, outside, old_snapshot,
):
    model = model_graph(client, administrator,
        entity_attributes=[definition(attribute_type, enumValues=[legacy if old_snapshot else value])])
    revision = db.scalar(select(OntologyRevision).where(OntologyRevision.ontology_id == model.ontology["id"]))
    if old_snapshot:
        # 构造升级前真实可能存在的字符串枚举快照；运行时只能解释，不能改写历史。
        snapshot = deepcopy(revision.snapshot)
        source = next(item for item in snapshot["concepts"] if item["id"] == model.source["id"])
        source["attributes"][0]["enumValues"] = [legacy]
        revision.snapshot = snapshot
        db.commit()
    before = deepcopy(revision.snapshot)
    result(write_property(client, administrator, model, "entity", value), 201)
    for rejected in (wrong, outside):
        response = write_property(client, administrator, model, "entity", rejected)
        assert response.status_code == 422, response.text
    db.refresh(revision)
    assert revision.snapshot == before
    rows = result(client.get(f"{GRAPHS}/{model.graph['id']}/entities", headers=administrator))
    assert len(rows) == 1 and type(rows[0]["properties"]["value"]) is type(value)


@pytest.mark.parametrize("updating", [False, True])
def test_relation_attribute_names_reject_duplicates_before_persistence(client, administrator, updating):
    model = model_graph(client, administrator)
    payload = {"code": "BAD" if not updating else model.relation["code"], "name": "重复属性",
        "sourceConceptId": model.source["id"], "targetConceptId": model.target["id"],
        "attributes": [definition("integer", required=True), definition("string")]}
    path = f"{ONTOLOGY}/{model.ontology['id']}/relation-types"
    if updating:
        payload["version"] = model.relation["version"]
        response = client.put(path + "/" + model.relation["id"], headers=administrator, json=payload)
    else:
        response = client.post(path, headers=administrator, json=payload)
    assert response.status_code == 422, response.text
    detail = result(client.get(f"{ONTOLOGY}/{model.ontology['id']}", headers=administrator))
    assert len(detail["relationTypes"]) == 1
    unchanged = detail["relationTypes"][0]
    assert unchanged["version"] == model.relation["version"] and unchanged["attributes"] == []


def test_legacy_duplicate_definition_fails_closed(client, administrator, db):
    model = model_graph(client, administrator)
    revision = db.scalar(select(OntologyRevision).where(OntologyRevision.ontology_id == model.ontology["id"]))
    snapshot = deepcopy(revision.snapshot)
    snapshot["relationTypes"][0]["attributes"] = [definition("integer", required=True), definition("string")]
    revision.snapshot = snapshot
    db.commit()
    response = write_property(client, administrator, model, "relation", "末项不能覆盖")
    assert response.status_code == 422, response.text
    assert db.scalar(select(func.count()).select_from(GraphRelation)) == 0


def test_unique_attribute_uses_pinned_ontology_revision(client, administrator):
    model = model_graph(client, administrator, entity_attributes=[definition(unique=True)])
    result(write_property(client, administrator, model, "entity", "SAME"), 201)
    result(client.put(f"{ONTOLOGY}/{model.ontology['id']}/concepts/{model.source['id']}",
        headers=administrator, json={"code": "Source", "name": "源", "version": model.source["version"],
            "attributes": [definition(unique=False)]}))
    result(client.post(f"{ONTOLOGY}/{model.ontology['id']}/publish", headers=administrator,
        json={"version": model.ontology["version"]}))
    assert write_property(client, administrator, model, "entity", "SAME").status_code == 409
    model.graph = result(client.post(GRAPHS, headers=administrator,
        json={"name": "新修订图", "ontologyId": model.ontology["id"]}), 201)
    assert model.graph["ontologyRevision"] == 2
    result(write_property(client, administrator, model, "entity", "SAME"), 201)
    result(write_property(client, administrator, model, "entity", "SAME"), 201)


@pytest.mark.parametrize("attribute_type,enums", [
    ("integer", [True]), ("boolean", [1]), ("number", ["NaN"]),
    ("integer", ["1", 1]), ("date", ["2025-02-29"]), ("json", ["{}"]),
])
def test_invalid_or_ambiguous_enum_definition_is_rejected(client, administrator, attribute_type, enums):
    ontology = result(client.post(ONTOLOGY, headers=administrator, json={"name": "非法枚举"}), 201)
    response = client.post(f"{ONTOLOGY}/{ontology['id']}/concepts", headers=administrator,
        json={"code": "Invalid", "name": "非法", "attributes": [definition(attribute_type, enumValues=enums)]})
    assert response.status_code == 422, response.text
    detail = result(client.get(f"{ONTOLOGY}/{ontology['id']}", headers=administrator))
    assert detail["concepts"] == []


@pytest.mark.parametrize("attribute_type,first,equivalent", [
    ("number", 1, 1.0),
    ("datetime", "2025-01-01T12:00:00+08:00", "2025-01-01T04:00:00Z"),
    ("json", {"a": 1, "b": [2]}, {"b": [2], "a": 1}),
])
def test_unique_comparison_uses_declared_type(client, administrator, attribute_type, first, equivalent):
    model = model_graph(client, administrator, entity_attributes=[definition(attribute_type, unique=True)])
    result(write_property(client, administrator, model, "entity", first), 201)
    response = write_property(client, administrator, model, "entity", equivalent)
    assert response.status_code == 409, response.text


def test_unique_scope_null_case_and_deleted_record_contract(client, administrator):
    attributes = [definition(unique=True)]
    model = model_graph(client, administrator, entity_attributes=attributes, target_attributes=attributes)
    result(entity(client, administrator, model, "missing"), 201)
    result(entity(client, administrator, model, "null", {"value": None}), 201)
    result(entity(client, administrator, model, "empty", {"value": ""}), 201)
    assert entity(client, administrator, model, "empty-again", {"value": ""}).status_code == 409
    original = result(entity(client, administrator, model, "upper", {"value": "SAME"}), 201)
    result(entity(client, administrator, model, "lower", {"value": "same"}), 201)
    result(entity(client, administrator, model, "other-concept", {"value": "SAME"}, target=True), 201)
    result(client.delete(f"{GRAPHS}/{model.graph['id']}/entities/{original['id']}?version={original['version']}",
        headers=administrator))
    result(entity(client, administrator, model, "released", {"value": "SAME"}), 201)
    model.graph = result(client.post(GRAPHS, headers=administrator,
        json={"name": "独立唯一范围", "ontologyId": model.ontology["id"]}), 201)
    result(entity(client, administrator, model, "other-graph", {"value": "SAME"}), 201)


def test_unique_parent_lock_keeps_workspace_authorization(client, administrator, db):
    model = model_graph(client, administrator, entity_attributes=[definition(unique=True)])
    result(entity(client, administrator, model, "first", {"value": "SAME"}), 201)
    current = db.get(Workspace, administrator["X-Workspace-Id"])
    owner = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    other = Workspace(id=new_id(), organization_id=current.organization_id, name="另一工作区", created_at=datetime.utcnow())
    db.add(other)
    db.flush()
    db.add(WorkspaceMember(id=new_id(), workspace_id=other.id, user_id=owner, role="admin", created_at=datetime.utcnow()))
    db.commit()
    foreign_headers = {**administrator, "X-Workspace-Id": other.id}
    assert entity(client, foreign_headers, model, "foreign", {"value": "OTHER"}).status_code == 404
    foreign_model = model_graph(client, foreign_headers, entity_attributes=[definition(unique=True)])
    result(entity(client, foreign_headers, foreign_model, "first", {"value": "SAME"}), 201)


def test_real_http_write_uses_parent_then_current_candidate_reads(client, administrator):
    """捕获真实请求发出的查询并编译 MySQL 方言；不把 SQL 结构验证当作并发证明。"""
    model = model_graph(client, administrator, entity_attributes=[definition(unique=True)])
    locks = []

    def observe(state):
        if state.is_select and getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append((state.statement.column_descriptions[0].get("entity"),
                str(state.statement.compile(dialect=mysql.dialect()))))

    event.listen(Session, "do_orm_execute", observe)
    try:
        result(entity(client, administrator, model, "locked", {"value": "SAME"}), 201)
    finally:
        event.remove(Session, "do_orm_execute", observe)
    assert [item[0] for item in locks] == [KnowledgeGraph, GraphEntity]
    assert all("FOR UPDATE" in item[1] for item in locks)


def test_invalid_historical_values_do_not_block_each_others_repair(client, administrator, db):
    """旧版允许非法日期；多个坏值仍能逐条修复，但新写入绝不继续接受坏值。"""
    model = model_graph(client, administrator, entity_attributes=[definition("date", unique=True)])
    first = result(entity(client, administrator, model, "first", {"value": "2025-01-01"}), 201)
    second = result(entity(client, administrator, model, "second", {"value": "2025-01-02"}), 201)
    for identifier in (first["id"], second["id"]):
        row = db.get(GraphEntity, identifier)
        row.properties = {"value": "旧版非法日期"}
    db.commit()
    row = db.get(GraphEntity, first["id"])
    response = client.put(f"{GRAPHS}/{model.graph['id']}/entities/{first['id']}", headers=administrator,
        json={"conceptId": model.source["id"], "businessKey": "first", "name": "first",
            "version": row.version, "properties": {"value": "2025-01-01"}})
    result(response)
    assert entity(client, administrator, model, "new-bad", {"value": "旧版非法日期"}).status_code == 422
    assert entity(client, administrator, model, "duplicate", {"value": "2025-01-01"}).status_code == 409


@pytest.mark.parametrize("old_snapshot", [False, True])
def test_large_integral_number_enum_is_exact(client, administrator, db, old_snapshot):
    original = "9007199254740993.0"
    model = model_graph(client, administrator, entity_attributes=[definition("number", enumValues=[original])])
    revision = db.scalar(select(OntologyRevision).where(OntologyRevision.ontology_id == model.ontology["id"]))
    if old_snapshot:
        snapshot = deepcopy(revision.snapshot)
        source = next(item for item in snapshot["concepts"] if item["id"] == model.source["id"])
        source["attributes"][0]["enumValues"] = [original]
        revision.snapshot = snapshot
        db.commit()
    before = deepcopy(revision.snapshot)
    result(entity(client, administrator, model, "exact", {"value": 9007199254740993}), 201)
    assert entity(client, administrator, model, "rounded", {"value": 9007199254740992}).status_code == 422
    db.refresh(revision)
    assert revision.snapshot == before


@pytest.mark.parametrize("old_snapshot", [False, True])
def test_inexact_fractional_number_enum_is_rejected(client, administrator, db, old_snapshot):
    original = "0.123456789012345678901"
    if not old_snapshot:
        ontology = result(client.post(ONTOLOGY, headers=administrator, json={"name": "枚举精度"}), 201)
        response = client.post(f"{ONTOLOGY}/{ontology['id']}/concepts", headers=administrator,
            json={"code": "Exact", "name": "精度", "attributes": [definition("number", enumValues=[original])]})
    else:
        model = model_graph(client, administrator, entity_attributes=[definition("number", enumValues=[0.1])])
        revision = db.scalar(select(OntologyRevision).where(OntologyRevision.ontology_id == model.ontology["id"]))
        snapshot = deepcopy(revision.snapshot)
        source = next(item for item in snapshot["concepts"] if item["id"] == model.source["id"])
        source["attributes"][0]["enumValues"] = [original]
        revision.snapshot = snapshot
        db.commit()
        response = entity(client, administrator, model, "rounded", {"value": 0.12345678901234568})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_target_update_has_mapper_version_predicate(client, administrator, db, kind):
    """实际 HTTP 更新必须带 ORM 版本 WHERE；结构验证不冒充真实 MySQL 并发运行。"""
    model = model_graph(client, administrator, **{kind + "_attributes": [definition(unique=True)]})
    row = result(write_property(client, administrator, model, kind, "FIRST"), 201)
    resource = "entities" if kind == "entity" else "relations"
    table = "kg_graph_" + resource
    payload = ({"conceptId": model.source["id"], "businessKey": row["businessKey"], "name": row["name"]}
        if kind == "entity" else {"relationTypeId": model.relation["id"],
            "sourceEntityId": row["sourceEntityId"], "targetEntityId": row["targetEntityId"]})
    payload.update(version=row["version"], properties={"value": "SECOND"})
    updates = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("update " + table):
            updates.append((statement.lower(), parameters))

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", observe)
    try:
        changed = result(client.put(f"{GRAPHS}/{model.graph['id']}/{resource}/{row['id']}",
            headers=administrator, json=payload))
    finally:
        event.remove(engine, "after_cursor_execute", observe)
    assert changed["version"] == row["version"] + 1
    assert len(updates) == 1
    assert table + ".version =" in updates[0][0].split("where", 1)[1]
    assert updates[0][1][-1] == row["version"]
    assert client.put(f"{GRAPHS}/{model.graph['id']}/{resource}/{row['id']}",
        headers=administrator, json=payload).status_code == 409
