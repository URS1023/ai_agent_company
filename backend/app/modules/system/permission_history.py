"""Human-readable permission history built from immutable, redacted audit records.

职责：把审计表里的权限变更事件翻译成管理员能读懂的“权限变更历史”（谁、何时、对谁、改了什么）。

位置与依赖：对外路由前缀 /api/v1/system（router.py）—— GET /permissions/history，
需要 system.permissions.history（高影响操作码之一）。只读取 AuditLog，不写入任何数据；
审计本身由 user_service/role_service/admin_service 通过 service_helpers.commit_change 写入。

关键约束：
- 数据来源是审计而非当前数据：角色/用户删掉后历史仍可回放，展示的名称取自变更时的快照
  （metadata.label / before|after.label），缺失时才回退查询现存记录。
- 只展示 ACTION_LABELS 中登记的 action（其它审计事件不在此页暴露），并按操作人的数据范围过滤
  （allowed_owner_ids 为 None 时表示可见全工作区）。
- before/after 是脱敏快照，不含密码、令牌与联系方式。
"""
from __future__ import annotations

from sqlalchemy import String, cast, func, or_, select

from app.common.authorization import allowed_owner_ids, require_permission
from app.common.deps import AuthContext
from app.models import AuditLog, AuthIdentity, User
from app.modules.system.models import Role


# 允许在权限历史中展示的动作编码 → 中文名称。键集合同时是 SQL 过滤条件（in_），
# 因此“哪些事件能出现在历史里”由这张表决定；新增审计动作时需同步在这里登记，否则不会展示。
ACTION_LABELS = {
    "user.create": "创建并授权用户",
    "user.update": "修改用户权限",
    "user.remove_from_workspace": "移除工作区成员",
    "user.bulk_import": "批量导入用户",
    "role.create": "创建角色",
    "role.update": "修改角色权限",
    "role.delete": "删除角色",
    "resource.grant": "新增资源授权",
    "resource.revoke": "撤销资源授权",
}
# 权限历史支持的对象类型，与 schemas.PermissionHistoryTarget.type 的取值保持一致。
RESOURCE_TYPES = {"user", "role", "resource_grant"}


def _difference(before: list | None, after: list | None) -> tuple[list, list]:
    """比较前后两个列表，返回 (新增项, 移除项)，均排序以保证展示顺序稳定。

    语义按集合处理：同一元素位置变化不算变更（快照里的列表在写入时已排序）。
    """
    left, right = set(before or []), set(after or [])
    return sorted(right - left), sorted(left - right)


def _summary(action: str, before: dict, after: dict, metadata: dict) -> str:
    """按动作类型生成中文摘要（用于列表展示，不参与过滤）。

    参数：action 动作编码；before/after 脱敏快照；metadata 审计附加信息（批量导入计数等）。
    返回：形如“新增角色 A、B；状态 active → disabled”的说明；无法归纳时回退为动作中文名。
    注意：批量导入用 metadata 里的计数而不是逐行快照；用户/角色类变更只概括“增删了哪些”，
    不逐字段罗列，避免列表过长且泄露无关细节。
    """
    if action == "user.bulk_import":
        return "新增 {created}、更新 {updated}、未变更 {unchanged}".format(
            created=metadata.get("created_count", 0),
            updated=metadata.get("updated_count", 0),
            unchanged=metadata.get("unchanged_count", 0),
        )
    if action.startswith("user."):
        added, removed = _difference(before.get("role_codes"), after.get("role_codes"))
        parts = []
        if added:
            parts.append("新增角色 " + "、".join(added))
        if removed:
            parts.append("移除角色 " + "、".join(removed))
        if before.get("status") != after.get("status") and (before.get("status") or after.get("status")):
            parts.append(f"状态 {before.get('status', '无')} → {after.get('status', '无')}")
        if before.get("department_id") != after.get("department_id"):
            parts.append("调整所属部门")
        return "；".join(parts) or ACTION_LABELS.get(action, action)
    if action.startswith("role."):
        added, removed = _difference(before.get("permissions"), after.get("permissions"))
        parts = []
        if added:
            parts.append(f"新增 {len(added)} 项权限")
        if removed:
            parts.append(f"移除 {len(removed)} 项权限")
        if before.get("data_scope") != after.get("data_scope") and (before.get("data_scope") or after.get("data_scope")):
            parts.append(f"数据范围 {before.get('data_scope', '无')} → {after.get('data_scope', '无')}")
        if before.get("enabled") != after.get("enabled") and ("enabled" in before or "enabled" in after):
            parts.append("启停状态变更")
        return "；".join(parts) or ACTION_LABELS.get(action, action)
    if action.startswith("resource."):
        snapshot = after or before
        return f"{snapshot.get('resource_type', '资源')} · {', '.join(snapshot.get('actions', [])) or '无动作'}"
    return ACTION_LABELS.get(action, action)


def _actor_labels(ctx: AuthContext, ids: set[str]) -> dict[str, dict]:
    """批量取操作人显示信息（一次查询，避免逐行 N+1）。

    返回：{user_id: {id, display_name, username}}；用户名取本地身份，缺失为空字符串（SSO 用户）。
    """
    if not ids:
        return {}
    rows = ctx.db.execute(select(User, AuthIdentity.username_normalized).outerjoin(
        AuthIdentity,
        (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
    ).where(User.id.in_(ids))).all()
    return {
        user.id: {"id": user.id, "display_name": user.display_name, "username": username or ""}
        for user, username in rows
    }


def _target_labels(ctx: AuthContext, rows: list[AuditLog]) -> dict[tuple[str, str], str]:
    """批量取变更对象名称，作为审计快照缺失时的回退展示。

    返回：{(resource_type, resource_id): 中文标签}；只查询 user/role 两类（resource_grant 的
    标签本身就在快照里）。角色查询带 workspace_id 过滤，避免取到其他工作区同名角色的名字。
    """
    user_ids = {row.resource_id for row in rows if row.resource_type == "user" and row.resource_id}
    role_ids = {row.resource_id for row in rows if row.resource_type == "role" and row.resource_id}
    labels: dict[tuple[str, str], str] = {}
    if user_ids:
        for user, username in ctx.db.execute(select(User, AuthIdentity.username_normalized).outerjoin(
            AuthIdentity,
            (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
        ).where(User.id.in_(user_ids))).all():
            labels[("user", user.id)] = f"{user.display_name}（{username or user.id}）"
    if role_ids:
        for role in ctx.db.scalars(select(Role).where(
            Role.workspace_id == ctx.workspace_id, Role.id.in_(role_ids),
        )):
            labels[("role", role.id)] = role.name
    return labels


def list_permission_history(
    ctx: AuthContext,
    *,
    page: int,
    page_size: int,
    target_type: str = "",
    target_id: str = "",
    actor_user_id: str = "",
    action: str = "",
    query: str = "",
) -> dict:
    """List authorization mutations visible in the caller's people data scope.

    用途：GET /api/v1/system/permissions/history（需要 system.permissions.history）。
    参数：page/page_size 分页；target_type/target_id/actor_user_id/action 为精确过滤；
    query 是关键词搜索，命中动作编码、资源标识或元数据 JSON 文本，以及操作人的姓名/登录名。
    返回：{items, total, page, page_size}，每项含 action_label、中文 summary、actor、target、
    before/after 快照与 source（manual/bulk_import/system）。
    异常：PermissionErrorEx（缺少查看权限）。
    权限与数据范围：非管理员只能看到 resource_type="user" 且资源在 allowed_owner_ids 范围内的事件，
    因此“别人对不在我管辖范围内的人做了什么”不可见（角色的历史变更只对全工作区可见者开放）。
    副作用：只读；排序 created_at + id 倒序，相同时间戳下用 id 兜底。
    实现约束：query 中的 cast(metadata_json, String) 是跨库可用的 JSON 文本搜索方式，
    不能改成方言函数，否则 SQLite/MySQL 行为不一致。
    """
    require_permission(ctx, "system.permissions.history")
    statement = select(AuditLog).where(
        AuditLog.workspace_id == ctx.workspace_id,
        AuditLog.action.in_(ACTION_LABELS),
    )
    owners = allowed_owner_ids(ctx, "system.permissions.history")
    if owners is not None:
        statement = statement.where(AuditLog.resource_type == "user", AuditLog.resource_id.in_(owners))
    if target_type:
        statement = statement.where(AuditLog.resource_type == target_type)
    if target_id:
        statement = statement.where(AuditLog.resource_id == target_id)
    if actor_user_id:
        statement = statement.where(AuditLog.actor_user_id == actor_user_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    if query.strip():
        value = query.strip()
        actor_ids = select(User.id).outerjoin(
            AuthIdentity,
            (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
        ).where(or_(
            User.display_name.contains(value, autoescape=True),
            AuthIdentity.username_normalized.contains(value.lower(), autoescape=True),
        ))
        statement = statement.where(or_(
            AuditLog.action.contains(value, autoescape=True),
            AuditLog.resource_id.contains(value, autoescape=True),
            cast(AuditLog.metadata_json, String).contains(value, autoescape=True),
            AuditLog.actor_user_id.in_(actor_ids),
        ))
    total = int(ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    rows = ctx.db.scalars(statement.order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size)).all()
    actors = _actor_labels(ctx, {row.actor_user_id for row in rows if row.actor_user_id})
    targets = _target_labels(ctx, rows)
    items = []
    for row in rows:
        metadata = row.metadata_json or {}
        before = metadata.get("before") if isinstance(metadata.get("before"), dict) else {}
        after = metadata.get("after") if isinstance(metadata.get("after"), dict) else {}
        target_label = metadata.get("label") or (after or before).get("label") or targets.get(
            (row.resource_type, row.resource_id or ""), row.resource_id or "批量任务",
        )
        items.append({
            "id": row.id,
            "occurred_at": row.created_at,
            "action": row.action,
            "action_label": ACTION_LABELS.get(row.action, row.action),
            "summary": _summary(row.action, before, after, metadata),
            "actor": actors.get(row.actor_user_id, {
                "id": row.actor_user_id or "", "display_name": "系统", "username": "",
            }),
            "target": {
                "type": row.resource_type,
                "id": row.resource_id or "",
                "label": target_label,
            },
            "before": before,
            "after": after,
            "source": metadata.get("source") if metadata.get("source") in {"manual", "bulk_import", "system"} else "system",
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}

