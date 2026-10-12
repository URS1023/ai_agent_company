"""把已经授权的成功查询保存为真实大屏，不重新查询数据库或调用模型。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from threading import RLock

from sqlalchemy import select

from ...common.authorization import require_permission
from ...errors import PermissionErrorEx, ValidationError
from ...models import Run, ToolCall
from ...providers.tool.base import ToolResult
from ..business_ai.dashboard_actions import invocation
from ..reports import service
from ..reports.models import Report, ReportRevision
from ..reports.schemas import ReportInput
from .chart_renderer import load_authorized_query
from .resume import validated_resume_calls


_SAVE_LOCK = RLock()
_MODE = "saved_query_snapshot"


def current_query_ids(ctx, run) -> list[str]:
    """仅从本轮成功业务回执提取标识，覆盖诊断不能混进业务大屏。"""
    from ..analytics.models import QueryHistory

    calls = ctx.db.scalars(select(ToolCall).where(ToolCall.workspace_id == ctx.workspace_id,
        ToolCall.run_id == run.id, ToolCall.tool_name.in_(["query_business_data", "query_inventory_data"]),
        ToolCall.status.in_(["success", "completed"]))).all()
    identities = []
    for call in calls:
        result = call.result_json or {}
        data = result.get("data") or {}
        if result.get("status") != "success" or not isinstance(data, dict) or data.get("success") is False:
            continue
        for item in data.get("results") if isinstance(data.get("results"), list) else [data]:
            if isinstance(item, dict) and item.get("queryId") and item.get("success") is not False:
                identities.append(item["queryId"])
    rows = ctx.db.scalars(select(QueryHistory).where(QueryHistory.id.in_(set(identities)),
        QueryHistory.workspace_id == ctx.workspace_id, QueryHistory.owner_user_id == ctx.user_id,
        QueryHistory.status == "success")).all() if identities else []
    return [q.id for q in sorted(rows, key=lambda q: (q.created_at, q.id))
            if q.query_mode != "profile" and (q.result_json or {}).get("queryType") != "profile"
            and (q.result_json or {}).get("evidenceKind") != "coverage_only"]


def _text(identity: str, value: str, x: int, y: int, width: int, height: int, size: int = 22) -> dict:
    """标题与范围说明使用宋体；查询列和值仍原样保留。"""
    return {"id": identity, "component": "JText", "componentName": value[:100],
            "x": x, "y": y, "w": width, "h": height,
            "config": {"option": {"body": {"text": value, "fontSize": size,
                       "fontFamily": "SimSun, 宋体, serif", "color": "#E4F4FF"}}}}


def snapshot_content(title: str, queries: list) -> tuple[dict, list[dict]]:
    """按留存结果构造表格，不推断业务合计，不丢弃返回行列，不冒称实时数据。"""
    prepared = []
    for index, query in enumerate(queries):
        result = query.result_json or {}
        rows = result.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValidationError("保存的查询结果缺少有效行数据，不能创建大屏")
        if not isinstance(result.get("truncated"), bool):
            raise ValidationError("保存的查询缺少截断标记，无法确认大屏的数据范围")
        supplied = result.get("columns") or []
        if not isinstance(supplied, list) or any(not isinstance(column, dict) for column in supplied):
            raise ValidationError("保存的查询字段格式无效")
        headers = {str(column["columnName"]): str(column.get("comment") or column["columnName"])
                   for column in supplied if column.get("columnName")}
        for row in rows:
            for key in row:
                headers.setdefault(str(key), str(key))
        if not headers:
            raise ValidationError("保存的查询缺少字段信息，不能创建可读的大屏")
        label = str(query.query_text or "").strip()
        if query.query_mode == "sql" or not label or len(label) > 90:
            label = f"查询结果 {index + 1}"
        prepared.append((query, headers, label))

    # 按真实字段宽度自动选单栏；超宽结果分成带相同识别字段的表，避免全部列挤成细线。
    width = 1920
    columns = 2 if len(queries) > 1 and all(len(headers) <= 7 for _, headers, _ in prepared) else 1
    panel_width, panel_height = (width - 72 - 24 * (columns - 1)) // columns, 410
    capacity = max(2, panel_width // 120)
    sections = []
    for query, headers, label in prepared:
        fields = list(headers)
        groups = [fields] if len(fields) <= capacity else [
            [fields[0], *fields[start:start + capacity - 1]] for start in range(1, len(fields), capacity - 1)]
        for page, displayed in enumerate(groups, start=1):
            part_label = label if len(groups) == 1 else f"{label} · 字段分组 {page}/{len(groups)}"
            sections.append((query, headers, part_label, displayed))
    height = max(720, 170 + math.ceil(len(sections) / columns) * (panel_height + 24))
    if len(sections) > 32 or height > 16384:
        raise ValidationError("查询字段过多，无法在可读大屏容量内完整展示；请显式选择展示字段或先汇总查询",
                              recovery="correct_input", execution_started=False, stage="layout_budget",
                              panel_count=len(sections), max_panels=32, height=height, max_height=16384,
                              query_columns=[{"query_id": query.id, "columns": len(headers)} for query, headers, _ in prepared])
    rows_json = {query.id: json.dumps(query.result_json["data"], ensure_ascii=False) for query, _, _ in prepared}
    # 三处原始快照及 JSON 转义按四份预算，在构造深拷贝前控制总量。
    snapshot_bytes = sum(len(rows_json[query.id].encode("utf-8")) * 4 for query, _, _, _ in sections)
    if snapshot_bytes > 16 * 1024 * 1024:
        raise ValidationError("原始查询快照过大，完整复制将超过大屏预算；请先按展示需求汇总查询",
                              recovery="correct_input", execution_started=False, stage="snapshot_budget",
                              estimated_bytes=snapshot_bytes, maximum_snapshot_bytes=16 * 1024 * 1024,
                              panel_count=len(sections), query_ids=[query.id for query, _, _ in prepared])
    widgets = [
        _text("query-title", title, 36, 24, width - 72, 52, 34),
        _text("query-scope", "已保存的查询快照；保留原始字段与返回范围，不自动刷新，不代表实时或全库总量。",
              36, 88, width - 72, 40, 20),
    ]
    scopes = []
    for index, (query, headers, label, displayed) in enumerate(sections):
        result = query.result_json
        rows = result["data"]
        scope = {"queryId": query.id, "datasourceId": query.datasource_id,
                 "returnedRows": len(rows), "columnCount": len(headers),
                 "truncated": result["truncated"], "queriedAt": query.created_at.isoformat(),
                 "widgetId": f"query-data-{index}", "requiredFields": list(headers),
                 "displayedFields": displayed, "unshownFields": []}
        scopes.append(scope)
        limit_note = "（已截断，仅为部分结果）" if scope["truncated"] else "（当前查询返回范围）"
        scope_text = f"保存 {len(rows)} 行 · {query.created_at.isoformat(sep=' ', timespec='seconds')} {limit_note}"
        x, y = 36 + (index % columns) * (panel_width + 24), 148 + (index // columns) * (panel_height + 24)
        widgets.append(_text(f"query-label-{index}", label, x, y, panel_width, 42, 24))
        widgets.append(_text(f"query-note-{index}", scope_text, x, y + 46, panel_width, 34, 17))
        # 静态数据类型避免页面首次加载就执行 SQL；query 中的 SQL 仅保留来源追溯。
        widgets.append({
            "id": f"query-data-{index}", "component": "JScrollBoard", "componentName": label,
            "x": x, "y": y + 86, "w": panel_width, "h": panel_height - 90,
            "query": {"datasourceId": query.datasource_id, "sql": query.generated_sql,
                      "parameters": deepcopy(result.get("parameters") or {})},
            "datasourceId": query.datasource_id, "generatedSql": query.generated_sql,
            "data": deepcopy(rows), "dataStatus": "verified",
            "config": {"dataType": 1, "datasourceId": query.datasource_id, "sql": query.generated_sql,
                       "queryId": query.id, "dataStatus": "verified", "verifiedAt": scope["queriedAt"],
                       "chartDataParsed": deepcopy(rows), "chartData": rows_json[query.id],
                       "snapshotOnly": True, "truncated": scope["truncated"], "columnLabels": headers,
                       "snapshotBinding": {"columns": displayed},
                       "option": {"header": [{"key": key, "label": headers[key]} for key in displayed],
                                  "rowNum": 7, "headerFontSize": 21, "bodyFontSize": 19,
                                  "fontFamily": "SimSun, 宋体, serif", "headerBGC": "#124C70",
                                  "oddRowBGC": "#0C263F", "evenRowBGC": "#12334D",
                                  "headerFontColor": "#E0F4FF", "fontColor": "#D9EEFF"}},
        })
    return {"canvas": {"width": width, "height": height, "backgroundColor": "#061225",
                       "refreshSeconds": 0, "fontFamily": "SimSun, 宋体, serif"}, "widgets": widgets}, scopes


def _result(report, query_ids: list[str], scopes: list[dict], *, reused: bool) -> ToolResult:
    """返回可打开的大屏标识及范围，供交付核验和后续图片导出使用。"""
    # 幂等复用展示的是已保存版本，不能把新算法拟定的字段范围误报成旧报表的实际内容。
    saved_scopes = (report.lineage or {}).get("queryScopes")
    if isinstance(saved_scopes, list):
        scopes = saved_scopes
    coverage = {}
    for scope in scopes:
        row = coverage.setdefault(scope["queryId"], {"queryId": scope["queryId"], "displayedFields": [],
                                                    "requiredFields": scope.get("requiredFields") or [],
                                                    "unshownFields": scope.get("unshownFields") or []})
        row["displayedFields"] = list(dict.fromkeys(row["displayedFields"] + (scope.get("displayedFields") or [])))
    for row in coverage.values():
        row["coverageComplete"] = not (set(row["requiredFields"]) - set(row["displayedFields"]))
    coverage_checked = bool(scopes) and all("requiredFields" in scope for scope in scopes)
    return ToolResult(status="success", data={
        "resultType": "dashboard", "reportId": report.id, "dashboard_id": report.id, "title": report.name,
        "href": f"/report/view/{report.id}?workspace_id={report.workspace_id}", "queryIds": query_ids, "sourceQueryIds": query_ids,
        "previewPath": f"/report/view/{report.id}?workspace_id={report.workspace_id}",
        "templateMode": report.template_mode, "templateId": report.template_id,
        "widgetCount": len((report.content or {}).get("widgets") or []), "datasourceIds": report.datasource_ids,
        "generationMode": _MODE, "scopes": scopes, "fieldCoverage": list(coverage.values()),
        "quality": {"coverageComplete": coverage_checked and all(row["coverageComplete"] for row in coverage.values()),
                    "coverageChecked": coverage_checked,
                    "queries": list(coverage.values()), "financialScope": (report.lineage or {}).get("financialScope")},
        "reused": reused, "version": report.version,
        "scopeNotes": (report.lineage or {}).get("financialScopeNotes") or [],
        "description": "已保存真实查询快照大屏，展示原始返回范围，关闭自动刷新；不代表实时或全库总量。"
                       + " ".join((report.lineage or {}).get("financialScopeNotes") or []),
        "next_action": "需要放入 Word 时，调用 render_dashboard_image 获取可信大屏图片，再嵌入文档。",
    }, metadata={"generationMode": _MODE, "source_query_ids": query_ids})


def _fingerprint(query_ids: list[str], title: str, *, template_id=None, bindings=None) -> str:
    """以服务端规范化参数标识同一个保存请求，不接受模型提交的幂等键。"""
    payload = {"query_ids": query_ids, "title": title}
    if template_id:
        payload.update(template_id=template_id, bindings=bindings or [])
    return sha256(json.dumps(payload, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _result_fingerprint(queries) -> str:
    """结果摘要：同 query_ids+title 但实际取数变化时不复用旧快照（评审 R22）。

    摘要包含每条的截断标记与全部返回行，保证“保存的查询快照”和当时看到的
    数据一致；结果变化时生成新快照，而不是静默返回上一次保存的旧值。
    """
    digests = []
    for query in queries:
        result = query.result_json or {}
        rows = result.get("data") if isinstance(result.get("data"), list) else []
        digests.append(sha256(json.dumps(
            {"query_id": query.id, "truncated": bool(result.get("truncated")), "rows": rows},
            ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest())
    return sha256(json.dumps({"results": digests}, sort_keys=True).encode("utf-8")).hexdigest()


def _check_reusable(ctx, report: Report | None, fingerprint: str, *, expected_version: int | None = None) -> Report:
    """同一请求已保存的结果只读复用；删除、修改或来源变化都不能触发自动重建。"""
    if report is None or report.workspace_id != ctx.workspace_id or report.owner_user_id != ctx.user_id:
        raise ValidationError("已保存的快照大屏不再可访问，不会自动创建替代副本")
    if report.status == "deleted":
        raise ValidationError("本轮同一快照大屏已被删除，不会自动重建；请明确发起新的创建任务")
    if expected_version is None:
        # 创建事务中的自动 flush 也可能递增版本，不能硬编码初始版本为 1；以首条不可变修订为准。
        expected_version = ctx.db.scalar(select(ReportRevision.revision).where(
            ReportRevision.workspace_id == ctx.workspace_id, ReportRevision.owner_user_id == ctx.user_id,
            ReportRevision.report_id == report.id,
        ).order_by(ReportRevision.revision).limit(1))
    if (report.status != "draft" or report.version != expected_version
            or (report.lineage or {}).get("generationMode") != _MODE
            or (report.lineage or {}).get("requestFingerprint") != fingerprint):
        raise ValidationError("已保存的快照大屏已被修改，请先核对现有版本；不会覆盖或自动创建副本")
    return service.get_report(ctx, report.id, permission="dashboards.create")


def _resumed_report(ctx, run: Run, fingerprint: str) -> Report | None:
    """只沿经过鉴权的续办回执查找同参数大屏，绝不遍历同会话的任意历史结果。"""
    for call in validated_resume_calls(ctx.db, run):
        if call.tool_name != "create_dashboard_from_queries":
            continue
        args = call.arguments_json or {}
        # 旧版本或人工回执可能保留 camelCase；恢复时统一读取两种正式等价写法，
        # 避免任务已经保存成功却因字段大小写差异重新生成或误报未完成。
        query_ids = args.get("query_ids") or args.get("queryIds")
        if not isinstance(query_ids, list):
            continue
        title = str(args.get("title") or args.get("reportName") or "业务查询结果大屏").strip()
        data = (call.result_json or {}).get("data") or {}
        if not isinstance(data, dict):
            raise ValidationError("已完成大屏的保存回执不完整，请核对原任务；不会自动创建副本")
        report_id = data.get("reportId") or data.get("dashboard_id") or data.get("report_id")
        report = ctx.db.get(Report, report_id) if report_id else None
        if _fingerprint(query_ids, title, template_id=args.get("template_id") or data.get("templateId"),
                        bindings=args.get("bindings")) != fingerprint:
            continue
        version = data.get("version")
        if version is not None and (type(version) is not int or version < 1):
            raise ValidationError("已完成大屏的版本回执不完整，请核对原任务；不会自动创建副本")
        return _check_reusable(ctx, report, fingerprint, expected_version=version)
    return None


def create_dashboard_from_queries(ctx, tool_context: dict, args: dict) -> ToolResult:
    """逐条验证本轮或明确续作的查询回执，以同轮同参数幂等创建大屏。"""
    from ..business_ai.context import auth_context_from_tool

    live_ctx = auth_context_from_tool(tool_context)
    if (ctx.workspace_id, ctx.user_id) != (live_ctx.workspace_id, live_ctx.user_id):
        raise PermissionErrorEx("大屏工具身份与当前执行身份不一致")
    ctx = live_ctx
    require_permission(ctx, "dashboards.create")
    require_permission(ctx, "analytics.view")
    run = invocation(ctx, tool_context)
    query_ids = args.get("query_ids")
    if (not isinstance(query_ids, list) or not 1 <= len(query_ids) <= 8
            or any(not isinstance(value, str) or not value.strip() for value in query_ids)
            or len(set(query_ids)) != len(query_ids)):
        raise ValidationError("query_ids 需要 1 至 8 个不重复的已成功查询标识")
    title = str(args.get("title") or "业务查询结果大屏").strip()
    if not title or len(title) > 200:
        raise ValidationError("大屏标题长度应为 1 至 200 个字符")
    snapshot = run.context_snapshot or {}
    template_id = (tool_context.get("selected_dashboard_template_id") or snapshot.get("dashboard_template_id")
                   or args.get("template_id"))
    manual = bool(template_id or snapshot.get("dashboard_template_mode") == "manual"
                  or tool_context.get("selected_dashboard_template_mode") == "manual")
    if manual and not template_id:
        raise ValidationError("手选大屏模板缺少有效模板标识，请重新选择模板")
    queries = [load_authorized_query(ctx, tool_context, query_id, permission="analytics.view") for query_id in query_ids]
    selected = set(tool_context.get("selected_datasource_ids") or [])
    if selected and any(query.datasource_id not in selected for query in queries):
        raise ValidationError("查询来源超出本次工具的数据源范围")
    from .query_presentation_quality import query_financial_scope
    display_title, financial_notes, financial_scope = query_financial_scope(queries, title)
    if len(display_title) > 200:
        # 范围限定必须完整保留；仅截短展示标题，原请求仍参与下方幂等指纹。
        qualifier = display_title[len(title):] if display_title.startswith(title) else ""
        display_title = title[:max(0, 200 - len(qualifier))] + qualifier if qualifier else display_title[:200]
    if manual:
        from ..reports.templates import get_template
        from .template_query_snapshot import template_snapshot_content
        template = get_template(ctx, template_id)
        content, scopes = template_snapshot_content(ctx, display_title, queries, template, args.get("bindings"))
    else:
        content, scopes = snapshot_content(display_title, queries)
    fingerprint = _fingerprint(query_ids, title, template_id=template_id, bindings=args.get("bindings"))
    result_fp = _result_fingerprint(queries)
    # 运行租约已限制执行者；此锁补足进程内并发，数据库行锁序列化跨线程的保存事务。
    with _SAVE_LOCK:
        run = ctx.db.scalar(select(Run).where(Run.id == run.id).with_for_update().execution_options(populate_existing=True))
        inherited_report = _resumed_report(ctx, run, fingerprint)
        if inherited_report is not None:
            if (inherited_report.lineage or {}).get("resultFingerprint") == result_fp:
                return _result(inherited_report, query_ids, scopes, reused=True)
            # 参数相同但取数结果已变化：旧快照不再代表当前结果，切到新建（R22）。
        previous = ctx.db.scalar(select(Report).where(
            Report.workspace_id == ctx.workspace_id, Report.owner_user_id == ctx.user_id,
            Report.lineage["invocationRunId"].as_string() == run.id,
            Report.lineage["requestFingerprint"].as_string() == fingerprint,
            Report.lineage["generationMode"].as_string() == _MODE,
        ).order_by(Report.created_at).limit(1))
        if previous is not None and (previous.lineage or {}).get("resultFingerprint") == result_fp:
            previous = _check_reusable(ctx, previous, fingerprint)
            return _result(previous, query_ids, scopes, reused=True)
        saved = service.save_report(ctx, ReportInput(
            reportName=display_title, description="来自本轮成功查询的留存快照，不自动刷新；各组件标明返回行数、截断状态与查询时间。"
                                               + " ".join(financial_notes),
            datasourceIds=list(dict.fromkeys(query.datasource_id for query in queries)), content=content,
            width=content["canvas"]["width"], height=content["canvas"]["height"],
            backgroundColor=content["canvas"].get("backgroundColor") or "#061225",
            refreshSeconds=0, templateMode="manual" if manual else "none", templateId=template_id, status="draft",
        ), lineage={"entry": "ai_chat", "dataStatus": "verified", "generationMode": _MODE,
                    "invocationRunId": run.id, "requestFingerprint": fingerprint,
                    "resultFingerprint": result_fp,
                    "queryIds": query_ids, "sourceQueryIds": query_ids, "queryScopes": scopes,
                    "financialScope": financial_scope, "financialScopeNotes": financial_notes},
            revision_reason="将已授权成功查询保存为不自动刷新的数据快照大屏")
        report = ctx.db.get(Report, saved.id)
        receipts = dict((run.context_snapshot or {}).get("query_dashboard_receipts") or {})
        receipts[fingerprint] = {"report_id": report.id, "query_ids": query_ids, "generation_mode": _MODE}
        run.context_snapshot = {**(run.context_snapshot or {}), "query_dashboard_receipts": receipts}
        ctx.db.commit()
        return _result(report, query_ids, scopes, reused=False)
