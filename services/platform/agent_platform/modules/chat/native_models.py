"""原生聊天只保存平台映射、运行预约和来源证据；消息正文与消息树仍在Open WebUI。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid4())


class NativeChatIdentity(Base):
    """每个平台用户、工作区和原生实例拥有独立普通账号，凭据只引用Vault密文。"""
    __tablename__ = 'native_chat_identities'
    __table_args__ = (
        UniqueConstraint('workspace_id', 'user_id', 'integration_id', name='uq_chat_identity_actor_instance'),
        UniqueConstraint('integration_id', 'native_user_id', name='uq_chat_identity_native_user'),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    integration_id: Mapped[str] = mapped_column(String(64), index=True)
    integration_revision: Mapped[int] = mapped_column(Integer)
    integration_url: Mapped[str] = mapped_column(String(500))
    native_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='provisioning')
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NativeConversation(Base):
    """平台会话ID是唯一外部入口；不接受客户端自报原生chat ID绑定。"""
    __tablename__ = 'native_chat_conversations'
    __table_args__ = (
        UniqueConstraint('integration_id', 'native_chat_id', name='uq_chat_conversation_native'),
        UniqueConstraint('workspace_id', 'user_id', 'integration_id', 'creation_key', name='uq_chat_conversation_intent'),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    integration_id: Mapped[str] = mapped_column(String(64), index=True)
    identity_id: Mapped[str] = mapped_column(String(64), index=True)
    native_chat_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(240), default='新对话')
    creation_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default='creating')
    active_turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inherited_sources: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NativeChatTurn(Base):
    """先提交准确消息ID与请求摘要再派发；unknown预约不能重新发送模型请求。"""
    __tablename__ = 'native_chat_turns'
    __table_args__ = (
        UniqueConstraint('conversation_id', 'idempotency_key', name='uq_chat_turn_intent'),
        UniqueConstraint('identity_id', 'assistant_message_id', name='uq_chat_turn_native_message'),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    integration_id: Mapped[str] = mapped_column(String(64), index=True)
    identity_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    native_chat_id: Mapped[str] = mapped_column(String(200))
    user_message_id: Mapped[str] = mapped_column(String(100))
    assistant_message_id: Mapped[str] = mapped_column(String(100))
    parent_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_id: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default='reserved')
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    native_task_ids: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NativeTurnAction(Base):
    """停止和工具审批是独立写操作；超时后保留原动作，不能盲目重复批准。"""
    __tablename__ = 'native_chat_turn_actions'
    __table_args__ = (UniqueConstraint('turn_id', 'idempotency_key', name='uq_chat_turn_action_intent'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default='reserved')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
