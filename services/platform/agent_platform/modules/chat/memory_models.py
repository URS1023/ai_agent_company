"""用户主动维护的记忆；隔离字段不接受客户端修改。"""
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base
from agent_platform.core.models import new_id, now_iso


class UserMemory(Base):
    """沿用 Open WebUI memory 的用户、内容和类型，增加工作区与修订号。"""

    __tablename__ = "chat_user_memories"
    __table_args__ = (Index("ix_chat_user_memories_scope", "workspace_id", "user_id", "updated_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20), default="user")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)
