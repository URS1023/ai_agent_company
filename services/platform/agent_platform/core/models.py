"""平台身份、资源目录、任务和文件表；强业务实体由各自模块建模。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return str(uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SystemState(Base):
    __tablename__ = 'system_state'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Organization(Base):
    __tablename__ = 'organizations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))


class Workspace(Base):
    __tablename__ = 'workspaces'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    name: Mapped[str] = mapped_column(String(160))
    factory_id: Mapped[str] = mapped_column(String(80), default='')
    industry: Mapped[str] = mapped_column(String(80), default='制造业')
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Role(Base):
    __tablename__ = 'roles'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('workspace_id', 'name'),)


class Membership(Base):
    __tablename__ = 'memberships'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    role_ids: Mapped[list] = mapped_column(JSON, default=list)
    department_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('workspace_id', 'user_id'),)


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'))
    csrf_token: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[str] = mapped_column(String(40))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class ResourceRecord(Base):
    __tablename__ = 'resource_records'
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(240), default='')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    visibility: Mapped[str] = mapped_column(String(20), default='workspace')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    __table_args__ = (Index('ix_resources_workspace_kind', 'workspace_id', 'kind'),)


class ResourceGrant(Base):
    __tablename__ = 'resource_grants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    actions: Mapped[list] = mapped_column(JSON, default=list)


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(160))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Task(Base):
    __tablename__ = 'tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(240))
    engine: Mapped[str] = mapped_column(String(60), default='platform')
    access_permission: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default='queued')
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    __table_args__ = (UniqueConstraint('workspace_id', 'user_id', 'idempotency_key'),)


class TaskEvent(Base):
    __tablename__ = 'task_events'
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    type: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String(240))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(80))
    read_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class SecretEntry(Base):
    __tablename__ = 'secret_entries'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(80), default='api_key')
    encrypted_value: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class FileEntry(Base):
    __tablename__ = 'files'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    visibility: Mapped[str] = mapped_column(String(20), default='private')
    name: Mapped[str] = mapped_column(String(240))
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default='uploaded')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class ResourceReference(Base):
    __tablename__ = 'resource_references'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    source_type: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class SecretVersion(Base):
    """凭据各版本密文保持不可变，轮换不抹去历史证据。"""
    __tablename__ = 'secret_versions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    secret_id: Mapped[str] = mapped_column(ForeignKey('secret_entries.id'), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    encrypted_value: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    __table_args__ = (UniqueConstraint('secret_id', 'revision'),)


class SecretRotationTicket(Base):
    """短期一次性轮换票据；数据库只存票据摘要和加密的新凭据。"""
    __tablename__ = 'secret_rotation_tickets'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    secret_id: Mapped[str] = mapped_column(ForeignKey('secret_entries.id'), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    expected_revision: Mapped[int] = mapped_column(Integer)
    encrypted_value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[str] = mapped_column(String(40))
    consumed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
