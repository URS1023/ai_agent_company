# -*- coding: utf-8 -*-
"""知识库模块的 HTTP 接口层（前缀 /api/knowledge）。

职责：知识库增删改查、文档入库与重建、检索（search）、手工分段（text/qa）管理、
设置（chunk/top_k/rerank）读写、召回日志、同步运行记录与角色级授权。

关键依赖：service.py（生命周期/入库/检索管线）、governance.py（设置/分段/召回/同步）。
审计与事务约定：写操作统一在业务函数返回后调用 common.audit.audit 再 commit，保证审计与业务同事务；
service 内部已 commit 的接口（入库、检索、重建）不再重复提交。

权限模型（两层）：
    - 数据范围：几乎所有接口用 get_current_context 拿到 ctx.workspace_id，跨工作区一律 404；
    - 权限点：导出分段要求 knowledge.export，同步要求 knowledge.execute，
      授权列表要求 system.permissions.view（授权本身走 admin_service，需管理员角色）；
      召回日志用 access_policy(ctx).administrator 决定"只看自己"还是"看全工作区"。
路由注册顺序：/documents、/search、/recall-logs 等静态段必须先于 /{kb_id} 注册，
    否则会被路径参数匹配走；/{kb_id}/segments/batch 也必须先于 /{kb_id}/segments/{chunk_id}。
"""
from __future__ import annotations

import csv
import io
import json
from time import perf_counter
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File as FileForm, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...common.audit import audit
from ...common.authorization import access_policy, require_permission
from ...common.deps import AuthContext, get_current_context
from ...errors import ConflictError, NotFoundError, ValidationError
from ...modules.system import admin_service
from ...modules.system.models import ResourceGrant, Role
from ...modules.system.schemas import ResourceGrantCreate
from . import service as kb_service
from . import governance as kb_governance

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class KBCreate(BaseModel):
    """新建知识库请求体；名称必填、不超过 200 字。"""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class KBUpdate(BaseModel):
    """修改知识库请求体；两项都可选，None 表示不改（与"改成空字符串"语义不同）。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class DocAdd(BaseModel):
    """文档入库请求体；chunk_size/overlap 的边界与 service.validate_chunking 保持一致。

    这里用 Field(ge/le) 先挡一层，是为了在进入昂贵的解析/向量化之前就返回 422。
    """

    kb_id: str
    file_id: str
    chunk_size: int = Field(default=kb_service.DEFAULT_CHUNK_SIZE, ge=200, le=2000)
    overlap: int = Field(default=kb_service.DEFAULT_CHUNK_OVERLAP, ge=0, le=400)


class DocReindex(BaseModel):
    """重建索引请求体；两项都不传时沿用文档现有解析指纹里的参数。"""

    chunk_size: Optional[int] = Field(default=None, ge=200, le=2000)
    overlap: Optional[int] = Field(default=None, ge=0, le=400)


class SearchRequest(BaseModel):
    """检索请求体。全部可选项为 None 时，由 governance.effective_search_options 用知识库设置补齐。"""

    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: Optional[List[str]] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    mode: Optional[Literal["hybrid", "keyword", "semantic"]] = None
    min_score: Optional[float] = Field(default=None, ge=0, le=100)
    filters: Optional[dict] = None


class RetrievalSettingsUpdate(BaseModel):
    """知识库设置更新请求体；version 为必填乐观锁字段。

    字段边界与 governance.update_profile 的校验规则一致（此处先挡明显越界值）。
    rerank_model 只在 rerank_enabled 为真时才会被校验真实性。
    """

    version: int = Field(ge=1, description="当前设置版本，防止并发覆盖")
    retrieval_mode: Literal["hybrid", "keyword", "semantic"] = "hybrid"
    top_k: int = Field(default=6, ge=1, le=20)
    min_score: float = Field(default=0, ge=0, le=100)
    chunk_size: int = Field(default=kb_service.DEFAULT_CHUNK_SIZE, ge=200, le=2000)
    overlap: int = Field(default=kb_service.DEFAULT_CHUNK_OVERLAP, ge=0, le=400)
    embedding_model: str = Field(default="", max_length=200)
    rerank_enabled: bool = False
    rerank_model: str = Field(default="", max_length=200)


class SegmentInput(BaseModel):
    """单个分段入参；text 与 qa 两种类型共用一个模型，按 segment_type 决定用哪些字段。

    text ≤20000 字，question ≤2000 字，answer ≤20000 字（与 governance._segment_content 的长度校验一致）。
    """

    segment_type: Literal["text", "qa"] = "text"
    text: str = Field(default="", max_length=20000)
    question: str = Field(default="", max_length=2000)
    answer: str = Field(default="", max_length=20000)
    enabled: bool = True


class SegmentBatch(BaseModel):
    """批量新增分段请求体；1~500 条（上限与 governance.MAX_SEGMENTS_PER_BATCH 一致）。"""

    items: list[SegmentInput] = Field(min_length=1, max_length=500)


class SegmentUpdate(SegmentInput):
    """修改分段请求体，在 SegmentInput 之上追加乐观锁 version。"""

    version: int = Field(ge=1)


class KnowledgeSyncRequest(BaseModel):
    """文件同步请求体：按分类或指定文件把资料库内容导入知识库。

    category_id 与 file_ids 可同时给出（取交集）；reindex=True 时对已就绪文档强制重建索引。
    """

    category_id: str | None = None
    file_ids: list[str] = Field(default_factory=list, max_length=500)
    reindex: bool = False


class KnowledgeRoleGrant(BaseModel):
    """知识库角色授权请求体；actions 取值与系统权限动作枚举保持一致，默认仅 view。

    该模型只做形状校验，真正的授权写入与管理动作由 admin_service.create_grant 负责。
    """

    role_id: str = Field(min_length=1, max_length=26)
    actions: list[Literal["view", "create", "edit", "delete", "execute", "publish", "export"]] = Field(
        default_factory=lambda: ["view"], min_length=1, max_length=7,
    )


@router.post("", status_code=201)
def create_kb(payload: KBCreate, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/knowledge —— 新建知识库，201 返回详情。

    两次 commit 是既有行为：先提交知识库本体拿到 id，再在同一函数内写审计并二次提交；
    因此若审计写失败，知识库会已经存在（审计缺一条），这是当前实现的已知取舍。
    异常：422 名称为空或超长。
    """
    row = kb_service.create_kb(
        ctx.db,
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        name=payload.name,
        description=payload.description,
    )
    ctx.db.commit()
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="knowledge.create",
        resource_type="knowledge_base",
        resource_id=row.id,
    )
    ctx.db.commit()
    return kb_service.kb_detail(ctx.db, workspace_id=ctx.workspace_id, kb_id=row.id)


@router.get("")
def list_kbs(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/knowledge —— 列出当前工作区的生效知识库（含文档计数、分块数与模型信息）。

    只读；返回 {items, count}。软删除（status=deleted）的库不返回。
    """
    items = kb_service.list_kb_details(ctx.db, ctx.workspace_id)
    return {"items": items, "count": len(items)}


@router.post("/documents", status_code=201)
def add_document(payload: DocAdd, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/knowledge/documents —— 把某个资料库文件导入知识库并建立索引，201 返回 document_id。

    同步接口：解析、识图、向量化都在本次请求内完成，大文件/图片多的文件会明显较慢。
    异常：404 知识库或文件不存在；409 该文件正在建立索引；422 参数非法、原始文件缺失、
        文件无可用文本或向量服务异常。
    副作用：由 service.add_document 完成（读文件、调用嵌入与视觉模型、写库与 ES、自身 commit）。
    """
    document_id = kb_service.add_document(
        ctx.db,
        workspace_id=ctx.workspace_id,
        kb_id=payload.kb_id,
        file_id=payload.file_id,
        chunk_size=payload.chunk_size,
        overlap=payload.overlap,
    )
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="knowledge.add_document",
        resource_type="knowledge_document",
        resource_id=document_id,
        metadata={"kb_id": payload.kb_id, "file_id": payload.file_id},
    )
    ctx.db.commit()
    return {"document_id": document_id, "status": "ready"}


@router.post("/search")
def search(payload: SearchRequest, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/knowledge/search —— 知识库检索，是知识召回测试页与 AI 工具检索的共同入口。

    行为要点：
        - knowledge_base_ids 不传时默认检索当前工作区全部生效知识库；
        - mode/top_k/min_score 不传则用（单库场景下的）知识库设置补齐，见 effective_search_options；
        - 没有任何知识库时跳过 service.search 直接返回空，避免空 in () 查询；
        - 命中后再过一次 filter_enabled_hits 去掉被停用的分段（停用是知识库治理语义，
          不应在检索管线内部实现，否则 AI 工具调用路径会绕过它）；
        - 无论命中多少都会写一条召回日志（含耗时，用于运营观测）。
    返回：{items, count, mode, top_k, min_score, duration_ms}。
    异常：422 查询为空或 mode 非法；404 任一知识库不属于该工作区。
    副作用：调用嵌入模型（可能还有重排服务）、写 knowledge_recall_events，并 commit。
    """
    knowledge_base_ids = payload.knowledge_base_ids
    if knowledge_base_ids is None:
        knowledge_base_ids = [row.id for row in kb_service.list_kbs(ctx.db, ctx.workspace_id)]
    mode, top_k, min_score = kb_governance.effective_search_options(
        ctx.db, ctx.workspace_id, knowledge_base_ids,
        mode=payload.mode, top_k=payload.top_k, min_score=payload.min_score,
    )
    started = perf_counter()
    hits = [] if not knowledge_base_ids else kb_service.search(
        ctx.db, workspace_id=ctx.workspace_id, query=payload.query,
        knowledge_base_ids=knowledge_base_ids, top_k=top_k,
        min_score=min_score, filters=payload.filters, mode=mode,
    )
    hits = kb_governance.filter_enabled_hits(ctx.db, ctx.workspace_id, hits)
    duration_ms = int((perf_counter() - started) * 1000)
    kb_governance.record_recall(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        query=payload.query, kb_ids=knowledge_base_ids,
        mode=mode, top_k=top_k, hits=hits, duration_ms=duration_ms,
        filters=payload.filters or {},
    )
    ctx.db.commit()
    return {
        "items": hits, "count": len(hits), "mode": mode,
        "top_k": top_k, "min_score": min_score, "duration_ms": duration_ms,
    }


@router.get("/recall-logs", operation_id="knowledge_list_recall_logs", summary="查看知识召回测试记录")
def recall_logs(
    knowledge_base_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/knowledge/recall-logs —— 查看知识召回测试记录，最近 limit 条（默认 50，上限 200）。

    权限：不额外要求权限点，但用 access_policy(ctx).administrator 区分范围——
        管理员看全工作区的召回记录，普通用户只看自己发起的检索。
    异常：404 knowledge_base_id 无效（会先校验知识库归属）。
    副作用：只读；不写审计（召回日志本身就是记录）。
    """
    policy = access_policy(ctx)
    items = kb_governance.list_recall_events(
        ctx.db, ctx.workspace_id, actor_user_id=ctx.user_id,
        include_all_actors=policy.administrator,
        kb_id=knowledge_base_id, limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/{kb_id}")
def get_kb(kb_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/knowledge/{kb_id} —— 单个知识库详情（含文档状态计数与生效模型名）。

    异常：404 知识库不存在或不属于该工作区。
    """
    return kb_service.kb_detail(ctx.db, workspace_id=ctx.workspace_id, kb_id=kb_id)


@router.get("/{kb_id}/settings", operation_id="knowledge_get_settings", summary="查看知识库检索与分段设置")
def get_settings(kb_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/knowledge/{kb_id}/settings —— 读取检索与分段设置（含乐观锁 version）。

    副作用：历史知识库没有设置行时，本"读"接口会补建一行并 commit（幂等纳管）。
    异常：404 知识库不存在。
    """
    return kb_governance.get_profile(ctx.db, ctx.workspace_id, kb_id)


@router.patch("/{kb_id}/settings", operation_id="knowledge_update_settings", summary="版本化修改知识库检索与分段设置")
def update_settings(
    kb_id: str, payload: RetrievalSettingsUpdate,
    ctx: AuthContext = Depends(get_current_context),
):
    """PATCH /api/knowledge/{kb_id}/settings —— 版本化修改检索与分段设置。

    乐观锁：payload.version 不匹配返回 409（带 current_version）；
    校验顺序为 version → retrieval_mode → chunk_size/overlap → top_k → min_score → rerank_model，
    具体规则见 governance.update_profile。开启 rerank 时会校验所选模型确实支持重排。
    副作用：写 knowledge_profiles，审计 action=knowledge.settings_update。
    """
    row = kb_governance.update_profile(
        ctx.db, ctx.workspace_id, kb_id, version=payload.version,
        retrieval_mode=payload.retrieval_mode, top_k=payload.top_k,
        min_score=payload.min_score, chunk_size=payload.chunk_size,
        overlap=payload.overlap, embedding_model=payload.embedding_model,
        rerank_enabled=payload.rerank_enabled, rerank_model=payload.rerank_model,
    )
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.settings_update", resource_type="knowledge_base", resource_id=kb_id,
    )
    ctx.db.commit()
    return kb_governance.profile_out(row)


@router.get("/{kb_id}/segments", operation_id="knowledge_list_segments", summary="分页查看文本与问答分段")
def list_segments(
    kb_id: str,
    q: str = Query(default="", max_length=200),
    segment_type: Literal["text", "qa"] | None = None,
    enabled: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/knowledge/{kb_id}/segments —— 分页查看文本与问答分段。

    参数：q 同时匹配正文/问题/答案（≤200 字）；segment_type 与 enabled 不传即不过滤；
        page_size 上限 200。返回 {items, total, page, page_size}，total 是过滤后的总数。
    副作用：会补建缺失的分段元数据，因此本读接口可能写库并 commit。
    异常：404 知识库不存在。
    """
    return kb_governance.list_segments(
        ctx.db, ctx.workspace_id, kb_id, ctx.user_id,
        query=q, segment_type=segment_type, enabled=enabled,
        page=page, page_size=page_size,
    )


@router.post("/{kb_id}/segments", status_code=201, operation_id="knowledge_create_segment", summary="新增文本或问答分段")
def create_segment(
    kb_id: str, payload: SegmentInput,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/knowledge/{kb_id}/segments —— 新增一个手工分段（text 或 qa），201 返回该分段。

    创建时会尝试向量化并写搜索索引；向量失败不会让请求失败，分段会以
    embedding_status="failed" 落库并只能被关键词检索命中（见 governance._index_segment）。
    副作用：可能创建"手工分段"内部容器文件；写 knowledge_chunks/knowledge_chunk_metadata，
        审计 action=knowledge.segment_create。
    异常：422 分段内容不合法（如 qa 缺问题或答案）。
    """
    chunk, metadata = kb_governance.create_segment(
        ctx.db, ctx.workspace_id, kb_id, ctx.user_id,
        **payload.model_dump(),
    )
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.segment_create", resource_type="knowledge_chunk", resource_id=chunk.id,
        metadata={"knowledge_base_id": kb_id, "segment_type": metadata.segment_type},
    )
    ctx.db.commit()
    return {
        "id": chunk.id, "segment_type": metadata.segment_type,
        "text": chunk.text if metadata.segment_type == "text" else "",
        "question": metadata.question, "answer": metadata.answer,
        "enabled": metadata.enabled, "source": metadata.source,
        "version": metadata.version,
    }


@router.post("/{kb_id}/segments/batch", status_code=201, operation_id="knowledge_batch_create_segments", summary="批量新增最多五百个知识分段")
def batch_create_segments(
    kb_id: str, payload: SegmentBatch,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/knowledge/{kb_id}/segments/batch —— 批量新增最多 500 个手工分段，201 返回数量与 id 列表。

    逐个创建、逐个向量化：任一条内容非法会整体 422 并回滚（审计也随之不落地）。
    副作用：写库与索引，审计 action=knowledge.segment_batch_create（只记 count，不逐条记）。
    """
    rows = kb_governance.create_segments(
        ctx.db, ctx.workspace_id, kb_id, ctx.user_id,
        [item.model_dump() for item in payload.items], source="manual",
    )
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.segment_batch_create", resource_type="knowledge_base", resource_id=kb_id,
        metadata={"count": len(rows)},
    )
    ctx.db.commit()
    return {"count": len(rows), "ids": [chunk.id for chunk, _metadata in rows]}


@router.post("/{kb_id}/segments/import", status_code=201, operation_id="knowledge_import_segments", summary="导入 JSON、CSV 或纯文本知识分段")
async def import_segments(
    kb_id: str,
    file: UploadFile = FileForm(...),
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/knowledge/{kb_id}/segments/import —— 上传文件批量导入分段（JSON / CSV / 纯文本）。

    按文件名后缀分派（不看 content-type，因为浏览器上报的 MIME 对 csv/json 不可靠）：
        .json → 顶层数组或 {"items": [...]}；.csv → 表头必须与 SegmentInput 字段名一致；
        其它 → 按空行分段，每段作为一个 text 分段（enabled 默认 True）。
    分层校验：先限 2 MB、再要求 UTF-8（含 BOM 兼容），最后逐条交给 SegmentInput 校验。
    异常：422 文件过大、非 UTF-8、或字段/格式不符合 SegmentInput。
    副作用：同批量新增，审计 action=knowledge.segment_import（记文件名与条数）。
    """
    data = await file.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValidationError("知识分段导入文件不能超过 2 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("导入文件必须使用 UTF-8 编码") from exc
    name = (file.filename or "").lower()
    try:
        if name.endswith(".json"):
            parsed = json.loads(text)
            raw_items = parsed if isinstance(parsed, list) else parsed.get("items", [])
        elif name.endswith(".csv"):
            raw_items = list(csv.DictReader(io.StringIO(text)))
        else:
            raw_items = [
                {"segment_type": "text", "text": item.strip(), "enabled": True}
                for item in text.replace("\r\n", "\n").split("\n\n") if item.strip()
            ]
        items = [SegmentInput.model_validate(item).model_dump() for item in raw_items]
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("无法解析分段导入文件，请检查字段和格式") from exc
    rows = kb_governance.create_segments(
        ctx.db, ctx.workspace_id, kb_id, ctx.user_id, items, source="import",
    )
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.segment_import", resource_type="knowledge_base", resource_id=kb_id,
        metadata={"file_name": file.filename or "", "count": len(rows)},
    )
    ctx.db.commit()
    return {"count": len(rows), "ids": [chunk.id for chunk, _metadata in rows]}


@router.get("/{kb_id}/segments/export", operation_id="knowledge_export_segments", summary="导出当前知识分段 CSV")
def export_segments(kb_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/knowledge/{kb_id}/segments/export —— 导出该知识库全部分段为 CSV 附件。

    权限：显式要求 knowledge.export（本模块唯一的分段导出权限点，导出会把知识内容带出系统）。
    实现细节：page_size=100000 一次性取全量（导出语义要求完整，不分页）；
        内容前缀 BOM 以便 Excel 正确识别 UTF-8；Cache-Control: no-store 防止被中间层缓存。
    异常：404 知识库不存在。
    """
    require_permission(ctx, "knowledge.export")
    result = kb_governance.list_segments(
        ctx.db, ctx.workspace_id, kb_id, ctx.user_id, page=1, page_size=100000,
    )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["分段ID", "类型", "文本", "问题", "答案", "启用", "来源", "文件"])
    for item in result["items"]:
        writer.writerow([
            item["id"], item["segment_type"], item["text"], item["question"], item["answer"],
            "是" if item["enabled"] else "否", item["source"], item["file_name"],
        ])
    return Response(
        content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=knowledge-segments.csv", "Cache-Control": "no-store"},
    )


@router.patch("/{kb_id}/segments/{chunk_id}", operation_id="knowledge_update_segment", summary="版本化修改知识分段")
def update_segment(
    kb_id: str, chunk_id: str, payload: SegmentUpdate,
    ctx: AuthContext = Depends(get_current_context),
):
    """PATCH /api/knowledge/{kb_id}/segments/{chunk_id} —— 版本化修改分段内容与启停。

    乐观锁：payload.version 不匹配返回 409；内容变更会重新向量化并更新索引
    （向量失败不阻断，置 embedding_status="failed"）。
    副作用：写分块与元数据、更新知识库时间、尝试写 ES，审计 action=knowledge.segment_update。
    异常：409 版本冲突；404 分段或来源文档不存在；422 内容非法。
    """
    chunk, metadata = kb_governance.update_segment(
        ctx.db, ctx.workspace_id, kb_id, chunk_id, ctx.user_id,
        **payload.model_dump(),
    )
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.segment_update", resource_type="knowledge_chunk", resource_id=chunk.id,
        metadata={"knowledge_base_id": kb_id},
    )
    ctx.db.commit()
    return {
        "id": chunk.id, "segment_type": metadata.segment_type,
        "text": chunk.text if metadata.segment_type == "text" else "",
        "question": metadata.question, "answer": metadata.answer,
        "enabled": metadata.enabled, "source": metadata.source,
        "version": metadata.version,
    }


@router.delete("/{kb_id}/segments/{chunk_id}", operation_id="knowledge_delete_segment", summary="删除单个知识分段")
def delete_segment(
    kb_id: str, chunk_id: str, ctx: AuthContext = Depends(get_current_context),
):
    """DELETE /api/knowledge/{kb_id}/segments/{chunk_id} —— 删除单个分段，成功返回 {"ok": true}。

    会同时清理 SQL 分块/元数据与对应的 ES 文档（ES 清理失败只记日志）。
    手工分段的 ordinal 不会被回收，删除后再新增仍从最大值 +1，保证历史引用不歧义。
    异常：404 分段不存在。
    """
    kb_governance.delete_segment(ctx.db, ctx.workspace_id, kb_id, chunk_id, ctx.user_id)
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.segment_delete", resource_type="knowledge_chunk", resource_id=chunk_id,
        metadata={"knowledge_base_id": kb_id},
    )
    ctx.db.commit()
    return {"ok": True}


@router.post("/{kb_id}/sync", operation_id="knowledge_sync_files", summary="从文件分类同步并可重建知识索引")
def sync_files(
    kb_id: str, payload: KnowledgeSyncRequest,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/knowledge/{kb_id}/sync —— 从文件分类/指定文件同步进知识库，返回同步运行记录。

    权限：显式要求 knowledge.execute（批量导入会消耗解析与向量化资源，且会改写库内容）。
    同步是逐文件独立提交的：单个文件失败不影响其它文件，最终 status 为 completed 或 partial，
    error_summary 里给出逐文件错误（详见 governance.sync_files）。
    异常：404 知识库或分类不存在（在创建运行记录之前就报错，不会留下 running 记录）。
    """
    require_permission(ctx, "knowledge.execute")
    row = kb_governance.sync_files(
        ctx.db, ctx.workspace_id, kb_id, ctx.user_id,
        category_id=payload.category_id, file_ids=payload.file_ids, reindex=payload.reindex,
    )
    audit(
        ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
        action="knowledge.sync", resource_type="knowledge_base", resource_id=kb_id,
        metadata={"sync_run_id": row.id, "status": row.status},
    )
    ctx.db.commit()
    return kb_governance.sync_run_out(row)


@router.get("/{kb_id}/sync-runs", operation_id="knowledge_list_sync_runs", summary="查看知识同步运行记录")
def sync_runs(
    kb_id: str, limit: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/knowledge/{kb_id}/sync-runs —— 查看同步运行历史，最近 limit 条（默认 50，上限 200）。

    只读；会先校验知识库归属。异常：404 知识库不存在。
    """
    items = kb_governance.list_sync_runs(ctx.db, ctx.workspace_id, kb_id, limit)
    return {"items": items, "count": len(items)}


@router.get("/{kb_id}/role-grants", operation_id="knowledge_list_role_grants", summary="查看知识库角色授权")
def list_role_grants(kb_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/knowledge/{kb_id}/role-grants —— 查看该知识库的角色级授权列表。

    权限：显式要求 system.permissions.view（授权信息属于系统权限面，比知识库本身更敏感）。
    只查 subject_type=="role" 的授权；角色已被删除时 role_name 回落为"角色已删除"。
    异常：404 知识库不存在。
    """
    require_permission(ctx, "system.permissions.view")
    kb_service.get_kb(ctx.db, ctx.workspace_id, kb_id)
    rows = ctx.db.execute(
        select(ResourceGrant, Role)
        .outerjoin(Role, Role.id == ResourceGrant.subject_id)
        .where(
            ResourceGrant.workspace_id == ctx.workspace_id,
            ResourceGrant.resource_type == "knowledge",
            ResourceGrant.resource_id == kb_id,
            ResourceGrant.subject_type == "role",
        )
        .order_by(ResourceGrant.created_at.desc())
    ).all()
    return {"items": [{
        "id": grant.id, "role_id": grant.subject_id,
        "role_name": role.name if role else "角色已删除",
        "actions": grant.actions, "version": grant.version,
        "created_at": grant.created_at.isoformat() if grant.created_at else None,
    } for grant, role in rows], "count": len(rows)}


@router.post("/{kb_id}/role-grants", status_code=201, operation_id="knowledge_create_role_grant", summary="向工作区角色授予知识库操作")
def create_role_grant(
    kb_id: str, payload: KnowledgeRoleGrant,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/knowledge/{kb_id}/role-grants —— 给工作区角色授予知识库操作权限，201 返回授权。

    先查重再创建：同一 (知识库, 角色) 只允许一条授权，重复时 409 并提示"请先撤销后重新设置"。
    actions 会去重排序后落库。真正的鉴权与写入交给 admin_service.create_grant
    （它会做管理员校验与授权审计），本接口只负责资源类型固定为 "knowledge"。
    异常：409 该角色已授权；403 无管理员权限（由 admin_service 抛出）。
    """
    existing = ctx.db.scalar(select(ResourceGrant).where(
        ResourceGrant.workspace_id == ctx.workspace_id,
        ResourceGrant.resource_type == "knowledge",
        ResourceGrant.resource_id == kb_id,
        ResourceGrant.subject_type == "role",
        ResourceGrant.subject_id == payload.role_id,
    ))
    if existing is not None:
        raise ConflictError("该角色已获得此知识库授权，请先撤销后重新设置")
    row = admin_service.create_grant(ctx, ResourceGrantCreate(
        resource_type="knowledge", resource_id=kb_id,
        subject_type="role", subject_id=payload.role_id,
        actions=sorted(set(payload.actions)),
    ))
    return {"id": row.id, "role_id": row.subject_id, "actions": row.actions, "version": row.version}


@router.delete("/{kb_id}/role-grants/{grant_id}", operation_id="knowledge_delete_role_grant", summary="撤销知识库角色授权")
def delete_role_grant(
    kb_id: str, grant_id: str, ctx: AuthContext = Depends(get_current_context),
):
    """DELETE /api/knowledge/{kb_id}/role-grants/{grant_id} —— 撤销知识库角色授权。

    先做归属校验（grant_id 必须属于该工作区、该知识库且主体是角色），再委托 admin_service 删除。
    异常：404 授权不存在或不属于该知识库。
    """
    row = ctx.db.scalar(select(ResourceGrant).where(
        ResourceGrant.id == grant_id,
        ResourceGrant.workspace_id == ctx.workspace_id,
        ResourceGrant.resource_type == "knowledge",
        ResourceGrant.resource_id == kb_id,
        ResourceGrant.subject_type == "role",
    ))
    if row is None:
        raise NotFoundError("知识库角色授权不存在")
    admin_service.delete_grant(ctx, grant_id)
    return {"ok": True}


@router.patch("/{kb_id}")
def update_kb(
    kb_id: str, payload: KBUpdate, ctx: AuthContext = Depends(get_current_context)
):
    """PATCH /api/knowledge/{kb_id} —— 修改知识库名称/描述，返回最新详情。

    两项都可省略（None 表示不改）；名称 strip 后为空返回 422。
    与 create_kb 相同，这里是"先提交业务、再写审计并二次提交"的既有模式。
    副作用：写 knowledge_bases，审计 action=knowledge.update。
    """
    kb_service.update_kb(
        ctx.db,
        workspace_id=ctx.workspace_id,
        kb_id=kb_id,
        name=payload.name,
        description=payload.description,
    )
    ctx.db.commit()
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="knowledge.update",
        resource_type="knowledge_base",
        resource_id=kb_id,
    )
    ctx.db.commit()
    return kb_service.kb_detail(ctx.db, workspace_id=ctx.workspace_id, kb_id=kb_id)


@router.get("/{kb_id}/documents")
def list_documents(
    kb_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    file_ids: list[str] | None = Query(
        default=None,
        description="仅返回指定文件 ID 中已导入当前知识库的文档；可重复传参，也兼容逗号分隔",
    ),
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/knowledge/{kb_id}/documents —— 分页查询该知识库的已导入文档。

    file_ids 为可选筛选：可重复传参，也兼容逗号分隔的写法（由 service 统一归一化并去重，上限 500）。
    传了 file_ids 时响应会额外带 existing_file_ids，供文件选择器判断"哪些文件已导入"，
    避免因历史上同一文件存在多条文档行、分页被重复行占满而误判。
    返回 {items, count, total, page, page_size}；异常：404 知识库不存在。
    """
    return kb_service.list_documents(
        ctx.db, workspace_id=ctx.workspace_id, kb_id=kb_id,
        page=page, page_size=page_size, file_ids=file_ids,
    )


@router.post("/{kb_id}/documents/{document_id}/reindex")
def reindex_document(
    kb_id: str,
    document_id: str,
    payload: DocReindex,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/knowledge/{kb_id}/documents/{document_id}/reindex —— 重建该文档的索引。

    payload 为空对象（{}）时沿用文档现有解析指纹里的 chunk_size/overlap；
    显式传值则先校验分块参数。重建会重新解析、重新向量化并替换全部分块。
    顺序保证：源文件不存在会在删除旧索引之前报错，因此不会出现"旧的删了、新的建不出"的空窗。
    异常：404 文档不存在；422 参数非法或原始文件缺失。
    副作用：由 service.reindex_document 完成（含写库与读写 ES、自身 commit），
        审计 action=knowledge.reindex_document。
    """
    new_document_id = kb_service.reindex_document(
        ctx.db,
        workspace_id=ctx.workspace_id,
        kb_id=kb_id,
        document_id=document_id,
        chunk_size=payload.chunk_size,
        overlap=payload.overlap,
    )
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="knowledge.reindex_document",
        resource_type="knowledge_document",
        resource_id=new_document_id,
        metadata={"previous_document_id": document_id, "kb_id": kb_id},
    )
    ctx.db.commit()
    return {"document_id": new_document_id, "status": "ready"}


@router.delete("/{kb_id}/documents/{document_id}")
def remove_document(
    kb_id: str, document_id: str, ctx: AuthContext = Depends(get_current_context)
):
    """DELETE /api/knowledge/{kb_id}/documents/{document_id} —— 从知识库移除文档及其分块。

    只删知识库侧索引记录，不删资料库中的原始文件（用户可再次导入）。
    副作用：删分块/元数据/ES 文档、更新知识库时间，审计 action=knowledge.remove_document。
    异常：404 知识库或文档不存在。
    """
    kb_service.remove_document(
        ctx.db,
        workspace_id=ctx.workspace_id,
        kb_id=kb_id,
        document_id=document_id,
    )
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="knowledge.remove_document",
        resource_type="knowledge_document",
        resource_id=document_id,
        metadata={"kb_id": kb_id},
    )
    ctx.db.commit()
    return {"ok": True}


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, ctx: AuthContext = Depends(get_current_context)):
    """DELETE /api/knowledge/{kb_id} —— 删除知识库（软删除 + 清理其分块/文档/授权）。

    不会删除资料库中的原始文件；只有本模块创建的"手工分段"内部容器文件会被一并软删。
    ES 清理失败只记日志，不阻断删除。
    副作用：删除多张表数据 + ES 文档 + 授权记录，把知识库 status 置为 deleted，
        审计 action=knowledge.delete；service 内部已 commit。
    异常：404 知识库不存在。
    """
    kb_service.delete_kb(ctx.db, ctx.workspace_id, kb_id)
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="knowledge.delete",
        resource_type="knowledge_base",
        resource_id=kb_id,
    )
    ctx.db.commit()
    return {"ok": True}
