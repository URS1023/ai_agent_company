"""Transactional alert-node findings and read-only report/history retrieval.

职责：设备预警的两块后端服务 ——
(1) 节点侧：``evaluate_alert`` 执行固定修订的预警策略，把证据（报告 + 原始问题）与节点结果
    **在同一事务**落库；
(2) 页面侧：报告/问题的查询、处置与导出，全部走"所有权 + 完整证据链"双重校验。

位置与依赖：由 ``alerts.router``（``/alerts/reports``、``/alerts/problems``）与工作流引擎
节点调用；依赖 ``alerts.registry`` 做设备/规则/修订授权，依赖 ``workflows.execution`` 校验
执行来源，依赖 ``owned_records`` 做工作区与所有者范围过滤。

重要约束：
- 报告一旦生成不可修改：``AlertProblem.finding`` 是规则判定的原始快照，人工处置只改
  ``status`` / ``note`` / ``version``，并把每次变更追加到 ``AlertProblemEvent``（只增不改）。
- 读取历史报告时按**当前**权限重新校验（执行、规则修订、设备、以及报告里的旧数据源），
  因此设备改绑数据源后，旧报告不会绕过对旧数据源的授权。
- 列表查询先授权后分页（``authorized_page``），避免"先取 20 条再过滤"导致页内缺行。
- 导出为自包含 HTML，只做转义与拼装，**不重跑设备采集**，视图与当时一致。
"""
from __future__ import annotations

from copy import deepcopy
from html import escape
import json
from typing import Any

from sqlalchemy import select

from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.modules.alerts.models import AlertProblem, AlertProblemEvent, AlertReport
from app.modules.alerts.registry import authorized_page, device, record_view, rule_revision
from app.modules.alerts.schemas import ProblemAction
from app.modules.system.service_helpers import check_version, commit_change, flush_changes
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.models import WorkflowExecution
from app.modules.workflows.schemas import RulesConfig


def evaluate_alert(ctx: AuthContext, config, value: Any, budget: RuleBudget, *, execution_id: str, node_id: str) -> dict:
    """Evaluate a pinned policy, persisting evidence atomically with its node result.

    No commit is performed here: engine output limits or later node-local errors
    roll back the report and problems. A downstream node failure remains visible
    as executionStatus, separate from this node's actual assessment outcome.

    （中文补充）用途与语义：
    - 参数：``config`` 需含 ``rule_id`` / ``rule_revision`` / 可选 ``device_id``；
      ``value`` 是上游节点记录，``budget`` 为共享规则预算，``execution_id`` / ``node_id``
      标识本次节点执行（与报告唯一约束 ``(execution_id, node_id)`` 对应）。
    - 返回：规则结果并追加 ``alertReportId`` / ``deviceId`` / ``alertRuleId`` /
      ``alertRuleRevision`` / ``outcome``，供下游通知节点使用。
    - ``outcome`` 三态互斥且**不等于整个流程状态**：``no_data``（无参与记录，不做正常判定）、
      ``issues``（发现问题）、``normal``（未发现问题）。
    - 异常：策略修订不是该设备、设备已取消 alert 用途、``recordKeyField`` 缺失/为空/类型错误
      或超过 500 字符时抛 ``ValidationError``，此时不生成任何报告（宁可不报，不留残报告）。
    - 副作用：写 ``equipment_alert_reports`` 与 ``equipment_alert_problems``，只 flush 不 commit。
    """
    from app.modules.workflows.nodes import bounded_output, evaluate_rules

    selected = rule_revision(ctx, config.rule_id, config.rule_revision, active=True, execute=True)
    snapshot = deepcopy(selected.snapshot)
    if config.device_id and snapshot["deviceId"] != config.device_id:
        raise ValidationError("所选预警策略不属于当前设备，请在节点内选择或新建此设备的策略")
    equipment = device(ctx, snapshot["deviceId"], active=True)
    if "alert" not in equipment.domains:
        raise ValidationError("设备已取消预警用途，不能执行设备预警")
    result = evaluate_rules(RulesConfig(mode="alert", rules=snapshot["rules"]), value, budget)
    bounded_output(result)
    keys = []
    field = snapshot["recordKeyField"]
    for index, row in enumerate(result["data"]):
        budget.consume()
        if field:
            if field not in row or row[field] is None or isinstance(row[field], (dict, list, bool)):
                raise ValidationError("问题定位字段缺失、空值或类型错误，未生成不完整报告")
            key = str(row[field])
            if len(key) > 500:
                raise ValidationError("问题定位字段超过 500 字符")
        else:
            key = str(index + 1)
        keys.append(key)
    outcome = "no_data" if not result["assessments"] else "issues" if result["issues"] else "normal"
    report = AlertReport(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, rule_id=selected.rule_id,
                         device_id=equipment.id, execution_id=execution_id, node_id=node_id,
                         title=snapshot["name"] + " · 预警报告", outcome=outcome,
                         snapshot={"rule": snapshot, "ruleRevision": selected.revision,
                                   "device": record_view(ctx, "devices", equipment)}, result_json=result)
    ctx.db.add(report)
    flush_changes(ctx)
    for finding in result["issues"]:
        budget.consume()
        ctx.db.add(AlertProblem(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                report_id=report.id, row_index=finding["rowIndex"],
                                record_key=keys[finding["rowIndex"]], finding=finding))
    flush_changes(ctx)
    return {**result, "alertReportId": report.id, "deviceId": equipment.id,
            "alertRuleId": selected.rule_id, "alertRuleRevision": selected.revision, "outcome": outcome}


def get_report(ctx: AuthContext, report_id: str, action: str = "view") -> AlertReport:
    """Report reads also honor current workflow/source/equipment permissions.

    （中文补充）``action`` 决定所需业务权限（``view`` / ``export`` 等），实际权限串为
    ``alerts.<action>``。校验链条：报告所有权 → 执行来源（含工作流可执行性）→ 规则修订 →
    设备（允许已删除）→ **报告快照里记录的旧数据源**。最后一步是为了防止设备后来改绑数据源
    后，历史报告变成绕过授权的旁路。任一步不可见即抛 ``NotFoundError`` / ``PermissionErrorEx``。
    只读，无副作用。
    """
    from app.modules.workflows.execution import get_execution

    row = owned_record(ctx, AlertReport, report_id, "alerts." + action)
    execution = ctx.db.get(WorkflowExecution, row.execution_id)
    if execution is None:
        raise NotFoundError("报告的执行来源不存在")
    get_execution(ctx, execution.workflow_id, execution.id)
    rule_revision(ctx, row.rule_id, row.snapshot["ruleRevision"])
    device(ctx, row.device_id, deleted=True)
    # Protect any earlier device source snapshot even after current mapping changes.
    old_source = row.snapshot["device"].get("datasourceId")
    if old_source:
        from app.models import DataSource
        owned_record(ctx, DataSource, old_source, "datasources.view")
    return row


def report_view(ctx: AuthContext, row: AlertReport, *, detail: bool = False) -> dict:
    """Separate no-data/normal/issues from whole-workflow running/failed/completed.

    （中文补充）``detail=True`` 时追加 ``snapshot`` / ``result`` / ``executionError`` 与
    该执行的写回操作（``writeOperations``），供导出与详情页；``detail=False`` 只返回元数据。
    注意 ``outcome``（本次判定的业务结论）与 ``executionStatus``（流程整体是否跑完）必须分开看，
    流程失败不代表本次判定"正常"，无数据也不等于正常。
    只读；``execution`` 理论上必存在（外键保证），若被清理则此处会抛 ``AttributeError``。
    """
    execution = ctx.db.get(WorkflowExecution, row.execution_id)
    view = {"id": row.id, "title": row.title, "ruleId": row.rule_id, "deviceId": row.device_id,
            "deviceName": row.snapshot["device"]["name"], "ruleRevision": row.snapshot["ruleRevision"],
            "executionId": row.execution_id, "workflowId": execution.workflow_id,
            "executionStatus": execution.status, "nodeId": row.node_id, "outcome": row.outcome,
            "summary": row.result_json.get("summary") or {}, "createTime": row.created_at.isoformat()}
    if detail:
        view.update(snapshot=row.snapshot, result=row.result_json, executionError=execution.error_message)
        from app.modules.writeback.execution import for_execution
        view["writeOperations"] = [item.model_dump() for item in for_execution(ctx, row.execution_id)]
    return view


def list_reports(ctx: AuthContext, *, limit: int, offset: int, device_id: str = "", rule_id: str = "") -> list[dict]:
    """Return source-authorized report metadata with true pre-pagination filtering.

    （中文补充）按 ``device_id`` / ``rule_id`` 附加过滤后交给 ``authorized_page``：
    每行都用 ``get_report`` 复核，因此**分页是"过滤后的分页"**，不会出现页内空位；
    代价是必要时会向后多扫描若干批（单批 200 行）。只读。
    """
    query = visible_records(ctx, AlertReport, "alerts.view")
    if device_id:
        query = query.where(AlertReport.device_id == device_id)
    if rule_id:
        query = query.where(AlertReport.rule_id == rule_id)
    rows = authorized_page(ctx, query.order_by(AlertReport.created_at.desc(), AlertReport.id),
                           lambda row: get_report(ctx, row.id), limit=limit, offset=offset)
    return [report_view(ctx, row) for row in rows]


def get_problem(ctx: AuthContext, problem_id: str, action: str = "view") -> AlertProblem:
    """Problem ownership and its complete evidence chain are both required.

    （中文补充）问题不能脱离报告单独授权：这里先按 ``alerts.<action>`` 取问题，再回溯其报告，
    因此只要报告链路（执行/规则/设备/旧数据源）中任一环节失权，问题也不可见。
    """
    row = owned_record(ctx, AlertProblem, problem_id, "alerts." + action)
    get_report(ctx, row.report_id)
    return row


def problem_view(row: AlertProblem) -> dict:
    """Return original finding plus separate, versioned manual processing status.

    （中文补充）``finding`` 是生成时的原始判定（行号、规则项、级别、说明），人工处置**不会**
    改写它；处置结果单独放在 ``status`` / ``note`` / ``version`` 三个可变字段里，
    前端应并列展示，不要用处置状态覆盖原始结论。

    处置状态机取值（``ProblemAction.status`` 的字面量）：
    - ``open``：待处理（新建问题的初始值，也可由其他状态"重新打开"回到此态）；
    - ``acknowledged``：已知悉/处理中；
    - ``resolved``：已解决；
    - ``ignored``：已忽略（需写明理由）。
    约束：后端**不校验状态迁移合法性**，任意状态之间都可互转（含 resolved → open），
    业务侧靠必填 ``note`` 与事件流审计来约束；因此不要把该字段当作强流程状态机使用。
    只读，不写库。
    """
    return {"id": row.id, "reportId": row.report_id, "rowIndex": row.row_index,
            "recordKey": row.record_key, **row.finding, "status": row.status, "note": row.note,
            "version": row.version, "createTime": row.created_at.isoformat()}


def list_problems(ctx: AuthContext, *, limit: int, offset: int, report_id: str = "", status: str = "") -> list[dict]:
    """Filter problem handling state without changing historical assessment totals.

    （中文补充）``status`` 过滤的是**人工处置状态**，不会改变报告中"原始发现的问题条数"，
    统计口径必须分别说明。``report_id`` 给定时报告链路已整体校验，可直接 SQL 分页；
    未给定时同一个报告会有多条问题，故用 ``allowed_reports`` 在本调用内缓存报告级判定结果
    （含拒绝），避免重复校验；但缓存**不跨请求复用**，也不会把授权结果或 ORM 证据带出去。
    只读。
    """
    query = visible_records(ctx, AlertProblem, "alerts.view")
    if report_id:
        get_report(ctx, report_id)
        query = query.where(AlertProblem.report_id == report_id)
    if status:
        query = query.where(AlertProblem.status == status)
    query = query.order_by(AlertProblem.created_at.desc(), AlertProblem.id)
    if report_id:
        # Its full evidence chain was checked above; per-problem ownership is
        # still enforced by visible_records, so SQL can page directly.
        rows = ctx.db.scalars(query.limit(limit).offset(offset)).all()
    else:
        # Many findings share one report. Reuse its decision only during this
        # list call, including denied reports, without retaining ORM evidence
        # payloads or carrying authorization into a subsequent request.
        allowed_reports: dict[str, bool] = {}

        def authorize_problem(row: AlertProblem) -> None:
            if row.report_id not in allowed_reports:
                try:
                    get_report(ctx, row.report_id)
                except (NotFoundError, PermissionErrorEx):
                    allowed_reports[row.report_id] = False
                    raise
                allowed_reports[row.report_id] = True
            if not allowed_reports[row.report_id]:
                raise NotFoundError("问题的报告不存在或不在当前操作的数据范围内")

        rows = authorized_page(ctx, query, authorize_problem, limit=limit, offset=offset)
    return [problem_view(row) for row in rows]


def handle_problem(ctx: AuthContext, problem_id: str, payload: ProblemAction) -> dict:
    """Append a mandatory reason; optimistic locking prevents lost human updates.

    （中文补充）需要报告的 ``alerts.edit`` 权限（经 ``get_problem(..., "edit")`` 校验）。
    流程：校验版本（``payload.version`` 过期报 409）→ 追加 ``AlertProblemEvent``
    （记录 previous/next 状态与理由，只增不改）→ 更新问题的 status/note 并把 version+1。
    返回处置后的 ``problem_view``。副作用：写 ``equipment_alert_problem_events``、
    更新 ``equipment_alert_problems``、写审计（``commit_change`` 会提交事务）。
    ``note`` 由 schema 强制非空白，处置理由不可省略。
    """
    row = get_problem(ctx, problem_id, "edit")
    check_version(row, payload.version)
    ctx.db.add(AlertProblemEvent(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                 problem_id=row.id, previous_status=row.status,
                                 next_status=payload.status, note=payload.note))
    row.status, row.note, row.version = payload.status, payload.note, row.version + 1
    commit_change(ctx, "alert.problem.handle", "alerts", row.id, {"status": row.status})
    return problem_view(row)


def problem_events(ctx: AuthContext, problem_id: str, *, limit: int = 50, offset: int = 0) -> list[dict]:
    """Read append-only processing history, protected through the parent problem.

    （中文补充）事件表本身没有独立的授权语义，一律先 ``get_problem`` 回溯报告链路，
    再按工作区 + problem_id 查询；按创建时间正序返回，便于前端按时间线展示处置过程。
    ``actorId`` 取自事件行的 ``owner_user_id``（即处置人）。只读。
    """
    get_problem(ctx, problem_id)
    records = ctx.db.scalars(select(AlertProblemEvent).where(AlertProblemEvent.workspace_id == ctx.workspace_id,
                              AlertProblemEvent.problem_id == problem_id).order_by(AlertProblemEvent.created_at, AlertProblemEvent.id).limit(limit).offset(offset)).all()
    return [{"id": row.id, "actorId": row.owner_user_id, "previousStatus": row.previous_status,
             "status": row.next_status, "note": row.note, "createTime": row.created_at.isoformat()} for row in records]


def export_report(ctx: AuthContext, report_id: str) -> str:
    """Generate a self-contained escaped HTML report without rerunning equipment.

    （中文补充）需要报告的 ``alerts.export`` 权限（``get_report(..., "export")``）。
    返回一段完整 HTML 字符串（不写文件、不落临时目录），内含五个部分：标题与判定、
    原始问题表、实际数据与逐项结果、规则与设备快照、实际数据库写入凭据。
    约束：所有插值都经 ``html.escape``，且快照/结果以 ``<pre>`` 内 JSON 呈现 ——
    报告内容含用户数据与 SQL 结果，**新增字段时必须同样转义**，否则会引入 XSS。
    报告中只呈现当时证据，不会重新采集设备数据，因此结果与生成时刻一致（可复现）。
    """
    row = get_report(ctx, report_id, "export")
    data = report_view(ctx, row, detail=True)
    labels = {"normal": "未发现问题", "issues": "发现问题", "no_data": "无数据，不能判定正常"}
    findings = "".join("<tr>" + "".join("<td>" + escape(str(item[key])) + "</td>" for key in
                        ("rowIndex", "ruleName", "severity", "message")) + "</tr>" for item in data["result"]["issues"])
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>预警报告</title>"
            "<style>body{font:15px sans-serif;max-width:1100px;margin:32px auto;padding:20px;color:#17263c}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #dce2ec;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            f"<h1>{escape(row.title)}</h1><p>设备：{escape(data['deviceName'])} · 规则修订 v{data['ruleRevision']}</p>"
            f"<p>判定：{labels[row.outcome]} · 工作流状态：{escape(data['executionStatus'])}</p>"
            f"<p>执行标识：{escape(row.execution_id)} · 节点：{escape(row.node_id)} · 时间：{escape(data['createTime'])}</p>"
            "<h2>原始问题</h2><table><tr><th>原始行号（从0起）</th><th>规则项</th><th>等级</th><th>说明</th></tr>"
            + findings + "</table><h2>实际数据与逐项结果</h2><pre>" + escape(json.dumps(data['result'], ensure_ascii=False, indent=2))
            + "</pre><h2>规则与设备快照</h2><pre>" + escape(json.dumps(data['snapshot'], ensure_ascii=False, indent=2))
            + "</pre><h2>实际数据库写入凭据</h2><pre>" + escape(json.dumps(data.get('writeOperations', []), ensure_ascii=False, indent=2)) + "</pre></html>")
