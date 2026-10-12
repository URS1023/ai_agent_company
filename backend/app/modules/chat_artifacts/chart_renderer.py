"""用本轮已成功查询的保存结果生成宋体图表，不接受模型自编的数据数组。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
import math
import re
from threading import RLock

from ...errors import PermissionErrorEx, ValidationError


_RENDER_LOCK = RLock()
_SUPPORTED = {"pie", "bar", "line"}
_QUERY_TOOLS = {"query_business_data", "query_inventory_data"}


def _receipt_contains(call, query_id: str) -> bool:
    """只认查询工具的成功持久回执，名称相似或失败回执不能授权数据复用。"""
    result = call.result_json or {}
    if (call.tool_name not in _QUERY_TOOLS or call.status not in {"completed", "success"}
            or not isinstance(result, dict) or result.get("status") != "success"):
        return False
    data = result.get("data")
    if not isinstance(data, dict) or data.get("success") is False:
        return False
    records = data.get("results") if isinstance(data.get("results"), list) else [data]
    return any(isinstance(item, dict) and item.get("queryId") == query_id and item.get("success") is not False
               for item in records)


def load_authorized_query(ctx, context: dict, query_id: str, *, permission: str = "analytics.export"):
    """重新验证当前身份、导出权限、数据源权限和本轮/明确续作回执，不连接业务库。"""
    from sqlalchemy import select
    from ...common.owned_records import owned_record
    from ...models import Run, ToolCall
    from ..analytics.connections import source_version
    from ..analytics.models import QueryHistory
    from ..analytics.sources import get_source
    from ..business_ai.context import auth_context_from_tool
    from .resume import validated_resume_calls

    live_ctx = auth_context_from_tool(context)
    if (ctx.workspace_id, ctx.user_id) != (live_ctx.workspace_id, live_ctx.user_id):
        raise PermissionErrorEx("查询回执身份与当前工具身份不一致")
    ctx = live_ctx
    run = ctx.db.get(Run, context.get("run_id"))
    if run is None or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
        raise PermissionErrorEx("图表工具缺少属于当前用户的运行记录")
    calls = ctx.db.scalars(select(ToolCall).where(
        ToolCall.workspace_id == ctx.workspace_id, ToolCall.run_id == run.id,
        ToolCall.tool_name.in_(_QUERY_TOOLS), ToolCall.status.in_(["completed", "success"]),
    )).all()
    if not any(_receipt_contains(call, query_id) for call in [*calls, *validated_resume_calls(ctx.db, run)]):
        raise ValidationError("该查询不是本轮或明确续作任务中已成功的查询，不能据此生成图表")
    row = owned_record(ctx, QueryHistory, query_id, permission)
    if row.workspace_id != ctx.workspace_id or row.owner_user_id != ctx.user_id or row.status != "success":
        raise PermissionErrorEx("图表只能使用当前用户在当前工作区的成功查询")
    selected = set((run.context_snapshot or {}).get("datasource_ids") or [])
    if selected and row.datasource_id not in selected:
        raise PermissionErrorEx("图表数据来源不在本次选择范围内")
    source = get_source(ctx, row.datasource_id, active=True, provider="any")
    if source_version(source) != row.datasource_version:
        raise ValidationError("数据来源配置已变化，原查询结果不能直接用于新图表")
    result = row.result_json
    if not isinstance(result, dict) or result.get("success") is False or result.get("queryId") != query_id:
        raise ValidationError("保存的查询结果不完整或与查询标识不一致")
    return row


def _number(value) -> float:
    """仅将有限数值或十进制数值字符串用于坐标，拒绝缺失值和布尔值。"""
    if isinstance(value, bool) or value is None or not isinstance(value, (int, float, Decimal, str)):
        raise ValidationError("图表数值字段包含空值或非数值，请使用已经核对的数值列")
    try:
        number = Decimal(str(value).strip())
        result = float(number)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValidationError("图表数值字段包含无法解析的数字") from None
    if not number.is_finite() or not math.isfinite(result):
        raise ValidationError("图表数值不能包含 NaN 或无穷大")
    return result


def chart_unit(result: dict, value_columns: list[str]) -> str | None:
    """识别回执中的真实单位，禁止把不同单位或缺失单位的数值放到同一坐标。"""
    rows = result.get("data") or []
    aliases = {"unit", "unitname", "measurementunit", "measureunit", "qtyunit", "单位", "计量单位", "单位名称",
               "库存计量单位", "物料计量单位"}
    unit_keys = {key for row in rows for key in row
                 if str(key).lower().replace("_", "").replace(" ", "") in aliases}
    units = set()
    for key in unit_keys:
        values = [row.get(key) for row in rows]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValidationError("图表数据包含未提供的计量单位，不能假定数量可比较；请保留表格或先补齐真实单位")
        key_units = {value.strip() for value in values}
        if len(key_units) > 1:
            raise ValidationError("图表数据包含不同计量单位，不能计算同一占比或使用同一数值轴；请按单位分别查询")
        units.update(key_units)
    columns = {column.get("columnName"): column for column in result.get("columns") or [] if isinstance(column, dict)}
    declared = []
    for name in value_columns:
        column = columns.get(name) or {}
        unit = column.get("unit")
        if not unit:
            label = str(column.get("comment") or name)
            match = re.search(r"[（(]([^（）()]{1,16})[）)]\s*$", label)
            unit = match.group(1).strip() if match else None
        declared.append(str(unit).strip() if unit else None)
    known = {unit for unit in declared if unit}
    if len(known) > 1 or (known and len(value_columns) > 1 and any(unit is None for unit in declared)):
        raise ValidationError("所选指标的计量单位不同或不完整，不能合并为同一图表，请分别展示")
    units.update(known)
    if len(units) > 1:
        raise ValidationError("字段声明与记录单位不一致，请核对查询结果后再绘图")
    return next(iter(units), None)


def chart_data(result: dict, args: dict) -> dict:
    """选择真实列并保留原始数值，不静默聚合、截断、补零或推断分类。"""
    chart_type = str(args.get("chart_type") or "").strip().lower()
    if chart_type not in _SUPPORTED:
        raise ValidationError("chart_type 仅支持 pie、bar、line")
    if result.get("truncated") is not False:
        raise ValidationError("查询结果已截断或缺少完整性标记，不能把返回样本绘制成完整统计图")
    rows = result.get("data")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("查询没有可绘制的数据，请保留无数据事实，不生成虚构图表")
    if result.get("totalCount") != len(rows):
        raise ValidationError("保存的查询行数与结果不一致，不能据此绘图")
    value_columns = args.get("value_columns")
    label_column = str(args.get("label_column") or "").strip()
    value_column = str(args.get("value_column") or "").strip()
    if value_columns is not None:
        if (not isinstance(value_columns, list) or not value_columns
                or any(not isinstance(item, str) or not item.strip() for item in value_columns)
                or len(set(value_columns)) != len(value_columns)):
            raise ValidationError("value_columns 必须是非空、不重复的真实数值字段列表")
        if len(rows) != 1 or label_column or value_column:
            raise ValidationError("多指标模式只支持单行聚合结果，且不能同时填写分类列或单值列")
        if any(column not in rows[0] for column in value_columns):
            raise ValidationError("value_columns 包含查询结果中不存在的字段")
        labels = list(value_columns)
        values = [_number(rows[0][column]) for column in value_columns]
        source_columns = value_columns
        mode = "single_row_metrics"
    else:
        if not label_column or not value_column or label_column == value_column:
            raise ValidationError("请填写不同的 label_column 与 value_column，必须来自实际查询结果")
        if any(label_column not in row or value_column not in row for row in rows):
            raise ValidationError("分类列或数值列在查询结果中不存在")
        labels = []
        for row in rows:
            value = row[label_column]
            if value is None or isinstance(value, (dict, list, bool)) or not str(value).strip():
                raise ValidationError("图表分类包含空值或复杂对象，请先明确分类口径")
            labels.append(str(value))
        values = [_number(row[value_column]) for row in rows]
        source_columns = [label_column, value_column]
        mode = "categorized_rows"
    if len(set(labels)) != len(labels):
        raise ValidationError("分类标签重复，请使用已经按明确口径聚合的查询结果")
    if len(labels) > (16 if chart_type == "pie" else 100):
        raise ValidationError("当前分类太多，无法清晰绘图；请使用明确汇总后的查询，不会静默省略数据")
    if any(len(label) > 80 for label in labels):
        raise ValidationError("分类标签过长，无法清晰绘图，请选择简短且唯一的真实分类字段")
    if chart_type == "pie" and (any(value < 0 for value in values) or sum(values) <= 0 or not math.isfinite(sum(values))):
        raise ValidationError("饼图要求非负数值且合计是大于零的有限值，零值或负值数据请使用柱状图")
    title = str(args.get("title") or {"pie": "查询结果占比分布", "bar": "查询结果对比", "line": "查询结果趋势"}[chart_type]).strip()
    if not title or len(title) > 120:
        raise ValidationError("图表标题长度应为 1 至 120 个字符")
    unit = chart_unit(result, value_columns if value_columns is not None else [value_column])
    return {"chart_type": chart_type, "title": title, "labels": labels, "values": values,
            "source_columns": source_columns, "data_mode": mode, "unit": unit}


def render_png(spec: dict) -> bytes:
    """使用固定图表类型和本机宋体绘图，显式验证字形并隔离 matplotlib 线程。"""
    from ...common.artifact_fonts import song_font_path
    from matplotlib import ft2font
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.text import Text
    from matplotlib.ticker import FuncFormatter

    path = str(song_font_path())
    font = FontProperties(fname=path, size=12)
    coverage = ft2font.FT2Font(path).get_charmap()
    text = spec["title"] + "".join(spec["labels"])
    if any(ord(character) not in coverage for character in text if not character.isspace()):
        raise ValidationError("宋体缺少图表中的部分字形，请使用可显示的标题和分类字段")
    with _RENDER_LOCK:
        figure = Figure(figsize=(11, 7), dpi=160, layout="constrained")
        FigureCanvasAgg(figure)
        axes = figure.add_subplot(111)
        labels, values = spec["labels"], spec["values"]
        if spec["chart_type"] == "pie":
            wedges, _, _ = axes.pie(values, autopct=lambda value: f"{value:.1f}%", startangle=90,
                                   pctdistance=0.72, textprops={"fontproperties": font})
            axes.legend(wedges, [f"{label}: {value:g}" for label, value in zip(labels, values)],
                        loc="center left", bbox_to_anchor=(1, 0.5), prop=font, frameon=False)
        elif spec["chart_type"] == "bar":
            axes.bar(range(len(labels)), values, color="#3268B2")
            axes.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
        else:
            axes.plot(range(len(labels)), values, color="#3268B2", marker="o", linewidth=2)
            axes.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
        axes.set_title(spec["title"], fontproperties=font, fontsize=18, pad=20)
        axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        unit_note = f"单位：{spec['unit']}；" if spec.get("unit") else "单位：查询未提供；"
        ratio_note = "占比按所选值计算；" if spec["chart_type"] == "pie" else ""
        figure.text(0.5, 0.01, unit_note + ratio_note + "口径：已保存查询返回的全部行，不代表整库总量。",
                    ha="center", fontproperties=font, fontsize=9)
        for item in figure.findobj(Text):
            item.set_fontproperties(font)
            item.set_usetex(False)
            item.set_parse_math(False)
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=160, bbox_inches="tight", facecolor="white")
        figure.clear()
        return buffer.getvalue()


def render_chart_image(ctx, context: dict, args: dict):
    """向本轮保存可下载图表与服务器生成的来源证明，返回统一工具结果。"""
    from ...core.observability import log_event
    from ...models import Run
    from ...providers.tool.base import ToolResult
    from ..artifact.service import artifact_reference, create_artifact

    query_id = str(args.get("query_id") or "").strip()
    if not query_id:
        raise ValidationError("请提供 query_business_data 成功返回的 query_id")
    query = load_authorized_query(ctx, context, query_id)
    run = ctx.db.get(Run, context["run_id"])
    result = query.result_json
    spec = chart_data(result, args)
    data = render_png(spec)
    evidence = {"kind": "chart", "chart_types": [spec["chart_type"]], "source_query_id": query.id,
                "producer": "server_renderer.v1", "source_datasource_id": query.datasource_id,
                "source_columns": spec["source_columns"], "data_mode": spec["data_mode"],
                "unit": spec["unit"],
                "source_rows": len(result["data"]), "scope": "saved_query_result", "truncated": False,
                "rendered_values_sha256": sha256(json.dumps(
                    {"labels": spec["labels"], "values": spec["values"]}, ensure_ascii=False,
                    sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()}
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", spec["title"]).strip(". ")[:100] or "查询结果图表"
    artifact = create_artifact(ctx.db, workspace_id=ctx.workspace_id, run_id=run.id,
        owner_user_id=ctx.user_id, type="chart", name=name + ".png", data=data, mime_type="image/png",
        metadata={"tool": "render_chart_image", "delivery_evidence": evidence, "font": "宋体"})
    log_event("chart.rendered", "已从保存的查询结果生成宋体图表", run_id=run.id,
              artifact_id=artifact.id, query_id=query.id, chart_type=spec["chart_type"])
    return ToolResult(status="success", data={"artifact_id": artifact.id, "query_id": query.id,
        "chart_type": spec["chart_type"], "title": spec["title"], "scope": "saved_query_result"},
        artifacts=[artifact_reference(artifact)], metadata={"delivery_evidence": evidence})
