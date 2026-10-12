"""Additive file-governance records built around the existing physical File rows."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.ids import new_id
from app.models import Base, now
from app.modules.system.models import WorkspaceRecord

__all__ = ["FileCategory", "FileMetadata", "FileParseEvent"]


class FileCategory(WorkspaceRecord, Base):
    """Workspace category tree; deleting a non-empty node is deliberately rejected."""

    __tablename__ = "file_categories"
    __table_args__ = (
        UniqueConstraint("workspace_id", "parent_id", "name", name="uq_file_category_sibling_name"),
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("file_categories.id"), nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class FileMetadata(WorkspaceRecord, Base):
    """One immutable content revision's business metadata.

    ``File`` remains the physical stored object used by every existing module.
    Rows sharing ``logical_id`` form one user-facing asset history; exactly one
    is current, while knowledge records may continue referencing an older file.
    """

    __tablename__ = "file_metadata"
    # 一个物理文件只能有一行元数据；同一逻辑文件内 revision_number 不能重复。
    # 这两条唯一约束是 ensure_metadata/create_version 并发正确性的最终兜底（服务层先查后插）。
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_file_metadata_file"),
        UniqueConstraint(
            "workspace_id", "logical_id", "revision_number",
            name="uq_file_metadata_logical_revision",
        ),
    )
    file_id: Mapped[str] = mapped_column(String(26), ForeignKey("files.id"), nullable=False, index=True)
    logical_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    category_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("file_categories.id"), nullable=True, index=True,
    )
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # 每个 logical_id 链上只应有一条 is_current=True（由服务层维护，非数据库约束）；
    # 列表/查询一律按 is_current 过滤，所以它同时是"生效版本"的语义开关。
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    previous_file_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    change_note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


class FileParseEvent(Base):
    """Append-only evidence for preview/extraction attempts."""

    __tablename__ = "file_parse_events"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("workspaces.id"), nullable=False, index=True,
    )
    logical_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    file_id: Mapped[str] = mapped_column(String(26), ForeignKey("files.id"), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), default="preview", nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    parser: Mapped[str] = mapped_column(String(80), default="builtin", nullable=False)
    block_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)
