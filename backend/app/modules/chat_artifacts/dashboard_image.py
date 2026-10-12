"""把已保存大屏的真实查询快照导出为适合嵌入文档的宋体图片。"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import math

from ...errors import PermissionErrorEx, ValidationError


def _inspection_panels(ctx, report, selected_datasource_ids: set[str]) -> list[dict]:
    """重读已保存巡检执行并重新授权，固定统计与大屏保存数据完全相符时才认可来源。"""
    from ...common.authorization import require_permission
    from ...common.owned_records import owned_record
    from ..alerts.inspection_export import inspection_facts
    from ..workflows.business_results import projected_report
    from ..workflows.graph import authorize_graph, graph_sources
    from ..workflows.models import WorkflowExecution
    from ..workflows.schemas import FlowGraph

    require_permission(ctx, "workflows.view")
    require_permission(ctx, "alerts.view")
    lineage = report.lineage or {}
    execution_ids = lineage.get("sourceExecutionIds")
    if (lineage.get("source") != "saved_workflow_reports" or lineage.get("dataStatus") != "saved_evidence"
            or not isinstance(execution_ids, list) or not 1 <= len(execution_ids) <= 20
            or any(not isinstance(value, str) or not value.strip() for value in execution_ids)
            or len(set(execution_ids)) != len(execution_ids)):
        raise ValidationError("巡检大屏缺少完整的已保存执行来源，不能导出可信图片")
    saved_reports = []
    datasource_ids = set()
    for execution_id in execution_ids:
        # 精确按执行主键读取，避免详情接口中“执行/运行/流程三选一”的宽松引用语义。
        execution = owned_record(ctx, WorkflowExecution, execution_id, "workflows.view")
        if execution.status != "completed":
            raise ValidationError("巡检大屏引用的执行尚未完成，不能用其结果生成快照")
        graph_data = (execution.snapshot or {}).get("graph")
        if not isinstance(graph_data, dict) or not graph_data.get("nodes"):
            raise ValidationError("巡检执行缺少资源图快照，无法重新核对来源权限")
        graph = FlowGraph.model_validate(graph_data)
        authorize_graph(ctx, graph)
        sources = graph_sources(graph)
        # 选择集为空表示“本次未限定数据源范围”：此时仍由上方 authorize_graph 对每个
        # 来源逐项做当前用户/工作区授权；非空选择即使引用的是留存证据，也必须完全
        # 包含于本次选择，防止把上一轮数据带入当前明确的数据范围。
        if selected_datasource_ids and not sources.issubset(selected_datasource_ids):
            raise ValidationError("巡检大屏执行结果的数据源超出本次选择范围")
        datasource_ids.update(sources)
        saved = projected_report(execution)
        if not saved or saved.get("kind") != "alert" or saved.get("executionId") != execution_id:
            raise ValidationError("巡检大屏引用的执行不包含对应设备检查报告")
        saved_reports.append(saved)
    facts = inspection_facts(saved_reports)
    if not facts["checkCount"]:
        raise ValidationError("巡检大屏来源没有逐项检查证据，不能生成空统计图")
    columns = ["设备", "检查项", "异常项", "待确认项"]
    expected_rows = [[row[column] for column in columns] for row in facts["devices"]]
    data_widgets = []
    for widget in (report.content or {}).get("widgets", []):
        if not isinstance(widget, dict):
            continue
        config = widget.get("config") or {}
        if any(key in widget for key in ("data", "queryId")) or any(
                key in config for key in ("chartDataParsed", "chartData", "queryId")):
            data_widgets.append(widget)
    if len(data_widgets) != 1:
        raise ValidationError("巡检快照的数据组件已变化，不能套用原检查项统计来源")
    widget = data_widgets[0]
    config = widget.get("config") or {}
    header = (config.get("option") or {}).get("header") or []
    labels = [item.get("label") if isinstance(item, dict) else item for item in header]
    if (widget.get("component") != "JScrollBoard" or config.get("dataType") != 1
            or config.get("chartDataParsed") != expected_rows or labels != columns
            or widget.get("data", expected_rows) != expected_rows or config.get("queryId") or widget.get("queryId")):
        raise ValidationError("巡检大屏快照与重新核对的逐项证据不一致，请核对后再导出")
    if "chartData" in config:
        try:
            encoded_rows = json.loads(config["chartData"]) if isinstance(config["chartData"], str) else config["chartData"]
        except (TypeError, ValueError):
            raise ValidationError("巡检大屏保存的表格数据格式无效") from None
        if encoded_rows != expected_rows:
            raise ValidationError("巡检大屏两份保存数据不一致，不能生成可信快照")
    return [{"title": "逐设备检查项统计（不代表未巡检设备数）", "columns": columns,
             "rows": facts["devices"], "truncated": False, "source_kind": "saved_workflow_reports",
             "source_execution_ids": execution_ids, "source_datasource_ids": sorted(datasource_ids),
             "scope_note": facts["scopeNote"]}]


def dashboard_panels(ctx, report, *, selected_datasource_ids=None) -> list[dict]:
    """逐组件核对来源；只读平台留存结果，绝不刷新大屏或重新执行业务 SQL。"""
    from ..analytics.queries import historical_result

    if (getattr(report, "lineage", None) or {}).get("entry") == "inspection_evidence_export":
        return _inspection_panels(ctx, report, set(selected_datasource_ids or []))
    panels = []
    results = {}
    for widget in (report.content or {}).get("widgets", []):
        if not isinstance(widget, dict):
            continue
        config = widget.get("config") or {}
        query_id = config.get("queryId") or widget.get("queryId")
        has_data = bool(widget.get("data") or config.get("chartDataParsed"))
        if not query_id:
            if has_data:
                raise ValidationError("大屏中存在没有查询来源的数据组件，请先补齐来源再导出图片")
            continue
        if query_id not in results:
            results[query_id] = historical_result(ctx, query_id, permission="analytics.export")
        result = results[query_id]
        if result.datasourceId not in (report.datasource_ids or []):
            raise ValidationError("组件查询的数据源与大屏绑定不一致")
        saved_rows = widget.get("data")
        if saved_rows is None:
            saved_rows = config.get("chartDataParsed")
        if saved_rows is not None and saved_rows != result.data:
            raise ValidationError("大屏快照与保存的查询结果不一致，请核对组件后再导出")
        panels.append({"title": str(widget.get("metricName") or widget.get("componentName") or
                                     widget.get("title") or "查询结果"),
                       "query_id": query_id, "columns": [column.columnName for column in result.columns],
                       "rows": result.data, "truncated": result.truncated})
    if not panels:
        raise ValidationError("该大屏没有带来源的查询快照，无法生成真实数据图片")
    if len(panels) > 16:
        raise ValidationError("大屏超过 16 个数据组件，请按业务分屏后导出，避免图片无法阅读")
    return panels


def render_snapshot(title: str, version: int, panels: list[dict]) -> bytes:
    """导出明确标记的数据快照；交互页面布局不会被冒称为网页截图。"""
    from PIL import Image, ImageDraw, ImageFont
    from ...common.artifact_fonts import song_font_path

    width = 1600
    columns = 2 if len(panels) > 1 else 1
    panel_width = (width - 72 - 24 * (columns - 1)) // columns
    panel_height = 390
    height = 150 + math.ceil(len(panels) / columns) * (panel_height + 24) + 60
    picture = Image.new("RGB", (width, height), "#08192D")
    draw = ImageDraw.Draw(picture)
    font_path = str(song_font_path())
    heading = ImageFont.truetype(font_path, 36)
    subtitle = ImageFont.truetype(font_path, 24)
    font = ImageFont.truetype(font_path, 21)
    small = ImageFont.truetype(font_path, 17)

    def fitted(value, available, selected_font=font):
        """按实际字宽截断，避免中文标签压到邻列。"""
        text = str(value).replace("\n", " ").replace("\r", " ")
        if draw.textlength(text, font=selected_font) <= available:
            return text
        while text and draw.textlength(text + "…", font=selected_font) > available:
            text = text[:-1]
        return text + "…"

    draw.text((36, 25), fitted(title, width - 72, heading), font=heading, fill="#EDF7FF")
    inspection = any(panel.get("source_kind") == "saved_workflow_reports" for panel in panels)
    scope = "按已保存巡检报告导出，非实时巡检" if inspection else "按留存查询结果导出，非实时刷新"
    draw.text((36, 82), f"大屏数据快照 · 保存版本 {version} · {scope}", font=subtitle, fill="#A7CBE8")
    for index, panel in enumerate(panels):
        x = 36 + (index % columns) * (panel_width + 24)
        y = 140 + (index // columns) * (panel_height + 24)
        draw.rounded_rectangle((x, y, x + panel_width, y + panel_height), radius=12, fill="#102B48", outline="#285780", width=2)
        draw.text((x + 18, y + 16), fitted(panel["title"], panel_width - 36, subtitle), font=subtitle, fill="#76DBFF")
        rows, names = panel["rows"], panel["columns"]
        shown_names = names[:5]
        if shown_names:
            cell_width = (panel_width - 36) / len(shown_names)
            for column, name in enumerate(shown_names):
                draw.text((x + 18 + column * cell_width, y + 62), fitted(name, cell_width - 10), font=font, fill="#E4F4FF")
            for row_index, row in enumerate(rows[:7]):
                for column, name in enumerate(shown_names):
                    value = row.get(name)
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value, ensure_ascii=False)
                    draw.text((x + 18 + column * cell_width, y + 103 + row_index * 30),
                              fitted("" if value is None else value, cell_width - 10), font=font, fill="#C6DEED")
        if not rows:
            draw.text((x + 18, y + 115), "查询成功，当前范围无匹配数据", font=font, fill="#C6DEED")
        note = f"显示 {min(7, len(rows))}/{len(rows)} 行，{min(5, len(names))}/{len(names)} 列"
        if panel["truncated"]:
            note += "；原查询已截断，不代表全量"
        draw.text((x + 18, y + 328), fitted(note, panel_width - 36, small), font=small, fill="#A7CBE8")
        execution_ids = panel.get("source_execution_ids") or []
        source_label = (f"执行：{execution_ids[0]} · 共 {len(execution_ids)} 份已保存报告" if execution_ids
                        else "查询：" + panel["query_id"])
        draw.text((x + 18, y + 357), fitted(source_label, panel_width - 36, small), font=small, fill="#A7CBE8")
    note = ("检查项、异常项和待确认项不等于未巡检设备数；本快照不推算巡检完成率。" if inspection
            else "数据来自大屏保存的查询快照；完整数据与交互布局请打开原大屏。")
    draw.text((36, height - 40), note, font=small, fill="#A7CBE8")
    output = BytesIO()
    picture.save(output, format="PNG")
    return output.getvalue()


def render_dashboard_image(ctx, tool_context: dict, args: dict):
    """校验大屏归属与当前数据范围后生成可追溯图片工件。"""
    from ..artifact.service import create_artifact, artifact_reference
    from ..business_ai.dashboard_actions import invocation
    from ..business_ai.context import auth_context_from_tool
    from ..reports.service import get_report
    from ...providers.tool.base import ToolResult

    live_ctx = auth_context_from_tool(tool_context)
    if (ctx.workspace_id, ctx.user_id) != (live_ctx.workspace_id, live_ctx.user_id):
        raise PermissionErrorEx("大屏图片工具身份与当前执行身份不一致")
    ctx = live_ctx
    run = invocation(ctx, tool_context)
    report_id = str(args.get("report_id") or "").strip()
    if not report_id:
        raise ValidationError("请提供已经创建成功的大屏 report_id")
    report = get_report(ctx, report_id)
    selected = set(tool_context.get("selected_datasource_ids") or [])
    if selected and not set(report.datasource_ids or []).issubset(selected):
        raise ValidationError("大屏的数据源超出本次选择范围")
    panels = dashboard_panels(ctx, report, selected_datasource_ids=selected)
    query_ids = list(dict.fromkeys(panel["query_id"] for panel in panels if panel.get("query_id")))
    execution_ids = list(dict.fromkeys(identity for panel in panels for identity in panel.get("source_execution_ids", [])))
    mode = "saved_inspection_snapshot" if execution_ids else "saved_data_snapshot"
    data = render_snapshot(report.name, report.version, panels)
    row = create_artifact(ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, owner_user_id=ctx.user_id,
        type="image", name=report.name[:100] + "-大屏数据快照.png", data=data,
        metadata={"tool": "render_dashboard_image", "delivery_evidence": {
            "kind": "dashboard_image", "producer": "server_renderer.v1", "dashboard_id": report.id,
            "dashboard_version": report.version, "source_query_ids": query_ids,
            "source_execution_ids": execution_ids,
            "source_datasource_ids": sorted({identity for panel in panels for identity in panel.get("source_datasource_ids", [])}),
            "render_mode": mode, "rendered_at": datetime.now(timezone.utc).isoformat()}})
    reference = artifact_reference(row)
    return ToolResult(status="success", data={"artifact_id": row.id, "dashboard_id": report.id,
        "source_query_ids": query_ids, "source_execution_ids": execution_ids, "render_mode": mode,
        "description": ("已保存巡检报告的数据快照，保留执行来源；检查项、异常项和待确认项不等于未巡检设备数，不推算巡检完成率。"
                        if execution_ids else "大屏真实数据快照，保留查询标识和截断范围；交互布局请打开原大屏。"),
        "next_action": "用 render_document 的 image 块 artifact_id 将此图片嵌入 Word。"}, artifacts=[reference])
