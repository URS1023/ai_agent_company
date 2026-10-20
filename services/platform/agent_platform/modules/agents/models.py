"""智能体应用模型：提示词、知识空间与模型参数构成一个可对话的应用。"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentApp(Base):
    """一个智能体应用：system 提示词 + 可选知识空间，回答时自动检索资料。"""

    __tablename__ = 'agent_apps'

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default='')
    prompt: Mapped[str] = mapped_column(Text, default='')
    space_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    top_k: Mapped[int] = mapped_column(Integer, default=3)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1200)
    temperature: Mapped[float] = mapped_column(default=0.3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
