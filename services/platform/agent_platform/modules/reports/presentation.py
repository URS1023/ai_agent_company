"""精提Test5文字、排名、表格、卡片及非笛卡尔图；字段和数值由可信查询投影。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from html import escape
from html.parser import HTMLParser
import math

from .chart_data import _number
from .capabilities import SOURCE_NAMES, SOURCE_TYPES


TABLE_TYPES = {"JCommonTable", "JScrollTable", "JScrollRankingBoard"}
RANK_TYPES = {"JRankingList", "JFlashList"}
LIST_TYPES = {"JList", "JScrollList"}
CARD_TYPES = {"JGrowCard", "JSimpleCard", "JColorBlock"}
NUMBER_TYPES = {"JNumber", "JCountTo"}
PROGRESS_TYPES = {"JProgress", "JCustomProgress", "JListProgress", "JTotalProgress", "JRingProgress", "JRoundProgress"}
SPECIAL_CHART_TYPES = {"JFunnel", "JPyramidFunnel", "JRose", "JSemiGauge", "JCircleRadar", "JRectangle"}
STATIC_TYPES = {"JCurrentTime", "JDragEditor"}
PRESENTATION_TYPES = TABLE_TYPES | RANK_TYPES | LIST_TYPES | CARD_TYPES | NUMBER_TYPES | PROGRESS_TYPES | SPECIAL_CHART_TYPES | STATIC_TYPES
PRESENTATION_TYPES |= SOURCE_TYPES
NAMES = {
    "JCommonTable": "数据表格", "JScrollTable": "滚动表格", "JScrollRankingBoard": "排名表格",
    "JRankingList": "排行榜", "JFlashList": "个性排名", "JList": "数据列表", "JScrollList": "滚动列表",
    "JGrowCard": "统计卡片", "JSimpleCard": "简洁卡片", "JColorBlock": "颜色卡片",
    "JNumber": "数值", "JCountTo": "翻牌器", "JCurrentTime": "当前时间", "JDragEditor": "富文本",
    "JProgress": "进度图", "JCustomProgress": "基础进度图", "JListProgress": "列表进度图",
    "JTotalProgress": "统计进度图", "JRingProgress": "环形进度", "JRoundProgress": "圆形进度",
    "JFunnel": "漏斗图", "JPyramidFunnel": "金字塔漏斗", "JRose": "玫瑰图", "JSemiGauge": "半圆仪表",
    "JCircleRadar": "圆形雷达", "JRectangle": "矩形图",
}
NAMES.update(SOURCE_NAMES)


class _RichText(HTMLParser):
    """保留基础排版，不接受事件、样式、链接、图片或可执行嵌入。"""
    tags = {"p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "h1", "h2", "h3", "blockquote", "pre", "code", "span", "table", "thead", "tbody", "tr", "th", "td"}
    blocked = {"script", "style", "iframe", "object", "svg", "math", "template", "video", "audio"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.stack, self.hidden = [], [], []

    def handle_starttag(self, tag, attrs):
        if self.hidden or tag in self.blocked:
            self.hidden.append(tag)
        elif tag in self.tags:
            self.output.append("<" + tag + ">")
            if tag != "br":
                self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag in self.hidden:
                self.hidden = self.hidden[:self.hidden.index(tag)]
        elif tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.output.append("</" + current + ">")
                if current == tag:
                    break

    def handle_data(self, data):
        if not self.hidden:
            self.output.append(escape(data))

    def result(self):
        return "".join(self.output) + "".join("</" + tag + ">" for tag in reversed(self.stack))


def sanitize_html(value):
    parser = _RichText()
    parser.feed(str(value or "")[:100000])
    parser.close()
    return parser.result()


def clock_text(option, now=None):
    """离线输出记录本次渲染时间，不能把浏览器本地时区默认为企业时区。"""
    zone = timezone.utc if option.get("timezone") == "UTC" else timezone(timedelta(hours=8))
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    return current.strftime("%Y-%m-%d" if option.get("date_only") else "%Y-%m-%d %H:%M:%S")


def presentation_model(widget, rows):
    """仅投影明确字段；Decimal保留原精度，缺失不补零，排名不使用浮点排序。"""
    component = widget.get("component")
    if component in SOURCE_TYPES:
        from .source_presentations import source_model
        return source_model(widget, rows)
    binding, config = widget.get("binding") or {}, widget.get("config") or {}
    option = config.get("option") or {}
    result = {"status": "empty", "component": component, "rows": [], "columns": []}
    if component in STATIC_TYPES:
        return {**result, "status": "ready", "html": sanitize_html(option.get("body", {}).get("text", ""))} if component == "JDragEditor" else {**result, "status": "ready"}
    if not rows:
        return result
    if len(rows) > 2000 or any(not isinstance(row, dict) for row in rows):
        return {**result, "status": "invalid_data", "message": "请将组件数据控制在2000行以内"}
    fields = binding.get("fields") or list(dict.fromkeys(key for row in rows for key in row))
    if component in TABLE_TYPES:
        if len(fields) > 20 or any(any(field not in row for row in rows) for field in fields):
            return {**result, "status": "needs_mapping", "message": "请选择结果中实际存在的不超过20个列字段"}
        labels = config.get("column_labels") or {}
        return {**result, "status": "ready", "columns": [{"field": field, "label": str(labels.get(field, field))} for field in fields], "rows": [{field: row[field] for field in fields} for row in rows]}
    label, value = binding.get("label_field", "name"), binding.get("value_field", "value")
    numeric = component not in LIST_TYPES
    required = ([value] if component in NUMBER_TYPES else [label, value] if numeric else [label])
    optional = {key: binding.get(key) for key in ("description_field", "time_field", "unit_field", "total_field")}
    required += [field for field in optional.values() if field]
    if any(any(field not in row for field in required) for row in rows):
        return {**result, "status": "needs_mapping", "message": "请选择查询结果中的名称、数值和其他映射字段"}
    if component in NUMBER_TYPES | {"JSemiGauge", "JProgress", "JCustomProgress", "JRingProgress", "JRoundProgress"} and len(rows) != 1:
        return {**result, "status": "needs_mapping", "message": "此组件需要一条数值，请调整查询"}
    try:
        for row in rows:
            number = _number(row[value]) if numeric else None
            item = {"name": str(row[label]) if row.get(label) is not None else "", "value": None if number is None else str(number), "description": str(row[optional["description_field"]]) if optional["description_field"] and row.get(optional["description_field"]) is not None else "", "time": str(row[optional["time_field"]]) if optional["time_field"] and row.get(optional["time_field"]) is not None else "", "unit": str(row[optional["unit_field"]]) if optional["unit_field"] and row.get(optional["unit_field"]) is not None else ""}
            if component in PROGRESS_TYPES:
                total = _number(row[optional["total_field"]]) if optional["total_field"] else Decimal(100)
                if total is None or total <= 0 or number is not None and not 0 <= number <= total:
                    raise ValueError("进度数值须在零与目标值之间，目标值须大于零")
                with localcontext() as context:
                    context.prec = 220
                    item["percentage"] = None if number is None else str(number / total * 100)
                item["total"] = str(total)
            if component in SPECIAL_CHART_TYPES and number is not None and number < 0:
                raise ValueError("此图表需要非负数值")
            result["rows"].append(item)
        if component == "JCircleRadar" and any(row["value"] is None for row in result["rows"]):
            raise ValueError("雷达图指标存在缺失值，请补齐数据")
        if component in RANK_TYPES:
            result["rows"].sort(key=lambda row: (row["value"] is None, Decimal(row["value"]).copy_negate() if row["value"] is not None else 0))
        if component in {"JFunnel", "JPyramidFunnel"}:
            result["rows"].sort(key=lambda row: (row["value"] is None, (Decimal(row["value"]) if component == "JPyramidFunnel" else Decimal(row["value"]).copy_negate()) if row["value"] is not None else 0))
        if component == "JSemiGauge":
            maximum = _number(option.get("max", 100))
            if maximum is None or maximum <= 0:
                raise ValueError("仪表上限须大于零")
            if result["rows"][0]["value"] is not None and Decimal(result["rows"][0]["value"]) > maximum:
                raise ValueError("实测值超过仪表上限，请调整上限")
            result["maximum"] = str(maximum)
        result["status"] = "ready" if not numeric or any(row["value"] is not None for row in result["rows"]) else "empty"
    except ValueError as exc:
        return {**result, "status": "invalid_data", "message": str(exc)}
    return result


def render_presentation(widget, rows, color, palette):
    """离线HTML/打印复用相同字段与排序，不将专用图形退化为通用表格。"""
    component = widget.get("component")
    if component in SOURCE_TYPES:
        from .source_presentations import render_source
        return render_source(widget, rows, color, palette)
    if component not in PRESENTATION_TYPES:
        return None
    option = widget.get("config", {}).get("option", {})
    model = presentation_model(widget, rows)
    if component == "JCurrentTime":
        return '<div class="presentation-number">' + clock_text(option) + '</div>'
    if component == "JDragEditor":
        return '<div class="presentation-rich">' + model["html"] + '</div>'
    if model["status"] != "ready":
        return '<div class="presentation-empty">' + escape(model.get("message", "暂无数据")) + '</div>'
    values = model["rows"]
    if component in TABLE_TYPES:
        fields = model["columns"]
        return '<table><thead><tr>' + ''.join('<th>' + escape(column["label"]) + '</th>' for column in fields) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(row[column["field"]]) if row[column["field"]] is not None else "—") + '</td>' for column in fields) + '</tr>' for row in values) + '</tbody></table>'
    if component in SPECIAL_CHART_TYPES:
        return _special_svg(component, values, model, palette)
    result = []
    for index, row in enumerate(values):
        label, value = escape(row["name"]), escape(row["value"]) if row["value"] is not None else "—"
        if component in NUMBER_TYPES:
            result.append('<div class="presentation-number">' + value + escape(row["unit"]) + '</div>')
        elif component in PROGRESS_TYPES:
            percent = row["percentage"]
            if component in {"JRoundProgress", "JRingProgress"} and percent is not None:
                radius = 80 if component == "JRoundProgress" else 90
                result.append(f'<svg viewBox="0 0 240 240"><circle cx="120" cy="120" r="{radius}" fill="none" stroke="#24354b" stroke-width="15"/><circle cx="120" cy="120" r="{radius}" fill="none" stroke="{palette[0]}" stroke-width="15" stroke-dasharray="{float(percent) * math.tau * radius / 100} {math.tau * radius}" transform="rotate(-90 120 120)"/><text x="120" y="125" fill="{color}" text-anchor="middle">{value}/{escape(row['total'])}</text></svg>')
            else:
                result.append(f'<div class="presentation-progress"><span>{label}</span><b>{value}/{escape(row["total"])}</b><div><i style="width:{float(percent) if percent is not None else 0}%;background:{palette[index % len(palette)]}"></i></div></div>')
        elif component in CARD_TYPES:
            result.append(f'<div class="presentation-card"><span>{label}</span><strong>{value}{escape(row["unit"])}</strong></div>')
        else:
            result.append(f'<div class="presentation-list"><b>{index + 1}</b><span>{label}<small>{escape(row["description"])}</small></span><strong>{value if component in RANK_TYPES else escape(row["time"])}</strong></div>')
    return '<div class="presentation-body">' + ''.join(result) + '</div>'


def _special_svg(component, rows, model, palette):
    valid = [row for row in rows if row["value"] is not None]
    values = [float(row["value"]) for row in valid]
    if not values or not max(values) and component not in {"JSemiGauge", "JCircleRadar"}:
        return '<div class="presentation-empty">暂无可绘制数值</div>'
    maximum, total, shapes = max(values) or 1, sum(values), []
    if component in {"JFunnel", "JPyramidFunnel"}:
        step = 210 / len(valid)
        for index, row in enumerate(valid):
            top = values[index] / maximum * 400
            bottom = values[index + 1] / maximum * 400 if index + 1 < len(valid) else (top if component == "JPyramidFunnel" else 0)
            y = 20 + index * step
            shapes.append(f'<polygon data-value="{escape(row["value"], quote=True)}" points="{240-top/2},{y} {240+top/2},{y} {240+bottom/2},{y+step-2} {240-bottom/2},{y+step-2}" fill="{palette[index % len(palette)]}"/><text x="490" y="{y+step/2}" fill="currentColor">{escape(row["name"])} {escape(row["value"])}</text>')
    elif component == "JRectangle":
        x = 10
        for index, row in enumerate(valid):
            width = values[index] / total * 620
            shapes.append(f'<rect x="{x}" y="20" width="{width}" height="215" fill="{palette[index % len(palette)]}"/><text x="{x+5}" y="50">{escape(row["name"])} {escape(row["value"])}</text>')
            x += width
    elif component == "JSemiGauge":
        ratio = values[0] / float(model["maximum"])
        angle = math.pi * (1 - ratio)
        x, y = 240 + 120 * math.cos(angle), 210 - 120 * math.sin(angle)
        shapes.extend(['<path d="M120,210 A120,120 0 0,1 360,210" fill="none" stroke="#24354b" stroke-width="20"/>', f'<path d="M120,210 A120,120 0 0,1 {x},{y}" fill="none" stroke="{palette[0]}" stroke-width="20"/><text x="240" y="190" text-anchor="middle">{escape(valid[0]["value"])}</text>'])
    elif component == "JCircleRadar":
        points = []
        shapes.append('<circle cx="300" cy="130" r="100" fill="none" stroke="currentColor" opacity=".25"/>')
        for index, row in enumerate(valid):
            angle = index / len(valid) * math.tau - math.pi / 2
            x, y = 300 + math.cos(angle) * 100, 130 + math.sin(angle) * 100
            shapes.append(f'<line x1="300" y1="130" x2="{x}" y2="{y}" stroke="currentColor" opacity=".25"/><text x="{x}" y="{y}" text-anchor="middle">{escape(row["name"])}</text>')
            points.append(f'{300+math.cos(angle)*100*values[index]/maximum},{130+math.sin(angle)*100*values[index]/maximum}')
        shapes.append(f'<polygon points="{" ".join(points)}" fill="{palette[0]}" fill-opacity=".3" stroke="{palette[0]}"/>')
    elif component == "JRose":
        step = math.tau / len(valid)
        for index, row in enumerate(valid):
            radius = 105 * math.sqrt(values[index] / maximum)
            if len(valid) == 1:
                shapes.append(f'<circle data-value="{escape(row["value"], quote=True)}" cx="240" cy="130" r="{radius}" fill="{palette[0]}"/>')
                shapes.append(f'<text x="380" y="25">{escape(row["name"])} {escape(row["value"])}</text>')
                continue
            if not radius:
                shapes.append(f'<text x="380" y="{25+index*20}">{escape(row["name"])} 0</text>')
                continue
            start, end = index * step - math.pi / 2, (index + 1) * step - math.pi / 2
            x, y = 240 + radius * math.cos(start), 130 + radius * math.sin(start)
            xx, yy = 240 + radius * math.cos(end), 130 + radius * math.sin(end)
            shapes.append(f'<path data-value="{escape(row["value"], quote=True)}" d="M240,130 L{x},{y} A{radius},{radius} 0 {int(step > math.pi)},1 {xx},{yy} Z" fill="{palette[index % len(palette)]}"/><text x="380" y="{25+index*20}">{escape(row["name"])} {escape(row["value"])}</text>')
    return '<svg viewBox="0 0 640 260" style="color:inherit"><g fill="currentColor" font-size="14">' + ''.join(shapes) + '</g></svg>'


def office_section(widget, rows):
    """Office保持选择列、精确数值与排名；未支持的原生专用图不冒充柱形图。"""
    component = widget.get("component")
    if component in SOURCE_TYPES:
        from .source_presentations import source_office_section
        return source_office_section(widget, rows)
    if component not in PRESENTATION_TYPES:
        return None
    section = {"title": widget.get("title") or widget.get("componentName") or NAMES[component]}
    option = widget.get("config", {}).get("option", {})
    model = presentation_model(widget, rows)
    if component == "JCurrentTime":
        return {**section, "text": clock_text(option)}
    if component == "JDragEditor":
        class PlainText(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.values = []
            def handle_data(self, value):
                self.values.append(value)
            def handle_endtag(self, tag):
                if tag in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
                    self.values.append("\n")
            def handle_starttag(self, tag, attrs):
                if tag == "br":
                    self.values.append("\n")
        parser = PlainText()
        parser.feed(model["html"])
        return {**section, "text": "".join(parser.values).strip()}
    if model["status"] != "ready":
        return {**section, "text": model.get("message", "暂无数据")}
    if component in TABLE_TYPES:
        fields = model["columns"]
        return {**section, "rows": [[column["label"] for column in fields]] + [[row[column["field"]] if row[column["field"]] is not None else "—" for column in fields] for row in model["rows"]]}
    keys = ["name"] + (["value"] if component not in LIST_TYPES else [])
    if component in PROGRESS_TYPES:
        keys += ["total", "percentage"]
    keys += [key for key in ("unit", "description", "time") if any(row.get(key) for row in model["rows"])]
    names = {"name": "名称", "value": "数值", "total": "目标值", "percentage": "进度（%）", "unit": "单位", "description": "说明", "time": "时间"}
    return {**section, "rows": [[names[key] for key in keys]] + [[row.get(key) if row.get(key) is not None else "—" for key in keys] for row in model["rows"]]}


def presentation_styles():
    return '.presentation-body{height:100%;overflow:auto;padding:12px;box-sizing:border-box}.presentation-number{height:100%;display:grid;place-items:center;font-size:34px}.presentation-rich{padding:12px;overflow-wrap:anywhere}.presentation-list{display:flex;align-items:center;gap:12px;padding:10px;border-bottom:1px solid #7b96bb40}.presentation-list span{flex:1}.presentation-list small{display:block;opacity:.7}.presentation-card{padding:15px;background:#243e5d50;margin:6px;display:inline-flex;flex-direction:column;gap:10px}.presentation-card strong{font-size:28px}.presentation-progress{padding:10px}.presentation-progress b{float:right}.presentation-progress div{height:9px;background:#24354b;margin-top:8px}.presentation-progress i{height:100%;display:block}.presentation-empty{display:grid;place-items:center;height:100%;padding:10px;box-sizing:border-box}'
