"""Duplicate imports and provider outages preserve the last usable knowledge.

中文说明：重复导入与向量化提供方故障时，要保留上一版可用知识（不能把索引清空）。
隔离方式：monkeypatch + tmp_path + client 夹具（内存 SQLite），不连业务库。
"""
from __future__ import annotations

from app.config import settings
from app.modules.knowledge import service
from app.modules.knowledge.models import KnowledgeChunkMetadata
from app.models import KnowledgeChunk
from sqlalchemy import select


def _setup(client, administrator, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(service, "embed_texts", lambda texts, **kwargs: [[1.0] * 8 for _ in texts])
    monkeypatch.setattr(service, "_index_chunk_es", lambda *args: None)
    monkeypatch.setattr(service, "_refresh_index", lambda: None)
    monkeypatch.setattr(service, "_delete_chunks_from_es", lambda _ids: None)
    uploaded = client.post("/api/files", headers=administrator, files={
        "file": ("设备知识.txt", "设备温度上限为八十度。".encode(), "text/plain"),
    })
    assert uploaded.status_code == 201, uploaded.text
    kb = client.post("/api/knowledge", headers=administrator, json={"name": "设备知识"}).json()
    return kb["id"], uploaded.json()["id"]


def _import(client, headers, kb_id, file_id):
    return client.post("/api/knowledge/documents", headers=headers, json={"kb_id": kb_id, "file_id": file_id})


def test_repeated_import_reuses_ready_document_without_reembedding(client, administrator, monkeypatch, tmp_path):
    kb_id, file_id = _setup(client, administrator, monkeypatch, tmp_path)
    first = _import(client, administrator, kb_id, file_id)
    assert first.status_code == 201, first.text
    monkeypatch.setattr(service, "embed_texts", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    second = _import(client, administrator, kb_id, file_id)
    assert second.status_code == 201, second.text
    assert second.json()["document_id"] == first.json()["document_id"]
    docs = client.get(f"/api/knowledge/{kb_id}/documents", headers=administrator).json()["items"]
    assert len(docs) == 1 and docs[0]["chunk_count"] == 1


def test_failed_reindex_preserves_document_chunks_and_allows_retry(client, administrator, monkeypatch, tmp_path):
    kb_id, file_id = _setup(client, administrator, monkeypatch, tmp_path)
    doc_id = _import(client, administrator, kb_id, file_id).json()["document_id"]
    monkeypatch.setattr(service, "embed_texts", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    failed = client.post(f"/api/knowledge/{kb_id}/documents/{doc_id}/reindex", headers=administrator, json={})
    assert failed.status_code >= 400
    docs = client.get(f"/api/knowledge/{kb_id}/documents", headers=administrator).json()["items"]
    assert len(docs) == 1 and docs[0]["id"] == doc_id
    assert docs[0]["status"] == "ready" and docs[0]["chunk_count"] == 1
    assert "保留" in docs[0]["error_summary"]
    monkeypatch.setattr(service, "embed_texts", lambda texts, **kwargs: [[1.0] * 8 for _ in texts])
    retried = client.post(f"/api/knowledge/{kb_id}/documents/{doc_id}/reindex", headers=administrator, json={})
    assert retried.status_code == 200, retried.text
    assert retried.json()["document_id"] == doc_id


def test_sync_retries_failed_import_without_reindex_checkbox(client, administrator, monkeypatch, tmp_path):
    kb_id, file_id = _setup(client, administrator, monkeypatch, tmp_path)
    monkeypatch.setattr(service, "embed_texts", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    first = client.post(f"/api/knowledge/{kb_id}/sync", headers=administrator, json={"file_ids": [file_id]})
    assert first.status_code == 200, first.text
    assert first.json()["failed_count"] == 1
    monkeypatch.setattr(service, "embed_texts", lambda texts, **kwargs: [[1.0] * 8 for _ in texts])
    retry = client.post(f"/api/knowledge/{kb_id}/sync", headers=administrator, json={"file_ids": [file_id]})
    assert retry.status_code == 200, retry.text
    assert retry.json()["imported_count"] == 1 and retry.json()["skipped_count"] == 0


def test_search_rejects_disabled_or_deleted_chunks_from_stale_external_index(
    client, administrator, monkeypatch, tmp_path, db,
):
    kb_id, file_id = _setup(client, administrator, monkeypatch, tmp_path)
    document_id = _import(client, administrator, kb_id, file_id).json()["document_id"]
    chunk = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
    source = {
        "chunk_id": chunk.id, "content": chunk.text, "file_id": file_id,
        "file_name": "设备知识.txt", "knowledge_base_id": kb_id,
        "document_version_id": document_id,
    }

    class StaleIndex:
        def search(self, **kwargs):
            return {"hits": {"hits": [{"_source": source, "_score": 1.0}]}}

    monkeypatch.setattr(service, "_es_client", StaleIndex)
    assert service.search(db, workspace_id=administrator["X-Workspace-Id"], query="设备温度", mode="keyword")
    db.add(KnowledgeChunkMetadata(
        workspace_id=administrator["X-Workspace-Id"], knowledge_base_id=kb_id,
        chunk_id=chunk.id, enabled=False, updated_by_user_id="test-actor",
    ))
    db.commit()
    assert service.search(db, workspace_id=administrator["X-Workspace-Id"], query="设备温度", mode="keyword") == []
    client.delete(f"/api/knowledge/{kb_id}/documents/{document_id}", headers=administrator)
    db.expire_all()
    assert service.search(db, workspace_id=administrator["X-Workspace-Id"], query="设备温度", mode="keyword") == []
