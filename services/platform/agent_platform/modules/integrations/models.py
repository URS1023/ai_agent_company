"""运行关联与回调收据是业务状态，独立建模，不能以通用配置记录替代。"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NativeRun(Base):
    __tablename__ = "native_runs"
    __table_args__ = (UniqueConstraint("workspace_id", "binding_id", "idempotency_key", name="uq_native_run_request"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    integration_id: Mapped[str] = mapped_column(String(64), index=True)
    binding_id: Mapped[str] = mapped_column(String(64), index=True)
    binding_kind: Mapped[str] = mapped_column(String(50))
    binding_revision: Mapped[int] = mapped_column(Integer)
    release_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="running")
    native_run_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    native_task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    platform_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_user_id: Mapped[str] = mapped_column(String(300))
    sequence: Mapped[int] = mapped_column(Integer, default=-1)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CallbackReceipt(Base):
    __tablename__ = "native_callback_receipts"
    __table_args__ = (UniqueConstraint("integration_id", "event_id", name="uq_native_callback_event"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    integration_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(160))
    body_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64))
    disposition: Mapped[str] = mapped_column(String(30))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
