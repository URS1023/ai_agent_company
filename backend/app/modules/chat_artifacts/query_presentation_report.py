"""将已保存快照组织为汇报页；明细留在备注，避免把报表导出当作汇报。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re

from ...errors import ValidationError
from .chart_renderer import chart_unit


def _number(value) -> float | None:
    """只认有限数值，空值、布尔值和标识文本不会被补成零。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        converted = float(number)
        return converted if number.is_finite() and math.isfinite(converted) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _metric_names(source: dict) -> list[str]:
    """按真实类型或明确指标字段选数值；数字编码不能成为金额或数量。"""
    columns = {item.get("columnName"): item for item in source["query"].result_json["columns"]}
    names = []
    for name in source["names"]:
        column = columns.get(name, {})
        if column.get("primaryKey") or column.get("businessKey") or re.search(r"(?:^id$|_id$|编号|编码|序号)", name, re.I):
            continue
        declared = re.search(r"int|decimal|numeric|float|double|real", str(column.get("dataType") or ""), re.I)
        named = re.search(r"金额|余额|数量|条数|总数|amount|balance|count|quantity", name, re.I)
        if (declared or named) and all(row.get(name) is None or _number(row[name]) is not None for row in source["rows"]):
            names.append(name)
    return names


def _display_value(value, name: str) -> str:
    """移除数据库小数补位，金额至少留两位；原始精度完整保存在备注。"""
    if value is None:
        return "未提供（空值）"
    if _number(value) is None:
        return str(value)
    text = format(Decimal(str(value)), "f")
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    if re.search(r"金额|余额|amount|balance", name, re.I):
        fraction = fraction.ljust(2, "0")
    return whole + ("." + fraction if fraction else "")


def _slide_title(role: str, source: dict) -> str:
    """SQL 来源编号只留在备注，正文使用业务角色或已有的业务问题标题。"""
    label = source["label"]
    return role if re.fullmatch(r"查询结果\s*\d+", label) else f"{role} · {label}"


def report_spec(sources: list[dict], title: str, scope_notes: list[str]) -> tuple[dict, list[dict]]:
    """使用原始汇总值、单指标分组图和明细预览，不重新取数或推算汇总。"""
    from .query_presentation import _cell, _json

    slides, scopes = [], []
    overview = [
        {"title": "数据依据", "text": f"使用 {len(sources)} 份已保存查询；指标、图表和明细来自同一批查询结果。"},
        {"title": "阅读方式", "text": "先读单行指标与分组对比，再核对明细预览。未返回的数据和空值不作零值解释。"},
        {"title": "完整数据", "text": "页面为汇报摘要，完整返回行列保存在各来源页备注；原始明细可使用同源 Excel 核对。"},
    ]
    slides.append({"layout": "content", "title": "汇报范围与阅读说明", "items": overview})
    for offset in range(0, len(scope_notes), 5):
        slides.append({"layout": "content", "title": "本次范围与待补充事项", "items": scope_notes[offset:offset + 5]})

    # 汇总和分组排在明细前，保持每份来源独立，不累加重复查询的数据。
    def priority(source):
        grouped = bool(re.search(r"\bGROUP\s+BY\b", source["query"].generated_sql or "", re.I))
        return 0 if len(source["rows"]) == 1 else 1 if grouped else 2

    for source in sorted(sources, key=priority):
        rows, names, labels, scope = source["rows"], source["names"], source["labels"], dict(source["scope"])
        metrics = _metric_names(source)
        notes = "完整保存快照（页面不等于全部明细）：\n" + _json({
            "source": scope, "actual_sql": source["query"].generated_sql,
            "columns": source["query"].result_json["columns"], "data": rows})
        scope_text = "查询已截断，仅含部分返回数据" if scope["truncated"] else "范围限于本次保存查询，不代表全库"
        scope.update({"presentation_mode": "report", "full_data_location": "speaker_notes", "displayed_rows": 0,
                      "displayed_columns": 0, "displayed_fields": [], "table_pages": 0, "notes_cells": 0})
        start = len(slides)
        if not rows:
            slides.append({"layout": "content", "title": _slide_title("本次查询暂无记录", source), "subtitle": scope_text,
                "items": [{"title": "本次未返回记录", "text": "不能由零行推断业务金额为零。"}], "notes": notes})
        elif len(rows) == 1 and metrics:
            context_fields = [name for name in names if name not in metrics
                              and re.search(r"物料|设备|部门|客户|供应商|名称|name|单位", labels[name], re.I)
                              and not re.search(r"编码|编号|(?:^id$|_id$)", name, re.I)]
            context_text = "；".join(f"{labels[name]}：{str(rows[0].get(name))[:24] if rows[0].get(name) is not None else '未提供'}"
                                     for name in context_fields[:3])
            monetary = [name for name in metrics if re.search(r"金额|余额|amount|balance", labels[name], re.I)]
            counts = [name for name in metrics if name not in monetary and re.search(r"条数|总数|记录数|笔数|count", labels[name], re.I)]
            header_fields = counts if len(monetary) == 4 and len(counts) in {1, 2} and len(metrics) == len(monetary) + len(counts) else []
            card_fields = [name for name in metrics if name not in header_fields]
            count_note = "；".join(f"{labels[name]}：{_display_value(rows[0].get(name), name)}" for name in header_fields)
            headline = f"{count_note} · {scope_text}" if count_note else scope_text
            # 按页均衡分配，五个指标为三加二，避免末页仅剩一个指标。
            page_total = math.ceil(len(card_fields) / 4)
            base, extra = divmod(len(card_fields), page_total)
            offset = 0
            for page_index in range(page_total):
                size = base + (1 if page_index < extra else 0)
                fields = card_fields[offset:offset + size]
                offset += size
                role = "关键指标" if page_index == 0 else "补充指标"
                slides.append({"layout": "metrics", "title": _slide_title(role, source), "subtitle": scope_text, "key_message": headline,
                    "items": [{"title": labels[name], "value": _display_value(rows[0].get(name), name),
                               "description": context_text or "直接采用查询返回值；单位沿用字段说明"} for name in fields], "notes": notes})
            scope.update(displayed_rows=1, displayed_columns=len(metrics), displayed_fields=metrics)
        else:
            dimensions = [name for name in names if name not in metrics]
            grouped = bool(re.search(r"\bGROUP\s+BY\b", source["query"].generated_sql or "", re.I))
            # 只对已分组且完整可比较的单一指标绘图，混合单位或空值保留表格。
            if grouped and dimensions and metrics and 1 < len(rows) <= 12:
                chosen = next((name for name in metrics if re.search(r"未付|未收|余额|应收|应付", name)), metrics[0])
                values = [_number(row.get(chosen)) for row in rows]
                try:
                    unit = chart_unit(source["query"].result_json, [chosen])
                except ValidationError:
                    values, unit = [], None
                if values and all(value is not None for value in values):
                    label = dimensions[0]
                    categories = [(str(row[label]) if len(str(row[label])) <= 24 else str(row[label])[:18] + f"…[{index + 1}]")
                                  if row.get(label) is not None else "未提供（空值）" for index, row in enumerate(rows)]
                    slides.append({"layout": "chart", "title": f"{labels[chosen]} · 按{labels[label]}对比", "subtitle": scope_text,
                        "chart": {"chart_type": "bar", "categories": categories,
                                  "series": [{"name": labels[chosen] + (f"（{unit}）" if unit else ""), "values": values}]},
                        "notes": notes})
            preview_rows = rows[:7]
            # 明细只取一个业务名称和关键数值；完整字段及长内容仍在本页备注。
            leading = next((name for name in dimensions if re.search(r"名称|部门|客户|供应商|物料|name", labels[name], re.I)), None)
            fields = list(dict.fromkeys(([leading] if leading else dimensions[:1]) + metrics + names))[:5]
            footnote = f"预览前 {len(preview_rows)} / {len(rows)} 行、{len(fields)} / {len(names)} 个字段；全部返回值见本页备注。{scope_text}"
            cells_notes = []
            headers = [labels[name] for name in fields]
            cells = [[_cell(_display_value(row[name], name) if name in metrics else row[name], cells_notes,
                           reference=f"行 {index + 1} / {name}", maximum=28)
                      if name in row else "未返回字段" for name in fields] for index, row in enumerate(preview_rows)]
            role = f"按{labels[dimensions[0]]}汇总" if grouped and dimensions else "分组汇总" if grouped else "明细预览"
            slides.append({"layout": "table", "title": _slide_title(role, source),
                "subtitle": footnote, "key_message": footnote, "items": [footnote, {"type": "table", "rows": [headers, *cells]}],
                "notes": notes + ("\n长字段内容：\n" + "\n".join(f"[注{index + 1}] {value}" for index, value in enumerate(cells_notes)) if cells_notes else "")})
            scope.update(displayed_rows=len(preview_rows), displayed_columns=len(fields), displayed_fields=fields,
                         table_pages=1, notes_cells=len(cells_notes))
        scope["report_pages"] = len(slides) - start
        scopes.append(scope)
    return {"title": title, "subtitle": "关键指标 · 分组对比 · 数据范围", "include_cover": True,
            "include_closing": False, "page_count": "auto", "slides": slides,
            "cover_notes": "数字绑定已保存查询，不重新取数。页面是汇报摘要，完整原始行列见来源页备注。"}, scopes
