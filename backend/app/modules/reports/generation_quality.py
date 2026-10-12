"""Presentation quality for newly generated free layouts; never rewrites SQL."""
from __future__ import annotations

from copy import deepcopy
import math
import re

MECHANICAL_SUFFIX_PATTERN = re.compile(r"[\s·_—\-]*(?:指标卡|核心主视觉|主视觉|数据组件|组件\d+|数据说明|大屏组件|（(?:图表|组件)）|\((?:图表|组件)\))$")
MECHANICAL_PREFIX_PATTERN = re.compile(r"^(?:核心主视觉|主视觉|数据组件|组件\d+|大屏组件)[\s·_—\-]*")
MECHANICAL_INNER_PATTERN = re.compile(r"[（\(](?:图表|组件|数据组件)[）\)]")


def clean_display_title(val: str) -> str:
    """清掉模型生成的机械占位后缀/前缀（如"指标卡""数据组件（图表）"）。

    优先返回清理结果，清成空串时回退原文——宁可留一个不完美的标题，也不返回空标题。
    """

    raw = str(val or "").strip()
    if not raw:
        return ""
    cleaned = MECHANICAL_INNER_PATTERN.sub("", raw).strip()
    cleaned = MECHANICAL_SUFFIX_PATTERN.sub("", cleaned).strip()
    cleaned = MECHANICAL_PREFIX_PATTERN.sub("", cleaned).strip()
    return cleaned or raw


def extract_requirement_title(text: str) -> str:
    """从需求描述里抽出"xx大屏/看板/报表/驾驶舱"作为标题；抽不到返回空串。

    只做确定性抽取，不做语义生成，因此抽不出时由调用方决定兜底标题。
    """

    raw = str(text or "").strip()
    if not raw:
        return ""
    m = re.search(r"(?:生成|创建|制作|构建)?([^\s，。,；;]+?(?:大屏|看板|报表|驾驶舱|大盘))", raw)
    if m:
        val = m.group(1).strip()
        val = re.sub(r"^(?:生成|创建|制作|构建)", "", val).strip()
        if val:
            return val
    return ""


METRICS = {"JStatsSummary", "JCountTo", "JNumber"}
TABLES = {"JScrollBoard", "JTable", "JSqlSheet", "JRankingBoard"}
DECORATIONS = {"JDragBorder", "JDragDecoration", "JCurrentTime", "JImage", "JDragImg",
               "JAmbientGlow", "JDataConstellation", "JFlowRibbon", "JSignalRail"}

QUALITY_GUIDANCE = (
    "这是企业用户要直接使用的完整报表。先确定每个板块的业务问题，再选择真实字段支持的概览、变化、构成、异常或明细。"
    "宽泛的综合大屏不能只放一张总额卡和一张无关图；各板块应提供可理解的分析，"
    "有金额和分类时可展示金额汇总与分类构成，有设备实体与状态时可展示设备总览与状态分布。"
    "没有字段支撑的趋势、比率或异常不可编造。明确要求的组件数量和指标优先，不为凑版面添加重复指标。"
    "趋势用时间排序的折线或柱形，分类对比用柱形，构成使用少量分类的饼图，单值用数字卡，记录用表格。"
    "在 JSON 的 designPlan 中简述分区和每个图表解决的业务问题；widgets 保留既有输出契约。"
    "统一网格、边距与字号，充分使用整屏空间，禁止组件重叠、出界、大块无意义留白。"
    "图表标题、维度和指标别名优先使用中文，单位必须来自用户或实际字段口径。"
    "金额未经换算不能标万元，空结果不能推导业务正常，用户指定时间筛选不能为了出数擅自修改。"
    "JStatsSummary 支持 config.dataMapping.valueField 和 option.card.valueFontSize/titleFontSize，"
    "单位用 widget.unit；JText 正文使用 option.body.text；表格使用 option.header、rowNum、headerFontColor、fontColor。"
    "表格的每个表头必须绑定实际查询列：option.header=[{key:'实际SQL别名',label:'中文显示名称'}]。"
    "不要使用组件不支持的任意样式属性。"
)


def _rect(widget: dict) -> tuple[float, float, float, float]:
    """取组件的 (x, y, w, h)；缺字段按 0 处理，非数值会在校验处被判为越界。"""

    return tuple(float(widget.get(key) or 0) for key in ("x", "y", "w", "h"))


def layout_findings(widgets: list[dict], width: int, height: int) -> list[str]:
    """检查自由布局的几何问题，返回去重后的标记集（可用作硬性拒绝依据）。

    判定项：``out_of_bounds``（越界/尺寸非正/非有限值）、``overlap``（重叠面积超过 3px 容差）、
    ``underused_canvas``（组件总面积不足 46% 或底部未达 74% 高度）。装饰件不参与判定。
    这些只是几何检查，**不校验指标口径**——口径由语义复核负责。
    """

    content = [w for w in widgets if w.get("component") not in DECORATIONS]
    findings = []
    for index, widget in enumerate(content):
        x, y, w, h = _rect(widget)
        if not all(math.isfinite(n) for n in (x, y, w, h)) or min(w, h) <= 0 or min(x, y) < 0 or x + w > width + 1 or y + h > height + 1:
            findings.append("out_of_bounds")
        for other in content[index + 1:]:
            ox, oy, ow, oh = _rect(other)
            if min(x + w, ox + ow) - max(x, ox) > 3 and min(y + h, oy + oh) - max(y, oy) > 3:
                findings.append("overlap")
    if len(content) >= 2:
        area = sum(max(0, _rect(w)[2]) * max(0, _rect(w)[3]) for w in content)
        bottom = max((_rect(w)[1] + _rect(w)[3] for w in content), default=0)
        if area < width * height * .46 or bottom < height * .74:
            findings.append("underused_canvas")
    return list(dict.fromkeys(findings))


def _arrange(widgets: list[dict], width: int, height: int) -> None:
    """就地重排自由布局：页头条 → 指标卡栅格（最多 4 列）→ 内容面板（2–3 列）。

    只改 x/y/w/h，不改组件集合、组件类型、查询或指标含义，因此重排不会改变产物语义。
    行高由剩余空间反算，保证组件落在画布内且不留大片空白。
    """

    margin = max(16, round(min(width, height) * .032))
    gap = max(12, round(min(width, height) * .022))
    ordered = sorted([w for w in widgets if w.get("component") not in DECORATIONS],
                     key=lambda w: (float(w.get("y") or 0), float(w.get("x") or 0)))
    headers = [w for w in ordered if w.get("component") == "JText" and float(w.get("y") or 0) < 100 and float(w.get("h") or 0) <= 110]
    metrics = [w for w in ordered if w.get("component") in METRICS]
    panels = [w for w in ordered if w not in headers and w not in metrics]
    top = margin
    for header in headers:
        header.update(x=margin, y=top, w=width - margin * 2, h=64)
        top += 64 + gap
    columns = min(4, len(metrics)) or 1
    metric_rows = math.ceil(len(metrics) / columns)
    panel_columns = min(2 if len(panels) <= 6 else 3, len(panels)) or 1
    panel_rows = math.ceil(len(panels) / panel_columns)
    available = height - top - margin
    metric_height = min(210, (available - gap * max(0, metric_rows + panel_rows - 1)) / max(1, metric_rows + panel_rows * 1.6)) if panels else (available - gap * max(0, metric_rows - 1)) / max(1, metric_rows)
    for row in range(metric_rows):
        items = metrics[row * columns:(row + 1) * columns]
        cell_width = (width - margin * 2 - gap * (len(items) - 1)) / len(items)
        for col, widget in enumerate(items):
            widget.update(x=round(margin + col * (cell_width + gap)), y=round(top), w=round(cell_width), h=round(metric_height))
        top += metric_height + gap
    panel_height = (height - margin - top - gap * max(0, panel_rows - 1)) / max(1, panel_rows)
    for row in range(panel_rows):
        items = panels[row * panel_columns:(row + 1) * panel_columns]
        cell_width = (width - margin * 2 - gap * (len(items) - 1)) / len(items)
        for col, widget in enumerate(items):
            widget.update(x=round(margin + col * (cell_width + gap)), y=round(top), w=round(cell_width), h=round(panel_height))
        top += panel_height + gap


def prepare_presentation(canvas: dict, widgets: list[dict], *, width: int, height: int) -> dict:
    """Normalize supported display properties and repair defective free geometry.

    Called only for free screen generation, never saved manual/template layouts.
    Keeps the exact component count, IDs, queries, filters and metric meaning.

    适用范围严格限制在**无模板自由生成**：模板/手动布局有用户确认过的几何，绝不能被自动
    重排。发现几何问题（重叠/越界/留白）时整体重排一次，否则只补默认视觉属性（配色、字体、
    面板样式等）。返回值供结果里 ``presentationQuality`` 字段使用（含 ``remainingFindings``），
    调用方据此决定是否拒绝——本函数不抛异常，也不改任何 SQL、查询结果或指标口径。
    """
    findings = layout_findings(widgets, width, height)
    if findings:
        _arrange(widgets, width, height)
    canvas.setdefault("visualMode", "ai")
    canvas.setdefault("backgroundColor", "#0b1220")
    canvas.setdefault("backgroundLayers", "none")
    light = str(canvas["backgroundColor"]).lower() in {"white", "#fff", "#ffffff", "#f5f5f5", "#f8fafc"}
    text, muted, panel, border = ("#172b45", "#52657d", "#ffffff", "#d9e2ee") if light else ("#e7eef8", "#9eb0c8", "#131e30", "#293c55")
    for widget in widgets:
        component = widget.get("component")
        if component in DECORATIONS:
            continue
        config = widget.setdefault("config", {})
        option = config.setdefault("option", {})
        style = widget.setdefault("style", {})
        if widget.get("componentName"):
            widget["componentName"] = clean_display_title(widget["componentName"])
        if widget.get("title"):
            widget["title"] = clean_display_title(widget["title"])
        if component == "JText":
            body = option.setdefault("body", {})
            body.setdefault("fontSize", 30)
            body.setdefault("color", text)
            body.setdefault("textAlign", "left")
            continue
        for key, value in {"background": panel, "borderColor": border, "borderWidth": 1, "radius": 12,
                           "padding": "22px", "fontFamily": "'Microsoft YaHei UI', sans-serif"}.items():
            style.setdefault(key, value)
        title = option.get("title")
        title = {"text": title} if isinstance(title, str) else deepcopy(title) if isinstance(title, dict) else {}
        title_text = clean_display_title(title.get("text") or widget.get("componentName") or "")
        title["text"] = title_text
        title.setdefault("show", True)
        title.setdefault("left", 0)
        title.setdefault("top", 0)
        title_style = title.setdefault("textStyle", {})
        title_style.setdefault("color", text)
        title_style.setdefault("fontSize", 20)
        title_style.setdefault("fontFamily", "Bahnschrift, 'Microsoft YaHei UI', sans-serif")
        title_style.setdefault("fontWeight", 600)
        option["title"] = title
        if component in METRICS:
            if style.get("padding") == "22px":
                style["padding"] = "16px"
            card = option.setdefault("card", {})
            card.setdefault("valueFontSize", option.get("valueFontSize", option.get("fontSize", 54)))
            card.setdefault("titleFontSize", option.get("titleFontSize", 18))
            card.setdefault("valueFontFamily", "Bahnschrift, 'Microsoft YaHei UI', sans-serif")
            card.setdefault("valueFontWeight", 700)
            card.setdefault("titleColor", muted)
            style.setdefault("valueColor", option.get("valueColor", option.get("color", "#00F2FE" if not light else "#1276e8")))
            style.setdefault("boxShadow", "0 8px 24px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.08)")
            style.setdefault("backdropFilter", "blur(12px)")
            if not widget.get("unit") and option.get("unit"):
                widget["unit"] = option["unit"]
        elif component in TABLES:
            option.setdefault("rowNum", max(4, min(12, round((float(widget.get("h") or 350) - 110) / 46))))
            option.setdefault("headerHeight", 46)
            option.setdefault("headerFontSize", 18)
            option.setdefault("bodyFontSize", 16)
            option.setdefault("headerFontColor", option.get("headerColor", text))
            option.setdefault("fontColor", option.get("bodyColor", text))
            option.setdefault("headerBGC", "rgba(39, 125, 255, 0.18)" if not light else border)
            option.setdefault("oddRowBGC", "transparent")
            option.setdefault("evenRowBGC", "rgba(39, 125, 255, 0.05)" if not light else "rgba(128,150,180,.07)")
            style.setdefault("boxShadow", "0 8px 24px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.06)")
            style.setdefault("backdropFilter", "blur(12px)")
        else:
            option.setdefault("color", ["#00F2FE", "#38EF7D", "#FFB647", "#A879FF", "#FF5E7E", "#277DFF", "#58D7FF"] if not light else ["#1276e8", "#12b886", "#f59f00", "#7950f2", "#fa5252"])
            grid = option.setdefault("grid", {})
            grid.setdefault("top", 64)
            grid.setdefault("left", 16)
            grid.setdefault("right", 22)
            grid.setdefault("bottom", 20)
            grid.setdefault("containLabel", True)
            option.setdefault("tooltip", {
                "show": True,
                "trigger": "axis" if component in {"JBar", "JLine", "JArea", "JHistogram"} else "item",
                "backgroundColor": "rgba(8, 24, 48, 0.92)" if not light else "rgba(255, 255, 255, 0.96)",
                "borderColor": "#23BED7" if not light else "#1276e8",
                "borderWidth": 1,
                "textStyle": {"color": "#EAF8FF" if not light else "#172b45", "fontSize": 13},
            })
            for axis_name in ("xAxis", "yAxis"):
                axis = option.get(axis_name)
                if not isinstance(axis, dict):
                    continue
                axis.setdefault("axisLabel", {}).setdefault("color", muted)
                axis["axisLabel"].setdefault("fontSize", 15)
                axis.setdefault("splitLine", {}).setdefault("lineStyle", {}).update(
                    color="rgba(39, 125, 255, 0.12)" if not light else border,
                    type="dashed"
                )
                axis.setdefault("axisLine", {}).setdefault("lineStyle", {}).update(
                    color="rgba(39, 125, 255, 0.28)" if not light else border
                )
                axis.setdefault("nameTextStyle", {}).setdefault("color", muted)
            for s in option.get("series") or []:
                if not isinstance(s, dict):
                    continue
                stype = str(s.get("type") or "").lower()
                if stype == "bar":
                    is_horizontal = "Horizontal" in component or option.get("yAxis", {}).get("type") == "category"
                    radius = [0, 4, 4, 0] if is_horizontal else [4, 4, 0, 0]
                    s.setdefault("itemStyle", {}).setdefault("borderRadius", radius)
                    s.setdefault("showBackground", True)
                    s.setdefault("backgroundStyle", {"color": "rgba(255, 255, 255, 0.03)", "borderRadius": radius})
                    s.setdefault("barMaxWidth", 36)
                elif stype == "line":
                    s.setdefault("smooth", 0.35)
                    s.setdefault("showSymbol", True)
                    s.setdefault("symbolSize", 6)
                    s.setdefault("lineStyle", {}).setdefault("width", 3)
                    s.setdefault("areaStyle", {
                        "color": {
                            "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                            "colorStops": [
                                {"offset": 0, "color": "rgba(0, 242, 254, 0.35)"},
                                {"offset": 1, "color": "rgba(0, 242, 254, 0.01)"}
                            ]
                        }
                    })
                elif stype == "pie":
                    s.setdefault("radius", ["46%", "68%"])
                    s.setdefault("center", ["36%", "52%"])
                    s.setdefault("avoidLabelOverlap", True)
                    s.setdefault("itemStyle", {}).setdefault("borderRadius", 4)
                    s.setdefault("itemStyle", {}).setdefault("borderColor", "#061531")
                    s.setdefault("itemStyle", {}).setdefault("borderWidth", 2)
                    s.setdefault("padAngle", 2)
                    s.setdefault("label", {"show": False})
                    s.setdefault("labelLine", {"show": False})
                    option.setdefault("legend", {
                        "show": True, "type": "scroll", "orient": "vertical",
                        "right": 16, "top": "middle",
                        "textStyle": {"color": text, "fontSize": 13},
                    })
            style.setdefault("boxShadow", "0 8px 24px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.06)")
            style.setdefault("backdropFilter", "blur(12px)")
    return {"layoutAdjusted": bool(findings), "initialFindings": findings,
            "remainingFindings": layout_findings(widgets, width, height), "version": "business-presentation-v1"}

