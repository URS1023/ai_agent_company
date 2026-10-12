"""Place verified widgets into the actual template regions, preserving its composition."""
from copy import deepcopy

METRICS = {"JStatsSummary", "JNumber", "JCountTo"}
DECORATIONS = {"JText", "JRichText", "JCurrentTime", "JDragBorder", "JDragDecoration", "JImage", "JDragImg",
               "JAmbientGlow", "JDataConstellation", "JFlowRibbon", "JSignalRail"}
AMBIENT = {"JAmbientGlow", "JDataConstellation", "JFlowRibbon", "JSignalRail", "JDragDecoration"}


def arrange_template_regions(widgets: list[dict], template: dict, width: int, height: int) -> bool:
    """把已验证组件按模板各区域的实际几何摆位，返回是否成功套用（False 表示调用方应回退）。

    做法：把模板中非装饰组件视为"槽位"，按其 x/y/w/h 的缩放比逐个分配给数据组件；
    存在 JDragBorder 时优先基于边框区域分配。约束与失败条件：槽位为空、没有数据组件、
    数据组件多于槽位时返回 ``False``（让上层走自由布局或重排，而不是硬塞到模板里），
    因此该函数**不修改模板文件**、只产出坐标。
    """

    content = template.get("content") or {}
    source = content.get("widgets") or []
    slots = [w for w in source if w.get("component") not in DECORATIONS]
    headings = [w for w in widgets if w.get("component") == "JText"]
    data = [w for w in widgets if (w.get("query") or {}).get("sql")]
    # Long missing-data notices need a visible panel, not a discarded heading.
    data += [w for w in headings[1:] if w not in data]
    if not slots or not data or len(data) > len(slots):
        return False
    canvas = content.get("canvas") or {}
    scale_x = width / float(canvas.get("width") or template.get("width") or width)
    scale_y = height / float(canvas.get("height") or template.get("height") or height)

    def geometry(item):
        """按画布尺寸缩放比换算组件坐标（x/w 用横向比例，y/h 用纵向比例）。"""
        return {key: round(float(item.get(key) or 0) * (scale_x if key in {"x", "w"} else scale_y))
                for key in ("x", "y", "w", "h")}

    frames = [w for w in source if w.get("component") == "JDragBorder"]
    assigned, remaining = [], list(slots)
    metrics = [w for w in data if w.get("component") in METRICS]
    panels = [w for w in data if w not in metrics]
    for widget in [*metrics, *panels]:
        is_metric = widget.get("component") in METRICS
        compatible = [s for s in remaining if (s.get("component") in METRICS) == is_metric]
        choices = compatible or remaining
        # The first key metric occupies the central hero number; first key trend occupies hero trend.
        if is_metric:
            hero = next((s for s in choices if s.get("visualRole") == "hero-number"), None)
        else:
            hero = next((s for s in choices if s.get("visualRole") == "hero-trend"), None)
        slot = hero or next((s for s in choices if s.get("component") == widget.get("component")), choices[0])
        remaining.remove(slot)
        frame_id = str(slot.get("id") or "").replace("-widget-", "-frame-")
        frame = next((f for f in frames if f.get("id") == frame_id), None)
        assigned.append((widget, slot, frame))
        widget.update(geometry(slot), templateSlotId=slot.get("id"), referenceStyle="template-slot")
        if slot.get("visualRole") in {"hero-number", "hero-trend"}:
            widget["templateVisualRole"] = slot["visualRole"]

    # Merge an unused adjacent slot into its neighbour. This preserves
    # the source column/grid structure without inserting fake filler metrics or leaving holes.
    for unused in remaining:
        rect = geometry(unused)
        if unused.get("component") in METRICS:
            neighbours = [w for w in metrics if abs(w["y"] - rect["y"]) <= 4 and abs(w["h"] - rect["h"]) <= 4]
            if not neighbours:
                continue
            closest = min(neighbours, key=lambda w: min(abs(w["x"] + w["w"] - rect["x"]), abs(rect["x"] + rect["w"] - w["x"])))
            distance = min(abs(closest["x"] + closest["w"] - rect["x"]), abs(rect["x"] + rect["w"] - closest["x"]))
            if distance <= 120:
                right = max(closest["x"] + closest["w"], rect["x"] + rect["w"])
                closest["x"] = min(closest["x"], rect["x"])
                closest["w"] = right - closest["x"]
        else:
            v_neighbours = [w for w in panels if abs(w["x"] - rect["x"]) <= 4 and abs(w["w"] - rect["w"]) <= 4]
            if v_neighbours:
                closest = min(v_neighbours, key=lambda w: min(abs(w["y"] + w["h"] - rect["y"]), abs(rect["y"] + rect["h"] - w["y"])))
                distance = min(abs(closest["y"] + closest["h"] - rect["y"]), abs(rect["y"] + rect["h"] - closest["y"]))
                if distance <= 80:
                    bottom = max(closest["y"] + closest["h"], rect["y"] + rect["h"])
                    closest["y"] = min(closest["y"], rect["y"])
                    closest["h"] = bottom - closest["y"]
                    continue
            h_neighbours = [w for w in panels if abs(w["y"] - rect["y"]) <= 4 and abs(w["h"] - rect["h"]) <= 4]
            if h_neighbours:
                closest = min(h_neighbours, key=lambda w: min(abs(w["x"] + w["w"] - rect["x"]), abs(rect["x"] + rect["w"] - w["x"])))
                distance = min(abs(closest["x"] + closest["w"] - rect["x"]), abs(rect["x"] + rect["w"] - closest["x"]))
                if distance <= 80:
                    right = max(closest["x"] + closest["w"], rect["x"] + rect["w"])
                    closest["x"] = min(closest["x"], rect["x"])
                    closest["w"] = right - closest["x"]

    decorations = []
    for widget, slot, frame in assigned:
        if not frame:
            continue
        border = {key: deepcopy(frame[key]) for key in ("component", "config", "style", "visualRole") if key in frame}
        inset_x = max(0, round((float(slot.get("x") or 0) - float(frame.get("x") or 0)) * scale_x))
        inset_y = max(0, round((float(slot.get("y") or 0) - float(frame.get("y") or 0)) * scale_y))
        border.update(id="reference-frame-" + str(widget["id"]), componentName=str(widget.get("componentName") or "") + "边框",
                      x=widget["x"] - inset_x, y=widget["y"] - inset_y,
                      w=widget["w"] + inset_x * 2, h=widget["h"] + inset_y * 2)
        # Imported presentation frames never inherit a data-source connection.
        config = border.get("config") or {}
        for key in ("sql", "datasourceId", "queryId", "parameters", "chartData", "chartDataParsed"):
            config.pop(key, None)
        decorations.append(border)
    for item in source:
        if item.get("component") in AMBIENT:
            decoration = {key: deepcopy(item[key]) for key in ("component", "config", "style", "visualRole") if key in item}
            decoration.update(geometry(item), id="reference-" + str(item["id"]))
            for key in ("sql", "datasourceId", "queryId", "parameters", "chartData", "chartDataParsed"):
                (decoration.get("config") or {}).pop(key, None)
            decorations.append(decoration)
    source_title = next((w for w in source if w.get("component") == "JText"), None)
    if headings and source_title:
        headings[0].update(geometry(source_title), referenceStyle="tech-title")
        headings[0].setdefault("config", {}).setdefault("option", {}).setdefault("body", {}).update(
            fontSize=round(16 * min(scale_x, scale_y)), textAlign="center", fontWeight=600)
    source_clock = next((w for w in source if w.get("component") == "JCurrentTime"), None)
    clocks = [w for w in widgets if w.get("component") == "JCurrentTime"]
    if source_clock:
        for clock in clocks:
            clock.update(geometry(source_clock))
    widgets[:] = [*decorations, *headings[:1], *clocks, *data]
    return True
