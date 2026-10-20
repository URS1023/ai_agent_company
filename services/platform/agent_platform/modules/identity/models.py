"""服务身份与短期调用令牌元数据；原始令牌只在签发响应中出现一次。"""

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.database import Base
from ...core.models import new_id, now_iso


class ServiceIdentity(Base):
    __tablename__ = "service_identities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    caller_kind: Mapped[str] = mapped_column(String(40), default="external")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class ServiceAccess(Base):
    __tablename__ = "service_access_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("service_identities.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("login_sessions.id"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[str] = mapped_column(String(40))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
