"""精提Test5胶囊/背景柱/仪表与业务展示语义；不执行原自定义脚本或制造业务位置。"""
from calendar import Calendar
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from html import escape
from hashlib import sha256
import json
import math
import re
from zoneinfo import ZoneInfo

from .capabilities import SOURCE_NAMES, SOURCE_TYPES, source_variant
from .chart_data import _number


BAR_TYPES = {"JCapsuleChart", "JBackgroundBar"}
GAUGE_TYPES = {"JColorGauge", "JAntvGauge"}
CALENDAR_TYPES = {"JPermanentCalendar", "JCalendar", "JMultiViewCalendar"}
CARD_TYPES = {"JCardScroll", "JCardCarousel"}
DATED_TYPES = CALENDAR_TYPES | {"JDevHistory"}
SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\Z")


def _gauge_options(option):
    """色段仅为可编辑显示刻度，不代表质量或设备故障阈值。"""
    minimum, maximum = _number(option.get("min", 0)), _number(option.get("max", 100))
    if minimum is None or maximum is None or minimum >= maximum:
        raise ValueError("仪表下限须小于上限")
    stops = option.get("gauge_stops")
    if stops is None:
        with localcontext() as context:
            context.prec = 220
            stops = [{"value": str(minimum + (maximum - minimum) * fraction), "color": color}
                     for fraction, color in ((Decimal("0.33"), "#36d7b7"), (Decimal("0.66"), "#4a9eff"), (Decimal(1), "#f7c948"))]
    if not isinstance(stops, list) or not 1 <= len(stops) <= 8:
        raise ValueError("仪表色段请选择1到8段")
    result, previous = [], minimum
    for stop in stops:
        if not isinstance(stop, dict) or not isinstance(stop.get("color"), str) or not _HEX_COLOR.fullmatch(stop["color"]):
            raise ValueError("仪表色段颜色无效")
        value = _number(stop.get("value"))
        if value is None or not previous < value <= maximum:
            raise ValueError("仪表色段须递增且不超过上限")
        with localcontext() as context:
            context.prec = 220
            position = str((value - minimum) / (maximum - minimum))
        result.append({"value": str(value), "color": stop["color"], "position": position})
        previous = value
    if previous != maximum:
        raise ValueError("最后一个色段须到达仪表上限")
    with localcontext() as context:
        context.prec = 220
        ticks = [{"position": str(Decimal(index) / 5),
                  "value": str(minimum if index == 0 else maximum if index == 5 else minimum + (maximum - minimum) * Decimal(index) / 5)} for index in range(6)]
    return {"minimum": str(minimum), "maximum": str(maximum), "gauge_stops": result, "gauge_ticks": ticks}


def validate_options(widget):
    """保存时反馈无效控件值，数据映射待真实取数后再检查，不妨碍创建空组件。"""
    component = widget.get("component")
    option = widget.get("config", {}).get("option", {})
    if component in GAUGE_TYPES:
        _gauge_options(option)
    if component in CALENDAR_TYPES:
        calendar = option.get("calendar", {})
        if not isinstance(calendar, dict) or calendar.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
            raise ValueError("日历请选择北京时间")
        month = calendar.get("month")
        if month is not None:
            if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValueError("请选择有效月份")
            try:
                date.fromisoformat(month + "-01")
            except ValueError as exc:
                raise ValueError("请选择有效月份") from exc
    if component in CARD_TYPES:
        carousel = option.get("carousel", {})
        if not isinstance(carousel, dict):
            raise ValueError("卡片播放设置无效")
        for key, low, high, default in (("page_size", 1, 8, 3), ("interval_seconds", 3, 60, 5)):
            value = carousel.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError("卡片数量或播放间隔超出范围")
        if not isinstance(carousel.get("autoplay", False), bool):
            raise ValueError("请选择是否自动播放")


def _event_time(value):
    """日期属于企业自然日；含时刻的数据必须明确时区，不能猜测服务器本地时间。"""
    if not isinstance(value, str) or not value:
        raise ValueError("时间字段存在空值，请补齐日期或带时区的时间")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = date.fromisoformat(value)
            timestamp = datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).astimezone(timezone.utc)
            return day.isoformat(), timestamp.isoformat(), day.isoformat()
        if not re.match(r"\d{4}-\d{2}-\d{2}T", value):
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        local = parsed.astimezone(SHANGHAI)
        return local.date().isoformat(), parsed.astimezone(timezone.utc).isoformat(), local.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError) as exc:
        raise ValueError("时间字段须为日期或带时区的ISO时间") from exc


def source_model(widget, rows):
    """只用真实已授权字段形成模型；数值保留Decimal，月份/事件位置来自实际日期。"""
    component, binding = widget.get("component"), widget.get("binding") or {}
    result = {"status": "empty", "component": component, "rows": [], "columns": []}
    if component in CARD_TYPES:
        result["variant"] = {**source_variant(widget), "mode": "carousel" if component == "JCardCarousel" else "scroll"}
    if not rows:
        return result
    if not isinstance(rows, list) or len(rows) > 2000 or any(not isinstance(row, dict) for row in rows):
        return {**result, "status": "invalid_data", "message": "请将组件数据控制在2000行以内"}
    label, value = binding.get("label_field", "name"), binding.get("value_field", "value")
    optional = {key: binding.get(key) for key in ("description_field", "time_field", "unit_field")}
    numeric = component in BAR_TYPES | GAUGE_TYPES
    required = [label] + ([value] if numeric or binding.get("value_field") else []) + [field for field in optional.values() if field]
    if component in DATED_TYPES and not optional["time_field"]:
        return {**result, "status": "needs_mapping", "message": "请选择时间字段"}
    if any(any(field not in row for field in required) for row in rows):
        return {**result, "status": "needs_mapping", "message": "请选择结果中实际存在的字段"}
    if component in GAUGE_TYPES and len(rows) != 1:
        return {**result, "status": "needs_mapping", "message": "仪表需要一条实测值，请调整查询"}
    try:
        validate_options(widget)
        for row in rows:
            reads_value = numeric or bool(binding.get("value_field")) or component in CARD_TYPES
            number = _number(row[value]) if reads_value and value in row else None
            item = {"name": str(row[label]) if row[label] is not None else "", "value": str(number) if number is not None else None,
                    "description": "", "time": "", "unit": ""}
            for key in optional:
                field = optional[key]
                if field and row[field] is not None:
                    item[key.removesuffix("_field")] = str(row[field])
            if component in DATED_TYPES:
                item["date"], item["_sort_time"], item["time_display"] = _event_time(row[optional["time_field"]])
            elif component in CARD_TYPES and item["time"]:
                # 卡片时间可用于业务文字；仅在确实是带时区时间/日期时提供本地显示。
                try:
                    item["time_display"] = _event_time(item["time"])[2]
                except ValueError:
                    item["time_display"] = item["time"]
            result["rows"].append(item)
        if component in GAUGE_TYPES:
            result.update(_gauge_options(widget.get("config", {}).get("option", {})))
            number = _number(result["rows"][0]["value"])
            if number is not None and not Decimal(result["minimum"]) <= number <= Decimal(result["maximum"]):
                raise ValueError("实测值超出仪表量程，请调整量程")
            with localcontext() as context:
                context.prec = 220
                result["gauge_position"] = None if number is None else str((number - Decimal(result["minimum"])) / (Decimal(result["maximum"]) - Decimal(result["minimum"])))
        if component in DATED_TYPES:
            result["rows"].sort(key=lambda row: row.pop("_sort_time"))
        if component in CALENDAR_TYPES:
            configured = widget.get("config", {}).get("option", {}).get("calendar", {}).get("month")
            result["calendar"] = {"month": configured or result["rows"][0]["date"][:7], "timezone": "Asia/Shanghai"}
        result["status"] = "ready" if not numeric or any(row["value"] is not None for row in result["rows"]) else "empty"
    except ValueError as exc:
        return {**result, "status": "invalid_data", "message": str(exc)}
    return result


def _gauge_svg(widget, model, color):
    """两种仪表分别保留分段色环和连续渐变；不把超量程读数裁成有效比例。"""
    value = model["rows"][0]["value"]
    ratio = float(model["gauge_position"])
    stops = [(float(stop["position"]), stop["color"]) for stop in model["gauge_stops"]]
    def point(fraction, radius=105):
        angle = math.pi * (1 - fraction)
        return 200 + radius * math.cos(angle), 145 - radius * math.sin(angle)
    def arc(start, end):
        x, y = point(start)
        xx, yy = point(end)
        return f'M{x},{y} A105,105 0 0,1 {xx},{yy}'
    shapes = ['<path d="M95,145 A105,105 0 0,1 305,145" fill="none" stroke="currentColor" opacity=".12" stroke-width="20"/>']
    if widget["component"] == "JAntvGauge":
        identifier = "gauge-" + sha256(widget["id"].encode()).hexdigest()[:16]
        gradient = '<stop offset="0%" stop-color="' + stops[0][1] + '"/>' + ''.join(f'<stop offset="{fraction * 100}%" stop-color="{shade}"/>' for fraction, shade in stops)
        shapes.append(f'<defs><linearGradient id="{identifier}" x1="0" x2="1" y1="0" y2="0">{gradient}</linearGradient></defs>')
        shapes.append(f'<path data-gauge="gradient" d="{arc(0, 1)}" fill="none" stroke="url(#{identifier})" stroke-width="20"/>')
    else:
        start = 0
        for end, shade in stops:
            shapes.append(f'<path data-gauge="segment" d="{arc(start, end)}" fill="none" stroke="{shade}" stroke-width="20"/>')
            start = end
    x, y = point(ratio, 85)
    shapes.append(f'<line data-value="{escape(str(value), quote=True)}" x1="200" y1="145" x2="{x}" y2="{y}" stroke="{color}" stroke-width="3"/><circle cx="200" cy="145" r="6" fill="{color}"/>')
    row = model["rows"][0]
    shapes.append(f'<text x="200" y="190" text-anchor="middle" font-size="28">{escape(row["value"])}{escape(row["unit"])}</text><text x="200" y="218" text-anchor="middle">{escape(row["name"])}</text>')
    shapes.append(f'<text x="85" y="170" text-anchor="middle">{escape(model["minimum"])}</text><text x="315" y="170" text-anchor="middle">{escape(model["maximum"])}</text>')
    return '<svg viewBox="0 0 400 240" style="width:100%;height:100%"><g fill="currentColor" font-size="13">' + ''.join(shapes) + '</g></svg>'


def _bars_svg(widget, model, palette):
    """胶囊横向圆角，背景柱竖向带底柱；正负共享零轴，缺测不补柱。"""
    rows = model["rows"]
    values = [Decimal(row["value"]) for row in rows if row["value"] is not None]
    low, high = min(min(values), Decimal(0)), max(max(values), Decimal(0))
    with localcontext() as context:
        context.prec = 220
        span = high - low or Decimal(1)
        scale = lambda number: float((number - low) / span)
        zero = scale(Decimal(0))
        points = [None if row["value"] is None else scale(Decimal(row["value"])) for row in rows]
    horizontal = widget["component"] == "JCapsuleChart"
    shapes = []
    for index, (row, position) in enumerate(zip(rows, points)):
        shade = palette[index % len(palette)]
        if horizontal:
            step = 210 / len(rows)
            y, height = 10 + index * step, max(step * .55, .2)
            start, length = 130 + zero * 400, 0 if position is None else (position - zero) * 400
            shapes.append(f'<rect data-background="capsule" x="130" y="{y}" width="400" height="{height}" rx="{height/2}" fill="currentColor" opacity=".09"/>')
            if position is not None:
                shapes.append(f'<rect data-value="{escape(row["value"], quote=True)}" x="{min(start,start+length)}" y="{y}" width="{abs(length)}" height="{height}" rx="{min(height/2,abs(length)/2)}" fill="{shade}"/>')
            shapes.append(f'<text x="5" y="{y+height*.7}">{escape(row["name"])}</text><text x="540" y="{y+height*.7}">{escape(row["value"]) if row["value"] is not None else "—"}</text>')
        else:
            step = 550 / len(rows)
            x, width = 45 + index * step, step * .6
            shapes.append(f'<rect data-background="column" x="{x}" y="10" width="{width}" height="200" fill="currentColor" opacity=".09"/>')
            if position is not None:
                top, bottom = 210 - position * 200, 210 - zero * 200
                shapes.append(f'<rect data-value="{escape(row["value"], quote=True)}" x="{x}" y="{min(top,bottom)}" width="{width}" height="{abs(top-bottom)}" fill="{shade}"/>')
            # 负数标签也放在柱外，避免浅色文字落在黄色/浅色柱上失去对比度。
            label_y = max(14, min(210 - (zero if position is None else position) * 200, 210 - zero * 200) - 5)
            shapes.append(f'<text x="{x+width/2}" y="230" text-anchor="middle">{escape(row["name"])}</text><text x="{x+width/2}" y="{label_y}" text-anchor="middle">{escape(row["value"]) if row["value"] is not None else "—"}</text>')
    return '<svg viewBox="0 0 640 250" style="width:100%;height:100%"><g fill="currentColor" font-size="13">' + ''.join(shapes) + '</g></svg>'


def _calendar_anchor(widget):
    return "source-agenda-" + sha256(str(widget["id"]).encode()).hexdigest()[:16]


def _calendar_html(widget, model, component):
    year, month = (int(value) for value in model["calendar"]["month"].split("-"))
    # Python日历按真实自然日排位；绝不按行号散布事件。
    weeks = Calendar(firstweekday=0).monthdayscalendar(year, month)
    events = {}
    for row in model["rows"]:
        events.setdefault(row["date"], []).append(row)
    cells = []
    for week in weeks:
        for day in week:
            key = f'{year:04}-{month:02}-{day:02}'
            # 固定月格只索引事项名；完整说明放在画布后可分页日程，不依赖被裁掉的滚动区域。
            items = ''.join('<a class="source-event" href="#' + _calendar_anchor(widget) + '">' + escape(row["name"]) + '</a>' for row in events.get(key, [])) if day else ''
            cells.append(f'<div data-date="{key if day else ""}"><b>{day or ""}</b>{items}</div>')
    heading = f'{year}年{month}月'
    aside = ''
    if component == "JMultiViewCalendar":
        aside = '<aside>' + ''.join('<article><time>' + escape(row["date"]) + '</time><strong>' + escape(row["name"]) + '</strong></article>' for row in model["rows"] if row["date"].startswith(model["calendar"]["month"])) + '</aside>'
    return '<div class="source-calendar"><header>' + heading + '<a href="#' + _calendar_anchor(widget) + '">月日程</a></header><div class="source-calendar-layout"><div class="source-month"><div class="source-week">' + ''.join('<b>' + day + '</b>' for day in '一二三四五六日') + '</div><div class="source-days" style="grid-template-rows:repeat(' + str(len(weeks)) + ',minmax(0,1fr))">' + ''.join(cells) + '</div></div>' + aside + '</div></div>'


def render_source(widget, rows, color, palette):
    if widget.get("component") not in SOURCE_TYPES:
        return None
    model = source_model(widget, rows)
    if model["status"] != "ready":
        return '<div class="presentation-empty">' + escape(model.get("message", "暂无数据")) + '</div>'
    component = widget["component"]
    if component in BAR_TYPES:
        return _bars_svg(widget, model, palette)
    if component in GAUGE_TYPES:
        return _gauge_svg(widget, model, color)
    if component in CALENDAR_TYPES:
        return _calendar_html(widget, model, component)
    if component == "JDevHistory":
        return '<ol class="source-history">' + ''.join('<li><time>' + escape(row["time_display"]) + '</time><strong>' + escape(row["name"]) + '</strong><p>' + escape(row["description"]) + '</p></li>' for row in model["rows"]) + '</ol>'
    variant = model["variant"]
    classes = 'source-cards ' + variant["direction"] + (' highlight' if variant["highlight"] else '')
    cards = []
    for index, row in enumerate(model["rows"]):
        number = '<b class="source-index">' + str(index + 1) + '</b>' if variant["show_index"] else ''
        value = '<strong>' + escape(row["value"]) + escape(row["unit"]) + '</strong>' if row["value"] is not None else ''
        cards.append('<article>' + number + '<h3>' + escape(row["name"]) + '</h3>' + value + '<p>' + escape(row["description"]) + '</p><time>' + escape(row.get("time_display", row["time"])) + '</time></article>')
    return '<div class="' + classes + '" data-mode="' + variant["mode"] + '" style="--source-card-columns:' + str(min(len(cards), 4)) + '">' + ''.join(cards) + '</div>'


def source_document_detail(widget, rows):
    """输出当前月完整事实的文档流，避免HTML/PDF把固定画布滚动区当成全部内容。"""
    if widget.get("component") not in CALENDAR_TYPES:
        return None
    model = source_model(widget, rows)
    if model["status"] != "ready":
        return None
    month = model["calendar"]["month"]
    events = [row for row in model["rows"] if row["date"].startswith(month)]
    if not events:
        return None
    columns = [("time_display", "时间"), ("name", "事项")]
    columns += [(field, label) for field, label in (("description", "说明"), ("value", "数值"), ("unit", "单位"))
                if any(row.get(field) not in (None, "") for row in events)]
    values = [[row.get(field) if row.get(field) is not None else "—" for field, _ in columns] for row in events]
    # 语义相同的多个日历样式共用明细；用户显式命名的业务范围仍分别保留。
    explicit_title = str(widget.get("title") or "")
    key = json.dumps([month, explicit_title, columns, values], ensure_ascii=False, sort_keys=True)
    return {"key": key, "anchors": [_calendar_anchor(widget)],
            "title": (explicit_title + " · " if explicit_title else "") + month + " 月日程",
            "columns": [label for _, label in columns], "rows": values}


def render_source_details(details, width):
    """完整事实位于绝对定位画布之外，长说明自然换行并允许打印分页。"""
    sections = []
    for detail in details:
        anchors = ''.join('<span id="' + anchor + '"></span>' for anchor in detail["anchors"])
        header = ''.join('<th scope="col">' + escape(label) + '</th>' for label in detail["columns"])
        body = ''.join('<tr>' + ''.join('<td>' + escape(str(value)) + '</td>' for value in row) + '</tr>' for row in detail["rows"])
        sections.append('<article class="source-agenda">' + anchors + '<h2>' + escape(detail["title"]) + '</h2><table><thead><tr>' + header + '</tr></thead><tbody>' + body + '</tbody></table></article>')
    return '<div class="source-document-details" style="max-width:' + str(float(width)) + 'px">' + ''.join(sections) + '</div>' if sections else ''


def source_office_section(widget, rows):
    """Office导出全部精确明细，不能把日历和卡片偷换成无依据的柱图。"""
    model = source_model(widget, rows)
    section = {"title": widget.get("title") or widget.get("componentName") or SOURCE_NAMES[widget["component"]]}
    if model["status"] != "ready":
        return {**section, "text": model.get("message", "暂无数据")}
    keys = ["name"] + (["value"] if any(row["value"] is not None for row in model["rows"]) else [])
    keys += [key for key in ("unit", "description", "time", "date") if any(row.get(key) for row in model["rows"])]
    names = {"name": "名称", "value": "数值", "unit": "单位", "description": "说明", "time": "原始时间", "date": "日期（北京时间）"}
    return {**section, "rows": [[names[key] for key in keys]] + [[row.get(key) if row.get(key) is not None else "—" for key in keys] for row in model["rows"]]}


def source_styles():
    """沿Test5业务展示的时间轴、暗色月历和描边卡片保留结构，静态导出不执行交互脚本。"""
    return '''
    .source-history{list-style:none;margin:0;padding:18px 24px;overflow:auto;height:100%;box-sizing:border-box}
    .source-history li{border-left:2px solid #35b5e680;padding:0 0 18px 20px;position:relative;overflow-wrap:anywhere}
    .source-history li:before{content:"";position:absolute;left:-5px;top:4px;width:8px;height:8px;border-radius:50%;background:#35b5e6}
    .source-history time,.source-cards time{display:block;opacity:.75}
    .source-history strong{display:block;margin-top:4px}
    .source-history p,.source-cards p{margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere}
    .source-calendar{height:100%;padding:12px;box-sizing:border-box;border:1px solid #59aae63d;background:linear-gradient(150deg,#0c2a47,#051527);color:#bad2e4}
    .source-calendar header{display:flex;align-items:center;justify-content:space-between;height:40px;box-sizing:border-box;font-size:17px;border-bottom:1px solid #65afe52e}
    .source-calendar header a{color:#88dcff;font-size:13px}
    .source-calendar-layout{display:flex;height:calc(100% - 40px);min-height:0}
    .source-month{flex:1;min-width:0;min-height:0}
    .source-week,.source-days{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
    .source-week{height:28px;text-align:center}
    .source-days{height:calc(100% - 28px)}
    .source-days>div{box-sizing:border-box;min-height:0;border:1px solid #65afe51c;padding:3px;overflow:hidden;font-size:12px}
    .source-days b{display:block}
    .source-event{display:block;margin-top:3px;padding:2px;background:#168ed033;color:inherit;text-decoration:none;overflow-wrap:anywhere}
    .source-calendar aside{box-sizing:border-box;flex:0 0 32%;min-width:0;min-height:0;padding:12px;overflow:auto;border-left:1px solid #65afe52e;overflow-wrap:anywhere}
    .source-calendar aside strong,.source-calendar aside time{display:block}
    .source-cards{height:100%;display:grid;grid-template-columns:repeat(var(--source-card-columns,3),minmax(0,1fr));gap:10px;padding:12px;overflow:auto;box-sizing:border-box}
    .source-cards article{position:relative;min-width:0;box-sizing:border-box;padding:12px;border:1px solid #46c8ff55;border-radius:6px;background:linear-gradient(135deg,#16466e22,#0c244022);overflow-wrap:anywhere}
    .source-cards.vertical{grid-template-columns:1fr;gap:8px}
    .source-cards.vertical article{display:grid;grid-template-columns:24px minmax(0,1fr) auto;grid-template-areas:"index name value" "index description time";align-items:center;gap:4px 8px;padding:5px 10px}
    .source-cards.vertical .source-index{grid-area:index;margin:0}
    .source-cards.vertical h3{grid-area:name;margin:0}
    .source-cards.vertical strong{grid-area:value;font-size:1.2em}
    .source-cards.vertical p{grid-area:description;margin:0}
    .source-cards.vertical time{grid-area:time;font-size:12px}
    .source-cards.highlight article{border-color:#35e4c6;background:linear-gradient(135deg,#35e4c622,#168ed033)}
    .source-cards h3{font-size:inherit;margin:0 0 10px}
    .source-cards strong{font-size:1.6em}
    .source-index{display:inline-grid;place-items:center;min-width:24px;min-height:24px;margin-bottom:8px;background:#168ed044}
    .source-document-details{box-sizing:border-box;padding:24px;color:#173758;background:#fff}
    .source-agenda{margin:0 0 24px;overflow-wrap:anywhere}
    .source-agenda h2{position:static;margin:0 0 16px;font-size:18px}
    .source-agenda table{table-layout:auto}
    .source-agenda th,.source-agenda td{white-space:pre-wrap;overflow:visible;text-overflow:clip;border-bottom:1px solid #d8e2eb;vertical-align:top}
    .source-agenda th{color:#22486b;background:#edf4fa}
    .source-agenda thead{display:table-header-group}
    @media print{.source-document-details{break-before:page}.source-agenda tr{break-inside:avoid}}
    '''
