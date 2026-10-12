"""Qknow ontology -> extraction -> graph -> Bot business chain on isolated SQLite.

中文说明：本体 -> 抽取 -> 图谱 -> Bot 的业务链路（在隔离 SQLite 上）。
隔离方式：client 夹具（内存 SQLite + TestClient），不连业务库；抽取用确定性桩。
"""
from datetime import datetime

from sqlalchemy import select, text

from app.ids import new_id
from app.models import Connector, File, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, Skill, Tool, WorkspaceMember
from test_system_foundation import activate_user, create_role, create_user


ONTOLOGY_API = "/api/v1/ontologies"
EXTRACTION_API = "/api/v1/extractions"
GRAPH_API = "/api/v1/graphs"
BOT_API = "/api/v1/bots"


def test_bot_rejects_archived_bindings_and_tools_of_archived_connectors(client, administrator, db):
    workspace_id = administrator["X-Workspace-Id"]
    owner_id = db.scalar(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id))
    now = datetime.utcnow()
    common = {"workspace_id": workspace_id, "created_at": now, "updated_at": now}
    active_skill = Skill(id=new_id(), name="可用技能", owner_user_id=owner_id, **common)
    archived_skill = Skill(id=new_id(), name="归档技能", status="archived", owner_user_id=owner_id, **common)
    active_connector = Connector(id=new_id(), name="可用连接器", provider="http", owner_user_id=owner_id, **common)
    archived_connector = Connector(id=new_id(), name="归档连接器", provider="http", status="archived", owner_user_id=owner_id, **common)
    active_tool = Tool(id=new_id(), name="可用工具", connector_id=active_connector.id, **common)
    archived_tool = Tool(id=new_id(), name="归档工具", status="archived", connector_id=active_connector.id, **common)
    orphan_tool = Tool(id=new_id(), name="父连接器归档", connector_id=archived_connector.id, **common)
    db.add_all([active_skill, archived_skill, active_connector, archived_connector, active_tool, archived_tool, orphan_tool])
    db.commit()
    options = result(client.get(f"{BOT_API}/binding-options", headers=administrator))
    assert active_skill.id in {item["id"] for item in options["skills"]}
    assert archived_skill.id not in {item["id"] for item in options["skills"]}
    assert active_tool.id in {item["id"] for item in options["tools"]}
    assert not {archived_tool.id, orphan_tool.id} & {item["id"] for item in options["tools"]}
    for field, resource_id in [("skillIds", archived_skill.id), ("toolIds", archived_tool.id), ("toolIds", orphan_tool.id)]:
        response = client.post(BOT_API, headers=administrator, json={"name": "旧页面提交", field: [resource_id]})
        assert response.status_code == 404, response.text
        assert "归档" in response.text
    result(client.post(BOT_API, headers=administrator, json={
        "name": "可用绑定", "skillIds": [active_skill.id], "toolIds": [active_tool.id],
    }), 201)


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def create_published_ontology(client, headers):
    model = result(client.post(ONTOLOGY_API, headers=headers, json={
        "name": "设备组成模型", "description": "设备与部件的固定语义",
    }), 201)
    equipment = result(client.post(f"{ONTOLOGY_API}/{model['id']}/concepts", headers=headers, json={
        "code": "Equipment", "name": "设备", "recognitionTerms": ["压缩机", "设备A"],
        "attributes": [{"name": "status", "label": "状态", "type": "string"}],
    }), 201)
    component = result(client.post(f"{ONTOLOGY_API}/{model['id']}/concepts", headers=headers, json={
        "code": "Component", "name": "部件", "recognitionTerms": ["轴承", "部件B"],
        "attributes": [{"name": "material", "label": "材质", "type": "string"}],
    }), 201)
    relation = result(client.post(f"{ONTOLOGY_API}/{model['id']}/relation-types", headers=headers, json={
        "code": "HAS_COMPONENT", "name": "包含部件",
        "sourceConceptId": equipment["id"], "targetConceptId": component["id"],
        "cardinality": "one_to_many", "directed": True,
    }), 201)
    published = result(client.post(f"{ONTOLOGY_API}/{model['id']}/publish", headers=headers, json={
        "version": model["version"], "reason": "首个生产模型",
    }))
    assert published["publishedRevision"] == 1
    return published, equipment, component, relation


def structured_payload(ontology, equipment, component, relation):
    return {
        "name": "设备台账结构化抽取",
        "mode": "structured",
        "ontologyId": ontology["id"],
        "records": [{
            "equipment_id": "EQ-001", "equipment_name": "设备A", "equipment_status": "运行",
            "component_id": "CP-001", "component_name": "部件B", "component_material": "钢",
        }],
        "mappings": {
            "entityMappings": [
                {"conceptId": equipment["id"], "keyField": "equipment_id", "nameField": "equipment_name",
                 "propertyFields": {"status": "equipment_status"}},
                {"conceptId": component["id"], "keyField": "component_id", "nameField": "component_name",
                 "propertyFields": {"material": "component_material"}},
            ],
            "relationMappings": [{
                "relationTypeId": relation["id"],
                "sourceConceptId": equipment["id"], "targetConceptId": component["id"],
                "sourceKeyField": "equipment_id", "targetKeyField": "component_id",
            }],
        },
    }


def test_complete_structured_chain_graph_publish_bot_key_and_revoke(client, administrator, db):
    # Existing MySQL installations constrain audit_logs.run_id to runtime runs.
    # KG extraction/Bot runs live in separate tables and belong in audit metadata.
    db.execute(text("""CREATE TRIGGER audit_runtime_run_reference BEFORE INSERT ON audit_logs
        WHEN NEW.run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM runs WHERE id = NEW.run_id)
        BEGIN SELECT RAISE(ABORT, 'audit run_id must reference a runtime run'); END"""))
    db.commit()
    ontology, equipment, component, relation = create_published_ontology(client, administrator)
    job = result(client.post(EXTRACTION_API, headers=administrator,
                             json=structured_payload(ontology, equipment, component, relation)), 201)
    execution = result(client.post(f"{EXTRACTION_API}/{job['id']}/execute", headers=administrator,
                                   json={"version": job["version"]}))
    run = execution["run"]
    assert run["status"] == "review"
    assert run["summary"]["entities"] == 2 and run["summary"]["relations"] == 1
    candidates = result(client.get(
        f"{EXTRACTION_API}/{job['id']}/runs/{run['id']}/candidates", headers=administrator,
    ))
    assert len(candidates) == 3
    assert all(item["reviewStatus"] == "pending" and item["evidence"]["source"] == "structured"
               for item in candidates)
    graph = result(client.post(GRAPH_API, headers=administrator, json={
        "name": "设备知识图谱", "ontologyId": ontology["id"],
    }), 201)
    incomplete = client.post(f"{EXTRACTION_API}/{job['id']}/materialize", headers=administrator, json={
        "graphId": graph["id"], "graphVersion": graph["version"], "runId": run["id"],
    })
    assert incomplete.status_code == 422, incomplete.text
    assert "待复核" in incomplete.text
    reviewed = result(client.post(
        f"{EXTRACTION_API}/{job['id']}/runs/{run['id']}/review", headers=administrator,
        json={"items": [{
            "candidateId": item["id"], "version": item["version"], "decision": "accepted",
            "note": "台账字段已核对",
        } for item in candidates]},
    ))
    assert reviewed["run"]["summary"]["review"]["accepted"] == 3

    materialized = result(client.post(f"{EXTRACTION_API}/{job['id']}/materialize", headers=administrator, json={
        "graphId": graph["id"], "graphVersion": graph["version"], "runId": run["id"],
    }))
    assert materialized["result"] == {
        "graphId": graph["id"], "runId": run["id"],
        "createdEntities": 2, "reusedEntities": 0, "createdRelations": 1, "reusedRelations": 0,
    }
    graph = materialized["graph"]
    assert graph["statistics"]["entityCount"] == 2
    assert graph["statistics"]["relationCount"] == 1
    assert graph["statistics"]["isolatedEntityCount"] == 0
    locked_candidates = result(client.get(
        f"{EXTRACTION_API}/{job['id']}/runs/{run['id']}/candidates", headers=administrator,
    ))
    assert client.post(f"{EXTRACTION_API}/{job['id']}/runs/{run['id']}/review", headers=administrator, json={
        "items": [{"candidateId": locked_candidates[0]["id"], "version": locked_candidates[0]["version"],
                   "decision": "rejected", "note": "不应允许事后改写"}],
    }).status_code == 409
    other_graph = result(client.post(GRAPH_API, headers=administrator, json={
        "name": "其他目标图谱", "ontologyId": ontology["id"],
    }), 201)
    assert client.post(f"{EXTRACTION_API}/{job['id']}/materialize", headers=administrator, json={
        "graphId": other_graph["id"], "graphVersion": other_graph["version"], "runId": run["id"],
    }).status_code == 409
    explored = result(client.get(f"{GRAPH_API}/{graph['id']}/explore?query=设备A&depth=1", headers=administrator))
    assert {item["name"] for item in explored["nodes"]} == {"设备A", "部件B"}
    graph = result(client.post(f"{GRAPH_API}/{graph['id']}/publish", headers=administrator, json={
        "version": graph["version"], "reason": "供设备助手使用",
    }))
    assert graph["publishedRevision"] == 1

    bot = result(client.post(BOT_API, headers=administrator, json={
        "name": "设备知识助手", "botType": "knowledge", "instructions": "仅依据设备知识回答",
        "graphIds": [graph["id"]], "apiEnabled": True,
    }), 201)
    debug = result(client.post(f"{BOT_API}/{bot['id']}/debug", headers=administrator, json={
        "query": "设备A有什么部件？", "mode": "retrieval",
    }))
    assert debug["status"] == "completed" and debug["references"]
    assert "设备A" in debug["answer"]
    bot = result(client.post(f"{BOT_API}/{bot['id']}/publish", headers=administrator, json={
        "version": bot["version"], "reason": "开放设备查询",
    }))
    key = result(client.post(f"{BOT_API}/{bot['id']}/api-keys", headers=administrator, json={
        "name": "产线调用",
    }), 201)
    assert key["shownOnce"] and key["key"].startswith("t5b_")
    plaintext_key = key["key"]
    listed = result(client.get(f"{BOT_API}/{bot['id']}/api-keys", headers=administrator))
    assert len(listed) == 1 and "key" not in listed[0]
    invoked = result(client.post(f"/api/public/v1/bots/{bot['id']}/invoke", headers={"X-Bot-Key": plaintext_key}, json={
        "query": "设备A关联什么？", "mode": "retrieval",
    }))
    assert invoked["revision"] == 1 and invoked["references"] and invoked["runId"]
    assert result(client.get(f"{BOT_API}/{bot['id']}/runs", headers=administrator))[0]["channel"] == "api"
    key = result(client.get(f"{BOT_API}/{bot['id']}/api-keys", headers=administrator))[0]
    result(client.delete(
        f"{BOT_API}/{bot['id']}/api-keys/{key['id']}?version={key['version']}", headers=administrator,
    ))
    assert client.post(f"/api/public/v1/bots/{bot['id']}/invoke", headers={"X-Bot-Key": plaintext_key}, json={
        "query": "设备A", "mode": "retrieval",
    }).status_code == 404


def test_unstructured_uses_real_chunks_and_keeps_location_evidence(client, administrator, db):
    ontology, equipment, component, relation = create_published_ontology(client, administrator)
    workspace_id = administrator["X-Workspace-Id"]
    owner_id = db.scalar(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id))
    now = datetime.utcnow()
    kb = KnowledgeBase(id=new_id(), workspace_id=workspace_id, owner_user_id=owner_id,
                       name="维修记录", description="", visibility="workspace", status="active",
                       created_at=now, updated_at=now)
    # 来源必须包含真实可用文件，不能靠无对应记录的 file_id 绕过文件状态校验。
    source_file = File(id=new_id(), workspace_id=workspace_id, owner_user_id=owner_id,
                       name="维修记录.txt", mime_type="text/plain", size=100, storage_key=new_id(),
                       sha256="0" * 64, status="ready", created_at=now)
    document = KnowledgeDocument(id=new_id(), workspace_id=workspace_id, knowledge_base_id=kb.id,
                                 file_id=source_file.id, status="ready", parser_version="v2-c500-o80-i1",
                                 created_at=now, updated_at=now)
    chunk = KnowledgeChunk(id=new_id(), workspace_id=workspace_id, knowledge_base_id=kb.id,
                           document_id=document.id, ordinal=0,
                           text="设备A压缩机巡检发现部件B轴承温度偏高，建议停机复核。",
                           location={"page": 3, "block": 2}, embedding_status="done", created_at=now)
    db.add_all([kb, source_file, document, chunk])
    db.commit()
    job = result(client.post(EXTRACTION_API, headers=administrator, json={
        "name": "维修文档抽取", "mode": "unstructured", "ontologyId": ontology["id"],
        "knowledgeBaseId": kb.id, "documentIds": [document.id], "mappings": {},
    }), 201)
    execution = result(client.post(f"{EXTRACTION_API}/{job['id']}/execute", headers=administrator,
                                   json={"version": job["version"]}))
    candidates = result(client.get(
        f"{EXTRACTION_API}/{job['id']}/runs/{execution['run']['id']}/candidates", headers=administrator,
    ))
    entities = [item for item in candidates if item["kind"] == "entity"]
    relations = [item for item in candidates if item["kind"] == "relation"]
    assert len(entities) >= 4 and relations
    evidence = entities[0]["evidence"]
    assert evidence["source"] == "unstructured"
    assert evidence["matches"][0]["chunkId"] == chunk.id
    assert evidence["matches"][0]["documentId"] == document.id
    assert evidence["matches"][0]["location"] == {"page": 3, "block": 2}
    assert "温度偏高" in evidence["matches"][0]["excerpt"]
    assert all(item["evidence"]["method"] == "co_occurrence_candidate" for item in relations)


def test_invalid_mapping_fails_honestly_versions_conflict_and_openapi_is_documented(client, administrator):
    ontology, equipment, component, relation = create_published_ontology(client, administrator)
    payload = structured_payload(ontology, equipment, component, relation)
    payload["mappings"]["entityMappings"] = payload["mappings"]["entityMappings"][:1]
    job = result(client.post(EXTRACTION_API, headers=administrator, json=payload), 201)
    failed = client.post(f"{EXTRACTION_API}/{job['id']}/execute", headers=administrator,
                         json={"version": job["version"]})
    assert failed.status_code == 422 and "未找到对应的实体候选" in failed.text
    saved = result(client.get(f"{EXTRACTION_API}/{job['id']}", headers=administrator))
    assert saved["status"] == "failed" and saved["runs"][0]["status"] == "failed"
    stale = client.put(f"{ONTOLOGY_API}/{ontology['id']}", headers=administrator, json={
        "name": "过期修改", "version": 1,
    })
    assert stale.status_code == 409

    spec = client.app.openapi()
    prefixes = (ONTOLOGY_API, EXTRACTION_API, GRAPH_API, BOT_API, "/api/public/v1/bots")
    operations = [operation for path, methods in spec["paths"].items() if path.startswith(prefixes)
                  for method, operation in methods.items() if method in {"get", "post", "put", "delete"}]
    assert len(operations) >= 45
    assert len({item["operationId"] for item in operations}) == len(operations)
    assert all(item.get("summary") and item.get("description") for item in operations)


def test_graph_keeps_pinned_cardinality_after_ontology_draft_changes(client, administrator):
    ontology, equipment, component, relation = create_published_ontology(client, administrator)
    graph = result(client.post(GRAPH_API, headers=administrator, json={
        "name": "固定基数图谱", "ontologyId": ontology["id"],
    }), 201)
    relation = result(client.put(f"{ONTOLOGY_API}/{ontology['id']}/relation-types/{relation['id']}",
                                 headers=administrator, json={
        "code": relation["code"], "name": relation["name"],
        "sourceConceptId": equipment["id"], "targetConceptId": component["id"],
        "cardinality": "many_to_many", "directed": True, "version": relation["version"],
    }))
    result(client.post(f"{ONTOLOGY_API}/{ontology['id']}/publish", headers=administrator, json={
        "version": ontology["version"], "reason": "新版允许多对多",
    }))
    source = result(client.post(f"{GRAPH_API}/{graph['id']}/entities", headers=administrator, json={
        "conceptId": equipment["id"], "businessKey": "EQ-PIN", "name": "固定设备", "properties": {},
    }), 201)
    first = result(client.post(f"{GRAPH_API}/{graph['id']}/entities", headers=administrator, json={
        "conceptId": component["id"], "businessKey": "CP-1", "name": "部件一", "properties": {},
    }), 201)
    second_source = result(client.post(f"{GRAPH_API}/{graph['id']}/entities", headers=administrator, json={
        "conceptId": equipment["id"], "businessKey": "EQ-PIN-2", "name": "另一设备", "properties": {},
    }), 201)
    result(client.post(f"{GRAPH_API}/{graph['id']}/relations", headers=administrator, json={
        "relationTypeId": relation["id"], "sourceEntityId": source["id"], "targetEntityId": first["id"],
        "properties": {},
    }), 201)
    conflict = client.post(f"{GRAPH_API}/{graph['id']}/relations", headers=administrator, json={
        "relationTypeId": relation["id"], "sourceEntityId": second_source["id"], "targetEntityId": first["id"],
        "properties": {},
    })
    assert conflict.status_code == 409 and "一对多" in conflict.text
    assert result(client.get(f"{GRAPH_API}/{graph['id']}", headers=administrator))["ontologyRevision"] == 1


def test_viewer_can_read_but_cannot_mutate_or_cross_module(client, administrator):
    ontology, *_ = create_published_ontology(client, administrator)
    role = create_role(client, administrator, "ontology_viewer", ["home.view", "ontology.view"], "workspace")
    create_user(client, administrator, [role["id"]], "ontology-reader")
    viewer = activate_user(client, "ontology-reader")
    items = result(client.get(ONTOLOGY_API, headers=viewer))
    assert [item["id"] for item in items] == [ontology["id"]]
    assert client.post(ONTOLOGY_API, headers=viewer, json={"name": "越权创建"}).status_code == 403
    assert client.get(EXTRACTION_API, headers=viewer).status_code == 403
    navigation = client.get("/api/v1/platform/navigation", headers=viewer).json()
    assert {item["id"] for item in navigation["items"]} == {"home", "ontology"}
