"""Department and position management with reference and cycle validation.

职责：组织架构（部门树）与岗位的增删改查。部门决定“数据范围”能覆盖哪些人，岗位只是职责
标签（不带来任何权限），因此本模块的校验重点是树的完整性与引用完整性。

位置与依赖：对外路由前缀 /api/v1/system（router.py）—— GET/POST /departments、
GET/PATCH/DELETE /departments/{id}、GET/POST /positions、GET/PATCH/DELETE /positions/{id}。
依赖 service_helpers（乐观锁/审计/提交）与 MemberProfile（成员-部门/岗位的引用方）。

关键约束：
- 部门 parent_id 必须在同一工作区内逐级可达且不成环，上级必须启用；
- 部门被成员、下级部门或角色数据范围（Role.department_ids）引用时不允许删除；
- 岗位被任何成员资料引用时不允许删除；
- 负责人（leader_user_id）必须是本工作区成员。
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, ValidationError
from app.modules.system.models import Department, MemberProfile, Position
from app.modules.system.schemas import DepartmentCreate, DepartmentUpdate, PositionCreate, PositionUpdate
from app.modules.system.service_helpers import (
    check_version, commit_change, flush_changes, lock_workspace, member_record, patch_record, scoped_record,
)


def list_records(ctx: AuthContext, model, permission: str, page: int, page_size: int, query: str = "") -> dict:
    """List a bounded, workspace-isolated page of organization records.

    用途：部门与岗位列表接口的共用实现（model + permission 由路由层指定）。
    参数：ctx；model 为 Department 或 Position；permission 决定所需的查看权限；
    page/page_size 分页；query 按 name 模糊匹配（autoescape 防通配注入）。
    返回：{items, total, page, page_size}，只覆盖当前工作区。
    异常：PermissionErrorEx（缺少对应查看权限）。
    副作用：只读；排序 created_at + id 保证分页稳定。
    """
    require_permission(ctx, permission)
    statement = select(model).where(model.workspace_id == ctx.workspace_id)
    if query.strip():
        statement = statement.where(model.name.contains(query.strip(), autoescape=True))
    total = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
    items = ctx.db.scalars(statement.order_by(model.created_at, model.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _validate_department(ctx: AuthContext, parent_id: str | None, leader_id: str | None, record_id: str | None = None) -> None:
    """校验部门归属与树结构：负责人必须是本工作区成员，父链必须有效且不成环。

    参数：parent_id 目标上级；leader_id 负责人（可空）；record_id 被编辑部门自身（编辑时传入，
    用于把“自己”预先放入已访问集合，从而拒绝把自己挂到自己的下级）。
    异常：ValidationError —— 形成环（上级是自身或自己的下级）、上级已停用；
    NotFoundError —— 上级或成员不属于当前工作区（来自 scoped_record/member_record）。
    副作用：无。调用约束：创建与修改部门都必须调用；沿 parent_id 逐级向上遍历，任一级缺失即失败。
    """
    if leader_id:
        member_record(ctx, leader_id)
    visited = {record_id} if record_id else set()
    current = parent_id
    while current:
        if current in visited:
            raise ValidationError("上级部门不能是自身或自己的下级部门")
        visited.add(current)
        parent = scoped_record(ctx, Department, current)
        if not parent.enabled:
            raise ValidationError("不能选择已停用的上级部门")
        current = parent.parent_id


def create_department(ctx: AuthContext, payload: DepartmentCreate) -> Department:
    """Create a department after validating its complete ancestry.

    用途：POST /api/v1/system/departments（需要 system.departments.manage）。
    参数：ctx；payload 为 DepartmentCreate。返回：新建的 Department。
    异常：ValidationError/NotFoundError（父链或成员越界、字段空白）；ConflictError（编码重复）。
    副作用：加工作区行锁后写入部门行，并写 department.create 审计（不提交则不生效，
    此处由 commit_change 提交）。code 在工作区内唯一，创建后不可修改。
    """
    require_permission(ctx, "system.departments.manage")
    lock_workspace(ctx)
    _validate_department(ctx, payload.parent_id, payload.leader_user_id)
    record = Department(workspace_id=ctx.workspace_id)
    patch_record(record, payload.model_dump())
    ctx.db.add(record)
    flush_changes(ctx)
    commit_change(ctx, "department.create", "department", record.id)
    return record


def update_department(ctx: AuthContext, record_id: str, payload: DepartmentUpdate) -> Department:
    """Update an organization node without introducing a cycle or foreign parent.

    用途：PATCH /api/v1/system/departments/{id}（需要 system.departments.manage）。
    参数：ctx；record_id；payload 为 DepartmentUpdate（version 必填）。
    返回：更新后的 Department。
    异常：NotFoundError（部门不属于本工作区）；ValidationError（成环、上级停用、字段空白）；
    ConflictError（版本冲突）。
    副作用：加行锁、更新部门行并写 department.update 审计（记录变更字段名）；调整上级会改变
    department_tree 数据范围覆盖的成员集合，请确认后再改动。
    """
    require_permission(ctx, "system.departments.manage")
    lock_workspace(ctx)
    record = scoped_record(ctx, Department, record_id)
    check_version(record, payload.version)
    values = payload.model_dump(exclude_unset=True)
    _validate_department(ctx, values.get("parent_id", record.parent_id), values.get("leader_user_id", record.leader_user_id), record.id)
    patch_record(record, values)
    commit_change(ctx, "department.update", "department", record.id, {"fields": sorted(values)})
    return record


def delete_department(ctx: AuthContext, record_id: str) -> None:
    """Delete only an empty department; assigned people and children are preserved.

    用途：DELETE /api/v1/system/departments/{id}（需要 system.departments.manage）。
    参数：ctx；record_id。返回：无。
    异常：NotFoundError（部门不属于本工作区）；ConflictError（仍有下级部门、仍有成员、
    仍被角色的数据范围引用）。
    副作用：加行锁后删除部门行并写 department.delete 审计；不级联删除任何成员或子部门，
    因此必须先人工迁移，避免出现“无归属成员”的隐式越权。
    """
    require_permission(ctx, "system.departments.manage")
    lock_workspace(ctx)
    record = scoped_record(ctx, Department, record_id)
    child = ctx.db.scalar(select(Department.id).where(Department.parent_id == record.id))
    member = ctx.db.scalar(select(MemberProfile.id).where(MemberProfile.department_id == record.id))
    if child or member:
        raise ConflictError("请先迁移下级部门及成员，再删除此部门")
    from app.modules.system.models import Role
    if any(record.id in role.department_ids for role in ctx.db.scalars(select(Role).where(Role.workspace_id == ctx.workspace_id))):
        raise ConflictError("该部门仍被角色数据范围引用")
    ctx.db.delete(record)
    commit_change(ctx, "department.delete", "department", record_id)


def create_position(ctx: AuthContext, payload: PositionCreate) -> Position:
    """Create a position without granting any implicit authorization.

    用途：POST /api/v1/system/positions（需要 system.positions.manage）。
    参数：ctx；payload 为 PositionCreate。返回：新建的 Position。
    异常：ValidationError（字段空白等）；ConflictError（编码重复）。
    副作用：写入岗位行并写 position.create 审计。岗位与权限无关：分配岗位不会带来任何动作权限。
    """
    require_permission(ctx, "system.positions.manage")
    record = Position(workspace_id=ctx.workspace_id)
    patch_record(record, payload.model_dump())
    ctx.db.add(record)
    flush_changes(ctx)
    commit_change(ctx, "position.create", "position", record.id)
    return record


def update_position(ctx: AuthContext, record_id: str, payload: PositionUpdate) -> Position:
    """Update a versioned job position.

    用途：PATCH /api/v1/system/positions/{id}（需要 system.positions.manage）。
    参数：ctx；record_id；payload 为 PositionUpdate（version 必填）。返回：更新后的 Position。
    异常：NotFoundError（不属于本工作区）；ConflictError（版本冲突）；ValidationError（字段非法）。
    副作用：更新岗位行并写 position.update 审计；停用岗位不会自动从成员资料中移除引用，
    只是让新分配与批量导入无法再选择它。
    """
    require_permission(ctx, "system.positions.manage")
    record = scoped_record(ctx, Position, record_id)
    check_version(record, payload.version)
    patch_record(record, payload.model_dump(exclude_unset=True))
    commit_change(ctx, "position.update", "position", record.id)
    return record


def delete_position(ctx: AuthContext, record_id: str) -> None:
    """Remove only an unassigned position.

    用途：DELETE /api/v1/system/positions/{id}（需要 system.positions.manage）。
    参数：ctx；record_id。返回：无。
    异常：NotFoundError（不属于本工作区）；ConflictError（仍有成员资料引用该岗位）。
    副作用：加行锁后删除岗位行并写 position.delete 审计；成员资料中的岗位标识不会自动清理，
    所以必须先调整成员岗位。
    """
    require_permission(ctx, "system.positions.manage")
    lock_workspace(ctx)
    record = scoped_record(ctx, Position, record_id)
    profiles = ctx.db.scalars(select(MemberProfile).where(MemberProfile.workspace_id == ctx.workspace_id)).all()
    if any(record_id in profile.position_ids for profile in profiles):
        raise ConflictError("该岗位仍关联成员，请先调整成员岗位")
    ctx.db.delete(record)
    commit_change(ctx, "position.delete", "position", record_id)
