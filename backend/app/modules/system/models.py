"""Workspace-scoped administration records; existing Cloud Brain identities are reused.

职责：system（系统管理）模块的持久化模型定义 —— 组织架构（部门/岗位）、成员资料、角色与授权、
菜单/参数/字典、以及组织与工作区生命周期、工作区安全策略、可信设备、登录证据、会话上下文和
OIDC 配置。所有表名统一以 system_ 前缀，避免与既有 Cloud Brain 表冲突。

位置与依赖：本模块只声明表结构，不注册路由（system 模块的路由前缀统一为 /api/v1/system，
见 router.py / governance_router.py），业务规则在对应的 *_service.py 中实现。

关键约束：
- 除 OrganizationProfile / OidcLoginState 外，其余表都继承 WorkspaceRecord，即强制带
  workspace_id（工作区隔离的物理保证）与三件套 created_at/updated_at/version；查询一律
  以 workspace_id 过滤，不做跨工作区联查。
- 身份来源：User/AuthIdentity/LocalCredential 等仍由既有 Cloud Brain 表承载（app.models），
  本模块只做“工作区内的附加属性”（MemberProfile、UserRole），不复制登录身份，避免双份密码源。
- 枚举型字段（status/data_scope/session_overflow/token_auth_method 等）取值必须与 schemas.py、
  permissions.DATA_SCOPES 和前端表单保持一致，三处同步修改。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.ids import new_id
from app.models import Base, now

__all__ = [
    "Department", "Position", "MemberProfile", "Role", "UserRole", "ResourceGrant",
    "MenuSetting", "SystemSetting", "DictionaryEntry", "OrganizationProfile",
    "WorkspaceLifecycle", "WorkspaceSecurityPolicy", "TrustedDevice", "LoginEvent",
    "SessionContext", "OidcProvider", "OidcLoginState",
]


class WorkspaceRecord:
    """Common identity, scope, timestamps and optimistic concurrency version.

    用途：本模块所有“工作区内记录”的公共基类，统一提供主键、工作区归属、时间戳与乐观锁版本。
    关键不变式：
    - workspace_id 是权限隔离的物理边界，写入时必须来自 AuthContext.workspace_id；
    - version 通过 __mapper_args__ 注册为 SQLAlchemy 的 version_id_col：每次 UPDATE 由 ORM 自动
      自增，并在“读到旧版本后再提交”时抛 StaleDataError（commit_change 会转成冲突提示）；
    - 因此调用方不需要（也不应该）手工维护 version，只允许把前端的 version 交给 check_version 比较；
    - 继承本类的模型必须同时继承 Base（class X(WorkspaceRecord, Base)），本类自身不是表。
    """

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id, comment="记录标识")
    workspace_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("workspaces.id"), nullable=False, index=True, comment="所属工作区",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, comment="创建时间 UTC")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, onupdate=now, nullable=False, comment="修改时间 UTC",
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="乐观锁版本")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:
        """把 version 注册为 SQLAlchemy 的乐观锁列（version_id_col）。

        效果：每次 UPDATE 自动 version+1，并且当“读取时的版本”与提交时的库中版本不一致时抛
        StaleDataError —— service 层据此把并发冲突转成用户可读的刷新提示，而不是静默覆盖。
        用 declared_attr 是为了让所有子类各自继承该映射参数（混入类本身不是表）。
        """
        return {"version_id_col": cls.version}


class Department(WorkspaceRecord, Base):
    """Organization tree inside an explicitly selected workspace.

    表 system_departments：(workspace_id, code) 唯一，parent_id 自引用构成部门树。
    约束：parent_id 必须同工作区（由 organization_service._validate_department 逐级向上校验，
    防止跨工作区挂接与成环）；被成员或角色数据范围引用时不允许删除；停用部门不能再被选为上级
    或分配给新成员。
    """

    __tablename__ = "system_departments"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_department_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="部门编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="部门名称")
    parent_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("system_departments.id"), nullable=True, comment="上级部门",
    )
    leader_user_id: Mapped[str | None] = mapped_column(String(26), nullable=True, comment="负责人用户")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="显示顺序")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")


class Position(WorkspaceRecord, Base):
    """Job position; a position never implicitly grants permissions.

    表 system_positions：(workspace_id, code) 唯一。岗位只描述“做什么工作”，不携带任何
    权限或数据范围；成员可同时持有多个岗位（MemberProfile.position_ids）。
    约束：被成员引用时不允许删除；停用岗位不能再分配给成员或通过批量导入写入。
    """

    __tablename__ = "system_positions"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_position_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="岗位编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="岗位名称")
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False, comment="职责说明")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")


class MemberProfile(WorkspaceRecord, Base):
    """Workspace-specific user attributes, separate from shared login identity.

    表 system_member_profiles：(workspace_id, user_id) 唯一，是“同一账号在不同工作区有不同
    部门/岗位/联系方式”的落点；user_id 指向共享的 users 表，本表不存登录名与密码。
    关键字段：roles_initialized 表示该成员的角色已显式初始化过 —— 为空角色列表不会再回退到
    旧版 WorkspaceMember.role 的隐式授权，避免迁移后被意外提权；version 是业务成员资料的
    乐观锁版本（前端“成员资料版本”），与 users 表的版本相互独立。
    """

    __tablename__ = "system_member_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_member_profile"),)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"), nullable=False, comment="用户")
    department_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("system_departments.id"), nullable=True, comment="所属部门",
    )
    position_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="岗位标识列表")
    employee_code: Mapped[str] = mapped_column(String(80), default="", nullable=False, comment="员工编号")
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False, comment="联系邮箱")
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False, comment="联系电话")
    roles_initialized: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="角色已显式初始化；空角色不回退授权",
    )


class Role(WorkspaceRecord, Base):
    """Role grants explicitly registered actions and an associated data scope.

    表 system_roles：(workspace_id, code) 唯一。权限与数据范围绑定在本行上：permissions 是
    permissions.ALL_PERMISSIONS 的子集，data_scope ∈ DATA_SCOPES，department_ids 仅在
    data_scope="custom" 时生效。
    约束：builtin=True 的内置角色（workspace_admin/member/viewer）由 bootstrap 在升级时对齐，
    不允许删除；workspace_admin 的权限与状态不可通过接口修改。enabled=False 后所有绑定用户
    立即失去该角色带来的授权（access_policy 只取 enabled 角色）。
    """

    __tablename__ = "system_roles"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_system_role_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="角色编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="角色名称")
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False, comment="角色说明")
    permissions: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="已登记权限编码列表")
    data_scope: Mapped[str] = mapped_column(String(32), default="self", nullable=False, comment="数据访问范围")
    department_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="自定义部门范围")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否系统内置角色")


class UserRole(WorkspaceRecord, Base):
    """Explicit assignment of a workspace role to a member.

    表 system_user_roles：(workspace_id, user_id, role_id) 唯一，是多对多授权关系。
    约束：role_id 必须属于同一工作区（写入前由 scoped_record 校验）；移除工作区成员时按
    (workspace_id, user_id) 整体删除；权限历史依赖审计表而不是本表，所以删除不影响追溯。
    """

    __tablename__ = "system_user_roles"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "role_id", name="uq_system_user_role"),)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(26), ForeignKey("system_roles.id"), nullable=False, index=True)


class ResourceGrant(WorkspaceRecord, Base):
    """Additional explicit, auditable access to one resource for a user or role.

    表 system_resource_grants：(workspace_id, resource_type, resource_id, subject_type, subject_id)
    唯一，表示“把某个已存在资源单独共享给某人/某角色”，是角色授权之外的补充（可在角色授权
    基础上放宽到本资源，但不会放宽动作权限本身）。
    约束：resource_type 必须在 admin_service.RESOURCE_MODELS 注册；resource_id 必须存在且属于
    本工作区（create_grant 会逐个模型查找）；subject_type=user 时要求目标是本工作区成员。
    """

    __tablename__ = "system_resource_grants"
    __table_args__ = (UniqueConstraint(
        "workspace_id", "resource_type", "resource_id", "subject_type", "subject_id",
        name="uq_system_resource_grant",
    ),)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="资源类型")
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False, comment="资源标识")
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="授权对象 user/role")
    subject_id: Mapped[str] = mapped_column(String(26), nullable=False, comment="用户或角色标识")
    actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="允许的资源动作")


class MenuSetting(WorkspaceRecord, Base):
    """Presentation preferences for known routes; cannot create executable routes.

    表 system_menu_settings：(workspace_id, menu_id) 唯一。menu_id 必须已在 platform.catalog
    登记，本表只覆盖显示名/显隐/排序，不产生新的可访问路由；隐藏菜单不等于收回后端权限
    （后端仍以 permissions 为准）。
    """

    __tablename__ = "system_menu_settings"
    __table_args__ = (UniqueConstraint("workspace_id", "menu_id", name="uq_system_menu_setting"),)
    menu_id: Mapped[str] = mapped_column(String(80), nullable=False, comment="已登记菜单标识")
    label: Mapped[str] = mapped_column(String(160), default="", nullable=False, comment="显示名称，空值使用原名")
    visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否显示菜单")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="组内显示顺序")


class SystemSetting(WorkspaceRecord, Base):
    """Non-secret workspace settings with typed values.

    表 system_settings：(workspace_id, key) 唯一。value 允许 JSON 标量或结构，供前端直接渲染。
    约束：key 必须出现在 admin_service.DEFAULT_SETTINGS（有真实消费方的参数），否则接口拒绝写入；
    本表禁止存凭据/密钥，敏感值一律走密钥模块（app.modules.secret），避免明文落库。
    """

    __tablename__ = "system_settings"
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_system_setting"),)
    key: Mapped[str] = mapped_column(String(120), nullable=False, comment="参数名称")
    value: Mapped[dict | list | str | int | bool] = mapped_column(JSON, nullable=False, comment="参数值，禁止凭据")
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False, comment="用途说明")


class DictionaryEntry(WorkspaceRecord, Base):
    """User-managed business dictionaries, isolated by workspace and namespace.

    表 system_dictionary_entries：(workspace_id, namespace, key) 唯一。namespace 用于隔离不同类型的
    字典（如下拉选项组），label/value 是给业务表单直接用的展示与取值。
    约束：字典只承载展示数据，绝不参与权限判定（权限只认 PERMISSION_CATALOG 编码）。
    """

    __tablename__ = "system_dictionary_entries"
    __table_args__ = (UniqueConstraint("workspace_id", "namespace", "key", name="uq_dictionary_entry"),)
    namespace: Mapped[str] = mapped_column(String(80), nullable=False, comment="字典类型")
    key: Mapped[str] = mapped_column(String(120), nullable=False, comment="字典项编码")
    label: Mapped[str] = mapped_column(String(200), nullable=False, comment="显示名称")
    value: Mapped[dict | list | str | int | bool] = mapped_column(JSON, nullable=False, comment="字典项值")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="显示顺序")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")


class OrganizationProfile(Base):
    """Versioned lifecycle metadata for an organization identity.

    The original organizations table remains the stable authentication anchor;
    this additive table provides lifecycle controls without rewriting source data.

    表 system_organization_profiles：organization_id 唯一，附加编码、说明与 active/archived 状态。
    约束：本表不是 WorkspaceRecord 子类（组织不属于某个工作区，只带 version 乐观锁）；
    organizations 表仍是登录与鉴权的权威锚点，本表只做“增量元数据”，不复制名称等源数据；
    code 全平台唯一，删除组织不在本表处理（生命周期只提供归档）。
    """

    __tablename__ = "system_organization_profiles"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_organization_profile"),)
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organizations.id"), nullable=False, index=True,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, comment="全平台唯一组织编码")
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, comment="active/archived")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}


class WorkspaceLifecycle(WorkspaceRecord, Base):
    """Lifecycle state for one workspace while preserving its stable base ID.

    表 system_workspace_lifecycles：workspace_id 唯一，(organization_id, code) 唯一。
    约束：工作区 id 永远保留（历史数据、会话、审计都指向它），“停用”体现为 status="archived"；
    归档前要求同一组织至少保留一个启用工作区，且不允许在当前工作区归档自身（见 lifecycle_service）；
    旧数据由 ensure_all_lifecycle_profiles 在启动时补齐本表行，不做数据搬迁。
    """

    __tablename__ = "system_workspace_lifecycles"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_lifecycle_workspace"),
        UniqueConstraint("organization_id", "code", name="uq_workspace_lifecycle_code"),
    )
    organization_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organizations.id"), nullable=False, index=True,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="组织内唯一工作区编码")
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, comment="active/archived")


class WorkspaceSecurityPolicy(WorkspaceRecord, Base):
    """Workspace login policy; secrets and OIDC credentials live elsewhere.

    表 system_workspace_security_policies：workspace_id 唯一，每个工作区一行（读取时按需创建）。
    约束：allowed_cidrs/blocked_cidrs 存规范化后的网段字符串，且不允许同一网段同时出现在两个列表；
    这些策略只在登录链路（security_service.evaluate_login）生效，不会踢出已签发会话；
    OIDC 客户端密钥存放在密钥模块，本表只保存 client_secret_id 引用。
    """

    __tablename__ = "system_workspace_security_policies"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_workspace_security_policy"),)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_cidrs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    blocked_cidrs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    require_trusted_device: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_failed_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    lock_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_active_sessions: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    session_overflow: Mapped[str] = mapped_column(
        String(24), default="revoke_oldest", nullable=False, comment="revoke_oldest/block",
    )


class TrustedDevice(WorkspaceRecord, Base):
    """Hashed browser/device enrollment; the raw browser identifier is never stored.

    表 system_trusted_devices：(workspace_id, user_id, device_hash) 唯一。device_hash =
    sha256("<workspace_id>:<前端设备标识>")，原始设备标识不落库；不同工作区、不同用户
    对同一浏览器的哈希不同，避免跨工作区关联。
    状态机：pending（首次出现，待管理员批准）→ trusted（approved_by/approved_at 有值）
    或 revoked（revoked_at 有值）；require_trusted_device 开启时，非 trusted 设备直接拒绝登录。
    """

    __tablename__ = "system_trusted_devices"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "device_hash", name="uq_workspace_trusted_device"),
    )
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"), nullable=False, index=True)
    device_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, comment="pending/trusted/revoked")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginEvent(WorkspaceRecord, Base):
    """Append-only login decision evidence for security review.

    表 system_login_events：只追加、不更新（安全审计证据）。
    约束：user_id 可空（登录名不存在或账号已删除时仍要留证），username 保留脱敏后的登录名；
    reason/risk_level/details 必须是可以直接展示给管理员的内容，禁止写入密码、令牌等凭据；
    本表写入会在 evaluate_login 的拒绝分支里先行提交，确保“拒绝登录”这件事本身有据可查。
    """

    __tablename__ = "system_login_events"
    user_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    auth_method: Mapped[str] = mapped_column(String(24), default="local", nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, comment="success/failed/blocked")
    reason: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    device_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SessionContext(WorkspaceRecord, Base):
    """Security context captured when a session is issued for a workspace.

    表 system_session_contexts：session_id 唯一，与 sessions 表一对一（本表是附加安全信息）。
    约束：会话列表只按本表展示认证方式/IP/设备/风险等级，绝不返回 token_digest；
    device_hash 在接口层截断为前 12 位返回，避免完整哈希被用于跨库匹配；
    session 由既有 Cloud Brain 表签发，本表不参与校验，删除本表行不会让会话失效。
    """

    __tablename__ = "system_session_contexts"
    __table_args__ = (UniqueConstraint("session_id", name="uq_system_session_context"),)
    session_id: Mapped[str] = mapped_column(String(26), ForeignKey("sessions.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"), nullable=False, index=True)
    auth_method: Mapped[str] = mapped_column(String(24), default="local", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    device_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)


class OidcProvider(WorkspaceRecord, Base):
    """OIDC provider configuration with a reference to an encrypted client secret.

    表 system_oidc_providers：(workspace_id, code) 唯一。只保存端点与引用，密钥本体在密钥模块
    （client_secret_id 指向 Secret），token_auth_method 决定令牌交换的认证方式
    （client_secret_basic / client_secret_post / none=公共客户端+PKCE）。
    约束：issuer/authorization/token/jwks 四个地址必须通过 _validate_url（HTTPS、无凭据、无片段、
    且目标解析符合出网策略，内网地址需显式 allow_private）；enabled=True 时要求 default_role_code
    对应的角色存在且启用，机密客户端必须已配置 client secret。
    """

    __tablename__ = "system_oidc_providers"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_workspace_oidc_provider"),)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    issuer_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    authorization_endpoint: Mapped[str] = mapped_column(String(1000), nullable=False)
    token_endpoint: Mapped[str] = mapped_column(String(1000), nullable=False)
    jwks_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    client_id: Mapped[str] = mapped_column(String(300), nullable=False)
    client_secret_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    token_auth_method: Mapped[str] = mapped_column(
        String(32), default="client_secret_basic", nullable=False,
        comment="client_secret_basic/client_secret_post/none",
    )
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["openid", "profile", "email"], nullable=False)
    claims: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    allowed_domains: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    auto_provision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_role_code: Mapped[str] = mapped_column(String(80), default="member", nullable=False)
    allow_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_probe_status: Mapped[str] = mapped_column(String(24), default="never", nullable=False)
    last_probe_message: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class OidcLoginState(Base):
    """Single-use, short-lived OIDC state and encrypted PKCE verifier.

    表 system_oidc_login_states：state_digest（sha256(state)）唯一，明文 state 只出现在跳转 URL 中，
    不落库；PKCE verifier 以 AES-GCM 密文 + 随机 nonce + 密钥版本存储（密文与密钥版本必须一起读，
    支持密钥轮换后解密旧记录）。
    约束：一次登录一条记录，consumed_at 标记后不得复用（finish_authorization 用 SELECT ... FOR UPDATE
    防止并发重放）；expires_at 默认 10 分钟，过期即失效；redirect_uri 在发起时固定，回调时必须原样
    用于令牌交换，避免开放重定向。
    """

    __tablename__ = "system_oidc_login_states"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    oidc_nonce: Mapped[str] = mapped_column(String(120), nullable=False)
    verifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    verifier_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    device_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
