# -*- coding: utf-8 -*-
"""Audit logging helper.

审计落库的唯一写入口：各业务写操作在自身事务里调用 ``audit()`` 追加一条 ``AuditLog``。

落库时机：**本模块不 commit**（仅 ``session.add``）。多数调用方把审计与业务变更放在同一事务，
业务回滚时审计一并回滚——这是刻意的取舍：审计只记录真正提交成功的动作，不做独立写入。
因此调用方必须在同一事务内既改业务数据又调 ``audit()``；确需"业务失败也留痕"的场景要自行
单独提交，不要在这里加 commit。

字段：``workspace_id`` 必填（审计按工作区隔离），``actor_user_id`` 允许为空（服务令牌/系统动作
没有自然人主体），``metadata_json`` 放结构化上下文（默认 ``{}``，**不要放凭据或完整请求体**）。
``action`` 采用 ``域.动词`` 命名（如 ``file.upload``、``auth.login``），新增值需与既有风格一致，
因为按 action 检索是运维的主要用法。

依赖：``models.AuditLog``、``ids.new_id``。只读查询在 ``modules/audit``（需 ``audit.view`` 权限），
本模块只负责写。
"""
from __future__ import annotations

from datetime import datetime

from ..ids import new_id
from ..models import AuditLog


def audit(session, *, workspace_id: str, actor_user_id=None, action: str, resource_type: str,
          resource_id=None, run_id=None, ip_address=None, metadata=None):
    """追加一条审计记录到调用方的事务，返回尚未提交的 ``AuditLog`` 实例。

    参数：``session`` 调用方持有的 SQLAlchemy 会话（本函数不 commit、不 rollback）；
    ``workspace_id`` 工作区隔离键，必填；``actor_user_id`` 操作主体，系统/服务动作为 None；
    ``action`` 动作名（``域.动词``）；``resource_type``/``resource_id`` 被操作资源；
    ``run_id`` 关联的 Run（Agent/工作流类动作用于串联事件流）；``ip_address`` 请求来源；
    ``metadata`` 结构化补充信息，落库到 ``metadata_json``，``None`` 归一为 ``{}``。

    返回：已 add 进 session 的 ``AuditLog``（调用方若需在响应里回 id 可读它，但提交由调用方负责）。

    异常：本函数不抛业务异常；字段超长/类型不符由数据库或 ORM 在 flush/commit 时报错，
    因此调用方无需为审计单独 try/except——审计失败即业务失败（同一事务语义）。

    副作用：仅内存态 session.add（及后续由调用方触发的落库）。
    """
    log = AuditLog(
        id=new_id(),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        run_id=run_id,
        ip_address=ip_address,
        metadata_json=metadata or {},
        created_at=datetime.utcnow(),
    )
    session.add(log)
    return log
