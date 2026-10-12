"""Adapt an existing template's presentation without inheriting its business data."""
from __future__ import annotations

from copy import deepcopy
import math
import re

from app.modules.reports.generation_requirements import business_scopes, table_scope_score, template_business_scopes
from app.modules.reports.generation_template_layout import arrange_template_regions

SCOPE_LABELS = {"finance": "财务", "equipment": "设备", "quality": "质检", "sales": "销售",
                "purchase": "采购", "energy": "能耗", "inventory": "仓储"}
CANVAS_STYLE_KEYS = {"backgroundColor", "backgroundImage", "theme", "themeId", "visualBlueprint"}
OPTION_STYLE_KEYS = {"color", "valueColor", "headerBGC", "oddRowBGC", "evenRowBGC", "fontColor",
                     "headerFontColor", "headerFontSize", "bodyFontSize", "headerHeight"}
AMBIENT_COMPONENTS = {"JAmbientGlow", "JDataConstellation", "JFlowRibbon", "JSignalRail"}


def needs_business_adaptation(requirement: str, template: dict) -> bool:
    """判断模板是否"需要业务改写"：需求板块与模板板块有交集但未被模板完全覆盖。

    即模板部分兼容（如需求要财务+设备，模板只有财务）时应参考其视觉但重排业务内容；
    完全覆盖或完全无关时返回 False。
    """

    requested, existing = business_scopes(requirement), template_business_scopes(template)
    return bool(requested and existing and requested & existing and not requested <= existing)


def choose_reference(requirement: str, templates: list[dict]) -> dict | None:
    """挑一套"仅作视觉参考"的模板：按与需求板块的重合度取最高分。

    只考虑有组件且至少命中一个板块的候选，**命中 0 时返回 None**（不返回第一套），
    因此"没有合适参考就让模型自由设计"是显式行为。
    """

    requested = business_scopes(requirement)
    ranked = [(len(requested & template_business_scopes(t)), t) for t in templates]
    ranked = [(score, t) for score, t in ranked if score and t.get("content", {}).get("widgets")]
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def required_sections(requirement: str, schemas: list[dict]) -> list[dict]:
    """把需求涉及的每个业务板块展开成"必须覆盖的小节"，附该板块可用的真实表。

    每项包含 ``scope``/``name``/``availableTables``（含数据源 ID 与表名）。这份清单会同时
    交给模型（要求逐板块产出组件）与 ``missing_sections``（检查是否真的绑定了数据），
    因此它是"板块覆盖"的唯一口径。
    """

    sections = []
    for scope in sorted(business_scopes(requirement)):
        tables = []
        for source in schemas:
            matches = sorted(source.get("tables") or [], key=lambda t: table_scope_score(t, scope), reverse=True)
            tables.extend({"datasourceId": source["id"], "tableName": t["tableName"], "tableComment": t.get("tableComment", "")}
                          for t in matches if table_scope_score(t, scope) > 0)
        sections.append({"scope": scope, "name": SCOPE_LABELS[scope], "availableTables": tables})
    return sections


def missing_sections(widgets: list[dict], sections: list[dict]) -> list[dict]:
    """A heading alone cannot stand in for available business data.

    返回"有可用表但没被任何组件真实查询到"的板块。判定方式是逐组件读 SQL 并做**词边界**表名
    匹配（同时要求数据源一致），因此只在标题里写板块名（JText 文案）不算覆盖。
    无可用表的板块直接跳过——那种情况应由语义复核判断"缺数据说明"是否成立。
    """
    missing = []
    for section in sections:
        if not section["availableTables"]:
            continue  # Missing-field explanations are judged by semantic review.
        covered = False
        for widget in widgets:
            query = widget.get("query") or {}
            sql = str(query.get("sql") or "")
            for table in section["availableTables"]:
                if query.get("datasourceId") == table["datasourceId"] and re.search(
                    r"(?<![\w])" + re.escape(table["tableName"]) + r"(?![\w])", sql, re.I
                ):
                    covered = True
        if not covered:
            missing.append(section)
    return missing


def reference_context(template: dict | None) -> dict | None:
    """给模型的"仅视觉参考"上下文：画布样式、组件几何与视觉角色，**不含原指标与数值**。

    ``policy`` 明确要求按 requiredSections 适配业务指标、不得继承模板示例数值与原指标，
    因此参考模板只影响构图与配色，不影响产物口径。无模板时返回 None。
    """

    if not template:
        return None
    content = template["content"]
    return {"id": template["id"], "name": template["name"],
            "canvas": {k: deepcopy(v) for k, v in content["canvas"].items() if k in CANVAS_STYLE_KEYS | {"width", "height"}},
            "layout": [{k: deepcopy(w[k]) for k in ("id", "component", "x", "y", "w", "h", "visualRole", "style") if k in w}
                       for w in content["widgets"]],
            "policy": "参考本模板分区与配色，采用适量科技装饰：细线边角、标题装饰与轻微光效，禁止嵌套大边框。按 requiredSections 适配业务指标，模板示例数值、原指标和单位不能继承。"}


def _arrange_reference(widgets: list[dict], width: int, height: int) -> None:
    """A shared KPI band and aligned analysis panels, without nested neon frames."""
    margin, gap = round(width * .025), 24
    metrics = [w for w in widgets if w.get("component") in {"JStatsSummary", "JCountTo", "JNumber"}]
    panels = [w for w in widgets if w.get("query", {}).get("sql") and w not in metrics]
    headings = [w for w in widgets if w.get("component") == "JText"]
    top = 112
    for index, heading in enumerate(headings):
        body = heading.setdefault("config", {}).setdefault("option", {}).setdefault("body", {})
        if index == 0:
            heading.update(x=round(width * .2), y=24, w=round(width * .6), h=64, referenceStyle="tech-title")
            body.update(fontSize=40, textAlign="center", fontWeight=600, letterSpacing=3)
        else:
            # Explanations keep their own visible rows; never discard a missing-data notice.
            heading.update(x=margin, y=top, w=width - margin * 2, h=40)
            body.update(fontSize=20, textAlign="left", fontWeight=500, letterSpacing=0)
            top += 48
    for clock in [w for w in widgets if w.get("component") == "JCurrentTime"]:
        clock.update(x=width - margin - 300, y=48, w=300, h=28)
    columns = min(4, len(metrics)) or 1
    metric_height = min(168, height * .16)
    for row in range(math.ceil(len(metrics) / columns)):
        group = metrics[row * columns:(row + 1) * columns]
        cell_width = (width - margin * 2 - gap * (len(group) - 1)) / len(group)
        for column, widget in enumerate(group):
            widget.update(x=round(margin + column * (cell_width + gap)), y=round(top), w=round(cell_width), h=round(metric_height))
        top += metric_height + gap
    panel_columns = min(2, len(panels)) or 1
    panel_rows = math.ceil(len(panels) / panel_columns)
    panel_height = (height - margin - top - gap * max(0, panel_rows - 1)) / max(1, panel_rows)
    # Keep corresponding finance/equipment visualizations side by side.
    scopes = list(SCOPE_LABELS)
    panels.sort(key=lambda w: (w.get("component") in {"JScrollBoard", "JTable", "JSqlSheet"},
                              scopes.index(w["businessScope"]) if w.get("businessScope") in scopes else len(scopes)))
    for row in range(panel_rows):
        group = panels[row * panel_columns:(row + 1) * panel_columns]
        cell_width = (width - margin * 2 - gap * (len(group) - 1)) / len(group)
        for column, widget in enumerate(group):
            widget.update(x=round(margin + column * (cell_width + gap)), y=round(top), w=round(cell_width), h=round(panel_height))
        top += panel_height + gap


def apply_reference(canvas: dict, widgets: list[dict], template: dict, *, adapt_layout: bool) -> dict:
    """Copy approved presentation properties only, keeping validated bindings intact."""
    source_canvas, source_widgets = template["content"]["canvas"], template["content"]["widgets"]
    for key in CANVAS_STYLE_KEYS:
        if key in source_canvas:
            canvas[key] = deepcopy(source_canvas[key])
    canvas.update(styleLocked=False, visualMode="ai", backgroundLayers="none")
    reference = {"id": template["id"], "name": template["name"], "version": template.get("version", 1), "adapted": True}
    canvas["templateReference"] = reference
    theme = source_canvas.get("theme") or {}
    if adapt_layout:
        width, height = int(canvas.get("width") or 1920), int(canvas.get("height") or 1080)
        if not arrange_template_regions(widgets, template, width, height):
            widgets[:] = [w for w in widgets if w.get("component") not in {"JDragBorder", "JDragDecoration"} and not (
                w.get("component") == "JText" and str(w.get("componentName") or "").endswith("板块标题"))]
            _arrange_reference(widgets, width, height)
        else:
            reference["layoutPolicy"] = "template-regions"
    for widget in widgets:
        component = widget.get("component")
        base = next((w for w in source_widgets if w.get("id") == widget.get("templateSlotId")), None)
        if base is None:
            base = next((w for w in source_widgets if w.get("component") == component), None)
        option = widget.setdefault("config", {}).setdefault("option", {})
        style = widget.setdefault("style", {})
        if theme:
            widget["theme"] = deepcopy(theme)
        if component in AMBIENT_COMPONENTS:
            # Ambient layers must never become opaque data panels over the charts.
            widget.pop("referenceStyle", None)
            style.update(background="transparent", borderWidth=0, padding="0px")
            style.pop("glow", None)
            continue
        if base:
            style.update(deepcopy(base.get("style") or {}))
            base_option = base.get("config", {}).get("option") or {}
            if isinstance(base_option, dict):
                option.update({k: deepcopy(v) for k, v in base_option.items() if k in OPTION_STYLE_KEYS})
                if "valueColor" in base_option:
                    style["valueColor"] = base_option["valueColor"]
            if component == "JText":
                source_body = base_option.get("body") or {}
                body = option.setdefault("body", {})
                body.update({k: deepcopy(source_body[k]) for k in ("color", "fontWeight", "letterSpacing") if k in source_body})
        if component not in {"JText", "JCurrentTime", "JDragBorder", "JDragDecoration"}:
            from_template_slot = bool(widget.get("templateSlotId"))
            widget["referenceStyle"] = "template-slot" if from_template_slot else "tech-panel"
            style.update(background=theme.get("panel", style.get("background", "#092A59E8")),
                         borderColor=theme.get("grid", style.get("borderColor", "#277DFF55")))
            style.update(borderWidth=1, radius=8, padding="24px")
            if from_template_slot:
                style.update(background="transparent", borderWidth=0, radius=0, padding="8px")
            style.pop("glow", None)
            if component in {"JStatsSummary", "JCountTo", "JNumber"}:
                option.setdefault("card", {}).update(valueFontSize=64, titleFontSize=20)
                style.update(valueColor=theme.get("text", "#EAF8FF"), padding="20px")
                if from_template_slot:
                    option["card"]["valueFontSize"] = min(64, max(32, round(float(widget["w"]) * .105)))
                    style["padding"] = "8px"
        else:
            style.pop("glow", None)
        if component not in {"JStatsSummary", "JCountTo", "JNumber", "JText", "JCurrentTime", "JDragBorder"} and theme.get("palette"):
            option["color"] = deepcopy(theme["palette"])
        if isinstance(option.get("title"), dict):
            raw_title = str(option["title"].get("text") or widget.get("componentName") or "")
            clean_t = re.sub(r"[\s·_—\-]*(?:指标卡|核心主视觉|主视觉|数据组件|组件\d+|数据说明|大屏组件)$", "", raw_title).strip() or raw_title
            option["title"]["text"] = clean_t
            if widget.get("componentName"):
                widget["componentName"] = clean_t
            option["title"].setdefault("textStyle", {})["color"] = theme.get("text", "#EAF8FF")
        if component in {"JPie", "JRing", "JRose", "JDoughnut"}:
            option["legend"] = {"show": True, "type": "scroll", "orient": "vertical", "left": "55%",
                                "right": 12, "top": 48, "bottom": 8,
                                "textStyle": {"color": theme.get("text", "#EAF8FF"), "fontSize": 15,
                                              "width": max(100, round(float(widget.get("w") or 600) * .34)), "overflow": "truncate"},
                                "tooltip": {"show": True}}
            for series in option.get("series") or []:
                if isinstance(series, dict) and series.get("type") == "pie":
                    series.update(center=["27%", "60%"], radius=["36%", "64%"],
                                  label={"show": False}, labelLine={"show": False},
                                  itemStyle={"borderRadius": 4, "borderColor": theme.get("background", "#031531"), "borderWidth": 2},
                                  padAngle=2)
        elif component in {"JBar", "JLine", "JArea", "JHorizontalBar"}:
            series = option.get("series") or []
            if len(series) == 1:
                option["legend"] = {"show": False}
            for s in series:
                if not isinstance(s, dict):
                    continue
                stype = str(s.get("type") or "").lower()
                if stype == "bar" or "Bar" in component:
                    is_h = "Horizontal" in component
                    radius = [0, 4, 4, 0] if is_h else [4, 4, 0, 0]
                    s.setdefault("itemStyle", {}).setdefault("borderRadius", radius)
                    s.setdefault("showBackground", True)
                    s.setdefault("backgroundStyle", {"color": "rgba(255, 255, 255, 0.03)", "borderRadius": radius})
                    s.setdefault("barMaxWidth", 36)
                elif stype == "line" or "Line" in component or "Area" in component:
                    s.setdefault("smooth", 0.35)
                    s.setdefault("showSymbol", True)
                    s.setdefault("symbolSize", 6)
                    s.setdefault("lineStyle", {}).setdefault("width", 3)
                    s.setdefault("areaStyle", {
                        "color": {
                            "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                            "colorStops": [
                                {"offset": 0, "color": "rgba(39, 125, 255, 0.38)"},
                                {"offset": 1, "color": "rgba(39, 125, 255, 0.01)"}
                            ]
                        }
                    })
            if float(widget.get("h") or 400) < 300:
                for axis_name in ("xAxis", "yAxis"):
                    axis = option.get(axis_name)
                    if isinstance(axis, dict) and axis.get("type") == "value":
                        axis["splitNumber"] = 2
        elif component in {"JScrollBoard", "JTable", "JSqlSheet"}:
            option["rowNum"] = max(1, min(int(option.get("rowNum") or 4), int((float(widget.get("h") or 350) - 150) / 32)))
        # Imported templates occasionally contain nine/eleven-digit hex alpha values.
        for key in ("oddRowBGC", "evenRowBGC"):
            value = option.get(key)
            if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{9,}", value):
                option[key] = value[:9]
    return reference
