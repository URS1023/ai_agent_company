"""Chinese field contracts for system forms, OpenAPI and integration collections.

职责：system 模块的请求/响应契约（Pydantic 模型），同时是 OpenAPI 文档、前端表单校验与
接口集成用例的字段来源，因此所有 description 都写成可直接面向管理员的中文说明。

位置与依赖：不注册路由（system 模块路由前缀统一为 /api/v1/system），被 router.py /
governance_router.py 用作 response_model，被各 *_service.py 用作入参类型。
相邻契约：安全策略、设备与 OIDC 见 security_schemas.py，组织/工作区生命周期见 lifecycle_schemas.py，
两者都复用本文件的 InputModel 与 Page。

关键约定：
- InputModel 统一 extra="forbid"：多传字段直接报错，避免前端字段名笔误被静默忽略；
- 所有 Update 契约都必填 version（乐观锁），service 层用 check_version 比对后才写入；
- “不传=不修改、显式 null=清空”只对少数可空关系字段成立（见 DepartmentUpdate/UserUpdate 注释），
  其余字段显式传 null 会被 patch_record 拒绝；
- 枚举取值（DataScope、status、action 等）必须与 models.py、permissions.DATA_SCOPES 保持一致。
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")
# 数据范围取值须与 permissions.DATA_SCOPES、models.Role.data_scope 同步；
# self=本人 / department=本部门 / department_tree=本部门及下级 / custom=指定部门 / workspace=整个工作区。
DataScope = Literal["self", "department", "department_tree", "custom", "workspace"]


class InputModel(BaseModel):
    """所有写操作入参的基类：拒绝未声明字段，防止前端字段名笔误被静默丢弃。"""
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel, Generic[T]):
    """A bounded page; total counts only records visible to the current actor.

    分页响应的统一外壳：service 层必须用 allowed_owner_ids 等范围条件裁剪后再计数，
    保证 total 反映的是“当前操作人可见的记录数”，而不是全工作区总数（否则可用于探测
    他人数据规模）。page_size 上限由各接口的 Query(le=100) 控制。
    """

    items: list[T] = Field(description="当前页记录")
    total: int = Field(description="当前权限范围内的记录总数")
    page: int = Field(description="页码，从 1 开始")
    page_size: int = Field(description="每页条数，最多 100")


class RecordOut(BaseModel):
    """带乐观锁版本的基础输出：version 必须原样回传，否则更新会被拒绝。"""
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(description="记录唯一标识")
    version: int = Field(description="乐观锁版本，编辑时原样提交")
    created_at: datetime = Field(description="创建时间 UTC")
    updated_at: datetime = Field(description="最后修改时间 UTC")


class DepartmentCreate(InputModel):
    """新增部门：code 在创建后不可修改（唯一约束 + 审计历史引用），parent_id 为空表示根部门。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$", description="工作区内唯一部门编码")
    name: str = Field(min_length=1, max_length=160, description="部门名称")
    parent_id: str | None = Field(default=None, description="本工作区上级部门，空值为根部门")
    leader_user_id: str | None = Field(default=None, description="本工作区负责人用户标识")
    sort_order: int = Field(default=0, ge=0, le=100000, description="组内顺序，越小越靠前")
    enabled: bool = Field(default=True, description="是否启用")


class DepartmentUpdate(InputModel):
    """修改部门：version 必填；parent_id/leader_user_id 显式 null 表示“移为根部门/清空负责人”，
    其余字段传 null 会被 patch_record 拒绝（避免误清空名称等必填列）。"""
    version: int = Field(ge=1, description="当前记录版本，防止覆盖其他人的修改")
    name: str | None = Field(default=None, min_length=1, max_length=160, description="新部门名称，不传则不修改")
    parent_id: str | None = Field(default=None, description="上级部门；显式 null 表示移为根部门")
    leader_user_id: str | None = Field(default=None, description="负责人；显式 null 清空")
    sort_order: int | None = Field(default=None, ge=0, le=100000, description="新顺序")
    enabled: bool | None = Field(default=None, description="启用状态")


class DepartmentOut(RecordOut):
    """部门输出：用于部门树与成员表单回显，不含成员明细（成员数走用户列表接口）。"""
    code: str = Field(description="部门编码")
    name: str = Field(description="部门名称")
    parent_id: str | None = Field(description="上级部门标识")
    leader_user_id: str | None = Field(description="负责人用户标识")
    sort_order: int = Field(description="排序值")
    enabled: bool = Field(description="是否启用")


class PositionCreate(InputModel):
    """新增岗位：只描述职责，不会带来任何权限；授权一律通过角色分配。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$", description="工作区内唯一岗位编码")
    name: str = Field(min_length=1, max_length=160, description="岗位名称")
    description: str = Field(default="", max_length=1000, description="岗位职责，不自动赋予角色权限")
    enabled: bool = Field(default=True, description="是否启用")


class PositionUpdate(InputModel):
    """修改岗位：version 必填，编码不可改（成员通过 code 关联导入模板）。"""
    version: int = Field(ge=1, description="当前记录版本")
    name: str | None = Field(default=None, min_length=1, max_length=160, description="岗位名称")
    description: str | None = Field(default=None, max_length=1000, description="职责说明")
    enabled: bool | None = Field(default=None, description="是否启用")


class PositionOut(RecordOut):
    """岗位输出：成员资料里以标识列表保存，展示时用本结构的 name 回显。"""
    code: str = Field(description="岗位编码")
    name: str = Field(description="岗位名称")
    description: str = Field(description="职责说明")
    enabled: bool = Field(description="是否启用")


class RoleCreate(InputModel):
    """新增角色：permissions 必须是权限目录中的完整编码，data_scope 与 department_ids 一同生效；
    创建者只能授予自己已有的动作、且不能扩大自己无权委派的数据范围（role_service.validate_role_policy）。"""
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$", description="工作区内唯一角色编码")
    name: str = Field(min_length=1, max_length=160, description="角色名称")
    description: str = Field(default="", max_length=1000, description="角色用途")
    permissions: list[str] = Field(default_factory=list, max_length=500, description="权限目录中的完整编码，空列表不授权")
    data_scope: DataScope = Field(default="self", description="本人/部门/部门及下级/指定部门/整个工作区")
    department_ids: list[str] = Field(default_factory=list, max_length=500, description="custom 范围允许的本工作区部门")
    enabled: bool = Field(default=True, description="是否启用；停用后所有绑定用户立即失去本角色授权")


class RoleUpdate(InputModel):
    """修改角色：version 必填；permissions 是“整体替换”语义（显式空列表即清空授权），
    内置 workspace_admin 角色的权限与状态字段被接口层拒绝修改。"""
    version: int = Field(ge=1, description="当前记录版本")
    name: str | None = Field(default=None, min_length=1, max_length=160, description="角色名称")
    description: str | None = Field(default=None, max_length=1000, description="角色用途")
    permissions: list[str] | None = Field(default=None, max_length=500, description="替换完整授权列表，显式空列表清空")
    data_scope: DataScope | None = Field(default=None, description="新的数据范围")
    department_ids: list[str] | None = Field(default=None, max_length=500, description="指定部门列表")
    enabled: bool | None = Field(default=None, description="启用状态")


class RoleOut(RecordOut):
    """角色输出：member_count 为当前绑定用户数（只统计本工作区），用于删除前的引用提示。"""
    code: str = Field(description="角色编码")
    name: str = Field(description="角色名称")
    description: str = Field(description="角色用途")
    permissions: list[str] = Field(description="授权动作编码")
    data_scope: DataScope = Field(description="与本角色授权动作绑定的数据范围")
    department_ids: list[str] = Field(description="指定部门标识列表")
    enabled: bool = Field(description="是否启用")
    builtin: bool = Field(description="是否内置角色")
    member_count: int = Field(default=0, description="本工作区绑定用户数")


class UserCreate(InputModel):
    """新增用户：一次调用同时创建登录身份（共享 users/AuthIdentity）、工作区成员关系、
    成员资料与角色绑定；password 只作为临时密码，登录后强制修改。"""
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$", description="全平台唯一登录名，不区分大小写")
    display_name: str = Field(min_length=1, max_length=160, description="显示姓名")
    password: str = Field(min_length=10, max_length=256, repr=False, description="初始临时密码，至少 10 位且包含字母数字", json_schema_extra={"writeOnly": True})
    role_ids: list[str] = Field(default_factory=list, max_length=50, description="本工作区角色标识列表")
    department_id: str | None = Field(default=None, description="所属本工作区部门")
    position_ids: list[str] = Field(default_factory=list, max_length=50, description="本工作区岗位标识列表")
    employee_code: str = Field(default="", max_length=80, description="员工编号")
    email: str = Field(default="", max_length=254, description="联系邮箱，可为空")
    phone: str = Field(default="", max_length=40, description="联系电话，可为空")


class UserUpdate(InputModel):
    """修改成员：version 指成员资料（MemberProfile）版本；display_name/status 属于共享账号字段，
    还需通过 require_global_account_control 检查该账号是否仅属于本工作区。"""
    version: int = Field(ge=1, description="当前成员资料版本")
    display_name: str | None = Field(default=None, min_length=1, max_length=160, description="显示姓名")
    status: Literal["active", "disabled"] | None = Field(default=None, description="账号启用或停用；停用撤销会话")
    role_ids: list[str] | None = Field(default=None, max_length=50, description="替换本工作区角色；空列表清空授权")
    department_id: str | None = Field(default=None, description="所属部门；显式 null 清空")
    position_ids: list[str] | None = Field(default=None, max_length=50, description="替换本工作区岗位列表")
    employee_code: str | None = Field(default=None, max_length=80, description="员工编号")
    email: str | None = Field(default=None, max_length=254, description="联系邮箱")
    phone: str | None = Field(default=None, max_length=40, description="联系电话")


class UserOut(BaseModel):
    """用户输出：只包含工作区视角的成员信息，不返回哈希、令牌或全局会话标识；
    workspace_role 仅用于兼容旧前端，真实授权一律看 permissions 与角色列表。"""
    id: str = Field(description="用户标识")
    username: str = Field(description="本地登录名")
    display_name: str = Field(description="显示姓名")
    status: str = Field(description="账号状态")
    workspace_role: str = Field(description="旧版兼容身份；实际功能使用 permissions")
    role_ids: list[str] = Field(description="本工作区分配的角色标识")
    role_names: list[str] = Field(description="角色名称，包括已停用角色的名称")
    department_id: str | None = Field(description="所属部门标识")
    department_name: str = Field(description="所属部门名称")
    position_ids: list[str] = Field(description="岗位标识列表")
    employee_code: str = Field(description="员工编号")
    email: str = Field(description="联系邮箱")
    phone: str = Field(description="联系电话")
    version: int = Field(description="成员资料版本")
    created_at: datetime = Field(description="创建时间 UTC")
    updated_at: datetime = Field(description="最后修改时间 UTC")
    must_change_password: bool = Field(description="是否要求用户修改临时密码")


class SelfProfileUpdate(InputModel):
    """Fields an authenticated member may maintain without administrator rights.

    自助资料修改：只允许改显示姓名与本工作区的联系方式，部门/岗位/角色/状态一律不可自改；
    同样需要 version，避免两个标签页互相覆盖。
    """

    version: int = Field(ge=1, description="当前成员资料版本，用于防止覆盖并发修改")
    display_name: str | None = Field(default=None, min_length=1, max_length=160, description="本人显示姓名")
    email: str | None = Field(default=None, max_length=254, description="当前工作区联系邮箱")
    phone: str | None = Field(default=None, max_length=40, description="当前工作区联系电话")


class SelfProfileOut(UserOut):
    """个人资料输出：在成员字段上补充组织/工作区名称与岗位名称，供“我的资料”页面展示。"""
    organization_name: str = Field(description="当前组织名称")
    workspace_name: str = Field(description="当前工作区名称")
    position_names: list[str] = Field(description="当前工作区岗位名称")


class UserImportIssue(BaseModel):
    """导入校验问题：message 面向管理员，必须可操作且不得回显密码原文。"""
    row: int = Field(ge=2, description="Excel 行号，从第 2 行开始")
    field: str = Field(description="出错的中文列名；整行错误使用“整行”")
    message: str = Field(description="可直接展示给管理员的修正说明，不包含密码原文")


class UserImportPreviewRow(BaseModel):
    """导入预览行：只回显脱敏字段（不含临时密码），action 是校验阶段的预计动作。"""
    row: int = Field(ge=2, description="Excel 行号")
    username: str = Field(description="规范化后的登录账号")
    display_name: str = Field(description="显示姓名")
    department_code: str = Field(description="部门编码")
    role_codes: list[str] = Field(description="角色编码")
    action: Literal["create", "update", "unchanged", "invalid"] = Field(description="导入后预计动作")


class UserImportPreview(BaseModel):
    """导入预览结果：valid=false 时整批不写入；checksum 用于绑定“校验过的文件”与后续提交。"""
    valid: bool = Field(description="全部行是否通过校验；false 时不会写入任何数据")
    file_name: str = Field(description="已校验文件名")
    checksum: str = Field(description="文件 SHA-256，用于确认预览与提交是否为同一文件")
    total_rows: int = Field(ge=0, description="有效数据行数，不含表头和空行")
    create_count: int = Field(ge=0, description="预计新增账号数")
    update_count: int = Field(ge=0, description="预计更新成员数")
    unchanged_count: int = Field(ge=0, description="无需修改的成员数")
    issues: list[UserImportIssue] = Field(description="按行排序的校验问题，最多返回 200 条")
    preview: list[UserImportPreviewRow] = Field(description="前 50 行脱敏预览，不返回临时密码")


class UserImportResult(BaseModel):
    """导入结果：ok=true 表示已在单一事务内全部落库，任何一个行错误都不会留下部分数据。"""
    ok: bool = Field(description="是否已在单一事务中完成导入")
    checksum: str = Field(description="实际提交文件 SHA-256")
    total_rows: int = Field(ge=0, description="处理的数据行数")
    created_count: int = Field(ge=0, description="新增账号数")
    updated_count: int = Field(ge=0, description="更新成员数")
    unchanged_count: int = Field(ge=0, description="未发生修改的成员数")
    message: str = Field(description="导入结果摘要")


class PermissionHistoryActor(BaseModel):
    """权限历史的操作人：系统/批量任务没有真实用户时留空字符串，前端显示“系统”。"""
    id: str = Field(description="操作人用户标识；系统任务可为空字符串")
    display_name: str = Field(description="操作人显示姓名")
    username: str = Field(description="操作人登录账号")


class PermissionHistoryTarget(BaseModel):
    """变更对象：类型受限为 user/role/resource_grant，与 permission_history.ACTION_LABELS 对应。"""
    type: Literal["user", "role", "resource_grant"] = Field(description="变更对象类型")
    id: str = Field(description="变更对象标识")
    label: str = Field(description="变更发生时的对象名称或账号")


class PermissionHistoryItem(BaseModel):
    """单条权限变更：before/after 是脱敏快照（不含密码、令牌），删除类操作 after 为空对象。"""
    id: str = Field(description="审计记录标识")
    occurred_at: datetime = Field(description="变更时间 UTC")
    action: str = Field(description="稳定的机器动作编码")
    action_label: str = Field(description="中文动作名称")
    summary: str = Field(description="适合列表展示的变更摘要")
    actor: PermissionHistoryActor
    target: PermissionHistoryTarget
    before: dict = Field(description="变更前权限快照；创建操作为空对象")
    after: dict = Field(description="变更后权限快照；删除或移除操作为空对象")
    source: Literal["manual", "bulk_import", "system"] = Field(description="变更来源")


class PasswordReset(InputModel):
    """重置密码：管理员设置的是“临时密码”，用户首次登录必须修改；有效期上限 168 小时。
    该接口同时会撤销目标用户的全部有效会话（共享账号跨工作区时会被拦截）。"""
    new_password: str = Field(min_length=10, max_length=256, repr=False, description="新的临时密码，不在响应或日志中返回", json_schema_extra={"writeOnly": True})
    expires_in_hours: int = Field(default=72, ge=1, le=168, description="临时密码有效小时数，默认 72、最多 168")


class ResourceGrantCreate(InputModel):
    """新增资源授权：resource_type 必须在 admin_service.RESOURCE_MODELS 注册，且目标资源必须
    真实存在并属于本工作区；actions 只能取该资源已登记的权限动作，不会凭空扩大动作集合。"""
    resource_type: str = Field(min_length=1, max_length=64, description="权限目录模块编码，如 knowledge、dashboards")
    resource_id: str = Field(min_length=1, max_length=80, description="当前工作区内资源标识")
    subject_type: Literal["user", "role"] = Field(description="授权对象类型")
    subject_id: str = Field(min_length=1, max_length=26, description="当前工作区用户或角色标识")
    actions: list[str] = Field(min_length=1, max_length=10, description="资源动作列表，例如 view、edit、execute")


class ResourceGrantOut(RecordOut):
    """资源授权输出：撤销时只删除本记录，不触碰被授权的业务对象本身。"""
    resource_type: str = Field(description="资源模块编码")
    resource_id: str = Field(description="资源标识")
    subject_type: str = Field(description="对象类型 user/role")
    subject_id: str = Field(description="用户或角色标识")
    actions: list[str] = Field(description="允许动作")


class MenuUpdate(InputModel):
    """菜单显示设置：label 为空字符串表示恢复系统内置名称（这是唯一允许空值的名称类字段）；
    隐藏菜单只影响导航展示，不会收回后端权限。"""
    label: str = Field(default="", max_length=160, description="自定义显示名称；空值使用系统原名")
    visible: bool = Field(default=True, description="是否显示；隐藏菜单不会改变后端权限")
    sort_order: int = Field(default=0, ge=0, le=100000, description="组内显示顺序")


class SettingUpdate(InputModel):
    """系统参数：key 由路径传入且必须已在 DEFAULT_SETTINGS 登记；值按参数类型分别校验
    （page_size 为 10–100 整数、timezone 必须是合法 IANA 时区、其余为非空短文本）。"""
    value: str | int | float | bool | list | dict = Field(description="非敏感参数值；凭据必须使用密钥模块")
    description: str = Field(default="", max_length=1000, description="参数用途和取值说明")


class DictionaryCreate(InputModel):
    """新增字典项：namespace + key 在工作区内唯一；字典只承载展示数据，不参与权限判定。"""
    namespace: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$", description="字典类型编码")
    key: str = Field(min_length=1, max_length=120, description="类型内唯一项编码")
    label: str = Field(min_length=1, max_length=200, description="显示名称")
    value: str | int | float | bool | list | dict = Field(description="字典值")
    sort_order: int = Field(default=0, ge=0, le=100000, description="显示顺序")
    enabled: bool = Field(default=True, description="是否启用")


class DictionaryUpdate(InputModel):
    """修改字典项：version 必填；namespace/key 不允许修改（唯一约束与前端缓存键依赖它们）。"""
    version: int = Field(ge=1, description="当前记录版本")
    label: str | None = Field(default=None, min_length=1, max_length=200, description="显示名称")
    value: str | int | float | bool | list | dict | None = Field(default=None, description="新的字典值")
    sort_order: int | None = Field(default=None, ge=0, le=100000, description="显示顺序")
    enabled: bool | None = Field(default=None, description="是否启用")


class ActionResult(BaseModel):
    """通用操作结果：删除、停用等无实体返回的接口统一用它，message 可直接展示给用户。"""
    ok: bool = Field(default=True, description="业务操作是否成功")
    message: str = Field(default="操作完成", description="适合直接显示的结果说明")
