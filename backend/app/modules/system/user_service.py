"""User administration over existing Cloud Brain identities and sessions.

职责：工作区成员（用户）的查询与全生命周期管理 —— 列表/详情、新建账号、修改资料与状态、
分配角色、移除成员、重置密码、本人资料自助修改。

位置与依赖：
- 对外路由（前缀 /api/v1/system，见 router.py）：GET /users、POST /users、GET /users/{id}、
  PATCH /users/{id}、DELETE /users/{id}、POST /users/{id}/password-reset、
  GET /users/{id}/permissions（该接口在 router 中调用本模块的 user_out/authorization_snapshot）；
  /organization 与本人资料接口也读取本模块返回值。
- 依赖共享身份表（User/AuthIdentity/LocalCredential）与会话表（SessionToken），本模块只增删
  “工作区内的关系”（WorkspaceMember/MemberProfile/UserRole），不改动跨工作区的登录名。
- 事务与并发：写操作统一走 service_helpers（check_version 乐观锁、patch_record 落值、
  protect_last_administrator 保住最后一个管理员、commit_change 提交并写审计）。

关键约束：
- 权限：读列表需要 system.users.view，管理需要 system.users.manage，分配角色额外需要
  system.users.assign_roles，重置密码需要 system.users.reset_password；同时按数据范围
  （allowed_owner_ids / require_user_scope）限制能操作哪些人。
- 跨工作区保护：display_name/status/密码属于共享账号级字段，必须通过
  require_global_account_control；停用账号还会逐工作区校验“不能停掉最后一个管理员”。
- 密码只以哈希形式写入 LocalCredential，接口不返回任何明文或哈希；新建/重置后一律
  must_change_password=True。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select

from app.common.authorization import (
    access_policy, allowed_department_ids, allowed_owner_ids, require_permission,
    require_user_scope,
)
from app.common.deps import AuthContext
from app.errors import ConflictError, PermissionErrorEx, ValidationError
from app.ids import new_id
from app.models import AuthIdentity, LocalCredential, Organization, OrganizationMember, SessionToken, User, WorkspaceMember
from app.modules.system.models import Department, MemberProfile, Position, Role, UserRole
from app.modules.system.role_service import validate_role_policy
from app.modules.system.schemas import PasswordReset, SelfProfileUpdate, UserCreate, UserUpdate
from app.modules.system.service_helpers import (
    check_version, commit_change, flush_changes, lock_workspace, member_record, patch_record,
    protect_last_administrator, require_global_account_control, scoped_record,
)
from app.security import hash_password


def _profile(ctx: AuthContext, user_id: str) -> MemberProfile:
    """取当前工作区的成员资料。

    参数：ctx 已鉴权上下文；user_id 目标用户标识。返回：MemberProfile（本工作区唯一一条）。
    异常：ConflictError —— 资料不存在，说明数据库迁移未完成或成员被手工删除，提示而不是静默补建。
    副作用：无。调用约束：本函数不校验权限，调用方需先确认目标在本工作区内可管理。
    """
    profile = ctx.db.scalar(select(MemberProfile).where(
        MemberProfile.workspace_id == ctx.workspace_id, MemberProfile.user_id == user_id,
    ))
    if profile is None:
        raise ConflictError("成员资料尚未初始化，请完成数据库迁移后重试")
    return profile


def _validate_assignment(ctx: AuthContext, role_ids: list[str], department_id: str | None, position_ids: list[str]) -> list[Role]:
    """校验“把某人分配到这些角色/部门/岗位”是否合法，并返回角色实体。

    参数：role_ids 角色标识（会先去重）；department_id 单个部门；position_ids 岗位标识列表。
    返回：去重排序后的 Role 列表（顺序与入参 sorted(set(...)) 一致）。
    异常：ValidationError —— 角色已停用、部门/岗位已停用或不存在；
    PermissionErrorEx —— 试图分配 workspace_admin 但操作人不是管理员；
    以及 validate_role_policy 抛出的越权授予错误。
    权限与副作用：只读校验，不写库、不分配。调用约束：必须先于 _set_roles/落库调用，
    这样“先校验后写入”的批量流程（user_bulk）才能在无副作用阶段发现全部问题。
    """
    roles = []
    for role_id in sorted(set(role_ids)):
        role = scoped_record(ctx, Role, role_id)
        if not role.enabled:
            raise ValidationError("不能分配已停用的角色")
        validate_role_policy(ctx, role.permissions, role.data_scope, role.department_ids)
        if role.code == "workspace_admin" and not access_policy(ctx).administrator:
            raise PermissionErrorEx("只有工作区管理员可以分配管理员角色")
        roles.append(role)
    if department_id and not scoped_record(ctx, Department, department_id).enabled:
        raise ValidationError("不能选择已停用的部门")
    for position_id in set(position_ids):
        if not scoped_record(ctx, Position, position_id).enabled:
            raise ValidationError("不能选择已停用的岗位")
    return roles


def _set_roles(ctx: AuthContext, user_id: str, member: WorkspaceMember, roles: list[Role]) -> None:
    """整体替换成员的角色绑定，并同步旧版兼容字段与初始化标记。

    参数：ctx；user_id 目标用户；member 其成员记录；roles 已通过 _validate_assignment 的角色列表。
    返回：无。异常：PermissionErrorEx（缺少 system.users.assign_roles）；
    ConflictError（撤销管理员会导致工作区没有可用管理员，来自 protect_last_administrator）。
    权限：必须持有 system.users.assign_roles —— 移除旧管理员角色会降权，属于敏感动作。
    副作用：删除并重建该用户的 UserRole 行；改写 WorkspaceMember.role（仅 admin/member 两态，
    供旧版逻辑与列表展示）；把 MemberProfile.roles_initialized 置 True，表示“空角色=无授权”，
    不再回退到隐式身份。调用约束：不提交事务，需由调用方用 commit_change 收尾。
    """
    require_permission(ctx, "system.users.assign_roles")
    new_admin = any(role.code == "workspace_admin" for role in roles)
    if member.role == "admin" and not new_admin:
        protect_last_administrator(ctx, user_id)
    member.role = "admin" if new_admin else "member"
    ctx.db.execute(delete(UserRole).where(UserRole.workspace_id == ctx.workspace_id, UserRole.user_id == user_id))
    for role in roles:
        ctx.db.add(UserRole(workspace_id=ctx.workspace_id, user_id=user_id, role_id=role.id))
    profile = _profile(ctx, user_id)
    profile.roles_initialized = True
    profile.updated_at = datetime.utcnow()


def user_out(ctx: AuthContext, user_id: str) -> dict:
    """Serialize workspace profile and role names, never hashes or session tokens.

    用途：把成员渲染成前端/审计共用的用户结构（list_users、create_user、update_user、
    GET /users/{id} 与 GET /users/{id}/permissions 都复用它）。
    参数：ctx；user_id 目标用户（须为本工作区成员）。
    返回：字典，含 username/display_name/status/workspace_role/role_ids/role_names/部门/岗位/
    联系方式/version/时间戳/must_change_password。
    异常：NotFoundError（非本工作区成员）、ConflictError（成员资料未初始化）。
    权限：本函数自身不校验权限，调用方需保证已通过 require_permission 与 require_user_scope；
    因此不要把它直接暴露成“按 id 返回任意用户”的接口。
    副作用：只读。注意两个约定：role_names 会为停用角色追加“（停用）”后缀便于前端提示；
    updated_at 取 users 与 MemberProfile 两者的较大值，避免只改资料时列表排序不更新。
    """
    member = member_record(ctx, user_id)
    user = ctx.db.get(User, user_id)
    profile = _profile(ctx, user_id)
    identity = ctx.db.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user_id, AuthIdentity.provider == "local"))
    credential = ctx.db.scalar(select(LocalCredential).where(LocalCredential.auth_identity_id == identity.id)) if identity else None
    roles = ctx.db.scalars(select(Role).join(UserRole, UserRole.role_id == Role.id).where(
        UserRole.workspace_id == ctx.workspace_id, UserRole.user_id == user_id, Role.workspace_id == ctx.workspace_id,
    )).all()
    department = ctx.db.get(Department, profile.department_id) if profile.department_id else None
    return {
        "id": user.id, "username": identity.username_normalized if identity else "",
        "display_name": user.display_name, "status": user.status, "workspace_role": member.role,
        "role_ids": [role.id for role in roles], "role_names": [role.name + ("（停用）" if not role.enabled else "") for role in roles],
        "department_id": profile.department_id, "department_name": department.name if department else "",
        "position_ids": profile.position_ids, "employee_code": profile.employee_code,
        "email": profile.email, "phone": profile.phone, "version": profile.version,
        "created_at": user.created_at, "updated_at": max(user.updated_at, profile.updated_at),
        "must_change_password": bool(credential and credential.must_change_password),
    }


def authorization_snapshot(ctx: AuthContext, user_id: str) -> dict:
    """Return a stable, redacted snapshot suitable for permission-change history.

    用途：在权限变更前后各取一次，写入审计 metadata 的 before/after，供权限变更历史展示。
    返回：仅含 label/username/display_name/status/department_id/role_ids/role_codes 的字典 ——
    刻意不含联系方式与令牌，保证历史记录长期留痕也不泄露个人信息。
    异常：同 user_out。权限：自身不校验，调用方负责；副作用：只读。
    调用约束：必须“改前一次、改后一次”成对采集，字段集合稳定才能做差异对比（permission_history）。
    """
    output = user_out(ctx, user_id)
    roles = ctx.db.scalars(select(Role).join(UserRole, UserRole.role_id == Role.id).where(
        UserRole.workspace_id == ctx.workspace_id,
        UserRole.user_id == user_id,
        Role.workspace_id == ctx.workspace_id,
    ).order_by(Role.code)).all()
    return {
        "label": f"{output['display_name']}（{output['username']}）",
        "username": output["username"],
        "display_name": output["display_name"],
        "status": output["status"],
        "department_id": output["department_id"],
        "role_ids": [role.id for role in roles],
        "role_codes": [role.code for role in roles],
    }


def self_profile(ctx: AuthContext) -> dict:
    """Return the current member's normal profile without requiring admin permissions.

    用途：支撑“我的资料”页面（GET 个人资料）。参数：ctx（用户取自登录态，不接受他人 id）。
    返回：user_out 的全部字段 + organization_name/workspace_name/position_names。
    异常：NotFoundError/ConflictError（同 user_out）。
    权限：无需任何 system.* 权限，任何已登录成员都可查看自己的资料。
    副作用：只读；岗位名称按 position_ids 逐个在本工作区查找，找不到的岗位会被跳过
    （岗位被删除后不阻塞资料展示）。
    """
    output = user_out(ctx, ctx.user_id)
    positions = []
    for position_id in output["position_ids"]:
        position = ctx.db.scalar(select(Position).where(
            Position.id == position_id, Position.workspace_id == ctx.workspace_id,
        ))
        if position is not None:
            positions.append(position.name)
    organization = ctx.db.get(Organization, ctx.workspace.organization_id)
    return {
        **output,
        "organization_name": organization.name if organization else "",
        "workspace_name": ctx.workspace.name,
        "position_names": positions,
    }


def update_self_profile(ctx: AuthContext, payload: SelfProfileUpdate) -> dict:
    """Allow a member to maintain only their own display and contact fields.

    用途：成员自助修改显示姓名与当前工作区联系方式（部门、岗位、角色、状态均不可自改）。
    参数：ctx；payload 为 SelfProfileUpdate（version 必填）。
    返回：更新后的 self_profile 结构。异常：ValidationError（版本已变、姓名为空白、
    email/phone 显式传 null）；ConflictError（并发提交时乐观锁冲突）。
    权限：无需管理权限，但只能改自己（用户标识固定取 ctx.user_id）。
    副作用：写 users.display_name 与 MemberProfile.email/phone，并写入 user.profile_update
    审计（含变更字段与前后值，其中 label 标记“（本人）”以便与管理员操作区分）。
    """
    user = ctx.user
    profile = _profile(ctx, ctx.user_id)
    check_version(profile, payload.version)
    values = payload.model_dump(exclude_unset=True)
    before = {
        "display_name": user.display_name,
        "email": profile.email,
        "phone": profile.phone,
    }
    if "display_name" in values:
        display_name = values["display_name"].strip()
        if not display_name:
            raise ValidationError("显示姓名不能为空白")
        user.display_name = display_name
        user.updated_at = datetime.utcnow()
    for key in ("email", "phone"):
        if key in values:
            value = values[key]
            if value is None:
                raise ValidationError(f"字段 {key} 不能为 null")
            setattr(profile, key, value.strip())
    profile.updated_at = datetime.utcnow()
    after = {
        "display_name": user.display_name,
        "email": profile.email,
        "phone": profile.phone,
    }
    commit_change(ctx, "user.profile_update", "user", ctx.user_id, {
        "fields": sorted(key for key in after if before[key] != after[key]),
        "before": before,
        "after": after,
        "source": "manual",
        "label": f"{user.display_name}（本人）",
    })
    return self_profile(ctx)


def list_users(ctx: AuthContext, page: int, page_size: int, query: str = "", status: str | None = None, department_id: str | None = None) -> dict:
    """Paginated user search, restricted to the selected workspace.

    用途：GET /api/v1/system/users 的实现（管理员用户列表）。
    参数：page/page_size 分页（page_size 由路由限制 ≤100）；query 模糊匹配显示姓名与
    规范化登录名（大小写不敏感、autoescape 防 LIKE 注入）；status 精确过滤；
    department_id 只返回该部门成员（会先做工作区范围校验）。
    返回：{items, total, page, page_size}，total 是“当前操作人数据范围内”的计数。
    异常：PermissionErrorEx（缺少 system.users.view）、NotFoundError（department_id 越界）。
    权限与数据范围：先 require_permission，再用 allowed_owner_ids(ctx, "system.users.view")
    把可见范围收敛到本人/本部门/指定部门（管理员不受限），越界人员不会出现在列表或计数中。
    副作用：只读；对每个命中用户调用 user_out（会逐条查询角色与部门，注意大页开销）。
    """
    require_permission(ctx, "system.users.view")
    statement = select(User.id).join(WorkspaceMember, WorkspaceMember.user_id == User.id).outerjoin(
        AuthIdentity, (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
    ).where(WorkspaceMember.workspace_id == ctx.workspace_id)
    owners = allowed_owner_ids(ctx, "system.users.view")
    if owners is not None:
        statement = statement.where(User.id.in_(owners))
    if query.strip():
        statement = statement.where(or_(
            User.display_name.contains(query.strip(), autoescape=True),
            AuthIdentity.username_normalized.contains(query.strip().lower(), autoescape=True),
        ))
    if status:
        statement = statement.where(User.status == status)
    if department_id:
        scoped_record(ctx, Department, department_id)
        statement = statement.join(MemberProfile, (MemberProfile.user_id == User.id) & (
            MemberProfile.workspace_id == ctx.workspace_id
        )).where(MemberProfile.department_id == department_id)
    statement = statement.distinct()
    total = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
    ids = ctx.db.scalars(statement.order_by(User.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [user_out(ctx, user_id) for user_id in ids], "total": total, "page": page, "page_size": page_size}


def _validate_password(password: str) -> None:
    """校验临时密码强度：至少 10 位且同时包含字母与数字。

    异常：ValidationError。副作用：无。调用约束：单用户新建、密码重置与批量导入都走这里，
    保证三处强度要求一致；本函数不做历史密码比对（当前未实现密码历史）。
    """
    if len(password) < 10 or not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise ValidationError("密码至少 10 位，并同时包含字母和数字")


def prepare_user_create(ctx: AuthContext, payload: UserCreate) -> list[Role]:
    """Validate a user creation without mutating state; shared by single and bulk flows.

    用途：新建用户前的“无副作用”校验阶段，供 create_user 与 user_bulk.validate_import 共用，
    保证批量导入与单条创建的业务规则完全一致（导入必须先整体校验、再统一提交）。
    参数：ctx；payload 为 UserCreate。
    返回：待绑定的 Role 列表（交给 create_user_records 落库）。
    异常：PermissionErrorEx（缺少 system.users.manage / system.users.assign_roles，或目标部门
    超出操作人可管理范围）；ValidationError（密码强度不足）；ConflictError（登录名已存在，大小写不敏感）。
    权限：需同时持有 manage 与 assign_roles 两个动作，并按两者的数据范围检查部门。
    副作用：无写库动作（仅查询），可在事务内被安全重放。注意：登录名在比较前统一 strip+lower。
    """
    require_permission(ctx, "system.users.manage")
    require_permission(ctx, "system.users.assign_roles")
    for permission in ("system.users.manage", "system.users.assign_roles"):
        departments = allowed_department_ids(ctx, permission)
        if departments is not None and payload.department_id not in departments:
            raise PermissionErrorEx("新用户必须分配到当前操作允许管理的部门", permission=permission)
    _validate_password(payload.password)
    username = payload.username.strip().lower()
    if ctx.db.scalar(select(AuthIdentity.id).where(AuthIdentity.provider == "local", AuthIdentity.username_normalized == username)):
        raise ConflictError("该登录名已存在")
    return _validate_assignment(ctx, payload.role_ids, payload.department_id, payload.position_ids)


def create_user_records(ctx: AuthContext, payload: UserCreate, roles: list[Role]) -> str:
    """Stage a validated user in the current transaction without committing it.

    用途：真正创建账号的落库阶段（登录身份 + 组织/工作区成员关系 + 成员资料 + 角色绑定），
    刻意不提交事务，以便批量导入把多行放进同一个事务。
    参数：ctx；payload 已通过 prepare_user_create 校验的 UserCreate；roles 其返回的角色列表。
    返回：新用户 id。异常：ValidationError（显示姓名为空白）；ConflictError（flush 时的唯一约束冲突）；
    PermissionErrorEx（_set_roles 内的分配权限检查）。
    权限：本函数不再重复校验 manage/assign_roles，由 prepare_user_create 负责。
    副作用：写入 users / AuthIdentity / LocalCredential（bcrypt 哈希，must_change_password=True，
    临时密码 72 小时过期）/ OrganizationMember / WorkspaceMember / MemberProfile / UserRole，
    并多次 flush 以拿到主键。调用约束：调用方必须自行 commit（单条用 commit_change，批量用整体提交）。
    """
    username = payload.username.strip().lower()
    now = datetime.utcnow()
    user = User(id=new_id(), display_name=payload.display_name.strip(), status="active", created_at=now, updated_at=now)
    if not user.display_name:
        raise ValidationError("显示姓名不能为空白")
    ctx.db.add(user)
    flush_changes(ctx)
    identity = AuthIdentity(id=new_id(), user_id=user.id, provider="local", username_normalized=username, created_at=now)
    ctx.db.add(identity)
    flush_changes(ctx)
    ctx.db.add(LocalCredential(
        id=new_id(), auth_identity_id=identity.id, password_hash=hash_password(payload.password),
        must_change_password=True, temporary_password_expires_at=now + timedelta(hours=72),
        credential_version=1, password_changed_at=now,
    ))
    ctx.db.add(OrganizationMember(id=new_id(), organization_id=ctx.workspace.organization_id, user_id=user.id, role="member", created_at=now))
    member = WorkspaceMember(id=new_id(), workspace_id=ctx.workspace_id, user_id=user.id, role="member", created_at=now)
    ctx.db.add(member)
    ctx.db.add(MemberProfile(
        workspace_id=ctx.workspace_id, user_id=user.id, department_id=payload.department_id,
        position_ids=sorted(set(payload.position_ids)), employee_code=payload.employee_code,
        email=payload.email, phone=payload.phone, roles_initialized=True,
    ))
    flush_changes(ctx)
    _set_roles(ctx, user.id, member, roles)
    return user.id


def create_user(ctx: AuthContext, payload: UserCreate) -> dict:
    """Create a login identity, membership, profile and role assignment atomically.

    用途：POST /api/v1/system/users —— 单条新建成员（需要 system.users.manage +
    system.users.assign_roles，部门需在操作人可管理范围内）。
    参数：ctx；payload 为 UserCreate。
    返回：新用户的 user_out 结构（不含密码）。异常：ValidationError/ConflictError/
    PermissionErrorEx（见 prepare_user_create 与 create_user_records）。
    副作用：先 lock_workspace 串行化同工作区的新建/授权，再写入账号、成员关系与角色，
    最后 commit_change("user.create") 提交并写入含 after 快照的审计；bcrypt 哈希入库、
    首登强制改密。
    """
    lock_workspace(ctx)
    roles = prepare_user_create(ctx, payload)
    user_id = create_user_records(ctx, payload, roles)
    after = authorization_snapshot(ctx, user_id)
    commit_change(ctx, "user.create", "user", user_id, {
        "before": {}, "after": after, "source": "manual", "label": after["label"],
    })
    return user_out(ctx, user_id)


def prepare_user_update(ctx: AuthContext, user_id: str, payload: UserUpdate):
    """Validate a member update and return the records needed by the atomic apply step.

    用途：修改用户前的准备阶段（PATCH /api/v1/system/users/{id} 与批量导入共用），
    把“需要查库的校验”全部前置，返回给 apply_user_update 执行无校验的落库。
    参数：ctx；user_id 目标用户；payload 为 UserUpdate（version 必填）。
    返回：元组 (member, user, profile, values, roles)，values 是 exclude_unset 后的字段字典。
    异常：NotFoundError（非本工作区成员）；PermissionErrorEx（缺少 system.users.manage /
    assign_roles，或目标超出数据范围，或账号还属于其他工作区却要改全局字段）；
    ValidationError（角色列表为 null、角色/部门/岗位已停用等）；ConflictError（版本不一致、
    或停用会移除最后一个管理员）。
    权限：manage（并按 require_user_scope 限制人员范围）；改角色时额外要求 assign_roles；
    display_name/status 需要 require_global_account_control。
    副作用：无写入（只读校验 + 加锁由调用方负责）；但会临时切换 ctx.workspace 复查其他工作区
    的管理员保留情况，并在 finally 中还原，注意不要在期间发起与工作区无关的写操作。
    """
    require_permission(ctx, "system.users.manage")
    member = member_record(ctx, user_id)
    require_user_scope(ctx, "system.users.manage", user_id)
    user = ctx.db.get(User, user_id)
    profile = _profile(ctx, user_id)
    check_version(profile, payload.version)
    values = payload.model_dump(exclude_unset=True)
    if "department_id" in values and values["department_id"] != profile.department_id:
        departments = allowed_department_ids(ctx, "system.users.manage")
        if departments is not None and values["department_id"] not in departments:
            raise PermissionErrorEx("不能将用户调入当前操作无权管理的部门")
    if "display_name" in values or "status" in values:
        require_global_account_control(ctx, user_id)
    role_ids = values.get("role_ids")
    if "role_ids" in values and role_ids is None:
        raise ValidationError("角色列表不能为 null")
    roles = _validate_assignment(
        ctx, role_ids if role_ids is not None else [],
        values.get("department_id", profile.department_id), values.get("position_ids", profile.position_ids) or [],
    )
    if role_ids is not None:
        require_user_scope(ctx, "system.users.assign_roles", user_id)
    if values.get("status") == "disabled":
        protect_last_administrator(ctx, user_id)
        # Shared identities may have administrators in several workspaces.
        for workspace_id in sorted(set(ctx.db.scalars(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)))):
            original = ctx.workspace
            from app.models import Workspace
            ctx.workspace = ctx.db.get(Workspace, workspace_id)
            try:
                protect_last_administrator(ctx, user_id)
            finally:
                ctx.workspace = original
    return member, user, profile, values, roles


def apply_user_update(ctx: AuthContext, user_id: str, prepared) -> None:
    """Stage a previously validated update without committing the surrounding transaction.

    用途：执行已校验的修改（角色替换、停用撤销会话、账号字段与成员资料落值）。
    参数：ctx；user_id；prepared 为 prepare_user_update 的返回值。
    返回：无。异常：ValidationError（patch_record 拒绝的字段，如把非空列置 null）。
    权限：不再重复校验，命中前提是 prepare_user_update 已通过。
    副作用：写 UserRole/WorkspaceMember/User/ MemberProfile；status=disabled 时撤销该用户所有
    未撤销会话（SessionToken.revoked_at）。调用约束：不提交事务，由 update_user 或批量导入统一提交。
    """
    member, user, profile, values, roles = prepared
    role_ids = values.get("role_ids")
    if role_ids is not None:
        _set_roles(ctx, user_id, member, roles)
    if values.get("status") == "disabled":
        _revoke_sessions(ctx, user_id)
    for field in ("display_name", "status"):
        if field in values:
            patch_record(user, {field: values[field]})
    profile_values = {key: value for key, value in values.items() if key not in {"version", "role_ids", "display_name", "status"}}
    patch_record(profile, profile_values)
    profile.updated_at = datetime.utcnow()
    user.updated_at = datetime.utcnow()


def update_user(ctx: AuthContext, user_id: str, payload: UserUpdate) -> dict:
    """Edit workspace profile and authorized account fields with version checks.

    用途：PATCH /api/v1/system/users/{id} 的编排函数：加锁 → 取 before 快照 → 校验 →
    落库 → 取 after 快照 → 提交并审计。
    参数：ctx；user_id；payload 为 UserUpdate。返回：更新后的 user_out。
    异常：见 prepare_user_update / apply_user_update（版本冲突、越权、最后一个管理员保护等）。
    副作用：同 prepare/apply，另写入 user.update 审计（含 fields 与 before/after 快照）；
    注意 values 里可能包含 version，审计字段列表会剔除它。
    """
    lock_workspace(ctx)
    before = authorization_snapshot(ctx, user_id)
    prepared = prepare_user_update(ctx, user_id, payload)
    values = prepared[3]
    apply_user_update(ctx, user_id, prepared)
    after = authorization_snapshot(ctx, user_id)
    commit_change(ctx, "user.update", "user", user_id, {
        "fields": sorted(key for key in values if key != "version"),
        "before": before,
        "after": after,
        "source": "manual",
        "label": after["label"],
    })
    return user_out(ctx, user_id)


def _revoke_sessions(ctx: AuthContext, user_id: str) -> None:
    """撤销目标用户所有未撤销的会话（置 revoked_at，不删除记录）。

    副作用：改写 SessionToken.revoked_at。调用约束：属于“账号级”动作，调用方需先通过
    require_global_account_control；仅影响全局会话，不区分工作区（会话本身是全局的）。
    """
    for session in ctx.db.scalars(select(SessionToken).where(SessionToken.user_id == user_id, SessionToken.revoked_at.is_(None))).all():
        session.revoked_at = datetime.utcnow()


def reset_password(ctx: AuthContext, user_id: str, payload: PasswordReset) -> None:
    """Set an expiring one-time password; the user must change it before business use.

    用途：POST /api/v1/system/users/{id}/password-reset，管理员为用户签发临时密码。
    参数：ctx；user_id；payload 为 PasswordReset（新密码 + 有效期小时数，1–168）。
    返回：无。异常：PermissionErrorEx（缺少 system.users.reset_password、超出数据范围，
    或账号仍属于操作人无权管理的其他工作区）；ValidationError（密码强度不足、用户没有本地密码）。
    权限：system.users.reset_password + require_user_scope + require_global_account_control。
    副作用：更新 LocalCredential（重新哈希、credential_version+1、must_change_password=True、
    临时密码过期时间、清零失败次数与锁定）、撤销该用户全部会话，并写 user.password_reset 审计
    （只记动作，不记录密码内容）。
    """
    require_permission(ctx, "system.users.reset_password")
    member_record(ctx, user_id)
    require_user_scope(ctx, "system.users.reset_password", user_id)
    require_global_account_control(ctx, user_id)
    _validate_password(payload.new_password)
    identity = ctx.db.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user_id, AuthIdentity.provider == "local"))
    credential = ctx.db.scalar(select(LocalCredential).where(LocalCredential.auth_identity_id == identity.id)) if identity else None
    if credential is None:
        raise ValidationError("该用户没有本地密码，不能使用此操作")
    credential.password_hash = hash_password(payload.new_password)
    credential.credential_version += 1
    credential.must_change_password = True
    credential.temporary_password_expires_at = datetime.utcnow() + timedelta(hours=payload.expires_in_hours)
    credential.failed_attempt_count = 0
    credential.locked_until = None
    credential.password_changed_at = datetime.utcnow()
    _revoke_sessions(ctx, user_id)
    commit_change(ctx, "user.password_reset", "user", user_id)


def remove_user(ctx: AuthContext, user_id: str) -> None:
    """Remove current workspace membership while retaining the account and audit history.

    用途：DELETE /api/v1/system/users/{id} —— 只移除“本工作区成员关系”，全局账号与历史审计保留，
    同一账号在其他工作区的成员关系不受影响。
    参数：ctx；user_id。返回：无。
    异常：ConflictError（目标是最后一个启用中的管理员，或仍是某个部门的负责人 —— 需先改负责人）；
    PermissionErrorEx（缺少 manage/assign_roles 或超出数据范围）。
    权限：system.users.manage + system.users.assign_roles，两个动作各自都要通过人员范围校验。
    副作用：删除该工作区的 UserRole 与 MemberProfile、删除 WorkspaceMember，并写
    user.remove_from_workspace 审计（before 快照保留，after 为空对象）。
    """
    require_permission(ctx, "system.users.manage")
    require_permission(ctx, "system.users.assign_roles")
    require_user_scope(ctx, "system.users.manage", user_id)
    require_user_scope(ctx, "system.users.assign_roles", user_id)
    before = authorization_snapshot(ctx, user_id)
    protect_last_administrator(ctx, user_id)
    member = member_record(ctx, user_id)
    if ctx.db.scalar(select(Department.id).where(Department.workspace_id == ctx.workspace_id, Department.leader_user_id == user_id)):
        raise ConflictError("该用户仍为部门负责人，请先调整负责人")
    ctx.db.execute(delete(UserRole).where(UserRole.workspace_id == ctx.workspace_id, UserRole.user_id == user_id))
    ctx.db.delete(_profile(ctx, user_id))
    ctx.db.delete(member)
    commit_change(ctx, "user.remove_from_workspace", "user", user_id, {
        "before": before, "after": {}, "source": "manual", "label": before["label"],
    })
