"""平台原件到当前普通原生账号副本的预约；正文解析和向量继续归Open WebUI。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def now():
    return datetime.now(timezone.utc)


class NativeChatFile(Base):
    __tablename__ = 'native_chat_files'
    __table_args__ = (
        UniqueConstraint('identity_id', 'request_key', name='uq_native_chat_file_intent'),
        UniqueConstraint('identity_id', 'file_id', 'source_revision', 'source_sha256', 'generation', name='uq_native_chat_file_generation'),
        UniqueConstraint('identity_id', 'native_file_id', name='uq_native_chat_file_copy'),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    identity_id: Mapped[str] = mapped_column(String(64), index=True)
    integration_id: Mapped[str] = mapped_column(String(64), index=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    source_revision: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_size: Mapped[int] = mapped_column(Integer)
    source_name: Mapped[str] = mapped_column(String(240))
    source_content_type: Mapped[str] = mapped_column(String(160))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    request_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    native_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='reserved')
    native_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cleanup_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
