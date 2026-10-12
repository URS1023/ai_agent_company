"""Generated files become authenticated chat artifacts, never browser-local paths.

中文说明：生成文件必须以"需鉴权的产物"形式在对话中呈现，绝不能暴露沙箱本地路径；
markdown 里的内部路径要换成预览/下载路由；图片以内联真实 MIME 返回并带工作区查询参数。
隔离方式：client 夹具（内存 SQLite + TestClient）+ tmp_path，不连业务库与外部模型。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import Artifact, User, Workspace
from app.modules.artifact.service import create_artifact
from app.modules.runtime.agent import (
    _persist_result_artifacts,
    _rewrite_artifact_references,
)
from app.modules.runtime.service import create_run
from app.providers.tool.base import ToolResult


def _actor(db):
    return (
        db.scalar(select(Workspace)),
        db.scalar(select(User)),
    )


def test_sandbox_artifact_is_publicized_and_internal_stdout_is_removed(
    client, administrator, db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    workspace, user = _actor(db)
    run = create_run(
        db,
        workspace_id=workspace.id,
        conversation_id="",
        user_id=user.id,
        context_snapshot={"test": "artifact-publication"},
    )
    db.commit()
    output = tmp_path / "sandbox" / f"run_{run.id}" / "north_china_analysis.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"synthetic-png-for-contract-test")
    result = ToolResult(
        status="success",
        data={
            "stdout": "图表已保存到 /mnt/data/north_china_analysis.png\n",
            "files": ["north_china_analysis.png"],
        },
        artifacts=[{"name": output.name, "path": str(output)}],
    )

    public = _persist_result_artifacts(db, run, "python_execute", result, {})
    db.commit()

    assert len(public) == 1
    assert public[0]["preview_url"].endswith("/content")
    assert public[0]["download_url"].endswith("/download")
    assert "path" not in result.artifacts[0]
    assert "/mnt/data" not in result.data["stdout"]
    assert public[0]["download_url"] in result.data["stdout"]
    stored = db.get(Artifact, public[0]["id"])
    assert stored is not None and stored.mime_type == "image/png"


def test_markdown_internal_paths_use_preview_and_download_routes():
    artifact = {
        "id": "01M1TESTARTIFACT0000000001",
        "name": "north_china_analysis.png",
        "type": "image",
        "preview_url": "/api/artifacts/01M1TESTARTIFACT0000000001/content",
        "download_url": "/api/artifacts/01M1TESTARTIFACT0000000001/download",
    }
    text = (
        "![华北销售图](/mnt/data/north_china_analysis.png)\n"
        "[下载图表](/tmp/north_china_analysis.png)\n"
        "文件位于 /mnt/data/north_china_analysis.png。"
    )

    rewritten = _rewrite_artifact_references(text, [artifact])

    assert "![华北销售图](/api/artifacts/01M1TESTARTIFACT0000000001/content)" in rewritten
    assert "[下载图表](/api/artifacts/01M1TESTARTIFACT0000000001/download)" in rewritten
    assert "[north_china_analysis.png](/api/artifacts/01M1TESTARTIFACT0000000001/download)" in rewritten
    assert "/mnt/data" not in rewritten and "/tmp/" not in rewritten


def test_image_content_is_inline_with_real_mime_and_workspace_query(
    client, administrator, db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    workspace, user = _actor(db)
    run = create_run(
        db, workspace_id=workspace.id, conversation_id="", user_id=user.id,
    )
    image = create_artifact(
        db,
        workspace_id=workspace.id,
        run_id=run.id,
        owner_user_id=user.id,
        type="image",
        name="sales-photo.jpg",
        data=b"synthetic-jpeg-for-contract-test",
    )
    document = create_artifact(
        db,
        workspace_id=workspace.id,
        run_id=run.id,
        owner_user_id=user.id,
        type="csv",
        name="sales.csv",
        data=b"amount\n1\n",
    )
    db.commit()

    metadata = client.get(f"/api/artifacts/{image.id}", headers=administrator)
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["mime_type"] == "image/jpeg"
    assert metadata.json()["preview_url"] == f"/api/artifacts/{image.id}/content"

    token = administrator["Authorization"].removeprefix("Bearer ")
    content = client.get(
        f"/api/artifacts/{image.id}/content",
        params={"token": token, "workspace_id": workspace.id},
    )
    assert content.status_code == 200, content.text
    assert content.headers["content-type"].startswith("image/jpeg")
    assert content.headers["content-disposition"].startswith("inline")
    assert content.headers["x-content-type-options"] == "nosniff"

    download = client.get(f"/api/artifacts/{image.id}/download", headers=administrator)
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment")
    unavailable = client.get(f"/api/artifacts/{document.id}/content", headers=administrator)
    assert unavailable.status_code == 422
