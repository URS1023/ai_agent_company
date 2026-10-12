"""Knowledge ingestion remains usable when the optional search index is down.

中文说明：当可选检索索引不可用时，知识入库仍要退化为数据库关键词检索而不是整体失败。
隔离方式：monkeypatch + tmp_path + client 夹具（内存 SQLite），用 UnavailableSearchIndex 桩模拟索引故障。
边界：降级必须可用且有明确状态，不允许静默返回空结果。
"""
from __future__ import annotations

from app.config import settings
from app.modules.knowledge import service


class UnavailableSearchIndex:
    """Small Elasticsearch double that reproduces a read-only/unavailable node."""

    @property
    def indices(self):
        return self

    def index(self, **_kwargs):
        raise RuntimeError("cluster_block_exception: index is read-only")

    def refresh(self, **_kwargs):
        raise RuntimeError("search index unavailable")

    def search(self, **_kwargs):
        raise RuntimeError("search index unavailable")


def test_ingestion_degrades_to_database_keyword_search(
    client,
    administrator,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "knowledge-files"))
    monkeypatch.setattr(
        service,
        "embed_texts",
        lambda texts, **_kwargs: [
            [1.0 if dimension == index % 8 else 0.0 for dimension in range(1024)]
            for index, _text in enumerate(texts)
        ],
    )
    unavailable = UnavailableSearchIndex()
    monkeypatch.setattr(service, "_es_client", lambda: unavailable)

    uploaded = client.post(
        "/api/files",
        headers=administrator,
        files={
            "file": (
                "投标说明.txt",
                "本项目的投标保证金为五十万元，应在开标前到账。".encode("utf-8"),
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text

    created = client.post(
        "/api/knowledge",
        headers=administrator,
        json={"name": "本机降级检索", "description": "验证搜索索引异常时仍可使用"},
    )
    assert created.status_code == 201, created.text
    knowledge_base_id = created.json()["id"]

    ingested = client.post(
        "/api/knowledge/documents",
        headers=administrator,
        json={
            "kb_id": knowledge_base_id,
            "file_id": uploaded.json()["id"],
            "chunk_size": 500,
            "overlap": 80,
        },
    )
    assert ingested.status_code == 201, ingested.text
    assert ingested.json()["status"] == "ready"

    documents = client.get(
        f"/api/knowledge/{knowledge_base_id}/documents",
        headers=administrator,
    )
    assert documents.status_code == 200, documents.text
    document = documents.json()["items"][0]
    assert document["status"] == "ready"
    assert document["chunk_count"] == 1
    assert "数据库" in document["error_summary"]
    assert "关键词回退检索" in document["error_summary"]

    searched = client.post(
        "/api/knowledge/search",
        headers=administrator,
        json={
            "query": "投标保证金是多少",
            "knowledge_base_ids": [knowledge_base_id],
            "top_k": 3,
            "mode": "keyword",
        },
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["count"] == 1
    hit = searched.json()["items"][0]
    assert hit["file_name"] == "投标说明.txt"
    assert "五十万元" in hit["content"]
    assert hit["retrieval_mode"] == "keyword"
