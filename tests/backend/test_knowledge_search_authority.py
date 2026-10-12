"""用真实隔离 SQLite 和内存索引桩验证当前知识正文及授权是检索唯一依据。"""
from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import File, KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge import governance, service


class MemoryIndex:
    """可控制写入失败及残留候选的索引桩，绝不连接 Elasticsearch。"""

    def __init__(self):
        self.documents = {}
        self.fail_writes = False
        self.candidates = None

    @property
    def indices(self):
        return self

    def index(self, *, id, document, **kwargs):
        if self.fail_writes:
            raise RuntimeError("隔离索引写入失败")
        self.documents[id] = deepcopy(document)
        return {"_id": id}

    def refresh(self, **kwargs):
        return {}

    def delete_by_query(self, **kwargs):
        # 故意保留残留文档，证明查询不能依赖外部删除成功。
        return {"deleted": 0}

    def search(self, **kwargs):
        sources = self.candidates if self.candidates is not None else list(self.documents.values())
        return {"hits": {"hits": [{"_source": deepcopy(source), "_score": 4.5} for source in sources]}}


@pytest.fixture
def index(monkeypatch):
    selected = MemoryIndex()
    monkeypatch.setattr(service, "_es_client", lambda: selected)
    monkeypatch.setattr(service, "_effective_model_names", lambda _workspace: ("isolated", "none"))
    monkeypatch.setattr("app.providers.model._embedding_config", lambda name, **kwargs:
                        (name, {"model": "isolated", "provider": "offline"}))
    vectors = lambda texts, **kwargs: [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(service, "embed_texts", vectors)
    monkeypatch.setattr(governance, "embed_texts", vectors)
    return selected


def create_segment(client, headers, text="设备温度上限为 80 摄氏度", kb_id=None):
    """通过真实 HTTP 服务创建临时知识库与分段，保持权限及事务路径。"""
    if kb_id is None:
        response = client.post("/api/knowledge", headers=headers, json={"name": "当前知识依据"})
        assert response.status_code == 201, response.text
        kb_id = response.json()["id"]
    response = client.post(f"/api/knowledge/{kb_id}/segments", headers=headers, json={"text": text})
    assert response.status_code == 201, response.text
    return kb_id, response.json()


@pytest.mark.parametrize("failure", ["embedding", "index"])
def test_updated_segment_never_returns_or_reranks_old_index_content(
    client, administrator, index, monkeypatch, failure,
):
    kb_id, chunk = create_segment(client, administrator)
    if failure == "embedding":
        def unavailable(*args, **kwargs):
            raise RuntimeError("隔离向量故障")
        monkeypatch.setattr(governance, "embed_texts", unavailable)
    else:
        index.fail_writes = True
    response = client.patch(f"/api/knowledge/{kb_id}/segments/{chunk['id']}", headers=administrator,
        json={"version": chunk["version"], "segment_type": "text", "text": "设备温度上限为 70 摄氏度",
              "question": "", "answer": "", "enabled": True})
    assert response.status_code == 200, response.text
    sent = []

    def capture(_db, _workspace, _query, ranked):
        sent.extend(item["src"]["content"] for item in ranked)
        return ranked, "disabled"

    monkeypatch.setattr("app.modules.knowledge.reranking.rerank", capture)
    result = client.post("/api/knowledge/search", headers=administrator,
        json={"query": "设备温度上限", "knowledge_base_ids": [kb_id], "mode": "keyword"})
    assert result.status_code == 200, result.text
    assert [item["content"] for item in result.json()["items"]] == ["设备温度上限为 70 摄氏度"]
    assert sent == ["设备温度上限为 70 摄氏度"]


def test_deleted_index_candidate_does_not_hide_authorized_database_match(client, administrator, index):
    kb_id, old = create_segment(client, administrator)
    _kb_id, current = create_segment(client, administrator, "设备温度检查使用当前标准", kb_id)
    index.candidates = [deepcopy(index.documents[old["id"]])]
    removed = client.delete(f"/api/knowledge/{kb_id}/segments/{old['id']}", headers=administrator)
    assert removed.status_code == 200, removed.text
    result = client.post("/api/knowledge/search", headers=administrator,
        json={"query": "设备温度", "knowledge_base_ids": [kb_id], "mode": "keyword"})
    assert result.status_code == 200, result.text
    assert [item["chunk_id"] for item in result.json()["items"]] == [current["id"]]


def test_result_reference_is_rebuilt_from_current_authorized_sql_rows(client, administrator, index, db):
    kb_id, segment = create_segment(client, administrator)
    chunk = db.get(KnowledgeChunk, segment["id"])
    document = db.get(KnowledgeDocument, chunk.document_id)
    source = db.get(File, document.file_id)
    source.name = "当前来源名称.txt"
    chunk.location = {"source": "manual", "ordinal": 0, "page": 2}
    db.commit()
    # 保持可用正文，故意令索引的引用指向不存在的其他资源，输出必须以 SQL 关系为准。
    index.documents[chunk.id].update(file_id="other-file", knowledge_base_id="other-kb",
        document_version_id="other-document", file_name="旧来源.txt", location={"page": 99})
    result = service.search(db, workspace_id=administrator["X-Workspace-Id"],
        query="设备温度", knowledge_base_ids=[kb_id], top_k=1, mode="keyword")
    assert len(result) == 1
    assert result[0]["file_id"] == source.id
    assert result[0]["file_name"] == source.name
    assert result[0]["knowledge_base_id"] == kb_id
    assert result[0]["document_id"] == document.id
    assert result[0]["location"] == chunk.location


def test_semantic_search_does_not_reuse_vector_for_changed_text(client, administrator, index, db):
    kb_id, segment = create_segment(client, administrator)
    chunk = db.get(KnowledgeChunk, segment["id"])
    chunk.text = "当前完全不同的检修要求"
    db.commit()
    assert service.search(db, workspace_id=administrator["X-Workspace-Id"], query="温度上限",
        knowledge_base_ids=[kb_id], mode="semantic") == []


def test_ready_index_match_keeps_score_without_unnecessary_database_scan(
    client, administrator, index, db, monkeypatch,
):
    kb_id, segment = create_segment(client, administrator)
    monkeypatch.setattr(service, "_db_keyword_hits", lambda *args, **kwargs: pytest.fail("充足有效候选不应全库回退"))
    result = service.search(db, workspace_id=administrator["X-Workspace-Id"],
        query="设备温度", knowledge_base_ids=[kb_id], top_k=1, mode="keyword")
    assert [item["chunk_id"] for item in result] == [segment["id"]]
    assert result[0]["score"] == 4.5


def test_processing_rebuild_preserves_existing_keyword_content(client, administrator, index, db):
    kb_id, segment = create_segment(client, administrator)
    chunk = db.get(KnowledgeChunk, segment["id"])
    document = db.get(KnowledgeDocument, chunk.document_id)
    document.status = "processing"
    db.commit()
    index.candidates = []
    result = service.search(db, workspace_id=administrator["X-Workspace-Id"], query="设备温度",
        knowledge_base_ids=[kb_id], mode="keyword")
    assert [item["chunk_id"] for item in result] == [segment["id"]]


def test_manual_segment_ordinals_advance_from_zero(client, administrator, index, db):
    kb_id, first = create_segment(client, administrator)
    _, second = create_segment(client, administrator, "第二条检查方法", kb_id)
    _, third = create_segment(client, administrator, "第三条处置要求", kb_id)
    rows = [db.get(KnowledgeChunk, item["id"]) for item in (first, second, third)]
    assert [row.ordinal for row in rows] == [0, 1, 2]
    assert [row.location["ordinal"] for row in rows] == [0, 1, 2]


def test_deleted_file_document_does_not_offer_broken_download(client, administrator, index, db):
    kb_id, segment = create_segment(client, administrator)
    chunk = db.get(KnowledgeChunk, segment["id"])
    document = db.get(KnowledgeDocument, chunk.document_id)
    response = client.delete(f"/api/files/{document.file_id}", headers=administrator)
    assert response.status_code == 200, response.text
    response = client.get(f"/api/knowledge/{kb_id}/documents", headers=administrator)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["download_url"] is None
    assert "已删除" in response.json()["items"][0]["name"]


def test_revoked_knowledge_candidate_cannot_block_other_authorized_match(client, administrator, index):
    role = client.post("/api/v1/system/roles", headers=administrator, json={"code": "current-knowledge-reader",
        "name": "当前知识阅读者", "permissions": ["home.view", "knowledge.view"], "data_scope": "self"})
    assert role.status_code == 201, role.text
    user = client.post("/api/v1/system/users", headers=administrator, json={"username": "current-knowledge-reader",
        "display_name": "当前知识阅读者", "password": "Temporary12345", "role_ids": [role.json()["id"]]})
    assert user.status_code == 201, user.text
    login = client.post("/api/auth/login", json={"username": "current-knowledge-reader", "password": "Temporary12345"})
    assert login.status_code == 200, login.text
    reader = {"Authorization": "Bearer " + login.json()["token"]}
    changed = client.post("/api/auth/password", headers=reader,
        json={"current_password": "Temporary12345", "new_password": "ChangedPassword12345"})
    assert changed.status_code == 200, changed.text
    login = client.post("/api/auth/login", json={"username": "current-knowledge-reader", "password": "ChangedPassword12345"})
    reader = {"Authorization": "Bearer " + login.json()["token"]}
    old_kb, old = create_segment(client, administrator, "设备温度：撤权前的内部标准")
    current_kb, current = create_segment(client, administrator, "设备温度：当前允许的标准")
    grants = []
    for kb_id in (old_kb, current_kb):
        granted = client.post(f"/api/knowledge/{kb_id}/role-grants", headers=administrator,
            json={"role_id": role.json()["id"], "actions": ["view"]})
        assert granted.status_code == 201, granted.text
        grants.append(granted.json()["id"])
    index.candidates = [deepcopy(index.documents[old["id"]])]
    revoked = client.delete(f"/api/knowledge/{old_kb}/role-grants/{grants[0]}", headers=administrator)
    assert revoked.status_code == 200, revoked.text
    result = client.post("/api/knowledge/search", headers=reader, json={"query": "设备温度", "mode": "keyword"})
    assert result.status_code == 200, result.text
    assert [item["chunk_id"] for item in result.json()["items"]] == [current["id"]]
    # 知识库分段授权并没有让原始文件下载权限随之放大。
    file_id = index.documents[current["id"]]["file_id"]
    assert client.get(f"/api/files/{file_id}/download", headers=reader).status_code == 403
