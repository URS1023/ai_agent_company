"""Knowledge settings, segment maintenance, synchronization, logs and role scope.

中文说明：知识库治理：设置项、分块维护、同步、日志与角色范围（用桩索引模拟检索不可用）。
隔离方式：monkeypatch + tmp_path + client 夹具（内存 SQLite），不连业务库、不调用嵌入模型。
边界：重置/重建索引需保留可用性并在最终检查点之外停止工作。
"""
from __future__ import annotations

import csv
import io

from app.config import settings
from app.modules.knowledge import governance, service


class UnavailableSearchIndex:
    @property
    def indices(self):
        return self

    def index(self, **_kwargs):
        raise RuntimeError("search index unavailable")

    def refresh(self, **_kwargs):
        raise RuntimeError("search index unavailable")

    def search(self, **_kwargs):
        raise RuntimeError("search index unavailable")

    def delete_by_query(self, **_kwargs):
        return {"deleted": 0}


def _offline_knowledge(monkeypatch):
    # Stub profile resolution together with the embedding call. Opening another
    # Session on the fixture's single in-memory connection would roll back the
    # caller's pending segment transaction, unlike an independent DB connection.
    monkeypatch.setattr("app.providers.model._embedding_config", lambda profile, **kwargs:
                        (profile or "embedding", {"model": "offline-embedding", "provider": "offline"}))
    monkeypatch.setattr(service, "_es_client", lambda: UnavailableSearchIndex())
    monkeypatch.setattr(
        service, "embed_texts",
        lambda texts, **_kwargs: [[float(index == 0) for index in range(8)] for _text in texts],
    )
    monkeypatch.setattr(
        governance, "embed_texts",
        lambda texts, **_kwargs: [[float(index == 0) for index in range(8)] for _text in texts],
    )


def _kb(client, headers, name="设备知识库") -> dict:
    response = client.post("/api/knowledge", headers=headers, json={
        "name": name, "description": "本机治理验收",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _activate_user(client, username: str) -> dict:
    first = client.post("/api/auth/login", json={
        "username": username, "password": "Temporary12345",
    })
    assert first.status_code == 200, first.text
    changed = client.post("/api/auth/password", headers={
        "Authorization": "Bearer " + first.json()["token"],
    }, json={
        "current_password": "Temporary12345",
        "new_password": "ChangedPassword12345",
    })
    assert changed.status_code == 200, changed.text
    login = client.post("/api/auth/login", json={
        "username": username, "password": "ChangedPassword12345",
    })
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["token"]}


def test_retrieval_settings_use_optimistic_concurrency(client, administrator, db):
    from datetime import datetime
    from app.ids import new_id
    from app.models import ModelProfile
    now = datetime.utcnow()
    db.add(ModelProfile(id=new_id(), workspace_id=administrator["X-Workspace-Id"],
                        profile_name="local-reranker", provider="openai-compatible", model="offline-reranker",
                        endpoint="http://127.0.0.1:1", enabled=True, status="active",
                        capabilities_json={"capabilities": ["rerank"]}, created_at=now, updated_at=now))
    db.commit()
    kb = _kb(client, administrator)
    settings_response = client.get(f"/api/knowledge/{kb['id']}/settings", headers=administrator)
    assert settings_response.status_code == 200, settings_response.text
    current = settings_response.json()
    updated = client.patch(f"/api/knowledge/{kb['id']}/settings", headers=administrator, json={
        "version": current["version"],
        "retrieval_mode": "keyword",
        "top_k": 9,
        "min_score": 0.2,
        "chunk_size": 700,
        "overlap": 100,
        "embedding_model": "local-embedding",
        "rerank_enabled": True,
        "rerank_model": "local-reranker",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["retrieval_mode"] == "keyword"
    assert updated.json()["top_k"] == 9
    assert updated.json()["version"] > current["version"]
    stale = client.patch(f"/api/knowledge/{kb['id']}/settings", headers=administrator, json={
        **updated.json(), "version": current["version"],
    })
    assert stale.status_code == 409, stale.text
    invalid = client.patch(f"/api/knowledge/{kb['id']}/settings", headers=administrator, json={
        **updated.json(), "overlap": 700,
    })
    assert invalid.status_code == 422, invalid.text


def test_text_and_qa_segments_import_export_search_and_recall_logs(
    client, administrator, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "knowledge"))
    _offline_knowledge(monkeypatch)
    kb = _kb(client, administrator)

    created = client.post(f"/api/knowledge/{kb['id']}/segments", headers=administrator, json={
        "segment_type": "text", "text": "轴承温度上限为 80 摄氏度", "enabled": True,
    })
    assert created.status_code == 201, created.text
    text_segment = created.json()
    batch = client.post(f"/api/knowledge/{kb['id']}/segments/batch", headers=administrator, json={
        "items": [
            {"segment_type": "qa", "question": "振动超限怎么办？", "answer": "立即停机并复核传感器。"},
            {"segment_type": "text", "text": "润滑周期为每三十天一次。"},
        ],
    })
    assert batch.status_code == 201, batch.text
    assert batch.json()["count"] == 2

    listed = client.get(f"/api/knowledge/{kb['id']}/segments", headers=administrator)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 3
    assert {item["segment_type"] for item in listed.json()["items"]} == {"text", "qa"}
    assert client.get("/api/files", headers=administrator).json()["total"] == 0

    disabled = client.patch(
        f"/api/knowledge/{kb['id']}/segments/{text_segment['id']}",
        headers=administrator,
        json={
            "version": text_segment["version"], "segment_type": "text",
            "text": "轴承温度上限为 85 摄氏度", "enabled": False,
            "question": "", "answer": "",
        },
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert disabled.json()["version"] > text_segment["version"]

    hidden = client.post("/api/knowledge/search", headers=administrator, json={
        "query": "轴承温度上限", "knowledge_base_ids": [kb["id"]], "mode": "keyword",
    })
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["count"] == 0
    recalled = client.post("/api/knowledge/search", headers=administrator, json={
        "query": "振动超限", "knowledge_base_ids": [kb["id"]], "mode": "keyword",
    })
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["count"] == 1
    assert "立即停机" in recalled.json()["items"][0]["content"]
    logs = client.get("/api/knowledge/recall-logs", headers=administrator, params={
        "knowledge_base_id": kb["id"],
    })
    assert logs.status_code == 200, logs.text
    assert logs.json()["count"] == 2
    assert logs.json()["items"][0]["hit_count"] == 1

    csv_stream = io.StringIO(newline="")
    writer = csv.DictWriter(csv_stream, fieldnames=["segment_type", "text", "question", "answer", "enabled"])
    writer.writeheader()
    writer.writerow({"segment_type": "text", "text": "导入的保养记录", "question": "", "answer": "", "enabled": "true"})
    imported = client.post(
        f"/api/knowledge/{kb['id']}/segments/import",
        headers=administrator,
        files={"file": ("segments.csv", csv_stream.getvalue().encode("utf-8"), "text/csv")},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["count"] == 1
    exported = client.get(f"/api/knowledge/{kb['id']}/segments/export", headers=administrator)
    assert exported.status_code == 200, exported.text
    exported_text = exported.content.decode("utf-8-sig")
    assert "分段ID" in exported_text
    assert "导入的保养记录" in exported_text

    deleted = client.delete(
        f"/api/knowledge/{kb['id']}/segments/{text_segment['id']}", headers=administrator,
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/knowledge/{kb['id']}/segments", headers=administrator).json()["total"] == 3


def test_file_category_sync_tracks_import_skip_and_reindex(
    client, administrator, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "knowledge"))
    _offline_knowledge(monkeypatch)
    category = client.post("/api/files/categories", headers=administrator, json={"name": "待同步"}).json()
    for name, content in (("设备甲.txt", "设备甲额定温度 70"), ("设备乙.txt", "设备乙额定温度 75")):
        uploaded = client.post(
            "/api/files", headers=administrator,
            data={"category_id": category["id"]},
            files={"file": (name, content.encode("utf-8"), "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
    kb = _kb(client, administrator, "同步知识库")

    first = client.post(f"/api/knowledge/{kb['id']}/sync", headers=administrator, json={
        "category_id": category["id"], "reindex": False,
    })
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["imported_count"] == 2
    assert client.get(f"/api/knowledge/{kb['id']}/documents", headers=administrator).json()["count"] == 2

    second = client.post(f"/api/knowledge/{kb['id']}/sync", headers=administrator, json={
        "category_id": category["id"], "reindex": False,
    })
    assert second.status_code == 200, second.text
    assert second.json()["skipped_count"] == 2
    rebuilt = client.post(f"/api/knowledge/{kb['id']}/sync", headers=administrator, json={
        "category_id": category["id"], "reindex": True,
    })
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["imported_count"] == 2
    runs = client.get(f"/api/knowledge/{kb['id']}/sync-runs", headers=administrator)
    assert runs.status_code == 200, runs.text
    assert runs.json()["count"] == 3


def test_role_grant_limits_list_search_and_does_not_grant_file_download(
    client, administrator, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "knowledge"))
    _offline_knowledge(monkeypatch)
    role = client.post("/api/v1/system/roles", headers=administrator, json={
        "code": "knowledge-reader", "name": "知识阅读者",
        "permissions": ["home.view", "knowledge.view"], "data_scope": "self",
    })
    assert role.status_code == 201, role.text
    user = client.post("/api/v1/system/users", headers=administrator, json={
        "username": "knowledge-reader", "display_name": "知识阅读者",
        "password": "Temporary12345", "role_ids": [role.json()["id"]],
    })
    assert user.status_code == 201, user.text
    reader = _activate_user(client, "knowledge-reader")

    file_response = client.post(
        "/api/files", headers=administrator,
        files={"file": ("保密规程.txt", "紧急停机后联系值班主管。".encode("utf-8"), "text/plain")},
    )
    assert file_response.status_code == 201, file_response.text
    kb = _kb(client, administrator, "受控知识库")
    indexed = client.post("/api/knowledge/documents", headers=administrator, json={
        "kb_id": kb["id"], "file_id": file_response.json()["id"], "chunk_size": 500, "overlap": 80,
    })
    assert indexed.status_code == 201, indexed.text

    assert client.get("/api/knowledge", headers=reader).json()["count"] == 0
    no_access = client.post("/api/knowledge/search", headers=reader, json={"query": "紧急停机", "mode": "keyword"})
    assert no_access.status_code == 200, no_access.text
    assert no_access.json()["count"] == 0

    granted = client.post(f"/api/knowledge/{kb['id']}/role-grants", headers=administrator, json={
        "role_id": role.json()["id"], "actions": ["view"],
    })
    assert granted.status_code == 201, granted.text
    assert client.get("/api/knowledge", headers=reader).json()["count"] == 1
    result = client.post("/api/knowledge/search", headers=reader, json={"query": "紧急停机", "mode": "keyword"})
    assert result.status_code == 200, result.text
    assert result.json()["count"] == 1
    assert "值班主管" in result.json()["items"][0]["content"]
    assert client.get(
        f"/api/files/{file_response.json()['id']}/download", headers=reader,
    ).status_code == 403

    grant_rows = client.get(f"/api/knowledge/{kb['id']}/role-grants", headers=administrator)
    assert grant_rows.status_code == 200, grant_rows.text
    assert grant_rows.json()["count"] == 1
    revoked = client.delete(
        f"/api/knowledge/{kb['id']}/role-grants/{granted.json()['id']}", headers=administrator,
    )
    assert revoked.status_code == 200, revoked.text
    assert client.get("/api/knowledge", headers=reader).json()["count"] == 0
