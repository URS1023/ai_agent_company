"""离线画布输出，保留Test5绝对坐标、参考皮肤与各组件实际图形类型。"""
import base64
import html
import math
from pathlib import Path
import re

from agent_platform.core.settings import PROJECT_ROOT

ALIASES = {"text": "JText", "metric": "JStatsSummary", "line": "JLine", "bar": "JBar", "table": "JScrollBoard"}
PALETTE = ["#32a7e8", "#4bd3c7", "#e8b75d", "#9b8ce8", "#e77f99"]


def _rows(widget):
    value = widget.get("data", widget.get("config", {}).get("chartDataParsed", []))
    return [row if isinstance(row, dict) else {"value": row} for row in value] if isinstance(value, list) else [{"value": value}]


def _css(value, default):
    text = str(value or "")
    return text if len(text) < 500 and re.fullmatch(r"[A-Za-z0-9#(),.%+\-\s]+", text) and "url" not in text.lower() else default


def _asset(path):
    """只内嵌前端已提取的参考素材，不读用户路径或URL。"""
    if not isinstance(path, str) or not path.startswith(("/assets/analytics/jimu-screen/", "/jimu-screen/iconlib/")):
        return ""
    root = (PROJECT_ROOT / "apps" / "web" / "public").resolve()
    file = (root / path.lstrip("/")).resolve()
    # 源目录有一张3.17MB位图；单素材上限4MB，总导出仍受离线渲染器5MB输入预算约束。
    if not file.is_relative_to(root) or not file.is_file() or file.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or file.stat().st_size > 4 * 1024 * 1024:
        return ""
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[file.suffix.lower()]
    return "data:" + mime + ";base64," + base64.b64encode(file.read_bytes()).decode()


def _reference_image(widget, asset, *, backdrop):
    skin = widget["referenceSkin"]
    rect = skin.get("rect", [0, 0, widget.get("w", 100), widget.get("h", 100)])
    source_width, source_height = skin.get("sourceWidth", 1440), skin.get("sourceHeight", 900)
    mask, definition = "", ""
    if backdrop:
        identifier = "mask-" + re.sub(r"[^a-zA-Z0-9-]", "", widget["id"])
        holes = "".join(f'<rect x="{float(r[0])}" y="{float(r[1])}" width="{float(r[2])}" height="{float(r[3])}" fill="black"/>' for r in skin.get("holes", []) if isinstance(r, list) and len(r) == 4)
        definition = f'<defs><mask id="{identifier}" maskUnits="userSpaceOnUse"><rect width="{source_width}" height="{source_height}" fill="white"/>{holes}</mask></defs>'
        mask = f' mask="url(#{identifier})"'
    return f'<svg viewBox="{" ".join(str(float(v)) for v in rect)}" preserveAspectRatio="none">{definition}<image href="{asset}" width="{source_width}" height="{source_height}"{mask}/></svg>'


def _chart(widget, rows, component, color):
    from .chart_data import CARTESIAN_COMPONENTS
    if component in CARTESIAN_COMPONENTS:
        from .cartesian import render_cartesian
        palette = widget.get("referenceSkin", {}).get("palette", widget.get("config", {}).get("option", {}).get("color", PALETTE))
        palette = [_css(value, PALETTE[index % len(PALETTE)]) for index, value in enumerate(palette)] if isinstance(palette, list) else PALETTE
        return render_cartesian({**widget, "component": component}, rows, color, palette or PALETTE)
    binding = widget.get("binding") or {}
    value_field = binding.get("value_field", "value")
    label_field = binding.get("label_field", "name")
    values, labels, displays = [], [], []
    for i, row in enumerate(rows):
        raw = row.get(value_field)
        try:
            value = float(raw)
            if not math.isfinite(value):
                return ""
        except (TypeError, ValueError):
            return ""
        values.append(value)
        labels.append(html.escape(str(row.get(label_field, i + 1))))
        displays.append(html.escape(str(raw)))
    if not values:
        return ""
    palette = widget.get("referenceSkin", {}).get("palette", PALETTE)
    palette = [_css(c, PALETTE[i % len(PALETTE)]) for i, c in enumerate(palette)] or PALETTE
    shapes = ['<line x1="45" y1="220" x2="615" y2="220" stroke="currentColor" opacity=".3"/>']
    low, high = min(min(values), 0), max(max(values), 1)
    span = max(high - low, 1)
    if component in ("JLine", "JArea"):
        points = [(45 + i * 560 / max(len(values) - 1, 1), 215 - (v - low) * 175 / span) for i, v in enumerate(values)]
        serialized = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        if component == "JArea":
            shapes.append(f'<polygon points="45,220 {serialized} {points[-1][0]},220" fill="{palette[0]}" opacity=".22"/>')
        shapes.append(f'<polyline points="{serialized}" fill="none" stroke="{palette[0]}" stroke-width="3"/>')
        for i, (x, y) in enumerate(points):
            shapes.append(f'<circle cx="{x}" cy="{y}" r="3" fill="{palette[0]}"/><text x="{x}" y="{max(15, y - 8)}" text-anchor="middle">{displays[i]}</text>')
    elif component in ("JBar", "JHorizontalBar"):
        for i, value in enumerate(values):
            if component == "JHorizontalBar":
                size = 180 / len(values)
                length = abs(value) * 520 / max(abs(low), abs(high), 1)
                y = 25 + i * size
                shapes.append(f'<rect x="75" y="{y}" width="{length}" height="{max(2, size * .65)}" fill="{palette[i % len(palette)]}"/><text x="5" y="{y + size * .4}">{labels[i]}</text><text x="{80 + length}" y="{y + size * .4}">{displays[i]}</text>')
            else:
                size = 550 / len(values)
                zero = 215 - (0 - low) * 175 / span
                point = 215 - (value - low) * 175 / span
                x = 50 + i * size
                shapes.append(f'<rect x="{x}" y="{min(zero, point)}" width="{max(2, size * .65)}" height="{max(1, abs(zero - point))}" fill="{palette[i % len(palette)]}"/><text x="{x + size * .32}" y="{max(15, min(zero, point) - 8)}" text-anchor="middle">{displays[i]}</text>')
    elif component in ("JPie", "JRing"):
        if min(values) < 0 or sum(values) <= 0:
            return ""
        shapes = []
        angle, total = -math.pi / 2, sum(values)
        for i, value in enumerate(values):
            end = angle + value / total * math.tau
            x1, y1 = 220 + 92 * math.cos(angle), 125 + 92 * math.sin(angle)
            x2, y2 = 220 + 92 * math.cos(end), 125 + 92 * math.sin(end)
            if value == total:
                shapes.append(f'<circle cx="220" cy="125" r="92" fill="{palette[i % len(palette)]}"/>')
            else:
                shapes.append(f'<path d="M220,125 L{x1},{y1} A92,92 0 {1 if end - angle > math.pi else 0},1 {x2},{y2} Z" fill="{palette[i % len(palette)]}"/>')
            shapes.append(f'<rect x="355" y="{30 + i * 22}" width="10" height="10" fill="{palette[i % len(palette)]}"/><text x="375" y="{40 + i * 22}">{labels[i]} {displays[i]}</text>')
            angle = end
        if component == "JRing":
            shapes.append(f'<circle cx="220" cy="125" r="58" fill="{_css(widget.get("referenceSkin", {}).get("background"), "#071b30")}"/>')
    elif component == "JGauge":
        shapes = []
        maximum = widget.get("config", {}).get("option", {}).get("max", 100)
        try:
            ratio = min(1, max(0, values[0] / float(maximum)))
        except (ValueError, TypeError, ZeroDivisionError):
            return ""
        angle = math.pi * (1 - ratio)
        end_x, end_y = 320 + 100 * math.cos(angle), 180 - 100 * math.sin(angle)
        shapes.append('<path d="M220,180 A100,100 0 0,1 420,180" fill="none" stroke="currentColor" opacity=".2" stroke-width="20"/>')
        shapes.append(f'<path d="M220,180 A100,100 0 0,1 {end_x},{end_y}" fill="none" stroke="{palette[0]}" stroke-width="20"/><text x="320" y="175" font-size="30" text-anchor="middle">{displays[0]}</text>')
    elif component == "JRadar":
        if len(values) < 3:
            return ""
        shapes, points = [], []
        maximum = max(abs(v) for v in values) or 1
        for i, value in enumerate(values):
            angle = -math.pi / 2 + math.tau * i / len(values)
            x, y = 320 + 100 * math.cos(angle), 125 + 100 * math.sin(angle)
            shapes.append(f'<line x1="320" y1="125" x2="{x}" y2="{y}" stroke="currentColor" opacity=".3"/><text x="{x}" y="{y}" text-anchor="middle">{labels[i]}</text>')
            points.append(f"{320 + 90 * value / maximum * math.cos(angle)},{125 + 90 * value / maximum * math.sin(angle)}")
        shapes.append(f'<polygon points="{" ".join(points)}" fill="{palette[0]}" fill-opacity=".25" stroke="{palette[0]}" stroke-width="2"/>')
    else:
        return ""
    if component in ("JLine", "JArea", "JBar") and len(labels) <= 20:
        shapes.extend(f'<text x="{50 + (i + (.32 if component == "JBar" else 0)) * 550 / max(len(labels) if component == "JBar" else len(labels) - 1, 1)}" y="245" text-anchor="middle">{label}</text>' for i, label in enumerate(labels))
    return '<svg viewBox="0 0 640 260" preserveAspectRatio="none" style="color:' + color + '"><g fill="currentColor" font-size="13">' + ''.join(shapes) + '</g></svg>'


def render_html(report):
    if report["layout"].get("kind") == "inspection_report":
        from .inspection_document import render_html as render_document
        return render_document(report)
    from .materials import material_styles, render_material
    layout = report["layout"]
    canvas = layout.get("canvas", {})
    width, height = canvas.get("width", 1440), canvas.get("height", 900)
    widgets = []
    for order, widget in enumerate(layout["widgets"]):
        if widget.get("visible") is False:
            continue
        skin = widget.get("referenceSkin", {})
        component = ALIASES.get(widget.get("component", widget.get("type", "text")), widget.get("component", widget.get("type", "text")))
        rows = _rows(widget)
        first = rows[0] if rows else {}
        config = widget.get("config", {})
        title = str(widget.get("componentName", widget.get("name", "")))
        x, y, w, h = widget.get("x", 24), widget.get("y", 24 + order * 330), widget.get("w", 640), widget.get("h", 300)
        color = _css(skin.get("textColor", config.get("color")), "#173758" if not skin else "#eaf8ff")
        background = _css(skin.get("surface", skin.get("background", config.get("background"))), "transparent" if skin else "#ffffff")
        font = min(max(float(skin.get("fontSize", config.get("fontSize", 14))), 8), 160)
        sample = config.get("referenceSample", False) and not widget.get("binding")
        asset = _asset(skin.get("asset"))
        body = render_material(widget, rows, _asset, _css)
        if asset and (skin.get("kind") == "backdrop" or sample):
            body = _reference_image(widget, asset, backdrop=skin.get("kind") == "backdrop")
        elif body is not None:
            pass
        elif component == "JDragImg":
            asset = _asset(config.get("url", config.get("src", widget.get("image"))))
            body = f'<img src="{asset}" alt="{html.escape(title, quote=True)}">' if asset else ""
        elif component == "JText":
            text = config.get("option", {}).get("body", {}).get("text", config.get("text", widget.get("text", first.get("value", ""))))
            body = '<div class="text">' + html.escape(str(text)) + '</div>'
        elif component == "JStatsSummary":
            value = first.get((widget.get("binding") or {}).get("value_field", "value"), "")
            unit = first.get("unit", "")
            available_height = h - font - 3 if skin.get("label") else h - 3
            value_font = min(float(skin.get("valueFontSize", 36)), max(8, available_height / 1.15), max(8, w / max(len(str(value)) + len(str(unit)), 1) * 1.6))
            label = '<span>' + html.escape(str(first.get("name", title))) + '</span>' if skin.get("label", not bool(skin)) else ""
            body = f'<div class="metric">{label}<strong style="font-size:{value_font}px">{html.escape(str(value))}<small>{html.escape(str(unit))}</small></strong></div>'
        else:
            body = _chart(widget, rows, component, color)
            if not body:
                columns = list(dict.fromkeys(key for row in rows for key in row))
                body = '<table><thead><tr>' + ''.join('<th>' + html.escape(k) + '</th>' for k in columns) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + html.escape(str(row.get(k, ""))) + '</td>' for k in columns) + '</tr>' for row in rows) + '</tbody></table>'
        heading = '<h2>' + html.escape(title) + '</h2>' if not skin and component not in ("JText", "JStatsSummary", "JDragImg", "JDragBorder", "JDragDecoration", "JImg", "JCustomIcon", "JCarousel") else ""
        style = f"left:{float(x)}px;top:{float(y)}px;width:{float(w)}px;height:{float(h)}px;z-index:{int(widget.get('orderNum', order))};transform:rotate({float(widget.get('rotation', 0))}deg);font-size:{font}px;color:{color};background:{background};"
        widgets.append(f'<section data-widget="{html.escape(str(widget["id"]), quote=True)}" style="{style}">{heading}{body}</section>')
    background = _css(canvas.get("backgroundColor"), "#f4f7fb")
    title = html.escape(report["name"])
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\';img-src data:;style-src \'unsafe-inline\'"><title>' + title + '</title><style>html,body{margin:0;font-family:"Microsoft YaHei",Arial,sans-serif}main{position:relative;overflow:hidden}section{position:absolute;box-sizing:border-box;overflow:hidden}section>svg,section>img{display:block;width:100%;height:100%}h2{position:absolute;margin:8px;font-size:16px;z-index:2}.text,.metric{display:flex;flex-direction:column;justify-content:center;align-items:center;width:100%;height:100%;line-height:1.15;overflow-wrap:anywhere}.metric small{font-size:.45em;margin-left:.3em}table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:inherit}th,td{padding:.5em;text-align:left;overflow:hidden;text-overflow:ellipsis;border-bottom:1px solid #ffffff20}th{font-weight:600}figure{margin:0;flex:1;min-width:0}figure img{width:100%;height:85%;object-fit:contain}figcaption{text-align:center}@media print{body{-webkit-print-color-adjust:exact;print-color-adjust:exact}}' + material_styles() + '</style><main aria-label="' + html.escape(report["name"], quote=True) + f'" style="width:{float(width)}px;height:{float(height)}px;background:{background}">' + ''.join(widgets) + '</main></html>'
