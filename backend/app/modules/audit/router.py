# -*- coding: utf-8 -*-
"""审计日志只读查询模块。

对外仅暴露一个查询路由，供前端「审计」页展示当前工作区内的操作留痕。数据源是
``AuditLog`` 表（由其他模块在写操作时落库），本模块**只读**，不产生审计写入。

依赖：``common.deps`` 提供登录上下文，``common.authorization`` 提供权限校验与可见范围
过滤（``allowed_owner_ids``），二者缺一不可。

约束：
- 必须持有 ``audit.view`` 权限；无权限时由 ``require_permission`` 抛业务异常。
- 数据严格按 ``ctx.workspace_id`` 隔离，且当调用者可见范围受限时再按 ``actor_user_id`` 收窄，
  因此不能用不带过滤条件的全表查询替代。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ...common.deps import AuthContext, get_current_context
from ...common.authorization import allowed_owner_ids, require_permission
from ...models import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit(ctx: AuthContext = Depends(get_current_context), limit: int = 100):
    """查询审计日志列表。

    HTTP：``GET /api/audit``。权限：``audit.view``。

    参数：
        ctx: 登录上下文，提供工作区、数据库会话与权限信息（依赖注入）。
        limit: 期望返回条数，会被夹紧到 ``1..500``，避免前端传入超大值拖垮查询。

    返回：
        ``{"items": [...], "count": n}``，按 ``created_at`` 倒序（最新在前）；
        单条记录含 actor/action/resource/run/ip/metadata 等字段，
        ``created_at`` 为 ISO 字符串，``metadata`` 直接透出 ``metadata_json`` 列。

    副作用：无（只读，不写审计）。
    """
    require_permission(ctx, "audit.view")
    limit = max(1, min(limit, 500))
    statement = select(AuditLog).where(AuditLog.workspace_id == ctx.workspace_id)
    owners = allowed_owner_ids(ctx, "audit.view")
    if owners is not None:
        statement = statement.where(AuditLog.actor_user_id.in_(owners))
    rows = ctx.db.scalars(statement.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return {"items": [{
        "id": a.id, "actor_user_id": a.actor_user_id, "action": a.action,
        "resource_type": a.resource_type, "resource_id": a.resource_id,
        "run_id": a.run_id, "ip_address": a.ip_address,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "metadata": a.metadata_json,
    } for a in rows], "count": len(rows)}
