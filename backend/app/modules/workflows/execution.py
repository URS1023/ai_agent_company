"""Idempotent version-pinned workflow execution and currently authorized history.

中文补充：执行的对外入口层，负责幂等、版本固定、并发闸门、恢复与取消，
真正的 DAG 执行在 ``engine.execute_graph``。

关键约束与设计意图：

- **幂等**：``(workspace, owner, idempotencyKey)`` 唯一约束 + ``request_hash``（流程、版本、
  输入的规范摘要）。相同键 + 相同输入直接返回原执行（不重跑任何外部请求）；相同键 + 不同输入
  抛 ConflictError。唯一键在第一个外部请求之前先落库，防止并发重复发送。
  历史兼容：未确认写回的旧运行其 request_hash 不含 confirmWrites，见 ``request_digest``。
- **版本固定**：运行使用具体修订号（published 模式用发布修订，draft 模式用当前编辑版本），
  并把整图快照写进执行记录；之后编辑草稿不影响已开始的运行。
- **并发闸门**：``_SLOTS``（进程内 BoundedSemaphore(4)）限制同时执行数，抢不到抛 WorkflowBusy(429)，
  并提示用同一个唯一键重试（因为唯一键未落库，本次什么都没做）。
- **递归防护**：``_FLOW_STACK``（ContextVar）记录当前调用链上的 workflow_id，
  子流程递归调用会在执行前被拒绝；恢复路径同样检查。
- **权限**：执行/恢复/取消需要 ``workflows.execute``，草稿试运行还需 ``workflows.edit``；
  历史读取需要 ``workflows.view``，并且逐条重新做资源授权（撤销权限后旧记录不可读）。
- **写回确认**：图中含 output-table 时必须 ``confirmWrites=true``，否则拒绝执行。
"""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from threading import BoundedSemaphore

from sqlalchemy import String, cast, or_, select

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import AppError, ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.modules.collection.transport import validate_body_secrets
from app.modules.runtime import service as runtime
from app.modules.system.service_helpers import check_version, commit_change, flush_changes
from app.modules.workflows.engine import execute_graph
from app.modules.workflows.graph import authorize_graph, executable_graph, graph_sources, validate_json
from app.modules.workflows.models import WorkflowExecution
from app.modules.workflows.periods import period_bounds
from app.models import ModelCall, Run, RunStep
from app.modules.workflows.schemas import ExecutionControlInput, ExecutionSummary, ExecutionView, FlowGraph, RunInput
from app.modules.workflows.service import _utc_timestamp, get_workflow, revision

_SLOTS = BoundedSemaphore(4)
_FLOW_STACK: ContextVar[tuple[str, ...]] = ContextVar("workflow_execution_stack", default=())


class WorkflowBusy(AppError):
    """并发闸门已满：返回 429，并保证本次没有产生任何副作用。

    ``_SLOTS`` 是进程内的信号量，因此它只约束单个进程；多进程部署下真正的幂等保证
    仍由数据库唯一键承担，本类只用于快速失败并让调用方原键重试。
    """

    status_code = 429
    code = "workflow_busy"


def execution_summary(row: WorkflowExecution) -> ExecutionSummary:
    """List metadata without transferring all node datasets.

    中文补充：列表接口专用投影——只带状态、耗时与错误摘要，不返回 snapshot/inputs/result，
    避免列表页把每行的多兆 JSON 全量传出去（MySQL filesort 也会因此变慢）。
    """
    return ExecutionSummary(id=row.id, workflowId=row.workflow_id, revision=row.revision, runId=row.run_id,
                            version=row.version,
                            status=row.status, errorMessage=row.error_message, createTime=_utc_timestamp(row.created_at),
                            durationMs=(row.result_json or {}).get("durationMs", 0))


def execution_view(row: WorkflowExecution) -> ExecutionView:
    """详情投影：在摘要基础上附带快照、真实输入与逐节点结果。

    调用方必须先通过 ``_execution_record`` 之类的授权检查再调用本函数，
    这里不再重复鉴权，也不做字段脱敏（脱敏在写入时完成）。
    """
    return ExecutionView(**execution_summary(row).model_dump(), snapshot=row.snapshot,
                         inputs=row.input_json, result=row.result_json)


def request_digest(workflow_id: str, payload: RunInput) -> str:
    """计算幂等比较用的请求摘要，保证"同键同输入"判定稳定。

    使用 sort_keys + 紧凑分隔符的规范 JSON 再取 SHA-256；``exclude`` 用于保持历史兼容：
    旧运行（未确认写回）计算摘要时没有 confirm_writes 字段，所以 payload 中该值为 False 时
    同样排除它，让旧键重试仍能命中同一条记录。``published_revision`` 由调用方追加到摘要，
    使"草稿修订"与"发布修订"即使是同一份图也不互相命中。
    """
    # Preserve retries of pre-writeback runs, whose canonical input had no flag.
    excluded = {"confirm_writes"} if not payload.confirm_writes else set()
    canonical = json.dumps({"workflowId": workflow_id, **payload.model_dump(mode="json", by_alias=True, exclude=excluded)},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def start(ctx, workflow_id: str, payload: RunInput, *, published_revision: int | None = None) -> ExecutionView:
    """Same actor/key/input returns the persisted run without repeating any side effect.

    中文补充：``POST /api/v1/workflows/{id}/runs`` 的实现。返回的 ``ExecutionView``
    由路由层按状态映射 HTTP 码：running/waiting/cancel_requested -> 202，
    completed/cancelled -> 200，failed -> 502（失败也返回持久化的节点状态与追溯标识）。

    执行顺序（顺序本身是契约，不要调整）：
    1. 递归检查（同一调用链重复出现同一 workflow_id 即拒绝）；
    2. 取流程（``workflows.execute``）+ 输入 JSON/密钥校验；
    3. 幂等命中检查（同键同摘要直接返回；同摘要但已授权资源被撤销则报错）；
    4. 版本校验（``check_version``）与修订选择（published 需已发布；draft 需编辑权限）；
    5. 写回确认：含 output-table 且未 confirmWrites 直接拒绝；
    6. ``executable_graph`` 结构与授权校验，再做一次 ``execute=True`` 的授权
       （必须在任何 HTTP 节点发出请求之前完成，避免"先发请求后被拒"）；
    7. 抢 ``_SLOTS``，创建统一运行与执行记录并**先提交唯一键**，再执行 DAG。

    异常：ValidationError / ConflictError / WorkflowBusy / PermissionErrorEx 等。
    副作用：写 runs、workflow_executions 与审计，并实际执行图内所有节点。
    """
    stack = _FLOW_STACK.get()
    if workflow_id in stack:
        raise ValidationError("子流程形成递归调用，已在执行前阻止")
    row = get_workflow(ctx, workflow_id, "workflows.execute")
    validate_json(payload.inputs)
    validate_body_secrets(payload.inputs)
    digest = request_digest(workflow_id, payload)
    if published_revision is not None:
        digest = hashlib.sha256(f"{digest}:{published_revision}".encode()).hexdigest()
    previous = ctx.db.scalar(select(WorkflowExecution).where(
        WorkflowExecution.workspace_id == ctx.workspace_id, WorkflowExecution.owner_user_id == ctx.user_id,
        WorkflowExecution.idempotency_key == payload.idempotency_key))
    if previous is not None:
        if previous.request_hash != digest:
            raise ConflictError("此执行唯一键已用于其他参数，请查看原运行或为新任务生成新键")
        authorize_graph(ctx, FlowGraph.model_validate(previous.snapshot["graph"]))
        return execution_view(previous)
    check_version(row, payload.version)
    if payload.mode == "published":
        if row.status != "published" or row.published_revision is None:
            raise ValidationError("流程尚未发布或已停用，不能正式运行")
        selected = revision(ctx, row, published_revision or row.published_revision)
    else:
        get_workflow(ctx, workflow_id, "workflows.edit")
        selected = revision(ctx, row, row.version)
    graph = FlowGraph.model_validate(selected.snapshot["graph"])
    if any(node.kind == "output-table" for node in graph.nodes) and not payload.confirm_writes:
        raise ValidationError("此工作流包含实际数据库写入，请核对所选版本的目标表字段后明确确认执行")
    validation = executable_graph(ctx, graph)
    # A denied business action must fail before an earlier HTTP node can send a POST.
    authorize_graph(ctx, graph, active=True, execute=True)
    if not _SLOTS.acquire(blocking=False):
        raise WorkflowBusy("工作流执行繁忙，请稍后使用相同唯一键重试；本次尚未执行")
    stack_token = _FLOW_STACK.set((*stack, workflow_id))
    try:
        source_ids = graph_sources(graph)
        from app.modules.writeback.targets import get_target
        for node in graph.nodes:
            if node.kind == "output-table":
                target, _ = get_target(ctx, node.config["targetId"])
                source_ids.add(target.datasource_id)
        run = runtime.create_run(ctx.db, workspace_id=ctx.workspace_id, user_id=ctx.user_id, conversation_id="",
                                  context_snapshot={"entry": "workflow", "workflow_id": row.id,
                                                    "revision": selected.revision, "source_ids": sorted(source_ids)},
                                  model_profile="none")
        run.status = "running"
        record = WorkflowExecution(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                   workflow_id=row.id, revision=selected.revision, run_id=run.id,
                                   idempotency_key=payload.idempotency_key, request_hash=digest,
                                   snapshot=selected.snapshot, input_json=payload.inputs, status="running")
        ctx.db.add(record)
        # The unique key is reserved durably before the first external request.
        flush_changes(ctx)
        commit_change(ctx, "workflow.start", "workflows", row.id, {"execution_id": record.id, "revision": selected.revision})
        execute_graph(ctx, record, run, graph, validation["order"], payload.timeout_seconds)
        return execution_view(record)
    finally:
        _FLOW_STACK.reset(stack_token)
        _SLOTS.release()


def _execution_record(ctx, workflow_id: str, execution_id: str, permission: str) -> WorkflowExecution:
    """取一条执行记录并做归属 + 图资源授权检查。

    刻意要求 ``execution.workflow_id`` 与路径参数一致，避免用 A 流程的路径读 B 流程的执行；
    读取时也会重新 ``authorize_graph``，因此撤销数据源权限后历史结果同样不可读。
    异常：NotFoundError / PermissionErrorEx / ValidationError。
    """
    get_workflow(ctx, workflow_id, deleted=True)
    row = owned_record(ctx, WorkflowExecution, execution_id, permission)
    if row.workflow_id != workflow_id:
        raise NotFoundError("执行记录不属于当前流程")
    authorize_graph(ctx, FlowGraph.model_validate(row.snapshot["graph"]))
    return row


def _append_control(row: WorkflowExecution, key: str, action: str, *, value=None) -> bool:
    """把一次控制操作（resume/cancel）追加进执行的 controls 审计列表。

    以 ``idempotencyKey`` 去重，返回 False 表示该控制请求此前已处理过（调用方据此直接返回现有结果）。
    列表保留最近 100 条；写入 row.result_json 但不提交，由调用方的事务决定落库时机。
    """
    trace = dict(row.result_json or {})
    controls = list(trace.get("controls") or [])
    if any(item.get("idempotencyKey") == key for item in controls):
        return False
    item = {"idempotencyKey": key, "action": action, "at": datetime.utcnow().isoformat()}
    if value is not None:
        item["input"] = value
    controls.append(item)
    trace["controls"] = controls[-100:]
    row.result_json = trace
    return True


def resume(ctx, workflow_id: str, execution_id: str, payload: ExecutionControlInput) -> ExecutionView:
    """Continue the one waiting node exactly once, then resume remaining DAG nodes.

    中文补充：``POST /{id}/runs/{execution_id}/resume``。只把 ``waiting.nodeId`` 指定的
    那个节点重置为 pending，然后把用户输入作为 ``resume_value`` 传回执行器；
    已经完成的节点（HTTP、SQL、工具、写回等）不会重跑。

    关键约束：
    - 幂等：重复的控制唯一键直接返回现有视图；
    - 版本校验：``payload.version`` 过期返回 409；
    - 只有 ``waiting`` 可恢复，且必须能定位等待节点，缺 nodeId 时拒绝猜测恢复位置；
    - delay 节点未到 ``resumeAt`` 时拒绝（返回剩余等待时间）；
    - 恢复前同样重跑结构与授权校验，并重新抢并发闸门与检查递归；
    - 等待中的 step 记成 completed + ``{"resumed": True}``，保留"人工介入已发生"的痕迹。

    返回：恢复后的执行视图；若本次恢复后状态为 completed，会顺带唤醒等待该子流程的父执行。
    异常：ConflictError / ValidationError / WorkflowBusy / NotFoundError。
    """
    row = _execution_record(ctx, workflow_id, execution_id, "workflows.execute")
    if any(item.get("idempotencyKey") == payload.idempotency_key
           for item in (row.result_json or {}).get("controls") or []):
        return execution_view(row)
    check_version(row, payload.version)
    if row.status != "waiting":
        raise ConflictError("只有等待中的工作流可以恢复")
    waiting = dict((row.result_json or {}).get("waiting") or {})
    node_id = str(waiting.get("nodeId") or "")
    if not node_id:
        raise ConflictError("执行记录缺少等待节点，不能猜测恢复位置")
    if waiting.get("kind") == "delay" and waiting.get("resumeAt"):
        try:
            due = datetime.fromisoformat(str(waiting["resumeAt"]))
        except ValueError as exc:
            raise ConflictError("延迟节点恢复时间已损坏") from exc
        if datetime.utcnow() < due:
            raise ConflictError("延迟尚未到期", resume_at=due.isoformat())
    graph = FlowGraph.model_validate(row.snapshot["graph"])
    validation = executable_graph(ctx, graph)
    authorize_graph(ctx, graph, active=True, execute=True)
    if not _SLOTS.acquire(blocking=False):
        raise WorkflowBusy("工作流执行繁忙，请稍后使用相同恢复唯一键重试")
    stack = _FLOW_STACK.get()
    if workflow_id in stack:
        _SLOTS.release()
        raise ValidationError("子流程形成递归恢复，已阻止")
    stack_token = _FLOW_STACK.set((*stack, workflow_id))
    try:
        _append_control(row, payload.idempotency_key, "resume", value=payload.input)
        # 只重置等待节点；其余节点保留 completed 结果，因此外部请求不会重放。
        states = dict((row.result_json or {}).get("nodes") or {})
        if node_id in states:
            states[node_id]["status"] = "pending"
            states[node_id].pop("message", None)
        row.status = "running"
        run = ctx.db.get(Run, row.run_id)
        if run is None:
            raise NotFoundError("统一运行记录不存在")
        run.status = "running"
        run.completed_at = None
        for step in ctx.db.scalars(select(RunStep).where(
            RunStep.run_id == run.id, RunStep.status == "waiting",
        )).all():
            step.status = "completed"
            step.completed_at = datetime.utcnow()
            step.output_json = {"resumed": True, "controlKey": payload.idempotency_key}
        ctx.db.commit()
        execute_graph(
            ctx, row, run, graph, validation["order"], 300,
            resume_node_id=node_id, resume_value=payload.input,
            has_resume_value=waiting.get("kind") == "question",
            wait_context=waiting,
        )
        result = execution_view(row)
    finally:
        _FLOW_STACK.reset(stack_token)
        _SLOTS.release()
    if result.status == "completed":
        _resume_waiting_parents(ctx, execution_id)
    return result


def _resume_waiting_parents(ctx, child_id):
    """子流程完成后唤醒所有等待它的人工介入父执行（最多 20 条）。

    用固定派生的幂等键 ``child-complete:<parent>:<child>``，保证并发恢复也只推进一次；
    抢不到闸门或版本冲突时回滚并保留检查点，由当前持有者完成续推（不重试、不报错）。
    """
    parents = ctx.db.scalars(select(WorkflowExecution).where(
        WorkflowExecution.workspace_id == ctx.workspace_id,
        WorkflowExecution.owner_user_id == ctx.user_id,
        WorkflowExecution.status == "waiting",
        WorkflowExecution.result_json["waiting"]["childExecutionId"].as_string() == child_id,
    ).limit(20)).all()
    for parent in parents:
        if parent.workflow_id in _FLOW_STACK.get():
            continue
        try:
            resume(ctx, parent.workflow_id, parent.id, ExecutionControlInput(
                version=parent.version, input=None,
                idempotencyKey=hashlib.sha256(f"child-complete:{parent.id}:{child_id}".encode()).hexdigest()))
        except (WorkflowBusy, ConflictError):
            # Preserve the checkpoint; a concurrent resumer owns the continuation.
            ctx.db.rollback()


def cancel(ctx, workflow_id: str, execution_id: str, payload: ExecutionControlInput) -> ExecutionView:
    """Cancel a waiting run immediately or request a safe stop between running nodes.

    中文补充：``POST /{id}/runs/{execution_id}/cancel``。两种语义：

    - ``waiting``：立即取消——把 pending/waiting/running 节点改为 cancelled、清掉 waiting 标记、
      结束统一运行、把等待中的 step 置为 cancelled，并写审计。
    - 运行中：只能"请求取消"——状态改为 ``cancel_requested``，由执行器在节点边界观察到后
      走 ``engine._finalize_cancelled`` 收尾。路由层对这个状态返回 202，表示请求已受理。

    重要边界：取消不会撤回已经发出的外部请求（HTTP/写回可能已完成），接口文案里也不做这个承诺。

    幂等与异常：重复控制键直接返回；状态已是 completed/failed/cancelled 时抛 ConflictError
    （已结束的运行不能被取消）；版本过期同样 409。
    """
    row = _execution_record(ctx, workflow_id, execution_id, "workflows.execute")
    if any(item.get("idempotencyKey") == payload.idempotency_key
           for item in (row.result_json or {}).get("controls") or []):
        return execution_view(row)
    check_version(row, payload.version)
    if row.status in {"completed", "failed", "cancelled"}:
        raise ConflictError("已结束的工作流不能取消")
    _append_control(row, payload.idempotency_key, "cancel")
    run = ctx.db.get(Run, row.run_id)
    if row.status == "waiting":
        trace = dict(row.result_json or {})
        for state in (trace.get("nodes") or {}).values():
            if state.get("status") in {"pending", "waiting", "running"}:
                state.update(status="cancelled", message="运行已由授权用户取消")
        trace.pop("waiting", None)
        row.result_json = trace
        row.status = "cancelled"
        if run:
            runtime.finalize_run(ctx.db, run, status="cancelled")
        for step in ctx.db.scalars(select(RunStep).where(
            RunStep.run_id == row.run_id, RunStep.status == "waiting",
        )).all():
            step.status = "cancelled"
            step.completed_at = datetime.utcnow()
        commit_change(ctx, "workflow.cancel", "workflows", workflow_id,
                      {"execution_id": row.id, "run_id": row.run_id})
    else:
        row.status = "cancel_requested"
        if run:
            run.status = "cancel_requested"
        commit_change(ctx, "workflow.cancel.request", "workflows", workflow_id,
                      {"execution_id": row.id, "run_id": row.run_id})
    return execution_view(row)


def list_executions(ctx, workflow_id: str, *, limit: int = 50, offset: int = 0,
                    keyword: str = "", status: str = "all", days: int = 0,
                    time_zone: str = "UTC") -> list[ExecutionSummary]:
    """分页查询当前仍有权查看的执行记录（``GET /{id}/runs``，需要 workflows.view）。

    过滤顺序：流程归属 -> 自然日范围（按浏览器时区换算）-> 状态
    （``active`` 等价于 running/waiting/cancel_requested）-> 关键词（执行 id、run id、
    错误摘要、``v<修订号>``）-> 授权过滤 -> 分页。

    实现细节：先只查 id 排序（``with_only_columns``）再按 id 取整行，避免 MySQL 把多兆的
    JSON 快照/结果放进 filesort 缓冲而报错 1038；授权过滤在 Python 侧逐条做，
    因此 ``offset`` 计的是"可见记录"的序号，不可见记录不参与计数（也不泄露数量）。
    """
    get_workflow(ctx, workflow_id, deleted=True)
    query = visible_records(ctx, WorkflowExecution, "workflows.view").where(WorkflowExecution.workflow_id == workflow_id)
    _, _, _, start, end = period_bounds(days, time_zone)
    if days:
        query = query.where(WorkflowExecution.created_at >= start, WorkflowExecution.created_at <= end)
    if status != "all":
        statuses = ["running", "waiting", "cancel_requested"] if status == "active" else [status]
        query = query.where(WorkflowExecution.status.in_(statuses))
    keyword = keyword.strip()
    if keyword:
        query = query.where(or_(*[column.icontains(keyword, autoescape=True) for column in (
            WorkflowExecution.id, WorkflowExecution.run_id, WorkflowExecution.error_message,
            "v" + cast(WorkflowExecution.revision, String),
        )]))
    query = query.order_by(WorkflowExecution.created_at.desc(), WorkflowExecution.id)
    visible = []
    scanned = skipped = 0
    while len(visible) < limit:
        # Sort only identifiers: MySQL otherwise puts multi-megabyte JSON
        # snapshots/results in its filesort buffer and fails with error 1038.
        ids = list(ctx.db.scalars(query.with_only_columns(WorkflowExecution.id).offset(scanned).limit(100)))
        by_id = {
            row.id: row for row in ctx.db.scalars(
                select(WorkflowExecution).where(WorkflowExecution.id.in_(ids))
            )
        } if ids else {}
        records = [by_id[key] for key in ids if key in by_id]
        for row in records:
            try:
                authorize_graph(ctx, FlowGraph.model_validate(row.snapshot["graph"]))
            except (NotFoundError, PermissionErrorEx):
                continue
            if skipped < offset:
                skipped += 1
                continue
            visible.append(execution_summary(row))
            if len(visible) == limit:
                break
        scanned += len(ids)
        if len(ids) < 100:
            break
    return visible


def monitor(ctx, workflow_id: str, *, days: int = 7, time_zone: str = "UTC") -> dict:
    """Aggregate only visible, persisted executions for the workflow monitor.

    中文补充：``GET /{id}/monitor``。按浏览器时区的自然日返回逐日曲线与合计，
    统计口径为"当前仍有权查看的执行"：先按 id 读取，再逐条做资源授权，
    无权查看的记录不计入任何计数（因此这些数字不等于全库总量）。

    指标口径：tokens/cost 优先取该 run 的 ModelCall 明细求和，没有明细时回退 run.model_usage；
    有 token 但没有费用时计入 ``unpricedTokens``，提醒"未计费不等于零成本"。
    每日 ``durationMs`` 会被换算成平均耗时后再输出，不返回累计值。
    """
    get_workflow(ctx, workflow_id, deleted=True)
    zone, local_start, end, start, utc_end = period_bounds(days, time_zone)
    start_day = local_start.date()
    query = visible_records(ctx, WorkflowExecution, "workflows.view").where(
        WorkflowExecution.workflow_id == workflow_id,
        WorkflowExecution.created_at >= start,
        WorkflowExecution.created_at <= utc_end,
    )
    records = []
    ids = list(ctx.db.scalars(query.with_only_columns(WorkflowExecution.id).order_by(
        WorkflowExecution.created_at.asc(), WorkflowExecution.id,
    )))
    by_id = {
        row.id: row for row in ctx.db.scalars(
            select(WorkflowExecution).where(WorkflowExecution.id.in_(ids))
        )
    } if ids else {}
    for key in ids:
        row = by_id.get(key)
        if row is None:
            continue
        try:
            authorize_graph(ctx, FlowGraph.model_validate(row.snapshot["graph"]))
            records.append(row)
        except (NotFoundError, PermissionErrorEx):
            continue

    run_ids = [row.run_id for row in records]
    runs = {
        row.id: row for row in (
            ctx.db.scalars(select(Run).where(Run.id.in_(run_ids))).all() if run_ids else []
        )
    }
    model_calls: dict[str, list[ModelCall]] = {}
    if run_ids:
        for call in ctx.db.scalars(select(ModelCall).where(ModelCall.run_id.in_(run_ids))).all():
            model_calls.setdefault(call.run_id, []).append(call)

    series = {
        (start_day + timedelta(days=index)).isoformat(): {
            "date": (start_day + timedelta(days=index)).isoformat(),
            "runs": 0,
            "completed": 0,
            "failed": 0,
            "activeUsers": set(),
            "tokens": 0,
            "cost": 0.0,
            "durationMs": 0,
        }
        for index in range(days)
    }
    all_users: set[str] = set()
    total_tokens = 0
    total_cost = 0.0
    unpriced_tokens = 0
    total_duration = 0

    for record in records:
        item = series.get(record.created_at.replace(tzinfo=timezone.utc).astimezone(zone).date().isoformat())
        if item is None:
            continue
        run = runs.get(record.run_id)
        calls = model_calls.get(record.run_id, [])
        if calls:
            tokens = sum(max(0, int((call.usage_json or {}).get("total_tokens") or 0)) for call in calls)
        else:
            tokens = max(0, int(((run.model_usage if run else None) or {}).get("total_tokens") or 0))
        cost = max(0.0, float(run.cost or 0)) if run else 0.0
        duration = max(0, int((record.result_json or {}).get("durationMs") or 0))
        item["runs"] += 1
        item["completed"] += int(record.status == "completed")
        item["failed"] += int(record.status == "failed")
        item["activeUsers"].add(record.owner_user_id)
        item["tokens"] += tokens
        item["cost"] += cost
        item["durationMs"] += duration
        all_users.add(record.owner_user_id)
        total_tokens += tokens
        total_cost += cost
        total_duration += duration
        if tokens and not cost:
            unpriced_tokens += tokens

    output_series = []
    for item in series.values():
        output_series.append({
            **item,
            "activeUsers": len(item["activeUsers"]),
            "cost": round(item["cost"], 6),
            "averageDurationMs": round(item["durationMs"] / item["runs"]) if item["runs"] else 0,
        })
        output_series[-1].pop("durationMs")

    active_users = len(all_users)
    completed = sum(record.status == "completed" for record in records)
    failed = sum(record.status == "failed" for record in records)
    return {
        "days": days,
        "timeZone": time_zone,
        "start": start_day.isoformat(),
        "end": end.date().isoformat(),
        "totals": {
            "runs": len(records),
            "activeUsers": active_users,
            "cost": round(total_cost, 6),
            "tokens": total_tokens,
            "unpricedTokens": unpriced_tokens,
            "averageRunsPerUser": round(len(records) / active_users, 2) if active_users else 0,
            "averageDurationMs": round(total_duration / len(records)) if records else 0,
            "completed": completed,
            "failed": failed,
        },
        "series": output_series,
    }


def get_execution(ctx, workflow_id: str, execution_id: str) -> ExecutionView:
    """读取单次执行的完整详情（``GET /{id}/runs/{execution_id}``）。

    需要 ``workflows.view`` 及图内数据源的当前读取权限；已删除流程的历史同样受保护，
    权限被撤销后返回"找不到"而非部分数据。
    """
    return execution_view(_execution_record(ctx, workflow_id, execution_id, "workflows.view"))
