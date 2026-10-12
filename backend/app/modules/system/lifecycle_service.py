"""Organization/workspace lifecycle and cross-workspace member transfer.

职责：组织与工作区的生命周期管理（编码、说明、active/archived 状态）以及组织内成员的
跨工作区复制/调动。历史数据不做搬迁 —— organizations/workspaces 仍是权威表，本模块在其上
叠加 system_organization_profiles / system_workspace_lifecycles 两张“增量元数据”表。

位置与依赖：对外路由前缀 /api/v1/system（governance_router.py）—— GET/POST /organizations、
PATCH /organizations/{id}、GET/POST /workspaces、PATCH /workspaces/{id}、
POST /workspaces/member-transfer。依赖 service_helpers（乐观锁、最后一个管理员保护）、
bootstrap.ensure_system_roles（新工作区初始化内置角色）。

关键约束：
- 归档不删除：工作区/组织的 id 永久保留，归档后不可登录（OIDC 公共入口与会话签发都会校验
  status="active"）；组织归档前必须先归档其全部工作区，且组织内至少保留一个启用工作区。
- 权限：查看需要 system.workspaces.view，管理需要 system.workspaces.manage；此外所有写操作
  还要求操作人是当前组织的 OrganizationMember 且 role="admin"。
- 免费序：旧数据由 ensure_all_lifecycle_profiles 在启动时补齐元数据行（幂等，容忍并发）。
"""
from __future__ import annotations

import re
from datetime import datetime
from threading import RLock

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.common.audit import audit
from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.ids import new_id
from app.models import Organization, OrganizationMember, User, Workspace, WorkspaceMember
from app.modules.system.bootstrap import ensure_system_roles
from app.modules.system.lifecycle_schemas import (
    MemberTransfer, OrganizationCreate, OrganizationUpdate, WorkspaceCreate, WorkspaceUpdate,
)
from app.modules.system.models import (
    Department, MemberProfile, OrganizationProfile, ResourceGrant, Role, UserRole,
    WorkspaceLifecycle,
)
from app.modules.system.service_helpers import check_version, protect_last_administrator

CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")
# 进程内可重入锁：生命周期元数据的“补齐”是幂等但会并发插入的，用进程锁 + 唯一约束双保险；
# 多进程部署时仍依赖 _insert_or_get 的 savepoint 兜底（见下）。
_lifecycle_adoption_lock = RLock()


def _generated_code(prefix: str, identifier: str) -> str:
    """为历史数据生成确定性编码（org-<id>/ws-<id>），保证多次补齐得到同一个值。"""
    return f"{prefix}-{identifier.lower()}"


def _insert_or_get(db, model, lookup: dict, values: dict):
    """Insert one additive metadata row while tolerating a concurrent adopter.

    参数：model 目标模型（OrganizationProfile/WorkspaceLifecycle）；lookup 唯一键过滤条件；
    values 插入值。
    返回：新建的实例，或并发下已存在的既有行。
    异常：IntegrityError —— 冲突后按 lookup 也查不到（说明不是唯一键竞争，原样抛出）。
    副作用：使用 begin_nested（SAVEPOINT）插入，冲突只回滚到保存点，不会让调用方的
    “登录/请求”事务整体失效 —— 这是本函数存在的核心原因。
    """
    candidate = model(**values)
    try:
        # A savepoint contains a uniqueness race without invalidating the
        # caller's whole authentication/request transaction.
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return candidate
    except IntegrityError:
        db.expire_all()
        existing = db.scalar(select(model).filter_by(**lookup))
        if existing is None:
            raise
        return existing


def _ensure_organization_profiles(db, organization: Organization) -> tuple[OrganizationProfile, list[WorkspaceLifecycle]]:
    """补齐一个组织及其全部工作区的生命周期元数据（幂等）。

    参数：db；organization 权威组织行。返回：(组织元数据, 该组织下全部工作区元数据列表)。
    异常：无显式业务异常（并发冲突由 _insert_or_get 消化）。
    副作用：缺失时插入新行（编码按 org-<id>/ws-<id> 生成，description 为空、status=active、
    时间戳沿用源行的 created_at，保证列表排序与历史一致）。不提交事务。
    """
    profile = db.scalar(select(OrganizationProfile).where(
        OrganizationProfile.organization_id == organization.id,
    ))
    if profile is None:
        profile = _insert_or_get(db, OrganizationProfile, {"organization_id": organization.id}, {
            "id": new_id(), "organization_id": organization.id,
            "code": _generated_code("org", organization.id), "description": "", "status": "active",
            "created_at": organization.created_at, "updated_at": datetime.utcnow(),
        })
    rows = []
    for workspace in db.scalars(select(Workspace).where(
        Workspace.organization_id == organization.id,
    ).order_by(Workspace.created_at, Workspace.id)).all():
        lifecycle = db.scalar(select(WorkspaceLifecycle).where(
            WorkspaceLifecycle.workspace_id == workspace.id,
        ))
        if lifecycle is None:
            lifecycle = _insert_or_get(db, WorkspaceLifecycle, {"workspace_id": workspace.id}, {
                "id": new_id(), "workspace_id": workspace.id, "organization_id": organization.id,
                "code": _generated_code("ws", workspace.id), "description": "", "status": "active",
                "created_at": workspace.created_at, "updated_at": datetime.utcnow(),
            })
        rows.append(lifecycle)
    return profile, rows


def ensure_all_lifecycle_profiles(db) -> None:
    """Adopt legacy base rows at startup before the server accepts requests.

    用途：启动阶段一次性把历史组织/工作区纳入生命周期表，避免首个管理员登录时才发现缺元数据。
    参数：db 会话。返回：无。异常：无显式业务异常。
    副作用：可能插入多行元数据并 commit（这是本模块唯一自行提交的函数，因为它在请求生命周期之外）；
    进程内用 _lifecycle_adoption_lock 串行化，重复启动不会产生重复行。
    """
    with _lifecycle_adoption_lock:
        for organization in db.scalars(select(Organization).order_by(
            Organization.created_at, Organization.id,
        )).all():
            _ensure_organization_profiles(db, organization)
        db.commit()


def ensure_profiles(ctx: AuthContext) -> tuple[OrganizationProfile, list[WorkspaceLifecycle]]:
    """Adopt legacy organization/workspace rows without mutating their IDs or data.

    用途：治理页读取前确保元数据已就绪（GET /organizations、GET /workspaces 会先调用它）。
    参数：ctx。返回：(当前组织的元数据, 当前组织下全部工作区元数据列表)。
    异常：NotFoundError（当前组织不存在）。权限：本函数不校验权限，调用方需先 require_permission。
    副作用：可能插入元数据行并 commit；不改动 organizations/workspaces 的既有字段与主键。
    """
    organization = ctx.db.get(Organization, ctx.workspace.organization_id)
    if organization is None:
        raise NotFoundError("当前组织不存在")
    with _lifecycle_adoption_lock:
        result = _ensure_organization_profiles(ctx.db, organization)
        ctx.db.commit()
        return result


def _organization_admin(ctx: AuthContext, organization_id: str) -> OrganizationMember:
    """要求操作人是指定组织的管理员（OrganizationMember.role == "admin"）。

    返回：组织成员行。异常：PermissionErrorEx —— 非该组织成员或不是管理员。
    约束：这是组织/工作区写操作的额外门槛（在 system.workspaces.manage 之外的“组织维度”检查），
    即使拥有平台级权限也必须满足；副作用：只读。
    """
    membership = ctx.db.scalar(select(OrganizationMember).where(
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.user_id == ctx.user_id,
    ))
    if membership is None or membership.role != "admin":
        raise PermissionErrorEx("只有组织管理员可以管理组织和工作区")
    return membership


def _organization_out(ctx: AuthContext, organization: Organization, profile: OrganizationProfile) -> dict:
    """组织输出结构：名称等权威字段取自 organizations，编码/说明/状态/版本取自元数据表。

    workspace_count 统计组织下全部工作区，active_workspace_count 只统计未归档工作区；
    current 标记是否是操作人当前所在组织（前端据此禁用“归档当前组织”等操作）。
    """
    total = ctx.db.scalar(select(func.count()).select_from(Workspace).where(
        Workspace.organization_id == organization.id,
    )) or 0
    active = ctx.db.scalar(select(func.count()).select_from(WorkspaceLifecycle).where(
        WorkspaceLifecycle.organization_id == organization.id,
        WorkspaceLifecycle.status == "active",
    )) or 0
    return {
        "id": organization.id, "code": profile.code, "name": organization.name,
        "description": profile.description, "status": profile.status, "version": profile.version,
        "workspace_count": total, "active_workspace_count": active,
        "current": organization.id == ctx.workspace.organization_id,
        "created_at": profile.created_at, "updated_at": profile.updated_at,
    }


def _workspace_out(ctx: AuthContext, workspace: Workspace, lifecycle: WorkspaceLifecycle) -> dict:
    """工作区输出结构：名称取自 workspaces，编码/说明/状态/版本取自生命周期表。

    member_count 统计成员总数；current 标记当前工作区；actor_role 是操作人在该工作区的角色
    （非成员为 None），用于治理页展示“我在这个工作区里是什么身份”。
    """
    member_count = ctx.db.scalar(select(func.count()).select_from(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace.id,
    )) or 0
    actor = ctx.db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace.id,
        WorkspaceMember.user_id == ctx.user_id,
    ))
    return {
        "id": workspace.id, "organization_id": workspace.organization_id,
        "code": lifecycle.code, "name": workspace.name,
        "description": lifecycle.description, "status": lifecycle.status,
        "version": lifecycle.version, "member_count": member_count,
        "current": workspace.id == ctx.workspace_id,
        "actor_role": actor.role if actor else None,
        "created_at": lifecycle.created_at, "updated_at": lifecycle.updated_at,
    }


def list_organizations(ctx: AuthContext) -> list[dict]:
    """列出操作人所属的全部组织（GET /api/v1/system/organizations，需要 system.workspaces.view）。

    返回：_organization_out 列表，仅包含操作人是其成员的组织（跨组织信息不泄露）。
    异常：PermissionErrorEx（缺少查看权限）。副作用：先执行元数据补齐（可能写库并提交）。
    """
    require_permission(ctx, "system.workspaces.view")
    ensure_profiles(ctx)
    organization_ids = ctx.db.scalars(select(OrganizationMember.organization_id).where(
        OrganizationMember.user_id == ctx.user_id,
    )).all()
    items = []
    for organization in ctx.db.scalars(select(Organization).where(
        Organization.id.in_(organization_ids),
    ).order_by(Organization.created_at, Organization.id)).all():
        profile = ctx.db.scalar(select(OrganizationProfile).where(
            OrganizationProfile.organization_id == organization.id,
        ))
        if profile:
            items.append(_organization_out(ctx, organization, profile))
    return items


def list_workspaces(ctx: AuthContext) -> list[dict]:
    """列出当前组织下的全部工作区（GET /api/v1/system/workspaces，需要 system.workspaces.view）。

    返回：_workspace_out 列表。异常：PermissionErrorEx（缺少查看权限或不是当前组织管理员 ——
    工作区属于组织级信息，即便有查看动作也要求组织管理员身份）。
    副作用：先补齐元数据（可能写库并提交）；只读查询。
    """
    require_permission(ctx, "system.workspaces.view")
    _, lifecycles = ensure_profiles(ctx)
    _organization_admin(ctx, ctx.workspace.organization_id)
    by_id = {item.workspace_id: item for item in lifecycles}
    return [
        _workspace_out(ctx, workspace, by_id[workspace.id])
        for workspace in ctx.db.scalars(select(Workspace).where(
            Workspace.organization_id == ctx.workspace.organization_id,
        ).order_by(Workspace.created_at, Workspace.id)).all()
    ]


def create_organization(ctx: AuthContext, payload: OrganizationCreate) -> dict:
    """创建组织及其首个工作区（POST /api/v1/system/organizations，需要 system.workspaces.manage）。

    参数：ctx；payload 为 OrganizationCreate（组织编码 + 初始工作区编码/名称）。
    返回：新组织的 _organization_out。
    异常：ValidationError（编码格式不合法 —— 必须字母开头，只含字母数字与 _.-）；ConflictError
    （编码重复，含并发写入时的唯一约束冲突）；PermissionErrorEx（缺少管理权限）。
    副作用（单事务）：写 organizations/workspaces 权威行、两张元数据表、把操作人设为新组织的
    admin 与新工作区的 admin，并调用 ensure_system_roles 初始化新工作区的内置角色；
    写 organization.create 审计后提交。管理员权限不继承自原组织，因此不需要组织管理员前置校验。
    """
    require_permission(ctx, "system.workspaces.manage")
    if not CODE_RE.fullmatch(payload.code) or not CODE_RE.fullmatch(payload.initial_workspace_code):
        raise ValidationError("组织和工作区编码格式不正确")
    now = datetime.utcnow()
    organization = Organization(id=new_id(), name=payload.name.strip(), created_at=now)
    workspace = Workspace(id=new_id(), organization_id=organization.id,
                          name=payload.initial_workspace_name.strip(), created_at=now)
    ctx.db.add_all([organization, workspace])
    ctx.db.flush()
    ctx.db.add_all([
        OrganizationProfile(
            id=new_id(), organization_id=organization.id, code=payload.code.strip(),
            description=payload.description.strip(), status="active", created_at=now, updated_at=now,
        ),
        WorkspaceLifecycle(
            id=new_id(), workspace_id=workspace.id, organization_id=organization.id,
            code=payload.initial_workspace_code.strip(), description="", status="active",
            created_at=now, updated_at=now,
        ),
        OrganizationMember(id=new_id(), organization_id=organization.id,
                           user_id=ctx.user_id, role="admin", created_at=now),
        WorkspaceMember(id=new_id(), workspace_id=workspace.id,
                        user_id=ctx.user_id, role="admin", created_at=now),
    ])
    try:
        ctx.db.flush()
        ensure_system_roles(ctx.db, workspace.id)
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="organization.create", resource_type="organization", resource_id=organization.id,
              metadata={"workspace_id": workspace.id})
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("组织编码或工作区编码已存在") from exc
    profile = ctx.db.scalar(select(OrganizationProfile).where(
        OrganizationProfile.organization_id == organization.id,
    ))
    return _organization_out(ctx, organization, profile)


def update_organization(ctx: AuthContext, organization_id: str, payload: OrganizationUpdate) -> dict:
    """修改组织信息或归档组织（PATCH /api/v1/system/organizations/{id}）。

    参数：ctx；organization_id；payload 为 OrganizationUpdate（version 必填）。
    返回：更新后的 _organization_out。
    异常：NotFoundError（组织或元数据不存在）；PermissionErrorEx（缺少管理权限或非该组织管理员）；
    ConflictError（版本冲突；归档时组织下仍有启用的工作区）。
    副作用：名称写回 organizations（权威表），其余字段写元数据表，写 organization.update 审计后提交。
    约束：组织归档前必须先归档全部工作区；归档后其工作区不能再登录（生命周期状态参与登录校验）。
    """
    require_permission(ctx, "system.workspaces.manage")
    _organization_admin(ctx, organization_id)
    organization = ctx.db.get(Organization, organization_id)
    profile = ctx.db.scalar(select(OrganizationProfile).where(
        OrganizationProfile.organization_id == organization_id,
    ))
    if organization is None or profile is None:
        raise NotFoundError("组织不存在")
    check_version(profile, payload.version)
    values = payload.model_dump(exclude_unset=True)
    values.pop("version", None)
    if "name" in values:
        organization.name = values.pop("name").strip()
    if values.get("status") == "archived":
        active = ctx.db.scalar(select(WorkspaceLifecycle.id).where(
            WorkspaceLifecycle.organization_id == organization_id,
            WorkspaceLifecycle.status == "active",
        ))
        if active:
            raise ConflictError("请先归档该组织的全部工作区")
    for key, value in values.items():
        setattr(profile, key, value.strip() if isinstance(value, str) else value)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="organization.update", resource_type="organization", resource_id=organization_id,
          metadata={"fields": sorted(payload.model_fields_set - {"version"})})
    ctx.db.commit()
    return _organization_out(ctx, organization, profile)


def create_workspace(ctx: AuthContext, payload: WorkspaceCreate) -> dict:
    """在当前组织内新建工作区（POST /api/v1/system/workspaces，需要 system.workspaces.manage）。

    参数：ctx；payload 为 WorkspaceCreate。返回：新工作区的 _workspace_out。
    异常：ValidationError（编码格式不合法）；ConflictError（同组织内编码重复）；
    PermissionErrorEx（缺少管理权限或不是当前组织管理员）。
    副作用（单事务）：写 workspaces 与生命周期元数据、把操作人设为新工作区 admin、
    调用 ensure_system_roles 初始化内置角色并种入提示词/参数，分别在新旧工作区写
    workspace.create 与 workspace.initialize 两条审计后提交。
    """
    require_permission(ctx, "system.workspaces.manage")
    _organization_admin(ctx, ctx.workspace.organization_id)
    if not CODE_RE.fullmatch(payload.code):
        raise ValidationError("工作区编码格式不正确")
    now = datetime.utcnow()
    workspace = Workspace(id=new_id(), organization_id=ctx.workspace.organization_id,
                          name=payload.name.strip(), created_at=now)
    ctx.db.add(workspace)
    ctx.db.flush()
    lifecycle = WorkspaceLifecycle(
        id=new_id(), workspace_id=workspace.id, organization_id=workspace.organization_id,
        code=payload.code.strip(), description=payload.description.strip(), status="active",
        created_at=now, updated_at=now,
    )
    ctx.db.add_all([
        lifecycle,
        WorkspaceMember(id=new_id(), workspace_id=workspace.id,
                        user_id=ctx.user_id, role="admin", created_at=now),
    ])
    try:
        ctx.db.flush()
        ensure_system_roles(ctx.db, workspace.id)
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="workspace.create", resource_type="workspace", resource_id=workspace.id)
        audit(ctx.db, workspace_id=workspace.id, actor_user_id=ctx.user_id,
              action="workspace.initialize", resource_type="workspace", resource_id=workspace.id)
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("工作区编码已存在") from exc
    return _workspace_out(ctx, workspace, lifecycle)


def _workspace_target(ctx: AuthContext, workspace_id: str) -> tuple[Workspace, WorkspaceLifecycle]:
    """按标识取工作区与其生命周期元数据，并限制在同一组织内。

    返回：(workspace, lifecycle)。异常：NotFoundError —— 工作区或元数据不存在、或不属于当前组织
    （跨组织操作与不存在返回同一个错误，避免探测其他组织的 id）。
    """
    workspace = ctx.db.get(Workspace, workspace_id)
    lifecycle = ctx.db.scalar(select(WorkspaceLifecycle).where(
        WorkspaceLifecycle.workspace_id == workspace_id,
    ))
    if workspace is None or lifecycle is None or workspace.organization_id != ctx.workspace.organization_id:
        raise NotFoundError("工作区不存在或不属于当前组织")
    return workspace, lifecycle


def update_workspace(ctx: AuthContext, workspace_id: str, payload: WorkspaceUpdate) -> dict:
    """修改或归档工作区（PATCH /api/v1/system/workspaces/{id}）。

    参数：ctx；workspace_id；payload 为 WorkspaceUpdate（version 必填）。
    返回：更新后的 _workspace_out。
    异常：NotFoundError（工作区不属于当前组织）；PermissionErrorEx（缺少管理权限或非组织管理员）；
    ConflictError（版本冲突；归档当前工作区；归档后组织将没有启用工作区）。
    副作用：名称写回 workspaces，其余字段写生命周期元数据，写 workspace.update 审计后提交。
    约束：不能在当前工作区里归档当前工作区（需先切到同组织其它工作区），保证操作人不会把自己锁在门外。
    """
    require_permission(ctx, "system.workspaces.manage")
    _organization_admin(ctx, ctx.workspace.organization_id)
    workspace, lifecycle = _workspace_target(ctx, workspace_id)
    check_version(lifecycle, payload.version)
    values = payload.model_dump(exclude_unset=True)
    values.pop("version", None)
    status = values.get("status")
    if status == "archived":
        if workspace_id == ctx.workspace_id:
            raise ConflictError("不能在当前工作区内归档当前工作区，请先切换到同组织的其他工作区")
        active_count = ctx.db.scalar(select(func.count()).select_from(WorkspaceLifecycle).where(
            WorkspaceLifecycle.organization_id == workspace.organization_id,
            WorkspaceLifecycle.status == "active",
        )) or 0
        if active_count <= 1:
            raise ConflictError("组织必须保留至少一个启用工作区")
    if "name" in values:
        workspace.name = values.pop("name").strip()
    for key, value in values.items():
        setattr(lifecycle, key, value.strip() if isinstance(value, str) else value)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="workspace.update", resource_type="workspace", resource_id=workspace_id,
          metadata={"fields": sorted(payload.model_fields_set - {"version"})})
    ctx.db.commit()
    return _workspace_out(ctx, workspace, lifecycle)


def transfer_member(ctx: AuthContext, payload: MemberTransfer) -> dict:
    """把成员复制或调动到同组织的另一个工作区（POST /api/v1/system/workspaces/member-transfer）。

    参数：ctx；payload 为 MemberTransfer（user_id、target_workspace_id、mode=copy|move、
    target_role_code）。返回：TransferResult 结构（含中文结果说明）。
    异常：ValidationError（目标是当前工作区、目标角色不存在或已停用）；NotFoundError
    （目标工作区不属于本组织、成员不属于当前工作区）；ConflictError（已属于目标工作区、
    目标工作区已归档、move 时成员仍是部门负责人、或会移除最后一个启用管理员）；
    PermissionErrorEx（缺少管理权限或非组织管理员）。
    copy 语义：在目标工作区建立成员关系与成员资料（复制员工编号/邮箱/电话，但部门与岗位为空）
    并按 target_role_code 授予角色；源工作区的成员关系保持不变。
    move 语义：先完成复制，再解除源工作区关系 —— 会先保护最后一个管理员、检查部门负责人引用，
    然后删除源 UserRole、针对该用户的 ResourceGrant、成员资料与成员关系。
    副作用：写目标工作区成员数据（必要时先 ensure_system_roles 初始化内置角色），并在源/目标
    两个工作区分别写 workspace.member_copy|move 与 workspace.member_received 审计后提交。
    """
    require_permission(ctx, "system.workspaces.manage")
    _organization_admin(ctx, ctx.workspace.organization_id)
    source_id = ctx.workspace_id
    target, target_lifecycle = _workspace_target(ctx, payload.target_workspace_id)
    if target.id == source_id:
        raise ValidationError("目标工作区不能是当前工作区")
    if target_lifecycle.status != "active":
        raise ConflictError("不能向已归档工作区调动成员")
    source_member = ctx.db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == source_id, WorkspaceMember.user_id == payload.user_id,
    ))
    if source_member is None:
        raise NotFoundError("成员不属于当前工作区")
    if ctx.db.scalar(select(WorkspaceMember.id).where(
        WorkspaceMember.workspace_id == target.id, WorkspaceMember.user_id == payload.user_id,
    )):
        raise ConflictError("成员已经属于目标工作区")
    ensure_system_roles(ctx.db, target.id)
    role = ctx.db.scalar(select(Role).where(
        Role.workspace_id == target.id, Role.code == payload.target_role_code, Role.enabled.is_(True),
    ))
    if role is None:
        raise ValidationError("目标工作区角色不存在或已停用")
    source_profile = ctx.db.scalar(select(MemberProfile).where(
        MemberProfile.workspace_id == source_id, MemberProfile.user_id == payload.user_id,
    ))
    now = datetime.utcnow()
    target_member = WorkspaceMember(
        id=new_id(), workspace_id=target.id, user_id=payload.user_id,
        role="admin" if role.code == "workspace_admin" else "member", created_at=now,
    )
    target_profile = MemberProfile(
        id=new_id(), workspace_id=target.id, user_id=payload.user_id,
        department_id=None, position_ids=[],
        employee_code=source_profile.employee_code if source_profile else "",
        email=source_profile.email if source_profile else "",
        phone=source_profile.phone if source_profile else "",
        roles_initialized=True, created_at=now, updated_at=now,
    )
    ctx.db.add_all([
        target_member, target_profile,
        UserRole(id=new_id(), workspace_id=target.id, user_id=payload.user_id,
                 role_id=role.id, created_at=now, updated_at=now),
    ])
    if payload.mode == "move":
        protect_last_administrator(ctx, payload.user_id)
        if ctx.db.scalar(select(Department.id).where(
            Department.workspace_id == source_id, Department.leader_user_id == payload.user_id,
        )):
            raise ConflictError("该成员仍是部门负责人，请先调整负责人")
        ctx.db.execute(delete(UserRole).where(
            UserRole.workspace_id == source_id, UserRole.user_id == payload.user_id,
        ))
        ctx.db.execute(delete(ResourceGrant).where(
            ResourceGrant.workspace_id == source_id,
            ResourceGrant.subject_type == "user", ResourceGrant.subject_id == payload.user_id,
        ))
        if source_profile:
            ctx.db.delete(source_profile)
        ctx.db.delete(source_member)
    audit(ctx.db, workspace_id=source_id, actor_user_id=ctx.user_id,
          action=f"workspace.member_{payload.mode}", resource_type="user", resource_id=payload.user_id,
          metadata={"target_workspace_id": target.id, "role_code": role.code})
    audit(ctx.db, workspace_id=target.id, actor_user_id=ctx.user_id,
          action="workspace.member_received", resource_type="user", resource_id=payload.user_id,
          metadata={"source_workspace_id": source_id, "mode": payload.mode, "role_code": role.code})
    ctx.db.commit()
    return {
        "ok": True, "user_id": payload.user_id, "source_workspace_id": source_id,
        "target_workspace_id": target.id, "mode": payload.mode,
        "message": "成员已复制到目标工作区" if payload.mode == "copy" else "成员已调动到目标工作区",
    }
