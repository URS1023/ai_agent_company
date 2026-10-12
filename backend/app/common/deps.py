# -*- coding: utf-8 -*-
"""FastAPI dependencies: DB session + authenticated user/workspace context.

职责：为所有需要登录的接口提供两块基础件——数据库会话（get_db）与"已认证请求上下文"
（get_current_context / get_content_auth），并在解析上下文的过程中串起整条判定链：

    认证（Bearer 头 / 内容 Cookie） → 令牌与工作区解析（resolve_principal、_resolve_workspace）
    → 口令状态闸门（_check_password_change） → 域级权限（common.authorization）
    → 请求级数据范围（common.data_scope.bind_data_scope）

系统位置：本模块是所有路由的依赖入口（``Depends(...)`` 的最内层），本身不承载业务逻辑；
工作区选择、角色读取、服务客户端身份等都只在这里"判定 + 装配"到 AuthContext。真正判定动作
权限的是 ``common.authorization``（require_permission / access_policy），真正限定可见数据的是
``common.data_scope``（with_loader_criteria），资源级归属判定见 ``common.owned_records``。

关键依赖：``SessionToken``（人机会话）与 ``IntegrationAccessToken``/``IntegrationClient``
（客户端令牌）、``LocalCredential``（口令版本与临时口令）、``WorkspaceMember``（成员与角色）、
``WorkspaceLifecycle``/``OrganizationProfile``（工作区与组织是否启用）。

约束：
- 默认拒绝。解析不出令牌、令牌被吊销/过期、工作区未加入或已停用、临时口令未改密、路径未
  登记权限规则，任一情况都直接抛 AuthError / PermissionErrorEx，没有"放行兜底"分支。
- 令牌只从 ``Authorization: Bearer`` 头读取；内容会话额外接受 HttpOnly Cookie，且仅限
  GET/HEAD。URL 查询串中的 token 一律不接受（历史 ``?token=`` 已移除，避免令牌进入日志、
  Referer 与浏览器历史）。
- 上下文与 Session 同生命周期：一次请求一条会话，不可跨请求缓存 AuthContext（工作区状态与
  角色在请求之间可能变化）。resolve_principal 内部会提交心跳时间戳，因此依赖本身带写副作用。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_factory
from ..errors import AuthError, PermissionErrorEx
from ..config import settings
from ..models import AuthIdentity, LocalCredential, SessionToken, User, Workspace, WorkspaceMember

# auto_error=False：缺失或格式错误的 Authorization 头不在 security scheme 层拦截，而是交给
# get_current_context / get_content_auth 抛 AuthError(401)，保证 401 与 403 的语义分界、错误体
# 格式与其它业务异常一致，也避免 FastAPI 默认的 403 混淆"未认证"与"无权限"。
# 该依赖仅用于 OpenAPI 文档展示"Bearer 头"这一认证方式，实际取值见下方的 request.headers。
bearer_scheme = HTTPBearer(auto_error=False, description="登录返回的会话令牌；通过 X-Workspace-Id 选择已加入的工作区")


def get_db() -> Session:
    """会话依赖：产出一条请求级 Session，请求结束（含异常）时必定 close()。

    返回值/副作用：作为 FastAPI 的生成器依赖使用；本函数**不做提交与回滚**，事务边界由服务层
    自己控制（与 db.py 的 expire_on_commit=False 约定一致）。调用方不得把该 Session 传到请求
    之外（例如丢进后台线程），因为它在响应返回后即被关闭。
    """
    factory = session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _hash_token(token: str) -> str:
    """令牌摘要：库中只存 sha256 十六进制摘要，明文令牌永不落库。

    约束：算法必须与令牌签发处（modules/auth/service.py、system_tools 的客户端令牌签发）保持一致，
    否则发版后所有既有令牌会立即失效（表现为全员被登出）。
    """
    # 与签发侧同源，改动需两侧同时进行。
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class AuthContext:
    """一次请求内的登录上下文：谁、在哪个工作区、以什么令牌、能做什么。

    生命周期：由 get_current_context / get_content_auth 在请求开始时构造，随请求结束而丢弃；
    禁止跨请求存进模块级变量或缓存，因为角色、成员关系与工作区启用状态都可能在两次请求之间被改。

    关键不变式：
    - ``workspace`` 一定是 user 已加入且处于启用状态的；``user`` 一定是 status=active 的。
    - ``role`` 是工作区成员表的冗余快照，只用于展示/兜底；**权限判定不看它**，一律走
      ``common.authorization.access_policy``（它按持久化的角色与启用状态重新计算）。
    - ``client_scopes`` 非 None 表示这是服务客户端令牌（非人机），其权限恒被 scope 快照收窄，
      且不能切换工作区；``client_id`` 同时用于豁免"临时口令须先改密"的闸门。
    - ``request_permission`` 不是构造参数，由 enforce_request_permission 在路径映射成功后追加，
      ``data_scope.bind_data_scope`` 依赖它决定按哪个动作限定可见数据。
    """

    db: Session
    user: User
    workspace: Workspace
    role: str  # admin | member
    token: str
    client_scopes: frozenset[str] | None = None
    client_id: str | None = None

    @property
    def workspace_id(self) -> str:
        """当前工作区标识的快捷访问。

        约束：一律以它为准（而不是请求参数或"用户默认工作区"），写库时的 workspace_id 必须来自这里。
        """
        return self.workspace.id

    @property
    def user_id(self) -> str:
        """当前登录用户标识的快捷访问。

        约束：写入资源的 owner_user_id 与审计 actor 时必须取它，不能采信请求体里的创建人字段。
        """
        return self.user.id


@dataclass(frozen=True)
class ResolvedPrincipal:
    """令牌解析结果：身份已确定，但（可能）尚未绑定到具体工作区。

    frozen=True 是刻意约束：解析结果会被多个下游复用（工作区校验、scope 计算），不允许被就地修改。

    字段语义：
    - ``workspace_id`` 仅服务客户端令牌会带上，表示该令牌被锁定在签发时的工作区；人机会话为 None，
      可以按 X-Workspace-Id 或 ``workspace_id`` 查询参数在已加入的工作区之间切换；
    - ``client_scopes`` 非 None 即"这是客户端令牌"，其值已在解析时与客户端当前 scope 取过交集；
    - ``client_id`` 供审计与权限收窄使用（例如 system_tools 会拒绝客户端令牌执行整库备份）。
    """

    user: User
    workspace_id: str | None = None
    client_scopes: frozenset[str] | None = None
    client_id: str | None = None


def resolve_service_principal(db: Session, service) -> ResolvedPrincipal:
    """按当前客户端与原令牌状态重建服务身份；不更新心跳，不保存或返回令牌凭据。"""
    from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient
    if service is not None:
        service = db.get(IntegrationAccessToken, service.id, populate_existing=True)
    if service is None or service.revoked_at is not None or service.expires_at <= datetime.utcnow():
        raise AuthError("服务令牌已失效")
    client = db.scalar(select(IntegrationClient).where(
        IntegrationClient.id == service.client_record_id,
        IntegrationClient.workspace_id == service.workspace_id,
        IntegrationClient.enabled.is_(True),
        IntegrationClient.created_by == service.actor_user_id,
    ).execution_options(populate_existing=True))
    user = db.get(User, service.actor_user_id, populate_existing=True)
    member = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == service.workspace_id,
        WorkspaceMember.user_id == service.actor_user_id,
    ).execution_options(populate_existing=True))
    if client is None or user is None or user.status != "active" or member is None:
        raise AuthError("服务客户端已停用或执行身份不可用")
    return ResolvedPrincipal(user=user, workspace_id=service.workspace_id,
        client_scopes=frozenset(service.scopes or []).intersection(client.scopes or []), client_id=client.client_id)


def resolve_principal(db: Session, token: str) -> ResolvedPrincipal:
    """把原始令牌解析成身份（人机会话或服务客户端），无效即抛 AuthError。

    参数：
    - ``db``：当前请求的会话；
    - ``token``：明文令牌（来自 Bearer 头或内容 Cookie），函数内部只比较 sha256 摘要。

    返回：``ResolvedPrincipal``。人机会话路径只填 user；服务客户端路径额外填 workspace_id /
    client_scopes / client_id。

    业务异常（均为 AuthError，401）：
    - 摘要无匹配 → "无效的会话"；服务客户端被吊销 / 过期 / 客户端停用 / 执行身份不可用；
    - 会话已注销、会话已过期、因长时间未活动过期、用户不可用；
    - 口令版本不一致（改密后旧会话立即失效）或临时口令已过期。

    副作用：命中"距上次活动 ≥ 30 秒"时才更新 ``SessionToken.last_seen_at``/``idle_expires_at``
    （服务令牌则是 IntegrationClient/Token 的 last_used_at）并 ``db.commit()``。因此本函数在
    读接口上也可能写库；30 秒节流是为了避免每个请求都产生一次写入与一次提交。
    """
    digest = _hash_token(token)
    row = db.execute(select(SessionToken).where(SessionToken.token_digest == digest)).scalar_one_or_none()
    if not row:
        from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient
        service = db.scalar(select(IntegrationAccessToken).where(IntegrationAccessToken.token_digest == digest))
        principal = resolve_service_principal(db, service)
        now = datetime.utcnow()
        if (now - service.last_used_at).total_seconds() >= 30:
            service.last_used_at = now
            from sqlalchemy import update
            db.execute(update(IntegrationClient).where(IntegrationClient.id == service.client_record_id).values(
                last_used_at=now,
            ).execution_options(synchronize_session=False))
            db.commit()
        return principal
    now = datetime.utcnow()
    if row.revoked_at is not None:
        raise AuthError("会话已注销")
    if row.expires_at < now:
        raise AuthError("会话已过期")
    if row.idle_expires_at < now:
        raise AuthError("会话因长时间未活动已过期")
    user = db.get(User, row.user_id)
    if not user or user.status != "active":
        raise AuthError("用户不可用")
    credential = db.scalar(select(LocalCredential).join(
        AuthIdentity, AuthIdentity.id == LocalCredential.auth_identity_id,
    ).where(AuthIdentity.user_id == user.id, AuthIdentity.provider == "local"))
    if credential and credential.credential_version != row.credential_version:
        raise AuthError("密码已修改，请重新登录")
    # 临时口令自身也有有效期：即使会话未到期，过期后也必须在依赖层拦住（不能只靠前端提示）。
    if credential and credential.temporary_password_expires_at and credential.temporary_password_expires_at < now:
        raise AuthError("临时密码已过期，请联系管理员")
    if (now - row.last_seen_at).total_seconds() >= 30:
        # 30 秒节流：读接口也会写库，节流值需明显小于 IDLE_TTL_SECONDS，否则活跃用户仍会掉线。
        row.last_seen_at = now
        row.idle_expires_at = min(row.expires_at, now + timedelta(seconds=settings.IDLE_TTL_SECONDS))
        db.commit()
    return ResolvedPrincipal(user=user)


def resolve_user(db: Session, token: str) -> User:
    """Backward-compatible user resolver used by older internal callers.

    中文补充：只返回 User，会丢弃服务客户端的 workspace_id / client_scopes。旧调用方保留此函数，
    新入口（路由依赖、v1 服务层）必须用 ``resolve_principal`` 或 ``get_current_context``，
    否则服务令牌的"工作区锁定"与"scope 收敛"两条约束会被静默绕过。
    """
    return resolve_principal(db, token).user


def _resolve_workspace(db: Session, user: User, requested: Optional[str]) -> Workspace:
    """确定本次请求的工作区：显式请求的必须已加入且启用，否则回退到任一启用工作区。

    参数 ``requested``：来自服务令牌的绑定工作区，或 X-Workspace-Id / ``workspace_id`` 查询参数。

    业务异常（AuthError）：用户未加入任何工作区；请求的工作区非成员或已停用；没有任何可访问的
    启用工作区。

    约束：未显式指定时按成员记录的返回顺序取第一个启用工作区，不做排序也不记忆用户上次选择——
    前端要得到稳定结果必须显式传 X-Workspace-Id。
    """
    memberships = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    ).scalars().all()
    if not memberships:
        raise AuthError("用户未加入任何工作区")
    if requested:
        ws = db.get(Workspace, requested)
        if ws and any(m.workspace_id == ws.id for m in memberships) and _workspace_is_active(db, ws):
            return ws
        raise AuthError("无权限访问该工作区")
    for membership in memberships:
        ws = db.get(Workspace, membership.workspace_id)
        if ws and _workspace_is_active(db, ws):
            return ws
    raise AuthError("用户没有可访问的启用工作区")


def _workspace_is_active(db: Session, workspace: Workspace) -> bool:
    """工作区是否可用：生命周期为 active 且所属组织为 active，缺行按可用处理。

    参数：``workspace`` 为待判断的工作区行。返回 True/False（不抛异常）。副作用：只读。

    约束：未初始化 lifecycle / 组织档案的工作区视为可用（兼容历史数据），不能让缺行把老工作区锁死；
    工作区一旦停用，其成员的全部接口都会在依赖阶段失败，因此"停用/启用工作区"本身必须有不依赖本
    判断的入口。
    """
    # 缺行按"启用"处理以兼容历史数据（未初始化 lifecycle / 组织档案的工作区不能被静默锁死）。
    from app.modules.system.models import OrganizationProfile, WorkspaceLifecycle
    lifecycle = db.scalar(select(WorkspaceLifecycle).where(
        WorkspaceLifecycle.workspace_id == workspace.id,
    ))
    organization = db.scalar(select(OrganizationProfile).where(
        OrganizationProfile.organization_id == workspace.organization_id,
    ))
    return (lifecycle is None or lifecycle.status == "active") and (
        organization is None or organization.status == "active"
    )


def get_current_context(
    request: Request,
    db: Session = Depends(get_db),
    x_workspace_id: Optional[str] = Header(default=None),
    # credentials 仅用于让 OpenAPI 展示 Bearer 认证方式；实际令牌取自 request.headers，
    # 不要改成用 credentials.credentials 取值，否则内容 Cookie 路径与错误语义都会变。
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthContext:
    """主认证依赖：Bearer 令牌 → AuthContext，并完成权限与数据范围的绑定。

    认证方式：只认 ``Authorization: Bearer <token>``（不读 Cookie，也不读查询串）。

    业务异常：AuthError(401) —— 缺少认证信息、令牌无效/过期/已注销、服务令牌试图切换工作区；
    PermissionErrorEx(403) —— 临时口令未改密，或路径未登记权限规则 / 当前角色没有对应动作权限。

    副作用：可能提交会话心跳（见 resolve_principal）；会调用
    ``bind_data_scope`` 把可见性条件写进 ``ctx.db.info``，从而影响本次请求内**所有** ORM 查询。

    顺序约束：口令闸门必须在权限与数据范围之前执行——临时口令状态下不允许列举资源，否则仅凭
    一个临时口令就能探测工作区内的数据。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError("缺少认证信息")
    token = auth[7:].strip()
    principal = resolve_principal(db, token)
    user = principal.user
    if principal.workspace_id and x_workspace_id and x_workspace_id != principal.workspace_id:
        raise AuthError("服务令牌不能切换工作区")
    workspace = _resolve_workspace(db, user, principal.workspace_id or x_workspace_id)
    memberships = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == workspace.id
        )
    ).scalars().all()
    role = memberships[0].role if memberships else "member"
    ctx = AuthContext(
        db=db, user=user, workspace=workspace, role=role, token=token,
        client_scopes=principal.client_scopes, client_id=principal.client_id,
    )
    _check_password_change(ctx, request.url.path)
    from app.common.authorization import enforce_request_permission
    enforce_request_permission(ctx, request)
    from app.common.data_scope import bind_data_scope
    bind_data_scope(ctx)
    return ctx


def require_admin(ctx: AuthContext):
    """管理员依赖：要求 ``system.admin`` 权限码，否则抛 PermissionErrorEx(403)。

    返回入参 ctx，便于在路由签名里 ``ctx = Depends(require_admin)`` 直接拿到上下文。
    约束：判定走 require_permission（未知权限码同样拒绝），因此客户端令牌即使携带 system.admin
    也会被自身 scope 收窄；需要"仅限人机管理员"的场景还要另加 client_id 判断。
    """
    from app.common.authorization import require_permission
    require_permission(ctx, "system.admin")
    return ctx


def _check_password_change(ctx: AuthContext, path: str) -> None:
    """A temporary password grants access only to account recovery operations.

    中文补充：首次登录或管理员重置后（``must_change_password``）只放行"自助改密逃生通道"——
    ``/api/auth/me``、``/api/auth/logout``、``/api/auth/password``。这三条白名单必须与
    modules/auth 实际注册的路径保持一致，改路由前缀时要同步这里，否则用户会被锁死在改密页。

    约束：服务客户端令牌（``ctx.client_id``）直接跳过——客户端没有"人机改密"语义，其口令状态
    由 IntegrationClient 的启停与 scope 控制。异常携带 ``password_change_required=True``，
    供前端识别并跳转到改密流程（见 errors.py 的 extra 约定）。
    """
    if ctx.client_id:
        return
    credential = ctx.db.scalar(select(LocalCredential).join(
        AuthIdentity, AuthIdentity.id == LocalCredential.auth_identity_id,
    ).where(AuthIdentity.user_id == ctx.user_id, AuthIdentity.provider == "local"))
    if credential and credential.must_change_password and path not in {
        "/api/auth/me", "/api/auth/logout", "/api/auth/password",
    }:
        raise PermissionErrorEx("首次登录或密码重置后，请先修改密码", password_change_required=True)


def get_content_auth(
    request: Request,
    db: Session = Depends(get_db),
    x_workspace_id: Optional[str] = Header(default=None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthContext:
    """Headers or HttpOnly browser cookie; URL query tokens are not accepted.

    中文补充：给"浏览器直接发起的资源请求"用的依赖——把令牌放在 ``<img src>``/下载链接/媒体
    标签里无法带 Authorization 头，因此允许读取内容会话 Cookie（见 common/content_session）。

    与 get_current_context 的差异：
    - 认证顺序为先 Bearer 头、再 Cookie，二者取一，都没有则 AuthError(401)；
    - Cookie 只在 ``GET``/``HEAD`` 上生效，写方法必须显式带 Bearer 头，避免 CSRF 借 Cookie 提权；
    - 工作区除 X-Workspace-Id 外还接受 ``workspace_id`` 查询参数（页面内嵌资源无法自定义请求头），
      但令牌本身仍不得出现在查询串里。

    业务异常与副作用同 get_current_context：401（认证/工作区）与 403（改密闸门、权限、未登记路径），
    并会写会话心跳、绑定数据范围。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    else:
        from .content_session import COOKIE_NAME
        token = request.cookies.get(COOKIE_NAME, "") if request.method in {"GET", "HEAD"} else ""
    if not token:
        raise AuthError("缺少认证信息")
    principal = resolve_principal(db, token)
    user = principal.user
    requested_workspace_id = x_workspace_id or request.query_params.get("workspace_id")
    if (
        principal.workspace_id
        and requested_workspace_id
        and requested_workspace_id != principal.workspace_id
    ):
        raise AuthError("服务令牌不能切换工作区")
    workspace = _resolve_workspace(
        db, user, principal.workspace_id or requested_workspace_id
    )
    memberships = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == workspace.id
        )
    ).scalars().all()
    role = memberships[0].role if memberships else "member"
    ctx = AuthContext(
        db=db, user=user, workspace=workspace, role=role, token=token,
        client_scopes=principal.client_scopes, client_id=principal.client_id,
    )
    _check_password_change(ctx, request.url.path)
    from app.common.authorization import enforce_request_permission
    enforce_request_permission(ctx, request)
    from app.common.data_scope import bind_data_scope
    bind_data_scope(ctx)
    return ctx
