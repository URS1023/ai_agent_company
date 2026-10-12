"""Versioned templates, notices and recipient-scoped inbox messages.

中文补充（通知与站内信）：
- 组成：通知模板（NotificationTemplate，带 code/variables，只做变量占位不执行代码）、
  系统公告（SystemNotice，含受众与发布/到期时间）、收件箱消息（InboxMessage，
  每个收件人一条）。
- 权限：读操作要求 system.operations.view，写操作要求 system.operations.manage；
  收件箱与消息状态变更**只允许操作自己**（查询条件带 recipient_user_id=ctx.user_id），
  不接受任何"代读/代归档他人消息"的参数。
- 幂等：publish_notice 对已发布公告直接返回原回执（不重复生成消息、
  不重置 published_at、不递增版本），因此重复点击/重试不会造成消息风暴；
  已归档公告拒绝再发布。公告只有在 draft 状态可修改，发布后要改必须复制为新公告。
- 受众解析：all 取工作区全部启用成员；users/roles 解析后若为空或包含不存在/
  已停用/跨工作区的 ID，直接报错而不是"少发几个人"。
- 时间：API 传入的带时区时间统一转成 UTC naive 再入库（与数据库契约一致）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import User, WorkspaceMember
from app.modules.system.models import Role, UserRole
from app.modules.system.service_helpers import check_version, commit_change, flush_changes, scoped_record

from .models import InboxMessage, NotificationTemplate, SystemNotice
from .schemas import NoticeInput, TemplateInput


def _iso(value: datetime | None) -> str | None:
    """把时间转成 ISO 字符串用于响应（None 保持 None，避免出现 "None" 字样）。"""
    return value.isoformat() if value else None


def _utc_naive(value: datetime | None) -> datetime | None:
    """Normalize API datetimes to the UTC-naive database contract.

    中文补充：带时区的时间先转 UTC 再去掉时区；不带时区的按 UTC 处理（历史行为）。
    数据库统一存 UTC naive，读取方不做二次换算。
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def template_view(row: NotificationTemplate) -> dict:
    """通知模板的对外视图（camelCase；variables 是模板占位符清单，供前端渲染提示）。"""
    return {
        "id": row.id, "code": row.code, "name": row.name,
        "subjectTemplate": row.subject_template, "bodyTemplate": row.body_template,
        "variables": row.variables, "status": row.status, "version": row.version,
        "createTime": _iso(row.created_at), "updateTime": _iso(row.updated_at),
    }


def list_templates(ctx: AuthContext, keyword: str = "") -> list[dict]:
    """列出本工作区通知模板（按更新时间倒序）；权限 system.operations.view。

    keyword 用 autoescape=True 做包含匹配，转义 %/_ 等通配符，避免用户输入被当作 LIKE 模式。
    """
    require_permission(ctx, "system.operations.view")
    statement = select(NotificationTemplate).where(NotificationTemplate.workspace_id == ctx.workspace_id)
    if keyword:
        statement = statement.where(or_(
            NotificationTemplate.name.contains(keyword, autoescape=True),
            NotificationTemplate.code.contains(keyword, autoescape=True),
        ))
    return [template_view(row) for row in ctx.db.scalars(
        statement.order_by(NotificationTemplate.updated_at.desc(), NotificationTemplate.id)
    ).all()]


def save_template(ctx: AuthContext, payload: TemplateInput, record_id: str | None = None) -> dict:
    """新建或修改通知模板；权限 system.operations.manage。

    ``record_id`` 为空表示新建（此时 payload.version 必须为 0，否则报错防止误用编辑语义）；
    修改时用 version 做乐观锁并自增版本号。副作用：写库 + 审计日志，返回模板视图。
    """
    require_permission(ctx, "system.operations.manage")
    values = payload.model_dump(exclude={"version"})
    if record_id:
        row = scoped_record(ctx, NotificationTemplate, record_id)
        check_version(row, payload.version)
        row.version += 1
    else:
        if payload.version:
            raise ValidationError("创建模板时 version 必须为 0")
        row = NotificationTemplate(workspace_id=ctx.workspace_id, created_by=ctx.user_id)
        ctx.db.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    flush_changes(ctx)
    commit_change(ctx, "system.notification_template.save", "notification_template", row.id)
    return template_view(row)


def delete_template(ctx: AuthContext, record_id: str, version: int) -> None:
    """停用模板（status=disabled，软删除）；权限 system.operations.manage。

    不做物理删除：历史通知记录仍引用模板 code，删除会导致追溯断裂。
    """
    require_permission(ctx, "system.operations.manage")
    row = scoped_record(ctx, NotificationTemplate, record_id)
    check_version(row, version)
    row.status = "disabled"
    row.version += 1
    commit_change(ctx, "system.notification_template.disable", "notification_template", row.id)


def notice_view(row: SystemNotice, *, delivered: int | None = None) -> dict:
    """公告视图；``delivered`` 传入时附带已投递消息数（不传则不出现该字段）。"""
    result = {
        "id": row.id, "title": row.title, "body": row.body,
        "noticeType": row.notice_type, "severity": row.severity,
        "audience": row.audience, "status": row.status,
        "publishAt": _iso(row.publish_at), "expiresAt": _iso(row.expires_at),
        "publishedAt": _iso(row.published_at), "createdBy": row.created_by,
        "version": row.version, "createTime": _iso(row.created_at), "updateTime": _iso(row.updated_at),
    }
    if delivered is not None:
        result["deliveredCount"] = delivered
    return result


def list_notices(ctx: AuthContext, keyword: str = "", status: str = "") -> list[dict]:
    """列出本工作区公告（按更新时间倒序）；权限 system.operations.view。

    投递数用一次 group by 聚合批量取回，避免逐条 count；keyword 同样转义通配符。
    """
    require_permission(ctx, "system.operations.view")
    statement = select(SystemNotice).where(SystemNotice.workspace_id == ctx.workspace_id)
    if keyword:
        statement = statement.where(or_(
            SystemNotice.title.contains(keyword, autoescape=True),
            SystemNotice.body.contains(keyword, autoescape=True),
        ))
    if status:
        statement = statement.where(SystemNotice.status == status)
    rows = ctx.db.scalars(statement.order_by(SystemNotice.updated_at.desc(), SystemNotice.id)).all()
    counts = dict(ctx.db.execute(select(
        InboxMessage.notice_id, func.count(InboxMessage.id),
    ).where(
        InboxMessage.workspace_id == ctx.workspace_id,
        InboxMessage.notice_id.in_([row.id for row in rows] or [""]),
    ).group_by(InboxMessage.notice_id)).all())
    return [notice_view(row, delivered=int(counts.get(row.id, 0))) for row in rows]


def get_notice(ctx: AuthContext, record_id: str) -> dict:
    """读取公告详情（含投递数）；权限 system.operations.view。"""
    require_permission(ctx, "system.operations.view")
    row = scoped_record(ctx, SystemNotice, record_id)
    delivered = int(ctx.db.scalar(select(func.count(InboxMessage.id)).where(
        InboxMessage.workspace_id == ctx.workspace_id, InboxMessage.notice_id == row.id,
    )) or 0)
    return notice_view(row, delivered=delivered)


def save_notice(ctx: AuthContext, payload: NoticeInput, record_id: str | None = None) -> dict:
    """新建或修改公告草稿；权限 system.operations.manage。

    约束：新建时 version 必须为 0；修改只允许 draft 状态（已发布/已归档抛 ConflictError，
    要求复制为新公告）；受众以 {"type","ids"} 结构存储，时间字段统一转 UTC naive。
    副作用：写库 + 审计日志。
    """
    require_permission(ctx, "system.operations.manage")
    audience = {"type": payload.audience_type, "ids": payload.audience_ids}
    if record_id:
        row = scoped_record(ctx, SystemNotice, record_id)
        check_version(row, payload.version)
        if row.status != "draft":
            raise ConflictError("已发布或归档公告不可修改；请复制为新公告")
        row.version += 1
    else:
        if payload.version:
            raise ValidationError("创建公告时 version 必须为 0")
        row = SystemNotice(workspace_id=ctx.workspace_id, created_by=ctx.user_id)
        ctx.db.add(row)
    row.title, row.body = payload.title.strip(), payload.body.strip()
    row.notice_type, row.severity, row.audience = payload.notice_type, payload.severity, audience
    row.publish_at = _utc_naive(payload.publish_at)
    row.expires_at = _utc_naive(payload.expires_at)
    flush_changes(ctx)
    commit_change(ctx, "system.notice.save", "notice", row.id)
    return notice_view(row, delivered=0)


def _recipient_ids(ctx: AuthContext, audience: dict) -> list[str]:
    """把公告受众解析成"当前可接收消息的启用成员 ID 列表"。

    all：工作区全部启用成员；users：按给定 ID 过滤；roles：先取本工作区启用角色，
    再取其成员。解析结果去重排序，保证同一公告的消息条数可预期。
    异常：受众类型非法、指定收件人缺失/停用/跨工作区、或最终收件人为空时抛 ValidationError
    ——宁可发不出去也不静默少发（未送达会被误认为"发过了"）。
    """
    members = select(WorkspaceMember.user_id).join(User, User.id == WorkspaceMember.user_id).where(
        WorkspaceMember.workspace_id == ctx.workspace_id, User.status == "active",
    )
    audience_type = audience.get("type", "all")
    identifiers = list(dict.fromkeys(audience.get("ids") or []))
    if audience_type == "users":
        members = members.where(WorkspaceMember.user_id.in_(identifiers))
    elif audience_type == "roles":
        valid_roles = select(Role.id).where(
            Role.workspace_id == ctx.workspace_id, Role.id.in_(identifiers), Role.enabled.is_(True),
        )
        members = members.where(WorkspaceMember.user_id.in_(select(UserRole.user_id).where(
            UserRole.workspace_id == ctx.workspace_id, UserRole.role_id.in_(valid_roles),
        )))
    elif audience_type != "all":
        raise ValidationError("公告受众类型无效")
    result = sorted(set(ctx.db.scalars(members).all()))
    if audience_type != "all" and len(result) != len(set(identifiers)) and audience_type == "users":
        raise ValidationError("部分公告收件人不存在、已停用或不属于当前工作区")
    if not result:
        raise ValidationError("当前受众没有可接收消息的启用成员")
    return result


def publish_notice(ctx: AuthContext, record_id: str, version: int) -> dict:
    """发布公告：为每个收件人生成一条收件箱消息；权限 system.operations.manage。

    幂等语义（重要）：已发布公告再次调用直接返回原回执与既有投递数，不重复建消息、
    不改 published_at、不递增版本——重复点击/请求重试不会造成消息风暴。
    已归档公告抛 ConflictError；已过期的公告要求先改到期时间。
    返回：公告视图（含 deliveredCount）；副作用：批量写 InboxMessage + 审计日志。
    """
    require_permission(ctx, "system.operations.manage")
    row = scoped_record(ctx, SystemNotice, record_id)
    check_version(row, version)
    if row.status == "archived":
        raise ConflictError("归档公告不能再次发布")
    existing = int(ctx.db.scalar(select(func.count(InboxMessage.id)).where(
        InboxMessage.workspace_id == ctx.workspace_id, InboxMessage.notice_id == row.id,
    )) or 0)
    if row.status == "published":
        return notice_view(row, delivered=existing)
    now = datetime.utcnow()
    if row.expires_at is not None and row.expires_at <= now:
        raise ConflictError("公告已过期，请修改到期时间后再发布")
    recipients = _recipient_ids(ctx, row.audience)
    for user_id in recipients:
        ctx.db.add(InboxMessage(
            workspace_id=ctx.workspace_id, notice_id=row.id, recipient_user_id=user_id,
            subject=row.title, body=row.body, message_type=row.notice_type, severity=row.severity,
        ))
    row.status, row.published_at, row.publish_at = "published", now, row.publish_at or now
    row.version += 1
    flush_changes(ctx)
    commit_change(ctx, "system.notice.publish", "notice", row.id, {"recipientCount": len(recipients)})
    return notice_view(row, delivered=len(recipients))


def archive_notice(ctx: AuthContext, record_id: str, version: int) -> dict:
    """归档公告（幂等：已归档直接返回当前视图，不再改版本）。
    权限 system.operations.manage；归档不删除已投递的收件箱消息。
    """
    require_permission(ctx, "system.operations.manage")
    row = scoped_record(ctx, SystemNotice, record_id)
    check_version(row, version)
    if row.status == "archived":
        return notice_view(row)
    row.status, row.version = "archived", row.version + 1
    commit_change(ctx, "system.notice.archive", "notice", row.id)
    return notice_view(row)


def inbox(ctx: AuthContext, *, status: str = "", limit: int = 50, offset: int = 0) -> dict:
    """当前用户的收件箱（分页）；查询条件强制 recipient_user_id=ctx.user_id。

    不需要额外权限码：任何人只能读自己的消息。返回 items/total/unread/limit/offset，
    unread 不受 status 过滤影响，便于前端始终显示未读数。已归档消息不出现在列表。
    """
    statement = select(InboxMessage).where(
        InboxMessage.workspace_id == ctx.workspace_id,
        InboxMessage.recipient_user_id == ctx.user_id,
        InboxMessage.status != "archived",
    )
    if status:
        statement = statement.where(InboxMessage.status == status)
    total = int(ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    unread = int(ctx.db.scalar(select(func.count(InboxMessage.id)).where(
        InboxMessage.workspace_id == ctx.workspace_id,
        InboxMessage.recipient_user_id == ctx.user_id,
        InboxMessage.status == "unread",
    )) or 0)
    rows = ctx.db.scalars(statement.order_by(InboxMessage.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [message_view(row) for row in rows], "total": total, "unread": unread,
            "limit": limit, "offset": offset}


def message_view(row: InboxMessage) -> dict:
    """收件箱消息视图（camelCase；readAt 仅在已读时有值）。"""
    return {
        "id": row.id, "noticeId": row.notice_id, "subject": row.subject, "body": row.body,
        "messageType": row.message_type, "severity": row.severity, "status": row.status,
        "readAt": _iso(row.read_at), "version": row.version, "createTime": _iso(row.created_at),
    }


def change_message(ctx: AuthContext, record_id: str, version: int, action: str) -> dict:
    """标记消息已读或归档；只能操作发给自己的消息。

    权限模型：查询条件同时限定 workspace_id 与 recipient_user_id，因此即使猜到他人
    消息 ID 也只会得到 NotFoundError（不泄露存在性）。version 做乐观锁；
    action 只接受 read/archive，其余抛 ValidationError；已读消息重复 read 不会覆盖 readAt。
    副作用：写库并提交（消息状态变更不写审计日志，避免审计噪音）。
    """
    row = ctx.db.scalar(select(InboxMessage).where(
        InboxMessage.id == record_id, InboxMessage.workspace_id == ctx.workspace_id,
        InboxMessage.recipient_user_id == ctx.user_id,
    ))
    if row is None:
        raise NotFoundError("消息不存在")
    check_version(row, version)
    if action == "read" and row.status == "unread":
        row.status, row.read_at = "read", datetime.utcnow()
    elif action == "archive":
        row.status = "archived"
    elif action not in {"read", "archive"}:
        raise ValidationError("消息操作无效")
    row.version += 1
    ctx.db.commit()
    return message_view(row)
