"""从已保存的大屏和查询快照核验交付，不执行任何业务查询或模型调用。"""
from __future__ import annotations

import re
import math
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models import DataSource, ToolCall
from app.common.json_numbers import preserve_json_integers
from app.modules.analytics.models import QueryHistory
from app.modules.business_ai.models import BusinessActionReceipt
from app.modules.reports.models import Report


_SNAPSHOT_NUMERIC = {"JStatsSummary", "JNumber", "JCountTo", "JBar", "JLine", "JArea", "JBarGroup", "JHorizontalBar"}
_SNAPSHOT_TABLES = {"JScrollBoard", "JTable"}
_NUMBER_TEXT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$", re.ASCII)


def _design_only(runs) -> bool:
    """只有明确要求纯设计、模板或示例的任务才能免除真实业务数据验收。"""
    intent = False
    for run in runs:
        snapshot = run.context_snapshot or {}
        requests = [snapshot.get("submitted_content") or snapshot.get("user_request") or ""]
        requests.extend(item.get("content") or "" for item in snapshot.get("steering_messages") or [])
        if snapshot.get("user_request") and snapshot["user_request"] != requests[-1]:
            requests.append(snapshot["user_request"])
        for request in requests:
            if re.search(r"(?:仅|只|纯).{0,6}(?:设计|模板|布局|样式)|(?:无需|不要|不需要).{0,8}(?:真实|业务)?数据", request):
                intent = True
            elif re.search(r"查询|统计|汇总|应收|付款|金额|库存|上月|本月|上个月|真实|实际|业务数据|数据源|读取", request):
                intent = False
            elif re.search(r"(?:大屏|看板).{0,5}(?:模板|设计稿|布局|样式)|(?:模板|设计稿|布局|样式).{0,5}(?:大屏|看板)", request):
                intent = True
    return intent


def _validate(db, report, run, source_runs):
    """核对真实查询、原样快照和可展示组件；空结果与截断保留实际口径。"""
    if report.status not in {"draft", "published"}:
        return "大屏已归档或不可预览", []
    content = report.content if isinstance(report.content, dict) else {}
    widgets = content.get("widgets")
    if not isinstance(widgets, list) or not widgets or report.width <= 0 or report.height <= 0:
        return "大屏缺少可展示的画布或组件", []
    design_only = _design_only(source_runs)
    selected = set((run.context_snapshot or {}).get("datasource_ids") or [])
    sources = set(report.datasource_ids or [])
    lineage = report.lineage or {}
    declared = set(lineage.get("queryIds") or []) | set(lineage.get("sourceQueryIds") or [])
    verified, queries, displayed = {}, {}, {}
    visible = 0
    for widget in widgets:
        if not isinstance(widget, dict):
            return "大屏组件格式无效", []
        config = widget.get("config") if isinstance(widget.get("config"), dict) else {}
        x, y, width, height = (widget.get(key, 0) for key in ("x", "y", "w", "h"))
        on_canvas = (widget.get("visible") is not False
                     and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y, width, height))
                     and width > 0 and height > 0 and x < report.width and y < report.height and x + width > 0 and y + height > 0)
        if on_canvas and isinstance(widget.get("component"), str) and widget["component"]:
            visible += 1
        query_id = config.get("queryId") or config.get("httpQueryId")
        rows = config.get("chartDataParsed", widget.get("data"))
        # 标题、装饰及未绑定模板槽位不需要查询；业务数值不得用模板样例冒充。
        if not query_id:
            if not design_only and (widget.get("dataStatus") == "verified" or config.get("dataStatus") == "verified"
                                    or isinstance(rows, list) and rows):
                return "组件数据缺少成功查询来源", []
            continue
        if not on_canvas or not widget.get("component"):
            return "业务指标组件位于画布外或不可展示", []
        if query_id not in queries:
            queries[query_id] = db.get(QueryHistory, query_id)
        query = queries[query_id]
        if (query is None or query.workspace_id != run.workspace_id or query.owner_user_id != run.started_by_user_id
                or query.status != "success" or query.datasource_id not in sources
                or selected and query.datasource_id not in selected or declared and query.id not in declared):
            return "组件查询不存在、未成功或来源不匹配", []
        result = query.result_json if isinstance(query.result_json, dict) else {}
        if (query.query_mode == "profile" or result.get("queryType") == "profile"
                or result.get("evidenceKind") == "coverage_only"):
            return "数据覆盖诊断不能作为业务指标交付", []
        data = result.get("data")
        if (not isinstance(data, list) or any(not isinstance(row, dict) for row in data)
                or not isinstance(result.get("truncated"), bool)
                or result.get("queryId", query.id) != query.id
                # 新传输把旧大整数编码为精确文本；不能接受舍入值、补零文本或安全整数的类型变化。
                or preserve_json_integers(rows) != preserve_json_integers(data)):
            return "大屏快照与已成功查询不一致或缺少范围信息", []
        columns = result.get("columns") or []
        if not data and not columns:
            return "空结果缺少指标字段，无法确认展示口径", []
        if config.get("truncated") is not None and config["truncated"] != result["truncated"]:
            return "大屏截断口径与查询不一致", []
        binding = config.get("snapshotBinding")
        fields = {column.get("columnName") for column in columns if isinstance(column, dict)}
        fields.update(key for row in data for key in row)
        if binding is not None:
            if not isinstance(binding, dict):
                return "大屏字段映射格式无效", []
            if widget.get("component") not in _SNAPSHOT_NUMERIC | _SNAPSHOT_TABLES:
                return "当前组件不支持查询快照展示，不能计入业务指标", []
            selected_fields = [binding[key] for key in ("valueField", "labelField") if binding.get(key)]
            projection = binding.get("columns", [])
            if not isinstance(projection, list):
                return "大屏表格展示字段格式无效", []
            selected_fields.extend(projection)
            if any(not isinstance(field, str) or field not in fields for field in selected_fields):
                return "大屏展示字段不在成功查询结果中", []
            value_field = binding.get("valueField")
            if widget.get("component") in _SNAPSHOT_NUMERIC and not value_field:
                return "大屏数值组件缺少明确展示字段", []
            if value_field:
                try:
                    # 原始数据正确仍可能绑定错列；交付前逐行核对实际展示值，空值保持缺失。
                    values = [row.get(value_field) for row in data if row.get(value_field) is not None]
                    if any(isinstance(value, bool) or isinstance(value, str) and not _NUMBER_TEXT.fullmatch(value.strip())
                           or not Decimal(str(value)).is_finite()
                           or not math.isfinite(float(value)) for value in values):
                        return "大屏数值字段含有不可展示数值", []
                except (InvalidOperation, ValueError, TypeError, OverflowError):
                    return "大屏数值字段含有不可展示数值", []
            # 图表和指标不会使用表格投影；只统计渲染器实际支持的字段映射。
            actual_fields = {binding[key] for key in ("valueField", "labelField") if binding.get(key)}
            if widget.get("component") in _SNAPSHOT_TABLES:
                actual_fields = set(projection) if projection else fields
            displayed.setdefault(query.id, set()).update(actual_fields)
        elif widget.get("component") in _SNAPSHOT_TABLES:
            headers = (config.get("option") or {}).get("header") or []
            header_fields = {item.get("key") for item in headers if isinstance(item, dict) and item.get("key")}
            displayed.setdefault(query.id, set()).update(header_fields or fields)
        verified[query.id] = {"queryId": query.id, "datasourceId": query.datasource_id,
                              "returnedRows": len(data), "truncated": result["truncated"],
                              "empty": not data, "queriedAt": query.created_at.isoformat()}
    if not verified and not design_only:
        return "大屏尚无经过成功查询验证的业务指标", []
    if not visible:
        return "大屏尚无可见组件", []
    # 新生成报表保存必需字段清单；后续编辑或组件隐藏不能让旧的完成回执掩盖指标遗漏。
    for scope in lineage.get("queryScopes") or []:
        required = scope.get("requiredFields") if isinstance(scope, dict) else None
        if required is None:
            continue
        if not isinstance(required, list) or any(not isinstance(field, str) for field in required):
            return "大屏必需字段清单无效", []
        missing = set(required) - displayed.get(scope.get("queryId"), set())
        if missing:
            return "大屏尚未展示必需指标：" + "、".join(sorted(missing)), []
    return "", list(verified.values())


def dashboard_deliveries(db, run, source_runs) -> list[dict]:
    """交付身份来自同任务的创建回执和保存对象，不能仅凭模型声称已生成。"""
    run_ids = [row.id for row in source_runs]
    candidates = {}
    for source in source_runs:
        for delivery in (source.context_snapshot or {}).get("deliveries") or []:
            if delivery.get("report_id") and delivery.get("source_run_id") in run_ids:
                candidates[delivery["report_id"]] = delivery["source_run_id"]
        for receipt in ((source.context_snapshot or {}).get("query_dashboard_receipts") or {}).values():
            if isinstance(receipt, dict) and receipt.get("report_id"):
                candidates[receipt["report_id"]] = source.id
    receipts = db.scalars(select(BusinessActionReceipt).where(
        BusinessActionReceipt.workspace_id == run.workspace_id, BusinessActionReceipt.owner_user_id == run.started_by_user_id,
        BusinessActionReceipt.invocation_run_id.in_(run_ids), BusinessActionReceipt.status == "completed",
        BusinessActionReceipt.resource_type == "dashboards")).all()
    for receipt in receipts:
        if receipt.resource_id:
            candidates[receipt.resource_id] = receipt.invocation_run_id
    calls = db.scalars(select(ToolCall).where(ToolCall.workspace_id == run.workspace_id, ToolCall.run_id.in_(run_ids),
        ToolCall.status.in_(["success", "completed"]), ToolCall.tool_name.in_(["create_business_dashboard", "create_dashboard_from_queries"]))).all()
    revisions = {}
    for call in calls:
        data = (call.result_json or {}).get("data") or {}
        if isinstance(data, dict) and (data.get("reportId") or data.get("dashboard_id")):
            identity = data.get("reportId") or data["dashboard_id"]
            candidates[identity] = call.run_id
            if call.run_id == run.id and not data.get("reused") and type(data.get("version")) is int:
                revisions[identity] = data["version"]
    # 只按轻量标识过滤，不对包含完整 JSON 快照的报表做排序。
    rows = db.scalars(select(Report).where(Report.workspace_id == run.workspace_id,
        Report.owner_user_id == run.started_by_user_id, Report.lineage["invocationRunId"].as_string().in_(run_ids))).all()
    for report in rows:
        candidates[report.id] = report.lineage["invocationRunId"]
    output = []
    for identity, origin in candidates.items():
        report = db.get(Report, identity)
        if report is None or report.workspace_id != run.workspace_id or report.owner_user_id != run.started_by_user_id:
            output.append({"id": "dashboard:" + identity, "report_id": identity,
                "task_id": (run.context_snapshot or {}).get("task_id"), "source_run_id": origin,
                "status": "missing", "type": "dashboard", "name": "数字大屏", "preview_url": None,
                "validation_error": "已交付的大屏已删除或不可访问", "scopes": [], "business_result": None})
            continue
        # 重用旧对象不会冒充本轮“修改后重新交付”；真实修改需由保存动作更新归属回执。
        source_run = (report.lineage or {}).get("invocationRunId") or origin
        if source_run not in run_ids:
            continue
        earlier_versions = [item.get("report_version", 0) for source in source_runs if source.id != run.id
                            for item in (source.context_snapshot or {}).get("deliveries") or []
                            if item.get("report_id") == identity]
        if (earlier_versions and revisions.get(identity) == report.version
                and report.version > max(earlier_versions)):
            source_run = run.id
        problem, scopes = _validate(db, report, run, source_runs)
        preview = f"/report/view/{report.id}?workspace_id={run.workspace_id}"
        data = {"resultType": "dashboard", "reportId": report.id, "title": report.name, "version": report.version,
                "widgetCount": len((report.content or {}).get("widgets") or []), "queryIds": [row["queryId"] for row in scopes],
                "datasourceIds": report.datasource_ids,
                "datasourceNames": list(db.scalars(select(DataSource.name).where(DataSource.workspace_id == run.workspace_id,
                    DataSource.id.in_(report.datasource_ids or []))).all()), "previewPath": preview, "scopes": scopes,
                "actions": [{"label": "交互预览", "href": preview},
                            {"label": "打开完整设计器", "href": f"/report/designer/{report.id}?workspace_id={run.workspace_id}"}],
                "description": "该范围无记录；未补填金额。" if scopes and all(row["empty"] for row in scopes)
                               else "已核验保存的大屏与成功查询快照；部分查询已截断，仅代表返回范围。" if any(row["truncated"] for row in scopes)
                               else "已核验保存的大屏与成功查询快照；不自动刷新业务数据。" if scopes else "纯设计大屏，不包含已核实业务数据。"}
        output.append({"id": "dashboard:" + report.id, "report_id": report.id, "report_version": report.version,
                       "task_id": (run.context_snapshot or {}).get("task_id"), "source_run_id": source_run,
                       "status": "invalid" if problem else "ready", "type": "dashboard", "name": report.name,
                       "preview_url": preview if not problem else None, "validation_error": problem,
                       "scopes": scopes, "business_result": data if not problem else None})
    return output
