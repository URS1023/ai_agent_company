"""Durable idempotency receipts for AI-initiated business mutations."""
from __future__ import annotations

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class BusinessActionReceipt(OwnedRecord, Base):
    """One actor-scoped receipt prevents a chat retry from duplicating a resource."""

    __tablename__ = "ai_business_action_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "owner_user_id", "request_key",
            name="uq_ai_business_action_request",
        ),
    )

    action: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="稳定 capability_id，例如 dashboard.generate",
    )
    request_key: Mapped[str] = mapped_column(
        String(160), nullable=False, comment="调用方保留的防重复键；同键不同参数拒绝执行",
    )
    request_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="除防重复键外的规范化请求摘要",
    )
    status: Mapped[str] = mapped_column(
        String(30), default="running", nullable=False, comment="running/completed/failed",
    )
    invocation_run_id: Mapped[str | None] = mapped_column(
        String(26), nullable=True, index=True, comment="发起此动作的对话 Run；直接 API 可为空",
    )
    resource_type: Mapped[str] = mapped_column(
        String(80), default="", nullable=False, comment="成功创建的业务资源类型",
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(26), nullable=True, index=True, comment="成功创建的业务资源标识",
    )
    result_json: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False, comment="可恢复的轻量业务结果，不保存密钥或大段数据快照",
    )
    error_message: Mapped[str] = mapped_column(
        Text, default="", nullable=False, comment="脱敏失败摘要；失败不会伪装成成功",
    )

