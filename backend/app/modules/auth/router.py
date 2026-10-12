# -*- coding: utf-8 -*-
"""认证路由层：本地登录、企业 SSO（OIDC）、客户端令牌、会话与个人资料。

对外路由统一挂在 ``/api/auth`` 前缀下，是前端拿到会话的唯一入口：
- ``POST /api/auth/login`` 本地账号口令登录；
- ``GET  /api/auth/sso/providers``、``POST /api/auth/sso/{provider_id}/authorize``、
  ``GET  /api/auth/sso/callback`` 企业 SSO 流程；
- ``POST /api/auth/client-token`` 认证客户端换取短期服务令牌；
- ``POST /api/auth/logout``、``GET /api/auth/me`` 会话生命周期；
- ``GET/PATCH /api/auth/profile``、``POST /api/auth/password`` 个人资料与改密。

依赖：``common.deps`` 提供登录上下文与数据库会话，``common.content_session`` 负责把令牌
同时写入 Cookie/内容会话，业务实现分别下沉到 ``auth.service``、``modules.system`` 下的
oidc_service / user_service / integrations。

约束：
- ``/api/auth/`` 前缀在 ``common.authorization.enforce_request_permission`` 中被整体跳过，
  这组路由没有域名级权限码校验，权限边界只能靠登录依赖与「仅限本人」语义保证；
  新增接口时不要因为挂上了登录依赖就认为已经做了授权。
- 除登录与 SSO 回调外，所有路由都要求已登录（``get_current_context``）。
- 登录/回调成功后必须调用 ``set_content_session``，登出必须调用 ``clear_content_session``，
  二者与 ``service`` 层的令牌签发/吊销成对出现，缺一会导致前端状态与服务端不一致。
- 响应体不得包含口令哈希、令牌摘要或客户端密钥等敏感字段。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ...common.deps import AuthContext, get_current_context, get_db
from ...common.audit import audit
from ...common.content_session import set_content_session, clear_content_session
from .schemas import LoginRequest, LoginResponse, PasswordChangeRequest, SsoAuthorizeRequest
from .service import login as do_login, logout as do_logout
from app.modules.system_tools.schemas import ClientTokenInput
from app.modules.system.schemas import SelfProfileOut, SelfProfileUpdate
from app.modules.system import user_service as users

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse, operation_id="auth_login", summary="使用本地账号和设备上下文登录")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """本地账号登录。

    HTTP：``POST /api/auth/login``。权限：匿名可访问（凭据校验在 service 层）。

    参数：
        payload: 用户名、口令及可选 workspace_id / device_id。
        request: 用于风控记录来源信息。
        response: 登录成功后写入内容会话 Cookie。

    返回：``LoginResponse``，含明文令牌与可访问工作区列表。

    业务异常：凭据错误、账号禁用/锁定、无可用工作区时由 ``do_login`` 抛 ``AuthError``
    （同时已记录失败登录）。副作用：签发会话、写登录审计、设置 Cookie。
    """
    result = do_login(
        db, payload.username, payload.password, request=request,
        workspace_id=payload.workspace_id, device_id=payload.device_id,
    )
    set_content_session(response, request, result["token"])
    return result


@router.get("/sso/providers", operation_id="auth_list_sso_providers", summary="查看已启用的企业 SSO 登录方式")
def list_sso_providers(db: Session = Depends(get_db)):
    """公开响应只包含显示名称和工作区，不返回 Issuer、客户端标识或密钥。

    HTTP：``GET /api/auth/sso/providers``。权限：匿名可访问——登录页在建立会话之前
    就要先渲染可用的 SSO 入口，因此本接口不做登录校验。

    返回：``{"items": [...]}``，每项只含展示所需字段（默认过滤掉未启用的供应商）。

    约束：这是匿名接口，新增字段前必须确认不含 Issuer、client_id、密钥或回调地址。
    """
    from app.modules.system.oidc_service import public_providers
    return {"items": public_providers(db)}


@router.post("/sso/{provider_id}/authorize", operation_id="auth_begin_oidc", summary="创建单次 OIDC 授权请求")
def begin_oidc(provider_id: str, payload: SsoAuthorizeRequest, request: Request,
               db: Session = Depends(get_db)):
    """生成固定回调地址、state、nonce 与 S256 PKCE；设备标识不会写入授权 URL。

    HTTP：``POST /api/auth/sso/{provider_id}/authorize``。权限：匿名可访问
    （此刻尚未建立本平台会话），安全性依赖后续回调阶段的 state/PKCE/签名校验。

    参数：
        provider_id: 已启用的 OIDC 供应商标识。
        payload.device_id: 前端设备标识，随服务端状态保存而不进入 URL，避免被
            浏览器历史或代理日志记录。

    返回：前端跳转所需的授权 URL 与一次性 state 等字段。

    业务异常：供应商不存在、未启用或配置不完整时由 ``begin_authorization`` 抛 ``AuthError``。

    副作用：在服务端落一条一次性授权状态；state 只能消费一次，重放会在回调阶段被拒。
    """
    from app.modules.system.oidc_service import begin_authorization
    return begin_authorization(db, provider_id, request, payload.device_id)


@router.get("/sso/callback", response_model=LoginResponse, operation_id="auth_finish_oidc", summary="校验 OIDC 回调并签发本平台会话")
def finish_oidc(code: str, state: str, request: Request, response: Response, db: Session = Depends(get_db)):
    """只接受单次 state；验证 PKCE、签名、issuer、audience、时间、nonce 和哈希声明。

    HTTP：``GET /api/auth/sso/callback``——由身份提供商重定向至此，前端不直接调用。
    权限：匿名可访问，安全性完全来自 state/PKCE/签名校验。

    返回：``LoginResponse``，与本地登录同构，含明文令牌与可访问工作区列表。

    业务异常：state 缺失/已使用/已过期、code 无效、签名或声明校验失败时抛 ``AuthError``；
    失败后 state 不可复用，前端应引导用户重新发起授权。

    副作用：消费 state、签发会话、写登录审计并设置内容会话 Cookie。
    """
    from app.modules.system.oidc_service import finish_authorization
    result = finish_authorization(db, state_value=state, code=code, request=request)
    set_content_session(response, request, result["token"])
    return result


@router.post("/client-token", operation_id="auth_client_token", summary="使用认证客户端换取短期服务令牌")
def client_token(payload: ClientTokenInput, db: Session = Depends(get_db)):
    """客户端密钥只用于本次校验；令牌固定到一个工作区并限制在登记的权限范围内。

    HTTP：``POST /api/auth/client-token``。权限：匿名可访问——以客户端密钥本身作为凭据。

    参数：
        payload: 客户端标识、密钥与目标工作区，契约见 ``system_tools.schemas.ClientTokenInput``。

    返回：短期服务令牌及其作用域；服务令牌不能用于切换工作区（``common.deps`` 会拒绝）。

    业务异常：客户端不存在、密钥错误、已停用或工作区不匹配时由 ``exchange_client_token`` 抛错。

    副作用：本接口不下发内容会话 Cookie——服务令牌只走请求头，避免覆盖浏览器会话。
    """
    from app.modules.system_tools.integrations import exchange_client_token
    return exchange_client_token(db, payload)


@router.post("/logout")
def logout(response: Response, ctx: AuthContext = Depends(get_current_context)):
    """退出当前用户会话或客户端服务令牌。

    HTTP：``POST /api/auth/logout``。权限：持有效用户会话或服务令牌的调用方。

    返回：``{"ok": True}``。

    副作用：仅吊销当前传入的令牌并清除内容会话 Cookie，其他令牌及客户端不变。
    已失效令牌由认证依赖返回 401；成功响应表示本令牌的撤销已经提交。
    """
    do_logout(ctx.db, ctx.token)
    clear_content_session(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request, response: Response, ctx: AuthContext = Depends(get_current_context)):
    """获取当前登录态：用户、当前工作区、角色与权限集合。

    HTTP：``GET /api/auth/me``。权限：任意已登录用户。

    返回：
        ``user``（含 must_change_password）、``workspace``、``role``、
        ``permissions``（已排序，前端据此控制入口显隐）、``roles``。

    约束：``permissions`` 来自 ``access_policy(ctx)``，是前端可见性的唯一依据，不要另行
    在前端硬编码权限判断；权限不足时此处返回的是空集而不是报错。

    副作用：无持久化写入；客户端令牌（``ctx.client_id`` 存在）不刷新 Cookie，
    避免服务令牌覆盖浏览器会话。
    """
    if not ctx.client_id:
        set_content_session(response, request, ctx.token)
    identity = None
    from ...models import AuthIdentity
    from sqlalchemy import select
    # 此处不按 provider 过滤或排序（与 service._build_login_payload 优先取 local 的口径不同），
    # 同时持有多身份的账号可能取到非 local 身份的用户名；改动需与前端展示约定一起评估。
    identity = ctx.db.execute(select(AuthIdentity).where(AuthIdentity.user_id == ctx.user.id)).scalars().first()
    from app.models import LocalCredential
    from app.common.authorization import access_policy
    credential = ctx.db.scalar(select(LocalCredential).where(LocalCredential.auth_identity_id == identity.id)) if identity else None
    policy = access_policy(ctx)
    return {
        "user": {
            "id": ctx.user.id,
            "display_name": ctx.user.display_name,
            "username": identity.username_normalized if identity else "",
            "status": ctx.user.status,
            "must_change_password": bool(credential and credential.must_change_password),
        },
        "workspace": {
            "id": ctx.workspace.id,
            "name": ctx.workspace.name,
            "organization_id": ctx.workspace.organization_id,
        },
        "role": ctx.role,
        "permissions": sorted(policy.permissions),
        "roles": [{"id": role.id, "name": role.name, "code": role.code} for role in policy.roles],
    }


@router.get(
    "/profile",
    response_model=SelfProfileOut,
    operation_id="auth_get_self_profile",
    summary="查看本人在当前工作区的完整资料",
    responses={401: {"description": "未登录或会话失效"}, 409: {"description": "成员资料尚未完成迁移"}},
)
def get_self_profile(ctx: AuthContext = Depends(get_current_context)):
    """任何已登录成员可查看本人资料；不返回密码哈希、令牌、密钥或其他成员信息。

    HTTP：``GET /api/auth/profile``。权限：任意已登录用户，只能读 ``ctx.user_id`` 自己的资料，
    不需要 ``system.users.view``；处于「必须先改密」状态的会话会被 ``deps`` 拦下（403）。

    返回：``SelfProfileOut``，含本人显示姓名、邮箱、电话及所属组织/工作区/岗位名称。

    业务异常：成员资料尚未完成迁移时为 409（见路由 ``responses`` 声明）。

    副作用：无持久化写入。
    """
    return users.self_profile(ctx)


@router.patch(
    "/profile",
    response_model=SelfProfileOut,
    operation_id="auth_update_self_profile",
    summary="修改本人的显示姓名与联系方式",
    responses={401: {"description": "未登录或会话失效"}, 409: {"description": "资料版本冲突"}, 422: {"description": "字段格式不正确"}},
)
def update_self_profile(payload: SelfProfileUpdate, ctx: AuthContext = Depends(get_current_context)):
    """仅允许修改显示姓名、邮箱和电话；部门、岗位、角色及状态必须由有权限的管理员维护。

    HTTP：``PATCH /api/auth/profile``。权限：任意已登录用户，且只能改自己的资料
    （目标用户固定取自 ``ctx.user_id``，请求体无法指定他人，因此无需 ``system.users.manage``）。

    参数：
        payload: 待改字段，只处理显式传入的键；``version`` 为乐观锁版本号，必传。

    返回：更新后的 ``SelfProfileOut``（重新查询，非回显请求体）。

    业务异常：``ConflictError``（409，``version`` 与库中不一致，表示他处已改）；``ValidationError``
    （422，显示姓名为空白或邮箱/电话为 null）。

    副作用：写 User.display_name 与资料表的 email/phone，并记录 ``user.profile_update``
    变更审计（含 before/after 对比），由 service 层统一提交。
    """
    return users.update_self_profile(ctx, payload)


@router.post("/password", operation_id="auth_change_password", summary="修改自己的密码并撤销旧会话")
def change_password(payload: PasswordChangeRequest, ctx: AuthContext = Depends(get_current_context)):
    """修改本人密码。

    HTTP：``POST /api/auth/password``。权限：任意已登录用户，只能改自己的口令。

    返回：``{"ok": True, "message": ...}``，提示前端重新登录。

    业务异常：``AuthError``（当前密码不正确）、``ValidationError``（新密码强度不足或与旧密码相同）。

    副作用：更新口令、吊销该用户全部会话并写 ``auth.password_change`` 审计；
    调用返回后当前会话即失效，前端必须跳转登录页。
    """
    from .service import change_password as do_change_password
    do_change_password(ctx.db, ctx.user_id, payload.current_password, payload.new_password)
    return {"ok": True, "message": "密码已修改，请重新登录"}
