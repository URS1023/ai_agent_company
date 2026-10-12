"""File governance behavior: category trees, immutable revisions and parse evidence.

中文说明：文件治理：分类树拒绝重复/成环/非空删除；批量上传的筛选、元数据、导出与原子上限；
内容版本可恢复、可预览、整链软删除；历史生成的二进制文件保持向后兼容。
隔离方式：monkeypatch + tmp_path + client 夹具（内存 SQLite），不连业务库。
"""
from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.models import File


def _category(client, headers, name: str, parent_id: str | None = None) -> dict:
    response = client.post("/api/files/categories", headers=headers, json={
        "name": name,
        "parent_id": parent_id,
        "description": "验收分类",
        "sort_order": 10,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, headers, name: str, content: str, category_id: str | None = None) -> dict:
    response = client.post(
        "/api/files",
        headers=headers,
        data={"category_id": category_id or "", "description": "现场资料", "tags": "设备, 维护"},
        files={"file": (name, content.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_category_tree_rejects_duplicates_cycles_and_nonempty_deletion(
    client, administrator, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "files"))
    root = _category(client, administrator, "设备资料")
    child = _category(client, administrator, "维护规程", root["id"])

    duplicate = client.post("/api/files/categories", headers=administrator, json={
        "name": "维护规程", "parent_id": root["id"],
    })
    assert duplicate.status_code == 409, duplicate.text

    cycle = client.patch("/api/files/categories/" + root["id"], headers=administrator, json={
        "version": root["version"],
        "name": root["name"],
        "parent_id": child["id"],
        "description": root["description"],
        "sort_order": root["sort_order"],
    })
    assert cycle.status_code == 422, cycle.text
    assert client.delete(
        "/api/files/categories/" + root["id"],
        params={"version": root["version"]},
        headers=administrator,
    ).status_code == 409

    uploaded = _upload(client, administrator, "巡检规程.txt", "每日检查轴承温度。", child["id"])
    categories = client.get("/api/files/categories", headers=administrator).json()["items"]
    indexed = {item["id"]: item for item in categories}
    assert indexed[root["id"]]["child_count"] == 1
    assert indexed[child["id"]]["file_count"] == 1
    assert client.delete(
        "/api/files/categories/" + child["id"],
        params={"version": child["version"]},
        headers=administrator,
    ).status_code == 409

    deleted = client.delete("/api/files/" + uploaded["id"], headers=administrator)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_versions"] == 1
    assert client.delete(
        "/api/files/categories/" + child["id"],
        params={"version": child["version"]},
        headers=administrator,
    ).status_code == 200
    assert client.delete(
        "/api/files/categories/" + root["id"],
        params={"version": root["version"]},
        headers=administrator,
    ).status_code == 200


def test_batch_upload_filter_metadata_export_and_atomic_limit(
    client, administrator, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "files"))
    category = _category(client, administrator, "财务资料")
    response = client.post(
        "/api/files/batch",
        headers=administrator,
        data={"category_id": category["id"], "description": "月度财务", "tags": "财务,月报"},
        files=[
            ("files", ("一月.txt", "收入 100".encode("utf-8"), "text/plain")),
            ("files", ("二月.txt", "收入 120".encode("utf-8"), "text/plain")),
        ],
    )
    assert response.status_code == 201, response.text
    assert response.json()["count"] == 2

    listed = client.get(
        "/api/files", headers=administrator, params={"category_id": category["id"], "q": "月"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 2
    first = listed.json()["items"][0]
    updated = client.patch("/api/files/" + first["id"] + "/metadata", headers=administrator, json={
        "version": first["metadata_version"],
        "category_id": category["id"],
        "description": "已复核月报",
        "tags": ["财务", "已复核"],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "已复核月报"
    assert updated.json()["tags"] == ["财务", "已复核"]

    exported = client.get("/api/files/export", headers=administrator)
    assert exported.status_code == 200, exported.text
    assert "文件ID" in exported.content.decode("utf-8-sig")
    assert "已复核" in exported.content.decode("utf-8-sig")

    before = client.get("/api/files", headers=administrator).json()["total"]
    too_many = client.post(
        "/api/files/batch",
        headers=administrator,
        files=[
            ("files", (f"{index}.txt", b"x", "text/plain"))
            for index in range(21)
        ],
    )
    assert too_many.status_code == 422, too_many.text
    assert client.get("/api/files", headers=administrator).json()["total"] == before


def test_content_versions_restore_preview_and_soft_delete_entire_chain(
    client, administrator, db, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "files"))
    original = _upload(client, administrator, "设备状态.txt", "温度 40")
    first_id = original["id"]

    second = client.post(
        f"/api/files/{first_id}/versions",
        headers=administrator,
        data={"change_note": "更新采集值"},
        files={"file": ("设备状态.txt", "温度 80".encode("utf-8"), "text/plain")},
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert second.json()["revision_number"] == 2
    assert client.get(f"/api/files/{first_id}/download", headers=administrator).text == "温度 40"
    assert client.get(f"/api/files/{second_id}/download", headers=administrator).text == "温度 80"

    listed = client.get("/api/files", headers=administrator).json()["items"]
    assert [item["id"] for item in listed] == [second_id]
    versions = client.get(f"/api/files/{first_id}/versions", headers=administrator)
    assert versions.status_code == 200, versions.text
    assert [item["revision_number"] for item in versions.json()["items"]] == [2, 1]
    assert [item["current"] for item in versions.json()["items"]] == [True, False]

    restored = client.post(
        f"/api/files/{second_id}/versions/1/restore", headers=administrator,
    )
    assert restored.status_code == 201, restored.text
    restored_id = restored.json()["id"]
    assert restored.json()["revision_number"] == 3
    assert client.get(f"/api/files/{restored_id}/download", headers=administrator).text == "温度 40"

    preview = client.get(f"/api/files/{restored_id}/text", headers=administrator)
    assert preview.status_code == 200, preview.text
    assert "温度 40" in preview.json()["text"]
    events = client.get(f"/api/files/{first_id}/parse-events", headers=administrator)
    assert events.status_code == 200, events.text
    assert events.json()["count"] == 1
    assert events.json()["items"][0]["status"] == "completed"

    deleted = client.delete(f"/api/files/{first_id}", headers=administrator)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_versions"] == 3
    assert client.get("/api/files", headers=administrator).json()["total"] == 0
    db.expire_all()
    physical = db.scalars(select(File)).all()
    assert len(physical) == 3
    assert all(row.deleted_at is not None and row.status == "deleted" for row in physical)


def test_generated_binary_file_remains_backward_compatible(
    client, administrator, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "files"))
    response = client.post(
        "/api/files",
        headers=administrator,
        files={"file": ("artifact.bin", b"\x00\x01", "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["mime_type"] == "application/octet-stream"
