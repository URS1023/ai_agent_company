"""Transactional invariants used by administration services.

本模块是 system 模块的“事务与并发约束底座”，被 user_service、user_bulk、role_service、
organization_service、lifecycle_service、admin_service 共同复用；自身不注册任何 HTTP 路由
（system 模块对外统一挂在 /api/v1/system，见 router.py 与 governance_router.py）。

职责与约束：
- 工作区隔离：所有查询都必须带上 ctx.workspace_id，跨工作区的标识一律按“不存在”处理，
  避免用报错回显其他工作区的记录标识（scoped_record / member_record 是统一入口）。
- 事务边界：本模块只校验、登记、落库，不自行开启嵌套事务；是否提交由调用方决定，且一次
  业务变更只允许 commit_change 一个提交出口，它会把审计与业务变更放进同一事务。
- 乐观锁：版本号来自 models.WorkspaceRecord.version（同时注册为 SQLAlchemy 的
  version_id_col），写前用 check_version 拦截陈旧表单，提交时仍可能由 StaleDataError 兜底。
- 权限：本模块只做“归属/范围”校验，动作权限（require_permission）与数据范围（allowed_*）
  由各 service 显式调用，不做隐式继承。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.common.deps import AuthContext
from app.errors import ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.models import User, Workspace, WorkspaceMember


def scoped_record(ctx: AuthContext, model, record_id: str):
    """Load only inside the actor's workspace, without leaking foreign IDs.

    用途：按“本工作区 + 主键”读取一条记录，是所有管理类服务的统一取数入口。
    参数：ctx 已鉴权上下文；model 必须带 workspace_id 列的 ORM 模型；record_id 目标标识。
    返回：命中的模型实例。
    异常：NotFoundError —— 记录不存在、或存在但属于其他工作区（两种情况刻意合并，
    使调用方无法通过错误差异探测其他工作区的标识是否有效）。
    副作用：无（只读，不触发 flush/commit）。
    """
    record = ctx.db.scalar(select(model).where(model.id == record_id, model.workspace_id == ctx.workspace_id))
    if record is None:
        raise NotFoundError("记录不存在或不属于当前工作区")
    return record


def member_record(ctx: AuthContext, user_id: str) -> WorkspaceMember:
    """Require membership before reading or modifying another user's data.

    用途：把“目标用户”收敛为“当前工作区成员”，任何针对他人的管理动作都要先过这一关。
    参数：ctx 已鉴权上下文；user_id 目标用户标识（User.id，不是成员记录 ID）。
    返回：该用户在当前工作区的 WorkspaceMember 实例（旧版兼容身份 role 字段也在这里）。
    异常：NotFoundError —— 用户不是当前工作区成员。
    副作用：无（只读）。注意：本函数不校验动作权限与数据范围，调用方仍需自行
    require_permission / require_user_scope。
    """
    record = ctx.db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ctx.workspace_id, WorkspaceMember.user_id == user_id,
    ))
    if record is None:
        raise NotFoundError("该用户不属于当前工作区")
    return record


def check_version(record, version: int) -> None:
    """A stale form must be reloaded, never silently overwrite saved changes.

    用途：乐观锁校验。前端提交的 version 必须等于库中当前 version，否则视为“表单打开后
    记录已被他人修改”，拒绝写入而不是静默覆盖。
    参数：record 已加载的记录（其 version 来自 models.WorkspaceRecord.version，同一列同时
    注册为 SQLAlchemy 的 version_id_col）；version 为请求体里的版本号。
    返回：无。
    异常：ConflictError —— 版本不一致，携带 current_version 供前端提示刷新。
    调用约束：
    - 必须在任何 setattr/patch_record 之前调用，且要在 scoped_record/member_record 之后；
    - 只保护“已存在记录”的更新，新建记录无需传入 version；
    - 即使通过本函数，提交阶段仍可能因并发写入抛 StaleDataError（由 commit_change 转换），
      所以两者是互补关系，不能相互替代。
    副作用：无（只读比较，不修改记录）。
    """
    if record.version != version:
        raise ConflictError("记录已被修改，请刷新后重试", current_version=record.version)


def patch_record(record, values: dict) -> None:
    """Apply explicitly supplied values; only nullable relationships accept null.

    用途：把请求体里“显式传入”的字段逐个写回记录（PATCH 语义），未出现的键保持原值，
    因此本函数只接受 payload.model_dump(exclude_unset=True) 这类结果。
    参数：record 目标记录；values 字段字典。
    返回：无（原地修改 record，需调用方后续 flush/commit）。
    异常：ValidationError —— 除 parent_id/leader_user_id/department_id 三个可空关系外，
    其他字段传 null 直接拒绝；name/display_name/label/code/key 空白（strip 后为空）也拒绝。
    调用约束：
    - 必须先 check_version 再调用，否则会把陈旧表单的值写进记录；
    - version 键被刻意跳过：版本号永远由数据库/ORM 维护，不接受客户端赋值；
    - 字符串统一 strip，避免出现前后空格的编码导致唯一约束形同虚设；
    - 本函数不做权限判定，越权字段（如 status、permissions、data_scope、builtin、
      data_scope 之外的敏感列）是否允许改写由调用方在准备阶段决定。
    副作用：修改 record 上的属性（内存态），可能触发 ORM 的脏检查。
    """
    for key, value in values.items():
        if key == "version":
            continue
        if value is None and key not in {"parent_id", "leader_user_id", "department_id"}:
            raise ValidationError(f"字段 {key} 不能为 null")
        if isinstance(value, str):
            value = value.strip()
            if key in {"name", "display_name", "label", "code", "key"} and not value:
                raise ValidationError("名称或编码不能为空白")
        setattr(record, key, value)


def commit_change(ctx: AuthContext, action: str, resource_type: str, resource_id: str, metadata: dict | None = None) -> None:
    """Commit mutation and its redacted audit record atomically.

    用途：一次业务变更的唯一提交出口 —— 先写审计，再提交，使“业务数据 + 审计记录”要么
    同时生效要么同时回滚，避免出现改了权限却没有留痕的窗口。
    参数：ctx 已鉴权上下文；action 稳定机器动作编码（permission_history.ACTION_LABELS 的键，
    如 user.create / role.update）；resource_type/resource_id 变更对象；metadata 审计明细，
    惯例包含 before/after/source/label 快照，写入前需已脱敏（不得含密码、token、密钥）。
    返回：无。
    异常：ConflictError —— IntegrityError（编码/关联重复或仍被其他业务引用）与
    StaleDataError（提交时发现记录已被并发修改）都会被回滚并转成中文冲突提示，
    保证数据库细节不外泄。
    调用约束：
    - 调用后事务即结束，调用方不得再基于同一 session 继续写库，也不应在同一业务动作里
      重复调用（会产生多条语义重复的审计）；
    - before 快照必须在修改前采集，after 快照必须在修改后采集，本函数不负责取快照；
    - 需要把“暂存但先不提交”的批量场景（如 user_bulk 导入）应改用 flush_changes + 自行提交。
    副作用：写入 audit 表并提交当前事务（可能的副作用还包括释放 lock_workspace 的行锁）。
    """
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id, action=action,
          resource_type=resource_type, resource_id=resource_id, metadata=metadata or {})
    try:
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("编码或关联已存在，或记录仍被其他业务引用") from exc
    except StaleDataError as exc:
        ctx.db.rollback()
        raise ConflictError("记录已被其他操作修改，请刷新后重试") from exc


def flush_changes(ctx: AuthContext) -> None:
    """Allocate IDs while translating duplicate-key errors without SQL leakage.

    用途：立即执行 flush，让默认值/自增/外键在事务内先落库（拿到主键、暴露唯一约束冲突），
    同时把数据库报错翻译成可展示的中文提示。
    参数：ctx 已鉴权上下文。返回：无。
    异常：ConflictError —— 唯一约束冲突（编码或关联已存在），并已回滚事务。
    调用约束：flush 不结束事务，后续仍可继续 add；失败时事务已回滚，调用方不要再继续写。
    副作用：向数据库发出未提交的写指令（事务内的“脏”状态对同 session 可见）。
    """
    try:
        ctx.db.flush()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("编码或关联已存在") from exc


def lock_workspace(ctx: AuthContext, workspace_id: str | None = None) -> None:
    """Serialize privilege changes on MySQL to preserve a usable administrator.

    用途：对工作区行加排他锁（SELECT ... FOR UPDATE），把“检查最后一个管理员”和后续
    写入串行化，避免两个并发请求各自认为“还有别人是管理员”从而把管理员全部降权。
    参数：ctx 已鉴权上下文；workspace_id 可选，缺省锁定 ctx.workspace_id（lifecycle 场景会
    临时切换 ctx.workspace 来锁定其他工作区，见 user_service.prepare_user_update）。
    返回：无。
    异常：无显式业务异常；工作区不存在时 scalar_one() 会抛 SQLAlchemy 的 NoResultFound。
    调用约束：
    - 必须在读取“管理员数量/成员角色”之前调用，否则锁起不到串行化作用；
    - 行锁随事务提交或回滚释放，因此持锁期间不要做外部调用（HTTP/LLM）以免长时间占锁；
    - 依赖数据库行锁语义（本项目目标库为 MySQL）；SQLite 上等价于无操作，此处的保护会弱化，
      所以业务侧仍保留 protect_last_administrator 的二次判断。
    副作用：获取数据库行锁，可能阻塞其他并发权限变更请求。
    """
    ctx.db.execute(select(Workspace).where(Workspace.id == (workspace_id or ctx.workspace_id)).with_for_update()).scalar_one()


def protect_last_administrator(ctx: AuthContext, user_id: str) -> None:
    """Reject disabling, removing or demoting the final active workspace admin.

    用途：保证工作区始终保留至少一个“启用中的管理员”，避免把最后一个管理员停用、移除或
    降级后无人能再进入权限管理（自助锁死）。
    语义与判定方式：
    1. 先 lock_workspace 取工作区行锁，防止并发请求同时通过检查；
    2. 读取目标成员，非 admin 直接放行（降级/移除普通成员不受限）；
    3. 统计“同工作区的其他 admin 且 User.status == active”的记录，若一条都没有则拒绝。
    参数：ctx 已鉴权上下文；user_id 即将被停用/移除/降权的用户。返回：无。
    异常：ConflictError —— 目标已是最后一名启用中的管理员。
    调用约束：
    - 所有会减少管理员集合的动作都必须调用：user_service._set_roles（撤销管理员角色）、
      prepare_user_update（status=disabled）、remove_user，以及 lifecycle_service.transfer_member
      的 move 分支；
    - 停用全局账号时会跨工作区遍历校验（同一 User 可能是多个工作区的管理员）；
    - 必须在加锁之后、真正执行 delete/降权之前调用；
    - 本函数按 User.status 判断“启用中”，因此 disabled 用户不计入可用管理员。
    副作用：加行锁并读取成员表（只读，不写库）。
    """
    lock_workspace(ctx)
    member = member_record(ctx, user_id)
    if member.role != "admin":
        return
    another = ctx.db.scalar(select(WorkspaceMember.id).join(User, User.id == WorkspaceMember.user_id).where(
        WorkspaceMember.workspace_id == ctx.workspace_id, WorkspaceMember.role == "admin",
        WorkspaceMember.user_id != user_id, User.status == "active",
    ))
    if another is None:
        raise ConflictError("必须保留至少一个启用中的工作区管理员")


def require_global_account_control(ctx: AuthContext, user_id: str) -> None:
    """A local admin cannot reset/disable a shared identity in other workspaces.

    用途：Cloud Brain 身份（users 表）是跨工作区共享的，本地工作区管理员只能管理“只属于本
    工作区”的账号；若目标账号还加入了其他工作区，则停用/改密这类全局动作必须由那些工作区
    的管理员（或平台方）执行，防止越权影响其他工作区的登录。
    语义：本工作区的动作权限与人员数据范围由调用方先行校验，本函数只额外要求“目标账号加入的
    每一个工作区，当前操作人都是该工作区的 admin”。
    参数：ctx 已鉴权上下文；user_id 目标账号。返回：无。
    异常：PermissionErrorEx —— 目标账号仍属于调用人并非管理员的其它工作区。
    调用约束：仅用于账号级全局动作（改密码、改 status/display_name、撤销会话）；同一账号在
    本工作区内的角色、部门调整不需要通过本函数；操作人管理自己时直接放行。
    副作用：无（只读成员关系）。
    """
    if ctx.user_id == user_id:
        return
    target_workspaces = set(ctx.db.scalars(select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user_id,
    )).all())
    managed_workspaces = set(ctx.db.scalars(select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == ctx.user_id, WorkspaceMember.role == "admin",
    )).all())
    # The calling service has already checked its exact action and people scope
    # in this workspace. Only additional workspace memberships need admin power.
    # 中文补充：把当前工作区视为已覆盖，只有“额外加入的其他工作区”才要求操作人是其管理员；
    # 这里刻意不查 access_policy，避免绕回权限解析导致递归。
    managed_workspaces.add(ctx.workspace_id)
    if not target_workspaces.issubset(managed_workspaces):
        raise PermissionErrorEx("此账号还属于其他工作区，不能修改全局账号状态或密码；可调整本工作区角色与部门")
