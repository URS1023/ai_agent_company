"""真实输出分组、堆叠、百分比、组合、负值、平滑和阶梯图的离线SVG。"""
from decimal import Decimal
import html

from .chart_data import series_data


def _path(points, smooth=False, step=False):
    if not points:
        return ""
    commands = [f"M{points[0][0]:.3f},{points[0][1]:.3f}"]
    for index in range(1, len(points)):
        x0, y0 = points[index - 1]
        x, y = points[index]
        if step == "start":
            commands.append(f"V{y:.3f} H{x:.3f}")
        elif step == "middle":
            middle = (x0 + x) / 2
            commands.append(f"H{middle:.3f} V{y:.3f} H{x:.3f}")
        elif step:
            commands.append(f"H{x:.3f} V{y:.3f}")
        elif smooth:
            middle = (x0 + x) / 2
            commands.append(f"C{middle:.3f},{y0:.3f} {middle:.3f},{y:.3f} {x:.3f},{y:.3f}")
        else:
            commands.append(f"L{x:.3f},{y:.3f}")
    return " ".join(commands)


def render_cartesian(widget, rows, color, palette):
    """用同一系列模型计算各轴范围和图元；正负堆叠分开累加，断点分段输出。"""
    data = series_data(widget, rows)
    if data["status"] != "ready":
        return '<div class="text" data-chart-state="' + data["status"] + '">' + html.escape(data.get("message", "暂无数据")) + '</div>'
    series, labels = data["series"], data["categories"]
    domains = {}
    for axis in (0, 1):
        members = [item for item in series if item["axis"] == axis]
        values = [point for item in members for point in item["values"] if point is not None]
        if data["stacked"]:
            values = [sum((item["values"][i] for item in members if item["values"][i] is not None and item["values"][i] > 0), Decimal(0)) for i in range(len(labels))] + [sum((item["values"][i] for item in members if item["values"][i] is not None and item["values"][i] < 0), Decimal(0)) for i in range(len(labels))]
        low, high = min([Decimal(0)] + values), max([Decimal(1)] + values)
        if data["percent"]:
            low, high = Decimal(0), Decimal(100)
        domains[axis] = (low, high, high - low)
    def point(value, axis):
        low, _, span = domains[axis]
        ratio = float((value - low) / span)
        return 215 - ratio * 170 if not data["horizontal"] else 90 + ratio * 470
    shapes = []
    zero = point(Decimal(0), 0)
    if data["horizontal"]:
        shapes.append('<line x1="45" y1="215" x2="605" y2="215" stroke="currentColor" opacity=".15"/>')
        shapes.append(f'<line data-zero-axis="primary" x1="{zero:.3f}" y1="40" x2="{zero:.3f}" y2="215" stroke="currentColor" opacity=".5"/>')
    else:
        if zero != 215:
            shapes.append('<line x1="45" y1="215" x2="605" y2="215" stroke="currentColor" opacity=".15"/>')
        shapes.append(f'<line data-zero-axis="primary" x1="45" y1="{zero:.3f}" x2="605" y2="{zero:.3f}" stroke="currentColor" opacity=".5"/>')
        if domains[0][0] < 0 < domains[0][1]:
            shapes.append(f'<text x="5" y="{zero:.3f}" font-size="11">0</text>')
    count = len(labels)
    bars = [item for item in series if item["kind"] == "bar"]
    bar_count = 1 if data["stacked"] else max(1, len(bars))
    category_width = 540 / count
    bar_width = category_width * .72 / bar_count
    positives, negatives = [Decimal(0)] * count, [Decimal(0)] * count
    bar_index = 0
    for index, item in enumerate(series):
        shade = palette[index % len(palette)]
        shapes.append(f'<g data-series="{html.escape(item["field"], quote=True)}" data-axis="{item["axis"]}" data-kind="{item["kind"]}">')
        groups, current = [], []
        for i, value in enumerate(item["values"]):
            if value is None:
                if current:
                    groups.append(current)
                    current = []
                continue
            caption = html.escape(f'{labels[i]} · {item["name"]}: {item["raw_values"][i]}')
            normalized = f' data-value="{value}" data-raw-value="{html.escape(str(item["raw_values"][i]), quote=True)}"'
            if item["kind"] == "bar":
                start = Decimal(0)
                if data["stacked"]:
                    starts = positives if value >= 0 else negatives
                    start = starts[i]
                    starts[i] += value
                first, last = point(start, item["axis"]), point(start + value, item["axis"])
                if data["horizontal"]:
                    y = 45 + i * 165 / count + bar_index * 165 / count * .72 / bar_count
                    shape = f'<rect x="{min(first, last):.3f}" y="{y:.3f}" width="{abs(last-first):.3f}" height="{max(.1,165/count*.72/bar_count):.3f}"'
                else:
                    x = 55 + i * category_width + (0 if data["stacked"] else bar_index * bar_width)
                    shape = f'<rect x="{x:.3f}" y="{min(first,last):.3f}" width="{max(.1,bar_width):.3f}" height="{abs(last-first):.3f}"'
                shapes.append(shape + normalized + f' fill="{shade}"><title>{caption}</title></rect>')
            else:
                x = 55 + (i + .36) * category_width if bars else 55 + i * 535 / max(1, count - 1)
                y = point(value, item["axis"])
                current.append((x, y))
                shapes.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3" fill="{shade}"{normalized}><title>{caption}</title></circle>')
        if current:
            groups.append(current)
        if item["kind"] == "line":
            for points in groups:
                if widget.get("component") == "JArea":
                    zero = point(Decimal(0), item["axis"])
                    path = _path(points, item["smooth"], item["step"]) + f" L{points[-1][0]:.3f},{zero:.3f} L{points[0][0]:.3f},{zero:.3f} Z"
                    shapes.append(f'<path d="{path}" fill="{shade}" fill-opacity=".22"/>')
                if not item["smooth"] and not item["step"]:
                    shapes.append('<polyline points="' + ' '.join(f'{x:.3f},{y:.3f}' for x,y in points) + f'" fill="none" stroke="{shade}" stroke-width="3"/>')
                else:
                    shapes.append(f'<path d="{_path(points,item["smooth"],item["step"])}" fill="none" stroke="{shade}" stroke-width="3"/>')
        if item["kind"] == "bar":
            bar_index += 1
        shapes.append('</g>')
        if len(series) > 1:
            shapes.append(f'<text x="{55+index*110}" y="22" fill="{shade}">{html.escape(item["name"])}</text>')
    for axis, anchor in ((0, "start"), (1, "end")):
        if axis == 1 and not any(item["axis"] == 1 for item in series):
            continue
        low, high, _ = domains[axis]
        x = 5 if axis == 0 else 635
        for value in (low, high):
            suffix = "%" if data["percent"] else ""
            if data["horizontal"]:
                shapes.append(f'<text x="{point(value,axis):.3f}" y="235" text-anchor="middle" font-size="11">{value}{suffix}</text>')
            else:
                shapes.append(f'<text x="{x}" y="{point(value,axis):.3f}" text-anchor="{anchor}" font-size="11">{value}{suffix}</text>')
    if count <= 30:
        for index, label in enumerate(labels):
            x = 55 + (index + .36) * category_width if bars else 55 + index * 535 / max(1,count-1)
            if data["horizontal"]:
                shapes.append(f'<text x="5" y="{55+index*165/count:.3f}" font-size="11">{html.escape(label)}</text>')
            else:
                shapes.append(f'<text x="{x:.3f}" y="242" text-anchor="middle" font-size="11">{html.escape(label)}</text>')
    return '<svg data-chart="' + html.escape(widget["component"], quote=True) + '" viewBox="0 0 640 260" preserveAspectRatio="none" style="color:' + color + '"><g fill="currentColor" font-size="13">' + ''.join(shapes) + '</g></svg>'
