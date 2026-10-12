"""将本次已授权预警结果直接转为 Excel，不让模型抄写报告与问题明细。"""
from __future__ import annotations

from .query_spreadsheet import spreadsheet_filename


def alert_workbook_spec(view: dict) -> dict:
    """仅导出当前页的事实与分页边界，不补查设备、工作流或其他页面。"""
    report_fields = [
        ("id", "报告标识"), ("title", "报告"), ("deviceName", "设备"),
        ("outcomeLabel", "报告判定"), ("executionStatusLabel", "流程执行状态"),
        ("rows", "检查记录数"), ("issues", "原始问题数"), ("createTime", "报告时间"),
        ("href", "查看报告"),
    ]
    problem_fields = [
        ("id", "问题标识"), ("reportId", "报告标识"), ("recordKey", "记录标识"),
        ("ruleName", "检查项"), ("message", "问题说明"), ("severity", "原始等级"),
        ("statusLabel", "人工处置状态"), ("note", "最新处置说明"),
        ("version", "记录版本"), ("createTime", "记录时间"), ("href", "查看报告"),
    ]

    def sheet(name, fields, rows):
        """固定列映射保留服务端返回值，缺失字段保持空白。"""
        return {"name": name, "columns": [label for _, label in fields],
                "rows": [[row.get(key) for key, _ in fields] for row in rows]}

    summary, origin = view.get("summary") or {}, view.get("provenance") or {}
    sheets = [sheet("预警报告", report_fields, view.get("reports") or []),
              sheet("问题记录", problem_fields, view.get("problems") or [])]
    history = view.get("processingHistory")
    if history is not None:
        sheets.append(sheet("处置日志", [
            ("id", "事件标识"), ("actorId", "处置人标识"),
            ("createTime", "时间"), ("previousStatus", "原状态"), ("status", "新状态"),
            ("note", "处置理由"),
        ], history.get("events") or []))
    source_rows = [
        ["读取时间（UTC）", origin.get("retrievedAt")], ["数据性质", origin.get("source")],
        ["工作区标识", origin.get("workspaceId")], ["报告返回数", len(view.get("reports") or [])],
        ["问题返回数", len(view.get("problems") or [])], ["分页起点", summary.get("offset", 0)],
        ["报告还有后续页", bool(summary.get("reportsMayHaveMore"))],
        ["问题还有后续页", bool(summary.get("problemsMayHaveMore"))],
        ["报告分页起点", (summary.get("reportsPage") or {}).get("offset", summary.get("offset", 0))],
        ["问题分页起点", (summary.get("problemsPage") or {}).get("offset", summary.get("offset", 0))],
        ["报告下页起点", (summary.get("reportsPage") or {}).get("nextOffset")],
        ["问题下页起点", (summary.get("problemsPage") or {}).get("nextOffset")],
        ["本问题页归属本报告页的问题数", summary.get("returnedProblemsInReportPage")],
        ["本问题页归属其他报告的问题数", summary.get("returnedProblemsOutsideReportPage")],
        ["统计范围", summary.get("scopeNote")],
        ["口径说明", "仅本次当前账号已授权的返回页；报告与问题分别分页，不代表全量。流程执行完成不表示问题已解决；保存记录不代表设备实时状态。"],
    ]
    if history is not None:
        source_rows.extend([["处置问题标识", history.get("problemId")],
                            ["处置日志分页起点", history.get("offset", 0)],
                            ["处置日志还有后续页", bool(history.get("hasMore"))]])
    sheets.append({"name": "数据来源", "columns": ["项目", "说明"], "rows": source_rows})
    return {"sheets": sheets}


def export_alert_spreadsheet(ctx, tool_context: dict, view: dict, name: str):
    """复用已过滤的服务回执与现有渲染器，一次调用交付卡片和真实工件。"""
    from ...errors import PermissionErrorEx
    from ...models import Run
    from ...providers.tool.base import ToolResult
    from ..artifact.renderers import render_spreadsheet
    from ..artifact.service import artifact_reference, create_artifact

    run = ctx.db.get(Run, tool_context.get("run_id"))
    if run is None or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
        raise PermissionErrorEx("预警 Excel 导出缺少属于当前用户的运行记录")
    filename = spreadsheet_filename(name, "设备预警信息表.xlsx")
    summary = view.get("summary") or {}
    scope = {key: summary.get(key) for key in (
        "returnedReports", "returnedProblems", "reportsMayHaveMore", "problemsMayHaveMore", "offset", "scopeNote",
        "reportsPage", "problemsPage", "relationship", "returnedProblemsInReportPage", "returnedProblemsOutsideReportPage")}
    row = create_artifact(ctx.db, workspace_id=ctx.workspace_id, run_id=run.id,
        owner_user_id=ctx.user_id, type="spreadsheet", name=filename,
        data=render_spreadsheet(alert_workbook_spec(view)),
        metadata={"tool": "view_device_alerts", "delivery_evidence": {
            "kind": "xlsx", "producer": "server_renderer.v1", "source": "authorized_alert_overview",
            "scope": scope, "retrieved_at": (view.get("provenance") or {}).get("retrievedAt")}})
    return ToolResult(status="success", data={**view, "artifact_id": row.id,
        "export": {"name": filename, "scope": scope,
                   "description": "已直接导出本次已授权的报告、问题及来源，不重新取数。"}},
        artifacts=[artifact_reference(row)])
