# -*- coding: utf-8 -*-
"""Artifact service — artifacts are first-class objects bound to a run."""
from __future__ import annotations

from app.core.observability import log_event, observe

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...common.storage import build_key, get_storage, sha256_bytes
from ...errors import NotFoundError
from ...ids import new_id
from ...models import Artifact

TYPE_MIME = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "image": "image/png",
    "chart": "image/png",
    "csv": "text/csv",
    "json": "application/json",
    "code": "text/x-python",
    "dataset": "application/json",
}

EXTENSION_TYPES = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".bmp": "image",
    ".svg": "image",
    ".csv": "csv",
    ".json": "json",
    ".py": "code",
    ".doc": "document",
    ".docx": "document",
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".pdf": "pdf",
}

EXTENSION_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/x-python",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}

INLINE_IMAGE_MIME = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
    "image/svg+xml",
})


def infer_artifact_type(name: str, fallback: str = "document") -> str:
    """Infer a customer-facing artifact kind without overriding an explicit chart."""
    # 按文件名扩展名推断产物类型；未知扩展名返回 fallback（默认 document）。
    #
    suffix = Path(str(name or "")).suffix.lower()
    inferred = EXTENSION_TYPES.get(suffix)
    if fallback == "chart" and inferred == "image":
        return "chart"
    return inferred or fallback or "document"


def artifact_mime_type(type: str, name: str) -> str:
    """Return the actual file MIME instead of assuming every image is PNG."""
    # 取产物的 MIME：优先按扩展名精确匹配，其次按类型取默认 MIME，最后回落 octet-stream。
    #
    suffix = Path(str(name or "")).suffix.lower()
    return EXTENSION_MIME.get(suffix) or TYPE_MIME.get(type, "application/octet-stream")


def is_inline_image(a: Artifact) -> bool:
    """判断产物是否可作为内联图片直接在对话里展示（图片类且非 svg 等需下载的格式）。"""
    return str(a.mime_type or "").lower() in INLINE_IMAGE_MIME


def artifact_reference(a: Artifact) -> dict:
    """Small, safe reference embedded in messages and tool results."""
    # 生成"引用产物"的最小结构（id/名称/类型/下载地址等），供对话消息与运行详情内嵌引用。
    #
    reference = {
        "id": a.id,
        "type": a.type,
        "name": a.name,
        "mime_type": a.mime_type,
        "size": a.size,
        "download_url": f"/api/artifacts/{a.id}/download",
    }
    if is_inline_image(a):
        reference["preview_url"] = f"/api/artifacts/{a.id}/content"
    return reference


@observe("artifact.create", "生成并保存文件", fields={"workspace_id": "workspace_id", "run_id": "run_id", "artifact_type": "type"})
def create_artifact(db: Session, *, workspace_id: str, run_id: str, owner_user_id: str,
                    type: str, name: str, data: bytes | None = None,
                    external_url: Optional[str] = None, metadata: Optional[dict] = None,
                    parent_artifact_id: Optional[str] = None,
                    mime_type: Optional[str] = None) -> Artifact:
    """写入一个产物：落对象存储 + 写 artifacts 表，返回 Artifact 行。
    参数：type 为产物类型；data 为字节；metadata 会随行保存（用于追溯来源，如 generationId/来源快照）。
    异常：存储写入失败或数据库写入失败时向上抛出（调用方负责回滚与清理）。
    副作用：写对象存储与 artifacts 表；本函数不 commit，由调用方决定事务边界。
    """
    storage = get_storage()
    artifact_id = new_id()
    storage_key = None
    sha = None
    type = infer_artifact_type(name, type)
    mime = mime_type or artifact_mime_type(type, name)
    size = 0
    if data is not None:
        from ...common.artifact_fonts import normalize_office_fonts
        data = normalize_office_fonts(data, Path(name).suffix.lower())
        sha = sha256_bytes(data)
        size = len(data)
        key = build_key("artifacts", workspace_id, artifact_id, name)
        storage.put_bytes(key, data)
        storage_key = key
    row = Artifact(
        id=artifact_id, workspace_id=workspace_id, run_id=run_id, owner_user_id=owner_user_id,
        type=type, name=name, mime_type=mime, size=size, storage_key=storage_key,
        external_url=external_url, sha256=sha, metadata_json=metadata or {},
        status="ready", parent_artifact_id=parent_artifact_id, created_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    log_event("artifact.saved", "文件已写入存储并登记，数据库提交由调用方完成", artifact_id=row.id,
              artifact_type=row.type, size_bytes=row.size, run_id=run_id)
    return row


def get_artifact(db: Session, workspace_id: str, artifact_id: str) -> Artifact:
    """按 id 取产物并校验工作区归属。异常：NotFoundError（不存在或跨工作区）。"""
    row = db.get(Artifact, artifact_id)
    if not row or row.workspace_id != workspace_id:
        raise NotFoundError("Artifact 不存在")
    return row


def list_artifacts(db: Session, workspace_id: str, run_id: str | None = None):
    """列出产物；传 run_id 时只返回该次运行的产物（按创建时间倒序）。"""
    q = select(Artifact).where(Artifact.workspace_id == workspace_id)
    if run_id:
        q = q.where(Artifact.run_id == run_id)
    q = q.order_by(Artifact.created_at.desc())
    return db.execute(q).scalars().all()


def artifact_out(a: Artifact) -> dict:
    """产物行的对外序列化（含下载地址与 MIME，供前端直接渲染或下载）。"""
    return {
        **artifact_reference(a),
        "run_id": a.run_id,
        "status": a.status,
        "external_url": a.external_url,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
