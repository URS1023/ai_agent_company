"""Validated schedules, pinned business targets and idempotent execution records.

中文补充（计划任务调度）：
- 定位：系统运维的计划任务（工作流/预警、知识抽取、通知发布、健康检查、数据源体检）
  的配置、派发与运行记录。
- 幂等与互斥：每次触发用 ``schedule-<jobId>-<窗口时间>`` 作为 idempotency_key，
  数据库唯一约束保证同一窗口只跑一次；run_job 在**产生任何外部副作用前**先提交
  运行记录占位（Reserve the unique key），因此进程崩溃也不会重复执行外部动作。
- 钉住语义：任务固定绑定的工作流修订（publishedRevision）不会因工作流再次发布而漂移，
  代码只刷新编辑态 version，绝不悄悄跟随新的已发布版本。
- 执行身份：以创建者身份执行，但每次派发都重新按当前角色校验
  system.operations.manage；创建者被停用、退出工作区或失去权限时任务自动暂停
  （见 _pause_due_job），而不是继续以旧身份执行。
- 失败隔离：run_due_jobs 逐个任务独立事务，单个任务异常不会阻塞其它到期任务；
  持久化的调度配置损坏也会转成一条失败运行记录并暂停任务，避免每个 tick 反复失败。
- 权限：查看 system.operations.view，管理与执行 system.operations.manage。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError as ContractError
from sqlalchemy import or_, select

from app.common.authorization import require_permission
from app.common.deps import AuthContext, _workspace_is_active
from app.errors import ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.models import User, Workspace, WorkspaceMember
from app.modules.system.service_helpers import check_version, commit_change, flush_changes, scoped_record

from .models import ScheduledJob, ScheduledJobRun, SystemNotice
from .schemas import JobInput


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串（None 保持 None）。"""
    return value.isoformat() if value else None


def _field_values(expression: str, minimum: int, maximum: int, *, sunday: bool = False) -> set[int]:
    """解析单个 cron 字段（支持 ``*`` / 逗号 / 范围 / 步长），返回取值集合。

    这是自实现的 cron 解析器（不依赖外部库），所有非法写法都抛 ValidationError：
    空项、非整数、步长越界、范围颠倒等；``sunday=True`` 时把 7 归一化为 0
    （cron 里周日可写 0 或 7，两种写法必须等价）。
    """
    def parse_number(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("cron 字段只能包含整数、范围、逗号或步长") from exc

    values: set[int] = set()
    for part in expression.split(","):
        part = part.strip()
        if not part:
            raise ValidationError("cron 字段不能包含空项")
        base, slash, step_text = part.partition("/")
        if slash and not step_text:
            raise ValidationError("cron 步长必须是整数")
        step = parse_number(step_text) if slash else 1
        if step < 1 or step > maximum - minimum + 1:
            raise ValidationError("cron 步长超出允许范围")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            left, right = base.split("-", 1)
            if not left or not right:
                raise ValidationError("cron 范围必须包含起止整数")
            start, end = parse_number(left), parse_number(right)
        else:
            start = end = parse_number(base)
        if start < minimum or start > maximum or end < minimum or end > maximum or start > end:
            raise ValidationError("cron 数值或范围无效")
        values.update(range(start, end + 1, step))
    return {0 if sunday and item == 7 else item for item in values}


def next_run(schedule_kind: str, expression: str, timezone_name: str, now: datetime | None = None) -> datetime:
    """Return a naive UTC timestamp to match the platform database contract.

    中文补充：计算下一次执行时间。``interval`` 只接受 60–31536000 秒（防止误配
    "每秒一次"把系统打满）；``cron`` 为五段格式，按指定 IANA 时区解释，
    计算时从"当前分钟 +1"起逐分钟推进并最多扫描一年，找不到就报错
    （例如 2 月 30 日这类不可能组合）。
    返回：naive UTC 时间（与数据库时间字段口径一致，调用方不要再套时区）。
    异常：时区名非法、调度类型非法、cron 格式非法或一年内无解均抛 ValidationError。
    """
    now = now or datetime.utcnow()
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError("时区名称无效，请使用 IANA 名称，例如 Asia/Shanghai") from exc
    if schedule_kind == "interval":
        try:
            seconds = int(expression)
        except (TypeError, ValueError) as exc:
            raise ValidationError("间隔调度必须填写整数秒数") from exc
        if seconds < 60 or seconds > 31536000:
            raise ValidationError("间隔秒数必须在 60 到 31536000 之间")
        return now + timedelta(seconds=seconds)
    if schedule_kind != "cron":
        raise ValidationError("调度类型必须是 interval 或 cron")
    fields = expression.split()
    if len(fields) != 5:
        raise ValidationError("cron 必须是五段格式：分 时 日 月 星期")
    minutes = _field_values(fields[0], 0, 59)
    hours = _field_values(fields[1], 0, 23)
    days = _field_values(fields[2], 1, 31)
    months = _field_values(fields[3], 1, 12)
    weekdays = _field_values(fields[4], 0, 7, sunday=True)
    candidate = now.replace(tzinfo=timezone.utc).astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 366):
        cron_weekday = (candidate.weekday() + 1) % 7
        if (candidate.minute in minutes and candidate.hour in hours and candidate.day in days
                and candidate.month in months and cron_weekday in weekdays):
            return candidate.astimezone(timezone.utc).replace(tzinfo=None)
        candidate += timedelta(minutes=1)
    raise ValidationError("一年内找不到下一次 cron 执行时间，请检查日期组合")


def _workflow_run_input(values: dict):
    """把调度参数校验成工作流运行契约（RunInput），并强制"只能跑已发布版本"。

    idempotencyKey 固定填 "schedule-validation"：这里只是校验，真正的幂等键在派发时
    由 run_job 按窗口生成。异常：不符合契约或非 published 模式抛 ValidationError。
    """
    from app.modules.workflows.schemas import RunInput
    arguments = {key: value for key, value in values.items() if key != "publishedRevision"}
    try:
        parsed = RunInput.model_validate({**arguments, "idempotencyKey": "schedule-validation"})
    except ContractError as exc:
        raise ValidationError("调度运行参数不符合工作流契约") from exc
    if parsed.mode != "published":
        raise ValidationError("调度只能运行已发布工作流，不能运行草稿")
    return parsed


def _published_binding(ctx, workflow, number: int) -> int:
    """确认要绑定的工作流修订确实是"已发布"版本，返回该修订号。

    兼容三类历史数据：修订快照里带 published=True、当前 published_revision 指向它、
    或旧版本（快照里没有 published 字段）且 reason 属于服务端生成发布记录的已知文案。
    判定不通过即抛 ValidationError，要求重新保存调度——避免把草稿修订排进定时执行。
    """
    from app.modules.workflows.service import revision
    if type(number) is not int or number < 1:
        raise ValidationError("调度必须绑定有效的已发布修订")
    bound = revision(ctx, workflow, number)
    # Old snapshots predate the explicit publication flag. Accept only known
    # server-authored release records or the current published pointer.
    historical_release = bound.reason in {"发布版本", "设备预警向导创建并发布", "质量检查向导创建并发布"}
    if not (bound.snapshot.get("published") is True or workflow.published_revision == number
            or ("published" not in bound.snapshot and historical_release)):
        raise ValidationError("调度绑定的修订没有发布记录，请重新保存调度")
    return number


def _validate_target(ctx: AuthContext, payload: JobInput) -> dict | None:
    """按任务类型校验目标与参数，返回规范化后的 payload（health 类型返回 None）。

    各类型的约束：workflow/alert 必须是已发布工作流（alert 还要求含 alert 业务域）
    且运行参数通过 RunInput 校验；extraction 要求知识抽取任务可见；
    notification 只接受本工作区**草稿**公告且参数只能含该公告当前 version；
    health 不允许目标与自定义参数；datasource_health 要求数据源启用且具
    datasources.execute 权限、不接受自定义参数。
    异常：任一不满足抛 ValidationError；资源不可见由下层抛 NotFoundError。
    """
    if payload.task_type in {"workflow", "alert"}:
        from app.modules.workflows.service import get_workflow

        if not payload.target_id:
            raise ValidationError("工作流或预警调度必须选择已发布工作流")
        row = get_workflow(ctx, payload.target_id, "workflows.view")
        if row.status != "published" or row.published_revision is None:
            raise ValidationError("调度只能固定到已发布工作流")
        if payload.task_type == "alert" and "alert" not in row.domains:
            raise ValidationError("设备预警调度必须选择包含 alert 业务域的工作流")
        run_input = _workflow_run_input(payload.payload)
        check_version(row, run_input.version)
        pinned = _published_binding(ctx, row, payload.payload.get("publishedRevision", row.published_revision))
        return {**payload.payload, "publishedRevision": pinned}
    elif payload.task_type == "extraction":
        from app.modules.knowledge_graph.extraction import get_job
        from app.modules.knowledge_graph.schemas import ExtractionExecuteInput

        if not payload.target_id:
            raise ValidationError("知识抽取调度必须选择任务")
        get_job(ctx, payload.target_id, "extraction.view")
        ExtractionExecuteInput.model_validate(payload.payload)
    elif payload.task_type == "notification":
        if not payload.target_id:
            raise ValidationError("通知调度必须选择草稿公告")
        notice = ctx.db.scalar(select(SystemNotice).where(
            SystemNotice.id == payload.target_id, SystemNotice.workspace_id == ctx.workspace_id,
        ))
        if notice is None or notice.status != "draft":
            raise ValidationError("通知调度只能选择当前工作区的草稿公告")
        if payload.payload != {"version": notice.version}:
            raise ValidationError("通知调度参数必须只包含公告当前 version")
    elif payload.task_type == "health":
        if payload.target_id or payload.payload:
            raise ValidationError("健康检查任务不接受目标或自定义参数")
    elif payload.task_type == "datasource_health":
        from app.modules.analytics.sources import get_source

        if not payload.target_id:
            raise ValidationError("数据源健康检查必须选择数据源")
        get_source(ctx, payload.target_id, "datasources.execute", active=True)
        if payload.payload:
            raise ValidationError("数据源健康检查不接受自定义参数")
    else:
        raise ValidationError("任务类型未登记")


def job_view(row: ScheduledJob) -> dict:
    """计划任务视图（camelCase；payload 是已规范化的目标参数，nextRunAt 为 UTC naive）。"""
    return {
        "id": row.id, "code": row.code, "name": row.name, "description": row.description,
        "taskType": row.task_type, "targetId": row.target_id,
        "scheduleKind": row.schedule_kind, "scheduleExpression": row.schedule_expression,
        "timezone": row.timezone, "payload": row.payload, "status": row.status,
        "nextRunAt": _iso(row.next_run_at), "lastRunAt": _iso(row.last_run_at),
        "createdBy": row.created_by, "version": row.version,
        "createTime": _iso(row.created_at), "updateTime": _iso(row.updated_at),
    }


def list_jobs(ctx: AuthContext, keyword: str = "", status: str = "") -> list[dict]:
    """列出本工作区计划任务（不含已归档）；权限 system.operations.view。

    keyword 转义通配符；status 精确筛选（active/paused 等）。
    """
    require_permission(ctx, "system.operations.view")
    statement = select(ScheduledJob).where(
        ScheduledJob.workspace_id == ctx.workspace_id, ScheduledJob.status != "archived",
    )
    if keyword:
        statement = statement.where(or_(
            ScheduledJob.name.contains(keyword, autoescape=True),
            ScheduledJob.code.contains(keyword, autoescape=True),
        ))
    if status:
        statement = statement.where(ScheduledJob.status == status)
    return [job_view(row) for row in ctx.db.scalars(
        statement.order_by(ScheduledJob.updated_at.desc(), ScheduledJob.id)
    ).all()]


def save_job(ctx: AuthContext, payload: JobInput, record_id: str | None = None) -> dict:
    """新建或修改计划任务；权限 system.operations.manage。

    保存时即完成"目标校验 + 下次执行时间计算"，因此保存后要么是可用任务，
    要么直接报错（不落库）。新建要求 version=0；修改用版本乐观锁，归档任务不可修改；
    只有 active 状态才写入 next_run_at（暂停任务不会在背后被排期）。
    副作用：写库 + 审计日志（记录 taskType 与 status）。
    """
    require_permission(ctx, "system.operations.manage")
    normalized_payload = _validate_target(ctx, payload)
    calculated = next_run(payload.schedule_kind, payload.schedule_expression, payload.timezone)
    if record_id:
        row = scoped_record(ctx, ScheduledJob, record_id)
        check_version(row, payload.version)
        if row.status == "archived":
            raise ConflictError("归档任务不能修改")
        row.version += 1
    else:
        if payload.version:
            raise ValidationError("创建调度任务时 version 必须为 0")
        row = ScheduledJob(workspace_id=ctx.workspace_id, created_by=ctx.user_id)
        ctx.db.add(row)
    values = payload.model_dump(exclude={"version"})
    if normalized_payload is not None:
        values["payload"] = normalized_payload
    for key, value in values.items():
        setattr(row, key, value)
    row.next_run_at = calculated if row.status == "active" else None
    flush_changes(ctx)
    commit_change(ctx, "system.schedule.save", "scheduled_job", row.id,
                  {"taskType": row.task_type, "status": row.status})
    return job_view(row)


def change_job(ctx: AuthContext, record_id: str, version: int, action: str) -> dict:
    """切换任务状态：activate（重新计算下次执行）/ pause / archive；权限 system.operations.manage。

    停止类动作会把 next_run_at 置空，确保调度轮询不会再捞到它；归档后不可再启用或修改。
    异常：动作非法抛 ValidationError，归档任务启用抛 ConflictError。
    """
    require_permission(ctx, "system.operations.manage")
    row = scoped_record(ctx, ScheduledJob, record_id)
    check_version(row, version)
    if action == "activate":
        if row.status == "archived":
            raise ConflictError("归档任务不能启用")
        row.status = "active"
        row.next_run_at = next_run(row.schedule_kind, row.schedule_expression, row.timezone)
    elif action == "pause":
        row.status, row.next_run_at = "paused", None
    elif action == "archive":
        row.status, row.next_run_at = "archived", None
    else:
        raise ValidationError("任务状态操作无效")
    row.version += 1
    commit_change(ctx, "system.schedule." + action, "scheduled_job", row.id)
    return job_view(row)


def run_view(row: ScheduledJobRun) -> dict:
    """运行记录视图（含幂等键、触发类型与输出摘要；输出已由 _bounded_output 限长）。"""
    return {
        "id": row.id, "jobId": row.job_id, "triggeredBy": row.triggered_by,
        "triggerType": row.trigger_type, "idempotencyKey": row.idempotency_key,
        "status": row.status, "scheduledFor": _iso(row.scheduled_for),
        "startedAt": _iso(row.started_at), "completedAt": _iso(row.completed_at),
        "output": row.output, "errorSummary": row.error_summary, "createTime": _iso(row.created_at),
    }


def _bounded_output(value) -> dict:
    """把任务输出裁剪成可入库的 dict：超过 50 KB 时改为保留大小与前 10000 字符预览。

    计划任务的输出会长期留在数据库里，因此必须限长；截断时显式标记 truncated=True，
    避免读者把预览当成完整结果。
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    elif not isinstance(value, dict):
        value = {"result": value}
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) > 50000:
        return {"truncated": True, "size": len(encoded), "preview": encoded[:10000]}
    return json.loads(encoded)


def _dispatch(ctx: AuthContext, row: ScheduledJob, run_key: str):
    """按任务类型派发到具体业务服务，返回其结果（供 run_view 记录）。

    工作流类任务在派发时重新解析钉住的发布修订，并只用 run_key 覆盖幂等键，
    绝不把版本切到"最新已发布"；其它类型分别调用抽取执行、公告发布（一次性动作）、
    健康检查与数据源体检。异常：任务类型未登记抛 ValidationError；
    具体业务异常由调用方（run_job）捕获并记入运行记录。
    """
    if row.task_type in {"workflow", "alert"}:
        from app.modules.workflows.execution import start
        from app.modules.workflows.service import get_workflow

        payload = _workflow_run_input(row.payload)
        workflow = get_workflow(ctx, row.target_id, "workflows.execute")
        pinned = _published_binding(ctx, workflow, row.payload.get("publishedRevision", payload.version))
        # The editor's optimistic version and the immutable release are two
        # different contracts. Refresh only the former; never follow a new
        # published graph without an explicit schedule update.
        payload = payload.model_copy(update={"version": workflow.version, "idempotency_key": run_key[:100]})
        return start(ctx, row.target_id, payload, published_revision=pinned)
    if row.task_type == "extraction":
        from app.modules.knowledge_graph.extraction import execute_job
        from app.modules.knowledge_graph.schemas import ExtractionExecuteInput

        return execute_job(ctx, row.target_id, ExtractionExecuteInput.model_validate(row.payload))
    if row.task_type == "notification":
        from .notifications import publish_notice

        return publish_notice(ctx, row.target_id, int(row.payload["version"]))
    if row.task_type == "health":
        from .monitoring import health_snapshot

        return health_snapshot(ctx)
    if row.task_type == "datasource_health":
        from app.modules.analytics.governance import run_health_check

        return run_health_check(ctx, row.target_id, trigger_type="schedule")
    raise ValidationError("任务类型未登记")


def run_job(ctx: AuthContext, record_id: str, version: int, idempotency_key: str,
            *, trigger_type: str = "manual", scheduled_for: datetime | None = None) -> dict:
    """执行一次计划任务（手动或定时触发）；权限 system.operations.manage。

    幂等协议：先用 idempotency_key 查既有运行——
    - 命中且属于同一任务：直接返回那条运行视图（不重复执行）；
    - 命中但属于别的任务：ConflictError（唯一键复用是明显错误）；
    然后写入 status=running 的运行记录并**先提交**，把唯一键占住之后再执行外部动作，
    因此崩溃/重启不会造成重复派发。
    执行结果：工作流保留真实 completed/failed/waiting/cancelled 状态，其余动作成功写 completed；
    失败写 failed + 错误摘要（异常不外抛，
    避免调度轮询被单个任务打断）；通知发布成功后任务自动暂停（公告是"一次性"动作）。
    调度配置损坏时同样转成失败运行并把任务暂停，防止每个 tick 重复失败。
    副作用：写运行记录、更新任务状态与 next_run_at，并触发对应业务动作。
    """
    require_permission(ctx, "system.operations.manage")
    if not _workspace_is_active(ctx.db, ctx.workspace):
        raise ConflictError("工作区或所属组织已归档，不能派发计划任务")
    row = scoped_record(ctx, ScheduledJob, record_id)
    check_version(row, version)
    if row.status == "archived":
        raise ConflictError("归档任务不能执行")
    previous = ctx.db.scalar(select(ScheduledJobRun).where(
        ScheduledJobRun.workspace_id == ctx.workspace_id,
        ScheduledJobRun.idempotency_key == idempotency_key,
    ))
    if previous:
        if previous.job_id != row.id:
            raise ConflictError("此执行唯一键已用于其他调度任务")
        return run_view(previous)
    run = ScheduledJobRun(
        workspace_id=ctx.workspace_id, job_id=row.id, triggered_by=ctx.user_id,
        trigger_type=trigger_type, idempotency_key=idempotency_key,
        scheduled_for=scheduled_for, status="running", started_at=datetime.utcnow(),
    )
    ctx.db.add(run)
    flush_changes(ctx)
    ctx.db.commit()  # Reserve the unique key before any external side effect.
    from app.business_worker.context import request_actor
    actor_token = request_actor.set({"user_id": ctx.user_id, "workspace_id": ctx.workspace_id})
    try:
        result = _dispatch(ctx, row, idempotency_key)
        run.status, run.output = "completed", _bounded_output(result)
        if row.task_type in {"workflow", "alert"}:
            # 工作流以结果返回失败或人工等待，函数正常返回不代表业务完成。
            outcome = result.model_dump(mode="json", by_alias=True) if hasattr(result, "model_dump") else result
            child_status = outcome.get("status") if isinstance(outcome, dict) else None
            if child_status in {"completed", "failed", "cancelled", "waiting", "running", "cancel_requested"}:
                run.status = child_status
                run.error_summary = str(outcome.get("errorMessage") or outcome.get("error_message") or "")[:2000]
            else:
                run.status, run.error_summary = "failed", "工作流未返回可识别的执行状态"
    except Exception as exc:
        run.status, run.error_summary = "failed", str(exc)[:2000]
        run.output = {"errorType": type(exc).__name__}
    finally:
        request_actor.reset(actor_token)
    now = datetime.utcnow()
    run.completed_at = now if run.status in {"completed", "failed", "cancelled"} else None
    row.last_run_at = now
    # Publishing one immutable notice is a one-shot operation. Re-running it would
    # only create stale-version failures, so a successful notification job pauses.
    if row.task_type == "notification" and run.status == "completed":
        row.status, row.next_run_at = "paused", None
    else:
        try:
            row.next_run_at = (
                next_run(row.schedule_kind, row.schedule_expression, row.timezone)
                if row.status == "active" else None
            )
        except Exception as exc:
            # A malformed persisted schedule must become an observable failed
            # run and a paused job, rather than escaping the polling loop and
            # being retried on every tick.
            run.status = "failed"
            run.error_summary = "调度配置无效：" + str(exc)[:1900]
            run.output = {"errorType": type(exc).__name__}
            row.status, row.next_run_at = "paused", None
    row.version += 1
    ctx.db.commit()
    return run_view(run)


def list_runs(ctx: AuthContext, job_id: str, limit: int = 100) -> list[dict]:
    """列出某任务的运行记录（按创建时间倒序，最多 limit 条）；权限 system.operations.view。

    先 scoped_record 校验任务归属，避免用别人的 job_id 读取运行历史。
    """
    require_permission(ctx, "system.operations.view")
    scoped_record(ctx, ScheduledJob, job_id)
    rows = ctx.db.scalars(select(ScheduledJobRun).where(
        ScheduledJobRun.workspace_id == ctx.workspace_id, ScheduledJobRun.job_id == job_id,
    ).order_by(ScheduledJobRun.created_at.desc()).limit(limit)).all()
    return [run_view(row) for row in rows]


def _pause_due_job(db, job_id: str, reason: str, *, error_type: str | None = None) -> dict:
    """Pause one bad scheduled job in its own transaction.

    The polling loop must be able to continue when a job's creator loses access
    or when a previously valid persisted configuration becomes invalid.  Reload
    after rollback so a concurrent manual pause/archive is never overwritten.

    中文补充：把一个"坏掉"的任务单独置为暂停（独立事务），使轮询循环能继续处理其它任务。
    先 rollback 清掉失败事务，再重新加载任务行——若此时状态已不是 active（被并发
    暂停/归档），返回 skipped 而不是覆盖。任何异常都被吞掉并以结果形式返回，
    因为本函数处在错误处理路径的最后一层，不能再抛错打断轮询。
    """
    db.rollback()
    try:
        row = db.get(ScheduledJob, job_id)
        if row is None or row.status != "active":
            result = {"jobId": job_id, "status": "skipped", "reason": "任务已由其他操作处理"}
        else:
            row.status, row.next_run_at = "paused", None
            row.version += 1
            db.commit()
            result = {"jobId": job_id, "status": "paused", "reason": reason}
            if error_type:
                result["errorType"] = error_type
    except Exception:
        db.rollback()
        result = {"jobId": job_id, "status": "failed", "reason": "无法更新调度任务状态"}
    return result


def run_due_jobs(now: datetime | None = None) -> dict:
    """Execute due jobs independently so one bad identity cannot block others.

    中文补充：调度轮询入口（由后台调度线程在持有租约时调用，一次最多 20 个到期任务）。
    每个任务单独构造 AuthContext 并以创建者身份执行，但**每次都会重新校验**
    system.operations.manage 权限：账号被停用、退出工作区或权限被收回的任务会被
    自动暂停（而不是继续以旧身份运行）。
    返回：{"count","items"}，items 是各任务的运行视图或暂停/跳过说明。
    副作用：不同任务各自提交事务，单个失败不影响其它任务。
    """
    from app.db import new_session

    now = now or datetime.utcnow()
    processed: list[dict] = []
    with new_session() as db:
        rows = db.scalars(select(ScheduledJob).where(
            ScheduledJob.status == "active", ScheduledJob.next_run_at.is_not(None),
            ScheduledJob.next_run_at <= now,
        ).order_by(ScheduledJob.next_run_at).limit(20)).all()
        for row in rows:
            try:
                user = db.get(User, row.created_by)
                workspace = db.get(Workspace, row.workspace_id)
                member = db.scalar(select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == row.workspace_id,
                    WorkspaceMember.user_id == row.created_by,
                ))
                if not user or user.status != "active" or not workspace or not member:
                    processed.append(_pause_due_job(db, row.id, "执行身份不可用，任务已暂停"))
                    continue
                if not _workspace_is_active(db, workspace):
                    processed.append(_pause_due_job(db, row.id, "工作区或所属组织已归档，任务已暂停"))
                    continue
                ctx = AuthContext(db=db, user=user, workspace=workspace, role=member.role, token="scheduler")
                # Resolve authorization from current roles for every execution;
                # a creator's active account/membership alone is insufficient.
                require_permission(ctx, "system.operations.manage")
                scheduled_for = row.next_run_at
                key = "schedule-" + row.id + "-" + scheduled_for.strftime("%Y%m%d%H%M%S")
                processed.append(run_job(ctx, row.id, row.version, key,
                                         trigger_type="schedule", scheduled_for=scheduled_for))
            except PermissionErrorEx:
                processed.append(_pause_due_job(
                    db, row.id, "执行身份已失去系统运维管理权限，任务已暂停",
                    error_type="PermissionErrorEx",
                ))
            except Exception as exc:
                processed.append(_pause_due_job(
                    db, row.id, "调度任务处理异常，任务已暂停",
                    error_type=type(exc).__name__,
                ))
    return {"count": len(processed), "items": processed}
