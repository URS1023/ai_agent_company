# -*- coding: utf-8 -*-
"""Knowledge base service: lifecycle, ingestion, indexing and retrieval."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...errors import ConflictError, NotFoundError, ValidationError
from ...ids import new_id
from ...models import File, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from ...providers.model import analyze_image, embed_texts
from .models import KnowledgeProfile
from ...modules.files.service import get_file
from ..files.extractor import UnsupportedDocumentError, extract_text
from ..files.vision import extract_visual_assets

logger = logging.getLogger("knowledge")

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 80
MIN_CHUNK_SIZE = 200
MAX_CHUNK_SIZE = 2000
MAX_CHUNK_OVERLAP = 400
PARSER_VERSION_RE = re.compile(
    r"^v[12]-c(?P<size>\d+)-o(?P<overlap>\d+)(?:-i(?P<images>\d+))?$"
)
# parser_version 是有版本号的解析指纹，含义：v<算法代次>-c<chunk_size>-o<overlap>-i<成功识图数>。
# 不带 -i 的是 v1 老格式（无图片信息）；parser_settings/parser_image_count 靠这个正则反解参数，
# 因此重建索引时能沿用"上次的分块大小与重叠"，不需要额外的历史表。改动编码格式会影响历史数据解读。


# ------------------------------------------------------------------
# Knowledge-base lifecycle
# ------------------------------------------------------------------
def create_kb(
    db: Session,
    *,
    workspace_id: str,
    owner_user_id: str,
    name: str,
    description: str = "",
    visibility: str = "workspace",
) -> KnowledgeBase:
    """新建知识库，返回已 flush（未 commit）的 KnowledgeBase 行。

    名称会 strip，空名报 422。新库固定 status="active"、可见性取调用方传入值。
    异常：ValidationError —— 名称为空。
    副作用：写 knowledge_bases；不 commit，调用方负责事务边界。
    注意：本函数不建对应的 KnowledgeProfile（设置行），由 governance.ensure_profile 惰性补建。
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("知识库名称不能为空")
    row = KnowledgeBase(
        id=new_id(),
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        name=clean_name,
        description=description.strip(),
        visibility=visibility,
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def list_kbs(db: Session, workspace_id: str) -> list[KnowledgeBase]:
    """列出工作区内生效的知识库（只含 status=="active"），按更新时间倒序。

    软删除（status=="deleted"）的库不出现；没有分页，按工作区级小集合对待。
    """
    return db.execute(
        select(KnowledgeBase)
        .where(
            KnowledgeBase.workspace_id == workspace_id,
            KnowledgeBase.status == "active",
        )
        .order_by(KnowledgeBase.updated_at.desc())
    ).scalars().all()


def get_kb(db: Session, workspace_id: str, kb_id: str) -> KnowledgeBase:
    """取本工作区的知识库，是本模块最基础的权限边界校验（检索/入库/设置都必须先过它）。

    与 File 不同，知识库用的是 status 软删除标记：status=="deleted" 视为不存在。
    异常：NotFoundError —— 不存在、不属于该工作区或已删除，统一文案避免跨工作区探测。
    """
    row = db.get(KnowledgeBase, kb_id)
    if not row or row.workspace_id != workspace_id or row.status == "deleted":
        raise NotFoundError("知识库不存在")
    return row


def update_kb(
    db: Session,
    *,
    workspace_id: str,
    kb_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> KnowledgeBase:
    """更新知识库名称与描述（只支持这两项）；None 表示不修改。

    名称 strip 后为空会 422。可见性、所有者等不在本函数范围内（改动需要走权限流程）。
    异常：NotFoundError（知识库不存在）、ValidationError（名称为空）。
    副作用：写 knowledge_bases.name/description/updated_at；不 commit。
    """
    row = get_kb(db, workspace_id, kb_id)
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("知识库名称不能为空")
        row.name = clean_name
    if description is not None:
        row.description = description.strip()
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def kb_detail(db: Session, *, workspace_id: str, kb_id: str) -> dict:
    """单个知识库的详情视图（含文档状态计数、分块数、生效的嵌入/视觉模型名）。"""
    return _kb_to_dict(db, get_kb(db, workspace_id, kb_id))


def list_kb_details(db: Session, workspace_id: str) -> list[dict]:
    """知识库列表视图（列表页专用），带文档状态计数、分块数与模型信息。

    为列表页做了批量聚合而不是逐个库查询：文档状态计数与分块数各用一条 group by 拿到，
    再用 dict 按 kb_id 组装，避免 N 个知识库产生 2N 次查询。
    profiles 一次性查出与该工作区所有知识库相关的设置行（可能缺失，_embedding_metadata 会兜底）。
    vision_model 是工作区级配置，取一次复用给所有条目。
    """
    knowledge_bases = list_kbs(db, workspace_id)
    if not knowledge_bases:
        return []
    kb_ids = [row.id for row in knowledge_bases]
    document_rows = db.execute(
        select(
            KnowledgeDocument.knowledge_base_id,
            KnowledgeDocument.status,
            func.count(KnowledgeDocument.id),
        )
        .where(
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.knowledge_base_id.in_(kb_ids),
        )
        .group_by(
            KnowledgeDocument.knowledge_base_id,
            KnowledgeDocument.status,
        )
    ).all()
    status_by_kb: dict[str, dict[str, int]] = {}
    for kb_id, status, count in document_rows:
        status_by_kb.setdefault(kb_id, {})[status] = int(count)
    chunk_rows = db.execute(
        select(
            KnowledgeChunk.knowledge_base_id,
            func.count(KnowledgeChunk.id),
        )
        .where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.knowledge_base_id.in_(kb_ids),
        )
        .group_by(KnowledgeChunk.knowledge_base_id)
    ).all()
    chunks_by_kb = {kb_id: int(count) for kb_id, count in chunk_rows}
    vision_model = _effective_model_names(workspace_id)[1]
    profiles = {
        row.knowledge_base_id: row
        for row in db.scalars(select(KnowledgeProfile).where(
            KnowledgeProfile.workspace_id == workspace_id,
            KnowledgeProfile.knowledge_base_id.in_(kb_ids),
        )).all()
    }
    items = []
    for row in knowledge_bases:
        embedding_profile, embedding_model, embedding_provider = _embedding_metadata(
            workspace_id, profiles.get(row.id),
        )
        items.append(_serialize_kb(
            row,
            status_counts=status_by_kb.get(row.id, {}),
            chunk_count=chunks_by_kb.get(row.id, 0),
            embedding_profile=embedding_profile,
            embedding_model=embedding_model,
            embedding_provider=embedding_provider,
            vision_model=vision_model,
        ))
    return items


def _kb_to_dict(db: Session, kb: KnowledgeBase) -> dict:
    """单库详情：现查文档状态计数与分块数，再委托 _serialize_kb 组装视图。

    与 list_kb_details 的批量版本共用 _serialize_kb，保证详情与列表的字段口径一致
    （前端两个页面复用同一套渲染逻辑）。
    """
    status_rows = db.execute(
        select(KnowledgeDocument.status, func.count(KnowledgeDocument.id))
        .where(
            KnowledgeDocument.workspace_id == kb.workspace_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
        )
        .group_by(KnowledgeDocument.status)
    ).all()
    status_counts = {status: int(count) for status, count in status_rows}
    chunk_count = db.execute(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.workspace_id == kb.workspace_id,
            KnowledgeChunk.knowledge_base_id == kb.id,
        )
    ).scalar_one()
    embedding_profile, embedding_model, embedding_provider = _embedding_metadata(
        kb.workspace_id, db.scalar(select(KnowledgeProfile).where(
            KnowledgeProfile.workspace_id == kb.workspace_id,
            KnowledgeProfile.knowledge_base_id == kb.id,
        )),
    )
    vision_model = _effective_model_names(kb.workspace_id)[1]
    return _serialize_kb(
        kb,
        status_counts=status_counts,
        chunk_count=int(chunk_count or 0),
        embedding_profile=embedding_profile,
        embedding_model=embedding_model,
        embedding_provider=embedding_provider,
        vision_model=vision_model,
    )


def _serialize_kb(
    kb: KnowledgeBase,
    *,
    status_counts: dict[str, int],
    chunk_count: int,
    embedding_profile: str,
    embedding_model: str,
    embedding_provider: str,
    vision_model: str,
) -> dict:
    """知识库视图的统一组装点（详情页与列表页共用，字段口径只在这里定义一次）。

    ready_document_count / processing_document_count / failed_document_count 由 status_counts 抽取，
    未出现的状态取 0；document_count 是三者之和（即该库已导入的文档总数）。
    时间统一 isoformat，空值下发 null；vision_enabled 是全局开关，供前端决定是否显示识图相关提示。
    """
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "visibility": kb.visibility,
        "status": kb.status,
        "document_count": sum(status_counts.values()),
        "ready_document_count": status_counts.get("ready", 0),
        "processing_document_count": status_counts.get("processing", 0),
        "failed_document_count": status_counts.get("failed", 0),
        "chunk_count": chunk_count,
        "embedding_profile": embedding_profile,
        "embedding_model": embedding_model,
        "embedding_provider": embedding_provider,
        "vision_model": vision_model,
        "vision_enabled": settings.VISION_ENABLED,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
    }


def _effective_model_names(workspace_id: str) -> tuple[str, str]:
    """返回当前工作区实际生效的 (嵌入模型名, 视觉模型名)，用于前端展示。

    读的是模型配置表（_profile_config），任一项读取失败（未配置/表缺失/字段缺失）就回落到
    settings 里的全局默认模型名。这里刻意不抛异常：知识库列表必须能在模型配置不完整时照常打开。
    """
    from ...providers.model import _profile_config

    try:
        embedding = _profile_config("embedding", workspace_id=workspace_id)["model"]
    except Exception:
        embedding = settings.SILICONFLOW_EMBED_MODEL
    try:
        vision = _profile_config("vision", workspace_id=workspace_id)["model"]
    except Exception:
        vision = settings.SILICONFLOW_VISION_MODEL
    return embedding, vision


def _embedding_metadata(workspace_id: str, profile: KnowledgeProfile | None) -> tuple[str, str, str]:
    """Return the selected profile and effective provider model for a KB view."""
    from ...providers.model import _embedding_config

    selected = (profile.embedding_model if profile else "") or "embedding"
    selected = selected.strip() or "embedding"
    try:
        resolved_profile, config = _embedding_config(selected, workspace_id=workspace_id)
        return resolved_profile, str(config.get("model") or selected), str(config.get("provider") or "")
    except Exception:
        # A settings page must remain readable when a newly selected profile is
        # temporarily unavailable; chunk metadata still records the provider
        # used by each successful indexing operation.
        return selected, selected, ""


def _embedding_selection(db: Session, workspace_id: str, kb_id: str) -> str:
    """读某知识库选定的嵌入配置名；没有 profile 行时用默认名 "embedding"。

    入库与手工分段索引都用它决定"用哪个嵌入配置"，因此历史知识库（无 profile）与新建知识库
    走的是同一条默认路径，不会出现行为分叉。
    """
    profile = db.scalar(select(KnowledgeProfile).where(
        KnowledgeProfile.workspace_id == workspace_id,
        KnowledgeProfile.knowledge_base_id == kb_id,
    ))
    return ((profile.embedding_model if profile else "") or "embedding").strip() or "embedding"


# ------------------------------------------------------------------
# Documents and ingestion
# ------------------------------------------------------------------
def validate_chunking(chunk_size: int, overlap: int) -> tuple[int, int]:
    """校验分块参数并原样返回 (chunk_size, overlap)，是入库与知识库设置共用的唯一规则来源。

    规则：chunk_size ∈ [MIN_CHUNK_SIZE, MAX_CHUNK_SIZE]（200~2000）；overlap ∈ [0, MAX_CHUNK_OVERLAP]
    （0~400）；且 overlap < chunk_size（否则切块循环可能原地打转）。
    入库、重建索引、governance.update_profile 三处都调用它，避免"设置里能保存、入库却失败"。
    异常：ValidationError —— 任一条件不满足，文案直接面向用户。
    """
    if chunk_size < MIN_CHUNK_SIZE or chunk_size > MAX_CHUNK_SIZE:
        raise ValidationError(
            f"分块大小必须在 {MIN_CHUNK_SIZE} 到 {MAX_CHUNK_SIZE} 字符之间"
        )
    if overlap < 0 or overlap > MAX_CHUNK_OVERLAP:
        raise ValidationError(f"重叠字符数必须在 0 到 {MAX_CHUNK_OVERLAP} 之间")
    if overlap >= chunk_size:
        raise ValidationError("重叠字符数必须小于分块大小")
    return chunk_size, overlap


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries."""
    # 中文说明：切块是"向量检索质量"的第一道决定因素，这里的策略是：
    #   1. 先把 3 个以上连续换行压成 1 个空行（消除解析器带来的大量空行噪声），再 strip；
    #   2. 窗口大小为 max_chars，尽量在窗口后 35% 区间内回退到段落/句末边界（\n 、。；.），
    #      避免把一句话从中间截断；找不到边界就按硬边界切；
    #   3. 下一块起点回退 overlap 个字符形成重叠，保证跨块语义不被割裂。
    # 边界保护：若 end - overlap 不大于 start（overlap 很大或边界紧贴起点），则用 end 作为
    # 下一块起点，确保 start 严格递增、循环必然终止。
    # 返回已 strip 的非空块；空输入返回 []。参数非法时由 validate_chunking 抛 ValidationError。
    validate_chunking(max_chars, overlap)
    clean_text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not clean_text:
        return []
    chunks: List[str] = []
    start = 0
    text_length = len(clean_text)
    while start < text_length:
        hard_end = min(start + max_chars, text_length)
        end = hard_end
        if hard_end < text_length:
            # Prefer a paragraph/newline boundary in the final third of the window.
            floor = start + int(max_chars * 0.65)
            newline = clean_text.rfind("\n", floor, hard_end)
            sentence = max(
                clean_text.rfind("。", floor, hard_end),
                clean_text.rfind("；", floor, hard_end),
                clean_text.rfind(".", floor, hard_end),
            )
            boundary = max(newline, sentence)
            if boundary > start:
                end = boundary + 1
        chunk = clean_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        next_start = end - overlap
        start = next_start if next_start > start else end
    return chunks


def _parser_version(chunk_size: int, overlap: int, image_count: int = 0) -> str:
    """构造解析指纹字符串（PARSER_VERSION_RE 的逆运算），入库成功后被写入文档行。

    必须与 PARSER_VERSION_RE 严格对应，否则 parser_settings 反解不到参数、重建索引会退回默认值。
    image_count 用 max(0, ...) 兜底防负数，写成 -i0 也仍能被正则解析。
    """
    return f"v2-c{chunk_size}-o{overlap}-i{max(0, image_count)}"


def parser_settings(parser_version: Optional[str]) -> tuple[int, int]:
    """从解析指纹反解出 (chunk_size, overlap)，无法识别时回落到全局默认值。

    回落是必要的：v1 老格式或手工分段（"manual-v1"）都不匹配正则，重建索引时用默认参数
    比报错更合理——报错会让这些历史文档完全无法重建。
    """
    match = PARSER_VERSION_RE.match(parser_version or "")
    if not match:
        return DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP
    return int(match.group("size")), int(match.group("overlap"))


def parser_image_count(parser_version: Optional[str]) -> int:
    """从解析指纹里取出成功识图的数量；v1 指纹（无 -i 段）与非法输入都返回 0。

    只用于列表页展示"这次入库识别了几张图"，不参与任何计算。
    """
    match = PARSER_VERSION_RE.match(parser_version or "")
    if not match or not match.group("images"):
        return 0
    return int(match.group("images"))


def add_document(
    db: Session,
    *,
    workspace_id: str,
    kb_id: str,
    file_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    _reindex_document_id: Optional[str] = None,
) -> str:
    """Import once, or replace chunks only after a full successful rebuild."""
    # 中文说明（不改动原说明）——这是知识库入库/重建的唯一入口，返回 document_id。
    #
    # 管线顺序：校验分块参数 → 校验知识库与文件归属 → 检查原始文件存在 → 加锁防并发入库
    #   → 文/图解析（extract_text + extract_visual_assets + analyze_image）
    #   → 按 location 归并同名块 → 切块 → 批量嵌入 → 逐块写 SQL 与 ES → 刷新 ES
    #   → 一次性切换新旧分块 → 清理旧 ES 文档。
    #
    # 事务与降级语义（重要）：
    #   - 旧分块删除与新分块插入在同一个 commit 里完成，外部索引清理放在 commit 之后；
    #     因此重建失败时旧索引仍完整可用（不会出现"查不到任何内容"的空窗）。
    #   - ES 只是加速层：_index_chunk_es 失败不抛异常，只把该块计入 search_index_failures，
    #     文档依然置为 ready，并把"已启用关键词回退检索"写进 error_summary 告知用户。
    #   - 嵌入向量失败是硬失败：数量不一致、空向量、维度不一致都会抛 RuntimeError 并回滚整次入库
    #     （没有向量的分块会让语义检索永远召不回，静默放过比失败更危险）。
    #   - 失败分支会把文档状态恢复为之前的 ready（重建失败则保留上次可用内容），
    #     只在原本不是 ready 时才置为 failed，并把原因前缀写入 error_summary。
    #
    # 参数：_reindex_document_id 仅供 reindex_document 内部使用，用于指定要重建的那一行；
    #   不传时若已存在 ready 文档则直接返回其 id（天然幂等，重复导入不会产生重复文档）。
    # 异常：ValidationError（参数/文件内容/原始文件缺失）、ConflictError（同文件正在建立索引）、
    #   NotFoundError（指定的文档不存在）、RuntimeError（向量服务异常）以及原始解析异常。
    # 副作用：读原文件与图片、调用嵌入与视觉模型（计费）、写 knowledge_documents/chunks 与
    #   knowledge_bases.updated_at、写 ES 索引、可能删除旧 ES 文档；本函数自行 commit。
    chunk_size, overlap = validate_chunking(chunk_size, overlap)
    kb = get_kb(db, workspace_id, kb_id)
    file_row = get_file(db, workspace_id, file_id)
    embedding_profile = _embedding_selection(db, workspace_id, kb_id)
    from ...common.storage import get_storage

    path = get_storage().get_path(file_row.storage_key)
    if not get_storage().exists(file_row.storage_key):
        raise ValidationError("原始文件不存在，无法建立索引")
    # Serialize the check/create transition on a persisted parent, including
    # first imports where there is no document row to lock yet.
    db.scalar(select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id, KnowledgeBase.workspace_id == workspace_id,
    ).with_for_update())
    docs = db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.workspace_id == workspace_id,
        KnowledgeDocument.knowledge_base_id == kb_id,
        KnowledgeDocument.file_id == file_id,
    ).order_by(KnowledgeDocument.created_at.desc()).with_for_update()
        .execution_options(populate_existing=True)).all()
    if any(row.status == "processing" for row in docs):
        raise ConflictError("该文件正在建立索引，请等待完成后再试")
    if _reindex_document_id:
        doc = next((row for row in docs if row.id == _reindex_document_id), None)
        if doc is None:
            raise NotFoundError("知识文档不存在，请刷新列表")
    else:
        ready_doc = next((row for row in docs if row.status == "ready"), None)
        if ready_doc is not None:
            return ready_doc.id
        doc = docs[0] if docs else None
    previous_status = doc.status if doc else None
    previous_parser = doc.parser_version if doc else None
    document_id = doc.id if doc else new_id()
    now = datetime.utcnow()
    if doc is None:
        doc = KnowledgeDocument(
            id=document_id, workspace_id=workspace_id, knowledge_base_id=kb_id,
            file_id=file_id, status="processing",
            parser_version=_parser_version(chunk_size, overlap),
            error_summary=None, created_at=now, updated_at=now,
        )
        db.add(doc)
    else:
        doc.status = "processing"
        doc.error_summary = None
        doc.updated_at = now
    kb.updated_at = now
    db.commit()
    indexed_es_ids: list[str] = []

    try:
        previous_chunks = db.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.document_id == document_id,
        )).all()
        try:
            blocks = extract_text(file_row.name, file_row.mime_type, path)
        except UnsupportedDocumentError as exc:
            raise ValidationError(str(exc)) from exc
        except ValueError as exc:
            raise ValidationError(
                "无法解析文件内容。请确认文件能够正常打开、扩展名与实际格式一致；"
                "加密文件请先解除密码保护，或重新导出后上传。"
            ) from exc
        visual_assets, vision_warnings = extract_visual_assets(
            file_row.name,
            file_row.mime_type,
            path,
        )
        recognized_images = 0
        for image_index, asset in enumerate(visual_assets):
            location = dict(asset.get("location") or {})
            context_parts = [f"文件：{file_row.name}"]
            if location.get("slide") is not None:
                context_parts.append(f"幻灯片：{int(location['slide']) + 1}")
            elif location.get("page") is not None:
                context_parts.append(f"页码：{int(location['page']) + 1}")
            if location.get("sheet"):
                context_parts.append(f"工作表：{location['sheet']}")
            if location.get("cell"):
                context_parts.append(f"锚点单元格：{location['cell']}")
            try:
                vision_result = analyze_image(
                    asset["data"],
                    asset["mime_type"],
                    context="；".join(context_parts),
                    workspace_id=workspace_id,
                )
            except Exception as exc:
                warning = (
                    f"图片 {image_index + 1} 识别失败："
                    f"{type(exc).__name__}: {str(exc)[:240]}"
                )
                vision_warnings.append(warning)
                logger.warning(
                    "vision analysis failed workspace=%s file=%s image=%s: %s",
                    workspace_id,
                    file_id,
                    image_index,
                    exc,
                )
                continue
            location.update(
                {
                    "source": "vision",
                    "visual_type": location.get("visual_type") or "document_image",
                    "image_index": image_index,
                    "width": asset.get("width"),
                    "height": asset.get("height"),
                    "vision_model": vision_result["model"],
                }
            )
            blocks.append(
                {
                    "text": (
                        f"图片识别结果（{asset.get('name') or f'图片 {image_index + 1}'}）\n"
                        f"{vision_result['content']}"
                    ),
                    **location,
                }
            )
            recognized_images += 1
        doc.parser_version = _parser_version(
            chunk_size, overlap, recognized_images
        )
        chunk_records: list[dict] = []
        grouped_blocks: list[dict] = []
        for block in blocks:
            block_text = str(block.get("text") or "").strip()
            if not block_text:
                continue
            location = {
                key: block[key]
                for key in (
                    "page",
                    "slide",
                    "sheet",
                    "table",
                    "cell",
                    "source",
                    "visual_type",
                    "image_index",
                    "width",
                    "height",
                    "vision_model",
                )
                if block.get(key) is not None
            }
            if grouped_blocks and grouped_blocks[-1]["location"] == location:
                grouped_blocks[-1]["text"] += "\n\n" + block_text
            else:
                grouped_blocks.append({"text": block_text, "location": location})
        for group in grouped_blocks:
            for chunk_value in chunk_text(
                group["text"], max_chars=chunk_size, overlap=overlap
            ):
                chunk_records.append(
                    {"text": chunk_value, "location": dict(group["location"])}
                )
        if not chunk_records:
            raise ValidationError("文件中没有可建立索引的文本内容")
        texts = [record["text"] for record in chunk_records]
        from ...providers.model import _embedding_config

        resolved_profile, embedding_config = _embedding_config(
            embedding_profile, workspace_id=workspace_id,
        )
        vectors = embed_texts(
            texts,
            workspace_id=workspace_id,
            profile_name=resolved_profile,
            usage_context={
                "source": "knowledge_ingestion",
                "source_id": document_id,
            },
        )
        if len(vectors) != len(texts):
            raise RuntimeError("向量服务返回数量与文本分块数量不一致")
        if not vectors or not vectors[0]:
            raise RuntimeError("向量服务未返回有效向量")
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("向量服务返回的维度不一致")
        actual_embedding_model = str(embedding_config.get("model") or resolved_profile)
        actual_embedding_provider = str(embedding_config.get("provider") or "")

        search_index_failures = 0
        for ordinal, (text, vector) in enumerate(zip(texts, vectors)):
            location = dict(chunk_records[ordinal]["location"])
            location["ordinal"] = ordinal
            chunk_id = new_id()
            chunk = KnowledgeChunk(
                id=chunk_id,
                workspace_id=workspace_id,
                knowledge_base_id=kb_id,
                document_id=document_id,
                ordinal=ordinal,
                text=text,
                location=location,
                embedding_status="done",
                embedding_model=actual_embedding_model,
                embedding_dimensions=dimensions,
                created_at=datetime.utcnow(),
            )
            db.add(chunk)
            db.flush()
            es_id = _index_chunk_es(
                chunk.id,
                text,
                {
                    "document_id": document_id,
                    "ordinal": ordinal,
                    "kb_id": kb_id,
                    "workspace_id": workspace_id,
                    "file_id": file_id,
                    "file_name": file_row.name,
                    "location": location,
                    "embedding_profile": resolved_profile,
                    "embedding_model": actual_embedding_model,
                    "embedding_provider": actual_embedding_provider,
                    "embedding_dimensions": dimensions,
                },
                vector,
            )
            if es_id:
                chunk.elasticsearch_document_id = es_id
                indexed_es_ids.append(es_id)
            else:
                search_index_failures += 1

        _refresh_index()
        doc.status = "ready"
        warnings = []
        if vision_warnings:
            warnings.append("识图警告：" + "；".join(vision_warnings))
        if search_index_failures:
            warnings.append(
                f"搜索索引暂不可用，{search_index_failures} 个分块已保存在数据库并启用关键词回退检索"
            )
        doc.error_summary = "；".join(warnings)[:2000] or None
        doc.updated_at = datetime.utcnow()
        kb.updated_at = doc.updated_at
        # Old and replacement SQL chunks switch in one commit. External index
        # deletion happens afterwards, so a failed rebuild preserves its source.
        previous_es_ids = _delete_chunk_rows(db, previous_chunks)
        db.commit()
        _delete_chunks_from_es(previous_es_ids)
        return document_id
    except Exception as exc:
        db.rollback()
        _delete_chunks_from_es(indexed_es_ids)
        failed_doc = db.get(KnowledgeDocument, document_id)
        if failed_doc:
            failed_doc.status = "ready" if previous_status == "ready" else "failed"
            if previous_parser:
                failed_doc.parser_version = previous_parser
            prefix = "重建未完成，已保留上次可用的知识内容。原因：" if previous_status == "ready" else ""
            failed_doc.error_summary = (prefix + str(exc))[:2000]
            failed_doc.updated_at = datetime.utcnow()
            failed_kb = db.get(KnowledgeBase, kb_id)
            if failed_kb:
                failed_kb.updated_at = failed_doc.updated_at
            db.commit()
        raise


def get_document(
    db: Session, *, workspace_id: str, kb_id: str, document_id: str
) -> KnowledgeDocument:
    """取知识库文档行，做三重归属校验（工作区 + 知识库 + 文档）。

    异常：NotFoundError —— 文档不存在或不属于该工作区/知识库。
    """
    row = db.get(KnowledgeDocument, document_id)
    if (
        not row
        or row.workspace_id != workspace_id
        or row.knowledge_base_id != kb_id
    ):
        raise NotFoundError("知识库文档不存在")
    return row


def list_documents(
    db: Session, *, workspace_id: str, kb_id: str,
    page: int = 1, page_size: int = 50, file_ids: Optional[list[str]] = None,
) -> dict:
    """分页列出知识库文档，返回 {items, count, total, page, page_size}（可带 existing_file_ids）。

    参数：page/page_size 必须为正，page_size 上限 200；file_ids 可选，用于"文件选择器"判断候选集
        是否已导入——支持重复参数与逗号分隔两种写法，去重后上限 500 个。
    返回：count 是本页条数，total 是过滤后的总数（两者都保留以兼容既有客户端的字段名）。
        file_row 缺失（文件已从资料库删除）时名称回落为提示文案、size 为 0、download_url 为 None。
        传了 file_ids 时额外返回 existing_file_ids（去重的已导入集合），避免历史上同一文件存在
        多条文档行时，因为分页被重复行占满而漏判"已导入"。
    异常：ValidationError —— 页码/每页数量非正，或去重后的文件 ID 超过 500。
    副作用：只读；但会调用 get_kb 做归属校验。
    """
    if page < 1 or page_size < 1:
        raise ValidationError("文档页码和每页数量必须为正数")
    page_size = min(int(page_size), 200)
    get_kb(db, workspace_id, kb_id)
    _, vision_model = _effective_model_names(workspace_id)
    # The optional file filter lets the file picker ask whether its current
    # candidate set is already imported without loading every document page.
    # IDs are normalized here so repeated query parameters and a comma
    # separated compatibility form have identical, bounded semantics.
    requested_file_ids: list[str] = []
    for value in file_ids or []:
        for part in str(value).split(","):
            normalized = part.strip()
            if normalized and normalized not in requested_file_ids:
                requested_file_ids.append(normalized)
            if len(requested_file_ids) > 500:
                raise ValidationError("文件筛选最多支持 500 个去重后的文件 ID")
    filters = [
        KnowledgeDocument.workspace_id == workspace_id,
        KnowledgeDocument.knowledge_base_id == kb_id,
    ]
    if requested_file_ids:
        filters.append(KnowledgeDocument.file_id.in_(requested_file_ids))
    total = int(db.scalar(select(func.count(KnowledgeDocument.id)).where(*filters)) or 0)
    rows = db.execute(
        select(KnowledgeDocument, File)
        .outerjoin(File, File.id == KnowledgeDocument.file_id)
        .where(*filters)
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    document_ids = [doc.id for doc, _file_row in rows]
    chunk_rows = db.execute(
        select(KnowledgeChunk.document_id, func.count(KnowledgeChunk.id))
        .where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.knowledge_base_id == kb_id,
            KnowledgeChunk.document_id.in_(document_ids) if document_ids else False,
        )
        .group_by(KnowledgeChunk.document_id)
    ).all()
    chunk_counts = {document_id: int(count) for document_id, count in chunk_rows}
    items = []
    for doc, file_row in rows:
        chunk_size, overlap = parser_settings(doc.parser_version)
        image_count = parser_image_count(doc.parser_version)
        # 软删除仍保留物理登记行，不能仅凭联表存在就给出必然 404 的下载链接。
        file_available = bool(file_row and file_row.deleted_at is None and file_row.status == "ready")
        items.append(
            {
                "id": doc.id,
                "file_id": doc.file_id,
                "name": file_row.name if file_available else "源文件已删除或不可用",
                "mime_type": file_row.mime_type if file_available else None,
                "size": file_row.size if file_available else 0,
                "status": doc.status,
                "parser_version": doc.parser_version,
                "chunk_size": chunk_size,
                "overlap": overlap,
                "image_count": image_count,
                "vision_model": vision_model,
                "vision_enabled": settings.VISION_ENABLED,
                "chunk_count": chunk_counts.get(doc.id, 0),
                "error_summary": doc.error_summary,
                "download_url": (
                    f"/api/files/{doc.file_id}/download" if file_available else None
                ),
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            }
        )
    result = {
        "items": items,
        # Keep the established response key for existing clients.  ``total``
        # is the full filtered count used for pagination; ``count`` remains
        # the number of rows in this page for backward compatibility.
        "count": len(items),
        "total": total,
        "page": page,
        "page_size": page_size,
    }
    if requested_file_ids:
        # A historical import may contain duplicate document rows for one
        # file.  Return the distinct membership set so a picker never loses a
        # candidate merely because document pagination was filled by repeats.
        result["existing_file_ids"] = list(db.scalars(
            select(KnowledgeDocument.file_id).where(*filters).distinct()
        ).all())
    return result


def remove_document(
    db: Session, *, workspace_id: str, kb_id: str, document_id: str
) -> None:
    """从知识库移除一个文档：删除其所有分块与元数据，并更新知识库时间。

    只删知识库侧记录，不动原始文件（资料库里的文件仍然存在，用户可再次导入）。
    副作用：删 knowledge_chunks/knowledge_chunk_metadata 行、删对应 ES 文档、更新 updated_at，
        并在函数内 commit。
    异常：NotFoundError —— 知识库或文档不存在。
    """
    kb = get_kb(db, workspace_id, kb_id)
    doc = get_document(
        db, workspace_id=workspace_id, kb_id=kb_id, document_id=document_id
    )
    _delete_document_rows(db, doc)
    kb.updated_at = datetime.utcnow()
    db.commit()


def reindex_document(
    db: Session,
    *,
    workspace_id: str,
    kb_id: str,
    document_id: str,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> str:
    """按新的分块参数重建某个知识库文档的索引，返回 document_id（通常与传入值相同）。

    参数缺省即沿用文档现有解析指纹里的参数（parser_settings 反解）；显式传值则先校验。
    关键安全顺序：先校验源文件仍然存在，再进入 add_document 的"重建"分支——否则一旦源文件缺失，
    旧索引已被删除而新索引建不出来，用户会直接丢失可检索内容。
    异常：NotFoundError（文档不存在）、ValidationError（参数非法或原始文件缺失）。
    副作用：同 add_document（重新解析、重新向量化、替换分块、读写 ES）。
    """
    doc = get_document(
        db, workspace_id=workspace_id, kb_id=kb_id, document_id=document_id
    )
    previous_size, previous_overlap = parser_settings(doc.parser_version)
    resolved_size = previous_size if chunk_size is None else chunk_size
    resolved_overlap = previous_overlap if overlap is None else overlap
    validate_chunking(resolved_size, resolved_overlap)
    file_id = doc.file_id
    # Validate the source before removing the current usable index.
    source_file = get_file(db, workspace_id, file_id)
    from ...common.storage import get_storage

    if not get_storage().exists(source_file.storage_key):
        raise ValidationError("原始文件不存在，无法重建索引")
    return add_document(
        db,
        workspace_id=workspace_id,
        kb_id=kb_id,
        file_id=file_id,
        chunk_size=resolved_size,
        overlap=resolved_overlap,
        _reindex_document_id=document_id,
    )


def _delete_document_rows(db: Session, doc: KnowledgeDocument) -> None:
    """删除一个文档及其所有分块（含元数据与 ES 文档），并删除文档行；不 commit。

    先删分块（_delete_chunk_rows 返回待清理的 ES id 并在事务内删 SQL 行），再清 ES，最后删文档行：
    这样 ES 清理失败也不会留下指向已删文档的数据库分块。
    """
    chunks = db.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == doc.workspace_id,
            KnowledgeChunk.knowledge_base_id == doc.knowledge_base_id,
            KnowledgeChunk.document_id == doc.id,
        )
    ).scalars().all()
    _delete_chunks_from_es(_delete_chunk_rows(db, chunks))
    db.delete(doc)
    db.flush()


def _delete_chunk_rows(db: Session, chunks: list[KnowledgeChunk]) -> list[str]:
    """Delete SQL chunks transactionally; let the caller schedule ES cleanup."""
    from .models import KnowledgeChunkMetadata

    es_ids = [chunk.elasticsearch_document_id for chunk in chunks if chunk.elasticsearch_document_id]
    chunk_ids = [chunk.id for chunk in chunks]
    if chunk_ids:
        db.execute(delete(KnowledgeChunkMetadata).where(
            KnowledgeChunkMetadata.workspace_id == chunks[0].workspace_id,
            KnowledgeChunkMetadata.chunk_id.in_(chunk_ids),
        ))
        db.flush()
    for chunk in chunks:
        db.delete(chunk)
    db.flush()
    return es_ids


def _purge_file_chunks(
    db: Session, workspace_id: str, kb_id: str, file_id: str
) -> None:
    """删除某文件在该知识库下的全部文档与分块（用于文件被删除/替换时联动清理），并 commit。

    按 file_id 匹配：一个文件在同一知识库下可能留下多条文档行（历史重复导入），
    所以这里遍历删除而不是只处理最新一条。
    """
    docs = db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.file_id == file_id,
        )
    ).scalars().all()
    for doc in docs:
        _delete_document_rows(db, doc)
    db.commit()


# ------------------------------------------------------------------
# Elasticsearch glue and retrieval
# ------------------------------------------------------------------
def _es_client():
    """每次调用新建一个 Elasticsearch 客户端（不缓存连接）。

    刻意不缓存：进程内长连接在索引重建/节点重启后会变成失效连接，而这里所有调用都在
    try/except 里以"失败即降级"处理，新建客户端的开销换来的是不会因陈旧连接而全链路报错。
    异常：elasticsearch 未安装或 URL 非法会在构造时抛出，由上层捕获降级。
    """
    from elasticsearch import Elasticsearch

    return Elasticsearch(settings.ES_URL)


def _index_chunk_es(
    chunk_id: str, content: str, info: dict, vector: list
) -> Optional[str]:
    """把一个分块的正文与向量写入 ES，成功返回 ES 文档 id，失败返回 None（不抛异常）。

    ES 文档里同时写入两套来源字段，供检索结果直接做引用定位：
        chunk_id（主键，与 SQL 分块一一对应）、content、
        workspace_id / knowledge_base_id（权限与范围过滤）、
        document_version_id（= document_id，命名沿用历史）、
        file_id / file_name（展示与"查看原文"链接）、
        location（含 page/slide/sheet/cell/ordinal 等定位信息，缺失时退化为 {ordinal}）、
        embedding + embedding_profile/model/provider/dimensions（用于判断向量可比性）。
    异常与降级：捕获所有异常并返回 None——ES 只是加速层，本地磁盘水位、节点不可用、索引只读
        都不应让已解析的文档与其数据库分块丢失（调用方据此累加 search_index_failures 并提示用户）。
    """
    body = {
        "chunk_id": chunk_id,
        "content": content,
        "workspace_id": info["workspace_id"],
        "knowledge_base_id": info["kb_id"],
        "document_version_id": info["document_id"],
        "file_id": info["file_id"],
        "file_name": info["file_name"],
        "location": info.get("location") or {"ordinal": info["ordinal"]},
        "embedding": vector,
        "embedding_profile": info.get("embedding_profile") or "embedding",
        "embedding_model": info.get("embedding_model"),
        "embedding_provider": info.get("embedding_provider"),
        "embedding_dimensions": info.get("embedding_dimensions") or len(vector),
    }
    try:
        response = _es_client().index(
            index=settings.ES_KNOWLEDGE_INDEX,
            id=chunk_id,
            document=body,
            refresh=False,
        )
        return response.get("_id")
    except Exception as exc:
        # Elasticsearch is an acceleration layer. A local disk watermark,
        # unavailable node or a read-only index must not discard the parsed
        # document and its database-backed chunks.
        logger.warning("knowledge chunk index failed chunk=%s: %s", chunk_id, exc)
        return None


def _refresh_index() -> None:
    """刷新知识库索引，使刚写入的分块立即可检索；失败只记日志（降级）。

    不带 refresh 的写入默认在约 1 秒后才可见，而用户上传后往往会立即检索，所以需要显式刷新。
    """
    try:
        _es_client().indices.refresh(index=settings.ES_KNOWLEDGE_INDEX)
    except Exception as exc:
        logger.warning("knowledge index refresh failed: %s", exc)


def _delete_chunks_from_es(es_ids: List[str]) -> None:
    """按 ES 文档 id 批量删除（delete_by_query + ids），并 refresh；失败只记日志。

    ignore=404 是因为索引可能尚未创建；空列表直接返回，避免无意义的 ES 调用。
    设计上与 SQL 删除分离：调用方先在事务里删库再调用本函数，因此 ES 删除失败只会留下
    "可被检索但会被 SQL 校验拦掉"的残留文档（search() 里会按 SQL 复核过滤）。
    """
    if not es_ids:
        return
    try:
        _es_client().delete_by_query(
            index=settings.ES_KNOWLEDGE_INDEX,
            body={"query": {"ids": {"values": es_ids}}},
            ignore=404,
            refresh=True,
        )
    except Exception as exc:
        logger.warning("knowledge chunk cleanup failed: %s", exc)


def _keyword_units(value: str) -> set[str]:
    """把查询串切成用于关键词打分的"词元"集合（中英文分别处理）。

    英文/数字按 ``[a-z0-9][a-z0-9_.%+-]*`` 提取整词；中文按 2 字与 3 字滑窗切（无分词器的近似
    方案，中文里 2-3 字组合的检索区分度足够）。最后减去一批疑问/停用词，
    否则"如何""什么"这类词会让所有分块都命中，把真正的关键词权重稀释掉。
    返回去重后的词元集合；纯空白输入返回空集（调用方会退化为整串包含匹配）。
    """
    normalized = re.sub(r"\s+", "", value.lower())
    units = set(re.findall(r"[a-z0-9][a-z0-9_.%+-]*", normalized))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        for width in (2, 3):
            units.update(
                sequence[index : index + width]
                for index in range(max(0, len(sequence) - width + 1))
            )
    return units - {"什么", "多少", "怎么", "如何", "是否", "请问", "这个", "一个"}


def _db_keyword_hits(
    db: Session,
    *,
    workspace_id: str,
    query: str,
    knowledge_base_ids: List[str] | None,
    file_ids: list[str] | None,
    top_k: int,
) -> list[tuple[dict, float]]:
    """索引不可用或有效候选不足时，对当前可见分块做有界词法回退。"""
    # 硬上限：只取最近 5000 个分块做内存打分（order_by created_at desc + limit 5000），
    # 保证最坏情况下耗时可控；因此对超大知识库，回退检索只覆盖最近导入的内容。
    # 打分规则：命中词元数 / 查询词元总数，整串（去空白后）包含时再 +1.0，
    # 直接命中整串的块一定排在只命中部分词元的块前面。
    # 先按当前权限、源文件和分段状态过滤再限量，避免停用/删除的候选占满回退窗口。
    rows = db.execute(
        _current_chunk_query(workspace_id, knowledge_base_ids, file_ids)
        .order_by(KnowledgeChunk.created_at.desc())
        .limit(5000)
    ).all()
    query_units = _keyword_units(query)
    normalized_query = re.sub(r"\s+", "", query.lower())
    ranked: list[tuple[dict, float]] = []
    for chunk, document, file_row in rows:
        normalized_text = re.sub(r"\s+", "", chunk.text.lower())
        matched = sum(1 for unit in query_units if unit in normalized_text)
        if not matched and normalized_query not in normalized_text:
            continue
        score = matched / max(1, len(query_units))
        if normalized_query and normalized_query in normalized_text:
            score += 1.0
        ranked.append((_current_chunk_source(chunk, document, file_row), score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:top_k]


def _current_chunk_query(workspace_id, knowledge_base_ids, file_ids):
    """检索候选与回退共用权威 SQL 条件；ORM 继续叠加当前动作的数据范围。"""
    from .models import KnowledgeChunkMetadata

    conditions = [
        KnowledgeChunk.workspace_id == workspace_id,
        KnowledgeDocument.workspace_id == workspace_id,
        KnowledgeDocument.knowledge_base_id == KnowledgeChunk.knowledge_base_id,
        # 重建期间旧分块仍可用，新分块在事务提交后才替换旧内容。
        KnowledgeDocument.status.in_(["ready", "processing"]),
        KnowledgeBase.workspace_id == workspace_id,
        KnowledgeBase.status == "active",
        File.workspace_id == workspace_id,
        File.status == "ready",
        File.deleted_at.is_(None),
        (KnowledgeChunkMetadata.id.is_(None) | KnowledgeChunkMetadata.enabled.is_(True)),
    ]
    if knowledge_base_ids:
        conditions.append(KnowledgeChunk.knowledge_base_id.in_(knowledge_base_ids))
    if file_ids:
        conditions.append(File.id.in_(file_ids))
    return (select(KnowledgeChunk, KnowledgeDocument, File)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeChunk.knowledge_base_id)
        .join(File, File.id == KnowledgeDocument.file_id)
        .outerjoin(KnowledgeChunkMetadata, KnowledgeChunkMetadata.chunk_id == KnowledgeChunk.id)
        .where(*conditions)
        .execution_options(populate_existing=True))


def _current_chunk_source(chunk, document, file_row) -> dict:
    """正文和引用都从已授权的当前 SQL 关系重建，索引只承担召回和排序。"""
    return {
        "chunk_id": chunk.id,
        "content": chunk.text,
        "file_id": file_row.id,
        "file_name": file_row.name,
        "knowledge_base_id": chunk.knowledge_base_id,
        "document_version_id": document.id,
        "location": chunk.location or {"ordinal": chunk.ordinal},
    }


def search(
    db: Session,
    *,
    workspace_id: str,
    query: str,
    knowledge_base_ids: List[str] | None = None,
    top_k: int = 6,
    min_score: float = 0.0,
    filters: dict | None = None,
    mode: str = "hybrid",
) -> List[dict]:
    """Search by keyword, vector similarity, or reciprocal-rank fusion."""
    # 中文说明（不改动原说明）——知识库检索的唯一入口，也是 AI 工具检索的底层实现。
    #
    # 参数：mode ∈ {hybrid, keyword, semantic}；top_k 夹紧到 1..20；min_score 是最低分阈值；
    #   filters 目前只支持 {"file_ids": [...]}；knowledge_base_ids 为 None 表示工作区内全部知识库。
    # 返回：按分数倒序的结果列表，每项字段如下（检索引用的来源字段就在这一层确定）：
    #   chunk_id（分块主键）、content（正文）、file_id / file_name（"查看原文"用）、
    #   knowledge_base_id、document_id（即 ES 里的 document_version_id）、
    #   location（含 page/slide/sheet/cell/ordinal，页码为 0 基，展示层负责 +1）、
    #   score、retrieval_mode、rerank_status、rerank_score。
    #   注意：链路里没有 source_file_id 字段——该名字只出现在模板填充工具的审计 metadata 中；
    #   检索侧的文件身份一律用 file_id/file_name，定位一律用 location。
    #
    # 降级与"静默返回空"的行为（重要，调用方必须知道）：
    #   - 关键词通道异常，或候选经当前权限/正文复核后不足时，用 _db_keyword_hits 补充；
    #     正文和来源始终取当前 SQL，不把过期正文的相关性分数套给新正文。
    #   - 语义通道异常：同样只记 warning，hits_knn 保持空。由于两个通道都"失败即空列表"，
    #     在混合模式下整体会静默退化成纯关键词结果；在 mode="semantic" 下则直接返回空列表，
    #     调用方无法从返回值区分"确实没有匹配"与"向量服务/ES 出错"。要判断原因需要看日志
    #     （"keyword search failed" / "semantic search failed"）或 embedding_status。
    #   - 语义通道还有一个刻意的静默跳过：选中的多个知识库嵌入配置不一致时
    #     （_semantic_embedding_profile 返回 None），不计算查询向量、不查 knn，
    #     避免把不同模型/维度的向量放在一起比较得出错误排序。
    #   - ES 可能残留已删除的分块（删除失败或重建），因此最后必须用 SQL 复核候选 id：
    #     只有文档状态为 ready/processing、知识库 active、文件 ready 且未删除、分段未被停用
    #     （KnowledgeChunkMetadata.enabled），且分数 ≥ min_score 的结果才会返回。SQL 是权威来源。
    #
    # 异常：ValidationError（查询为空、mode 非法）、NotFoundError（任一知识库不属于该工作区）。
    # 副作用：只读数据库与 ES；会调用嵌入模型为查询串生成向量（计费），并可能调用重排服务。
    clean_query = query.strip()
    if not clean_query:
        raise ValidationError("检索内容不能为空")
    if mode not in {"hybrid", "keyword", "semantic"}:
        raise ValidationError("检索模式必须是 hybrid、keyword 或 semantic")
    top_k = max(1, min(int(top_k), 20))
    for kb_id in knowledge_base_ids or []:
        get_kb(db, workspace_id, kb_id)

    es = _es_client()
    filters = filters or {}
    es_filters: list[dict] = [{"term": {"workspace_id": workspace_id}}]
    if knowledge_base_ids:
        es_filters.append({"terms": {"knowledge_base_id": knowledge_base_ids}})
    if filters.get("file_ids"):
        es_filters.append({"terms": {"file_id": filters["file_ids"]}})

    source_fields = [
        "chunk_id",
        "content",
        "file_id",
        "file_name",
        "knowledge_base_id",
        "document_version_id",
        "location",
    ]
    hits_bm25: list[tuple[dict, float]] = []
    hits_knn: list[tuple[dict, float]] = []

    if mode in {"hybrid", "keyword"}:
        body = {
            "size": min(top_k * 3, 60),
            "query": {
                "bool": {
                    "must": [{"match": {"content": clean_query}}],
                    "filter": es_filters,
                }
            },
            "_source": source_fields,
        }
        try:
            response = es.search(index=settings.ES_KNOWLEDGE_INDEX, body=body)
            hits_bm25 = [
                (hit["_source"], float(hit.get("_score") or 0.0))
                for hit in response["hits"]["hits"]
            ]
        except Exception as exc:
            # 降级：ES 关键词查询失败不抛异常，留空后由下面 _db_keyword_hits 用数据库关键词兜底。
            logger.warning("keyword search failed: %s", exc)

    if mode in {"hybrid", "semantic"}:
        try:
            semantic_profile = _semantic_embedding_profile(
                db, workspace_id, knowledge_base_ids,
            )
            if semantic_profile is None:
                # A single vector cannot be compared to an index containing
                # different model dimensions.  Keep keyword results in hybrid
                # mode and make pure semantic mode return no unsafe guess.
                logger.info("semantic search skipped for mixed KB embedding profiles")
                vector = []
            else:
                vector = embed_texts(
                    [clean_query],
                    workspace_id=workspace_id,
                    profile_name=semantic_profile,
                    usage_context={"source": "knowledge_search"},
                )[0]
            body = {
                "size": top_k,
                "knn": {
                    "field": "embedding",
                    "query_vector": vector,
                    "k": top_k,
                    "num_candidates": max(top_k * 4, 40),
                    "filter": {"bool": {"filter": es_filters}},
                },
                "_source": source_fields,
            }
            if vector:
                response = es.search(index=settings.ES_KNOWLEDGE_INDEX, body=body)
                hits_knn = [
                    (hit["_source"], float(hit.get("_score") or 0.0))
                    for hit in response["hits"]["hits"]
                ]
        except Exception as exc:
            # 降级：语义通道失败同样只记日志——hits_knn 保持空，混合模式下退化为纯关键词结果；
            # mode="semantic" 时这里会让最终结果静默变成空列表（调用方无法从返回值分辨原因）。
            logger.warning("semantic search failed: %s", exc)

    candidate_ids = {source.get("chunk_id") for source, _score in hits_bm25 + hits_knn}
    current_sources = {}
    if candidate_ids:
        rows = db.execute(_current_chunk_query(
            workspace_id, knowledge_base_ids, filters.get("file_ids"),
        ).where(KnowledgeChunk.id.in_(candidate_ids))).all()
        current_sources = {chunk.id: _current_chunk_source(chunk, document, file_row)
                           for chunk, document, file_row in rows}

    def current_hits(hits):
        """旧正文的向量和相关性不可套用到新正文；先剔除，再用当前词法补足。"""
        return [(current_sources[source.get("chunk_id")], score) for source, score in hits
                if source.get("chunk_id") in current_sources
                and source.get("content") == current_sources[source["chunk_id"]]["content"]]

    hits_bm25 = current_hits(hits_bm25)
    hits_knn = current_hits(hits_knn)
    if mode in {"hybrid", "keyword"} and len(hits_bm25) < top_k:
        fallback = _db_keyword_hits(db, workspace_id=workspace_id, query=clean_query,
            knowledge_base_ids=knowledge_base_ids, file_ids=filters.get("file_ids"),
            top_k=min(top_k * 3, 60))
        seen = {source["chunk_id"] for source, _score in hits_bm25}
        # 保留已验证 ES 的相对顺序，补充候选采用当前正文重新计算的词法分数。
        hits_bm25.extend((source, score) for source, score in fallback if source["chunk_id"] not in seen)

    if mode == "keyword":
        ranked = [{"src": src, "score": score} for src, score in hits_bm25]
    elif mode == "semantic":
        ranked = [{"src": src, "score": score} for src, score in hits_knn]
    else:
        fused: dict[str, dict] = {}

        def add_hits(hits: list[tuple[dict, float]], weight: float) -> None:
            """把某一路召回结果按 RRF（倒数排名融合）加权累加到融合表。
            要点：用 rank 而非原始分数（两路的分数体系不可比）；同一 chunk 在两路都命中时同键累加，
            因此无需额外实现"去重后合并"。闭包访问外层 fused 字典。
            """
            for rank, (source, _score) in enumerate(hits):
                chunk_id = source["chunk_id"]
                fused.setdefault(chunk_id, {"src": source, "score": 0.0})
                fused[chunk_id]["score"] += weight / (60.0 + rank + 1)

        add_hits(hits_bm25, 60.0)
        add_hits(hits_knn, 60.0)
        ranked = sorted(fused.values(), key=lambda item: item["score"], reverse=True)

    ranked = [item for item in ranked if item["score"] >= min_score]
    from .reranking import rerank
    ranked, rerank_status = rerank(db, workspace_id, clean_query, ranked)
    ranked = ranked[:top_k]
    return [
        {
            "chunk_id": item["src"]["chunk_id"],
            "content": item["src"]["content"],
            "file_id": item["src"].get("file_id"),
            "file_name": item["src"].get("file_name"),
            "knowledge_base_id": item["src"].get("knowledge_base_id"),
            "document_id": item["src"].get("document_version_id"),
            "location": item["src"].get("location") or {},
            "score": round(float(item["score"]), 4),
            "retrieval_mode": mode,
            "rerank_status": rerank_status,
            "rerank_score": item.get("rerank_score"),
        }
        for item in ranked
    ]


def _semantic_embedding_profile(
    db: Session, workspace_id: str, knowledge_base_ids: List[str] | None,
) -> str | None:
    """Return one profile only when all selected KBs share it.

    Elasticsearch's KNN request accepts one query vector.  Refusing to mix
    profiles avoids comparing vectors from different models or dimensions;
    callers still retain the keyword channel in hybrid mode.
    """
    # 中文说明：返回值语义——字符串表示"所有选中知识库共用这一个嵌入配置，可以安全算查询向量"；
    # None 表示"配置不一致，禁止做向量检索"。没有任何知识库时（或都没配 profile）返回默认名
    # "embedding"，与入库时的默认选择保持一致。
    from .models import KnowledgeProfile

    kb_statement = select(KnowledgeBase.id).where(
        KnowledgeBase.workspace_id == workspace_id,
        KnowledgeBase.status == "active",
    )
    if knowledge_base_ids:
        kb_statement = kb_statement.where(KnowledgeBase.id.in_(knowledge_base_ids))
    kb_ids = list(db.scalars(kb_statement).all())
    if not kb_ids:
        return "embedding"
    rows = db.scalars(select(KnowledgeProfile).where(
        KnowledgeProfile.workspace_id == workspace_id,
        KnowledgeProfile.knowledge_base_id.in_(kb_ids),
    )).all()
    profiles_by_kb = {row.knowledge_base_id: row for row in rows}
    selected = {
        ((profiles_by_kb.get(kb_id).embedding_model if profiles_by_kb.get(kb_id) else "")
         or "embedding").strip() or "embedding"
        for kb_id in kb_ids
    }
    if not selected:
        return "embedding"
    return next(iter(selected)) if len(selected) == 1 else None


def delete_kb(db: Session, workspace_id: str, kb_id: str) -> None:
    """删除知识库（软删除 + 物理清理其分块/文档/授权记录），并 commit。

    清理顺序：手工分段容器文件 → ES 中该库的全部文档（delete_by_query，按 workspace+kb 过滤）
      → 分块元数据与分块行 → 文档行 → 该库的 ResourceGrant（数据授权） → 最后把知识库
      status 置为 "deleted"（真正的软删除标记，知识库列表/检索都不再返回它）。

    注意：原资料库文件不会被删除（只删知识库侧的索引与记录），手工分段的容器文件是个例外——
    它是本模块创建的内部文件，所以会一并 mark_deleted（不存在时静默忽略 NotFoundError）。
    ES 清理失败只记日志，不阻断删除流程（残留文档会被 search() 的 SQL 复核过滤掉）。
    异常：NotFoundError —— 知识库不存在或不属于该工作区。
    副作用：删除多张表的数据 + ES 文档 + 软删内部文件；本函数自行 commit。
    """
    from app.modules.files import service as files_service
    from app.modules.system.models import ResourceGrant
    from .models import KnowledgeChunkMetadata, KnowledgeProfile

    kb = get_kb(db, workspace_id, kb_id)
    profile = db.scalar(select(KnowledgeProfile).where(
        KnowledgeProfile.workspace_id == workspace_id,
        KnowledgeProfile.knowledge_base_id == kb_id,
    ))
    if profile and profile.manual_file_id:
        try:
            files_service.mark_deleted(db, workspace_id, profile.manual_file_id)
        except NotFoundError:
            pass
    try:
        _es_client().delete_by_query(
            index=settings.ES_KNOWLEDGE_INDEX,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"workspace_id": workspace_id}},
                            {"term": {"knowledge_base_id": kb_id}},
                        ]
                    }
                }
            },
            ignore=404,
            refresh=True,
        )
    except Exception as exc:
        logger.warning("knowledge base index cleanup failed: %s", exc)

    chunks = db.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.knowledge_base_id == kb_id,
        )
    ).scalars().all()
    chunk_ids = [chunk.id for chunk in chunks]
    if chunk_ids:
        db.execute(delete(KnowledgeChunkMetadata).where(
            KnowledgeChunkMetadata.workspace_id == workspace_id,
            KnowledgeChunkMetadata.chunk_id.in_(chunk_ids),
        ))
        db.flush()
    for chunk in chunks:
        db.delete(chunk)
    documents = db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.knowledge_base_id == kb_id,
        )
    ).scalars().all()
    for document in documents:
        db.delete(document)
    db.execute(delete(ResourceGrant).where(
        ResourceGrant.workspace_id == workspace_id,
        ResourceGrant.resource_type == "knowledge",
        ResourceGrant.resource_id == kb_id,
    ))
    kb.status = "deleted"
    kb.updated_at = datetime.utcnow()
    db.commit()
