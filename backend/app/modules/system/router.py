"""Normal administration APIs; the AI module invokes the same authorized services."""
# 职责：系统管理域的日常管理 API —— 组织/工作区信息、成员（用户）、角色、部门与岗位、权限目录与
# 权限历史、资源授权、菜单、会话、系统参数、业务字典；AI 工具与页面请求调用的是同一批服务，
# 因此这里没有“给智能体专用的旁路”。
#
# 位置与依赖：路由前缀 /api/v1/system（tags=["系统管理"]），本文件只做 HTTP 编排 —— 解析路径、
# 查询与表单参数，注入 AuthContext，把请求转交服务层（admin_service / organization_service /
# permission_history / role_service / user_bulk / user_service）；版本校验、数据范围裁剪、审计写库
# 都在服务层，这里不写业务规则，也不自行 commit/rollback（服务的写操作内部已提交并记录审计）。
# 组织生命周期、工作区、登录安全策略与企业 SSO 在同一前缀的 governance_router.py，不在本文件。
#
# 权限码规律：统一为 system.<域>.<动作>，动作以 view（只读）/ manage（写）为主，另有细粒度动作
# system.users.import、system.users.export、system.users.reset_password、
# system.users.assign_roles、system.permissions.history。校验统一走
# common/authorization.require_permission，且权限码必须已登记在
# system.permissions.ALL_PERMISSIONS 中，否则一律拒绝（默认拒绝）。数据范围按“授予该动作的那个
# 角色”单独计算（allowed_owner_ids / require_user_scope / allowed_department_ids），因此本文件
# 各路由注释标注的权限码即服务层真实校验值，新增路由必须与之一致。
#
# 与旧版路径映射的关系：common/authorization.enforce_request_permission 只对未版本化的旧 Cloud
# Brain 路径（/api/... 且不以 /api/v1/ 开头）按 LEGACY_DOMAINS 推导 <域>.<动作> 再校验；本文件
# 所有路径都以 /api/v1/ 开头，会在该函数第一段就 return，不参与任何推断。也就是说这里的权限
# 完全由各路由显式声明的授权控制，不能依赖旧的路径映射兜底。
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.common.authorization import require_permission, require_user_scope
from app.common.deps import AuthContext, get_current_context
from app.models import Organization
from app.modules.system import admin_service as administration
from app.modules.system import organization_service as organization
from app.modules.system import permission_history
from app.modules.system import role_service as roles
from app.modules.system import user_bulk
from app.modules.system import user_service as users
from app.modules.system.models import Department, Position, Role
from app.modules.system.schemas import (
    ActionResult, DepartmentCreate, DepartmentOut, DepartmentUpdate, DictionaryCreate,
    DictionaryUpdate, MenuUpdate, Page, PasswordReset, PositionCreate, PositionOut,
    PermissionHistoryItem, PositionUpdate, ResourceGrantCreate, ResourceGrantOut,
    RoleCreate, RoleOut, RoleUpdate, SettingUpdate, UserCreate, UserImportPreview,
    UserImportResult, UserOut, UserUpdate,
)
from app.modules.system.service_helpers import scoped_record

router = APIRouter(prefix="/api/v1/system", tags=["系统管理"], responses={
    401: {"description": "未登录、会话失效或密码已更改"},
    403: {"description": "无操作权限、数据范围不足或需先修改临时密码"},
    404: {"description": "记录不存在或不属于当前工作区"},
    409: {"description": "版本冲突、唯一编码重复或仍存在业务引用"},
    422: {"description": "字段校验失败或配置不符合业务约束"},
})
# 所有端点的统一依赖：解析会话得到 AuthContext（含当前工作区与用户）；未登录、会话失效或密码已更改
# 时由该依赖抛 401，因此本文件的处理函数一律不重复做登录校验
Context = Annotated[AuthContext, Depends(get_current_context)]
# 分页起点固定为 1；page_size 上限 100 与服务层 limit 语义一致，避免一次拉取整个工作区
PageNumber = Annotated[int, Query(ge=1, description="页码，从 1 开始")]
PageSize = Annotated[int, Query(ge=1, le=100, description="每页记录数，最多 100")]
# 搜索词只用于 LIKE 匹配（服务层 autoescape），不会被解释成 SQL 片段
Search = Annotated[str, Query(max_length=160, description="名称或登录名的文本搜索，不解释为 SQL")]
# 记录标识：本库主键是 26 位字符串，空值与超长值在进入服务层前就被 422 拦下；
# scoped_record 会再按 ctx.workspace_id 过滤，使跨工作区 id 表现为“不存在”而不是 403
RecordId = Annotated[str, Path(min_length=1, max_length=26, description="当前工作区内的记录标识")]


@router.get("/organization", operation_id="system_get_organization", summary="查看当前组织和工作区")
def organization_info(ctx: Context) -> dict:
    """返回当前已授权组织、工作区和用户身份，不枚举其他组织。"""
    # 路由：GET /api/v1/system/organization；权限：仅要求已登录（无独立权限码）——
    # 返回的就是请求自身所属的组织与工作区，不构成对他人信息的枚举。
    # 数据范围：只用 ctx.workspace.organization_id 与 ctx.workspace_id 两条已绑定标识，不接受请求参数。
    # 副作用：只读，不写审计。
    record = ctx.db.get(Organization, ctx.workspace.organization_id)
    return {"organization": {"id": record.id, "name": record.name},
            "workspace": {"id": ctx.workspace_id, "name": ctx.workspace.name}}


@router.get("/users", response_model=Page[UserOut], operation_id="system_list_users", summary="分页查询工作区用户")
def list_users(ctx: Context, page: PageNumber = 1, page_size: PageSize = 20, q: Search = "",
               status: Annotated[Literal["active", "disabled"] | None, Query(description="账号启停状态")] = None,
               department_id: Annotated[str | None, Query(description="按所属部门筛选")] = None):
    """需要 system.users.view；只返回当前工作区成员，不包含密码、密钥或会话令牌。"""
    # 路由：GET /api/v1/system/users（分页）。权限：system.users.view，并叠加该动作的人员数据范围
    # （allowed_owner_ids），因此 total 是“操作人可见的人数”而非工作区人数。
    # 数据范围：限定 ctx.workspace_id，department_id/status/q 只在可见集合内继续过滤，不能放大范围。
    # 副作用：只读，不写审计。
    return users.list_users(ctx, page, page_size, q, status, department_id)


def _xlsx_response(data: bytes, file_name: str) -> StreamingResponse:
    """把内存中的 xlsx 字节流包装成下载响应（非路由的内部辅助函数）。"""
    # 参数：data 已生成好的工作簿字节；file_name 中文文件名。
    # 返回：StreamingResponse，Content-Disposition 同时给出 ASCII 回退名与 filename*（RFC 5987
    # 的 UTF-8 编码）；Cache-Control: no-store —— 导出内容含成员资料，禁止中间层缓存。
    # 副作用：无（不查库、不写审计）。
    headers = {
        "Content-Disposition": f"attachment; filename=users.xlsx; filename*=UTF-8''{quote(file_name)}",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get(
    "/users/import-template",
    operation_id="system_download_user_import_template",
    summary="下载当前工作区用户导入模板",
    responses={200: {"description": "包含字段说明和当前部门编码下拉项的 XLSX 模板", "content": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
            "schema": {"type": "string", "format": "binary"},
        },
    }}},
)
def download_user_import_template(ctx: Context):
    """需要 system.users.import；模板不包含用户数据、密码、令牌或已停用部门。"""
    # 路由：GET /api/v1/system/users/import-template，返回 XLSX 附件（200）。
    # 权限：system.users.import（模板本身不含任何成员数据，因此不需要 manage/assign_roles）。
    # 数据范围：模板按当前工作区实时生成，下拉项只取本工作区启用中的部门/岗位/角色编码。
    # 副作用：只读，不写审计；生成逻辑在 user_bulk.import_template。
    return _xlsx_response(user_bulk.import_template(ctx), "用户导入模板.xlsx")


@router.get(
    "/users/export",
    operation_id="system_export_users",
    summary="按当前筛选导出工作区用户",
    responses={200: {"description": "可再次用于更新导入的 XLSX；临时密码列始终为空", "content": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
            "schema": {"type": "string", "format": "binary"},
        },
    }}},
)
def export_users(
    ctx: Context,
    q: Search = "",
    status: Annotated[Literal["active", "disabled"] | None, Query(description="账号启停状态")] = None,
    department_id: Annotated[str | None, Query(description="按所属部门筛选")] = None,
):
    """需要 system.users.export；沿用人员数据范围，最多导出 10000 条且防止 Excel 公式注入。"""
    # 路由：GET /api/v1/system/users/export，返回 XLSX 附件（200）。
    # 权限：system.users.export（与查看权限分开授予，避免“能看”被默认解释成“能带走”）。
    # 数据范围：复用 users.list_users 的可见集合（system.users.export + allowed_owner_ids），
    # 因此“能看的人”与“能导出的人”是同一批；超过 10000 条抛 ValidationError(422) 要求先筛选。
    # 副作用：只读，不写审计；文件名带本地时间戳，密码列始终为空（状态输出为中文）。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _xlsx_response(
        user_bulk.export_users(ctx, q, status, department_id),
        f"工作区用户-{stamp}.xlsx",
    )


@router.post(
    "/users/import-preview",
    response_model=UserImportPreview,
    operation_id="system_preview_user_import",
    summary="校验用户导入文件并生成脱敏预览",
)
async def preview_user_import(
    ctx: Context,
    file: Annotated[UploadFile, File(description="不超过 5 MB、最多 1000 行的 .xlsx 文件")],
    update_existing: Annotated[bool, Form(description="允许按登录账号更新当前工作区已有成员；不允许批量改密码")] = False,
):
    """只读校验所有行、引用编码、授权边界和管理员保护规则；不会写入任何用户。"""
    # 路由：POST /api/v1/system/users/import-preview（multipart，200）。
    # 权限：system.users.import + system.users.manage + system.users.assign_roles（导入会建账号
    # 并分配角色，三者缺一不可）。
    # 数据范围：仅当前工作区；逐行沿用单条接口的部门范围、角色委派边界与最后管理员保护规则，
    # 因此“批量预览”不会比单条新建更宽松。
    # 副作用：不写库、不写审计；只读读取本工作区编码表。file.read 多读 1 字节用于识别超限文件
    # （MAX_FILE_BYTES + 1），因此这里不依赖 UploadFile.size。
    data = await file.read(user_bulk.MAX_FILE_BYTES + 1)
    preview, _ = user_bulk.validate_import(ctx, data, file.filename or "users.xlsx", update_existing)
    return preview


@router.post(
    "/users/import",
    response_model=UserImportResult,
    status_code=201,
    operation_id="system_import_users",
    summary="原子提交已校验的用户导入文件",
)
async def import_users(
    ctx: Context,
    file: Annotated[UploadFile, File(description="与预览阶段 SHA-256 完全一致的 .xlsx 文件")],
    expected_checksum: Annotated[str, Form(min_length=64, max_length=64, description="预览响应中的文件 SHA-256")],
    update_existing: Annotated[bool, Form(description="必须与预览阶段的更新策略一致")] = False,
):
    """重新校验后在一个数据库事务中写入；任一行失败时整批回滚，并记录逐用户及批次权限历史。"""
    # 路由：POST /api/v1/system/users/import（multipart，201）。
    # 权限：system.users.import + system.users.manage + system.users.assign_roles。
    # 数据范围：仅当前工作区；expected_checksum 必须与预览响应一致（此处统一转小写比较），
    # 否则 409 —— 防止“预览通过的文件”与“提交的文件”不是同一份。
    # 副作用：加工作区行锁后逐行创建/更新账号、成员关系与角色，写逐用户审计与批次汇总审计
    # （source="bulk_import"）后统一提交；异常路径整体回滚，不会留下部分导入。
    data = await file.read(user_bulk.MAX_FILE_BYTES + 1)
    return user_bulk.import_users(
        ctx, data, file.filename or "users.xlsx", update_existing, expected_checksum.lower(),
    )


@router.post("/users", response_model=UserOut, status_code=201, operation_id="system_create_user", summary="创建账号并分配组织角色")
def create_user(payload: UserCreate, ctx: Context):
    """账号、成员资料和角色在同一事务中创建；临时密码 72 小时有效，首次登录必须修改。"""
    # 路由：POST /api/v1/system/users（201）。权限：system.users.manage + system.users.assign_roles，
    # 两个动作都要通过；department_id 还必须在 allowed_department_ids("system.users.manage") 范围内，
    # 因此“能建人”不等于“能建到任意部门”。
    # 数据范围：只写当前工作区；登录名是跨工作区共享身份，重名由唯一约束冲突暴露为 409。
    # 副作用：服务层先 lock_workspace 串行化同工作区的新建与授权，再写账号、成员关系与角色，
    # 由 commit_change("user.create") 提交并写审计（含 after 权限快照）；密码只以哈希入库，
    # 响应不含明文，且 must_change_password=True 强制首次登录改密。
    return users.create_user(ctx, payload)


@router.get("/users/{record_id}", response_model=UserOut, operation_id="system_get_user", summary="查看用户资料及角色")
def get_user(record_id: RecordId, ctx: Context):
    """需要 system.users.view；跨工作区用户标识按不存在处理。"""
    # 路由：GET /api/v1/system/users/{record_id}。权限：system.users.view，并在调用服务前显式校验
    # 该动作的人员数据范围 —— 超出范围是 403，而记录不属于本工作区时由服务层按 404 处理
    # （不区分“不存在”与“属于别的工作区”，避免拿 id 探测他人成员）。
    # 副作用：只读，不写审计；返回资料与角色名，不含密码哈希与会话令牌。
    require_user_scope(ctx, "system.users.view", record_id)
    return users.user_out(ctx, record_id)


@router.patch("/users/{record_id}", response_model=UserOut, operation_id="system_update_user", summary="修改用户、状态及角色")
def update_user(record_id: RecordId, payload: UserUpdate, ctx: Context):
    """提交成员 version；停用账号会撤销会话，不能停用或降级最后一个有效管理员。"""
    # 路由：PATCH /api/v1/system/users/{record_id}。权限：system.users.manage（改角色时另需
    # system.users.assign_roles），两者都要通过各自的人员数据范围；display_name/status 属于跨工作区
    # 共享账号字段，还需 require_global_account_control，仅本工作区管理员不足以改动。
    # 数据范围：目标必须在操作人可见集合内，调入的部门必须在 allowed_department_ids 范围内。
    # 副作用：服务层先 lock_workspace 加工作区行锁，再落库并写 user.update 审计（含 before/after
    # 权限快照）；停用会撤销该账号的会话。最后管理员保护必须先锁工作区行再判定，否则并发请求会
    # 同时通过检查、把工作区变成无人可管理。
    return users.update_user(ctx, record_id, payload)


@router.delete("/users/{record_id}", response_model=ActionResult, operation_id="system_remove_user", summary="移除工作区成员")
def remove_user(record_id: RecordId, ctx: Context):
    """删除本工作区成员关系和角色，保留账号及历史审计；不删除其他工作区数据。"""
    # 路由：DELETE /api/v1/system/users/{record_id}（返回 ActionResult，不是 204）。
    # 权限：system.users.manage + system.users.assign_roles，两个动作各自都要通过人员数据范围校验。
    # 数据范围：只删本工作区的 UserRole / MemberProfile / WorkspaceMember；全局账号与其他工作区的
    # 成员关系不动，因此“移除成员”不等于“删除账号”。
    # 副作用：由服务层撤销该成员的授权并写 user.remove_from_workspace 审计（保留 before 快照）；
    # 目标仍是部门负责人或最后一个启用中的管理员时返回 409，必须先改负责人/先加管理员。
    users.remove_user(ctx, record_id)
    return {"ok": True, "message": "已移除本工作区成员，账号与历史记录保留"}


@router.post("/users/{record_id}/password-reset", response_model=ActionResult,
             operation_id="system_reset_user_password", summary="重置临时密码并撤销旧会话")
def reset_user_password(record_id: RecordId, payload: PasswordReset, ctx: Context):
    """需要单独的 system.users.reset_password 权限；新密码不会出现在响应或审计中。"""
    # 路由：POST /api/v1/system/users/{record_id}/password-reset。权限：
    # system.users.reset_password，并叠加该动作的人员数据范围；密码是账号级字段，另需
    # require_global_account_control（共享身份的其他工作区管理员也要认可）。
    # 数据范围：仅当前工作区成员，超出范围返回 403。
    # 副作用：写入新的密码哈希并置 must_change_password=True，撤销该账号全部未撤销会话，
    # 写 user.password_reset 审计（只记动作，不记密码）；响应只有固定文案，不含明文或哈希。
    users.reset_password(ctx, record_id, payload)
    return {"ok": True, "message": "旧会话已撤销，用户下次登录须修改临时密码"}


@router.get("/users/{record_id}/permissions", operation_id="system_get_user_permissions", summary="查看用户有效权限及来源")
def effective_user_permissions(record_id: RecordId, ctx: Context):
    """列出授权来源角色及其对应数据范围；停用账号返回空有效权限。"""
    # 路由：GET /api/v1/system/users/{record_id}/permissions。权限：system.permissions.view，
    # 并且目标必须在 system.permissions.view 的人员数据范围内（排障用途，不能用来枚举他人权限）。
    # 数据范围：仅当前工作区成员，非成员按 404。
    # 副作用：只读；服务层用临时 AuthContext 复用 access_policy，因此不会污染当前请求的权限缓存，
    # 也不会把“查看时的瞬时权限”写回会话或审计。
    return roles.effective_user_permissions(ctx, record_id)


@router.get("/roles", response_model=Page[RoleOut], operation_id="system_list_roles", summary="分页查询角色")
def list_roles(ctx: Context, page: PageNumber = 1, page_size: PageSize = 20, q: Search = ""):
    """分页查询当前工作区的角色。

    路由：GET /api/v1/system/roles。权限：system.roles.view。
    数据范围：限定 ctx.workspace_id；角色不按人员数据范围裁剪 —— 角色本身就是授权的载体，
    看到角色名称不泄露成员数据。q 只按角色名称模糊匹配（autoescape，不解释为 SQL）。
    副作用：只读，不写审计；items 会逐条统计 member_count，分页按 created_at + id 稳定排序。
    """
    return roles.list_roles(ctx, page, page_size, q)


@router.post("/roles", response_model=RoleOut, status_code=201, operation_id="system_create_role", summary="创建角色与功能数据授权")
def create_role(payload: RoleCreate, ctx: Context):
    """只能使用权限目录中的编码，不得授予超出当前操作者权限或数据范围的能力。"""
    # 路由：POST /api/v1/system/roles（201）。权限：system.roles.manage；委派边界由
    # validate_role_policy 逐条比对操作人自己的权限与数据范围，越权授予一律拒绝。
    # 数据范围：新角色固定归属 ctx.workspace_id，code 在工作区内唯一（重复 409）；自定义部门的
    # data_scope="custom" 时 department_ids 必须都在本工作区且逐级可达。
    # 副作用：写 system_roles 一行，写 role.create 审计（含 after 权限快照）后提交；权限与部门集合
    # 都会去重排序后再存，保证同一集合只有一种持久化表示（否则审计差异会误报变更）。
    return roles.create_role(ctx, payload)


@router.get("/roles/{record_id}", response_model=RoleOut, operation_id="system_get_role", summary="查看角色详情")
def get_role(record_id: RecordId, ctx: Context):
    """查看角色详情。

    路由：GET /api/v1/system/roles/{record_id}。权限：system.roles.view。
    数据范围：scoped_record 先按 ctx.workspace_id 过滤，跨工作区 id 表现为 404（不泄露存在性）。
    副作用：只读，不写审计；返回权限、数据范围与 member_count，不返回成员名单。
    """
    require_permission(ctx, "system.roles.view")
    return roles.role_out(ctx, scoped_record(ctx, Role, record_id))


@router.patch("/roles/{record_id}", response_model=RoleOut, operation_id="system_update_role", summary="修改角色与授权范围")
def update_role(record_id: RecordId, payload: RoleUpdate, ctx: Context):
    """version 不一致返回 409；更新后的权限在后续请求中生效，不依赖重新登录。"""
    # 路由：PATCH /api/v1/system/roles/{record_id}。权限：system.roles.manage，且改动后的权限集合
    # 仍要满足“不得超出操作者自己的权限与数据范围”。
    # 数据范围：仅当前工作区角色；内置角色 workspace_admin 只允许改名称与描述，改权限/状态返回 422。
    # 副作用：加工作区行锁后更新 system_roles，写 role.update 审计（fields + before/after 快照）；
    # 授权实时生效 —— access_policy 每次请求重新读取角色，因此不需要用户重新登录。
    return roles.update_role(ctx, record_id, payload)


@router.delete("/roles/{record_id}", response_model=ActionResult, operation_id="system_delete_role", summary="删除未分配的自定义角色")
def delete_role(record_id: RecordId, ctx: Context):
    """删除未分配的自定义角色。

    路由：DELETE /api/v1/system/roles/{record_id}（返回 ActionResult，不是 204）。
    权限：system.roles.manage。数据范围：仅当前工作区角色，否则 404。
    约束：内置角色、仍有用户绑定、或仍被资源授权引用时返回 409 —— 删除不可回滚，必须先解除引用。
    副作用：加工作区行锁后删除 system_roles 行，写 role.delete 审计（保留 before 快照）；角色的
    历史授权变更记录保留在审计表中，不随角色删除。
    """
    roles.delete_role(ctx, record_id)
    return {"ok": True}


@router.get("/departments", response_model=Page[DepartmentOut], operation_id="system_list_departments", summary="查询组织部门")
def list_departments(ctx: Context, page: PageNumber = 1, page_size: PageSize = 100, q: Search = ""):
    """返回带 parent_id 的有序记录，前端按同一数据构建部门树。"""
    # 路由：GET /api/v1/system/departments（分页，默认 page_size=100）。
    # 权限：system.departments.view（由 organization.list_records 按传入的权限码校验）。
    # 数据范围：仅 ctx.workspace_id；部门列表不做人员范围裁剪 —— 部门树是数据范围的计算基础，
    # 裁剪后反而会让拥有 department_tree 范围的角色算错覆盖集合。
    # 副作用：只读，不写审计；排序 created_at + id 保证分页稳定。
    return organization.list_records(ctx, Department, "system.departments.view", page, page_size, q)


@router.post("/departments", response_model=DepartmentOut, status_code=201, operation_id="system_create_department", summary="创建部门")
def create_department(payload: DepartmentCreate, ctx: Context):
    """创建部门。

    路由：POST /api/v1/system/departments（201）。权限：system.departments.manage。
    数据范围：新部门固定归属 ctx.workspace_id；parent_id 必须逐级可达本工作区且未停用，
    leader_user_id 必须是本工作区成员（跨工作区引用按 404 处理）。
    副作用：服务层先 lock_workspace 加行锁，再写部门行并写 department.create 审计后提交；
    code 在工作区内唯一且创建后不可修改（它会被角色数据范围与导入模板引用）。
    """
    return organization.create_department(ctx, payload)


@router.get("/departments/{record_id}", response_model=DepartmentOut, operation_id="system_get_department", summary="查看部门详情")
def get_department(record_id: RecordId, ctx: Context):
    """查看部门详情。

    路由：GET /api/v1/system/departments/{record_id}。权限：system.departments.view。
    数据范围：scoped_record 按 ctx.workspace_id 过滤，跨工作区 id 按 404 处理。
    副作用：只读，不写审计；这里直接返回 ORM 记录（response_model 负责裁剪字段）。
    """
    require_permission(ctx, "system.departments.view")
    return scoped_record(ctx, Department, record_id)


@router.patch("/departments/{record_id}", response_model=DepartmentOut, operation_id="system_update_department", summary="修改部门或调整组织树")
def update_department(record_id: RecordId, payload: DepartmentUpdate, ctx: Context):
    """禁止自身、下级或其他工作区部门成为上级；保留明确的版本冲突。"""
    # 路由：PATCH /api/v1/system/departments/{record_id}。权限：system.departments.manage；
    # payload.version 必填（乐观锁），不一致直接 409，不静默覆盖他人刚做的改动。
    # 数据范围：仅当前工作区部门，否则 404。
    # 副作用：加工作区行锁后更新部门行并写 department.update 审计（记录变更字段名）；调整上级会
    # 改变 department_tree 数据范围覆盖的成员集合，因此该改动等于批量变更可见数据，需谨慎。
    return organization.update_department(ctx, record_id, payload)


@router.delete("/departments/{record_id}", response_model=ActionResult, operation_id="system_delete_department", summary="删除无引用部门")
def delete_department(record_id: RecordId, ctx: Context):
    """删除无引用部门。

    路由：DELETE /api/v1/system/departments/{record_id}（返回 ActionResult，不是 204）。
    权限：system.departments.manage。数据范围：仅当前工作区部门，否则 404。
    约束：仍有下级部门、仍有成员资料、或仍被角色数据范围（Role.department_ids）引用时返回 409；
    不级联删除任何成员或子部门，必须先人工迁移，避免出现“无归属成员”的隐式越权。
    副作用：加工作区行锁后删除部门行并写 department.delete 审计后提交。
    """
    organization.delete_department(ctx, record_id)
    return {"ok": True}


@router.get("/positions", response_model=Page[PositionOut], operation_id="system_list_positions", summary="分页查询岗位")
def list_positions(ctx: Context, page: PageNumber = 1, page_size: PageSize = 100, q: Search = ""):
    """分页查询岗位。

    路由：GET /api/v1/system/positions（默认 page_size=100）。权限：system.positions.view
    （由 organization.list_records 按传入的权限码校验）。
    数据范围：仅 ctx.workspace_id。岗位只是职责标签，不参与授权，因此列表不需要按人员范围裁剪。
    副作用：只读，不写审计；排序 created_at + id 保证分页稳定。
    """
    return organization.list_records(ctx, Position, "system.positions.view", page, page_size, q)


@router.post("/positions", response_model=PositionOut, status_code=201, operation_id="system_create_position", summary="创建岗位")
def create_position(payload: PositionCreate, ctx: Context):
    """创建岗位。

    路由：POST /api/v1/system/positions（201）。权限：system.positions.manage。
    数据范围：新岗位固定归属 ctx.workspace_id，code 在工作区内唯一（重复 409）。
    副作用：写岗位行并写 position.create 审计后提交。岗位与权限无关 —— 给成员分配岗位不会带来
    任何动作权限，因此这里没有像角色那样的委派边界校验。
    """
    return organization.create_position(ctx, payload)


@router.get("/positions/{record_id}", response_model=PositionOut, operation_id="system_get_position", summary="查看岗位详情")
def get_position(record_id: RecordId, ctx: Context):
    """查看岗位详情。

    路由：GET /api/v1/system/positions/{record_id}。权限：system.positions.view。
    数据范围：scoped_record 按 ctx.workspace_id 过滤，跨工作区 id 按 404 处理。
    副作用：只读，不写审计。
    """
    require_permission(ctx, "system.positions.view")
    return scoped_record(ctx, Position, record_id)


@router.patch("/positions/{record_id}", response_model=PositionOut, operation_id="system_update_position", summary="修改岗位")
def update_position(record_id: RecordId, payload: PositionUpdate, ctx: Context):
    """修改岗位。

    路由：PATCH /api/v1/system/positions/{record_id}。权限：system.positions.manage；
    payload.version 必填（乐观锁），版本不一致返回 409。
    数据范围：仅当前工作区岗位，否则 404。
    副作用：更新岗位行并写 position.update 审计后提交；停用岗位不会自动从成员资料里移除引用，
    只是让新分配与批量导入无法再选择它。
    """
    return organization.update_position(ctx, record_id, payload)


@router.delete("/positions/{record_id}", response_model=ActionResult, operation_id="system_delete_position", summary="删除无成员引用的岗位")
def delete_position(record_id: RecordId, ctx: Context):
    """删除无成员引用的岗位。

    路由：DELETE /api/v1/system/positions/{record_id}（返回 ActionResult，不是 204）。
    权限：system.positions.manage。数据范围：仅当前工作区岗位，否则 404。
    约束：仍有成员资料的 position_ids 引用该岗位时返回 409 —— 必须先调整成员岗位；成员资料里的
    岗位标识不会随岗位删除自动清理。
    副作用：加工作区行锁后删除岗位行并写 position.delete 审计后提交。
    """
    organization.delete_position(ctx, record_id)
    return {"ok": True}


@router.get("/permissions", operation_id="system_list_permissions", summary="获取功能权限与数据范围目录")
def permission_catalog(ctx: Context):
    """获取功能权限与数据范围目录。

    路由：GET /api/v1/system/permissions。权限：system.permissions.view。
    数据范围：只返回全局登记的 PERMISSION_CATALOG 与五个数据范围码，与工作区无关，不含成员数据。
    返回：items 中每项带 grantable —— 表示“操作人自己是否拥有该动作”，前端据此禁用不能委派的选项；
    真正的拒止仍由 validate_role_policy 在提交时执行，前端限制只是提示。
    副作用：只读，不写审计。
    """
    return roles.permission_catalog(ctx)


@router.get(
    "/permission-history",
    response_model=Page[PermissionHistoryItem],
    operation_id="system_list_permission_history",
    summary="分页追溯用户、角色与资源授权变更",
)
def list_permission_history(
    ctx: Context,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    target_type: Annotated[Literal["user", "role", "resource_grant"] | None, Query(description="变更对象类型")] = None,
    target_id: Annotated[str, Query(max_length=26, description="用户、角色或资源授权标识")] = "",
    actor_user_id: Annotated[str, Query(max_length=26, description="按操作人筛选")] = "",
    action: Annotated[str, Query(max_length=100, description="稳定动作编码，如 role.update")] = "",
    q: Search = "",
):
    """需要 system.permissions.history；返回变更前后快照和来源，不返回密码、令牌或密钥。"""
    # 路由：GET /api/v1/system/permission-history（分页）。权限：system.permissions.history，
    # 并叠加该动作的人员数据范围。
    # 数据范围：非管理员只能看到 resource_type="user" 且资源在 allowed_owner_ids 范围内的事件 ——
    # “别人对不在我管辖范围内的人做了什么”不可见；角色变更历史仅对全工作区可见者开放。
    # 副作用：只读，不写审计；source 取 manual / bulk_import / system，供前端区分来源。
    # 实现约束：服务层用 cast(metadata_json, String) 做 JSON 文本搜索，是跨库（SQLite/MySQL）可用的
    # 写法，不能改成方言函数，否则两个环境的搜索行为会不一致。
    return permission_history.list_permission_history(
        ctx,
        page=page,
        page_size=page_size,
        target_type=target_type or "",
        target_id=target_id,
        actor_user_id=actor_user_id,
        action=action,
        query=q,
    )


@router.get("/resource-grants", response_model=Page[ResourceGrantOut], operation_id="system_list_resource_grants", summary="查看资源授权")
def list_resource_grants(ctx: Context, page: PageNumber = 1, page_size: PageSize = 20):
    """查看资源授权。

    路由：GET /api/v1/system/resource-grants（分页）。权限：system.permissions.view。
    数据范围：仅 ctx.workspace_id 的 ResourceGrant 行，不跨工作区；不按人员范围裁剪 ——
    授权记录是权限治理的审计面，隐藏它会让管理员无法判断“谁被额外授权了什么”。
    副作用：只读，不写审计；按 created_at 倒序展示最近授权。
    """
    return administration.list_grants(ctx, page, page_size)


@router.post("/resource-grants", response_model=ResourceGrantOut, status_code=201,
             operation_id="system_create_resource_grant", summary="授予指定用户或角色单个资源的访问范围")
def create_resource_grant(payload: ResourceGrantCreate, ctx: Context):
    """被授权者仍需具备该动作的功能权限；资源授权不自动授予修改、导出等动作。"""
    # 路由：POST /api/v1/system/resource-grants（201）。权限：system.permissions.manage。
    # 数据范围与委派边界：资源必须属于当前工作区，且操作人对该资源也必须具备 payload.actions 里
    # 每个动作的功能权限 —— “不能把你自己都没有权限的资源共享给别人”（逐动作 require_resource）。
    # 副作用：写 system_resource_grants 并写 resource.grant 审计（after 快照含 label 便于列表展示）；
    # 授权对象是角色时，该角色下所有用户都会获得这个资源的访问权，且同一（资源, 对象, 动作集合）
    # 重复授权会因唯一约束返回 409。
    return administration.create_grant(ctx, payload)


@router.delete("/resource-grants/{record_id}", response_model=ActionResult, operation_id="system_delete_resource_grant", summary="撤销单个资源授权")
def delete_resource_grant(record_id: RecordId, ctx: Context):
    """撤销单个资源授权。

    路由：DELETE /api/v1/system/resource-grants/{record_id}（返回 ActionResult，不是 204）。
    权限：system.permissions.manage。数据范围：授权记录必须属于当前工作区，否则 404。
    副作用：只删除授权行并写 resource.revoke 审计（保留 before 快照）；被授权的文件、知识库等
    业务对象本身不受影响，也不会级联删除用户或角色 —— 撤销只收回“额外共享”，不影响角色自带的权限。
    """
    administration.delete_grant(ctx, record_id)
    return {"ok": True}


@router.get("/menus", operation_id="system_list_menus", summary="查看已登记菜单及显示设置")
def list_menus(ctx: Context):
    """查看已登记菜单及显示设置。

    路由：GET /api/v1/system/menus（返回 {"items": [...]}，刻意不加 response_model，避免把可变
    导航结构固化进 OpenAPI 契约）。权限：system.menus.view。
    数据范围：菜单来自全局 platform.catalog 登记表，显示偏好取当前工作区的 MenuSetting；
    未落库的菜单回退为登记表的默认名称与显隐，不会因为设置而新增路由。
    副作用：只读，不写审计。
    """
    return {"items": administration.menu_settings(ctx)}


@router.put("/menus/{menu_id}", operation_id="system_update_menu", summary="修改菜单显示名称、顺序和显隐")
def update_menu(menu_id: Annotated[str, Path(max_length=80, description="已登记菜单编码")], payload: MenuUpdate, ctx: Context):
    """不能通过此接口创建任意路由；菜单隐藏不影响后端授权规则。"""
    # 路由：PUT /api/v1/system/menus/{menu_id}。权限：system.menus.manage。
    # 数据范围：只改当前工作区的 MenuSetting；menu_id 必须是已登记模块，未登记返回 404，
    # 因此这里永远无法凭空造出一条路由。刻意隐藏首页及权限恢复所需的基础入口会返回 422。
    # 副作用：按需新建/更新 menu_settings 行并写 menu.update 审计后提交；隐藏只影响导航显示，
    # 不收回任何功能权限，已授权用户仍可直接调用对应接口。
    return administration.update_menu(ctx, menu_id, payload)


@router.get("/sessions", operation_id="system_list_sessions", summary="分页查看可管理用户的有效会话")
def list_sessions(ctx: Context, page: PageNumber = 1, page_size: PageSize = 20):
    """分页查看可管理用户的有效会话。

    路由：GET /api/v1/system/sessions。权限：system.sessions.view。
    数据范围：先用 allowed_owner_ids("system.sessions.view") 收敛人员范围，再对每个候选用户调用
    require_global_account_control 过滤掉“还属于其他工作区”的共享账号 —— 这类账号的会话只有其全部
    工作区管理员才能看到。返回只含未过期、未撤销的会话。
    副作用：只读，不写审计；device_hash 只返回前 12 位，绝不返回 token_digest。
    """
    return administration.list_sessions(ctx, page, page_size)


@router.delete("/sessions/{record_id}", response_model=ActionResult, operation_id="system_revoke_session", summary="撤销指定会话")
def revoke_session(record_id: RecordId, ctx: Context):
    """撤销指定会话。

    路由：DELETE /api/v1/system/sessions/{record_id}（返回 ActionResult，不是 204）。
    权限：system.sessions.manage，并叠加该动作的人员数据范围；目标账号还需通过
    require_global_account_control（共享身份在其他工作区的会话本工作区无权单独撤销）。
    数据范围：会话不落 workspace_id，因此这里是通过“会话所属用户必须是本工作区成员且在可见范围内”
    间接限定作用域。
    副作用：把 SessionToken.revoked_at 置为当前时间（不删行，保留审计痕迹）并写 session.revoke 审计
    后提交；用户需重新登录，已下发的访问令牌在下次校验时失效。
    """
    administration.revoke_session(ctx, record_id)
    return {"ok": True, "message": "会话已撤销"}


@router.get("/settings", operation_id="system_list_settings", summary="查看非敏感系统参数")
def list_settings(ctx: Context):
    """查看非敏感系统参数。

    路由：GET /api/v1/system/settings。权限：system.settings.view（路由层显式校验）。
    数据范围：仅 current workspace 的 SystemSetting 行，未落库的键回退为 DEFAULT_SETTINGS 默认值。
    返回：{"items": [...]}；刻意只遍历 DEFAULT_SETTINGS，历史遗留的未知键不会外泄给前端。
    副作用：只读且不会创建缺失行（读取不写库、不写审计）；凭证类配置由各自的密钥模块管理，不在这里。
    """
    require_permission(ctx, "system.settings.view")
    return {"items": administration.workspace_settings(ctx)}


@router.put("/settings/{key}", operation_id="system_update_setting", summary="修改已登记系统参数")
def update_setting(key: Annotated[str, Path(max_length=120, description="参数名称，如 ui.page_size")], payload: SettingUpdate, ctx: Context):
    """修改已登记系统参数。

    路由：PUT /api/v1/system/settings/{key}。权限：system.settings.manage。
    数据范围：只写当前工作区的 SystemSetting 行；key 必须已登记在 DEFAULT_SETTINGS 中，否则 422
    （模型密钥等敏感配置请走对应管理模块，不允许从这里写入）。
    副作用：按需新建/更新 system_settings 行并写 setting.update 审计（只记 key，不记值）后提交；
    产品名称固定、page_size 必须是 10–100 的整数、时区必须是有效 IANA 名称。
    """
    return administration.update_setting(ctx, key, payload)


@router.get("/dictionary", operation_id="system_list_dictionary", summary="分页查询业务字典")
def list_dictionary(ctx: Context, page: PageNumber = 1, page_size: PageSize = 20,
                    namespace: Annotated[str, Query(max_length=80, description="字典类型编码，留空查询全部")] = ""):
    """分页查询业务字典。

    路由：GET /api/v1/system/dictionary。权限：system.settings.view（与系统参数共用查看权限）。
    数据范围：仅 ctx.workspace_id，namespace 留空表示不筛选；排序 namespace + sort_order + id。
    返回：分页结构且含 version，供前端提交修改时做乐观锁。
    副作用：只读，不写审计。
    """
    return administration.list_dictionary(ctx, page, page_size, namespace)


@router.post("/dictionary", status_code=201, operation_id="system_create_dictionary", summary="新增业务字典项")
def create_dictionary(payload: DictionaryCreate, ctx: Context):
    """新增业务字典项。

    路由：POST /api/v1/system/dictionary（201）。权限：system.settings.manage。
    数据范围：写入当前工作区，namespace + code 在工作区内唯一（重复 409）。
    约束：字典只承载展示数据，绝不参与权限判定（权限只认 PERMISSION_CATALOG 编码），因此这里
    不存在“改字典就能提权”的路径。
    副作用：写 dictionary_entries 行并写 dictionary.save 审计后提交。
    """
    return administration.save_dictionary(ctx, payload)


@router.patch("/dictionary/{record_id}", operation_id="system_update_dictionary", summary="修改业务字典项")
def update_dictionary(record_id: RecordId, payload: DictionaryUpdate, ctx: Context):
    """修改业务字典项。

    路由：PATCH /api/v1/system/dictionary/{record_id}。权限：system.settings.manage。
    数据范围：字典项必须属于当前工作区，否则 404；namespace + code 仍要满足工作区内唯一。
    副作用：更新字典行并写 dictionary.save 审计后提交；既有 version 参与乐观锁，避免覆盖他人改动。
    """
    return administration.save_dictionary(ctx, payload, record_id)


@router.delete("/dictionary/{record_id}", response_model=ActionResult, operation_id="system_delete_dictionary", summary="删除业务字典项")
def delete_dictionary(record_id: RecordId, ctx: Context):
    """删除业务字典项。

    路由：DELETE /api/v1/system/dictionary/{record_id}（返回 ActionResult，不是 204）。
    权限：system.settings.manage。数据范围：仅当前工作区字典项，否则 404。
    副作用：删除字典行并写 dictionary.delete 审计后提交；删除不会校验业务引用 —— 已按旧值保存的
    业务数据不会被改写，前端展示时缺失项按原值回退。
    """
    administration.delete_dictionary(ctx, record_id)
    return {"ok": True}
