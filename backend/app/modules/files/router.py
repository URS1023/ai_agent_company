# -*- coding: utf-8 -*-
"""文件资产治理的 HTTP 接口层（前缀 /api/files）。

职责：把 HTTP 语义（方法/路径/查询参数/响应码）映射到 files/service.py 的业务函数，
并统一处理三件横切关注点——鉴权上下文（ctx.workspace_id / ctx.user_id）、审计写入
（common.audit.audit）、事务提交（本地 _commit 或显式 ctx.db.commit）。

关键约定：
    - 路由只做参数解析与序列化，业务规则（校验顺序、乐观锁、版本链、权限范围）都在 service 层；
    - 依赖注入决定鉴权强度：get_current_context 为常规操作，get_content_auth 额外校验"内容读取"
      权限（下载/预览正文这类会外泄原始字节的接口都用它）；
    - 只有导出接口显式要求 files.export 权限，其余写操作依赖数据范围（workspace_id）隔离；
    - 静态子路径（/categories、/batch、/export）必须在 /{file_id} 之前注册，
      否则会被路径参数吞掉。
"""
from __future__ import annotations

import csv
import io
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File as FileForm, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...common.audit import audit
from ...common.authorization import require_permission
from ...common.deps import AuthContext, get_content_auth, get_current_context
from ...common.storage import get_storage
from ...errors import ConflictError, NotFoundError, ValidationError
from . import service as files_service

router = APIRouter(prefix="/api/files", tags=["文件资产治理"], responses={
    401: {"description": "未登录或会话失效"},
    403: {"description": "没有当前文件操作权限或数据范围"},
    404: {"description": "文件、版本或分类不存在"},
    409: {"description": "版本冲突、分类非空或同级名称重复"},
    422: {"description": "文件类型、大小、名称或元数据不符合约束"},
})


class FileRename(BaseModel):
    """重命名请求体；扩展名不可变，改名超过 500 字会被拒。"""

    name: str = Field(min_length=1, max_length=500, examples=["设备维护规程.pdf"])


class FileCopy(BaseModel):
    """复制请求体；new_name 为空时服务端自动生成"xxx（副本）.ext"。"""

    new_name: str = Field(default="", max_length=500)


class FileMetadataUpdate(BaseModel):
    """元数据更新请求体。

    version 是必填的乐观锁字段（当前 FileMetadata.version），防止两个页面互相覆盖；
    其余字段为 None 表示"本次不改这一项"，因此前端只提交变化项即可。
    """

    version: int = Field(ge=1, description="当前文件元数据版本；防止覆盖并发修改")
    name: str | None = Field(default=None, min_length=1, max_length=500)
    category_id: str | None = None
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class FileCategoryCreate(BaseModel):
    """新建分类请求体；parent_id 为空表示顶层分类。"""

    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    description: str = Field(default="", max_length=1000)
    sort_order: int = Field(default=0, ge=-100000, le=100000)


class FileCategoryUpdate(FileCategoryCreate):
    """更新分类请求体，在创建字段之上追加乐观锁 version。"""

    version: int = Field(ge=1)


def _tags(value: str) -> list[str]:
    """把表单里的标签串解析成列表：逗号（含换行转成的逗号）分隔，去空白项。

    表单只能提交字符串，所以标签用逗号分隔传输；换行也当分隔符是兼容用户在文本框里换行输入。
    真正的长度/数量/去重规则在 service.normalize_tags，这里只做切分。
    """
    return [item.strip() for item in (value or "").replace("\n", ",").split(",") if item.strip()]


def _commit(ctx: AuthContext, *, action: str, resource_id: str, metadata: dict | None = None) -> None:
    """写审计 + 提交事务，失败时回滚后重新抛出。

    审计与业务写入在同一个事务里：先 audit 再 commit，保证"审计落库"与"业务落库"同生共死。
    参数 action 形如 file.upload / file.version_restore，resource_type 固定为 file。
    """
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action=action, resource_type="file", resource_id=resource_id,
        metadata=metadata or {},
    )
    try:
        ctx.db.commit()
    except Exception:
        ctx.db.rollback()
        raise


@router.get("/categories", operation_id="files_list_categories", summary="查看文件分类树及文件数量")
def list_categories(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/files/categories —— 列出当前工作区所有生效分类及其子分类数、文件数。

    权限：登录 + 当前工作区；无需额外权限点。只读，返回扁平列表，由前端组装成树。
    """
    return {"items": files_service.list_categories(ctx.db, ctx.workspace_id)}


@router.post("/categories", status_code=201, operation_id="files_create_category", summary="新增文件分类")
def create_category(payload: FileCategoryCreate, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/files/categories —— 新建文件分类，201 返回新建行。

    异常：404 父分类不存在；409 同级重名；422 名称超长等参数问题。
    副作用：写 file_categories，审计 action=file.category_create。
    """
    row = files_service.create_category(
        ctx.db, ctx.workspace_id, name=payload.name, parent_id=payload.parent_id,
        description=payload.description, sort_order=payload.sort_order,
    )
    _commit(ctx, action="file.category_create", resource_id=row.id)
    return files_service.category_out(row)


@router.patch("/categories/{category_id}", operation_id="files_update_category", summary="版本化修改文件分类")
def update_category(
    category_id: str, payload: FileCategoryUpdate,
    ctx: AuthContext = Depends(get_current_context),
):
    """PATCH /api/files/categories/{category_id} —— 版本化修改分类。

    payload.version 必须等于当前版本（乐观锁），否则 409 并回传 current_version；
    parent_id 传自己或形成环会返回 422。
    副作用：写 file_categories，审计 action=file.category_update。
    """
    row = files_service.update_category(
        ctx.db, ctx.workspace_id, category_id, version=payload.version,
        name=payload.name, parent_id=payload.parent_id,
        description=payload.description, sort_order=payload.sort_order,
    )
    _commit(ctx, action="file.category_update", resource_id=row.id)
    return files_service.category_out(row)


@router.delete("/categories/{category_id}", operation_id="files_delete_category", summary="归档空文件分类")
def delete_category(
    category_id: str,
    version: Annotated[int, Query(ge=1)],
    ctx: AuthContext = Depends(get_current_context),
):
    """DELETE /api/files/categories/{category_id} —— 归档空分类，成功只返回 {"ok": true}。

    version 走查询参数（Query(ge=1)）而非请求体，因为 DELETE 带 body 兼容性差。
    非空保护：存在 active 子分类或仍挂着当前版本文件时返回 409；"删除"实为 status=archived。
    """
    row = files_service.delete_category(ctx.db, ctx.workspace_id, category_id, version)
    _commit(ctx, action="file.category_delete", resource_id=row.id)
    return {"ok": True}


@router.post("/batch", status_code=201, operation_id="files_batch_upload", summary="原子批量上传最多二十个文件")
async def batch_upload(
    files: list[UploadFile] = FileForm(...),
    category_id: str | None = Form(default=None),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/files/batch —— 原子批量上传（multipart，最多 20 个 / 合计 50 MB）。

    本地先行校验与读取：按 MAX_FILE_BYTES + 1 读取是为了在"恰好等于上限"时不误判超限，
    同时避免把超大文件整个读进内存。任一文件超限或合计超限都在写库前直接 422。
    原子性：所有文件写库后一次性提交；任一步失败则回滚数据库并删除已落盘的对象，
    因此不会留下"部分成功"的批次。审计按文件逐条写（metadata.batch=True 标记同批）。
    """
    if not files or len(files) > files_service.MAX_BATCH_FILES:
        raise ValidationError("每批必须上传 1 至 20 个文件")
    payloads = []
    total = 0
    for upload in files:
        data = await upload.read(files_service.MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > files_service.MAX_FILE_BYTES:
            raise ValidationError(f"文件 {upload.filename or '未命名'} 超过 20 MB")
        if total > files_service.MAX_BATCH_BYTES:
            raise ValidationError("每批文件总大小不能超过 50 MB")
        payloads.append((upload, data))
    created = []
    try:
        for upload, data in payloads:
            created.append(files_service.save_upload(
                ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                filename=upload.filename or "未命名", mime_type=upload.content_type or "application/octet-stream",
                data=data, category_id=category_id, description=description, tags=_tags(tags),
            ))
        for row in created:
            audit(
                ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                action="file.upload", resource_type="file", resource_id=row.id,
                metadata={"batch": True},
            )
        ctx.db.commit()
    except Exception:
        ctx.db.rollback()
        storage = get_storage()
        for row in created:
            storage.delete(row.storage_key)
        raise
    return {"items": [files_service.file_out(ctx.db, row) for row in created], "count": len(created)}


@router.get("/export", operation_id="files_export_catalog", summary="导出当前授权范围的文件目录 CSV")
def export_catalog(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/files/export —— 导出当前授权范围的文件目录 CSV 附件。

    权限：显式要求 files.export（本模块唯一带权限点的接口，因为导出会把目录数据带出系统）。
    实现细节：内容是 BOM + CSV，BOM 是给 Excel 认 UTF-8 用的；文件名同时给 ASCII 与非 ASCII
    两种写法（filename= 给老浏览器，filename*=UTF-8'' 给现代浏览器）。
    Cache-Control: no-store 避免导出的目录被中间层缓存。
    """
    require_permission(ctx, "files.export")
    rows = files_service.list_files(ctx.db, ctx.workspace_id)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["文件ID", "名称", "分类", "类型", "字节", "版本", "SHA256", "标签", "创建时间"])
    for row in rows:
        item = files_service.file_out(ctx.db, row)
        writer.writerow([
            item["id"], item["name"], item["category_name"] or "", item["mime_type"],
            item["size"], item["revision_number"], item["sha256"],
            ",".join(item["tags"]), item["created_at"] or "",
        ])
    name = "文件目录.csv"
    return Response(
        content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=file-catalog.csv; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
        },
    )


@router.post("", status_code=201, operation_id="files_upload", summary="上传一个文件并创建首个内容版本")
async def upload_file(
    file: UploadFile = FileForm(...),
    category_id: str | None = Form(default=None),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/files —— 上传单个文件并建立首个内容版本，201 返回文件视图。

    文件名缺失时用"未命名"，MIME 缺失时给 application/octet-stream 交由扩展名推断兜底。
    异常：422 文件名/大小/类型不合规、404 分类无效。
    副作用：写对象存储 + files/file_metadata，审计 action=file.upload。
    """
    data = await file.read(files_service.MAX_FILE_BYTES + 1)
    row = files_service.save_upload(
        ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        filename=file.filename or "未命名", mime_type=file.content_type or "application/octet-stream",
        data=data, category_id=category_id, description=description, tags=_tags(tags),
    )
    _commit(ctx, action="file.upload", resource_id=row.id)
    return files_service.file_out(ctx.db, row)


@router.get("", operation_id="files_list", summary="分页查询当前版本文件并按分类筛选")
def list_files(
    q: Annotated[str, Query(max_length=200)] = "",
    category_id: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/files —— 按关键字/分类筛选后分页返回当前版本文件。

    分页在内存里做（先取全量再切片）：资料库是工作区级小集合，这样可以让 service 层保持
    单一查询、避免两套过滤条件不一致；page_size 上限 200。响应同时给 count（本页条数）
    与 total（过滤后总数），兼容既有前端字段名。
    副作用：读取时会触发历史文件纳管（顺带补建元数据），故末尾显式 commit。
    """
    rows = files_service.list_files(
        ctx.db, ctx.workspace_id, query=q, category_id=category_id,
    )
    total = len(rows)
    start = (page - 1) * page_size
    items = [files_service.file_out(ctx.db, row) for row in rows[start:start + page_size]]
    ctx.db.commit()
    return {"items": items, "count": total, "total": total, "page": page, "page_size": page_size}


@router.get("/{file_id}/download", operation_id="files_download", summary="下载指定文件内容版本")
def download(file_id: str, ctx: AuthContext = Depends(get_content_auth)):
    """GET /api/files/{file_id}/download —— 下载指定内容版本的原始字节。

    权限：get_content_auth（在常规上下文之外额外校验内容读取权限），因为这是唯一直接返回
    文件字节的接口。file_id 可以是历史版本，历史版本也能下载。
    异常：404 文件不存在或存储对象缺失（记录还在但字节被清理的情况）。
    副作用：只读；不做审计写入。响应带 private, no-store，避免企业代理缓存敏感文档。
    """
    row = files_service.get_file(ctx.db, ctx.workspace_id, file_id)
    storage = get_storage()
    if not storage.exists(row.storage_key):
        raise NotFoundError("文件数据不存在")
    disposition = "attachment; filename=\"file\"; filename*=UTF-8''" + quote(row.name)
    return Response(
        content=storage.read_bytes(row.storage_key), media_type=row.mime_type,
        headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"},
    )


@router.get("/{file_id}/text", operation_id="files_preview_text", summary="解析并预览文件文本，同时记录解析证据")
def file_text(file_id: str, ctx: AuthContext = Depends(get_content_auth)):
    """GET /api/files/{file_id}/text —— 解析并返回文件文本（预览用）。

    权限：get_content_auth。返回 service 的原始结构（text/blocks/char_count 等），
    blocks 保留 page/sheet/slide 定位，前端预览与之后的知识库引用共用同一份结构。
    异常：404 文件或存储对象不存在；422 解析失败（失败原因已先写入解析事件）。
    副作用：写 file_parse_events 一条成功或失败记录；因此这里必须 commit。
    """
    result = files_service.extract_preview(ctx.db, ctx.workspace_id, file_id, ctx.user_id)
    ctx.db.commit()
    return result


@router.get("/{file_id}/parse-events", operation_id="files_parse_events", summary="查看文件解析与预览记录")
def parse_events(
    file_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/files/{file_id}/parse-events —— 查看该逻辑文件的解析/预览历史（默认 50，上限 200）。

    只读；按 logical_id 聚合，所以换过版本后旧证据依然可见。用于排查"为什么预览是空的"。
    """
    items = files_service.list_parse_events(ctx.db, ctx.workspace_id, file_id, limit)
    return {"items": items, "count": len(items)}


@router.get("/{file_id}/versions", operation_id="files_list_versions", summary="查看不可变文件内容版本历史")
def list_versions(file_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/files/{file_id}/versions —— 查看不可变内容版本历史（最新在前）。

    只读；入参可为任意版本（内部归一到当前版本再取整链）。current=True 标记当前版本。
    """
    items = files_service.list_versions(ctx.db, ctx.workspace_id, file_id)
    return {"items": items, "count": len(items)}


@router.post("/{file_id}/versions", status_code=201, operation_id="files_create_version", summary="上传文件的新内容版本")
async def create_version(
    file_id: str,
    file: UploadFile = FileForm(...),
    change_note: str = Form(default="上传新版本", max_length=500),
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/files/{file_id}/versions —— 上传字节并追加为新的内容版本，201 返回新版本视图。

    不覆盖旧版本：旧行与旧存储对象都保留，因此知识库对老版本的引用不会失效。
    change_note 默认"上传新版本"，上限 500 字（表单层已限制）。
    异常：422 上传校验失败、404 当前文件不存在。
    副作用：写对象存储与两张表，审计 action=file.version_create（metadata 记录 previous_file_id）。
    """
    data = await file.read(files_service.MAX_FILE_BYTES + 1)
    row = files_service.create_version(
        ctx.db, ctx.workspace_id, file_id, actor_user_id=ctx.user_id,
        filename=file.filename or "", mime_type=file.content_type or "application/octet-stream",
        data=data, change_note=change_note,
    )
    _commit(ctx, action="file.version_create", resource_id=row.id, metadata={"previous_file_id": file_id})
    return files_service.file_out(ctx.db, row)


@router.post("/{file_id}/versions/{revision_number}/restore", status_code=201, operation_id="files_restore_version", summary="把历史内容恢复为一个新版本")
def restore_version(
    file_id: str,
    revision_number: int,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/files/{file_id}/versions/{revision_number}/restore —— 把历史版本恢复为新版本。

    恢复语义是"再发一版"而不是删除历史，所以审计 metadata 同时记录 source_revision 与
    previous_file_id，可追溯"这一版是从哪一版恢复出来的"。
    异常：404 指定版本号不存在、存储对象缺失时读字节会失败。
    副作用：写对象存储与两张表，审计 action=file.version_restore。
    """
    row = files_service.restore_version(
        ctx.db, ctx.workspace_id, file_id, revision_number, ctx.user_id,
    )
    _commit(
        ctx, action="file.version_restore", resource_id=row.id,
        metadata={"source_revision": revision_number, "previous_file_id": file_id},
    )
    return files_service.file_out(ctx.db, row)


@router.patch("/{file_id}/metadata", operation_id="files_update_metadata", summary="修改文件分类、说明与标签")
def update_metadata(
    file_id: str, payload: FileMetadataUpdate,
    ctx: AuthContext = Depends(get_current_context),
):
    """PATCH /api/files/{file_id}/metadata —— 修改当前版本的分类/说明/标签（可同时改名）。

    payload.version 为必需乐观锁，不匹配返回 409 并回传 current_version；改名时扩展名必须不变，
    否则 422。作用对象始终是当前版本（历史版本会被自动归一）。
    副作用：写 files.name 与 file_metadata，审计 action=file.metadata_update。
    """
    row, metadata = files_service.update_metadata(
        ctx.db, ctx.workspace_id, file_id, version=payload.version,
        category_id=payload.category_id, description=payload.description, tags=payload.tags,
        name=payload.name,
    )
    _commit(ctx, action="file.metadata_update", resource_id=row.id)
    return files_service.file_out(ctx.db, row)


@router.post("/{file_id}/copy", status_code=201, operation_id="files_copy", summary="复制当前文件为独立资产")
def copy_file(
    file_id: str, payload: FileCopy | None = None,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/files/{file_id}/copy —— 复制当前版本为一个独立文件资产，201 返回新资产。

    与"新建版本"的区别：副本是新的 logical_id，与原文件互不影响（分类/描述/标签会复制一份，
    便于"同一份文件放进多个分类"）。new_name 为空时自动生成"（副本）"名，且同样受
    扩展名不可变约束。源文件不存在时 404（避免复制出空文件）。
    副作用：写对象存储与两张表，审计 action=file.copy（metadata.source_id 指向源文件）。
    """
    source, metadata = files_service.current_file(ctx.db, ctx.workspace_id, file_id)
    storage = get_storage()
    if not storage.exists(source.storage_key):
        raise NotFoundError("文件数据不存在")
    new_name = files_service.validate_renamed_filename(
        source.name, payload.new_name if payload and payload.new_name else files_service.copied_filename(source.name),
    )
    row = files_service.save_upload(
        ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        filename=new_name, mime_type=source.mime_type, data=storage.read_bytes(source.storage_key),
        category_id=metadata.category_id, description=metadata.description, tags=metadata.tags,
        change_note="从其他文件复制",
    )
    _commit(ctx, action="file.copy", resource_id=row.id, metadata={"source_id": source.id})
    return {**files_service.file_out(ctx.db, row), "source_id": source.id}


@router.patch("/{file_id}", operation_id="files_rename", summary="重命名当前文件资产")
def rename_file(
    file_id: str, payload: FileRename,
    ctx: AuthContext = Depends(get_current_context),
):
    """PATCH /api/files/{file_id} —— 重命名当前文件资产（只改展示名，不产生新版本）。

    扩展名不可变（422），因此不会出现"名字是 .pdf、内容是 docx"的错配。
    副作用：写 files.name，审计 action=file.rename。
    """
    row = files_service.rename_file(ctx.db, ctx.workspace_id, file_id, payload.name)
    _commit(ctx, action="file.rename", resource_id=row.id)
    return files_service.file_out(ctx.db, row)


@router.delete("/{file_id}", operation_id="files_delete", summary="软删除文件资产及全部内容版本")
def delete_file(file_id: str, ctx: AuthContext = Depends(get_current_context)):
    """DELETE /api/files/{file_id} —— 软删除文件资产及其全部内容版本。

    软删除：只置 deleted_at/status 并把整链 is_current 置 False，不动对象存储、不删数据库行，
    因此知识库对已删文件的引用仍能解析（检索侧会按 File.deleted_at 过滤掉）。
    返回 {"ok": true, "deleted_versions": N}，N 为链上被标记的物理版本数。
    副作用：批量更新 files 与 file_metadata，审计 action=file.delete。
    """
    deleted_ids = files_service.mark_deleted(ctx.db, ctx.workspace_id, file_id)
    _commit(ctx, action="file.delete", resource_id=file_id, metadata={"version_file_ids": deleted_ids})
    return {"ok": True, "deleted_versions": len(deleted_ids)}
