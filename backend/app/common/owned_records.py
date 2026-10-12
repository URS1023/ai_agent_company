"""Shared resource scope for new Python business modules.

职责：为"新建的 Python 业务模块"提供统一的资源归属基类与两个查询入口——
``OwnedRecord``（带创建人的工作区记录基类）、``visible_records``（构造带数据范围过滤的查询）、
``owned_record``（按 id 加载单条记录，越权即 404）。

系统位置：判定链的最后一段（认证 → 权限 → 数据范围 → 资源归属），服务层用它替代手写
``where(workspace_id=...)``：显式过滤写进 SQL，而不是"查出来再逐条拒绝"。它与
``common.data_scope`` 是互补而非替代关系：data_scope 用 ``with_loader_criteria`` 对已登记的
ROOT_RESOURCES 做全局兜底，本模块对**任意**继承 OwnedRecord 的业务表按动作显式过滤（包括未
登记进 data_scope 的表，如 alerts / analytics 下的业务表）；已登记的表两道过滤叠加（取交集，
更严格，属预期行为）。

约束：
- 目标模型必须继承 ``OwnedRecord``（即具备 workspace_id 与 owner_user_id 两列），并且传进来的
  ``permission`` 必须是登记过的 ``<域>.<动作>`` 权限码——域同时用于查显式资源授权，拼写错误会被
  require_permission 直接拒绝，不会静默放行；
- 越权访问统一表现为 404（"记录不存在或不在当前操作的数据范围内"），不返回 403，避免用状态码
  探测资源是否存在；
- 本模块只读，不写库、不写审计（增删改与审计由服务层负责）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from app.common.authorization import allowed_owner_ids, private_visibility_predicate, require_resource, resource_grant_ids
from app.errors import NotFoundError
from app.modules.system.models import WorkspaceRecord

if TYPE_CHECKING:
    # 模型注册只需要类型名，不能因此加载认证依赖及默认数据库配置。
    from app.common.deps import AuthContext


class OwnedRecord(WorkspaceRecord):
    """Versioned business record with explicit workspace and creator ownership.

    中文补充：业务模块"有归属的工作区记录"基类，在 WorkspaceRecord（主键 / workspace_id /
    时间戳 / 乐观锁 version）之上补一列创建人。

    关键不变式：
    - ``owner_user_id`` 写入时必须取自 ``AuthContext.user_id``，**不得**接受请求体里的创建人，
      否则资源可见性可被调用方自行指定；
    - 与 WorkspaceRecord 一样，子类必须同时继承 Base（``class X(OwnedRecord, Base)``）；
    - 创建人用于数据范围过滤（allowed_owner_ids）与"仅自己可见"判断，一旦被改写，历史数据范围
      判定会随之改变，因此业务上不允许"转移归属"以外的修改。

    生命周期：随工作区记录一起创建/软删（不适用软删的表直接删除）；本类不承载权限逻辑，
    可见性一律由下面的查询函数与 common.authorization 决定。
    """

    owner_user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id"), nullable=False, index=True, comment="创建人用户标识",
    )


def visible_records(ctx: AuthContext, model, permission: str):
    """Build an action-specific query, including explicit per-resource grants.

    中文补充：按"当前动作"构造一条只含可见记录的 SELECT，供列表 / 统计 / 校验查询复用。

    参数：``permission`` 决定两件事——数据范围取哪个角色（allowed_owner_ids）与显式资源授权按哪个
    域.动作查（resource_grant_ids），因此列表用 ``<域>.view``、写操作前校验用 ``<域>.edit`` 等，
    传错动作会得到过宽或过窄的结果集。

    返回：未执行的 SQLAlchemy Select，调用方可继续 ``.where()`` / ``.order_by()`` / ``.with_only_columns()``；
    返回的语句**不含**分页，也不排序，分页由调用方按 common/pagination 的约定追加。

    业务异常：PermissionErrorEx(403) —— 动作未授权或权限码未登记（在 allowed_owner_ids 内抛出），
    即"没有该动作权限"时不会退化成空列表，而是明确拒绝。

    副作用：无（只构造语句，不执行）。

    过滤条件（三层，AND 叠加）：本工作区 → 可见性（private 资源仅本人/管理员）→ 创建人范围或显式
    资源授权。第三层为 OR 关系：``owner 在允许集合内`` 或 ``资源被显式授权给本人/本人角色``。
    """
    owners = allowed_owner_ids(ctx, permission)
    statement = select(model).where(model.workspace_id == ctx.workspace_id)
    visibility = private_visibility_predicate(ctx, model)
    if visibility is not None:
        statement = statement.where(visibility)
    if owners is not None:
        domain, action = permission.rsplit(".", 1)
        statement = statement.where(or_(
            model.owner_user_id.in_(owners), model.id.in_(resource_grant_ids(ctx, domain, action)),
        ))
    return statement


def owned_record(ctx: AuthContext, model, record_id: str, permission: str):
    """Load a resource in the actor's currently authorized operation scope.

    中文补充：按 id 取一条业务记录，是服务层读/改/删的统一起点。

    参数：``record_id`` 为目标主键；``permission`` 为本次操作要执行的动作码（如 ``alerts.edit``），
    它同时决定可见范围与最终归属校验。

    返回：加载到的 ORM 实例（受乐观锁 version 管理，调用方按 WorkspaceRecord 约定提交）。

    业务异常：
    - NotFoundError(404) —— 记录不存在、属于其它工作区、创建人不在数据范围内且未被显式授权，
      三种情况统一为同一条文案，避免探测；
    - PermissionErrorEx(403) —— 动作本身未授权或权限码未登记（由 allowed_owner_ids 抛出），
      以及 require_resource 的资源级兜底校验失败。

    副作用：无（纯读取，不写库、不写审计）。调用方若随后修改记录并提交，审计需服务层另行调用。

    约束：先按查询条件过滤再校验，两步都保留是刻意的（纵深防御）：查询级过滤保证不会把越权记录
    带进内存，require_resource 保证即使将来过滤条件被改错，单条操作仍然会拒绝。
    """
    record = ctx.db.scalar(visible_records(ctx, model, permission).where(model.id == record_id))
    if record is None:
        raise NotFoundError("记录不存在或不在当前操作的数据范围内")
    require_resource(ctx, permission, record)
    return record
