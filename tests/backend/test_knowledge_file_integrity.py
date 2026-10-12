"""An unsupported upload stays downloadable, but never becomes indexed text.

中文说明：不受支持格式的上传保持可下载，但绝不进入索引文本：旧版 Office 文件在向量化前就失败。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连业务库。
边界：失败要在嵌入调用之前发生，避免产生半索引文档。
"""
import pytest
from sqlalchemy import select

from app.models import KnowledgeChunk
from app.modules.knowledge import service


@pytest.mark.parametrize("extension", ["doc", "xls", "ppt"])
def test_legacy_office_import_fails_before_embedding(client, administrator, monkeypatch, db, extension):
    def unexpected(*args, **kwargs):
        pytest.fail("Unparsed binary content reached the embedding provider")
    monkeypatch.setattr(service, "embed_texts", unexpected)
    content = bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 32
    uploaded = client.post("/api/files", headers=administrator, files={
        "file": ("legacy." + extension, content, "application/octet-stream"),
    })
    assert uploaded.status_code == 201, uploaded.text
    downloaded = client.get(f"/api/files/{uploaded.json()['id']}/download", headers=administrator)
    assert downloaded.status_code == 200 and downloaded.content == content
    kb = client.post("/api/knowledge", headers=administrator, json={"name": "格式完整性"}).json()
    imported = client.post("/api/knowledge/documents", headers=administrator, json={
        "kb_id": kb["id"], "file_id": uploaded.json()["id"],
    })
    assert imported.status_code == 422 and "另存为" in imported.text
    docs = client.get(f"/api/knowledge/{kb['id']}/documents", headers=administrator).json()["items"]
    assert len(docs) == 1 and docs[0]["status"] == "failed" and docs[0]["chunk_count"] == 0
    assert not db.scalars(select(KnowledgeChunk)).all()
