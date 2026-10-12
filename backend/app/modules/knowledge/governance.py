"""Knowledge settings, segment maintenance, retrieval evidence and file synchronization."""
from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ConflictError, NotFoundError, ValidationError
from app.ids import new_id
from app.models import File, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.modules.files import service as files_service
from app.modules.files.models import FileMetadata
from app.providers.model import embed_texts

from . import service as core
from .models import (
    KnowledgeChunkMetadata, KnowledgeProfile, KnowledgeRecallEvent, KnowledgeSyncRun,
)

_adoption_lock = RLock()
SEGMENT_TYPES = {"text", "qa"}
RETRIEVAL_MODES = {"hybrid", "keyword", "semantic"}
MAX_SEGMENTS_PER_BATCH = 500


def ensure_profile(db: Session, workspace_id: str, kb_id: str) -> tuple[KnowledgeProfile, bool]:
    """Adopt a legacy knowledge base into the versioned settings contract."""
    # 中文说明：知识库设置（chunk/top_k/rerank 等）存在独立的 KnowledgeProfile 表，老知识库没有这行。
    # 本函数做幂等"补建"：默认 hybrid + top_k=6 + 与全局一致的 chunk 参数，使老库无需迁移即可读取设置。
    # 返回 (profile, 是否本次新建)，调用方据此决定 commit。
    # 并发保护与服务层一致：无锁快路径 → 进程内锁二次确认 → 唯一约束 IntegrityError 后回读。
    core.get_kb(db, workspace_id, kb_id)
    row = db.scalar(select(KnowledgeProfile).where(
        KnowledgeProfile.workspace_id == workspace_id,
        KnowledgeProfile.knowledge_base_id == kb_id,
    ))
    if row is not None:
        return row, False
    with _adoption_lock:
        row = db.scalar(select(KnowledgeProfile).where(
            KnowledgeProfile.workspace_id == workspace_id,
            KnowledgeProfile.knowledge_base_id == kb_id,
        ))
        if row is not None:
            return row, False
        embedding_model, _vision_model = core._effective_model_names(workspace_id)
        candidate = KnowledgeProfile(
            workspace_id=workspace_id, knowledge_base_id=kb_id,
            retrieval_mode="hybrid", top_k=6, min_score=0.0,
            chunk_size=core.DEFAULT_CHUNK_SIZE, overlap=core.DEFAULT_CHUNK_OVERLAP,
            embedding_model=embedding_model, rerank_enabled=False, rerank_model="",
            sync_config={}, manual_file_id=None, manual_document_id=None,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate, True
        except IntegrityError:
            db.expire_all()
            existing = db.scalar(select(KnowledgeProfile).where(
                KnowledgeProfile.workspace_id == workspace_id,
                KnowledgeProfile.knowledge_base_id == kb_id,
            ))
            if existing is None:
                raise
            return existing, False


def profile_out(row: KnowledgeProfile) -> dict:
    """知识库设置的对外序列化；version 是前端做乐观锁回传的必填字段。

    sync_config 兜底成 {}：该列历史数据可能是 null（老记录的 JSON 列为空），
    直接下发 null 会让前端取属性时报错。
    """
    return {
        "knowledge_base_id": row.knowledge_base_id,
        "retrieval_mode": row.retrieval_mode,
        "top_k": row.top_k,
        "min_score": row.min_score,
        "chunk_size": row.chunk_size,
        "overlap": row.overlap,
        "embedding_model": row.embedding_model,
        "rerank_enabled": row.rerank_enabled,
        "rerank_model": row.rerank_model,
        "sync_config": row.sync_config or {},
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_profile(db: Session, workspace_id: str, kb_id: str) -> dict:
    """读知识库设置；若该库是历史数据则顺手补建 profile 并提交。

    副作用：可能新建一行 KnowledgeProfile（因此本"读"接口会写库并 commit）。
    异常：NotFoundError —— 知识库不存在或不属于该工作区（由 core.get_kb 抛出）。
    """
    row, adopted = ensure_profile(db, workspace_id, kb_id)
    if adopted:
        db.commit()
    return profile_out(row)


def update_profile(
    db: Session, workspace_id: str, kb_id: str, *, version: int,
    retrieval_mode: str, top_k: int, min_score: float,
    chunk_size: int, overlap: int, embedding_model: str,
    rerank_enabled: bool, rerank_model: str,
) -> KnowledgeProfile:
    """更新知识库设置（检索模式、召回数、分块参数、嵌入模型、rerank），返回更新后的行。

    校验顺序（刻意固定，先失败先返回，保证前端拿到的第一个报错就是最该修的那个）：
        1. 乐观锁 version —— 与当前行不一致直接 409，避免后写的请求覆盖别人刚保存的设置；
        2. retrieval_mode 必须是 hybrid/keyword/semantic；
        3. chunk_size / overlap 复用入库时的同一套规则（core.validate_chunking，范围 + overlap<size），
           保证"设置里能存的组合"与"建索引时能用的组合"完全一致，不会出现存得下却建不了索引；
        4. top_k 限 1..20（与检索接口的 clamp 上限对齐，防止设置里配置出永远达不到的召回数）；
        5. min_score 限 0..100（接口语义是百分制相关度）；
        6. 只有在 rerank_enabled 为真时才校验 rerank_model（关闭状态下允许留空/留失效模型名，
           否则用户没法先关 rerank 再改模型）。

    参数：embedding_model / rerank_model 会 strip 并截断到 200 字，与列宽一致。
    异常：ConflictError（版本冲突）、ValidationError（上述各项不合法）。
    副作用：写 knowledge_profile（含 updated_at）；不 commit，由路由层决定事务边界。
    """
    row, _adopted = ensure_profile(db, workspace_id, kb_id)
    if row.version != version:
        raise ConflictError("知识库设置已被修改，请刷新后重试", current_version=row.version)
    if retrieval_mode not in RETRIEVAL_MODES:
        raise ValidationError("检索模式必须是 hybrid、keyword 或 semantic")
    core.validate_chunking(chunk_size, overlap)
    if top_k < 1 or top_k > 20:
        raise ValidationError("召回数量必须在 1 到 20 之间")
    if min_score < 0 or min_score > 100:
        raise ValidationError("最低相关度必须在 0 到 100 之间")
    if rerank_enabled:
        from .reranking import validate_profile
        validate_profile(db, workspace_id, (rerank_model or "").strip())
    row.retrieval_mode = retrieval_mode
    row.top_k = top_k
    row.min_score = float(min_score)
    row.chunk_size = chunk_size
    row.overlap = overlap
    row.embedding_model = (embedding_model or "").strip()[:200]
    row.rerank_enabled = bool(rerank_enabled)
    row.rerank_model = (rerank_model or "").strip()[:200]
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def effective_search_options(
    db: Session, workspace_id: str, kb_ids: list[str] | None,
    *, mode: str | None, top_k: int | None, min_score: float | None,
) -> tuple[str, int, float]:
    """把"显式请求参数"与"知识库设置"合并成最终检索选项，返回 (mode, top_k, min_score)。

    优先级：显式传入的参数 > 知识库设置 > 硬编码默认值（hybrid / 6 / 0.0）。
    只在恰好选中一个知识库时才读设置：多库检索时各库 top_k/模式可能不同，取任一库的设置都会
    造成"选了 A+B 得到的召回数取决于顺序"的隐性偏差，因此多库一律用调用方参数或默认值。
    top_k 与 min_score 会再夹紧到与 update_profile 相同的合法区间。
    异常：ValidationError —— 合并后的 mode 非法。
    """
    profile = None
    if kb_ids and len(kb_ids) == 1:
        profile, _adopted = ensure_profile(db, workspace_id, kb_ids[0])
    resolved_mode = mode or (profile.retrieval_mode if profile else "hybrid")
    resolved_top_k = top_k if top_k is not None else (profile.top_k if profile else 6)
    resolved_min_score = min_score if min_score is not None else (profile.min_score if profile else 0.0)
    if resolved_mode not in RETRIEVAL_MODES:
        raise ValidationError("检索模式必须是 hybrid、keyword 或 semantic")
    return resolved_mode, max(1, min(int(resolved_top_k), 20)), max(0.0, float(resolved_min_score))


def ensure_chunk_metadata(
    db: Session, chunk: KnowledgeChunk, actor_user_id: str,
) -> tuple[KnowledgeChunkMetadata, bool]:
    """确保分块有对应的治理元数据行（分段类型/问答对/启用开关），返回 (行, 是否新建)。

    历史分块没有这行，同样用"快路径 → 进程内锁 → 唯一约束兜底回读"的幂等纳管模式补建，
    默认 segment_type="text"、enabled=True、source="parser"，即"解析器产出、默认参与检索"。
    """
    row = db.scalar(select(KnowledgeChunkMetadata).where(
        KnowledgeChunkMetadata.workspace_id == chunk.workspace_id,
        KnowledgeChunkMetadata.chunk_id == chunk.id,
    ))
    if row is not None:
        return row, False
    with _adoption_lock:
        row = db.scalar(select(KnowledgeChunkMetadata).where(
            KnowledgeChunkMetadata.workspace_id == chunk.workspace_id,
            KnowledgeChunkMetadata.chunk_id == chunk.id,
        ))
        if row is not None:
            return row, False
        candidate = KnowledgeChunkMetadata(
            workspace_id=chunk.workspace_id, knowledge_base_id=chunk.knowledge_base_id,
            chunk_id=chunk.id, segment_type="text", question="", answer="",
            enabled=True, source="parser", updated_by_user_id=actor_user_id,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate, True
        except IntegrityError:
            db.expire_all()
            existing = db.scalar(select(KnowledgeChunkMetadata).where(
                KnowledgeChunkMetadata.workspace_id == chunk.workspace_id,
                KnowledgeChunkMetadata.chunk_id == chunk.id,
            ))
            if existing is None:
                raise
            return existing, False


def _segment_content(segment_type: str, text: str, question: str, answer: str) -> tuple[str, str, str]:
    """把分段入参规范化成 (用于向量化的正文, 存库的问题, 存库的答案)。

    两条语义分支：
        - qa：正文拼成"问题：x\\n回答：y"再向量化，这样提问式检索也能命中；question/answer 分列
          单独存库供前端编辑，检索结果的展示不走这段拼接文本。
          两者任一为空即报错（半截问答对没有检索价值）；问题 ≤2000、答案 ≤20000 字。
        - text：正文取 text，为空时回落到 answer 字段（兼容前端只填了 answer 的情况），
          上限 20000 字。
    异常：ValidationError —— 类型非法、必填内容为空或超出长度上限。
    """
    if segment_type not in SEGMENT_TYPES:
        raise ValidationError("分段类型必须是 text 或 qa")
    if segment_type == "qa":
        clean_question = (question or "").strip()
        clean_answer = (answer or text or "").strip()
        if not clean_question or not clean_answer:
            raise ValidationError("问答分段必须填写问题与答案")
        if len(clean_question) > 2000 or len(clean_answer) > 20000:
            raise ValidationError("问答分段内容过长")
        return f"问题：{clean_question}\n回答：{clean_answer}", clean_question, clean_answer
    clean_text = (text or answer or "").strip()
    if not clean_text:
        raise ValidationError("文本分段内容不能为空")
    if len(clean_text) > 20000:
        raise ValidationError("单个文本分段不能超过 20000 个字符")
    return clean_text, "", ""


def _segment_out(
    chunk: KnowledgeChunk, metadata: KnowledgeChunkMetadata,
    document: KnowledgeDocument | None = None, file_row: File | None = None,
) -> dict:
    """分段列表项的对外序列化。

    text 只在 segment_type=="text" 时下发：qa 分段的 chunk.text 是拼接后的检索文本，
    原样返回会让前端把它当成可编辑正文，与 question/answer 字段重复并互相覆盖。
    file_name 兜底"手工分段"是因为手工分段挂在内部虚拟文件上，没有对应用户可见文件名。
    version 供前端回传做乐观锁；enabled=False 的分段不会进入检索结果。
    """
    return {
        "id": chunk.id,
        "document_id": chunk.document_id,
        "file_id": document.file_id if document else None,
        "file_name": file_row.name if file_row else "手工分段",
        "ordinal": chunk.ordinal,
        "segment_type": metadata.segment_type,
        "text": chunk.text if metadata.segment_type == "text" else "",
        "question": metadata.question,
        "answer": metadata.answer,
        "enabled": metadata.enabled,
        "source": metadata.source,
        "location": chunk.location or {},
        "embedding_status": chunk.embedding_status,
        "embedding_model": chunk.embedding_model,
        "version": metadata.version,
        "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
        "updated_at": metadata.updated_at.isoformat() if metadata.updated_at else None,
    }


def get_segment(
    db: Session, workspace_id: str, kb_id: str, chunk_id: str, actor_user_id: str,
) -> tuple[KnowledgeChunk, KnowledgeChunkMetadata]:
    """取单个分段及其治理元数据（分段的所有读写入口都先过这里做归属校验）。

    三重归属校验：知识库属于该工作区、分块属于该工作区且属于该知识库，任一不满足即 404。
    副作用：可能补建 KnowledgeChunkMetadata（老分块首次被访问时），调用方需自行 commit。
    异常：NotFoundError —— 知识库或分段不存在。
    """
    core.get_kb(db, workspace_id, kb_id)
    chunk = db.scalar(select(KnowledgeChunk).where(
        KnowledgeChunk.id == chunk_id,
        KnowledgeChunk.workspace_id == workspace_id,
        KnowledgeChunk.knowledge_base_id == kb_id,
    ))
    if chunk is None:
        raise NotFoundError("知识分段不存在")
    metadata, _adopted = ensure_chunk_metadata(db, chunk, actor_user_id)
    return chunk, metadata


def list_segments(
    db: Session, workspace_id: str, kb_id: str, actor_user_id: str,
    *, query: str = "", segment_type: str | None = None,
    enabled: bool | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    """分页列出知识库分段，支持按类型/启用状态/关键字过滤，返回 {items, total, page, page_size}。

    过滤与分页都在内存里做（先取全库分块再过滤切片）：为了让"过滤后 total"准确，若先用 SQL
    分页会得到错误的总数。分段是人工维护的小集合，这个取舍可接受。
    query 同时匹配正文、问题、答案三个字段（qa 分段正文里含拼接文本，所以三个都要搜）。
    segment_type/enabled 为 None 表示不过滤，用 `is not` 比较布尔值是为了区分 False 与未传。
    副作用：会补建缺失的分段元数据，有新建时 commit（所以此"读"接口可能写库）。
    异常：NotFoundError —— 知识库不存在。
    """
    core.get_kb(db, workspace_id, kb_id)
    rows = db.execute(
        select(KnowledgeChunk, KnowledgeDocument, File)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .outerjoin(File, File.id == KnowledgeDocument.file_id)
        .where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.knowledge_base_id == kb_id,
        )
        .order_by(KnowledgeChunk.created_at.desc(), KnowledgeChunk.ordinal)
    ).all()
    items = []
    adopted = False
    query_value = query.strip().lower()
    for chunk, document, file_row in rows:
        metadata, created = ensure_chunk_metadata(db, chunk, actor_user_id)
        adopted = adopted or created
        if segment_type and metadata.segment_type != segment_type:
            continue
        if enabled is not None and metadata.enabled is not enabled:
            continue
        searchable = " ".join((chunk.text, metadata.question, metadata.answer)).lower()
        if query_value and query_value not in searchable:
            continue
        items.append(_segment_out(chunk, metadata, document, file_row))
    if adopted:
        db.commit()
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size], "total": total,
        "page": page, "page_size": page_size,
    }


def _manual_document(
    db: Session, workspace_id: str, kb: KnowledgeBase, actor_user_id: str,
) -> tuple[KnowledgeDocument, str]:
    """取（或按需创建）承载"手工分段"的内部虚拟文档，返回 (document, 展示用文件名)。

    手工分段需要一个 document_id 才能复用统一的 KnowledgeChunk 表结构与检索管线，
    所以这里为每个知识库惰性创建一个内部 txt 文件作为容器，并把 id 缓存到 profile 的
    manual_file_id/manual_document_id 上，后续调用直接复用。
    容器文件的 source_type 被改成 "knowledge_manual"，因此不会出现在资料库列表里（见
    files.service.list_files 的过滤）；内容只是一句占位说明，不参与检索。
    异常：storage 写入失败会向上抛（容器创建失败时后续分段也无法建立索引）。
    副作用：可能写对象存储、files/file_metadata/knowledge_documents 三张表，并回写 profile 指针。
    """
    profile, _adopted = ensure_profile(db, workspace_id, kb.id)
    if profile.manual_document_id:
        document = db.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.id == profile.manual_document_id,
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
        ))
        if document is not None:
            return document, "手工分段"
    file_row = files_service.save_upload(
        db, workspace_id=workspace_id, owner_user_id=actor_user_id,
        filename=f"{kb.name}-手工分段.txt", mime_type="text/plain",
        data="由知识库分段管理维护。".encode("utf-8"),
        description="知识库内部手工分段容器", tags=["知识库", "内部"],
        change_note="创建知识库手工分段容器",
    )
    file_row.source_type = "knowledge_manual"
    document = KnowledgeDocument(
        id=new_id(), workspace_id=workspace_id, knowledge_base_id=kb.id,
        file_id=file_row.id, status="ready", parser_version="manual-v1",
        error_summary=None, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(document)
    db.flush()
    profile.manual_file_id = file_row.id
    profile.manual_document_id = document.id
    profile.updated_at = datetime.utcnow()
    return document, file_row.name


def _index_segment(
    db: Session, chunk: KnowledgeChunk, document: KnowledgeDocument,
    *, workspace_id: str, file_name: str,
) -> None:
    """为单个分段做向量化并写入搜索索引（就地修改 chunk 行，不 commit）。

    成功：embedding_status="done"，记录 embedding_model/dimensions 与 ES 文档 id。
    失败：embedding_status="failed"，并把 embedding_model/dimensions 清空（避免留下上一次成功
        时的元数据，让"这段到底有没有向量"变成假象）。
    重要行为——向量失败被静默吞掉：下面的 try 把 embed_texts 与配置解析的异常一律转成
    vector=[]，不向调用方抛出。后果是 create_segment/update_segment 会"看似成功"地返回，
    分段以 embedding_status="failed" 落库，只能靠关键词检索命中（_db_keyword_hits 不依赖向量），
    语义检索则永远召回不到它。前端需要靠 embedding_status 字段提示用户重试；
    这里不抛异常是为了让纯文本分段在没有可用嵌入模型时依然可用。
    """
    embedding_profile = core._embedding_selection(
        db, workspace_id, chunk.knowledge_base_id,
    )
    try:
        from ...providers.model import _embedding_config

        resolved_profile, embedding_config = _embedding_config(
            embedding_profile, workspace_id=workspace_id,
        )
        vectors = embed_texts(
            [chunk.text],
            workspace_id=workspace_id,
            profile_name=resolved_profile,
            usage_context={
                "source": "knowledge_segment",
                "source_id": chunk.id,
            },
        )
        vector = vectors[0] if vectors else []
    except Exception:
        # 静默降级为"无向量"：嵌入模型不可用/超限/返回空都不该让分段保存失败（见函数 docstring）。
        # 注意 embedding_config/resolved_profile 只在 try 内赋值，except 分支不能再引用它们，
        # 因此下面的成功分支才读取这两个变量。
        vector = []
    if vector:
        chunk.embedding_status = "done"
        chunk.embedding_model = str(embedding_config.get("model") or resolved_profile)
        chunk.embedding_dimensions = len(vector)
        es_id = core._index_chunk_es(chunk.id, chunk.text, {
            "document_id": document.id,
            "ordinal": chunk.ordinal,
            "kb_id": chunk.knowledge_base_id,
            "workspace_id": workspace_id,
            "file_id": document.file_id,
            "file_name": file_name,
            "location": chunk.location or {"ordinal": chunk.ordinal},
            "embedding_profile": resolved_profile,
            "embedding_model": chunk.embedding_model,
            "embedding_provider": str(embedding_config.get("provider") or ""),
            "embedding_dimensions": chunk.embedding_dimensions,
        }, vector)
        chunk.elasticsearch_document_id = es_id
    else:
        chunk.embedding_status = "failed"
        chunk.embedding_model = None
        chunk.embedding_dimensions = None


def create_segment(
    db: Session, workspace_id: str, kb_id: str, actor_user_id: str,
    *, segment_type: str, text: str = "", question: str = "", answer: str = "",
    enabled: bool = True, source: str = "manual",
) -> tuple[KnowledgeChunk, KnowledgeChunkMetadata]:
    """新建一个手工分段（text 或 qa），返回 (chunk, metadata)。

    ordinal 取该文档现有最大 ordinal + 1，保证手工分段与解析产出的分块在同一序列中不冲突
    （max 返回 None 时用 -1 起算，所以首个分段的 ordinal 是 0）。
    embedding_status 先写 "pending"，再由 _index_segment 改成 done/failed——因为索引可能失败，
    先置 pending 才能区分"还没做"与"做了但失败"。
    异常：ValidationError（分段内容不合法）、NotFoundError（知识库不存在）。
    副作用：可能创建手工容器文件与对象存储对象；写 knowledge_chunks/knowledge_chunk_metadata，
        并尝试写 ES 索引；不 commit。
    """
    kb = core.get_kb(db, workspace_id, kb_id)
    content, clean_question, clean_answer = _segment_content(segment_type, text, question, answer)
    document, file_name = _manual_document(db, workspace_id, kb, actor_user_id)
    previous_ordinal = db.scalar(select(func.max(KnowledgeChunk.ordinal)).where(
        KnowledgeChunk.workspace_id == workspace_id,
        KnowledgeChunk.document_id == document.id,
    ))
    # 第一条序号 0 是有效最大值，不能按假值回退，否则后续分段一直重复序号 0。
    ordinal = int(previous_ordinal) + 1 if previous_ordinal is not None else 0
    chunk = KnowledgeChunk(
        id=new_id(), workspace_id=workspace_id, knowledge_base_id=kb_id,
        document_id=document.id, ordinal=ordinal, text=content,
        location={"source": "manual", "ordinal": ordinal},
        embedding_status="pending", embedding_model=None,
        embedding_dimensions=None, elasticsearch_document_id=None,
        created_at=datetime.utcnow(),
    )
    metadata = KnowledgeChunkMetadata(
        workspace_id=workspace_id, knowledge_base_id=kb_id, chunk_id=chunk.id,
        segment_type=segment_type, question=clean_question, answer=clean_answer,
        enabled=enabled, source=source[:32], updated_by_user_id=actor_user_id,
    )
    db.add_all([chunk, metadata])
    db.flush()
    _index_segment(db, chunk, document, workspace_id=workspace_id, file_name=file_name)
    kb.updated_at = datetime.utcnow()
    db.flush()
    return chunk, metadata


def create_segments(
    db: Session, workspace_id: str, kb_id: str, actor_user_id: str,
    items: Iterable[dict], *, source: str = "manual",
) -> list[tuple[KnowledgeChunk, KnowledgeChunkMetadata]]:
    """批量新建手工分段（1~500 个），返回创建结果列表。

    逐个走 create_segment（而不是批量插入），因此每个分段都会独立做向量化与索引；
    代价是逐个调用嵌入模型，好处是单个分段失败不会让整批回滚——校验失败会向上抛，
    让调用方整体回滚并给出具体错误。
    异常：ValidationError —— 数量不在 1~500 之间（防单次请求打满嵌入模型配额）。
    副作用：同 create_segment，逐条写库与写索引；不 commit。
    """
    values = list(items)
    if not values or len(values) > MAX_SEGMENTS_PER_BATCH:
        raise ValidationError("每批必须包含 1 至 500 个知识分段")
    result = []
    for item in values:
        result.append(create_segment(
            db, workspace_id, kb_id, actor_user_id,
            segment_type=str(item.get("segment_type") or "text"),
            text=str(item.get("text") or ""),
            question=str(item.get("question") or ""),
            answer=str(item.get("answer") or ""),
            enabled=bool(item.get("enabled", True)), source=source,
        ))
    return result


def update_segment(
    db: Session, workspace_id: str, kb_id: str, chunk_id: str, actor_user_id: str,
    *, version: int, segment_type: str, text: str, question: str,
    answer: str, enabled: bool,
) -> tuple[KnowledgeChunk, KnowledgeChunkMetadata]:
    """更新一个分段的类型/内容/启停，返回更新后的 (chunk, metadata)。

    乐观锁 version 不匹配即 409（与知识库设置、文件元数据同一套并发控制口径）。
    内容变更后会重新走 _index_segment（先改 chunk.text 再索引），因此改完立即对检索生效；
    若向量化失败，分段会被置为 embedding_status="failed"，但更新本身仍算成功。
    异常：ConflictError（版本冲突）、ValidationError（内容不合法）、NotFoundError（分段或来源文档不存在）。
    副作用：写 knowledge_chunks 与 knowledge_chunk_metadata、更新知识库 updated_at、尝试写 ES；不 commit。
    """
    chunk, metadata = get_segment(db, workspace_id, kb_id, chunk_id, actor_user_id)
    if metadata.version != version:
        raise ConflictError("知识分段已被修改，请刷新后重试", current_version=metadata.version)
    content, clean_question, clean_answer = _segment_content(segment_type, text, question, answer)
    metadata.segment_type = segment_type
    metadata.question = clean_question
    metadata.answer = clean_answer
    metadata.enabled = enabled
    metadata.updated_by_user_id = actor_user_id
    metadata.updated_at = datetime.utcnow()
    chunk.text = content
    document = db.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == chunk.document_id,
        KnowledgeDocument.workspace_id == workspace_id,
    ))
    if document is None:
        raise NotFoundError("知识分段来源文档不存在")
    file_row = db.scalar(select(File).where(File.id == document.file_id, File.workspace_id == workspace_id))
    _index_segment(
        db, chunk, document, workspace_id=workspace_id,
        file_name=file_row.name if file_row else "手工分段",
    )
    kb = core.get_kb(db, workspace_id, kb_id)
    kb.updated_at = datetime.utcnow()
    db.flush()
    return chunk, metadata


def delete_segment(
    db: Session, workspace_id: str, kb_id: str, chunk_id: str, actor_user_id: str,
) -> None:
    """删除一个分段：先清 ES 文档，再删元数据行，最后删 chunk 行。

    顺序有意：ES 删除失败不会让数据库记录残留（若先删库再删 ES 失败，就会留下永远搜得到、
    却打不开引用的幽灵命中）。ES 清理失败在 core._delete_chunks_from_es 内部被降级为日志。
    手工分段的 ordinal 不会被回收，删除后再建仍从最大值 +1，保证历史引用不歧义。
    异常：NotFoundError（分段不存在）。
    副作用：删除 knowledge_chunk_metadata 与 knowledge_chunks 行、删 ES 文档、更新知识库 updated_at；不 commit。
    """
    chunk, metadata = get_segment(db, workspace_id, kb_id, chunk_id, actor_user_id)
    if chunk.elasticsearch_document_id:
        core._delete_chunks_from_es([chunk.elasticsearch_document_id])
    db.execute(delete(KnowledgeChunkMetadata).where(KnowledgeChunkMetadata.id == metadata.id))
    db.flush()
    db.delete(chunk)
    kb = core.get_kb(db, workspace_id, kb_id)
    kb.updated_at = datetime.utcnow()
    db.flush()


def filter_enabled_hits(db: Session, workspace_id: str, hits: list[dict]) -> list[dict]:
    """过滤掉被管理员停用的分段命中（enabled=False），保持原有顺序。

    只查一次"被停用的 chunk_id 集合"再在内存里过滤，避免对每个命中都查库。
    没有元数据的 chunk 视为启用（列条件写成 enabled IS False，而不是 enabled IS NOT True），
    这样老分块在没有治理行时不会被误判为停用而搜不到。
    """
    ids = [item["chunk_id"] for item in hits]
    if not ids:
        return hits
    disabled = set(db.scalars(select(KnowledgeChunkMetadata.chunk_id).where(
        KnowledgeChunkMetadata.workspace_id == workspace_id,
        KnowledgeChunkMetadata.chunk_id.in_(ids),
        KnowledgeChunkMetadata.enabled.is_(False),
    )).all())
    return [item for item in hits if item["chunk_id"] not in disabled]


def record_recall(
    db: Session, *, workspace_id: str, actor_user_id: str,
    query: str, kb_ids: list[str], mode: str, top_k: int,
    hits: list[dict], duration_ms: int, filters: dict,
) -> KnowledgeRecallEvent:
    """记录一次检索召回事件（谁、在什么库、用什么模式、命中多少、耗时多少）。

    query 截断到 1000 字、duration_ms 取非负：这是审计表，不能让超长输入或时钟回拨写进脏数据。
    max_score 取所有命中的最高分（无命中时为 0.0），用于运营侧判断"检索质量是否下降"。
    副作用：写 knowledge_recall_events；不 commit（由路由层与业务写入一起提交）。
    """
    row = KnowledgeRecallEvent(
        id=new_id(), workspace_id=workspace_id, knowledge_base_ids=list(kb_ids),
        actor_user_id=actor_user_id, query=query.strip()[:1000],
        retrieval_mode=mode, top_k=top_k, hit_count=len(hits),
        max_score=max((float(item.get("score") or 0) for item in hits), default=0.0),
        duration_ms=max(0, duration_ms), filters=filters or {},
    )
    db.add(row)
    db.flush()
    return row


def list_recall_events(
    db: Session, workspace_id: str, *, actor_user_id: str,
    include_all_actors: bool, kb_id: str | None, limit: int,
) -> list[dict]:
    """按时间倒序列出召回事件；include_all_actors=False 时只看自己的记录。

    权限口径：是否看全量由调用方（路由层）根据权限决定，本函数只负责按开关过滤，
    因此路由必须先做权限判断再传 include_all_actors=True。
    kb_id 过滤在内存做（knowledge_base_ids 是 JSON 数组列，跨库用 SQL 过滤不可靠），
    因此先多取 limit*3 条再筛，最后截到 limit；这是权衡后的近似分页，不保证严格精确。
    副作用：kb_id 非空时会校验知识库存在（可能抛 NotFoundError）。
    """
    if kb_id:
        core.get_kb(db, workspace_id, kb_id)
    statement = select(KnowledgeRecallEvent).where(
        KnowledgeRecallEvent.workspace_id == workspace_id,
    )
    if not include_all_actors:
        statement = statement.where(KnowledgeRecallEvent.actor_user_id == actor_user_id)
    rows = db.scalars(statement.order_by(KnowledgeRecallEvent.created_at.desc()).limit(limit * 3)).all()
    if kb_id:
        rows = [row for row in rows if kb_id in (row.knowledge_base_ids or [])]
    rows = rows[:limit]
    return [{
        "id": row.id, "knowledge_base_ids": row.knowledge_base_ids or [],
        "actor_user_id": row.actor_user_id, "query": row.query,
        "retrieval_mode": row.retrieval_mode, "top_k": row.top_k,
        "hit_count": row.hit_count, "max_score": row.max_score,
        "duration_ms": row.duration_ms, "filters": row.filters or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


def sync_files(
    db: Session, workspace_id: str, kb_id: str, actor_user_id: str,
    *, category_id: str | None, file_ids: list[str], reindex: bool,
) -> KnowledgeSyncRun:
    """把资料库文件批量同步进知识库，返回同步运行记录（status 为 completed 或 partial）。

    逐文件独立提交是刻意的：每处理完一个文件就 commit 一次并刷新 run 计数，这样单个文件失败
    （解析失败、向量服务超限等）只回滚该文件，前面成功的导入结果和计数都会保留，
    最终 run.status = "partial" 并在 error_summary 里给出逐文件错误。
    跳过规则：已 status=="ready" 且 reindex=False 时计入 skipped_count（不重复建索引）。
    候选集固定排除 source_type=="knowledge_manual"（内部容器文件）与已删除/非 ready 文件，
    且只取 is_current 的元数据行，避免把历史版本也导入。
    参数：category_id 与 file_ids 可同时使用（取交集）；reindex=True 时对已有文档走 reindex_document。
    异常：NotFoundError —— 知识库或分类不存在（在创建 run 之前就报错，不会留下 running 记录）。
    副作用：写 knowledge_sync_runs、knowledge_documents、knowledge_chunks、ES 索引；
        更新 profile.sync_config 与知识库 updated_at；函数内部自行 commit。
    """
    kb = core.get_kb(db, workspace_id, kb_id)
    category = files_service.get_category(db, workspace_id, category_id)
    run = KnowledgeSyncRun(
        workspace_id=workspace_id, knowledge_base_id=kb_id,
        source_type="files", source_config={
            "category_id": category.id if category else None,
            "file_ids": list(dict.fromkeys(file_ids)), "reindex": reindex,
        },
        status="running", imported_count=0, skipped_count=0, failed_count=0,
        error_summary=None, actor_user_id=actor_user_id,
    )
    db.add(run)
    db.commit()
    statement = select(File, FileMetadata).join(
        FileMetadata, FileMetadata.file_id == File.id,
    ).where(
        File.workspace_id == workspace_id,
        File.status == "ready",
        File.deleted_at.is_(None),
        File.source_type != "knowledge_manual",
        FileMetadata.workspace_id == workspace_id,
        FileMetadata.is_current.is_(True),
    )
    if category:
        statement = statement.where(FileMetadata.category_id == category.id)
    if file_ids:
        statement = statement.where(File.id.in_(file_ids))
    candidates = db.execute(statement.order_by(File.created_at)).all()
    errors = []
    for file_row, _metadata in candidates:
        existing = db.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.file_id == file_row.id,
        ).order_by(KnowledgeDocument.created_at.desc()))
        try:
            if existing and existing.status == "ready" and not reindex:
                run = db.get(KnowledgeSyncRun, run.id)
                run.skipped_count += 1
                db.commit()
                continue
            if existing:
                core.reindex_document(
                    db, workspace_id=workspace_id, kb_id=kb_id,
                    document_id=existing.id,
                )
            else:
                profile, _adopted = ensure_profile(db, workspace_id, kb_id)
                core.add_document(
                    db, workspace_id=workspace_id, kb_id=kb_id, file_id=file_row.id,
                    chunk_size=profile.chunk_size, overlap=profile.overlap,
                )
            run = db.get(KnowledgeSyncRun, run.id)
            run.imported_count += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(KnowledgeSyncRun, run.id)
            run.failed_count += 1
            errors.append(f"{file_row.name}: {str(exc)[:240]}")
            db.commit()
    run = db.get(KnowledgeSyncRun, run.id)
    run.status = "completed" if not errors else "partial"
    run.error_summary = "；".join(errors)[:2000] or None
    run.completed_at = datetime.utcnow()
    run.updated_at = run.completed_at
    profile, _adopted = ensure_profile(db, workspace_id, kb_id)
    profile.sync_config = run.source_config
    profile.updated_at = run.completed_at
    kb = core.get_kb(db, workspace_id, kb_id)
    kb.updated_at = run.completed_at
    db.commit()
    return run


def sync_run_out(row: KnowledgeSyncRun) -> dict:
    """同步运行记录的对外序列化；source_config 兜底 {} 兼容历史 null 值。"""
    return {
        "id": row.id, "knowledge_base_id": row.knowledge_base_id,
        "source_type": row.source_type, "source_config": row.source_config or {},
        "status": row.status, "imported_count": row.imported_count,
        "skipped_count": row.skipped_count, "failed_count": row.failed_count,
        "error_summary": row.error_summary, "actor_user_id": row.actor_user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def list_sync_runs(db: Session, workspace_id: str, kb_id: str, limit: int) -> list[dict]:
    """按时间倒序列出某知识库的同步运行历史（最近 limit 条）。

    只读接口，但会先校验知识库归属以防跨工作区探测。
    异常：NotFoundError —— 知识库不存在或不属于该工作区。
    """
    core.get_kb(db, workspace_id, kb_id)
    rows = db.scalars(select(KnowledgeSyncRun).where(
        KnowledgeSyncRun.workspace_id == workspace_id,
        KnowledgeSyncRun.knowledge_base_id == kb_id,
    ).order_by(KnowledgeSyncRun.created_at.desc()).limit(limit)).all()
    return [sync_run_out(row) for row in rows]
