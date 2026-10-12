# -*- coding: utf-8 -*-
"""产物模块的 HTTP 接口层（前缀 /api/artifacts）。
职责：列出/查询产物、下载字节、内联预览（图片直接回图、文本类回文本）、把产物登记为下游可用输入。
权限：列表与详情用常规上下文（get_current_context）；下载与内联用 get_content_auth，
因为这两条会直接把文件字节交给调用方。
MIME 与类型推断全部委托 artifact.service，路由层不做格式判断。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...common.audit import audit
from ...common.deps import AuthContext, get_content_auth, get_current_context
from ...errors import NotFoundError, ValidationError
from . import service as artifact_service
from .renderers import RENDERERS

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("")
def list_artifacts(ctx: AuthContext = Depends(get_current_context), run_id: str = None):
    """GET /api/artifacts —— 列出产物；run_id 可选，用于只看某次运行的结果。只读。"""
    rows = artifact_service.list_artifacts(ctx.db, ctx.workspace_id, run_id)
    return {"items": [artifact_service.artifact_out(a) for a in rows], "count": len(rows)}


@router.get("/{artifact_id}")
def get_artifact(artifact_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/artifacts/{artifact_id} —— 产物详情。异常：404（不存在或不属于本工作区）。"""
    a = artifact_service.get_artifact(ctx.db, ctx.workspace_id, artifact_id)
    return artifact_service.artifact_out(a)


@router.get("/{artifact_id}/download")
def download(artifact_id: str, ctx: AuthContext = Depends(get_content_auth)):
    """GET /api/artifacts/{artifact_id}/download —— 下载产物字节（附件形式）。
    权限：内容读取权限。异常：404（产物或底层存储对象缺失）。响应带 no-store，避免中间层缓存。
    """
    a = artifact_service.get_artifact(ctx.db, ctx.workspace_id, artifact_id)
    if a.external_url and not a.storage_key:
        return {"redirect": a.external_url}
    from ...common.storage import get_storage
    storage = get_storage()
    if not a.storage_key or not storage.exists(a.storage_key):
        raise NotFoundError("文件内容不存在")
    data = storage.read_bytes(a.storage_key)
    from urllib.parse import quote
    disp = "attachment; filename=\"file\"; filename*=UTF-8''" + quote(a.name)
    return Response(content=data, media_type=a.mime_type,
                    headers={"Content-Disposition": disp,
                             "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store"})


@router.get("/{artifact_id}/content")
def content(artifact_id: str, ctx: AuthContext = Depends(get_content_auth)):
    """Render safe image artifacts inline; downloads remain attachments."""
    # GET /api/artifacts/{artifact_id}/content —— 内联返回产物内容（图片/文本可直接展示）。
    # 权限：内容读取权限。行为差异：图片与文本返回原始媒体类型并允许内联；
    # 其它类型返回 JSON 包装（含元信息），避免浏览器把二进制当页面渲染。异常：404。
    #
    a = artifact_service.get_artifact(ctx.db, ctx.workspace_id, artifact_id)
    if not artifact_service.is_inline_image(a):
        raise ValidationError("该结果不是可预览的图片")
    if not a.storage_key:
        raise NotFoundError("图片内容不存在")
    from ...common.storage import get_storage
    storage = get_storage()
    if not storage.exists(a.storage_key):
        raise NotFoundError("图片内容不存在")
    from urllib.parse import quote
    disp = "inline; filename=\"image\"; filename*=UTF-8''" + quote(a.name)
    return Response(
        content=storage.read_bytes(a.storage_key),
        media_type=a.mime_type,
        headers={
            "Content-Disposition": disp,
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/{artifact_id}/use", status_code=201)
def use_as_input(
    artifact_id: str,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/artifacts/{artifact_id}/use-as-input —— 把该产物登记为下游可引用的输入资源。
    用途：让生成/工作流可以把上一次运行的产物当材料，而不必重新上传。
    异常：404（产物不存在）。副作用：写引用记录并审计。
    """
    artifact = artifact_service.get_artifact(
        ctx.db, ctx.workspace_id, artifact_id
    )
    if not artifact.storage_key:
        raise ValidationError("外部链接结果暂不支持直接作为输入，请先下载后上传")
    from ...common.storage import get_storage
    from ...modules.files.service import save_upload

    storage = get_storage()
    if not storage.exists(artifact.storage_key):
        raise NotFoundError("结果文件内容不存在")
    row = save_upload(
        ctx.db,
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        filename=artifact.name,
        mime_type=artifact.mime_type,
        data=storage.read_bytes(artifact.storage_key),
    )
    row.source_type = "artifact"
    ctx.db.commit()
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="artifact.use_as_input",
        resource_type="artifact",
        resource_id=artifact.id,
        run_id=artifact.run_id,
        metadata={"file_id": row.id},
    )
    ctx.db.commit()
    return {
        "id": row.id,
        "name": row.name,
        "mime_type": row.mime_type,
        "size": row.size,
    }
