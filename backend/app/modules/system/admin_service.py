"""Resource grants, safe menu preferences, dictionaries, settings and sessions.

职责：系统管理里的“运行时配置与共享授权”部分 —— 资源级授权（把某个已存在资源单独共享给
用户/角色）、菜单显示偏好、非敏感系统参数、业务字典，以及有效会话的查看与撤销。

位置与依赖：对外路由前缀 /api/v1/system（router.py）—— GET/POST /resource-grants、
DELETE /resource-grants/{id}、GET /menus、PUT /menus/{menu_id}、GET/PUT /settings、
GET/POST/PATCH/DELETE /dictionary、GET /sessions、DELETE /sessions/{id}。
依赖 service_helpers（乐观锁/审计/提交）、platform.catalog（菜单与模块登记表）。

关键约束：
- 只做“已登记项”的管理：资源类型必须在 RESOURCE_MODELS、参数键必须在 DEFAULT_SETTINGS、
  菜单必须在 MODULES 登记，未登记的键一律拒绝，避免造出没有消费方的配置或虚假授权。
- 本模块不存任何凭据（密钥、密码），敏感配置一律走密钥模块；会话列表不返回令牌摘要。
- 资源授权只放宽“对某个具体资源”的可见性，不会放宽动作权限本身（动作仍要 require_resource）。
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from app.branding import PRODUCT_NAME
from app.common.authorization import allowed_owner_ids, require_permission, require_resource, require_user_scope
from app.common.deps import AuthContext
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.models import Artifact, Connector, DataSource, File, KnowledgeBase, Plugin, SessionToken, Skill, User, WorkspaceMember
from app.modules.platform.catalog import MODULES, navigation_catalog
from app.modules.system.models import DictionaryEntry, MenuSetting, ResourceGrant, Role, SessionContext, SystemSetting
from app.modules.system.permissions import ALL_PERMISSIONS
from app.modules.system.schemas import DictionaryCreate, DictionaryUpdate, MenuUpdate, ResourceGrantCreate, SettingUpdate
from app.modules.system.service_helpers import (
    check_version, commit_change, flush_changes, member_record, patch_record,
    require_global_account_control, scoped_record,
)

# 资源授权允许引用的资源类型 → ORM 模型（一个类型可对应多个模型，如 files 同时覆盖
# File 与 Artifact）。未登记的类型一律拒绝创建授权，防止出现指向不存在资源的“虚假授权”。
# 新增资源类型时必须在此登记，否则 create_grant 会抛 ValidationError。
RESOURCE_MODELS = {
    "files": (File, Artifact), "knowledge": (KnowledgeBase,), "datasources": (DataSource,),
    "plugins": (Plugin,), "skills": (Skill,), "connectors": (Connector,),
}
# 已登记的系统参数：只有在这里出现的 key 才能读写；每个参数都必须在 UI 有真实消费方，
# 并且 value 类型固定（page_size 为整数、timezone 为 IANA 时区、其余为非空短文本）。
# 说明文案同时用于“未落库时”的默认值与恢复默认时的描述回填。
DEFAULT_SETTINGS = {
    "site.name": {"value": PRODUCT_NAME, "description": "固定产品名称，用于顶栏、登录页和页面标题"},
    "site.description": {"value": "数据、知识与业务，集中协作。", "description": "首页的系统介绍"},
    "ui.page_size": {"value": 20, "description": "管理列表每页记录数，范围 10–100"},
    "ui.timezone": {"value": "Asia/Shanghai", "description": "页面日期显示使用的 IANA 时区"},
}


def list_grants(ctx: AuthContext, page: int, page_size: int) -> dict:
    """List explicit resource grants inside one workspace.

    用途：GET /api/v1/system/resource-grants（需要 system.permissions.view）。
    参数：page/page_size 分页。返回：{items: ResourceGrant 行, total, page, page_size}。
    异常：PermissionErrorEx（缺少查看权限）。
    副作用：只读；按 created_at 倒序展示最近授权。
    """
    require_permission(ctx, "system.permissions.view")
    statement = select(ResourceGrant).where(ResourceGrant.workspace_id == ctx.workspace_id)
    total = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
    items = ctx.db.scalars(statement.order_by(ResourceGrant.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def create_grant(ctx: AuthContext, payload: ResourceGrantCreate) -> ResourceGrant:
    """Share only existing authorized resources, never widen an action permission.

    用途：POST /api/v1/system/resource-grants（需要 system.permissions.manage），把某个具体资源
    额外共享给一个用户或角色。
    参数：ctx；payload 为 ResourceGrantCreate。
    返回：新建的 ResourceGrant 行。异常：ValidationError（资源类型未接入、动作不在权限目录）；
    NotFoundError（资源不存在或不属于本工作区）；PermissionErrorEx（操作人自己对该资源无对应动作）；
    ConflictError（同一 (资源, 对象, 动作集合) 已存在，唯一约束冲突）。
    权限：必须先 require_permission("system.permissions.manage")，再对每个动作调用
    require_resource —— 即“不能把你自己都没有权限的资源共享给别人”。
    副作用：写 system_resource_grants 并写 resource.grant 审计（after 快照含 label 便于列表展示）；
    授权对象为角色时，角色下所有用户都会获得该资源访问权。
    """
    require_permission(ctx, "system.permissions.manage")
    models = RESOURCE_MODELS.get(payload.resource_type)
    if models is None:
        raise ValidationError("该资源类型尚未接入授权服务，不能创建虚假授权")
    record = None
    for model in models:
        record = ctx.db.scalar(select(model).where(model.id == payload.resource_id, model.workspace_id == ctx.workspace_id))
        if record is not None:
            break
    if record is None:
        raise NotFoundError("资源不存在或不属于当前授权范围")
    for action in payload.actions:
        permission = f"{payload.resource_type}.{action}"
        if permission not in ALL_PERMISSIONS:
            raise ValidationError("不支持的资源操作")
        require_resource(ctx, permission, record)
    if payload.subject_type == "user":
        member_record(ctx, payload.subject_id)
    else:
        scoped_record(ctx, Role, payload.subject_id)
    grant = ResourceGrant(workspace_id=ctx.workspace_id, **payload.model_dump())
    grant.actions = sorted(set(payload.actions))
    ctx.db.add(grant)
    flush_changes(ctx)
    snapshot = {
        "label": f"{payload.subject_type}:{payload.subject_id} → {payload.resource_type}:{payload.resource_id}",
        **payload.model_dump(),
        "actions": grant.actions,
    }
    commit_change(ctx, "resource.grant", "resource_grant", grant.id, {
        "before": {}, "after": snapshot, "source": "manual", "label": snapshot["label"],
    })
    return grant


def delete_grant(ctx: AuthContext, grant_id: str) -> None:
    """Revoke an explicit grant without touching the referenced business object.

    用途：DELETE /api/v1/system/resource-grants/{id}（需要 system.permissions.manage）。
    参数：ctx；grant_id。返回：无。异常：NotFoundError（授权记录不属于本工作区）；
    PermissionErrorEx（缺少管理权限）。
    副作用：仅删除授权行并写 resource.revoke 审计（保留 before 快照）；被授权的文件、知识库等
    业务对象本身不受影响，也不会级联删除用户或角色。
    """
    require_permission(ctx, "system.permissions.manage")
    record = scoped_record(ctx, ResourceGrant, grant_id)
    before = {
        "label": f"{record.subject_type}:{record.subject_id} → {record.resource_type}:{record.resource_id}",
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "subject_type": record.subject_type,
        "subject_id": record.subject_id,
        "actions": list(record.actions),
    }
    ctx.db.delete(record)
    commit_change(ctx, "resource.revoke", "resource_grant", grant_id, {
        "before": before, "after": {}, "source": "manual", "label": before["label"],
    })


def menu_settings(ctx: AuthContext, *, management: bool = True) -> list[dict]:
    """Merge registered routes with presentation preferences; routes stay immutable.

    用途：把 platform.catalog 登记的导航项与本工作区的显示设置合并输出（GET /menus，
    管理页与前端导航共用）。
    参数：ctx；management 为 True 时校验 system.menus.view 权限，为 False 表示内部渲染
    （如 update_menu 的返回值）不需要再校验。
    返回：导航项列表，含 label/visible/sort_order 三个可被设置覆盖的字段；
    没有任何设置行时回退为登记表的 name 与 default_visible，顺序按登记顺序 ×10。
    异常：PermissionErrorEx（management=True 且缺少查看权限）。
    副作用：只读；不会因为设置而新增任何路由（未登记的菜单永远不会出现）。
    """
    if management:
        require_permission(ctx, "system.menus.view")
    settings = {row.menu_id: row for row in ctx.db.scalars(select(MenuSetting).where(MenuSetting.workspace_id == ctx.workspace_id))}
    items = []
    for index, entry in enumerate(navigation_catalog()):
        preference = settings.get(entry["id"])
        items.append(dict(entry, label=(preference.label if preference else "") or entry["name"],
                          visible=preference.visible if preference else entry.get("default_visible", True),
                          sort_order=preference.sort_order if preference else index * 10))
    return items


def update_menu(ctx: AuthContext, menu_id: str, payload: MenuUpdate) -> dict:
    """Change known menu presentation without granting routes or capabilities.

    用途：PUT /api/v1/system/menus/{menu_id}（需要 system.menus.manage）。
    参数：ctx；menu_id 必须是 MODULES 中已登记的模块标识；payload 为 MenuUpdate。
    返回：更新后的单个菜单项（含 label/visible/sort_order）。
    异常：NotFoundError（菜单未登记）；ValidationError（试图隐藏首页及权限恢复所需的基础入口）；
    PermissionErrorEx（缺少管理权限）。
    副作用：按需新建/更新 system_menu_settings 行并写 menu.update 审计；隐藏菜单只影响导航显示，
    不收回后端权限，也不会影响已有授权。
    """
    require_permission(ctx, "system.menus.manage")
    entry = next((entry for entry in MODULES if entry.id == menu_id), None)
    if entry is None:
        raise NotFoundError("菜单尚未登记")
    if menu_id in {"home", "users", "roles", "permissions", "menus"} and not payload.visible:
        raise ValidationError("不能隐藏首页及权限恢复所需的基础管理入口")
    record = ctx.db.scalar(select(MenuSetting).where(MenuSetting.workspace_id == ctx.workspace_id, MenuSetting.menu_id == menu_id))
    if record is None:
        record = MenuSetting(workspace_id=ctx.workspace_id, menu_id=menu_id)
        ctx.db.add(record)
    # An empty label explicitly restores the registered module name. Other
    # entities (dictionary entries, roles, etc.) still require nonempty labels.
    record.label = payload.label.strip()
    patch_record(record, payload.model_dump(exclude={"label"}))
    flush_changes(ctx)
    commit_change(ctx, "menu.update", "menu", record.id, {"menu_id": menu_id})
    return next(item for item in menu_settings(ctx, management=False) if item["id"] == menu_id)


def workspace_settings(ctx: AuthContext) -> list[dict]:
    """Read safe presentation settings; no credential is stored in this table.

    用途：GET /api/v1/system/settings，返回 DEFAULT_SETTINGS 中每个参数的有效值。
    参数：ctx。返回：key/value/description 列表；未落库的参数回退为默认值（读取不会创建行）。
    异常：无（查看参数不需要额外权限，已登录成员即可读取页面所需配置）。
    副作用：只读；刻意只遍历 DEFAULT_SETTINGS，历史遗留的未知键不会外泄给前端。
    """
    existing = {row.key: row for row in ctx.db.scalars(select(SystemSetting).where(SystemSetting.workspace_id == ctx.workspace_id))}
    return [dict(key=key, value=existing[key].value if key in existing else item["value"],
                 description=existing[key].description if key in existing else item["description"])
            for key, item in DEFAULT_SETTINGS.items()]


def update_setting(ctx: AuthContext, key: str, payload: SettingUpdate) -> dict:
    """Persist only settings that have an actual, typed consumer in the UI.

    用途：PUT /api/v1/system/settings/{key}（需要 system.settings.manage）。
    参数：ctx；key 必须在 DEFAULT_SETTINGS 中；payload 为 SettingUpdate。
    返回：{key, value, description}。异常：ValidationError（参数未登记、产品名称被改写、
    page_size 不是 10–100 整数、文本超长或空白、时区不存在）；PermissionErrorEx（缺少管理权限）。
    副作用：按需新建/更新 system_settings 行并写 setting.update 审计（只记录 key）；
    description 为空时回填默认说明，保证前端始终有可读文案。
    """
    require_permission(ctx, "system.settings.manage")
    if key not in DEFAULT_SETTINGS:
        raise ValidationError("参数未登记；模型密钥等敏感配置请使用对应管理模块")
    value = payload.value
    if key == "site.name" and value != PRODUCT_NAME:
        raise ValidationError(f"产品名称固定为“{PRODUCT_NAME}”，不能改为其他名称")
    if key == "ui.page_size" and (type(value) is not int or not 10 <= value <= 100):
        raise ValidationError("每页条数必须是 10–100 之间的整数")
    if key != "ui.page_size" and (not isinstance(value, str) or not value.strip() or len(value) > 300):
        raise ValidationError("参数值须为 1–300 字符的非空文本")
    if key == "ui.timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError("时区不存在") from exc
    record = ctx.db.scalar(select(SystemSetting).where(SystemSetting.workspace_id == ctx.workspace_id, SystemSetting.key == key))
    if record is None:
        record = SystemSetting(workspace_id=ctx.workspace_id, key=key)
        ctx.db.add(record)
    record.value = value
    record.description = payload.description or DEFAULT_SETTINGS[key]["description"]
    flush_changes(ctx)
    commit_change(ctx, "setting.update", "setting", record.id, {"key": key})
    return {"key": key, "value": record.value, "description": record.description}


def list_dictionary(ctx: AuthContext, page: int, page_size: int, namespace: str = "") -> dict:
    """List business dictionary values with namespace filtering.

    用途：GET /api/v1/system/dictionary（需要 system.settings.view）。
    参数：page/page_size 分页；namespace 精确过滤某类字典。返回分页结构（含 version 供编辑用）。
    异常：PermissionErrorEx（缺少查看权限）。副作用：只读；排序为 namespace + sort_order + id。
    """
    require_permission(ctx, "system.settings.view")
    statement = select(DictionaryEntry).where(DictionaryEntry.workspace_id == ctx.workspace_id)
    if namespace:
        statement = statement.where(DictionaryEntry.namespace == namespace)
    total = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = ctx.db.scalars(statement.order_by(DictionaryEntry.namespace, DictionaryEntry.sort_order, DictionaryEntry.id).offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": row.id, "namespace": row.namespace, "key": row.key, "label": row.label, "value": row.value,
              "enabled": row.enabled, "sort_order": row.sort_order, "version": row.version} for row in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def save_dictionary(ctx: AuthContext, payload: DictionaryCreate | DictionaryUpdate, record_id: str | None = None) -> dict:
    """Create/update dictionary data; never use dictionary values to grant permissions.

    用途：POST /dictionary 与 PATCH /dictionary/{id} 的共用实现（需要 system.settings.manage）。
    参数：ctx；payload 为 DictionaryCreate（新建）或 DictionaryUpdate（修改）；record_id 非空
    表示修改，此时必须带 version。返回：{id, version}。
    异常：ValidationError（序列化后超过 16000 字符、字段为空等由 patch_record 抛出的校验）；
    NotFoundError（记录不属于本工作区）；ConflictError（namespace+key 重复或版本冲突）。
    副作用：写入/更新 system_dictionary_entries 并写 dictionary.save 审计（仅记动作）。
    注意：json.dumps 用 ensure_ascii=False 计算长度，保证“中文按字符数”而不是转义后的字节数计限。
    """
    require_permission(ctx, "system.settings.manage")
    values = payload.model_dump(exclude_unset=True)
    if len(json.dumps(values, ensure_ascii=False)) > 16000:
        raise ValidationError("单条字典配置不能超过 16000 字符")
    if record_id:
        record = scoped_record(ctx, DictionaryEntry, record_id)
        check_version(record, payload.version)
    else:
        values = payload.model_dump()
        record = DictionaryEntry(workspace_id=ctx.workspace_id)
        ctx.db.add(record)
    patch_record(record, values)
    flush_changes(ctx)
    commit_change(ctx, "dictionary.save", "dictionary", record.id)
    return {"id": record.id, "version": record.version}


def delete_dictionary(ctx: AuthContext, record_id: str) -> None:
    """Delete a user-managed dictionary item only in this workspace.

    用途：DELETE /api/v1/system/dictionary/{id}（需要 system.settings.manage）。
    参数：ctx；record_id。返回：无。异常：NotFoundError（记录不属于本工作区）或权限异常。
    副作用：删除字典行并写 dictionary.delete 审计；业务表若已引用该字典项的文本值，不会级联修改
    （字典只是展示与取值来源，不做外键约束）。
    """
    require_permission(ctx, "system.settings.manage")
    record = scoped_record(ctx, DictionaryEntry, record_id)
    ctx.db.delete(record)
    commit_change(ctx, "dictionary.delete", "dictionary", record_id)


def list_sessions(ctx: AuthContext, page: int, page_size: int) -> dict:
    """List active sessions of manageable members, without token digests.

    用途：GET /api/v1/system/sessions（需要 system.sessions.view），展示当前工作区内
    “操作人有权管理”的成员的未过期、未撤销会话。
    参数：ctx；page/page_size 分页。返回：分页结构；device_hash 只返回前 12 位，绝不返回
    token_digest；缺少 SessionContext 的历史会话以 auth_method="legacy"、风险等级 unknown 呈现。
    异常：PermissionErrorEx（缺少查看权限）。
    权限与数据范围：先用 allowed_owner_ids("system.sessions.view") 收敛人员范围，再对每个候选
    用户调用 require_global_account_control 过滤掉“还属于其他工作区”的共享账号（这类账号的会话
    只有其全部工作区管理员才能处置），过滤逻辑用 try/except PermissionErrorEx 实现。
    副作用：只读。
    """
    require_permission(ctx, "system.sessions.view")
    now = datetime.utcnow()
    users = ctx.db.scalars(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == ctx.workspace_id)).all()
    owners = allowed_owner_ids(ctx, "system.sessions.view")
    manageable = []
    for user_id in users:
        if owners is not None and user_id not in owners:
            continue
        try:
            require_global_account_control(ctx, user_id)
            manageable.append(user_id)
        except PermissionErrorEx:
            continue
    statement = select(SessionToken, User.display_name, SessionContext).join(
        User, User.id == SessionToken.user_id,
    ).outerjoin(
        SessionContext,
        (SessionContext.session_id == SessionToken.id) & (SessionContext.workspace_id == ctx.workspace_id),
    ).where(
        SessionToken.user_id.in_(manageable), SessionToken.revoked_at.is_(None),
        SessionToken.expires_at > now, SessionToken.idle_expires_at > now, User.status == "active",
    )
    total = ctx.db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = ctx.db.execute(statement.order_by(SessionToken.last_seen_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": row.id, "user_id": row.user_id, "display_name": name, "last_seen_at": row.last_seen_at,
              "expires_at": row.expires_at, "idle_expires_at": row.idle_expires_at,
              "auth_method": context.auth_method if context else "legacy",
              "ip_address": context.ip_address if context else "",
              "device_hash": context.device_hash[:12] if context else "",
              "risk_level": context.risk_level if context else "unknown"}
             for row, name, context in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def revoke_session(ctx: AuthContext, session_id: str) -> None:
    """Revoke a known actor session; cross-workspace account administration is denied.

    用途：DELETE /api/v1/system/sessions/{id}（需要 system.sessions.manage）。
    参数：ctx；session_id。返回：无。异常：NotFoundError（会话不存在）；PermissionErrorEx
    （目标用户非本工作区成员、超出数据范围、或账号仍属于其他工作区，来自
    require_global_account_control）。
    副作用：把 SessionToken.revoked_at 置为当前时间（不删除行，保留审计痕迹）并写 session.revoke
    审计；用户需重新登录，已下发的访问令牌在下次校验时失效。
    """
    require_permission(ctx, "system.sessions.manage")
    row = ctx.db.get(SessionToken, session_id)
    if row is None:
        raise NotFoundError("会话不存在")
    member_record(ctx, row.user_id)
    require_user_scope(ctx, "system.sessions.manage", row.user_id)
    require_global_account_control(ctx, row.user_id)
    row.revoked_at = datetime.utcnow()
    commit_change(ctx, "session.revoke", "session", row.id, {"user_id": row.user_id})
