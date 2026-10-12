# -*- coding: utf-8 -*-
"""工作区与成员管理路由（HTTP 前缀 ``/api/workspace``）。

职责：查询当前工作区基本信息、列出 / 新增工作区成员、由管理员创建本地账号。
登录上下文统一由 ``Depends(get_current_context)`` 注入；权限不足由 ``require_admin`` /
``require_permission`` 抛业务异常（403），目标资源不存在或不属于本工作区时抛 ``NotFoundError``。

约束：
- 读写一律限定在 ``ctx.workspace_id``（组织归属判断用 ``ctx.workspace.organization_id``）内，
  不允许跨工作区读取成员或用户。
- 新增成员 / 创建用户会写 ``WorkspaceMember``、``AuthIdentity``、``LocalCredential``、
  ``MemberProfile`` 等多张表并重绑角色，审计记录必须随之落库（见各函数"副作用"说明）。
- 新建账号的初始密码为一次性密码（``must_change_password=True``），因此响应只回标识字段，
  绝不返回密码、哈希或会话信息。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...common.deps import AuthContext, get_current_context, require_admin
from ...errors import ValidationError
from ...ids import new_id
from ...models import Workspace, WorkspaceMember
from datetime import datetime

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("")
def workspace_info(ctx: AuthContext = Depends(get_current_context)):
    """返回当前登录用户所处的工作区基本信息。

    HTTP：``GET /api/workspace``。仅要求已登录（由 ``get_current_context`` 完成认证与
    请求级权限校验），无额外业务权限要求。

    参数：
        ctx: 登录上下文（依赖注入）；工作区由 token 或 ``X-Workspace-Id`` 解析得到，
            服务令牌不允许切换工作区。

    返回：``{"id", "name", "organization_id"}``，只透出标识字段，不含配额、密钥等配置。
    副作用：无（只读，不写审计）。
    """
    return {"id": ctx.workspace.id, "name": ctx.workspace.name,
            "organization_id": ctx.workspace.organization_id}


@router.get("/members")
def members(ctx: AuthContext = Depends(get_current_context)):
    """列出当前工作区的成员及其显示名与角色。

    HTTP：``GET /api/workspace/members``。权限：``system.admin``（由 ``require_admin`` 校验）。

    返回：``{"items": [{"user_id", "display_name", "role"}], "count": n}``。
    用户记录已不存在时 ``display_name`` 退化为空串，仍保留成员行，以免前端计数与实际成员数错位。

    副作用：无（只读，不写审计）。
    """
    require_admin(ctx)
    rows = ctx.db.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ctx.workspace_id)
                          ).scalars().all()
    from ...models import User
    out = []
    for m in rows:
        u = ctx.db.get(User, m.user_id)
        out.append({"user_id": m.user_id, "display_name": u.display_name if u else "",
                    "role": m.role})
    return {"items": out, "count": len(out)}


class MemberAdd(BaseModel):
    """新增成员 / 调整成员角色的请求体。

    ``role`` 只允许 ``admin`` / ``member``（正则约束），分别映射到工作区预置角色
    ``workspace_admin`` / ``member``；更细粒度的多角色分配走 system 模块的用户角色接口，
    不要在此扩展，避免两处角色来源不一致。
    """

    user_id: str
    role: str = Field(default="member", pattern="^(admin|member)$")


class AdminUserCreate(BaseModel):
    """管理员创建本地账号的请求体。

    ``username`` 限定 3–80 位 ``[A-Za-z0-9_.-]``，落库前会转为小写做归一化比对，
    以防大小写不同的重名账号；``password`` 至少 10 位，创建后标记为必须修改；
    ``role`` 语义同 ``MemberAdd.role``。
    """

    username: str = Field(min_length=3, max_length=80, pattern="^[A-Za-z0-9_.-]+$")
    display_name: str = Field(default="", max_length=100)
    password: str = Field(min_length=10, max_length=256, repr=False)
    role: str = Field(default="member", pattern="^(admin|member)$")


@router.post("/users", status_code=201)
def create_user(payload: AdminUserCreate, ctx: AuthContext = Depends(get_current_context)):
    """管理员创建本地账号并直接加入当前工作区。

    HTTP：``POST /api/workspace/users``（201）。除 ``system.admin`` 外，同样要求
    ``system.users.manage/assign_roles`` 及新用户服务的角色、部门范围校验。

    参数：
        payload: 见 ``AdminUserCreate``；用户名 / 密码为空或用户名已被占用时抛 ``ValidationError``
            （重名按 ``provider="local"`` + 归一化用户名判断，与其他登录方式互不影响）。

    返回：``{"id", "username", "display_name", "role"}``，不含密码或凭据字段。
    副作用：复用新版创建服务，将账号、组织/工作区成员、角色绑定与审计同事务提交。
    新账号首次登录强制改密，不能通过旧入口直接写管理员兼容字段。
    """
    from ..system.models import Role
    from ..system.schemas import UserCreate
    from ..system.user_service import create_user as create_system_user

    require_admin(ctx)
    role = ctx.db.scalar(select(Role).where(
        Role.workspace_id == ctx.workspace_id,
        Role.code == ("workspace_admin" if payload.role == "admin" else "member"),
    ))
    if role is None:
        raise ValidationError("工作区默认角色尚未初始化")
    user = create_system_user(ctx, UserCreate(
        username=payload.username, display_name=payload.display_name or payload.username,
        password=payload.password, role_ids=[role.id],
    ))
    return {"id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "role": payload.role}


@router.post("/members", status_code=201)
def add_member(payload: MemberAdd, ctx: AuthContext = Depends(get_current_context)):
    """把已有用户加入当前工作区，或调整其在工作区内的角色。

    HTTP：``POST /api/workspace/members``（201）。权限：``system.admin``，且必须持有
    ``system.users.assign_roles``；目标用户已是本工作区成员时还会校验操作者可见范围
    （``require_user_scope``），越界抛权限异常。

    参数：
        payload: ``user_id`` 必须存在且状态为 ``active``，否则抛 ``NotFoundError``；
            非成员时还要求其属于同一组织（跨组织须走成员调动流程）；
            ``role`` 对应的预置角色未初始化时抛 ``ValidationError``。

    返回：``{"ok": True}``。
    副作用：先对 workspace 行加锁串行化特权变更，再写 / 改 ``WorkspaceMember``、补齐
    ``MemberProfile`` 并重绑 ``UserRole``；经 ``commit_change`` 将业务数据与
    ``workspace.add_member`` 审计记录同事务提交，重复键或并发冲突统一转 ``ConflictError``。
    """
    require_admin(ctx)
    from ...common.authorization import require_permission, require_user_scope
    from ...errors import NotFoundError
    from ...models import OrganizationMember, User
    from ..system.models import MemberProfile, Role
    from ..system.service_helpers import commit_change, lock_workspace
    from ..system.user_service import _set_roles, _validate_assignment

    require_permission(ctx, "system.users.assign_roles")
    # 角色变更必须先锁工作区行：否则并发升降级可能同时通过"最后一个管理员"校验。
    lock_workspace(ctx)
    target = ctx.db.get(User, payload.user_id)
    if target is None or target.status != "active":
        raise NotFoundError("目标用户不存在或未启用")
    existing = ctx.db.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ctx.workspace_id,
        WorkspaceMember.user_id == payload.user_id)).scalar_one_or_none()
    if not existing:
        # 有 WorkspaceMember 记录不代表组织归属成立；跨组织直接建成员会绕过调动流程。
        organization_member = ctx.db.scalar(select(OrganizationMember.id).where(
            OrganizationMember.organization_id == ctx.workspace.organization_id,
            OrganizationMember.user_id == payload.user_id,
        ))
        if organization_member is None:
            raise NotFoundError("目标用户不属于当前组织，请通过成员调动流程处理")
        # 先以 member 落库占位，最终角色统一由下面的 _set_roles 决定，避免两处状态不一致。
        existing = WorkspaceMember(id=new_id(), workspace_id=ctx.workspace_id,
                                   user_id=payload.user_id, role="member", created_at=datetime.utcnow())
        ctx.db.add(existing)
    else:
        require_user_scope(ctx, "system.users.assign_roles", payload.user_id)
    role = ctx.db.scalar(select(Role).where(
        Role.workspace_id == ctx.workspace_id,
        Role.code == ("workspace_admin" if payload.role == "admin" else "member"),
    ))
    if role is None:
        raise ValidationError("工作区默认角色尚未初始化")
    roles = _validate_assignment(ctx, [role.id], None, [])
    profile = ctx.db.scalar(select(MemberProfile).where(
        MemberProfile.workspace_id == ctx.workspace_id, MemberProfile.user_id == payload.user_id,
    ))
    if profile is None:
        ctx.db.add(MemberProfile(workspace_id=ctx.workspace_id, user_id=payload.user_id))
    ctx.db.flush()
    _set_roles(ctx, payload.user_id, existing, roles)
    commit_change(ctx, "workspace.add_member", "workspace", ctx.workspace_id,
                  {"user_id": payload.user_id, "role": payload.role})
    return {"ok": True}
