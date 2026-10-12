# -*- coding: utf-8 -*-
"""SQLAlchemy models mirroring the pre-provisioned company_agent schema.

Every business object carries workspace_id; versioned objects use
{name}_versions with a JSON `spec`.

职责：声明平台级 ORM 表结构（本文件即结构契约的组成来源之一）。

系统位置：app.schema.initialize_schema 依据 Base.metadata 建表并做摘要校验，因此任何字段改动
都必须同步升 SCHEMA_VERSION，否则服务启动即失败（见 app/schema.py）。

按业务域的表归属（域内表分散在两部分，避免循环导入）：

- 平台治理域：本文件 organizations / users / organization_members / workspaces /
  workspace_members / auth_identities / local_credentials / sessions / audit_logs；
  角色权限、菜单、数据源治理等扩展表见 modules/system、modules/platform。
- 能力资产域：本文件 skills / skill_versions / connectors / connector_versions / tools /
  tool_versions / connector_tool_bindings / plugins / plugin_versions 及各类绑定表。
- 对话与运行域：本文件 conversations / messages / runs / run_steps / tool_calls /
  run_events / run_resource_snapshots / model_calls / citations / artifacts。
- 数据接入与密钥域：本文件 data_sources / secrets。
- 工作流域：不在本文件，见 modules/workflows/models.py（流程定义、版本与运行实例）。
- 设备域：不在本文件，见 modules/alerts/models.py（共享设备台账与测点，预警/质检共用）。
- 质检域：不在本文件，见 modules/quality/models.py 与 modules/writeback/models.py（回写）。
- 报表域：不在本文件，见 modules/reports/models.py 与 modules/analytics/models.py（查询/模板）。
- 其他业务扩展域（知识图谱、文档、素材、应用、系统工具、模型与文件治理等）见各自
  modules/<feature>/models.py，均由 app.schema.initialize_schema 统一导入注册。

通用约定：业务表主键统一为 26 字符 ULID 字符串（app.ids.new_id）；所有业务对象带 workspace_id
做隔离；有版本概念的对象采用 {name} + {name}_versions 两表结构，spec 为 JSON。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Float, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.ids import new_id


class Base(DeclarativeBase):
    """全项目唯一的声明式基类：app.schema 用它收集元数据建表与校验结构契约。"""

    pass


class OrgMixin:
    """需要按工作区隔离的模型的公共字段（仅声明，不提供查询级过滤）。"""

    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


def now() -> datetime:
    """默认时间戳工具：统一用 UTC naive 时间（库中所有 DateTime 列均按此写入）。"""
    return datetime.utcnow()


class IdempotencyClaim(Base):
    """Durable atomic claim for side-effecting entry points.

    The unique scope/key pair is claimed in its own committed transaction
    before a report generation or application launch creates a run, calls a
    model, or executes a data collection.  ``result_json`` is intentionally
    limited to the already-authorized response, so a retry can replay without
    re-running the side effect.

    代表一次"带幂等键的副作用请求"的占用记录：先抢占再执行，重放时直接返回 result_json。
    不变式：同 (workspace, 用户, scope, key) 唯一；result_json 只存已授权的响应体，
    绝不放内部错误细节或未授权数据。
    """

    __tablename__ = "idempotency_claims"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "owner_user_id", "scope", "idempotency_key",
            name="uq_idempotency_claim_scope_key",
        ),
    )
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(180), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="claimed")
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    resource_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


# ------------------------------------------------------------------
# Organization / Workspace / Users
# 平台治理域·主体：组织 → 工作区 → 成员，是全部业务数据的隔离与授权根。
# ------------------------------------------------------------------
class Organization(Base):
    """组织：租户级容器，工作区必须挂在其下；删除/改名会影响所有工作区归属。"""

    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class User(Base):
    """用户：只存展示信息与状态，登录标识在 auth_identities、口令在 local_credentials。"""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OrganizationMember(Base):
    """组织成员关系：用户在某组织中的角色（工作区可见范围由 workspace_members 决定）。"""

    __tablename__ = "organization_members"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Workspace(Base):
    """工作区：业务数据隔离的实际边界，几乎所有业务表的 workspace_id 指向它。"""

    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WorkspaceMember(Base):
    """工作区成员关系：唯一约束 (workspace_id, user_id) 保证同一用户在工作区内只有一条角色。"""

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # admin | member
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------------
# Auth
# 平台治理域·认证：身份标识、本地口令凭据、会话令牌三张表分离，便于接入多种登录方式。
# ------------------------------------------------------------------
class AuthIdentity(Base):
    """登录身份：(provider, username_normalized) 唯一，同一用户可绑定多种 provider。"""

    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "username_normalized", name="uq_identity_username"),)
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    username_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LocalCredential(Base):
    """本地口令凭据：只存 Argon2 哈希（见 app.security），明文口令绝不落库。

    失败计数与 locked_until 由登录流程写入；credential_version 递增会让既有会话失效。
    """

    __tablename__ = "local_credentials"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    auth_identity_id: Mapped[str] = mapped_column(String(26), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    temporary_password_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SessionToken(Base):
    """会话：只保存令牌的 SHA-256 摘要（原始令牌仅在客户端），支持绝对与空闲双过期。

    expires_at 为绝对有效期、idle_expires_at 为最近活动 + 空闲 TTL；revoked_at 非空即已失效。
    """

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------------
# Conversation / Message
# 对话域：会话与消息。运行过程（runs/run_steps/run_events）见下方 Run 分组。
# ------------------------------------------------------------------
class Conversation(Base):
    """对话会话：按 workspace_id + owner_user_id 做可见性隔离，archived_at 为软归档。"""

    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Message(Base):
    """会话消息：sequence 为会话内单调序号（前端据此排序与增量拉取）。

    content 只是纯文本降级字段，结构化内容以 parts_json（app.core.parts 构造）为准。
    """

    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user | assistant | system
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # plain-text fallback (frontend convenience)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    parts_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # structured message parts


# ------------------------------------------------------------------
# Files
# 平台治理域·文件：上传文件的元数据登记；物理字节在对象存储，storage_key 唯一。
# ------------------------------------------------------------------
class File(Base):
    """用户上传文件：storage_key 唯一且不可复用，sha256 用于去重与完整性核对。

    软删除（deleted_at）后仍保留登记行，避免历史消息中的引用失效。
    """

    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")  # uploading|ready|failed|deleted
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------
# Knowledge
# 知识库域：知识库 → 文档 → 分块三层；向量/全文检索的官方副本在 Elasticsearch，
# 这里的 embedding_* 字段只是入库状态与索引定位信息。
# ------------------------------------------------------------------
class KnowledgeBase(Base):
    """知识库：visibility 决定工作区内共享还是仅创建者可见。"""

    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class KnowledgeDocument(Base):
    """知识库文档：一条 file_id 在某个知识库中的解析登记（可失败重试，故有 status）。"""

    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    file_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")  # processing|ready|failed
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class KnowledgeChunk(Base):
    """知识分块：检索与引用定位的最小单位，ordinal 在文档内唯一递增。

    location 记录页码/幻灯片/工作表等定位信息（形状随文件类型变化，读取方需按类型兼容）；
    elasticsearch_document_id 指向 ES 中的检索副本，为空表示尚未索引成功。
    """

    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {page, ordinals...}
    embedding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending|done|failed
    embedding_model: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    embedding_dimensions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    elasticsearch_document_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------------
# Skill
# 能力资产域·技能：技能定义 + 不可变版本（spec 为 JSON），发布后由 published_version_id 指向。
# ------------------------------------------------------------------
class Skill(Base):
    """技能：可被插件/智能体复用的能力定义，草稿与正式对外版本通过 published_version_id 分离。"""

    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    published_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SkillVersion(Base):
    """技能版本：一旦发布即视为不可变（spec 不应再改），修改需另建草稿版本。"""

    __tablename__ = "skill_versions"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    based_on_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")  # draft|published|archived
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------
# Connector / Tool
# 能力资产域·连接器与工具：connector（外部系统接入）→ tool（可调用动作）→ backend 版本绑定。
# 三层都是"定义 + 版本"结构，工具版本通过 ConnectorToolBinding 绑定到某个连接器版本。
# ------------------------------------------------------------------
class Connector(Base):
    """连接器：一类外部能力的接入定义（provider 决定走 HTTP/MCP/数据库/内置/沙箱）。"""

    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # http|mcp|database|builtin|sandbox
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    published_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ConnectorVersion(Base):
    """连接器版本：spec 为 ConnectorSpec（凭据只存 secret_id 引用，绝不放明文）。"""

    __tablename__ = "connector_versions"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    based_on_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)  # ConnectorSpec
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Tool(Base):
    """工具：暴露给智能体调用的动作定义，必须归属于某个连接器。"""

    __tablename__ = "tools"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    connector_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    published_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ToolVersion(Base):
    """工具版本：spec 为 ToolSpec；connector_version_id 固定它依赖的连接器版本（不可漂移到最新）。"""

    __tablename__ = "tool_versions"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tool_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    connector_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)  # ToolSpec
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ConnectorToolBinding(Base):
    """连接器版本与工具版本的多对多绑定（无状态字段，仅关联）。"""

    __tablename__ = "connector_tool_bindings"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    connector_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    tool_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


# ------------------------------------------------------------------
# DataSource
# 数据接入域：分析用的外部数据源登记，凭据走 secrets（secret_id 引用）。
# ------------------------------------------------------------------
class DataSource(Base):
    """数据源：connection_config 为连接参数（不含明文口令），secret_id 指向加密保存的凭据。

    注意：本项目约定测试只允许本机数据源，生产/远程连接需显式授权。
    """

    __tablename__ = "data_sources"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # mysql|postgres|http|...
    connection_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    secret_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")


# ------------------------------------------------------------------
# Plugin
# 能力资产域·插件：把技能/工具/资源打包成对话可直接启用的整体能力，版本发布后由会话引用。
# ------------------------------------------------------------------
class Plugin(Base):
    """插件：面向用户的成品能力包（如默认的"PPT 生成"），published_version_id 为对外版本。"""

    __tablename__ = "plugins"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    published_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PluginVersion(Base):
    """插件版本：spec 为 PluginSpec（含 tool_refs/permissions 等，见 app.seed 的期望状态示例）。"""

    __tablename__ = "plugin_versions"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    based_on_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)  # PluginSpec
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PluginSkillBinding(Base):
    """插件版本 → 技能版本绑定（决定该版本能用哪些技能）。"""

    __tablename__ = "plugin_version_skill_bindings"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plugin_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    skill_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


class PluginToolBinding(Base):
    """插件版本 → 工具版本绑定（插件实际可调用的工具集合）。"""

    __tablename__ = "plugin_version_tool_bindings"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plugin_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    tool_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


class PluginResourceBinding(Base):
    """插件版本 → 资源绑定（数据源/知识库/文件/产物），config 为资源级使用参数。"""

    __tablename__ = "plugin_version_resource_bindings"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plugin_version_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)  # datasource|knowledge|file|artifact
    resource_id: Mapped[str] = mapped_column(String(26), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)


# ------------------------------------------------------------------
# Secret
# 数据接入域·密钥：密文 + nonce + key_version 三元组，由 app.crypto 加解密。
# ------------------------------------------------------------------
class Secret(Base):
    """加密保存的凭据：ciphertext/nonce/key_version 三者必须一起读写（见 app.crypto）。

    约束：解密只在真正使用凭据的瞬间发生；本表不存明文，删标志为 deleted_at 软删除。
    """

    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")  # api_key|db_password|oauth...
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------
# Run / Step / ToolCall / Events
# 对话运行域·执行轨迹：run（一次智能体任务）→ step（阶段）→ tool_call / model_call（外部调用）
# → run_events（前端可回放的流式事件）。这些表是审计与问题定位的事实来源，只追加不修改。
# ------------------------------------------------------------------
class Run(Base):
    """运行实例：一次智能体执行的顶层记录（状态机：created → running → 终态）。

    idempotency_key 用于请求级去重；parent_run_id 支持子运行（如工具内再触发一次运行）。
    """

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    started_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    plugin_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    effective_config_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_usage: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    parent_run_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    model_profile: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class RunStep(Base):
    """运行阶段：run 内按 sequence 排序的步骤（retry_count 记录本步骤已重试次数）。"""

    __tablename__ = "run_steps"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    input_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    output_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ToolCall(Base):
    """工具调用记录：arguments_json/result_json 均为已脱敏的对外结构。

    idempotency_key 唯一：同一工具同一副作用不允许重复执行（重放返回既有结果）。
    """

    __tablename__ = "tool_calls"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    step_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    tool_version_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    arguments_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, unique=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class RunEvent(Base):
    """运行事件流：自增 id 作为全局序号供前端按序拉取/断点续传（sequence 为运行内序号）。

    注意 id 用 BigInteger 但在 SQLite 上退化为 Integer（with_variant），以保证本地/测试库的自增行为。
    """

    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # 旧事件保留 NULL；规范工具回执依靠数据库唯一约束防止重复最终事件。
    receipt_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)


class RunResourceSnapshot(Base):
    """运行期资源快照：记录本次运行读取到的资源版本内容，用于结果可复现与审计追溯。"""

    __tablename__ = "run_resource_snapshots"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(26), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ModelCall(Base):
    """模型调用记录：统计 token/费用与排障（usage_json），错误码与 app.errors 的模型错误分类对应。"""

    __tablename__ = "model_calls"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    step_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    usage_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------
# Artifact / Citation
# 对话运行域·产物与引用：artifact 为运行产出（文件/图表），lineage 记录派生关系，
# citation 记录模型回答引用的知识出处。
# ------------------------------------------------------------------
class Artifact(Base):
    """运行产物：storage_key 与 external_url 二者其一必填（本地存储或外部链接）。

    type 取值同时驱动前端展示与下载方式，新增类型需同步前端。
    """

    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # document|spreadsheet|presentation|pdf|image|chart|code|dataset|json
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[Optional[str]] = mapped_column(String(768), nullable=True, unique=True)
    external_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    parent_artifact_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ArtifactLineage(Base):
    """产物血缘：显式记录"由哪个产物派生、以何关系派生"，供追溯与重新生成。"""

    __tablename__ = "artifact_lineage"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    parent_artifact_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Citation(Base):
    """回答引用：chunk_id 指向知识分块（可能为空，如引用的是文件级出处）。

    ordinal 为同一条消息内引用的展示序号（从 0 起）。
    """

    __tablename__ = "citations"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    chunk_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[Optional[dict]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------------
# Audit
# 平台治理域·审计：只追加的操作留痕，元数据用 metadata_json 承载（不存敏感明文）。
# ------------------------------------------------------------------
class AuditLog(Base):
    """审计日志：谁在何时对什么资源做了什么（actor 可能为空，表示系统动作）。

    约束：只追加不更新；写入失败不应阻断业务主流程（由调用方决定容错策略）。
    """

    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------------
# Messaging infra
# 基础设施表：outbox 用于"事务内落事件、事务外投递"的可靠消息模式。
# ------------------------------------------------------------------
class OutboxEvent(Base):
    """发件箱事件：与业务写入同事务落库，由投递进程在 published_at 置空的行上重试发送。"""

    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)


# ------------------------------------------------------------------
# Model profiles
# 模型治理域：可用模型档案与其探测结果（模型管理页依赖这里的数据）。
# ------------------------------------------------------------------
class ModelProfile(Base):
    """模型档案：一个"供应商 + 模型 + 端点"的可用配置，enabled 决定是否对外可选。

    约束：endpoint 只存地址，密钥仍走 secrets/环境变量，不入本表。
    """

    __tablename__ = "model_profiles"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    profile_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # agnes|siliconflow|...
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capabilities_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ModelProbeResult(Base):
    """模型探测结果：每次连通性/能力探测追加一行，详情在 details_json。"""

    __tablename__ = "model_probe_results"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------------
# Test runs
# 能力资产域·试运行：技能/连接器/插件/工具的调试执行记录（不影响正式运行数据）。
# ------------------------------------------------------------------
class TestRun(Base):
    """试运行记录：resource_version_id 精确到版本，保证复现时依赖不变。"""

    __tablename__ = "test_runs"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)  # skill|connector|plugin|tool
    resource_version_id: Mapped[str] = mapped_column(String(26), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
