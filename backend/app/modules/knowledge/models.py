"""Additive governance records for the existing knowledge ingestion engine."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.ids import new_id
from app.models import Base, now
from app.modules.system.models import WorkspaceRecord

__all__ = [
    "KnowledgeProfile", "KnowledgeChunkMetadata", "KnowledgeRecallEvent", "KnowledgeSyncRun",
]


class KnowledgeProfile(WorkspaceRecord, Base):
    """Versioned retrieval, chunking and synchronization defaults for one knowledge base."""

    __tablename__ = "knowledge_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "knowledge_base_id", name="uq_knowledge_profile_base"),
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("knowledge_bases.id"), nullable=False, index=True,
    )
    # 默认值与 service.DEFAULT_CHUNK_SIZE / DEFAULT_CHUNK_OVERLAP 必须保持一致：
    # ensure_profile 补建历史 profile 时用的就是这两个常量，不一致会让"默认设置"随建库路径不同而变。
    retrieval_mode: Mapped[str] = mapped_column(String(24), default="hybrid", nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    min_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    overlap: Mapped[int] = mapped_column(Integer, default=80, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rerank_model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    sync_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    manual_file_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    manual_document_id: Mapped[str | None] = mapped_column(String(26), nullable=True)


class KnowledgeChunkMetadata(WorkspaceRecord, Base):
    """Editable business metadata around a legacy or manually created chunk."""

    __tablename__ = "knowledge_chunk_metadata"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_knowledge_chunk_metadata_chunk"),
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("knowledge_bases.id"), nullable=False, index=True,
    )
    chunk_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("knowledge_chunks.id"), nullable=False, index=True,
    )
    segment_type: Mapped[str] = mapped_column(String(24), default="text", nullable=False)
    question: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # source 记录这条分段的来源：parser=解析器产出，manual=后台手工新建，其它值由调用方自定义
    # （create_segment 的 source 参数截断到 32 字）。用于区分"系统生成"与"人工维护"以便排查。
    source: Mapped[str] = mapped_column(String(32), default="parser", nullable=False)
    updated_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


class KnowledgeRecallEvent(Base):
    """Append-only retrieval evidence without storing result document contents."""

    __tablename__ = "knowledge_recall_events"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("workspaces.id"), nullable=False, index=True,
    )
    knowledge_base_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    query: Mapped[str] = mapped_column(String(1000), nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class KnowledgeSyncRun(WorkspaceRecord, Base):
    """One auditable file-category synchronization execution."""

    __tablename__ = "knowledge_sync_runs"
    knowledge_base_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("knowledge_bases.id"), nullable=False, index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), default="files", nullable=False)
    # source_config 同时承担两种角色：记录本次同步的入参（category_id/file_ids/reindex），
    # 成功结束后还会被回写到 knowledge_profiles.sync_config 作为"上次同步条件"，供页面回显。
    source_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # status 取值：running（进行中）→ completed（全部成功）/ partial（有文件失败，见 error_summary）。
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    imported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
