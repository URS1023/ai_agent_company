"""系统辅助配置与不可变修订；保存元数据，不执行代码或修改业务数据库结构。"""
from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...core.database import Base
from ...core.models import new_id, now_iso


class OperationSupportResource(Base):
    __tablename__ = "operation_support_resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    scope: Mapped[str] = mapped_column(String(20), default="workspace")
    scope_owner: Mapped[str] = mapped_column(String(80), default="workspace")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "scope_owner", "code", name="uq_operation_support_key"),
        Index("ix_operation_support_scope", "workspace_id", "kind", "scope"),
    )


class OperationSupportRevision(Base):
    __tablename__ = "operation_support_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_id: Mapped[str] = mapped_column(ForeignKey("operation_support_resources.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    __table_args__ = (UniqueConstraint("resource_id", "number", name="uq_operation_support_revision"),)
