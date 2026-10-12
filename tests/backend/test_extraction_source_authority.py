"""以真实隔离 HTTP 验证抽取只读取当前授权且可用的知识来源。"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from sqlalchemy import func, select

from app.common.data_scope import bind_data_scope
from app.errors import NotFoundError, ValidationError
from app.ids import new_id
from app.models import Artifact, File, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, Workspace
from app.modules.files.service import get_file
from app.modules.knowledge.models import KnowledgeChunkMetadata
from app.modules.knowledge_graph import extraction
from app.modules.knowledge_graph.models import ExtractionCandidate, ExtractionRun
from app.providers.tool.gateway import ToolGateway
from test_bot_runtime_authorization import service_headers
from test_knowledge_graph import create_published_ontology, result
from test_knowledge_search_authority import create_segment, index
from test_knowledge_tool_scope import SCOPE_KEYS, context, grant
from test_system_foundation import activate_user, create_role, create_user


API = "/api/v1/extractions"


def unstructured_job(client, headers, ontology, kb_id, document_ids):
    return result(client.post(API, headers=headers, json={
        "name": "当前来源契约", "mode": "unstructured", "ontologyId": ontology["id"],
        "knowledgeBaseId": kb_id, "documentIds": document_ids, "mappings": {}}), 201)


def test_execute_excludes_soft_deleted_source_file(client, administrator, db, index):
    ontology, *_ = create_published_ontology(client, administrator)
    kb_id, chunk = create_segment(client, administrator, "设备A压缩机与部件B轴承，已停用来源不得进入证据")
    document = db.get(KnowledgeDocument, db.get(KnowledgeChunk, chunk["id"]).document_id)
    job = unstructured_job(client, administrator, ontology, kb_id, [document.id])
    db.get(File, document.file_id).deleted_at = datetime.utcnow()
    db.commit()
    response = client.post(f"{API}/{job['id']}/execute", headers=administrator, json={"version": job["version"]})
    assert response.status_code == 422, response.text
    run = db.scalar(select(ExtractionRun).where(ExtractionRun.job_id == job["id"]))
    assert run.status == "failed"
    assert db.scalar(select(func.count()).select_from(ExtractionCandidate).where(
        ExtractionCandidate.run_id == run.id)) == 0


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("invalid", ["file_unready", "document_unready", "disabled", "kb_inactive",
    "file_foreign", "document_foreign", "document_kb_mismatch", "metadata_kb_mismatch"])
def test_current_source_relationship_and_state_required(client, administrator, db, index, explicit, invalid):
    ontology, *_ = create_published_ontology(client, administrator)
    kb_id, chunk = create_segment(client, administrator, "设备A压缩机与部件B轴承")
    doc = db.get(KnowledgeDocument, db.get(KnowledgeChunk, chunk["id"]).document_id)
    job = unstructured_job(client, administrator, ontology, kb_id, [doc.id] if explicit else [])
    if invalid == "file_unready":
        db.get(File, doc.file_id).status = "failed"
    elif invalid == "document_unready":
        doc.status = "processing"
    elif invalid == "disabled":
        db.scalar(select(KnowledgeChunkMetadata).where(KnowledgeChunkMetadata.chunk_id == chunk["id"])).enabled = False
    elif invalid == "kb_inactive":
        db.get(KnowledgeBase, kb_id).status = "archived"
    elif invalid in {"file_foreign", "document_foreign"}:
        original = db.get(Workspace, administrator["X-Workspace-Id"])
        foreign = Workspace(id=new_id(), organization_id=original.organization_id,
            name="隔离其他工作区", created_at=datetime.utcnow())
        db.add(foreign)
        db.flush()
        (db.get(File, doc.file_id) if invalid == "file_foreign" else doc).workspace_id = foreign.id
    else:
        other, _ = create_segment(client, administrator, "另一知识库")
        if invalid == "metadata_kb_mismatch":
            db.scalar(select(KnowledgeChunkMetadata).where(KnowledgeChunkMetadata.chunk_id == chunk["id"])).knowledge_base_id = other
        else:
            doc.knowledge_base_id = other
    db.commit()
    response = client.post(f"{API}/{job['id']}/execute", headers=administrator, json={"version": job["version"]})
    assert response.status_code in {404, 422}, response.text
    assert db.scalar(select(func.count()).select_from(ExtractionCandidate)) == 0


def test_unavailable_chunks_do_not_consume_limit_or_leak_evidence(client, administrator, db, index, monkeypatch):
    ontology, *_ = create_published_ontology(client, administrator)
    kb_id, invalid = create_segment(client, administrator, "设备A压缩机：不得泄漏的停用正文")
    _, valid = create_segment(client, administrator, "部件B轴承：允许读取", kb_id=kb_id)
    db.scalar(select(KnowledgeChunkMetadata).where(KnowledgeChunkMetadata.chunk_id == invalid["id"])).enabled = False
    db.commit()
    monkeypatch.setattr(extraction, "MAX_UNSTRUCTURED_CHUNKS", 1)
    job = unstructured_job(client, administrator, ontology, kb_id, [])
    run = result(client.post(f"{API}/{job['id']}/execute", headers=administrator, json={"version": job["version"]}))["run"]
    candidates = result(client.get(f"{API}/{job['id']}/runs/{run['id']}/candidates", headers=administrator))
    assert candidates and run["summary"]["chunks"] == 1
    assert invalid["id"] not in str(candidates) and "不得泄漏" not in str(candidates)
    assert valid["id"] in str(candidates)


def member(client, administrator, *, file_permissions=False):
    permissions = ["home.view", "knowledge.view", "ontology.view", "ontology.create", "ontology.edit", "ontology.publish",
        "extraction.view", "extraction.create", "extraction.edit", "extraction.execute"]
    if file_permissions:
        permissions += ["files.view", "files.export"]
    role = create_role(client, administrator, code="extraction_reader", permissions=permissions)
    user = create_user(client, administrator, [role["id"]])
    headers = {**activate_user(client), "X-Workspace-Id": administrator["X-Workspace-Id"]}
    return {"headers": headers, "role_id": role["id"], "user_id": user["id"], "role": role}


@pytest.mark.parametrize("file_permissions", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_shared_knowledge_grant_enables_extraction_without_original_file_access(
    client, administrator, db, index, monkeypatch, file_permissions, failure,
):
    kb_id, chunk = create_segment(client, administrator, "设备A压缩机与部件B轴承")
    doc = db.get(KnowledgeDocument, db.get(KnowledgeChunk, chunk["id"]).document_id)
    original = db.get(File, doc.file_id)
    artifact_id = new_id()
    db.add(Artifact(id=artifact_id, workspace_id=original.workspace_id, run_id="fixture-run", owner_user_id=original.owner_user_id,
        type="document", name="管理员附件", mime_type="text/plain", size=0, storage_key=None,
        external_url="https://example.invalid/not-requested", sha256=None, metadata_json={}, status="ready", created_at=datetime.utcnow()))
    db.commit()
    selected = member(client, administrator, file_permissions=file_permissions)
    grant(client, administrator, selected, kb_id)
    ontology, *_ = create_published_ontology(client, selected["headers"])
    job = unstructured_job(client, selected["headers"], ontology, kb_id, [doc.id])
    result(client.post(f"{API}/{job['id']}/execute", headers=selected["headers"], json={"version": job["version"]}))
    tool_ctx = context(selected["headers"], db, administrator["X-Workspace-Id"])
    actor = tool_ctx["auth_context"]
    actor.request_permission = "extraction.execute"
    bind_data_scope(actor, "extraction.execute")
    before = dict(db.info)
    job_source = SimpleNamespace(knowledge_base_id=kb_id, document_ids=[doc.id])
    if failure:
        check_source = extraction._knowledge_source

        def interrupted_source(ctx, knowledge_base_id, document_ids):
            check_source(ctx, knowledge_base_id, document_ids)
            assert db.scalar(select(File).where(File.id == original.id)) is original
            raise ValidationError("在临时范围内读取后失败")

        monkeypatch.setattr(extraction, "_knowledge_source", interrupted_source)
        with pytest.raises(ValidationError):
            extraction._load_chunks(actor, job_source)
    else:
        loaded = extraction._load_chunks(actor, job_source)
        assert [row.id for row in loaded] == [chunk["id"]]
    assert actor.request_permission == "extraction.execute"
    for key in SCOPE_KEYS:
        assert db.info[key] is before[key]
    assert original in db.identity_map.values(), "保留身份映射中的原文件，验证后续强制授权读取"
    assert db.scalar(select(File).where(File.id == original.id)) is None
    assert db.scalar(select(Artifact).where(Artifact.id == artifact_id)) is None
    with pytest.raises(NotFoundError):
        get_file(db, actor.workspace_id, original.id)
    read = ToolGateway(db).execute(actor.workspace_id, "read_file", {"file_id": original.id}, tool_ctx)
    assert read.error_code == ("not_found" if file_permissions else "forbidden")
    for path in (f"/api/files/{original.id}/text", f"/api/files/{original.id}/download",
                 f"/api/artifacts/{artifact_id}", f"/api/artifacts/{artifact_id}/download"):
        assert client.get(path, headers=selected["headers"]).status_code == (404 if file_permissions else 403)


@pytest.mark.parametrize("revoke", ["grant", "role"])
def test_permission_revocation_blocks_next_execution(client, administrator, db, index, revoke):
    kb_id, chunk = create_segment(client, administrator, "设备A压缩机")
    selected = member(client, administrator)
    grant_id = grant(client, administrator, selected, kb_id)
    ontology, *_ = create_published_ontology(client, selected["headers"])
    job = unstructured_job(client, selected["headers"], ontology, kb_id, [])
    if revoke == "grant":
        response = client.delete("/api/v1/system/resource-grants/" + grant_id, headers=administrator)
    else:
        response = client.patch("/api/v1/system/roles/" + selected["role_id"], headers=administrator,
            json={"version": selected["role"]["version"],
                "permissions": [p for p in selected["role"]["permissions"] if p != "knowledge.view"]})
    assert response.status_code == 200, response.text
    response = client.post(f"{API}/{job['id']}/execute", headers=selected["headers"], json={"version": job["version"]})
    assert response.status_code == (404 if revoke == "grant" else 403), response.text
    assert db.scalar(select(func.count()).select_from(ExtractionCandidate)) == 0


@pytest.mark.parametrize("knowledge_scope", [False, True])
def test_real_service_token_keeps_client_scope_and_rotation(client, administrator, db, index, knowledge_scope):
    ontology, *_ = create_published_ontology(client, administrator)
    kb_id, _ = create_segment(client, administrator, "设备A压缩机")
    job = unstructured_job(client, administrator, ontology, kb_id, [])
    scopes = ["extraction.execute", "ontology.view"] + (["knowledge.view"] if knowledge_scope else [])
    configured, headers = service_headers(client, administrator, scopes)
    response = client.post(f"{API}/{job['id']}/execute", headers=headers, json={"version": job["version"]})
    assert response.status_code == (200 if knowledge_scope else 403), response.text
    actor = context(headers, db, administrator["X-Workspace-Id"])["auth_context"]
    assert actor.client_id == configured["clientId"] and actor.client_scopes == frozenset(scopes)
    assert client.get(f"/api/knowledge/{kb_id}", headers=headers).status_code == (200 if knowledge_scope else 403)
    rotated = client.post(f"/api/v1/system-tools/integration-clients/{configured['id']}/rotate", headers=administrator,
        json={"version": configured["version"]})
    assert rotated.status_code == 200, rotated.text
    assert client.post(f"{API}/{job['id']}/execute", headers=headers, json={"version": job["version"]}).status_code == 401


@pytest.mark.parametrize("prebound", [False, True])
@pytest.mark.parametrize("failure", ["none", "query", "binding"])
def test_scope_restores_presence_and_values_on_all_exits(client, administrator, db, index, monkeypatch, prebound, failure):
    kb_id, chunk = create_segment(client, administrator, "设备A压缩机")
    actor = context(administrator, db, administrator["X-Workspace-Id"])["auth_context"]
    actor.request_permission = "extraction.execute"
    bind_data_scope(actor, "extraction.execute")
    if not prebound:
        for key in SCOPE_KEYS:
            db.info.pop(key, None)
    before = dict(db.info)
    marker = object()
    db.info["unrelated"] = marker
    if failure == "query":
        def fail_query(*args):
            raise ValidationError("隔离读取失败")
        monkeypatch.setattr(extraction, "_knowledge_source", fail_query)
    elif failure == "binding":
        def fail_binding(ctx, permission):
            ctx.db.info["actor_user_id"] = "绑定中断"
            ctx.db.info["resource_access_criteria"] = ()
            raise ValidationError("隔离绑定失败")
        monkeypatch.setattr(extraction, "bind_data_scope", fail_binding)
    job = SimpleNamespace(knowledge_base_id=kb_id, document_ids=[])
    if failure == "none":
        assert [row.id for row in extraction._load_chunks(actor, job)] == [chunk["id"]]
    else:
        with pytest.raises(ValidationError):
            extraction._load_chunks(actor, job)
    for key in SCOPE_KEYS:
        assert (key in db.info) == (key in before)
        if key in before:
            assert db.info[key] is before[key]
    assert db.info["unrelated"] is marker and actor.request_permission == "extraction.execute"


def test_nested_source_scope_restores_outer_binding(client, administrator, db, index):
    kb_id, chunk = create_segment(client, administrator, "设备A压缩机")
    actor = context(administrator, db, administrator["X-Workspace-Id"])["auth_context"]
    bind_data_scope(actor, "extraction.execute")
    original = db.info["resource_access_criteria"]
    with extraction._knowledge_scope(actor):
        outer = db.info["resource_access_criteria"]
        assert [row.id for row in extraction._load_chunks(actor, SimpleNamespace(knowledge_base_id=kb_id, document_ids=[]))] == [chunk["id"]]
        assert db.info["resource_access_criteria"] is outer
    assert db.info["resource_access_criteria"] is original
