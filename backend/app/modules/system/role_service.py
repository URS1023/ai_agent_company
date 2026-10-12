"""Role lifecycle, delegation bounds and effective authorization inspection.

职责：角色的增删改查、权限目录说明、以及“某用户当前实际拥有什么权限”的预览。

位置与依赖：对外路由前缀 /api/v1/system（router.py）——
GET/POST /roles、GET/PATCH/DELETE /roles/{id}、GET /permissions、GET /users/{id}/permissions。
权限编码与数据范围目录来自 permissions.py，越权与范围判定复用 common.authorization.access_policy，
事务与乐观锁复用 service_helpers。

关键约束：
- 委派边界（validate_role_policy）：非管理员不能授予自己不具备的动作，也不能把数据范围放宽到
  自己无权委派的级别；部门范围只能引用本工作区的部门。
- 内置角色（builtin=True）不可删除，workspace_admin 的权限与状态不可修改。
- 删除角色要求“无绑定用户、无资源授权”，授权历史靠审计表留痕，不靠角色行。
- 权限目录接口会把每个动作标注 grantable（当前操作人能否授予），前端据此禁用选项。
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.common.authorization import access_policy, require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, PermissionErrorEx, ValidationError
from app.models import User
from app.modules.system.models import Department, ResourceGrant, Role, UserRole
from app.modules.system.schemas import RoleCreate, RoleOut, RoleUpdate
from app.modules.system.permissions import ALL_PERMISSIONS, DATA_SCOPES, PERMISSION_CATALOG
from app.modules.system.service_helpers import (
    check_version, commit_change, flush_changes, lock_workspace, member_record, patch_record, scoped_record,
)


def validate_role_policy(ctx: AuthContext, permissions: list[str], scope: str, departments: list[str]) -> None:
    """Reject unknown actions, foreign departments and privilege escalation.

    用途：创建/修改角色（以及给用户分配角色）时的统一策略校验，是“权限不能被放大”的落点。
    参数：permissions 待授予的动作编码列表；scope 数据范围（DATA_SCOPES 之一）；
    departments custom 范围引用的部门标识列表。
    返回：无。异常：ValidationError（未登记编码、范围非法、custom 未选部门、部门越界）；
    PermissionErrorEx（授予了自己没有的动作，或把范围放宽到自己无权委派的级别）。
    判定规则（管理员 policy.administrator 直接放行）：
    1. permissions ⊆ ALL_PERMISSIONS；
    2. scope ∈ DATA_SCOPES；scope=custom 时至少一个部门，且每个部门都属于当前工作区；
    3. permissions ⊆ 操作人自身权限集合；
    4. scope != "self" 时，操作人必须在每个相关动作上拥有 data_scope="workspace" 的角色，
       即“只能委派自己不高于自身的数据范围”。
    副作用：无（只读校验）。调用约束：必须在写库之前调用；数据范围跟着角色走，因此校验的是
    角色整体而不是单个权限。
    """
    unknown = set(permissions) - ALL_PERMISSIONS
    if unknown:
        raise ValidationError("包含未登记的权限编码", invalid_permissions=sorted(unknown))
    if scope not in DATA_SCOPES:
        raise ValidationError("不支持的数据权限范围")
    if scope == "custom" and not departments:
        raise ValidationError("指定部门范围至少选择一个部门")
    for department_id in departments:
        scoped_record(ctx, Department, department_id)
    policy = access_policy(ctx)
    if policy.administrator:
        return
    if not set(permissions).issubset(policy.permissions):
        raise PermissionErrorEx("不能授予自己不具备的操作权限")
    for permission in permissions:
        actor_roles = [role for role in policy.roles if permission in role.permissions]
        if scope != "self" and not any(role.data_scope == "workspace" for role in actor_roles):
            raise PermissionErrorEx("不能扩大自己无权委派的数据范围")


def role_out(ctx: AuthContext, role: Role) -> dict:
    """Serialize a role plus current member count without leaking identities.

    用途：角色列表/详情/创建/修改的统一输出。
    返回：RoleOut 字段 + member_count（按本工作区统计的绑定用户数，用于提示“角色仍关联用户”）。
    异常：无。权限：本函数不校验，调用方需已通过 system.roles.view 或 system.roles.manage。
    副作用：只读（一次 count 查询）。
    """
    output = RoleOut.model_validate(role).model_dump()
    output["member_count"] = ctx.db.scalar(select(func.count()).select_from(UserRole).where(
        UserRole.workspace_id == ctx.workspace_id, UserRole.role_id == role.id,
    ))
    return output


def _permission_snapshot(role: Role) -> dict:
    """Stable role state retained in permission history even after deletion.

    用途：角色变更前后写入审计的脱敏快照（before/after），字段集合保持稳定以便做差异展示。
    返回：label/code/name/permissions/data_scope/department_ids/enabled 的字典。
    注意：权限与部门都排序后输出，避免同一集合因顺序不同被误判为“发生变更”。
    副作用：只读。
    """
    return {
        "label": role.name,
        "code": role.code,
        "name": role.name,
        "permissions": sorted(role.permissions),
        "data_scope": role.data_scope,
        "department_ids": sorted(role.department_ids),
        "enabled": role.enabled,
    }


def list_roles(ctx: AuthContext, page: int, page_size: int, query: str = "") -> dict:
    """List roles only in the selected workspace.

    用途：GET /api/v1/system/roles（需要 system.roles.view）。
    参数：page/page_size 分页；query 按角色名称模糊匹配（autoescape 防通配注入）。
    返回：{items, total, page, page_size}；total 为本工作区角色数（角色不按人员范围裁剪）。
    异常：PermissionErrorEx（缺少查看权限）。副作用：只读；items 会逐条统计 member_count。
    排序：按 created_at + id 保证分页稳定（相同时间戳时用 id 兜底）。
    """
    require_permission(ctx, "system.roles.view")
    statement = select(Role).where(Role.workspace_id == ctx.workspace_id)
    if query.strip():
        statement = statement.where(Role.name.contains(query.strip(), autoescape=True))
    total = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = ctx.db.scalars(statement.order_by(Role.created_at, Role.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [role_out(ctx, role) for role in rows], "total": total, "page": page, "page_size": page_size}


def create_role(ctx: AuthContext, payload: RoleCreate) -> dict:
    """Create a finite, validated action grant.

    用途：POST /api/v1/system/roles（需要 system.roles.manage）。
    参数：ctx；payload 为 RoleCreate。返回：role_out（含 member_count=0）。
    异常：ValidationError（未登记权限、范围非法、名称/编码空白）；PermissionErrorEx（越权授予）；
    ConflictError（编码重复）。
    副作用：写 system_roles 一行，并写 role.create 审计（after 快照）；权限与部门集合都会去重排序
    后存储，保证同一集合只有一种持久化表示（否则审计差异会误报变更）。
    """
    require_permission(ctx, "system.roles.manage")
    validate_role_policy(ctx, payload.permissions, payload.data_scope, payload.department_ids)
    record = Role(workspace_id=ctx.workspace_id)
    patch_record(record, payload.model_dump())
    record.permissions = sorted(set(payload.permissions))
    record.department_ids = sorted(set(payload.department_ids))
    ctx.db.add(record)
    flush_changes(ctx)
    after = _permission_snapshot(record)
    commit_change(ctx, "role.create", "role", record.id, {
        "before": {}, "after": after, "source": "manual", "label": after["label"],
    })
    return role_out(ctx, record)


def update_role(ctx: AuthContext, role_id: str, payload: RoleUpdate) -> dict:
    """Update one role atomically with version and delegation checks.

    用途：PATCH /api/v1/system/roles/{id}（需要 system.roles.manage）。
    参数：ctx；role_id；payload 为 RoleUpdate（version 必填）。
    返回：更新后的 role_out。
    异常：NotFoundError（角色不属于本工作区）；ValidationError（内置管理员角色被改权限/状态、
    版本或范围非法、权限/范围为 null）；PermissionErrorEx（越权授予）；ConflictError（版本冲突）。
    副作用：加工作区行锁后写 system_roles，并写 role.update 审计（fields + before/after 快照）；
    修改会立即影响所有绑定用户的授权（access_policy 每次请求实时读取）。
    """
    require_permission(ctx, "system.roles.manage")
    lock_workspace(ctx)
    record = scoped_record(ctx, Role, role_id)
    before = _permission_snapshot(record)
    check_version(record, payload.version)
    values = payload.model_dump(exclude_unset=True)
    if record.code == "workspace_admin" and set(values) - {"version", "name", "description"}:
        raise ValidationError("内置管理员角色的权限与状态不可修改；请建立自定义角色")
    permissions = values.get("permissions", record.permissions)
    scope = values.get("data_scope", record.data_scope)
    departments = values.get("department_ids", record.department_ids)
    if permissions is None or scope is None or departments is None:
        raise ValidationError("权限列表和数据范围不能为 null")
    validate_role_policy(ctx, permissions, scope, departments)
    patch_record(record, values)
    record.permissions = sorted(set(permissions))
    record.department_ids = sorted(set(departments))
    after = _permission_snapshot(record)
    commit_change(ctx, "role.update", "role", record.id, {
        "fields": sorted(key for key in values if key != "version"),
        "before": before,
        "after": after,
        "source": "manual",
        "label": after["label"],
    })
    return role_out(ctx, record)


def delete_role(ctx: AuthContext, role_id: str) -> None:
    """Delete only unassigned custom roles; keep authorization history intact.

    用途：DELETE /api/v1/system/roles/{id}（需要 system.roles.manage）。
    参数：ctx；role_id。返回：无。
    异常：NotFoundError（角色不属于本工作区）；ConflictError（内置角色、仍有绑定用户、
    或仍存在以本角色为对象的资源授权）。
    副作用：删除 system_roles 行并写 role.delete 审计（保留 before 快照，after 为空）；
    删除不可回滚，因此要求先解除所有引用。
    """
    require_permission(ctx, "system.roles.manage")
    lock_workspace(ctx)
    record = scoped_record(ctx, Role, role_id)
    before = _permission_snapshot(record)
    if record.builtin:
        raise ConflictError("内置角色不能删除，可复制为自定义角色后调整")
    if ctx.db.scalar(select(UserRole.id).where(UserRole.role_id == role_id)):
        raise ConflictError("角色仍关联用户，请先调整用户角色")
    if ctx.db.scalar(select(ResourceGrant.id).where(
        ResourceGrant.workspace_id == ctx.workspace_id,
        ResourceGrant.subject_type == "role", ResourceGrant.subject_id == role_id,
    )):
        raise ConflictError("角色仍存在资源授权，请先撤销授权")
    ctx.db.delete(record)
    commit_change(ctx, "role.delete", "role", role_id, {
        "before": before, "after": {}, "source": "manual", "label": before["label"],
    })


def permission_catalog(ctx: AuthContext) -> dict:
    """Explain every action code and the actor's delegation boundary.

    用途：GET /api/v1/system/permissions（需要 system.permissions.view），给角色编辑页提供
    可勾选的完整动作目录与数据范围说明。
    返回：{items: [动作项 + grantable], data_scopes: [五个范围码与中文名]}。
    异常：PermissionErrorEx（缺少查看权限）。
    权限语义：grantable 表示“当前操作人自己是否拥有该动作”，前端据此禁用不能委派的选项；
    真正的拒止仍由 validate_role_policy 在提交时执行（前端限制只是提示）。
    副作用：只读。
    """
    require_permission(ctx, "system.permissions.view")
    policy = access_policy(ctx)
    return {
        "items": [dict(item, grantable=item["code"] in policy.permissions) for item in PERMISSION_CATALOG.values()],
        "data_scopes": [
            {"code": "self", "name": "本人数据"}, {"code": "department", "name": "本部门"},
            {"code": "department_tree", "name": "本部门及下级"}, {"code": "custom", "name": "指定部门"},
            {"code": "workspace", "name": "整个工作区"},
        ],
    }


def effective_user_permissions(ctx: AuthContext, user_id: str) -> dict:
    """Preview current effective grants, attached data scopes and source roles.

    用途：GET /api/v1/system/users/{id}/permissions（需要 system.permissions.view +
    该动作的人员数据范围），用于排障“某人为什么有/没有某个权限”。
    参数：ctx；user_id 目标成员。
    返回：{user_id, active, administrator, permissions, sources}；sources 列出每个来源角色的
    数据范围与权限，便于解释差异来源。
    异常：PermissionErrorEx（缺少查看权限或目标超范围）；NotFoundError（非本工作区成员）。
    副作用：只读。注意：账号被停用时 permissions 返回空列表，避免误导管理员以为停用用户仍有权限；
    这里用临时 AuthContext 复用 access_policy，因此不会污染当前请求的权限缓存。
    """
    require_permission(ctx, "system.permissions.view")
    from app.common.authorization import require_user_scope
    require_user_scope(ctx, "system.permissions.view", user_id)
    member = member_record(ctx, user_id)
    user = ctx.db.get(User, user_id)
    target = AuthContext(db=ctx.db, user=user, workspace=ctx.workspace, role=member.role, token="")
    policy = access_policy(target)
    return {
        "user_id": user_id, "active": user.status == "active", "administrator": policy.administrator,
        "permissions": sorted(policy.permissions) if user.status == "active" else [],
        "sources": [{"role_id": role.id, "name": role.name, "data_scope": role.data_scope,
                     "department_ids": role.department_ids, "permissions": role.permissions} for role in policy.roles],
    }
