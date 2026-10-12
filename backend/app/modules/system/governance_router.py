"""Organization lifecycle, login security and enterprise SSO administration APIs.

职责：组织/工作区生命周期、跨工作区成员转移、工作区登录安全策略、可信设备审批、登录事件查询，
以及企业 SSO（OIDC）提供方管理。本模块只做 HTTP 编排 —— 解析路径与查询参数、注入 AuthContext、
把请求转交服务层；版本校验、数据范围过滤、审计写库都在服务层，这里不写业务规则，也不自行
commit/rollback（服务的写操作内部已提交）。

位置与依赖：路由前缀 /api/v1/system（tags=["组织与安全治理"]），按域分组为 /organizations、
/workspaces（含 /workspaces/member-transfer）、/security/{policy,devices,login-events}、
/sso-providers（含 /discover）。依赖 common.deps.get_current_context 提供 AuthContext（含绑定的
工作区与用户）；业务全部委托给同域三个服务：lifecycle_service（组织/工作区生命周期与成员转移）、
security_service（登录策略、可信设备、登录事件）、oidc_service（OIDC 提供方与发现文档探测）。

权限码规律：生命周期与成员转移用 system.workspaces.view|manage；安全策略、可信设备、登录事件、
OIDC 提供方用 system.security.view|manage。view 只读、manage 才写，实际校验发生在服务层
（require_permission），因此各路由 docstring 里标注的权限码即服务层真实校验值，新增路由须保持一致。

数据范围：不由本层参数裁剪，而是由服务层按 ctx.workspace_id（安全与 SSO 域）或
ctx.workspace.organization_id（生命周期域）限定 —— 生命周期写操作还要求操作人是当前组织的
admin；跨组织/跨工作区访问一律按“记录不存在”处理（404），避免被用来探测他人 id。

约定：标识统一为 26 位以内字符串（RecordId），分页从 1 起、每页最多 100；服务层异常
（NotFoundError / ConflictError / ValidationError / PermissionErrorEx 等）由全局异常处理器映射为
下面 responses 声明的状态码，路由函数不捕获；本模块不发送通知，审计是唯一的对外副作用记录。
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query

from app.common.deps import AuthContext, get_current_context
from app.modules.system import lifecycle_service, oidc_service, security_service
from app.modules.system.lifecycle_schemas import (
    MemberTransfer, OrganizationCreate, OrganizationLifecycleOut, OrganizationUpdate,
    TransferResult, WorkspaceCreate, WorkspaceLifecycleOut, WorkspaceUpdate,
)
from app.modules.system.schemas import ActionResult, Page
from app.modules.system.security_schemas import (
    DeviceDecision, OidcProviderCreate, OidcProviderUpdate,
    SecurityPolicyOut, SecurityPolicyUpdate,
)

# 统一声明本组路由的公共失败语义，供 OpenAPI 文档与前端错误提示复用；真正的抛出点在服务层
router = APIRouter(prefix="/api/v1/system", tags=["组织与安全治理"], responses={
    401: {"description": "未登录、会话失效或 SSO 回调校验失败"},
    403: {"description": "没有组织、工作区或安全策略管理权限"},
    404: {"description": "组织、工作区、设备或 SSO 配置不存在"},
    409: {"description": "版本冲突、最后管理员保护或生命周期状态冲突"},
    422: {"description": "编码、CIDR、OIDC 地址或角色配置不符合约束"},
})
# 所有端点的统一依赖：解析会话得到 AuthContext（含当前工作区与用户），未登录时由该依赖抛 401
Context = Annotated[AuthContext, Depends(get_current_context)]
# 记录标识：本库主键是 26 位字符串，空值与超长值在进入服务层前就被 422 拦下
RecordId = Annotated[str, Path(min_length=1, max_length=26)]
# 分页起点固定为 1；page_size 上限 100 与服务层 limit 语义一致，避免一次拉取整表安全证据
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/organizations", response_model=list[OrganizationLifecycleOut],
            operation_id="system_list_organizations", summary="查看本人可管理的组织生命周期")
def list_organizations(ctx: Context):
    """列出操作人可管理的组织（GET /api/v1/system/organizations）。

    权限：system.workspaces.view。数据范围：只返回操作人是其成员的组织，跨组织信息不泄露。
    副作用：转交 lifecycle_service.list_organizations，会先做元数据补齐（可能写库并提交），
    本身只读；不写审计。
    """
    return lifecycle_service.list_organizations(ctx)


@router.post("/organizations", response_model=OrganizationLifecycleOut, status_code=201,
             operation_id="system_create_organization", summary="创建组织及首个工作区")
def create_organization(payload: OrganizationCreate, ctx: Context):
    """创建者成为新组织和首个工作区管理员；角色、提示词和权限基线同步初始化。

    路由：POST /api/v1/system/organizations（201），权限 system.workspaces.manage。
    数据范围：不要求操作人已是任何组织的管理员 —— 管理员权限不继承，操作人只是新建组织/工作区的
    admin，因此这里没有组织级前置校验。
    副作用：单个事务内写权威表与元数据表、初始化内置角色，并写 organization.create 审计；不发送通知。
    """
    return lifecycle_service.create_organization(ctx, payload)


@router.patch("/organizations/{record_id}", response_model=OrganizationLifecycleOut,
              operation_id="system_update_organization", summary="修改或归档组织")
def update_organization(record_id: RecordId, payload: OrganizationUpdate, ctx: Context):
    """归档组织前必须先归档其全部工作区；仅组织管理员可以执行。

    路由：PATCH /api/v1/system/organizations/{record_id}，权限 system.workspaces.manage。
    数据范围：目标组织必须在操作人的组织成员表中且 role="admin"，否则 403/404；
    payload.version 为必填乐观锁，避免覆盖其他人的并发修改。
    副作用：名称写回 organizations 权威表、其余字段写元数据表，写 organization.update 审计后提交；
    归档会让该组织下的工作区无法再登录（生命周期状态参与登录校验）。
    """
    return lifecycle_service.update_organization(ctx, record_id, payload)


@router.get("/workspaces", response_model=list[WorkspaceLifecycleOut],
            operation_id="system_list_workspaces", summary="查看当前组织的工作区")
def list_workspaces(ctx: Context):
    """列出当前组织下的工作区（GET /api/v1/system/workspaces）。

    权限：system.workspaces.view；此外服务层还要求操作人是当前组织的管理员 —— 工作区列表属于
    组织级信息，仅有查看权限不足以看到兄弟工作区。
    数据范围：严格限制在 ctx.workspace.organization_id 内。副作用：先补齐生命周期元数据
    （可能写库并提交），查询本身只读、不写审计。
    """
    return lifecycle_service.list_workspaces(ctx)


@router.post("/workspaces", response_model=WorkspaceLifecycleOut, status_code=201,
             operation_id="system_create_workspace", summary="在当前组织创建工作区")
def create_workspace(payload: WorkspaceCreate, ctx: Context):
    """创建者成为管理员；新工作区使用独立角色、数据、密钥和权限边界。

    路由：POST /api/v1/system/workspaces（201），权限 system.workspaces.manage，且需为当前组织
    管理员。数据范围：新工作区归属 ctx.workspace.organization_id，编码在组织内唯一。
    副作用：单事务写工作区与元数据、初始化内置角色与提示词/参数，并在新旧工作区分别写
    workspace.create 与 workspace.initialize 审计后提交；不发送通知。
    """
    return lifecycle_service.create_workspace(ctx, payload)


@router.patch("/workspaces/{record_id}", response_model=WorkspaceLifecycleOut,
              operation_id="system_update_workspace", summary="修改、归档或恢复工作区")
def update_workspace(record_id: RecordId, payload: WorkspaceUpdate, ctx: Context):
    """不能从当前工作区归档自身，也不能归档组织内最后一个启用工作区。

    路由：PATCH /api/v1/system/workspaces/{record_id}，权限 system.workspaces.manage，
    且需为当前组织管理员；payload.version 为必填乐观锁。
    数据范围：目标工作区必须属于当前组织，否则按 404 处理（不区分“不存在”与“跨组织”）。
    副作用：名称写回 workspaces、其余字段写生命周期元数据，写 workspace.update 审计后提交；
    归档或恢复都会立即影响该工作区的登录可用性。
    """
    return lifecycle_service.update_workspace(ctx, record_id, payload)


@router.post("/workspaces/member-transfer", response_model=TransferResult,
             operation_id="system_transfer_workspace_member", summary="复制或调动成员到同组织工作区")
def transfer_workspace_member(payload: MemberTransfer, ctx: Context):
    """目标角色在目标工作区解析；移动时清理来源角色、资源授权和成员资料，并保护最后管理员。

    路由：POST /api/v1/system/workspaces/member-transfer，权限 system.workspaces.manage，
    且需为当前组织管理员。注意本路径不接收 record_id —— 源工作区恒为 ctx.workspace_id，
    目标来自 payload.target_workspace_id，且必须与源工作区同组织（否则 404）。
    副作用（copy 与 move 都算写操作）：写目标工作区成员与角色数据（必要时先初始化内置角色），
    move 还会删除源工作区的 UserRole、该用户的 ResourceGrant、成员资料与成员关系；并在源/目标
    两个工作区各写一条审计后提交（workspace.member_copy|move 与 workspace.member_received）。
    """
    return lifecycle_service.transfer_member(ctx, payload)


@router.get("/security/policy", response_model=SecurityPolicyOut,
            operation_id="system_get_security_policy", summary="查看工作区登录安全策略")
def get_security_policy(ctx: Context):
    """查看当前工作区的登录安全策略（GET /api/v1/system/security/policy）。

    权限：system.security.view。数据范围：仅 ctx.workspace_id，不含其它工作区。
    副作用：转交 security_service.read_policy —— 策略行不存在时会创建默认行并提交
    （读取即初始化），因此这个 GET 也可能写库；不写审计。
    """
    return security_service.read_policy(ctx)


@router.patch("/security/policy", response_model=SecurityPolicyOut,
              operation_id="system_update_security_policy", summary="修改 IP、设备、锁定与会话策略")
def update_security_policy(payload: SecurityPolicyUpdate, ctx: Context):
    """CIDR 会规范化并检查冲突；策略只作用于当前工作区。

    路由：PATCH /api/v1/system/security/policy，权限 system.security.manage；
    payload.version 必填（乐观锁），未传字段保持原值。
    数据范围：只改 ctx.workspace_id 的策略行，不跨工作区。
    副作用：更新策略行并写 security.policy_update 审计（只记变更字段名，不记具体值）后提交；
    策略只在之后的登录生效，不会中断已签发会话，也不发送通知。
    """
    return security_service.update_policy(ctx, payload)


@router.get("/security/devices", operation_id="system_list_trusted_devices", summary="分页查看可信设备和待审批设备")
def list_trusted_devices(
    ctx: Context, page: PageNumber = 1, page_size: PageSize = 20,
    status: Annotated[Literal["pending", "trusted", "revoked"] | None, Query()] = None,
):
    """分页查看可信设备与待审批设备（GET /api/v1/system/security/devices）。

    权限：system.security.view。数据范围：当前工作区全量设备，不按人员数据范围裁剪 ——
    管理员需要看到所有人的待审批设备；status 为空串时表示不筛选。
    返回：{items, total, page, page_size}，其中 device_hash 只返回前 12 位，防止完整哈希被拿去做
    跨库匹配。副作用：只读，不写审计。
    """
    return security_service.list_devices(ctx, page, page_size, status or "")


@router.patch("/security/devices/{record_id}", operation_id="system_decide_trusted_device", summary="批准或撤销设备")
def decide_trusted_device(record_id: RecordId, payload: DeviceDecision, ctx: Context):
    """服务端仅保存工作区加盐哈希，响应不会返回浏览器原始设备标识。

    路由：PATCH /api/v1/system/security/devices/{record_id}，权限 system.security.manage；
    payload.version 必填，避免覆盖其他管理员刚做出的审批。
    数据范围：设备必须属于 ctx.workspace_id，否则 404。
    副作用：改写设备状态与审批信息（trusted 记录审批人/时间，revoked 记录撤销时间），写
    security.device_decision 审计后提交。撤销在 require_trusted_device 开启时会立即阻止该设备
    后续登录，但不影响该设备上已签发的会话。
    """
    return security_service.decide_device(ctx, record_id, payload)


@router.get("/security/login-events", operation_id="system_list_login_events", summary="分页查看登录成功、失败和阻止记录")
def list_login_events(
    ctx: Context, page: PageNumber = 1, page_size: PageSize = 20,
    outcome: Annotated[Literal["success", "failed", "blocked"] | None, Query()] = None,
):
    """分页查看登录事件（GET /api/v1/system/security/login-events）。

    权限：system.security.view。数据范围：当前工作区全量事件，不按人员数据范围裁剪（登录失败往往
    没有可信的 user_id，需要整表排查）；outcome 为空串表示不筛选。
    返回：按创建时间倒序的分页结构，device_hash 截断为 12 位，reason 是稳定机器码由前端映射中文。
    副作用：只读，不写审计。
    """
    return security_service.list_login_events(ctx, page, page_size, outcome or "")


@router.get("/sso-providers", operation_id="system_list_oidc_providers", summary="查看工作区 OIDC 提供方")
def list_oidc_providers(ctx: Context):
    """查看当前工作区的 OIDC 提供方（GET /api/v1/system/sso-providers）。

    权限：system.security.view。数据范围：仅 ctx.workspace_id，不含其它工作区配置。
    返回：{"items": [...]} 形式的列表（刻意不加 response_model，避免把可变的安全配置字段固化进
    OpenAPI 契约）。副作用：只读；输出只含 client_secret_configured 布尔，绝不返回密钥内容。
    """
    return {"items": oidc_service.list_admin(ctx)}


@router.post("/sso-providers", status_code=201, operation_id="system_create_oidc_provider", summary="创建 OIDC 提供方")
def create_oidc_provider(payload: OidcProviderCreate, ctx: Context):
    """client secret 加密后仅保存引用；普通响应和审计不返回密钥。

    路由：POST /api/v1/system/sso-providers（201），权限 system.security.manage。
    数据范围：新配置固定归属 ctx.workspace_id，code 经小写规范化后在当前工作区内唯一（重复 409）。
    副作用：写提供方表与密钥模块，写 oidc_provider.create 审计（只记 code/enabled）后提交；
    新建默认 enabled=False，需另行修改启用后才会出现在登录页。
    """
    return oidc_service.create_provider(ctx, payload)


@router.patch("/sso-providers/{record_id}", operation_id="system_update_oidc_provider", summary="修改 OIDC 提供方")
def update_oidc_provider(record_id: RecordId, payload: OidcProviderUpdate, ctx: Context):
    """修改 OIDC 提供方（PATCH /api/v1/system/sso-providers/{record_id}）。

    权限：system.security.manage；payload.version 必填（乐观锁）。
    数据范围：配置必须属于 ctx.workspace_id，否则 404（不区分“不存在”与“属于别的工作区”）。
    副作用：更新配置行，传入非空 client_secret 时轮换密钥（旧密钥立即 revoked，需重新登录一次），
    写 oidc_provider.update 审计（只记字段名，不记密钥值）后提交；启用/停用会立即影响登录页的
    SSO 入口，但不影响已建立会话。
    """
    return oidc_service.update_provider(ctx, record_id, payload)


@router.post("/sso-providers/{record_id}/discover", operation_id="system_discover_oidc_provider", summary="读取 OIDC 发现文档并校验签名密钥")
def discover_oidc_provider(record_id: RecordId, ctx: Context):
    """不跟随重定向；Issuer、授权端点、令牌端点和 JWKS 必须通过网络目标校验。

    路由：POST /api/v1/system/sso-providers/{record_id}/discover，权限 system.security.manage。
    数据范围：只能探测当前工作区的配置（否则 404）。
    副作用：对外发起两次 HTTP GET（发现文档与 JWKS，均 8 秒超时、follow_redirects=False）；
    成功时用发现文档覆盖三个端点地址并写入探测状态，写 oidc_provider.discover 审计后提交。
    网络或格式错误不抛异常，而是落库为 last_probe_status="failed"，让管理员在页面上看到原因。
    """
    return oidc_service.discover_provider(ctx, record_id)


@router.delete("/sso-providers/{record_id}", response_model=ActionResult,
               operation_id="system_delete_oidc_provider", summary="删除已停用的 OIDC 提供方")
def delete_oidc_provider(record_id: RecordId, ctx: Context):
    """删除已停用的 OIDC 提供方（DELETE /api/v1/system/sso-providers/{record_id}）。

    权限：system.security.manage。数据范围：仅当前工作区的配置，否则 404。
    约束：仍处于 enabled 状态时返回 409，必须先停用 —— 避免把正在使用的登录入口直接删掉。
    副作用：删除配置行、把关联 client secret 标记为 revoked（保留密钥记录以备审计），写
    oidc_provider.delete 审计后提交；已通过该提供方登录的会话不受影响。返回固定的中文成功消息。
    """
    oidc_service.delete_provider(ctx, record_id)
    return {"ok": True, "message": "SSO 配置和客户端密钥引用已删除"}
