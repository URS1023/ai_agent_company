"""Real workspace operations data with action and data-scope enforcement.

中文补充：运维中心服务层，为 `/api/v1/operations` 的三类视图提供数据，全部读当前工作区
的持久化记录，不做任何统计快照或外部调用。

- 运行记录视图（`list_runs` / `run_detail`）：数据源 runs + run_steps + tool_calls +
  model_calls + run_events，产物另取自 artifacts；状态语义取自 runs.status，
  `created/running/waiting` 为非终态、`completed/failed/cancelled` 为终态，
  界面"未结束"判定与 `summary` 的 `active`/`stale` 均按此口径。
- 审计日志视图（`list_audit`）：数据源 audit_logs，由各写入方在动作发生时落库，本层只读。
- 业务运营视图（`business_operations`）：数据源为知识库/文档/分段、Bot 与 Bot 调试运行、
  应用与启动记录等真实业务表，按域权限与数据范围聚合。

约束：每个入口都先 `require_permission` 再按 `allowed_owner_ids`/`resource_grant_ids` 收窄
数据范围，"不可见"与"不存在"一律返回同一个 404；除 `recover` 在 `dryRun=false` 时改写
运行状态并写审计外，其余函数只读。所有对外载荷必须经过 `_safe_json` 遮蔽后才返回。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select

from app.common.audit import audit
from app.common.authorization import (
    access_policy,
    allowed_owner_ids,
    require_permission,
    resource_grant_ids,
)
from app.common.deps import AuthContext
from app.errors import NotFoundError
from app.models import (
    Artifact,
    AuditLog,
    AuthIdentity,
    Conversation,
    ModelCall,
    Run,
    RunEvent,
    RunStep,
    ToolCall, KnowledgeBase, KnowledgeDocument, KnowledgeChunk,
    User,
)
from app.modules.runtime.recovery import recover_stale_runs

from .schemas import RecoverStaleInput


# runs.status 的全集；筛选接口的 Literal 取值必须与此保持一致，新增状态需同步两处。
RUN_STATES = {"created", "running", "waiting", "completed", "failed", "cancelled"}
# 命中即整体替换为 "***"；键名先小写并把连字符归一为下划线再比对，避免 api-key / apiKey 漏网。
_SENSITIVE_KEYS = {
    "password", "secret", "token", "api_key", "apikey", "authorization",
    "credential", "access_token", "refresh_token", "cookie", "set_cookie",
}


def _iso(value: datetime | None) -> str | None:
    """时间统一出参格式：空值原样返回 None（保留"未开始/未结束"语义，不补零值时间）。"""
    return value.isoformat() if value else None


def _duration(started: datetime | None, completed: datetime | None) -> float | None:
    """起止时间双全才计算耗时，秒级保留 3 位小数；缺任一端点返回 None。

    用 `max(0.0, ...)` 兜底时钟回拨或跨库时区导致的负差值，避免界面出现负耗时。
    """
    if not started or not completed:
        return None
    return round(max(0.0, (completed - started).total_seconds()), 3)


def _safe_json(value: Any, depth: int = 0) -> Any:
    """Redact credentials in trace payloads without inventing replacement data.

    递归遮蔽凭据字段：键名命中 `_SENSITIVE_KEYS` 或以 `_password`/`_secret`/`_token`
    结尾时整体替换为 `"***"`。只做遮蔽，不新增、不猜测字段内容；
    超过 24 层返回固定占位符，防止自引用或异常深的追踪载荷拖垮响应。
    """
    if depth > 24:
        return "[嵌套过深]"
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            sensitive = normalized in _SENSITIVE_KEYS or normalized.endswith(("_password", "_secret", "_token"))
            result[str(key)] = "***" if sensitive else _safe_json(child, depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_json(child, depth + 1) for child in value]
    return value


def _run_statement(ctx: AuthContext, permission: str):
    """构造运行记录查询基座：`operations.view` + 传入的 runs 权限双重校验后收窄数据范围。

    `permission` 传 `runs.view`（列表/详情）或 `runs.edit`（恢复），决定用哪条角色数据范围；
    没有对应权限时 `allowed_owner_ids` 直接抛 403。范围为 None 时不做归属收窄，等于整个
    工作区（管理员，或未配置细分角色的历史成员契约）；范围非空时只保留"本人发起的运行"或
    "被显式授权的运行"（`resource_grant_ids`）。
    调用方负责追加筛选条件，不得绕开本函数直接查 runs 表。
    """
    require_permission(ctx, "operations.view")
    owners = allowed_owner_ids(ctx, permission)
    statement = select(Run).where(Run.workspace_id == ctx.workspace_id)
    if owners is not None:
        # 动作取自权限码末段（runs.view → view），与资源授权表记录的 action 口径一致。
        grants = resource_grant_ids(ctx, "runs", permission.rsplit(".", 1)[-1])
        statement = statement.where(or_(
            Run.started_by_user_id.in_(owners), Run.id.in_(grants),
        ))
    return statement


def _audit_statement(ctx: AuthContext):
    """构造审计日志查询基座：`operations.view` + `audit.view`，按操作者收窄数据范围。

    审计行没有"资源授权"概念，因此只按 `actor_user_id` 归属过滤，不查 `resource_grant_ids`；
    管理员（`allowed_owner_ids` 返回 None）可读整个工作区审计。
    """
    require_permission(ctx, "operations.view")
    owners = allowed_owner_ids(ctx, "audit.view")
    statement = select(AuditLog).where(AuditLog.workspace_id == ctx.workspace_id)
    if owners is not None:
        statement = statement.where(AuditLog.actor_user_id.in_(owners))
    return statement


def _user_labels(ctx: AuthContext, user_ids: set[str]) -> dict[str, dict]:
    """批量取用户的显示名与本地登录名，避免逐行查库。

    返回 `{user_id: {id, name, username}}`；`username` 只取 `provider == "local"` 的身份，
    第三方登录或未设置时返回空串而非 None。入参为空时直接返回空字典，不发起查询。
    """
    if not user_ids:
        return {}
    rows = ctx.db.execute(
        select(User, AuthIdentity.username_normalized).outerjoin(
            AuthIdentity,
            (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
        ).where(User.id.in_(user_ids))
    ).all()
    return {
        user.id: {
            "id": user.id,
            "name": user.display_name,
            "username": username or "",
        }
        for user, username in rows
    }


def _conversation_titles(ctx: AuthContext, conversation_ids: set[str]) -> dict[str, str]:
    """取当前工作区内"未归档"会话的标题：已归档或跨工作区的 ID 不会出现在结果里。

    调用方据此判断会话是否可见（`row.conversation_id in titles`）：不可见时对外输出空
    `conversationId`，既不暴露标题也不提供跳转链接。
    """
    if not conversation_ids:
        return {}
    return {
        row.id: row.title
        for row in ctx.db.scalars(select(Conversation).where(
            Conversation.id.in_(conversation_ids),
            Conversation.workspace_id == ctx.workspace_id,
            Conversation.archived_at.is_(None),
        )).all()
    }


def _run_title(row: Run, titles: dict[str, str]) -> str:
    """派生运行标题的固定优先级：会话标题 → 应用启动 → 模型配置名 + "任务" → 未命名任务。

    顺序即界面文案约定，前端以同一标题作为运行入口；模型配置缺失时不能直接拼接字符串，
    否则会出现 "None 任务"。
    """
    title = titles.get(row.conversation_id, "").strip()
    if title:
        return title
    if row.model_profile == "application":
        return "应用启动"
    if row.model_profile:
        return row.model_profile + " 任务"
    return "未命名任务"


def list_runs(
    ctx: AuthContext,
    *,
    keyword: str = "",
    status: str = "",
    profile: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """运行记录列表（GET /api/v1/operations/runs，需 `operations.view` + `runs.view`）。

    `keyword` 匹配运行 ID / 会话标题 / 发起人显示名 / 错误摘要，`status` 与 `profile` 为
    精确筛选；`limit`/`offset` 分页，`total` 为筛选后的全量条数（与当前页无关）。
    返回 `profiles` 只聚合本页出现过的模型配置，供筛选下拉使用。
    只读无副作用；步骤/工具/产物计数按模型各一条 group by 查询取得，避免逐行 N+1。
    """
    statement = _run_statement(ctx, "runs.view")
    if status:
        statement = statement.where(Run.status == status)
    if profile:
        statement = statement.where(Run.model_profile == profile)
    if keyword:
        conversations = select(Conversation.id).where(
            Conversation.workspace_id == ctx.workspace_id,
            Conversation.archived_at.is_(None),
            Conversation.title.contains(keyword, autoescape=True),
        )
        users = select(User.id).where(User.display_name.contains(keyword, autoescape=True))
        statement = statement.where(or_(
            Run.id.contains(keyword, autoescape=True),
            Run.conversation_id.in_(conversations),
            Run.started_by_user_id.in_(users),
            Run.error_summary.contains(keyword, autoescape=True),
        ))
    total = int(ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    rows = ctx.db.scalars(statement.order_by(Run.created_at.desc()).limit(limit).offset(offset)).all()
    run_ids = [row.id for row in rows]

    def counts(model) -> dict[str, int]:
        """按模型批量取"每运行的条数"，一次 group by 覆盖本页全部运行，避免逐行计数。"""
        if not run_ids:
            return {}
        return {
            run_id: int(count)
            for run_id, count in ctx.db.execute(
                select(model.run_id, func.count()).where(
                    model.workspace_id == ctx.workspace_id,
                    model.run_id.in_(run_ids),
                ).group_by(model.run_id)
            ).all()
        }

    step_counts = counts(RunStep)
    tool_counts = counts(ToolCall)
    artifact_counts = counts(Artifact)
    users = _user_labels(ctx, {row.started_by_user_id for row in rows})
    titles = _conversation_titles(ctx, {row.conversation_id for row in rows if row.conversation_id})
    profiles = sorted({row.model_profile for row in rows if row.model_profile})
    return {
        "items": [{
            "id": row.id,
            "title": _run_title(row, titles),
            "conversationId": row.conversation_id if row.conversation_id in titles else "",
            "status": row.status,
            "modelProfile": row.model_profile or "",
            "actor": users.get(row.started_by_user_id, {
                "id": row.started_by_user_id, "name": "未知用户", "username": "",
            }),
            "stepCount": step_counts.get(row.id, 0),
            "toolCount": tool_counts.get(row.id, 0),
            "artifactCount": artifact_counts.get(row.id, 0),
            "durationSeconds": _duration(row.started_at, row.completed_at),
            "cost": row.cost,
            "errorCode": row.error_code,
            "errorSummary": row.error_summary or "",
            "createTime": _iso(row.created_at),
            "completeTime": _iso(row.completed_at),
        } for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "profiles": profiles,
    }


def run_detail(ctx: AuthContext, run_id: str) -> dict:
    """单个运行的调用链详情（GET /api/v1/operations/runs/{run_id}，需 `operations.view` + `runs.view`）。

    返回步骤、工具调用、模型调用、事件时间线，以及当前用户有权查看的产物列表
    （仅在具备 `files.view` 时附加，否则为空数组）。产物下载地址指向
    `/api/artifacts/{id}/download`，由产物模块自行做权限校验。
    运行不存在或超出数据范围统一抛 `NotFoundError`（由路由层转 404），不区分两者。
    只读：所有输入/输出载荷经 `_safe_json` 遮蔽后再输出。
    """
    row = ctx.db.scalar(_run_statement(ctx, "runs.view").where(Run.id == run_id))
    if row is None:
        raise NotFoundError("运行记录不存在")
    users = _user_labels(ctx, {row.started_by_user_id})
    titles = _conversation_titles(ctx, {row.conversation_id} if row.conversation_id else set())
    steps = ctx.db.scalars(select(RunStep).where(
        RunStep.workspace_id == ctx.workspace_id, RunStep.run_id == row.id,
    ).order_by(RunStep.sequence)).all()
    tools = ctx.db.scalars(select(ToolCall).where(
        ToolCall.workspace_id == ctx.workspace_id, ToolCall.run_id == row.id,
    ).order_by(ToolCall.started_at, ToolCall.id)).all()
    models = ctx.db.scalars(select(ModelCall).where(
        ModelCall.workspace_id == ctx.workspace_id, ModelCall.run_id == row.id,
    ).order_by(ModelCall.started_at, ModelCall.id)).all()
    events = ctx.db.scalars(select(RunEvent).where(
        RunEvent.workspace_id == ctx.workspace_id, RunEvent.run_id == row.id,
    ).order_by(RunEvent.sequence)).all()

    artifacts = []
    # 产物清单随 files.view 独立裁剪：有运行查看权不等于有文件查看权，两者不能互相放宽。
    if "files.view" in access_policy(ctx).permissions:
        owners = allowed_owner_ids(ctx, "files.view")
        artifact_statement = select(Artifact).where(
            Artifact.workspace_id == ctx.workspace_id, Artifact.run_id == row.id,
        )
        if owners is not None:
            grants = resource_grant_ids(ctx, "files", "view")
            artifact_statement = artifact_statement.where(or_(
                Artifact.owner_user_id.in_(owners), Artifact.id.in_(grants),
            ))
        artifact_rows = ctx.db.scalars(artifact_statement.order_by(Artifact.created_at)).all()
        artifacts = [{
            "id": item.id, "name": item.name, "type": item.type,
            "mimeType": item.mime_type, "size": item.size, "status": item.status,
            "downloadPath": f"/api/artifacts/{item.id}/download",
            "createTime": _iso(item.created_at),
        } for item in artifact_rows]

    public_conversation_id = row.conversation_id if row.conversation_id in titles else ""
    return {
        "id": row.id,
        "title": _run_title(row, titles),
        "conversationId": public_conversation_id,
        "conversationPath": f"/ai?cid={public_conversation_id}" if public_conversation_id else "",
        "status": row.status,
        "modelProfile": row.model_profile or "",
        "actor": users.get(row.started_by_user_id, {
            "id": row.started_by_user_id, "name": "未知用户", "username": "",
        }),
        "parentRunId": row.parent_run_id,
        "cost": row.cost,
        "modelUsage": _safe_json(row.model_usage or {}),
        "context": _safe_json(row.context_snapshot or {}),
        "errorCode": row.error_code,
        "errorSummary": row.error_summary or "",
        "createTime": _iso(row.created_at),
        "startTime": _iso(row.started_at),
        "completeTime": _iso(row.completed_at),
        "durationSeconds": _duration(row.started_at, row.completed_at),
        "steps": [{
            "id": item.id, "sequence": item.sequence, "type": item.step_type,
            "name": item.name, "status": item.status,
            "input": _safe_json(item.input_json or {}),
            "output": _safe_json(item.output_json or {}),
            "errorCode": item.error_code, "errorSummary": item.error_summary or "",
            "retryCount": item.retry_count,
            "startTime": _iso(item.started_at), "completeTime": _iso(item.completed_at),
            "durationSeconds": _duration(item.started_at, item.completed_at),
        } for item in steps],
        "toolCalls": [{
            "id": item.id, "stepId": item.step_id, "toolName": item.tool_name,
            "status": item.status, "arguments": _safe_json(item.arguments_json or {}),
            "result": _safe_json(item.result_json or {}),
            "errorCode": item.error_code, "errorSummary": item.error_summary or "",
            "startTime": _iso(item.started_at), "completeTime": _iso(item.completed_at),
            "durationSeconds": _duration(item.started_at, item.completed_at),
        } for item in tools],
        "modelCalls": [{
            "id": item.id, "stepId": item.step_id, "provider": item.provider,
            "model": item.model, "status": item.status,
            "usage": _safe_json(item.usage_json or {}), "metadata": _safe_json(item.metadata_json or {}),
            "errorCode": item.error_code,
            "startTime": _iso(item.started_at), "completeTime": _iso(item.completed_at),
            "durationSeconds": _duration(item.started_at, item.completed_at),
        } for item in models],
        "events": [{
            "sequence": item.sequence, "type": item.event_type,
            "payload": _safe_json(item.payload or {}), "createTime": _iso(item.created_at),
        } for item in events],
        "artifacts": artifacts,
    }


def summary(ctx: AuthContext, *, threshold_seconds: int = 900) -> dict:
    """运行健康摘要（GET /api/v1/operations/summary，需 `operations.view`）。

    实际做的运维动作只有一次**只读**探针 `SELECT 1`：成功则 `database=healthy`，
    抛异常才置 `unhealthy` 并继续返回其余字段（降级不报错）。`api` 恒为 `healthy`
    ——能执行到这里就说明 API 进程存活，不额外探测下游；本模块也不提供缓存清理动作，
    因此该字段不代表缓存状态。

    按权限裁剪：无 `runs.view` 时 `runs` 为 None（不返回 0，避免与"零运行"混淆），
    无 `audit.view` 时 `auditCount` 为 None；`canRecover` 要求 `operations.edit` 与
    `runs.edit` 同时具备。`stale` 统计处于非终态且起始时间（`started_at`，缺失取
    `created_at`）早于阈值的运行，与恢复接口的判定口径一致。
    """
    require_permission(ctx, "operations.view")
    permissions = access_policy(ctx).permissions
    database = "healthy"
    try:
        # 唯一真实探针：只读 SELECT 1，不写库、不触碰业务表；失败仅降级标记，不影响其余字段。
        ctx.db.scalar(select(1))
    except Exception:
        database = "unhealthy"
    runs = None
    if "runs.view" in permissions:
        statement = _run_statement(ctx, "runs.view")
        status_rows = ctx.db.execute(
            statement.with_only_columns(Run.status, func.count()).group_by(Run.status)
        ).all()
        counts = {status: int(count) for status, count in status_rows}
        cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
        stale = int(ctx.db.scalar(select(func.count()).select_from(
            statement.where(
                Run.status.in_(["created", "running", "waiting"]),
                func.coalesce(Run.started_at, Run.created_at) < cutoff,
            ).order_by(None).subquery()
        )) or 0)
        runs = {
            "total": sum(counts.values()), "byStatus": counts,
            "active": sum(counts.get(key, 0) for key in ("created", "running", "waiting")),
            "stale": stale,
        }
    audit_count = None
    if "audit.view" in permissions:
        audit_count = int(ctx.db.scalar(select(func.count()).select_from(
            _audit_statement(ctx).order_by(None).subquery()
        )) or 0)
    return {
        "service": {"api": "healthy", "database": database, "serverTime": _iso(datetime.utcnow())},
        "runs": runs,
        "auditCount": audit_count,
        "canViewAudit": "audit.view" in permissions,
        "canRecover": {"operations.edit", "runs.edit"}.issubset(permissions),
        "thresholdSeconds": threshold_seconds,
    }


def list_audit(
    ctx: AuthContext,
    *,
    keyword: str = "",
    action: str = "",
    resource_type: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """审计日志列表（GET /api/v1/operations/audit，需 `operations.view` + `audit.view`）。

    数据源为 audit_logs：由业务写入方在动作发生时落库，本函数只读、不生成也不修改审计。
    因此在没有新动作的前提下，反复调用本接口不会让"审计条数"增长。
    `action`/`resource_type` 为精确匹配，`keyword` 匹配动作、资源类型/ID、运行 ID 以及
    操作者显示名或本地登录名；`actor_user_id` 为空的历史条目按"系统"归类输出。
    `actions`/`resourceTypes` 只聚合当前页，供筛选下拉参考，不能当全量枚举使用。
    """
    statement = _audit_statement(ctx)
    if action:
        statement = statement.where(AuditLog.action == action)
    if resource_type:
        statement = statement.where(AuditLog.resource_type == resource_type)
    if keyword:
        actor_ids = select(User.id).outerjoin(
            AuthIdentity,
            (AuthIdentity.user_id == User.id) & (AuthIdentity.provider == "local"),
        ).where(or_(
            User.display_name.contains(keyword, autoescape=True),
            AuthIdentity.username_normalized.contains(keyword, autoescape=True),
        ))
        statement = statement.where(or_(
            AuditLog.action.contains(keyword, autoescape=True),
            AuditLog.resource_type.contains(keyword, autoescape=True),
            AuditLog.resource_id.contains(keyword, autoescape=True),
            AuditLog.run_id.contains(keyword, autoescape=True),
            AuditLog.actor_user_id.in_(actor_ids),
        ))
    total = int(ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    rows = ctx.db.scalars(statement.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)).all()
    users = _user_labels(ctx, {row.actor_user_id for row in rows if row.actor_user_id})
    actions = sorted({row.action for row in rows})
    resource_types = sorted({row.resource_type for row in rows})
    return {
        "items": [{
            "id": row.id,
            "actor": users.get(row.actor_user_id, {
                "id": row.actor_user_id or "", "name": "系统", "username": "",
            }),
            "action": row.action, "resourceType": row.resource_type,
            "resourceId": row.resource_id, "runId": row.run_id,
            "ipAddress": row.ip_address or "", "metadata": _safe_json(row.metadata_json or {}),
            "createTime": _iso(row.created_at),
        } for row in rows],
        "total": total, "limit": limit, "offset": offset,
        "actions": actions, "resourceTypes": resource_types,
    }


def recover(ctx: AuthContext, payload: RecoverStaleInput) -> dict:
    """中断运行恢复（POST /api/v1/operations/runs/recover），两段式，默认不改数据。

    权限：需 `operations.edit`；可见范围另按 `runs.edit` 的角色数据范围收窄
    （`allowed_owner_ids` 的作用域 + `resource_grant_ids` 的显式授权），无权限者抛 403。

    - 第一段 `payload.dry_run=True`（默认）：只调用 `recover_stale_runs(apply=False)`
      做预览，返回候选 `items` 与 `skipped`（含跳过原因），**不写库、不写审计**。
    - 第二段 `payload.dry_run=False`：把能证明执行者已退出的运行标记为失败
      （`error_code=stale_run_recovered`），并补一条用户可见的中断说明；只改状态、
      不删除任何步骤/工具调用/历史记录。批次在同一事务内提交，随后写入
      `operations.run.recover` 审计（含 count、thresholdSeconds、runIds）。
      没有任何可处理项时仍然提交，以获得干净的事务边界，但不产生审计噪音。

    返回值比恢复器原始结果更面向界面：把 `skipped_count/skipped` 与 `items` 一并输出，
    便于管理员区分"已恢复""因等待审批或归属不明而保留"两类运行。
    """
    require_permission(ctx, "operations.edit")
    owners = allowed_owner_ids(ctx, "runs.edit")
    grants = resource_grant_ids(ctx, "runs", "edit")
    result = recover_stale_runs(
        ctx.db,
        workspace_id=ctx.workspace_id,
        threshold_seconds=payload.threshold_seconds,
        apply=not payload.dry_run,
        actor=ctx.user_id,
        owner_ids=owners,
        run_ids=grants,
    )
    if not payload.dry_run and result["count"]:
        audit(
            ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
            action="operations.run.recover", resource_type="run", resource_id=None,
            metadata={
                "count": result["count"], "thresholdSeconds": payload.threshold_seconds,
                "runIds": [item["run_id"] for item in result["items"]],
            },
        )
        ctx.db.commit()
    elif not payload.dry_run:
        ctx.db.commit()
    return {
        "count": result["count"], "dryRun": result["dry_run"],
        "skippedCount": result["skipped_count"],
        "skipped": [{"runId": item["run_id"], "status": item["status"], "reason": item["reason"]}
                    for item in result["skipped"]],
        "items": [{
            "runId": item["run_id"], "conversationId": item["conversation_id"],
            "status": item["status"], "ageSeconds": item["age_s"],
        } for item in result["items"]],
    }


def _complete_days(days: int) -> list[str]:
    """生成连续完整日期序列（含今天），由早到晚。

    图表要求"无数据的日期也要有点"，因此缺数据的日子由调用方补 0，而不是跳过；
    分类多于实际业务天数时也不会出现断档，前端可直接对齐 x 轴。
    """
    today = datetime.utcnow().date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _series(categories: list[str], rows: list[tuple[datetime, str]], names: list[str]) -> list[dict]:
    """把 (时间, 状态) 记录按天聚合成多条序列，输出顺序与 `names` 一致。

    只有状态名出现在 `names` 里的记录才计数（如 Bot/应用固定输出 completed 与 failed，
    其余状态不画线）；`categories` 之外的日期一律丢弃，保证每个序列长度与 x 轴严格相等。
    """
    counters = {name: {day: 0 for day in categories} for name in names}
    for created_at, status in rows:
        day = created_at.date().isoformat()
        if day in counters.get(status, {}):
            counters[status][day] += 1
    return [{"name": name, "data": [counters[name][day] for day in categories]} for name in names]


def _count_status(rows, attribute: str = "status") -> list[dict]:
    """按对象状态计数，供分布图使用：状态为空时归入 "unknown"，并按状态名排序保证输出稳定。"""
    counts: dict[str, int] = {}
    for row in rows:
        value = str(getattr(row, attribute) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return [{"name": key, "value": value} for key, value in sorted(counts.items())]


def _knowledge_operations(
    ctx: AuthContext, days: int, cutoff: datetime, categories: list[str], object_id: str = "",
) -> dict:
    """知识资产运营看板（`domain="knowledge"`），需 `knowledge.view`。

    数据源为知识库 / 文档 / 分段三张真实业务表（非统计快照）：`metrics` 统计知识库数、
    文档数与分段数，"待治理"= 文档解析失败数 + 分段向量失败数；`breakdown` 按文档状态分布；
    `trend` 统计窗口内每日新增文档与分段；`topItems` 取文档与分段合计数最高的前 10 个知识库；
    `issues` 列出解析失败文档（最多 20 条）并给出原失败原因；`governance` 汇总空知识库、
    解析失败文档、向量失败分段三类待治理项（有失败项时 level 升为 critical）。
    数据范围按知识库 owner 收窄；`object_id` 不在范围内抛 `NotFoundError`（转 404）。
    `documents`/`chunks` 用 `in_(base_ids or [""])` 兜底：无可见知识库时返回空集而非全表。
    只读，无副作用。
    """
    require_permission(ctx, "knowledge.view")
    owners = allowed_owner_ids(ctx, "knowledge.view")
    base_statement = select(KnowledgeBase).where(
        KnowledgeBase.workspace_id == ctx.workspace_id, KnowledgeBase.status != "deleted",
    )
    if owners is not None:
        base_statement = base_statement.where(KnowledgeBase.owner_user_id.in_(owners))
    bases = ctx.db.scalars(base_statement.order_by(KnowledgeBase.name)).all()
    available_objects = [{"id": row.id, "name": row.name} for row in bases]
    if object_id:
        bases = [row for row in bases if row.id == object_id]
        if not bases:
            raise NotFoundError("知识库不存在或不在当前数据范围内")
    base_ids = [row.id for row in bases]
    documents = ctx.db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.workspace_id == ctx.workspace_id,
        KnowledgeDocument.knowledge_base_id.in_(base_ids or [""]),
    )).all()
    chunks = ctx.db.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.workspace_id == ctx.workspace_id,
        KnowledgeChunk.knowledge_base_id.in_(base_ids or [""]),
    )).all()
    doc_counts: dict[str, int] = {}
    chunk_counts: dict[str, int] = {}
    for row in documents:
        doc_counts[row.knowledge_base_id] = doc_counts.get(row.knowledge_base_id, 0) + 1
    for row in chunks:
        chunk_counts[row.knowledge_base_id] = chunk_counts.get(row.knowledge_base_id, 0) + 1
    failed = [row for row in documents if row.status == "failed"]
    embedding_failed = [row for row in chunks if row.embedding_status == "failed"]
    recent_rows = [(row.created_at, "documents") for row in documents if row.created_at >= cutoff]
    recent_rows += [(row.created_at, "chunks") for row in chunks if row.created_at >= cutoff]
    return {
        "label": "知识资产运营",
        "availableObjects": available_objects,
        "metrics": [
            {"key": "bases", "label": "知识库", "value": len(bases), "unit": "个"},
            {"key": "documents", "label": "文档", "value": len(documents), "unit": "份"},
            {"key": "chunks", "label": "分段", "value": len(chunks), "unit": "段"},
            {"key": "failures", "label": "待治理", "value": len(failed) + len(embedding_failed), "unit": "项"},
        ],
        "breakdown": _count_status(documents),
        "trend": {"categories": categories, "series": _series(categories, recent_rows, ["documents", "chunks"])},
        "topItems": [{
            "id": row.id, "name": row.name, "status": row.status,
            "primary": doc_counts.get(row.id, 0), "secondary": chunk_counts.get(row.id, 0),
            "primaryLabel": "文档", "secondaryLabel": "分段", "path": f"/knowledge?id={row.id}",
        } for row in sorted(bases, key=lambda item: (doc_counts.get(item.id, 0), chunk_counts.get(item.id, 0)), reverse=True)[:10]],
        "issues": [{
            "id": row.id, "name": "文档解析失败", "detail": row.error_summary or "未记录失败原因",
            "status": row.status, "time": _iso(row.updated_at), "path": f"/knowledge?id={row.knowledge_base_id}",
        } for row in sorted(failed, key=lambda item: item.updated_at, reverse=True)[:20]],
        "governance": [
            {"name": "空知识库", "value": sum(doc_counts.get(row.id, 0) == 0 for row in bases), "level": "warning", "path": "/knowledge"},
            {"name": "解析失败文档", "value": len(failed), "level": "critical" if failed else "normal", "path": "/knowledge"},
            {"name": "向量失败分段", "value": len(embedding_failed), "level": "critical" if embedding_failed else "normal", "path": "/knowledge"},
        ],
    }


def _bot_operations(
    ctx: AuthContext, days: int, cutoff: datetime, categories: list[str], object_id: str = "",
) -> dict:
    """Bot 运营看板（`domain="bots"`），需 `bots.view`。

    数据源为 BusinessBot 与 BotDebugRun：`metrics` 统计 Bot 数、已发布数、调试调用次数与
    失败率（失败率 = 失败调用 / 总调用，无调用时为 0 而非 null）；`trend` 只画
    completed 与 failed 两条线；`governance` 汇总草稿态、零调用、平均响应耗时（仅按已完成
    调用计算，ms）。
    可见范围由 `visible_records` 统一裁决（所有者 + 显式共享），`object_id` 越界抛
    `NotFoundError`。`select(BotDebugRun).where(bot_id.in_(bot_ids or [""]))` 的空列表兜底
    保证无可见 Bot 时不返回全量调用记录。只读，无副作用。
    """
    require_permission(ctx, "bots.view")
    # 函数内导入：knowledge_graph / applications 模块可能反向依赖运维能力，置于模块顶层会形成循环导入。
    from app.common.owned_records import visible_records
    from app.modules.knowledge_graph.models import BusinessBot, BotDebugRun

    bots = ctx.db.scalars(visible_records(ctx, BusinessBot, "bots.view").where(
        BusinessBot.status != "deleted",
    ).order_by(BusinessBot.name)).all()
    available_objects = [{"id": row.id, "name": row.name} for row in bots]
    if object_id:
        bots = [row for row in bots if row.id == object_id]
        if not bots:
            raise NotFoundError("Bot 不存在或不在当前数据范围内")
    bot_ids = [row.id for row in bots]
    runs = ctx.db.scalars(select(BotDebugRun).where(
        BotDebugRun.workspace_id == ctx.workspace_id,
        BotDebugRun.bot_id.in_(bot_ids or [""]),
    )).all()
    run_counts: dict[str, int] = {}
    failures: list = []
    for row in runs:
        run_counts[row.bot_id] = run_counts.get(row.bot_id, 0) + 1
        if row.status == "failed":
            failures.append(row)
    recent_rows = [(row.created_at, row.status) for row in runs if row.created_at >= cutoff]
    completed = [row for row in runs if row.status == "completed"]
    return {
        "label": "Bot 运营",
        "availableObjects": available_objects,
        "metrics": [
            {"key": "bots", "label": "Bot", "value": len(bots), "unit": "个"},
            {"key": "published", "label": "已发布", "value": sum(row.status == "published" for row in bots), "unit": "个"},
            {"key": "invocations", "label": "调用", "value": len(runs), "unit": "次"},
            {"key": "failureRate", "label": "失败率", "value": round(len(failures) * 100 / len(runs), 2) if runs else 0, "unit": "%"},
        ],
        "breakdown": _count_status(bots),
        "trend": {"categories": categories, "series": _series(categories, recent_rows, ["completed", "failed"])},
        "topItems": [{
            "id": row.id, "name": row.name, "status": row.status,
            "primary": run_counts.get(row.id, 0), "secondary": row.published_revision or 0,
            "primaryLabel": "调用", "secondaryLabel": "发布修订", "path": f"/bots?id={row.id}",
        } for row in sorted(bots, key=lambda item: run_counts.get(item.id, 0), reverse=True)[:10]],
        "issues": [{
            "id": row.id, "name": next((bot.name for bot in bots if bot.id == row.bot_id), "Bot 调用"),
            "detail": row.error_message or "调用失败", "status": row.status,
            "time": _iso(row.updated_at), "path": f"/bots?id={row.bot_id}",
        } for row in sorted(failures, key=lambda item: item.updated_at, reverse=True)[:20]],
        "governance": [
            {"name": "未发布 Bot", "value": sum(row.status == "draft" for row in bots), "level": "warning", "path": "/bots"},
            {"name": "无调用 Bot", "value": sum(run_counts.get(row.id, 0) == 0 for row in bots), "level": "warning", "path": "/bots"},
            {"name": "平均响应", "value": round(sum(row.duration_ms for row in completed) / len(completed), 1) if completed else 0, "level": "normal", "unit": "ms", "path": "/bots"},
        ],
    }


def _application_operations(
    ctx: AuthContext, days: int, cutoff: datetime, categories: list[str], object_id: str = "",
) -> dict:
    """应用运营看板（`domain="applications"`），需 `applications.view`。

    数据源为 BusinessApplication 与 ApplicationLaunch：`metrics` 统计应用数、已发布数、
    启动次数与失败次数；`topItems` 同时给出"实际记录"（窗口内的 ApplicationLaunch 条数）
    与"累计启动"（应用表上的累计计数器 `launch_count`），两者口径不同、不可互相替代；
    `trend` 只画 completed 与 failed；失败下钻跳转 `/operations?id=<runId>`（运行主键），
    因此出错的应用可回到运行记录查看调用链。
    可见范围由 `visible_records` 裁决；`object_id` 越界抛 `NotFoundError`；空应用列表同样
    以 `in_(app_ids or [""])` 兜底避免全表扫描。只读，无副作用。
    """
    require_permission(ctx, "applications.view")
    # 函数内导入：applications 模块可能反向依赖运维能力，置于模块顶层会形成循环导入。
    from app.common.owned_records import visible_records
    from app.modules.applications.models import ApplicationLaunch, BusinessApplication

    apps = ctx.db.scalars(visible_records(ctx, BusinessApplication, "applications.view").order_by(
        BusinessApplication.name,
    )).all()
    available_objects = [{"id": row.id, "name": row.name} for row in apps]
    if object_id:
        apps = [row for row in apps if row.id == object_id]
        if not apps:
            raise NotFoundError("应用不存在或不在当前数据范围内")
    app_ids = [row.id for row in apps]
    launches = ctx.db.scalars(select(ApplicationLaunch).where(
        ApplicationLaunch.workspace_id == ctx.workspace_id,
        ApplicationLaunch.application_id.in_(app_ids or [""]),
    )).all()
    launch_counts: dict[str, int] = {}
    failures = []
    for row in launches:
        launch_counts[row.application_id] = launch_counts.get(row.application_id, 0) + 1
        if row.status == "failed":
            failures.append(row)
    recent_rows = [(row.created_at, row.status) for row in launches if row.created_at >= cutoff]
    return {
        "label": "应用运营",
        "availableObjects": available_objects,
        "metrics": [
            {"key": "applications", "label": "应用", "value": len(apps), "unit": "个"},
            {"key": "published", "label": "已发布", "value": sum(row.status == "published" for row in apps), "unit": "个"},
            {"key": "launches", "label": "启动", "value": len(launches), "unit": "次"},
            {"key": "failures", "label": "失败", "value": len(failures), "unit": "次"},
        ],
        "breakdown": _count_status(apps),
        "trend": {"categories": categories, "series": _series(categories, recent_rows, ["completed", "failed"])},
        "topItems": [{
            "id": row.id, "name": row.name, "status": row.status,
            "primary": launch_counts.get(row.id, 0), "secondary": row.launch_count,
            "primaryLabel": "实际记录", "secondaryLabel": "累计启动", "path": f"/applications?id={row.id}",
        } for row in sorted(apps, key=lambda item: launch_counts.get(item.id, 0), reverse=True)[:10]],
        "issues": [{
            "id": row.id, "name": next((item.name for item in apps if item.id == row.application_id), "应用启动"),
            "detail": "应用启动未完成，请进入运行记录查看调用链", "status": row.status,
            "time": _iso(row.updated_at), "path": f"/operations?id={row.run_id}",
        } for row in sorted(failures, key=lambda item: item.updated_at, reverse=True)[:20]],
        "governance": [
            {"name": "草稿应用", "value": sum(row.status == "draft" for row in apps), "level": "warning", "path": "/applications"},
            {"name": "停用应用", "value": sum(row.status == "disabled" for row in apps), "level": "normal", "path": "/applications"},
            {"name": "零启动应用", "value": sum(launch_counts.get(row.id, 0) == 0 for row in apps), "level": "warning", "path": "/applications"},
        ],
    }


def business_operations(ctx: AuthContext, domain: str, days: int, object_id: str = "") -> dict:
    """Return complete chart categories/series and scoped governance drill-downs.

    业务运营视图入口（GET /api/v1/operations/business）：先校验 `operations.view`，
    再按 `domain` 分发到对应域构建函数，由后者追加模块权限（`knowledge.view` /
    `bots.view` / `applications.view`）与数据范围——因此只有运营权限拿不到业务明细。

    `days` 同时决定 x 轴分类（`_complete_days`，含今天且连续完整）与统计窗口起点
    `cutoff`；`object_id` 为空表示聚合该域全部可见对象。返回体在域结果之上补
    `domain`/`objectId`/`days`/`generatedAt` 元信息（`objectId` 为空时输出 None）。
    只读，无副作用；记录不足时各图表返回空序列或 0，不报错。
    """
    require_permission(ctx, "operations.view")
    categories = _complete_days(days)
    cutoff = datetime.utcnow() - timedelta(days=days)
    builders = {
        "knowledge": _knowledge_operations,
        "bots": _bot_operations,
        "applications": _application_operations,
    }
    result = builders[domain](ctx, days, cutoff, categories, object_id)
    return {
        "domain": domain, "objectId": object_id or None, "days": days,
        "generatedAt": _iso(datetime.utcnow()),
        **result,
    }
