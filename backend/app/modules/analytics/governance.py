"""Governed health evidence, schedules and bounded query leases for data sources."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.exc import OperationalError

from app.common.authorization import require_permission
from app.errors import AppError, ConflictError
from app.ids import new_id
from app.models import DataSource
from app.modules.analytics.connections import source_version
from app.modules.analytics.models import (
    DataSourceHealthCheck,
    DataSourcePolicy,
    DataSourceQueryLease,
    QueryHistory,
)
from app.modules.analytics.schemas import (
    DataSourceHealthView,
    DataSourcePolicyInput,
    DataSourcePolicyView,
)
from app.modules.system.service_helpers import commit_change
from app.modules.system_tools.models import ScheduledJob
from app.modules.system_tools.schedules import next_run


DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_DAILY_QUERY_LIMIT = 10000
DEFAULT_HEALTH_INTERVAL_MINUTES = 60
_TRANSIENT_LOCK_ERROR_CODES = {1205, 1213}
_LOCK_RETRY_DELAYS = (0.02, 0.05)


class DataSourceBusy(AppError):
    """并发租约已满（HTTP 429）：属于可重试的容量限制，不是数据源故障。"""

    status_code = 429
    code = "datasource_busy"


class DataSourceDailyLimit(AppError):
    """当日查询尝试次数达到治理上限（HTTP 429）；按 UTC 自然日重置。"""

    status_code = 429
    code = "datasource_daily_limit"


def add_default_policy(ctx, source: DataSource) -> DataSourcePolicy:
    """Attach policy defaults in the caller's transaction for newly created sources."""
    row = DataSourcePolicy(
        workspace_id=ctx.workspace_id,
        datasource_id=source.id,
        max_concurrency=DEFAULT_MAX_CONCURRENCY,
        daily_query_limit=DEFAULT_DAILY_QUERY_LIMIT,
        health_check_enabled=False,
        health_interval_minutes=DEFAULT_HEALTH_INTERVAL_MINUTES,
        created_by_user_id=ctx.user_id,
        updated_by_user_id=ctx.user_id,
    )
    ctx.db.add(row)
    return row


def _policy_record(ctx, source_id: str, *, lock: bool = False) -> DataSourcePolicy | None:
    """读取数据源治理策略；``lock=True`` 时加行锁，用于“读-改-写”与限额判定。

    加锁必须与源行锁一起按固定顺序获取（见 acquire_query_lease），否则并发大屏组件
    同时申请/释放租约时容易出现死锁。
    """
    statement = select(DataSourcePolicy).where(
        DataSourcePolicy.workspace_id == ctx.workspace_id,
        DataSourcePolicy.datasource_id == source_id,
    )
    if lock:
        statement = statement.with_for_update()
    return ctx.db.scalar(statement)


def _start_of_utc_day(now: datetime) -> datetime:
    """当日限额的起点：统一用 UTC 自然日，避免不同服务器时区导致限额提前/延后重置。"""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _usage_counts(ctx, source_id: str, *, cleanup: bool = False, lock: bool = False,
                  history_limit: int | None = None) -> tuple[int, int]:
    """统计 (当前活跃查询数, 当日查询尝试数)。

    活跃数来自未过期的查询租约（跨请求的并发事实来源，不是进程内计数，因此多进程
    部署也一致）；当日数来自查询历史行及尚未落历史的有效预留。历史与租约使用
    同一尝试 ID，避免预留提交到历史插入之间漏计，也避免历史插入后双计。
    ``cleanup=True`` 会先删除已过期租约，
    避免过期记录把并发名额长期占住。
    """
    now = datetime.utcnow()
    if cleanup:
        ctx.db.execute(delete(DataSourceQueryLease).where(
            DataSourceQueryLease.workspace_id == ctx.workspace_id,
            DataSourceQueryLease.datasource_id == source_id,
            DataSourceQueryLease.expires_at <= now,
        ))
    if lock:
        # MySQL RR 下权限读取可能已建立旧快照；父源锁之后仍须使用当前锁定读，
        # 不能让普通 count 查询漏掉刚提交的历史/预留。状态页继续使用下方聚合查询。
        history_ids = set()
        if history_limit != 0:
            statement = select(QueryHistory.id).where(
                QueryHistory.workspace_id == ctx.workspace_id,
                QueryHistory.datasource_id == source_id,
                QueryHistory.created_at >= _start_of_utc_day(now),
            ).with_for_update()
            # 预留判定只需知道是否达到上限；不限日额度时无需锁扫全天历史。
            if history_limit is not None:
                statement = statement.limit(history_limit)
            history_ids = set(ctx.db.scalars(statement).all())
        leases = ctx.db.execute(select(DataSourceQueryLease.id, DataSourceQueryLease.acquired_at).where(
            DataSourceQueryLease.workspace_id == ctx.workspace_id,
            DataSourceQueryLease.datasource_id == source_id,
            DataSourceQueryLease.expires_at > now,
        ).with_for_update()).all()
        reserved = sum(1 for lease_id, acquired_at in leases
                       if lease_id not in history_ids and acquired_at >= _start_of_utc_day(now))
        return len(leases), len(history_ids) + reserved
    active = int(ctx.db.scalar(select(func.count()).select_from(DataSourceQueryLease).where(
        DataSourceQueryLease.workspace_id == ctx.workspace_id,
        DataSourceQueryLease.datasource_id == source_id,
        DataSourceQueryLease.expires_at > now,
    )) or 0)
    today = int(ctx.db.scalar(select(func.count()).select_from(QueryHistory).where(
        QueryHistory.workspace_id == ctx.workspace_id,
        QueryHistory.datasource_id == source_id,
        QueryHistory.created_at >= _start_of_utc_day(now),
    )) or 0)
    reserved = int(ctx.db.scalar(select(func.count()).select_from(DataSourceQueryLease).where(
        DataSourceQueryLease.workspace_id == ctx.workspace_id,
        DataSourceQueryLease.datasource_id == source_id,
        DataSourceQueryLease.expires_at > now,
        DataSourceQueryLease.acquired_at >= _start_of_utc_day(now),
        ~select(QueryHistory.id).where(QueryHistory.id == DataSourceQueryLease.id).exists(),
    )) or 0)
    return active, today + reserved


def _schedule(ctx, policy: DataSourcePolicy | None) -> ScheduledJob | None:
    """取策略关联的健康检查调度任务；跨工作区或已删除的调度一律视为不存在。"""
    if policy is None or not policy.health_schedule_id:
        return None
    row = ctx.db.get(ScheduledJob, policy.health_schedule_id)
    if row is None or row.workspace_id != ctx.workspace_id:
        return None
    return row


def policy_view(ctx, source: DataSource, policy: DataSourcePolicy | None = None) -> DataSourcePolicyView:
    """组装策略视图：限额、调度状态与**真实**资源占用。

    ``activeQueries``/``queriesToday`` 来自租约与查询历史（会顺带清理过期租约），
    展示的活跃数是跨请求的真实值；没有策略记录时回落到默认值，让界面显示“未配置但
    有默认约束”而不是 0 上限。
    """
    policy = policy if policy is not None else _policy_record(ctx, source.id)
    schedule = _schedule(ctx, policy)
    active, today = _usage_counts(ctx, source.id, cleanup=True)
    return DataSourcePolicyView(
        datasourceId=source.id,
        version=policy.version if policy else 0,
        maxConcurrency=policy.max_concurrency if policy else DEFAULT_MAX_CONCURRENCY,
        dailyQueryLimit=policy.daily_query_limit if policy else DEFAULT_DAILY_QUERY_LIMIT,
        healthCheckEnabled=bool(policy and policy.health_check_enabled),
        healthIntervalMinutes=(
            policy.health_interval_minutes if policy else DEFAULT_HEALTH_INTERVAL_MINUTES
        ),
        healthScheduleId=schedule.id if schedule else "",
        scheduleStatus=schedule.status if schedule else "not_configured",
        nextHealthCheckAt=(schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else ""),
        activeQueries=active,
        queriesToday=today,
    )


def get_policy(ctx, source_id: str) -> DataSourcePolicyView:
    """读取数据源治理策略（GET /datasources/{id}/policy），沿数据源查看权限与范围。

    副作用：会清理该数据源的过期租约（因此是“读”但可能写库），不改变限额配置。
    """
    from app.modules.analytics.sources import get_source

    source = get_source(ctx, source_id, "datasources.view")
    return policy_view(ctx, source)


def _reconcile_schedule(ctx, source: DataSource, policy: DataSourcePolicy) -> ScheduledJob | None:
    """按策略创建或更新统一调度中心的健康检查任务。

    启用健康检查但没有调度时创建（间隔取 healthIntervalMinutes）；已有调度则同步名称、
    间隔与状态，并在数据源停用或关闭健康检查时暂停。创建后只 flush 该调度行：
    整会话 flush 会让策略的乐观锁版本被推进两次，导致前端提交的版本号立刻过期。
    """
    schedule = _schedule(ctx, policy)
    if policy.health_check_enabled and schedule is None:
        schedule = ScheduledJob(
            id=new_id(),
            workspace_id=ctx.workspace_id,
            code="datasource-health-" + source.id,
            name="数据源健康检查 · " + source.name,
            description="按数据源治理策略执行真实只读连接检查并保留脱敏历史。",
            task_type="datasource_health",
            target_id=source.id,
            schedule_kind="interval",
            schedule_expression=str(policy.health_interval_minutes * 60),
            timezone="Asia/Shanghai",
            payload={},
            status="active" if source.status == "active" else "paused",
            created_by=ctx.user_id,
        )
        if schedule.status == "active":
            schedule.next_run_at = next_run(
                schedule.schedule_kind, schedule.schedule_expression, schedule.timezone,
            )
        ctx.db.add(schedule)
        # Flush only the referenced job first. Flushing the whole session here
        # would advance the policy's optimistic version twice.
        ctx.db.flush([schedule])
        policy.health_schedule_id = schedule.id
    elif schedule is not None:
        schedule.name = "数据源健康检查 · " + source.name
        schedule.schedule_expression = str(policy.health_interval_minutes * 60)
        schedule.status = "active" if policy.health_check_enabled and source.status == "active" else "paused"
        schedule.next_run_at = (
            next_run(schedule.schedule_kind, schedule.schedule_expression, schedule.timezone)
            if schedule.status == "active" else None
        )
    return schedule


def update_policy(ctx, source_id: str, payload: DataSourcePolicyInput) -> DataSourcePolicyView:
    """保存数据源治理策略（PUT /datasources/{id}/policy）。

    权限：改并发/日限额需要 ``datasources.edit``；启用自动健康检查额外需要
    ``datasources.execute`` 与 ``system.operations.manage``（会在无人值守时持续
    访问目标系统，属于运维级授权）。并发控制：首次保存必须提交 version=0，
    已有策略必须提交当前版本，否则 409。副作用：写策略、协调调度任务、提交审计。
    """
    from app.modules.analytics.sources import get_source

    source = get_source(ctx, source_id, "datasources.edit")
    if payload.healthCheckEnabled:
        require_permission(ctx, "datasources.execute")
        require_permission(ctx, "system.operations.manage")
    policy = _policy_record(ctx, source.id, lock=True)
    if policy is None:
        if payload.version != 0:
            raise ConflictError("数据源治理策略尚未创建，请刷新后重试", current_version=0)
        policy = DataSourcePolicy(
            workspace_id=ctx.workspace_id,
            datasource_id=source.id,
            created_by_user_id=ctx.user_id,
            updated_by_user_id=ctx.user_id,
        )
        ctx.db.add(policy)
    elif policy.version != payload.version:
        raise ConflictError("数据源治理策略已修改，请刷新后重试", current_version=policy.version)
    policy.max_concurrency = payload.maxConcurrency
    policy.daily_query_limit = payload.dailyQueryLimit
    policy.health_check_enabled = payload.healthCheckEnabled
    policy.health_interval_minutes = payload.healthIntervalMinutes
    policy.updated_by_user_id = ctx.user_id
    _reconcile_schedule(ctx, source, policy)
    ctx.db.flush()
    commit_change(
        ctx, "datasource.policy.update", "datasources", source.id,
        {
            "max_concurrency": policy.max_concurrency,
            "daily_query_limit": policy.daily_query_limit,
            "health_check_enabled": policy.health_check_enabled,
            "health_interval_minutes": policy.health_interval_minutes,
        },
    )
    ctx.db.refresh(policy)
    return policy_view(ctx, source, policy)


def reconcile_source_schedule(ctx, source: DataSource) -> None:
    """Pause or resume an existing generated schedule after source state changes."""
    policy = _policy_record(ctx, source.id, lock=True)
    if policy is None or not policy.health_schedule_id:
        return
    _reconcile_schedule(ctx, source, policy)
    commit_change(
        ctx, "datasource.policy.reconcile", "datasources", source.id,
        {"source_status": source.status, "health_check_enabled": policy.health_check_enabled},
    )


def health_view(row: DataSourceHealthCheck) -> DataSourceHealthView:
    """健康检查记录的对外视图；只含脱敏 detail 与错误码，不含连接串或驱动堆栈。"""
    return DataSourceHealthView(
        id=row.id,
        datasourceId=row.datasource_id,
        datasourceVersion=row.datasource_version,
        triggerType=row.trigger_type,
        status=row.status,
        latencyMs=row.latency_ms,
        detail=row.detail,
        errorCode=row.error_code,
        checkedAt=row.checked_at.isoformat(),
    )


def run_health_check(
    ctx, source_id: str, *, trigger_type: str = "manual",
) -> DataSourceHealthView:
    """Run one actual connection probe and persist redacted success or failure evidence."""
    from app.modules.analytics import sources

    source = sources.get_source(ctx, source_id, "datasources.execute", active=True)
    started = time.monotonic()
    failure: AppError | None = None
    try:
        result = sources.test_source(ctx, source_id=source.id)
        status, detail, error_code = "healthy", str(result.get("detail") or "只读连接测试成功"), ""
    except AppError as exc:
        failure = exc
        ctx.db.rollback()
        source = sources.get_source(ctx, source_id, "datasources.execute", active=True)
        status, detail, error_code = "unhealthy", exc.message[:1000], exc.code
    row = DataSourceHealthCheck(
        id=new_id(),
        workspace_id=ctx.workspace_id,
        datasource_id=source.id,
        datasource_version=source_version(source),
        trigger_type=trigger_type,
        status=status,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        detail=detail[:1000],
        error_code=error_code[:80],
        checked_by_user_id=ctx.user_id,
        checked_at=datetime.utcnow(),
    )
    ctx.db.add(row)
    ctx.db.commit()
    view = health_view(row)
    if failure is not None:
        failure.extra.setdefault("health_check_id", row.id)
        raise failure
    return view


def list_health_checks(ctx, source_id: str, limit: int = 50) -> list[DataSourceHealthView]:
    """列出某数据源的健康检查历史（GET /datasources/{id}/health-checks）。

    权限：沿数据源查看范围；不返回连接串、驱动堆栈或密码，失败记录同样保留。
    按 checked_at + id 倒序，同秒内创建的多条记录顺序稳定。
    """
    from app.modules.analytics.sources import get_source

    get_source(ctx, source_id, "datasources.view")
    rows = ctx.db.scalars(select(DataSourceHealthCheck).where(
        DataSourceHealthCheck.workspace_id == ctx.workspace_id,
        DataSourceHealthCheck.datasource_id == source_id,
    ).order_by(DataSourceHealthCheck.checked_at.desc(), DataSourceHealthCheck.id.desc()).limit(limit)).all()
    return [health_view(row) for row in rows]


def latest_health_checks(ctx, source_ids: list[str]) -> dict[str, DataSourceHealthCheck]:
    """批量取每个数据源**最新一条**健康检查，返回 {source_id: row}。

    用“不存在更新的同源记录”这一 NOT EXISTS 条件在数据库内取最新，避免把全部历史
    读进内存再挑；同秒记录用 id 打破平局，保证结果稳定。
    """
    if not source_ids:
        return {}
    newer = aliased(DataSourceHealthCheck)
    newer_exists = select(newer.id).where(
        newer.workspace_id == DataSourceHealthCheck.workspace_id,
        newer.datasource_id == DataSourceHealthCheck.datasource_id,
        or_(newer.checked_at > DataSourceHealthCheck.checked_at, and_(
            newer.checked_at == DataSourceHealthCheck.checked_at, newer.id > DataSourceHealthCheck.id,
        )),
    ).exists()
    rows = ctx.db.scalars(select(DataSourceHealthCheck).where(
        DataSourceHealthCheck.workspace_id == ctx.workspace_id,
        DataSourceHealthCheck.datasource_id.in_(source_ids),
        ~newer_exists,
    ).order_by(DataSourceHealthCheck.checked_at.desc(), DataSourceHealthCheck.id.desc())).all()
    result: dict[str, DataSourceHealthCheck] = {}
    for row in rows:
        result.setdefault(row.datasource_id, row)
    return result


def _is_transient_lock_error(exc: OperationalError) -> bool:
    """判断 MySQL 死锁/锁等待超时（1205/1213 是**MySQL 错误码**，不是 SQLSTATE）。

    这两类错误滚回整笔事务后可以安全重试；其余 OperationalError 必须原样抛出，
    否则会把真实故障（例如账号/网络问题）伪装成容量繁忙。
    """
    args = getattr(getattr(exc, "orig", None), "args", ())
    try:
        return bool(args) and int(args[0]) in _TRANSIENT_LOCK_ERROR_CODES
    except (TypeError, ValueError):
        return False


def _reserve_query_lease(ctx, source_id: str, timeout: int) -> str:
    """Reserve one slot while holding the source lock for this transaction."""
    now = datetime.utcnow()
    ctx.db.execute(select(DataSource.id).where(
        DataSource.id == source_id,
        DataSource.workspace_id == ctx.workspace_id,
    ).with_for_update()).scalar_one()
    policy = _policy_record(ctx, source_id, lock=True)
    max_concurrency = policy.max_concurrency if policy else DEFAULT_MAX_CONCURRENCY
    daily_limit = policy.daily_query_limit if policy else DEFAULT_DAILY_QUERY_LIMIT
    active, today = _usage_counts(ctx, source_id, cleanup=True, lock=True, history_limit=daily_limit)
    if active >= max_concurrency:
        ctx.db.rollback()
        raise DataSourceBusy(
            "当前数据源查询已达到并发上限，请稍后重试",
            datasource_id=source_id, max_concurrency=max_concurrency,
        )
    if daily_limit and today >= daily_limit:
        ctx.db.rollback()
        raise DataSourceDailyLimit(
            "当前数据源今日查询次数已达到治理上限",
            datasource_id=source_id, daily_query_limit=daily_limit,
        )
    lease = DataSourceQueryLease(
        id=new_id(), workspace_id=ctx.workspace_id, datasource_id=source_id,
        user_id=ctx.user_id, acquired_at=now,
        expires_at=now + timedelta(seconds=max(120, min(900, timeout + 120))),
    )
    ctx.db.add(lease)
    ctx.db.commit()
    return lease.id


def acquire_query_lease(ctx, source: DataSource) -> str:
    """Atomically reserve one cross-request query slot before model or DB work.

    MySQL may choose either transaction as the deadlock victim while dashboard
    widgets acquire and release leases concurrently.  A lease insert is safe to
    retry after MySQL rolls that transaction back; retries are deliberately
    short and bounded so a real capacity limit still returns ``429``.
    """
    source_id = source.id
    timeout = int((source.connection_config or {}).get("timeoutSeconds") or 15)
    for attempt in range(len(_LOCK_RETRY_DELAYS) + 1):
        try:
            return _reserve_query_lease(ctx, source_id, timeout)
        except OperationalError as exc:
            ctx.db.rollback()
            if not _is_transient_lock_error(exc):
                raise
            if attempt >= len(_LOCK_RETRY_DELAYS):
                raise DataSourceBusy(
                    "当前数据源查询排队发生冲突，请稍后重试",
                    datasource_id=source_id, retryable=True,
                ) from exc
            time.sleep(_LOCK_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


def validate_query_lease(ctx, source: DataSource, lease_id: str) -> None:
    """派发 SQL 前验证本次租约；过期直接拒绝，不重取、不重放业务查询。"""
    from app.modules.runtime.task_control import checkpoint
    checkpoint()
    # 与取得/释放保持父记录在先的锁顺序；刷新的是租约窗口，绝不扩大驱动 SQL 超时。
    ctx.db.execute(select(DataSource.id).where(
        DataSource.id == source.id, DataSource.workspace_id == ctx.workspace_id,
    ).with_for_update()).scalar_one()
    lease = ctx.db.scalar(select(DataSourceQueryLease).where(
        DataSourceQueryLease.id == lease_id,
        DataSourceQueryLease.workspace_id == ctx.workspace_id,
        DataSourceQueryLease.datasource_id == source.id,
        DataSourceQueryLease.user_id == ctx.user_id,
    ).with_for_update().execution_options(populate_existing=True))
    now = datetime.utcnow()
    if lease is None or lease.expires_at <= now:
        ctx.db.rollback()
        raise DataSourceBusy("本次查询准备期间租约已失效，尚未执行 SQL，请重新发起查询",
                             datasource_id=source.id, lease_expired=True)
    timeout = int((source.connection_config or {}).get("timeoutSeconds") or 15)
    lease.expires_at = now + timedelta(seconds=max(120, min(900, timeout + 120)))
    ctx.db.commit()
    checkpoint()


def release_query_lease(ctx, lease_id: str, source_id: str) -> None:
    """Release with the same parent-first lock order used by acquisition.

    Cleanup must never mask a successful query.  Transient MySQL lock failures
    are retried; a final cleanup failure leaves only the expiring lease record.
    """
    for attempt in range(len(_LOCK_RETRY_DELAYS) + 1):
        try:
            ctx.db.execute(select(DataSource.id).where(
                DataSource.id == source_id,
                DataSource.workspace_id == ctx.workspace_id,
            ).with_for_update()).scalar_one_or_none()
            ctx.db.execute(delete(DataSourceQueryLease).where(
                DataSourceQueryLease.id == lease_id,
                DataSourceQueryLease.workspace_id == ctx.workspace_id,
                DataSourceQueryLease.datasource_id == source_id,
            ))
            ctx.db.commit()
            return
        except OperationalError as exc:
            ctx.db.rollback()
            if _is_transient_lock_error(exc) and attempt < len(_LOCK_RETRY_DELAYS):
                time.sleep(_LOCK_RETRY_DELAYS[attempt])
                continue
            return
        except Exception:
            ctx.db.rollback()
            return


def resource_status(ctx, source_id: str) -> dict:
    """查询数据源的资源占用概览（GET /datasources/{id}/pool-status）。

    权限：沿 datasources.view。返回内容混合两类口径，不要混淆：
      连接池信息（poolStatus）是**进程内**的真实连接占用，且只有 MySQL/Postgres
      走 QueuePool（其余为 NullPool，poolSize 恒为 0）；
      并发/配额信息（activeQueries、queriesToday、usageRate）来自跨请求的查询租约与
      当日查询历史，这才是真正的并发上限来源。副作用：会清理过期租约并提交。
    """
    from app.modules.analytics.connection_pool import pool_status
    from app.modules.analytics.sources import get_source

    source = get_source(ctx, source_id, "datasources.view")
    policy = _policy_record(ctx, source.id)
    active, today = _usage_counts(ctx, source.id, cleanup=True)
    maximum = policy.max_concurrency if policy else DEFAULT_MAX_CONCURRENCY
    daily = policy.daily_query_limit if policy else DEFAULT_DAILY_QUERY_LIMIT
    ctx.db.commit()
    return {
        "provider": source.provider,
        **pool_status(source),
        "timeoutSeconds": int((source.connection_config or {}).get("timeoutSeconds") or 15),
        "activeQueries": active,
        "maxConcurrency": maximum,
        "usageRate": round(active / maximum * 100, 1) if maximum else 0,
        "queriesToday": today,
        "dailyQueryLimit": daily,
        "dailyUsageRate": round(today / daily * 100, 1) if daily else 0,
    }
