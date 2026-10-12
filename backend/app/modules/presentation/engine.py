# -*- coding: utf-8 -*-
"""High-quality built-in presentation renderer.

The content model stays semantic. This module owns page-role selection,
pagination, layout, typography, native charts/tables, and deck-wide chrome.
"""
from __future__ import annotations

import base64
import io
import math
import re
import zipfile
from copy import deepcopy
from typing import Iterable


def render_builtin_presentation(spec: dict, template: dict) -> bytes:
    """用内置渲染器把语义 spec 渲染成 .pptx 字节流（不落盘）。

    输入 ``spec`` 是已归一化的 PresentationSpec，``template`` 来自内置目录（提供 theme/
    typography/design）。生成顺序为启用的封面 → 每页正文 → 启用的结束页，页数满足方式分两种：

    - **自动页数**（``page_count`` 为 auto/空）：走 ``_expand_slides``，对条目过多的页做
      **分页**（标题加"（续）"）而不是裁掉内容；
    - **精确页数**（3–50 的整数）：先 ``_fit_slides_to_requested_total`` 对齐总页数，再
      ``_compact_slides_for_exact_count`` 把溢出内容压成"补充要点/其余条目"摘要，最后
      若实际页数仍与要求不符则抛 ``ValueError``——宁可报错要求重新提供 N 个内容页，
      也不同步返回页数不对的成品。

    结束页只在最后一页不是 closing 且 ``include_closing`` 为真时追加；文档属性写入模板
    标识与 schema 版本，最终统一过一遍 ``_normalize_pptx_package``。返回的是完整 PPTX
    字节，异常（页数不符）由调用方转成业务错误。
    """
    from pptx import Presentation
    from pptx.util import Inches

    spec = deepcopy(spec or {})
    prs = Presentation()
    if spec.get("aspect_ratio") == "4:3":
        prs.slide_width, prs.slide_height = Inches(10), Inches(7.5)
    else:
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    builder = _DeckBuilder(prs, template, spec)
    include_cover = bool(spec.get("include_cover", True))
    requested_pages = _requested_page_count(spec.get("page_count"))
    raw_slides = [deepcopy(item or {}) for item in (spec.get("slides") or [])]
    if requested_pages:
        raw_slides = _fit_slides_to_requested_total(
            raw_slides,
            requested_pages,
            include_cover=include_cover,
            include_closing=bool(spec.get("include_closing", True)),
        )
    slides = (
        _compact_slides_for_exact_count(raw_slides)
        if requested_pages else _expand_slides(raw_slides)
    )
    add_closing = bool(spec.get("include_closing", True)) and (
        not slides or _infer_layout(slides[-1]) != "closing"
    )
    actual_pages = int(include_cover) + len(slides) + int(add_closing)
    if requested_pages and actual_pages != requested_pages:
        expected_content = requested_pages - int(include_cover) - int(add_closing)
        raise ValueError(
            f"指定总页数为 {requested_pages} 页（含已启用的封面与结束页），"
            f"当前内容会生成 {actual_pages} 页。请提供恰好 {expected_content} 个内容页，"
            "并控制每页条目数量以避免自动拆页。"
        )
    if include_cover:
        builder.add_cover()
    for page_number, slide_spec in enumerate(slides, start=1 + int(include_cover)):
        builder.add_slide(slide_spec, page_number, actual_pages)
    if add_closing:
        builder.add_closing(actual_pages)
    prs.core_properties.title = str(spec.get("title") or "演示文稿")
    prs.core_properties.subject = (
        "由智慧云脑 Cloud Brain 基于语义页面角色与自适应模板生成"
    )
    prs.core_properties.keywords = (
        f"template={template.get('id')};"
        f"layout_family={template.get('layout_family')};"
        "schema=presentation_spec.v2"
    )
    output = io.BytesIO()
    prs.save(output)
    return _normalize_pptx_package(output.getvalue())


def _normalize_pptx_package(data: bytes) -> bytes:
    """Make chart XML strict-OpenXML compatible across renderers."""
    source = io.BytesIO(data)
    target = io.BytesIO()
    with zipfile.ZipFile(source, "r") as input_zip:
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED
        ) as output_zip:
            for info in input_zip.infolist():
                content = input_zip.read(info.filename)
                if (
                    info.filename.startswith("ppt/charts/chart")
                    and info.filename.endswith(".xml")
                ):
                    text = content.decode("utf-8")

                    def fix_id(match):
                        """把图表 XML 里的负 axId 转成无符号值（部分渲染器不接受负数）。"""
                        value = int(match.group(2))
                        if value < 0:
                            value &= 0xFFFFFFFF
                        return match.group(1) + str(value) + match.group(3)

                    text = re.sub(
                        r'(<c:(?:axId|crossAx)[^>]*\bval=")(-?\d+)(")',
                        fix_id,
                        text,
                    )
                    content = text.encode("utf-8")
                output_zip.writestr(info, content)
    return target.getvalue()


def _expand_slides(slides: list[dict]) -> list[dict]:
    """Paginate dense slides rather than silently clipping content.

    自动页数模式下的"内容过密"处理：按版式上限（content/two_column 8 条、cards/metrics 4 条、
    agenda 6 条）把一页拆成多页，后续页标题加"（续）"；表格超过 9 行时按每页 8 行拆成多个
    table 版式页。**只拆不删**，因此自动模式下页数由内容量决定，不保证与用户预期一致。
    """
    expanded: list[dict] = []
    for raw in slides:
        slide = deepcopy(raw or {})
        layout = _infer_layout(slide)
        items = list(slide.get("items") or slide.get("content") or [])
        limit = {
            "content": 8,
            "two_column": 8,
            "cards": 4,
            "metrics": 4,
            "agenda": 6,
        }.get(layout)
        table = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and item.get("type") == "table"
                and item.get("rows")
            ),
            None,
        )
        if table and len(table["rows"]) > 9:
            header = list(table["rows"][0])
            rows = list(table["rows"][1:])
            for index in range(0, len(rows), 8):
                page = deepcopy(slide)
                page["layout"] = "table"
                page["title"] = _continued_title(slide.get("title"), index > 0)
                replacement = deepcopy(table)
                replacement["rows"] = [header] + rows[index:index + 8]
                page["items"] = [replacement]
                expanded.append(page)
            continue
        if limit and len(items) > limit:
            for index in range(0, len(items), limit):
                page = deepcopy(slide)
                page["title"] = _continued_title(
                    slide.get("title"), index > 0
                )
                page["items"] = items[index:index + limit]
                expanded.append(page)
            continue
        expanded.append(slide)
    return expanded


def _compact_slides_for_exact_count(slides: list[dict]) -> list[dict]:
    """精确页数内压缩可见内容，表格溢出明细必须完整保存在实际演讲备注中。

    精确页数模式下的"内容过密"处理：条目超限时保留前 N-1 条，把剩余条目拼成一条
    "补充要点"，表格超过 9 行时保留表头 + 7 行和一行溢出提示，余下数据逐行写入备注。
    双栏每侧超过 5 条时折叠成一行；需要全部明细同时可见时应改用自动分页。
    """
    compacted: list[dict] = []
    limits = {"content": 8, "two_column": 8, "cards": 4, "metrics": 4, "agenda": 6, "timeline": 5}
    for raw in slides:
        slide = deepcopy(raw or {})
        layout = _infer_layout(slide)
        items = list(slide.get("items") or slide.get("content") or [])
        table = next((item for item in items if isinstance(item, dict) and item.get("type") == "table" and item.get("rows")), None)
        if table and len(table["rows"]) > 9:
            rows = list(table["rows"])
            kept = rows[:8]
            remainder = rows[8:]
            width = max(len(row) for row in rows)
            summary = ["其余条目", f"共 {len(remainder)} 行，详见演讲备注"] + [""] * max(0, width - 2)
            replacement = deepcopy(table)
            replacement["rows"] = kept + [summary[:width]]
            slide["items"] = [replacement]
            overflow_notes = "表格溢出明细（页面未完整展示）：\n" + "\n".join(
                " | ".join(str(cell) for cell in row) for row in [rows[0], *remainder]
            )
            slide["notes"] = (str(slide.get("notes") or "").strip() + "\n\n" + overflow_notes).strip()
            slide["_table_overflow"] = {
                "total_data_rows": len(rows) - 1,
                "visible_data_rows": len(kept) - 1,
                "notes_data_rows": len(remainder),
            }
        else:
            limit = limits.get(layout)
            if limit and len(items) > limit:
                head = items[: limit - 1]
                tail = items[limit - 1 :]
                combined_text = "；".join(_item_text(item) for item in tail if _item_text(item))
                head.append({"title": "补充要点", "description": combined_text})
                slide["items"] = head
            if layout in {"two_column", "comparison"}:
                for side in ("left", "right"):
                    values = slide.get(side)
                    if isinstance(values, list) and len(values) > 5:
                        slide[side] = values[:4] + ["；".join(_item_text(item) for item in values[4:])]
        compacted.append(slide)
    return compacted


def presentation_overflow_report(spec: dict) -> list[dict]:
    """给工具回执提供精确页数下的显示与备注行数；须在实际渲染成功后使用。"""
    requested = _requested_page_count(spec.get("page_count"))
    if not requested:
        return []
    cover = bool(spec.get("include_cover", True))
    slides = _fit_slides_to_requested_total(spec.get("slides") or [], requested,
        include_cover=cover, include_closing=bool(spec.get("include_closing", True)))
    result = []
    for index, slide in enumerate(_compact_slides_for_exact_count(slides), start=1):
        overflow = slide.get("_table_overflow")
        if overflow:
            result.append({"page_number": index + int(cover), "slide_number": index,
                           **overflow, "overflow_location": "speaker_notes",
                           "message": f"本页表格共{overflow['total_data_rows']}行数据，页面可见{overflow['visible_data_rows']}行，另{overflow['notes_data_rows']}行已写入PPT演讲备注；不得声称全部数据在页面完整展示。"})
    return result


def _fit_slides_to_requested_total(
    slides: list[dict], target_total: int, *, include_closing: bool, include_cover: bool = True
) -> list[dict]:
    """Reconcile minor model page-count drift without discarding evidence pages.

    精确页数模式的第一道对齐，处理"模型给的页数与要求不一致"：

    页数偏多：优先把 ``statement``/``section``/``quote``/``agenda`` 这类"一句话页"合并进相邻页
    （把它的关键信息追加到邻页的 ``key_message``/``notes``），找不到可合并页时从末尾往前删。

    页数偏少：优先**拆分**条目 ≥4 的普通内容页（后半页标题加"（续）"）；若没有可拆分的页，
    就插入一张由现有各页标题拼成的"关键证据与结论之间的关系"补充页。

    **已知边界**：缺口大于 1 且无页可拆时，上述补充页会被反复插入（或被再次拆分），
    因此可能生成内容重复的填充页——它保证的是"总页数精确"，不是"内容不重复"。
    调用方若要求每页都有独立论据，应在写大纲阶段就凑足页数。
    """
    fitted = [deepcopy(item or {}) for item in slides]
    explicit_closing = bool(fitted and _infer_layout(fitted[-1]) == "closing")
    desired_specs = target_total - int(include_cover) - int(include_closing and not explicit_closing)
    desired_specs = max(1, desired_specs)

    while len(fitted) > desired_specs:
        candidate = next(
            (index for index, slide in enumerate(fitted)
             if _infer_layout(slide) in {"statement", "section", "quote", "agenda"}
             and not (index == len(fitted) - 1 and _infer_layout(slide) == "closing")),
            None,
        )
        if candidate is None:
            candidate = len(fitted) - 2 if explicit_closing and len(fitted) >= 2 else len(fitted) - 1
        source = fitted[candidate]
        neighbor_index = candidate + 1 if candidate + 1 < len(fitted) and _infer_layout(fitted[candidate + 1]) != "closing" else candidate - 1
        if neighbor_index >= 0:
            neighbor = fitted[neighbor_index]
            summary = str(
                source.get("statement")
                or source.get("key_message")
                or source.get("title")
                or ""
            ).strip()
            if summary:
                existing = str(neighbor.get("key_message") or "").strip()
                neighbor["key_message"] = summary if not existing else existing + "；" + summary
            source_notes = str(source.get("notes") or "").strip()
            if source_notes:
                neighbor["notes"] = (str(neighbor.get("notes") or "").strip() + "\n" + source_notes).strip()
        fitted.pop(candidate)
        explicit_closing = bool(fitted and _infer_layout(fitted[-1]) == "closing")

    while len(fitted) < desired_specs:
        split_index = next(
            (index for index, slide in enumerate(fitted)
             if _infer_layout(slide) not in {"chart", "table", "statement", "section", "quote", "closing"}
             and len(slide.get("items") or slide.get("content") or []) >= 4),
            None,
        )
        if split_index is not None:
            source = fitted[split_index]
            values = list(source.get("items") or source.get("content") or [])
            midpoint = max(2, len(values) // 2)
            first, second = deepcopy(source), deepcopy(source)
            first["items"] = values[:midpoint]
            second["items"] = values[midpoint:]
            second["title"] = _continued_title(source.get("title"), True)
            fitted[split_index:split_index + 1] = [first, second]
            continue
        evidence = [
            {"title": str(slide.get("title") or f"要点 {index + 1}"),
             "description": str(slide.get("key_message") or slide.get("statement") or "已纳入整体分析")}
            for index, slide in enumerate(fitted)
            if _infer_layout(slide) != "closing"
        ][:6]
        insertion = len(fitted) - 1 if explicit_closing else len(fitted)
        fitted.insert(insertion, {
            "title": "关键证据与结论之间的关系",
            "layout": "content",
            "section": "补充分析",
            "items": evidence or ["现有材料已完成结构化分析"],
        })
    return fitted


def _requested_page_count(value) -> int | None:
    """解析用户指定页数：返回 ``None`` 表示自动，否则为 3–50 的整数。

    非法值抛 ``ValueError`` 而不是静默回落到自动——页数是用户的明确约束，
    悄悄放宽会让用户拿到与预期页数不符的成品。
    """

    if value in (None, "", "auto"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("PPT 总页数必须是 3–50 的整数或 auto")
    if parsed < 3 or parsed > 50:
        raise ValueError("PPT 总页数必须在 3–50 页之间")
    return parsed


def _continued_title(title, continued: bool) -> str:
    """续页标题：在原标题后追加"（续）"，用于拆页后仍能看出是同一话题。"""

    value = str(title or "未命名页面")
    return value + "（续）" if continued else value


def _infer_layout(slide: dict) -> str:
    """推断页面版式（渲染分支的唯一依据），返回受支持版式名之一。

    优先级体现"结构证据强于标签"：条目里出现 table/chart 类型时**强制**用对应版式，
    即模型把表格页标成 metrics 也不会让真实数据消失。标签 ``metrics`` 但没有指标值时降级为
    cards；别名（title/hero/kpi/toc 等）先归一化；都没有依据时按条目数量落到 cards/metrics/
    statement/content。返回值必须落在 ``_DeckBuilder`` 的 ``_layout_*`` 方法集合内。
    """

    explicit = str(
        slide.get("layout")
        or slide.get("page_role")
        or slide.get("type")
        or ""
    ).lower().replace("-", "_")
    aliases = {
        "title": "statement",
        "hero": "statement",
        "key_message": "statement",
        "chapter": "section",
        "toc": "agenda",
        "columns": "two_column",
        "3_cards": "cards",
        "4_cards": "cards",
        "kpi": "metrics",
        "data": "chart",
        "closing_page": "closing",
    }
    explicit = aliases.get(explicit, explicit)
    items = list(slide.get("items") or slide.get("content") or [])
    types = {
        str(item.get("type") or "").lower()
        for item in items if isinstance(item, dict)
    }
    # Structured content is stronger evidence than a mistaken layout label.
    # This prevents real chart/table data from disappearing in a metrics/card page.
    if "table" in types:
        return "table"
    if "chart" in types:
        return "chart"
    if explicit == "table" and items and "table" not in types:
        return "content"
    if explicit == "metrics" and items and not any(
        isinstance(item, dict) and _has_metric_value(item)
        for item in items
    ):
        return "cards"
    supported = {
        "statement", "agenda", "section", "content", "two_column", "cards",
        "metrics", "comparison", "timeline", "table", "chart", "quote",
        "image", "matrix", "closing",
    }
    if explicit in supported:
        return explicit
    if "timeline" in types or any(
        isinstance(item, dict) and item.get("steps") for item in items
    ):
        return "timeline"
    if "comparison" in types or slide.get("left") or slide.get("right"):
        return "comparison"
    if "quote" in types:
        return "quote"
    if "image" in types:
        return "image"
    if "metric" in types or (
        2 <= len(items) <= 4
        and all(isinstance(item, dict) and _has_metric_value(item) for item in items)
    ):
        return "metrics"
    if 2 <= len(items) <= 4 and all(
        isinstance(item, dict) and item.get("title") for item in items
    ):
        return "cards"
    if len(items) == 0 and slide.get("title"):
        return "statement"
    return "content"


class _DeckBuilder:
    """把一个模板 + 一份 spec 渲染成整套 PPTX 的"装配器"（单次生成内使用，禁止复用）。

    职责边界：本类负责**全部**版式与像素级细节（背景纹理、页眉页脚 chrome、标题/正文排版、
    卡片/指标/时间线/表格/原生图表、图片裁剪），输入侧只接受语义化 spec——模型给的是"这页
    有哪些要点"，位置与字号完全由这里决定。

    关键不变式：
    - 坐标统一用 13.333×7.5 英寸（16:9）体系，``scale_x`` 只用于比例校正；4:3 时画布更窄，
      因此横向元素仍按 16:9 布局，靠留白适应；
    - 页脚线固定在 6.95 英寸，正文区必须在 ``_content_top`` 到 6.72 之间——内容过密由排版
      函数自适应（缩字号/换行）而不是压掉页脚；
    - ``self.theme`` 取色只允许通过 ``color()``，颜色键缺失时按原样当十六进制值处理；
    - 所有版式方法都只追加形状，不删除既有形状；``_layout_closing`` 故意为空，因为结束页由
      ``add_closing`` 单独生成。
    """

    def __init__(self, prs, template: dict, spec: dict):
        """绑定演示对象、模板主题与 spec；``blank`` 用索引 6 的空版式以完全自绘。"""

        from pptx.dml.color import RGBColor

        self.prs = prs
        self.template = template
        self.spec = spec
        self.theme = template["theme"]
        self.typography = template.get("typography") or {}
        self.design = template.get("design") or {}
        self.family = template.get("layout_family") or "executive"
        self.width = prs.slide_width
        self.height = prs.slide_height
        self.scale_x = (self.width / 914400) / 13.333
        self.blank = prs.slide_layouts[6]
        self.RGBColor = RGBColor

    def color(self, key_or_value: str):
        """把主题键（如 ``accent``/``surface``）或 ``#RRGGBB`` 解析为 RGBColor。"""

        value = self.theme.get(key_or_value, key_or_value).lstrip("#")
        return self.RGBColor(
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
        )

    def add_cover(self) -> None:
        """生成封面页：背景 + 装饰 + eyebrow/标题/副标题/署名，并按模板块配置排布。

        标题过长时由 ``_text`` 自动缩字号（min_size 28、最多 2 行）；副标题的 Y 坐标会被
        推到标题实际高度之下，避免长标题压住副标题（这是历史排版缺陷的修正）。
        """

        slide = self.prs.slides.add_slide(self.blank)
        self._background(slide)
        title = str(self.spec.get("title") or "演示文稿")
        subtitle = str(self.spec.get("subtitle") or "")
        eyebrow = str(
            self.spec.get("eyebrow")
            or self.template.get("category")
            or "PRESENTATION"
        )
        cfg = self._cover_composition(
            slide, str(self.design.get("cover") or "split")
        )
        self._text(
            slide, eyebrow.upper(), cfg["eyebrow_x"], cfg["eyebrow_y"],
            cfg["eyebrow_w"], 0.38, 12, cfg.get("eyebrow_color", "accent"),
            bold=True, spacing=1.4, align=cfg.get("align", "left"),
        )
        self._text(
            slide, title, cfg["title_x"], cfg["title_y"], cfg["title_w"],
            cfg.get("title_h", 2.05), cfg.get("title_size", 50),
            cfg.get("title_color", "text"), bold=True, min_size=28,
            max_lines=cfg.get("title_lines", 2), align=cfg.get("align", "left"),
        )
        if subtitle:
            subtitle_y = max(
                float(cfg["subtitle_y"]),
                float(cfg["title_y"]) + float(cfg.get("title_h", 2.05)) + 0.12,
            )
            self._text(
                slide, subtitle, cfg["subtitle_x"], subtitle_y,
                cfg["subtitle_w"], cfg.get("subtitle_h", 1.0),
                cfg.get("subtitle_size", 20),
                cfg.get("subtitle_color", "muted"), min_size=14, max_lines=3,
                align=cfg.get("align", "left"),
            )
        self._text(
            slide,
            str(self.spec.get("author") or "智慧云脑 Cloud Brain"),
            cfg.get("author_x", 0.86), cfg.get("author_y", 6.72),
            cfg.get("author_w", 5.5), 0.32, 11,
            cfg.get("author_color", "muted"), align=cfg.get("author_align", "left"),
        )
        self._notes(slide, self.spec.get("cover_notes"))

    def _cover_composition(self, slide, variant: str) -> dict:
        """按 ``design.cover`` 画封面装饰并返回文字排布配置（坐标/字号/对齐/颜色键）。

        是"一套模板一种封面构图"的实现：每种 variant 先画装饰形状，再用 ``cfg.update``
        覆盖文字位置，因此新增封面风格只需加一个分支。未知 variant 落到默认分栏构图；
        返回的字典键必须与 ``add_cover``/``add_closing`` 读取的键一致。
        """

        from pptx.enum.shapes import MSO_SHAPE

        cfg = {
            "eyebrow_x": 0.86, "eyebrow_y": 1.48, "eyebrow_w": 5.2,
            "title_x": 0.82, "title_y": 2.08, "title_w": 8.5,
            "subtitle_x": 0.86, "subtitle_y": 4.46, "subtitle_w": 7.4,
            "title_size": 50, "align": "left",
        }
        if variant == "data_wall":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 8.35, 0, 4.98, 7.5, "surface", line=None)
            for index, value in enumerate(("42%", "18.6%", "06W")):
                self._text(slide, value, 8.92, 1.05 + index * 1.65, 3.1, 0.72, 38, "accent" if index == 0 else "text", bold=True)
                self._line(slide, 8.92, 1.87 + index * 1.65, 12.12, 1.87 + index * 1.65, "line", 1)
            cfg.update(title_w=6.8, title_y=1.95, subtitle_w=6.7)
        elif variant == "aurora":
            self._shape(slide, MSO_SHAPE.OVAL, 0.45, 0.30, 3.3, 3.3, "accent2", line=None)
            self._shape(slide, MSO_SHAPE.OVAL, 9.35, 3.95, 3.3, 3.3, "accent", line=None)
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 1.55, 1.12, 10.25, 5.25, "surface", line="line")
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 2.05, 1.55, 2.05, 0.48, "background", line=None)
            cfg.update(eyebrow_x=2.28, eyebrow_y=1.65, title_x=2.05, title_y=2.42, title_w=9.1, subtitle_x=2.08, subtitle_y=4.60, subtitle_w=7.9, author_x=2.08, author_y=5.78)
        elif variant == "swiss":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.78, 5.28, 1.25, 1.25, "accent", line=None)
            self._text(slide, "20", 10.0, 4.0, 2.45, 1.5, 88, "text", bold=True, align="right")
            self._line(slide, 0.78, 0.92, 12.55, 0.92, "text", 2)
            cfg.update(eyebrow_x=0.8, eyebrow_y=0.46, title_x=0.78, title_y=2.0, title_w=11.6, title_size=60, subtitle_x=0.82, subtitle_y=3.70, subtitle_w=8.8, author_x=9.3, author_y=6.70, author_w=3.2, author_align="right")
        elif variant == "editorial":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 0.20, 7.5, "accent", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 8.82, 0.54, 3.62, 6.03, "accent2", line=None)
            self._text(slide, "PERSPECTIVE", 9.12, 1.0, 2.7, 0.4, 13, "background", bold=True)
            self._text(slide, "01", 9.02, 4.35, 2.9, 1.15, 64, "background", bold=True)
            cfg.update(title_w=7.25, title_size=55)
        elif variant == "blueprint":
            for x1, y1, x2, y2 in ((7.2, 4.9, 9.1, 3.85), (9.1, 3.85, 11.15, 4.9), (7.2, 4.9, 9.1, 5.95), (9.1, 5.95, 11.15, 4.9), (9.1, 3.85, 9.1, 5.95)):
                self._line(slide, x1, y1, x2, y2, "accent", 1.4)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 10.75, 0.58, 1.68, 0.72, "background", line="accent")
            self._text(slide, "SCALE 1:50", 10.92, 0.79, 1.34, 0.24, 11, "accent", bold=True)
            cfg.update(title_w=7.6, title_size=48)
        elif variant == "paper":
            note = self._shape(slide, MSO_SHAPE.RECTANGLE, 6.95, 1.15, 4.85, 5.18, "surface", line="line")
            note.rotation = 2
            self._line(slide, 7.35, 2.05, 11.35, 2.05, "line", 1)
            self._line(slide, 7.35, 3.15, 11.35, 3.15, "line", 1)
            self._line(slide, 7.35, 4.25, 11.35, 4.25, "line", 1)
            self._text(slide, "STRATEGY / NOTES", 7.42, 1.52, 3.3, 0.34, 12, "accent", bold=True)
            cfg.update(title_w=5.8, title_size=47, subtitle_w=5.7)
        elif variant == "ink":
            self._shape(slide, MSO_SHAPE.OVAL, 8.4, 0.55, 3.65, 3.65, "surface", line=None)
            self._shape(slide, MSO_SHAPE.OVAL, 10.75, 5.56, 0.62, 0.62, "accent2", line=None)
            self._line(slide, 8.25, 5.3, 12.0, 5.3, "line", 1)
            cfg.update(title_w=7.4, title_size=54, subtitle_w=6.7)
        elif variant == "memphis":
            self._shape(slide, MSO_SHAPE.OVAL, 0.62, 0.58, 0.95, 0.95, "accent2", line="text")
            self._shape(slide, MSO_SHAPE.RECTANGLE, 10.5, 0.82, 1.25, 1.25, "accent", line="text").rotation = 11
            self._shape(slide, MSO_SHAPE.OVAL, 9.0, 3.25, 2.7, 2.7, "accent2", line="text")
            # Keep the dynamically positioned subtitle inside the paper panel.
            # Long or large cover titles may push the subtitle below its nominal
            # Y position; the previous 2.75-inch panel then cut through the text.
            self._shape(slide, MSO_SHAPE.RECTANGLE, 1.3, 2.05, 7.1, 3.18, "surface", line="text")
            cfg.update(eyebrow_x=1.68, eyebrow_y=1.55, title_x=1.68, title_y=2.34, title_w=6.25, title_size=52, subtitle_x=1.72, subtitle_y=4.0, subtitle_w=6.15, subtitle_h=0.68, author_x=1.72)
        elif variant == "brutalist":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 1.08, 1.22, 9.8, 4.45, "accent2", line="text")
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.72, 0.86, 9.8, 4.45, "surface", line="text")
            self._shape(slide, MSO_SHAPE.RECTANGLE, 10.92, 0.86, 1.42, 1.42, "accent", line="text")
            cfg.update(eyebrow_x=1.08, eyebrow_y=1.28, title_x=1.05, title_y=2.0, title_w=8.9, subtitle_x=1.08, subtitle_y=4.24, subtitle_w=8.2, author_x=1.08, author_y=5.82)
        elif variant == "soft":
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 0.72, 0.72, 11.9, 6.0, "surface", line="line")
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 8.82, 1.32, 3.15, 4.8, "background", line=None)
            for index in range(3):
                self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 9.25, 1.78 + index * 1.28, 2.25, 0.82, "surface", line="line")
            cfg.update(eyebrow_x=1.22, title_x=1.18, title_w=7.0, subtitle_x=1.22, subtitle_w=6.7, author_x=1.22, author_y=6.15)
        elif variant == "cyber":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.55, 0.5, 12.2, 6.5, "background", line="accent")
            for x, y in ((9.8, 1.35), (11.3, 2.85), (9.4, 5.55)):
                self._shape(slide, MSO_SHAPE.OVAL, x, y, 0.22, 0.22, "accent", line=None)
            self._line(slide, 9.9, 1.46, 11.4, 2.96, "accent", 1)
            self._line(slide, 11.4, 2.96, 9.5, 5.66, "accent2", 1)
            cfg.update(title_w=8.0, title_size=50)
        elif variant == "canopy":
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 8.1, 0, 5.23, 7.5, "surface", line=None)
            for diameter, x, y in ((3.5, 9.05, 0.72), (2.5, 9.55, 1.22), (1.5, 10.05, 1.72)):
                self._shape(slide, MSO_SHAPE.OVAL, x, y, diameter, diameter, "background", line="accent")
            cfg.update(title_w=6.75, title_size=50, subtitle_w=6.6)
        elif variant == "luxury":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.58, 0.58, 12.17, 6.34, "background", line="accent")
            self._line(slide, 4.35, 1.58, 8.98, 1.58, "accent", 1)
            cfg.update(eyebrow_x=4.2, eyebrow_y=1.78, eyebrow_w=4.95, title_x=2.0, title_y=2.55, title_w=9.33, title_size=54, subtitle_x=2.4, subtitle_y=4.62, subtitle_w=8.53, align="center", author_x=4.2, author_y=6.25, author_w=4.95, author_align="center")
        elif variant == "civic":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 0.62, "accent", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.8, 5.78, 11.75, 0.08, "accent2", line=None)
            self._text(slide, "汇报材料", 10.7, 0.16, 1.65, 0.25, 11, "background", bold=True, align="right")
            cfg.update(eyebrow_y=1.55, title_y=2.22, title_x=1.2, title_w=10.95, title_size=52, subtitle_x=1.65, subtitle_y=4.42, subtitle_w=10.0, align="center", author_x=4.2, author_y=6.45, author_w=4.9, author_align="center")
        elif variant == "clinical":
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 7.82, 0.72, 4.75, 6.05, "surface", line="line")
            self._shape(slide, MSO_SHAPE.RECTANGLE, 9.72, 2.0, 0.48, 2.05, "accent2", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 8.93, 2.78, 2.05, 0.48, "accent2", line=None)
            cfg.update(title_w=6.65, title_size=48, subtitle_w=6.5)
        elif variant == "academic":
            self._line(slide, 0.82, 1.18, 12.45, 1.18, "accent", 2)
            self._line(slide, 0.82, 5.92, 12.45, 5.92, "line", 1)
            self._text(slide, "RESEARCH NOTE · VOL. 01", 8.4, 0.72, 4.0, 0.28, 11, "muted", align="right")
            cfg.update(eyebrow_y=1.52, title_y=2.13, title_w=10.9, title_size=49, subtitle_y=4.45, subtitle_w=9.1)
        elif variant == "industrial":
            self._shape(slide, MSO_SHAPE.CHEVRON, 8.15, 0, 5.18, 7.5, "surface", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 9.65, 1.12, 2.25, 0.16, "accent", line=None)
            self._text(slide, "OPS / 2026", 9.62, 1.52, 2.45, 0.42, 15, "accent", bold=True)
            cfg.update(title_w=6.9, title_size=48, subtitle_w=6.7)
        elif variant == "cinematic":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 0.75, "surface", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 6.75, 13.333, 0.75, "surface", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 8.9, 1.2, 3.45, 4.85, "accent2", line=None)
            cfg.update(eyebrow_y=1.38, title_y=2.08, title_w=7.65, title_size=54, subtitle_y=4.55, subtitle_w=7.2, author_y=6.28)
        elif variant == "press":
            self._line(slide, 0.72, 1.08, 12.62, 1.08, "text", 2)
            self._line(slide, 0.72, 5.92, 12.62, 5.92, "line", 1)
            self._line(slide, 8.72, 1.35, 8.72, 5.62, "line", 1)
            self._text(slide, "DATA / PRESS", 9.1, 1.55, 3.05, 0.42, 15, "accent", bold=True)
            cfg.update(eyebrow_y=0.58, title_y=1.78, title_w=7.45, title_size=50, subtitle_y=4.42, subtitle_w=7.2)
        else:
            self._shape(slide, MSO_SHAPE.RECTANGLE, 10.55, 0, 2.78, 7.5, "surface", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.82, 1.18, 1.42, 0.09, "accent", line=None)
        return cfg

    def add_slide(
        self, slide_spec: dict, page_number: int, total_pages: int
    ) -> None:
        """生成一张正文页：背景 → 页眉页脚 → 按 ``_infer_layout`` 派发到对应版式 → 备注。

        版式方法缺失时回退 ``_layout_content``（含 closing 走 ``add_closing``），因此新增
        spec 版式不会导致渲染崩溃。``page_number``/``total_pages`` 只用于页码显示。
        """

        layout = _infer_layout(slide_spec)
        if layout == "closing":
            self.add_closing(page_number, slide_spec)
            return
        slide = self.prs.slides.add_slide(self.blank)
        self._background(slide)
        self._chrome(slide, slide_spec, page_number, total_pages)
        method = getattr(self, f"_layout_{layout}", self._layout_content)
        method(slide, slide_spec)
        self._notes(slide, slide_spec.get("notes"))

    def add_closing(
        self, page_number: int, slide_spec: dict | None = None
    ) -> None:
        """生成结束页，复用封面构图但限制标题高度/字号；页码与模板名写在署名位置。

        ``slide_spec`` 可覆盖标题与副标题，未给出时用 spec 的 ``closing_title``/``closing_subtitle``；
        页脚同时充当"结束页标记"，因此同一 deck 内不应出现第二张 closing。
        """

        slide_spec = slide_spec or {}
        slide = self.prs.slides.add_slide(self.blank)
        self._background(slide)
        cfg = self._cover_composition(
            slide, str(self.design.get("cover") or "split")
        )
        self._text(
            slide,
            str(slide_spec.get("title") or self.spec.get("closing_title") or "谢谢"),
            cfg["title_x"], cfg["title_y"], cfg["title_w"],
            min(1.45, cfg.get("title_h", 2.05)),
            min(50, cfg.get("title_size", 50)), cfg.get("title_color", "text"),
            bold=True, min_size=30, max_lines=2, align=cfg.get("align", "left"),
        )
        self._text(
            slide,
            str(
                slide_spec.get("subtitle")
                or self.spec.get("closing_subtitle")
                or "期待进一步交流"
            ),
            cfg["subtitle_x"], cfg["subtitle_y"], cfg["subtitle_w"],
            0.72, 20, cfg.get("subtitle_color", "muted"),
            align=cfg.get("align", "left"),
        )
        self._text(
            slide, f"{self.template['name']}  ·  {page_number:02d}",
            cfg.get("author_x", 0.86), cfg.get("author_y", 6.72),
            cfg.get("author_w", 5.5), 0.32, 11,
            cfg.get("author_color", "muted"), bold=True,
            align=cfg.get("author_align", "left"),
        )
        self._notes(
            slide,
            slide_spec.get("notes")
            or self.spec.get("closing_notes"),
        )

    def _background(self, slide) -> None:
        """铺底色并按 ``design.background`` 画纹理（网格、纸纹、光斑、彩屑、同心环等）。

        纹理由大量细线/小形状组成，全部使用主题色键，因此换主题即换底色与纹理颜色；
        坐标按 13.333×7.5 固定，切换 4:3 时纹理仍铺满 16:9 逻辑画布。
        """

        from pptx.enum.shapes import MSO_SHAPE

        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = self.color("background")
        pattern = str(self.design.get("background") or "plain")
        if pattern in {"grid", "blueprint", "soft_grid", "paper_grid"}:
            step = 0.62 if pattern == "blueprint" else 0.92
            line_color = "accent" if pattern == "blueprint" else "line"
            max_x = int(13.333 / step) + 1
            max_y = int(7.5 / step) + 1
            for index in range(max_x):
                x = index * step
                self._line(slide, x, 0, x, 7.5, line_color, 0.35)
            for index in range(max_y):
                y = index * step
                self._line(slide, 0, y, 13.333, y, line_color, 0.35)
        elif pattern == "paper":
            self._line(slide, 1.02, 0, 1.02, 7.5, "accent2", 0.7)
            for index in range(10):
                y = 0.88 + index * 0.64
                self._line(slide, 0, y, 13.333, y, "line", 0.4)
        elif pattern == "orbs":
            self._shape(slide, MSO_SHAPE.OVAL, 0.05, 0.05, 2.4, 2.4, "accent2", line=None)
            self._shape(slide, MSO_SHAPE.OVAL, 10.85, 5.10, 2.3, 2.3, "accent", line=None)
        elif pattern == "confetti":
            for x, y, shape_type, color in (
                (0.28, 0.42, MSO_SHAPE.OVAL, "accent2"),
                (11.82, 0.58, MSO_SHAPE.RECTANGLE, "accent"),
                (12.25, 5.95, MSO_SHAPE.OVAL, "accent2"),
                (0.42, 6.25, MSO_SHAPE.DIAMOND, "accent"),
            ):
                self._shape(slide, shape_type, x, y, 0.42, 0.42, color, line="text")
        elif pattern == "wash":
            self._shape(slide, MSO_SHAPE.OVAL, 9.45, 0.20, 3.2, 3.2, "surface", line=None)
            self._shape(slide, MSO_SHAPE.OVAL, 0.05, 5.55, 1.8, 1.8, "surface", line=None)
        elif pattern == "circuit":
            for index in range(5):
                y = 1.0 + index * 1.18
                self._line(slide, 8.75, y, 12.65, y, "line", 0.8)
                self._shape(slide, MSO_SHAPE.OVAL, 8.62 + (index % 2) * 3.62, y - 0.10, 0.20, 0.20, "accent" if index % 2 == 0 else "accent2", line=None)
        elif pattern == "rings":
            for diameter in (2.0, 3.2, 4.4):
                self._shape(slide, MSO_SHAPE.OVAL, 10.55 - diameter / 2, 3.78 - diameter / 2, diameter, diameter, "background", line="line")
        elif pattern == "frame":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.38, 0.38, 12.57, 6.74, "background", line="line")
        elif pattern == "soft":
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 9.75, 0.05, 3.45, 1.65, "surface", line=None)
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 0.05, 6.05, 2.8, 1.30, "surface", line=None)

    def _chrome(
        self, slide, slide_spec: dict, page_number: int, total_pages: int
    ) -> None:
        """画页眉/页脚、章节标签、页码与结论行，并确定标题的落点坐标。

        每种 ``design.chrome`` 风格会改写 ``title_x/title_y/title_w``，所以版式方法必须从
        ``_content_top`` 起排版、不能假设标题高度固定。结论行取 ``key_message``，
        有结论时正文下移（见 ``_content_top``）；页脚基线固定 6.95 英寸。
        """

        from pptx.enum.shapes import MSO_SHAPE

        section = str(slide_spec.get("section") or self.template.get("category") or "")
        conclusion = str(slide_spec.get("key_message") or "")
        chrome = str(self.design.get("chrome") or "minimal")
        title_x, title_y, title_w = 0.62, 0.68, 11.3
        if chrome == "band":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 0.18, 13.333, 0.48, "accent", line=None)
            self._text(slide, section, 0.58, 0.30, 3.1, 0.25, 11, "background", bold=True)
            title_y = 0.88
        elif chrome == "rail":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.36, 0.32, 0.08, 6.52, "accent", line=None)
            self._text(slide, section.upper(), 0.65, 0.28, 3.4, 0.28, 10, "accent", bold=True)
            title_x, title_y, title_w = 0.65, 0.66, 10.85
        elif chrome == "grid":
            self._line(slide, 0.72, 0.58, 12.62, 0.58, "text", 1.5)
            self._text(slide, section.upper(), 0.74, 0.20, 3.4, 0.25, 10, "text", bold=True, spacing=1.0)
            self._text(slide, f"{page_number:02d}", 11.4, 0.70, 1.2, 0.64, 32, "accent", bold=True, align="right")
            title_x, title_y, title_w = 0.72, 0.82, 10.25
        elif chrome == "editorial":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 0.18, 7.5, "accent", line=None)
            self._text(slide, section.upper(), 0.62, 0.30, 2.8, 0.28, 10, "accent", bold=True, spacing=1.2)
            self._line(slide, 3.65, 0.45, 12.6, 0.45, "line", 1)
            title_x, title_y, title_w = 0.62, 0.73, 11.4
        elif chrome == "technical":
            self._line(slide, 0.52, 0.38, 1.72, 0.38, "accent", 2)
            self._line(slide, 0.52, 0.38, 0.52, 0.78, "accent", 2)
            self._text(slide, section.upper(), 1.88, 0.28, 3.5, 0.25, 10, "accent", bold=True)
            self._text(slide, f"SYS-{page_number:02d}", 10.9, 0.27, 1.7, 0.25, 10, "muted", align="right")
            title_x, title_y, title_w = 0.56, 0.72, 11.3
        elif chrome == "notebook":
            self._line(slide, 1.02, 0, 1.02, 7.5, "accent2", 1)
            self._text(slide, section, 1.3, 0.30, 3.2, 0.28, 10, "accent", bold=True)
            title_x, title_y, title_w = 1.28, 0.73, 10.7
        elif chrome == "ink":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 11.86, 0.34, 0.46, 0.46, "accent2", line=None)
            self._text(slide, section, 0.68, 0.31, 3.2, 0.28, 11, "text", bold=True)
            self._line(slide, 0.68, 0.73, 4.1, 0.73, "line", 1)
            title_x, title_y, title_w = 0.68, 0.88, 10.9
        elif chrome == "playful":
            self._shape(slide, MSO_SHAPE.OVAL, 0.52, 0.30, 0.34, 0.34, "accent2", line="text")
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.98, 0.27, 0.34, 0.34, "accent", line="text").rotation = 8
            self._text(slide, section.upper(), 1.55, 0.30, 3.0, 0.26, 10, "text", bold=True)
            title_x, title_y, title_w = 0.58, 0.78, 11.6
        elif chrome == "boxed":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.48, 0.32, 12.32, 1.15, "surface", line="text")
            self._text(slide, section.upper(), 0.72, 0.44, 2.6, 0.22, 9, "accent2", bold=True)
            title_x, title_y, title_w = 0.72, 0.73, 11.45
        elif chrome == "floating":
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 0.58, 0.32, 2.2, 0.46, "surface", line="line")
            self._text(slide, section, 0.78, 0.43, 1.8, 0.22, 10, "accent", bold=True, align="center")
            title_x, title_y, title_w = 0.62, 0.92, 11.3
        elif chrome == "organic":
            self._shape(slide, MSO_SHAPE.OVAL, 0.56, 0.28, 0.48, 0.48, "accent", line=None)
            self._text(slide, section, 1.25, 0.36, 3.0, 0.25, 10, "accent", bold=True)
            title_x, title_y, title_w = 0.62, 0.86, 11.2
        elif chrome == "luxury":
            self._line(slide, 0.68, 0.48, 12.62, 0.48, "accent", 1)
            self._text(slide, section.upper(), 0.72, 0.18, 3.0, 0.23, 9, "accent", bold=True, spacing=1.3)
            title_x, title_y, title_w = 0.72, 0.82, 11.7
        elif chrome == "clinical":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.62, 0.24, 0.13, 0.52, "accent2", line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.43, 0.43, 0.52, 0.13, "accent2", line=None)
            self._text(slide, section, 1.18, 0.36, 3.0, 0.24, 10, "accent", bold=True)
            title_x, title_y, title_w = 0.62, 0.88, 11.2
        elif chrome == "academic":
            self._text(slide, "RESEARCH / " + section.upper(), 0.64, 0.26, 4.2, 0.24, 9, "accent", bold=True, spacing=0.8)
            self._text(slide, f"{page_number:02d}", 11.65, 0.24, 0.9, 0.28, 11, "muted", align="right")
            self._line(slide, 0.64, 0.62, 12.6, 0.62, "accent", 1.5)
            title_x, title_y, title_w = 0.64, 0.88, 11.4
        elif chrome == "cinematic":
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 0.28, "surface", line=None)
            self._text(slide, section.upper(), 0.68, 0.42, 3.0, 0.25, 9, "accent", bold=True, spacing=1.2)
            title_x, title_y, title_w = 0.68, 0.82, 11.3
        elif chrome == "press":
            self._text(slide, "DATA PRESS", 0.62, 0.20, 2.2, 0.26, 10, "text", bold=True, spacing=1.4)
            self._text(slide, section.upper(), 5.0, 0.20, 3.2, 0.26, 10, "accent", bold=True, align="center")
            self._text(slide, f"PAGE {page_number:02d}", 10.55, 0.20, 2.1, 0.26, 9, "muted", align="right")
            self._line(slide, 0.62, 0.58, 12.66, 0.58, "text", 1.5)
            title_x, title_y, title_w = 0.62, 0.86, 11.45
        else:
            self._shape(slide, MSO_SHAPE.RECTANGLE, 0.62, 0.42, 0.92, 0.07, "accent", line=None)
            self._text(slide, section.upper(), 1.72, 0.31, 3.4, 0.30, 10, "accent", bold=True, spacing=0.8)
        self._text(
            slide,
            str(slide_spec.get("title") or f"第 {page_number} 页"),
            title_x, title_y, title_w, 0.68,
            int(self.typography.get("title_size") or 33),
            "text", bold=True, min_size=24, max_lines=2,
        )
        if conclusion:
            self._text(
                slide, conclusion, 0.64, 1.48, 11.45, 0.42,
                14, "accent", bold=True, min_size=12, max_lines=2,
            )
        footer_line_color = "accent" if chrome in {"luxury", "academic", "press"} else "line"
        self._line(slide, 0.62, 6.95, 12.72, 6.95, footer_line_color, 1)
        self._text(
            slide,
            str(self.spec.get("footer") or self.template["name"]),
            0.64, 7.02, 5.0, 0.22, 9, "muted",
        )
        self._text(
            slide, f"{page_number:02d}",
            12.32, 7.02, 0.40, 0.22, 9, "muted", align="left",
        )

    def _content_top(self, slide_spec: dict) -> float:
        """正文区起始 Y（英寸）：有结论行时下移，保证不与结论行重叠。"""

        return 1.96 if slide_spec.get("key_message") else 1.60

    def _layout_statement(self, slide, spec) -> None:
        """观点页版式：一句主论点 + 最多 3 条支撑要点，用于章节开场的核心判断。"""

        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)
        statement = str(
            spec.get("statement")
            or spec.get("key_message")
            or _item_text(items[0] if items else "")
            or spec.get("title")
            or ""
        )
        supporting = [
            _item_text(item) for item in items[1:] if _item_text(item)
        ]
        evidence = str(spec.get("key_message") or "").strip()
        if evidence and evidence != statement:
            supporting.insert(0, evidence)
        self._shape(
            slide, MSO_SHAPE.RECTANGLE,
            0.64, 2.0, 0.11, 3.75, "accent", line=None,
        )
        self._text(
            slide, statement, 1.05, 2.05, 10.5, 2.5,
            38 if self.family != "editorial" else 44,
            "text", bold=True, min_size=27, max_lines=4,
        )
        if supporting:
            self._text(
                slide, "  ·  ".join(supporting[:3]),
                1.08, 4.98, 9.6, 0.62, 17, "muted",
                min_size=13, max_lines=2,
            )

    def _layout_agenda(self, slide, spec) -> None:
        """目录页版式：最多 6 项，超过 3 项自动分两栏，各项带序号与引导线。"""

        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)[:6]
        top = self._content_top(spec)
        columns = 2 if len(items) > 3 else 1
        per_column = math.ceil(len(items) / columns)
        for index, item in enumerate(items):
            column = index // per_column
            row = index % per_column
            x = 0.72 + column * 6.15
            y = top + row * 1.23
            self._text(
                slide, f"{index + 1:02d}", x, y, 0.68, 0.45,
                24, "accent", bold=True,
            )
            self._line(slide, x + 0.82, y + 0.44, x + 5.55, y + 0.44, "line", 1)
            self._text(
                slide, _item_title(item) or _item_text(item),
                x + 0.82, y - 0.02, 4.7, 0.46,
                18, "text", bold=True, min_size=15, max_lines=2,
            )
            description = _item_description(item)
            if description:
                self._text(
                    slide, description, x + 0.82, y + 0.48, 4.55, 0.42,
                    12, "muted", min_size=10, max_lines=2,
                )

    def _layout_section(self, slide, spec) -> None:
        """章节分隔页版式：左侧色块放编号/标签，右侧放章节标题与最多 3 条提示。"""

        from pptx.enum.shapes import MSO_SHAPE

        number = str(spec.get("number") or spec.get("section_number") or "01")
        self._shape(
            slide, MSO_SHAPE.RECTANGLE,
            0.62, 2.12, 3.2, 3.55, "accent", line=None,
        )
        self._text(
            slide, number, 1.0, 2.6, 2.35, 1.15,
            60, "background", bold=True,
        )
        self._text(
            slide, str(spec.get("label") or "SECTION"),
            1.03, 4.45, 2.25, 0.45, 13, "background",
            bold=True, spacing=1.1,
        )
        items = [_item_text(item) for item in _items(spec) if _item_text(item)]
        if items:
            self._text(
                slide, items[0], 4.35, 2.45, 7.25, 1.6,
                30, "text", bold=True, min_size=22, max_lines=3,
            )
        if len(items) > 1:
            self._text(
                slide, "  ·  ".join(items[1:4]),
                4.38, 4.48, 6.8, 0.7, 16, "muted",
                min_size=13, max_lines=2,
            )

    def _layout_content(self, slide, spec) -> None:
        """通用正文版式：编号列表，最多渲染 8 条，行高按条目数自适应。

        行高由"可用高度 / 条目数"决定（上限 1.45 英寸）：行高足够时用"标题 + 描述"两级排版，
        紧凑时退化为单行自动缩字号文本；条目为空时依次回退到 statement/key_message/summary，
        仍为空才显示"本页暂无内容"（占位而非留白，便于用户发现问题）。
        """

        raw_items = [item for item in _items(spec) if _item_text(item)]
        if not raw_items:
            fallback = str(
                spec.get("statement")
                or spec.get("key_message")
                or spec.get("summary")
                or ""
            ).strip()
            if fallback:
                raw_items = [fallback]
        top = self._content_top(spec)
        if not raw_items:
            self._text(
                slide, "本页暂无内容", 0.82, top + 1.6, 11.3, 0.7,
                22, "muted", align="center",
            )
            return

        visible = raw_items[:8]
        # Reserve the footer and calculate a real row budget.  A fixed 0.78-inch
        # step caused eight-item pages to overlap the footer while three-item
        # pages were crammed into the top half.  The expanded treatment gives
        # short lists separate title/description typography; dense lists use a
        # compact, auto-fitting line without sacrificing the footer boundary.
        content_bottom = 6.72
        row_height = min(1.45, (content_bottom - top) / max(1, len(visible)))
        expanded = row_height >= 1.05
        for index, item in enumerate(visible):
            y = top + index * row_height
            self._text(
                slide, f"{index + 1:02d}", 0.78, y + 0.02, 0.55, 0.40,
                16, "accent", bold=True,
            )

            title = _item_title(item)
            description = _item_description(item)
            if expanded and title and description:
                self._text(
                    slide, title, 1.48, y, 10.45, 0.36,
                    17, "text", bold=True, min_size=14, max_lines=1,
                )
                self._text(
                    slide, description, 1.48, y + 0.40,
                    10.45, max(0.42, row_height - 0.52),
                    14, "muted", min_size=11, max_lines=3,
                )
            else:
                self._text(
                    slide, _item_text(item), 1.48, y,
                    10.45, max(0.44, row_height - 0.08),
                    17 if expanded else 14, "text",
                    min_size=11 if not expanded else 13,
                    max_lines=3 if expanded else 2,
                )
            if index < len(visible) - 1:
                self._line(
                    slide, 1.48, y + row_height - 0.08,
                    11.95, y + row_height - 0.08, "line", 0.75
                )

    def _layout_two_column(self, slide, spec) -> None:
        """双栏对照版式：左栏"关键要点"、右栏"落地行动"（可由 column_titles 覆盖）。

        未显式给出左右内容时按顺序对半分栏；每栏最多显示 4 条（超出部分由页数处理阶段
        合并成摘要，渲染层不再截断）。
        """

        items = _items(spec)
        left = list(spec.get("left") or [])
        right = list(spec.get("right") or [])
        if not left and not right:
            midpoint = math.ceil(len(items) / 2)
            left, right = items[:midpoint], items[midpoint:]
        headings = spec.get("column_titles") or [
            spec.get("left_title") or "关键要点",
            spec.get("right_title") or "落地行动",
        ]
        self._column(slide, 0.68, 1.72, 5.72, 4.95, headings[0], left, "accent")
        self._column(slide, 6.82, 1.72, 5.80, 4.95, headings[1], right, "accent2")

    def _column(self, slide, x, y, width, height, heading, items, color):
        """在给定矩形内画一栏（顶部色条 + 标题 + 最多 4 条圆点要点），供双栏版式复用。"""

        from pptx.enum.shapes import MSO_SHAPE

        self._shape(
            slide, MSO_SHAPE.RECTANGLE,
            x, y, width, 0.08, color, line=None,
        )
        self._text(
            slide, str(heading), x, y + 0.24, width - 0.2, 0.48,
            18, color, bold=True, min_size=15, max_lines=2,
        )
        values = [_item_text(item) for item in items if _item_text(item)]
        for index, value in enumerate(values[:4]):
            self._text(
                slide, "•", x + 0.02, y + 1.02 + index * 0.82,
                0.28, 0.32, 17, color, bold=True,
            )
            self._text(
                slide, value, x + 0.38, y + 0.92 + index * 0.82,
                width - 0.58, 0.65, 16, "text",
                min_size=13, max_lines=2,
            )

    def _layout_cards(self, slide, spec) -> None:
        """卡片版式：最多 4 张卡片，按模板 ``design.cards`` 风格（press/editorial/cinematic/
        软卡片等）切换为竖条、通栏或 2×2 栅格；每张卡片渲染标题 + 描述，缺标题时补"要点 N"。"""

        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)[:4]
        count = max(1, len(items))
        top = self._content_top(spec)
        variant = str(self.design.get("cards") or "soft")
        if variant == "press":
            width = 11.9 / count
            for index, item in enumerate(items):
                x = 0.68 + index * width
                if index:
                    self._line(slide, x, top, x, top + 4.72, "line", 1)
                self._text(slide, f"{index + 1:02d}", x + 0.18, top + 0.12, width - 0.35, 0.52, 28, "accent", bold=True)
                self._text(slide, _item_title(item), x + 0.18, top + 0.88, width - 0.42, 0.78, 18, "text", bold=True, min_size=14, max_lines=2)
                self._text(slide, _item_description(item), x + 0.18, top + 1.95, width - 0.42, 1.95, 13, "muted", min_size=11, max_lines=7)
            return
        if variant == "editorial":
            for index, item in enumerate(items):
                row, col = divmod(index, 2)
                x, y = 0.72 + col * 6.0, top + row * 2.4
                self._line(slide, x, y, x + 5.62, y, "accent" if index % 2 == 0 else "accent2", 2)
                self._text(slide, _item_title(item), x, y + 0.30, 5.5, 0.52, 19, "text", bold=True)
                self._text(slide, _item_description(item), x, y + 1.02, 5.45, 0.88, 13, "muted", min_size=11, max_lines=4)
            return
        if count <= 2:
            cols, rows = count, 1
        else:
            cols, rows = 2, 2
        if variant == "cinematic":
            cols, rows = 1, count
        gap_x, gap_y = 0.28, 0.28
        width = (12.05 - gap_x * (cols - 1)) / cols
        height = (4.92 - gap_y * (rows - 1)) / rows
        for index, item in enumerate(items):
            row, col = divmod(index, cols)
            x = 0.64 + col * (width + gap_x)
            y = top + row * (height + gap_y)
            rounded = variant in {"soft", "glass", "organic", "clinical", "dark_tiles"}
            shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
            fill_key = "accent" if variant == "pop" and index % 2 == 0 else "accent2" if variant == "pop" else "surface"
            if variant == "brutal":
                self._shape(slide, MSO_SHAPE.RECTANGLE, x + 0.10, y + 0.10, width, height, "accent2", line="text")
            self._shape(slide, shape_type, x, y, width, height, fill_key, line="accent" if variant in {"neon", "schematic"} else "line")
            if variant in {"paper", "academic"}:
                self._line(slide, x + 0.28, y + 0.78, x + width - 0.28, y + 0.78, "line", 1)
            elif variant == "organic":
                self._shape(slide, MSO_SHAPE.OVAL, x + width - 0.72, y + 0.28, 0.36, 0.36, "accent2", line=None)
            elif variant == "industrial":
                self._shape(slide, MSO_SHAPE.RECTANGLE, x, y, width, 0.12, "accent" if index % 2 == 0 else "accent2", line=None)
            elif variant == "luxury":
                self._line(slide, x + 0.28, y + 0.35, x + width - 0.28, y + 0.35, "accent", 1)
            elif variant == "cinematic":
                self._shape(slide, MSO_SHAPE.RECTANGLE, x, y, 0.16, height, "accent" if index % 2 == 0 else "accent2", line=None)
            else:
                self._shape(slide, MSO_SHAPE.RECTANGLE, x, y, 0.09, height, "accent" if index % 2 == 0 else "accent2", line=None)
            text_color = "background" if variant == "pop" else "text"
            muted_color = "background" if variant == "pop" else "muted"
            self._text(
                slide, _item_title(item) or f"要点 {index + 1}",
                x + 0.32, y + 0.30, width - 0.58, 0.45,
                17, text_color, bold=True, min_size=14, max_lines=2,
            )
            self._text(
                slide, _item_description(item) or _item_text(item),
                x + 0.32, y + 0.95, width - 0.60, height - 1.18,
                14, muted_color, min_size=11, max_lines=5,
            )

    def _layout_metrics(self, slide, spec) -> None:
        """指标版式：最多 4 个指标，按 ``design.metrics`` 风格渲染（hero 主指标 + 次要指标、
        tiles 平铺、poster 巨型数字、ledger 台账行、bubbles 气泡、vertical 柱高等）。

        数值一律来自 spec 的 value/number（缺值时由 ``_item_value`` 回退到标题），
        本层不做任何数值推导，避免把标题当数字展示。
        """

        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)[:4]
        top = self._content_top(spec) + 0.35
        count = max(1, len(items))
        variant = str(self.design.get("metrics") or "columns")
        if variant == "hero" and items:
            first, rest = items[0], items[1:]
            self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 0.68, top, 6.05, 4.45, "surface", line="line")
            self._text(slide, _item_value(first), 1.02, top + 0.72, 5.3, 1.35, 58, "accent", bold=True, min_size=36, max_lines=2)
            self._text(slide, _item_title(first), 1.05, top + 2.25, 5.15, 0.52, 18, "text", bold=True)
            self._text(slide, _item_description(first), 1.05, top + 3.03, 5.15, 0.75, 13, "muted", max_lines=3)
            for index, item in enumerate(rest):
                y = top + index * (4.45 / max(1, len(rest)))
                self._text(slide, _item_value(item), 7.28, y + 0.25, 2.1, 0.65, 30, "accent2" if index % 2 else "text", bold=True)
                self._text(slide, _item_title(item), 9.45, y + 0.30, 2.65, 0.42, 15, "text", bold=True)
                self._text(slide, _item_description(item), 9.45, y + 0.80, 2.75, 0.58, 11, "muted", max_lines=3)
                if index < len(rest) - 1:
                    self._line(slide, 7.28, y + 1.42, 12.22, y + 1.42, "line", 1)
            return
        if variant == "tiles":
            for index, item in enumerate(items):
                row, col = divmod(index, 2)
                x, y = 0.68 + col * 6.0, top + row * 2.18
                self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 5.72, 1.92, "surface", line="line")
                self._text(slide, _item_value(item), x + 0.35, y + 0.32, 2.25, 0.75, 35, "accent" if index % 2 == 0 else "accent2", bold=True)
                self._text(slide, _item_title(item), x + 2.72, y + 0.34, 2.55, 0.42, 15, "text", bold=True)
                self._text(slide, _item_description(item), x + 2.72, y + 0.92, 2.62, 0.58, 11, "muted", max_lines=3)
            return
        if variant == "poster" and items:
            first = items[0]
            self._text(slide, _item_value(first), 0.68, top + 0.15, 7.2, 2.0, 78, "accent", bold=True, min_size=48, max_lines=2)
            self._text(slide, _item_title(first), 0.78, top + 2.22, 6.8, 0.52, 19, "text", bold=True)
            self._text(slide, _item_description(first), 0.78, top + 2.92, 6.5, 0.62, 13, "muted", max_lines=3)
            for index, item in enumerate(items[1:]):
                x = 8.0
                y = top + index * 1.42
                self._text(slide, _item_value(item), x, y + 0.10, 1.75, 0.62, 28, "text", bold=True)
                self._text(slide, _item_title(item), x + 1.88, y + 0.18, 2.18, 0.40, 14, "text", bold=True)
                self._line(slide, x, y + 1.08, 12.22, y + 1.08, "line", 1)
            return
        if variant == "ledger":
            for index, item in enumerate(items):
                y = top + index * 1.03
                self._text(slide, f"{index + 1:02d}", 0.72, y + 0.08, 0.62, 0.42, 16, "accent", bold=True)
                self._text(slide, _item_title(item), 1.58, y + 0.06, 4.4, 0.40, 15, "text", bold=True)
                self._text(slide, _item_description(item), 5.35, y + 0.08, 4.45, 0.42, 12, "muted", max_lines=2)
                self._text(slide, _item_value(item), 10.05, y - 0.02, 2.05, 0.62, 27, "accent2" if index % 2 else "accent", bold=True, align="right")
                self._line(slide, 1.58, y + 0.73, 12.12, y + 0.73, "line", 1)
            return
        if variant == "bubbles":
            centers = ((2.0, 3.65), (5.1, 3.0), (8.35, 3.85), (11.2, 3.05))
            for index, item in enumerate(items):
                cx, cy = centers[index]
                diameter = 2.0 if index % 2 == 0 else 1.75
                x, y = cx - diameter / 2, cy - diameter / 2
                self._shape(slide, MSO_SHAPE.OVAL, x, y, diameter, diameter, "accent" if index % 2 == 0 else "accent2", line="text")
                self._text(slide, _item_value(item), x + 0.15, y + 0.48, diameter - 0.3, 0.62, 28, "background", bold=True, align="center")
                self._text(slide, _item_title(item), x - 0.15, y + diameter + 0.16, diameter + 0.3, 0.42, 13, "text", bold=True, align="center")
                description = _item_description(item)
                if description and description != _item_title(item):
                    self._text(
                        slide, description,
                        x - 0.28, y + diameter + 0.62,
                        diameter + 0.56, 0.68,
                        9, "muted", min_size=8, max_lines=3, align="center",
                    )
            return
        if variant == "vertical":
            width = 11.88 / count
            max_height = 3.45
            for index, item in enumerate(items):
                x = 0.72 + index * width
                bar_h = max_height * (0.58 + index * 0.12)
                y = top + 4.0 - bar_h
                self._shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.18, y, width - 0.38, bar_h, "surface", line="line")
                self._text(slide, _item_value(item), x + 0.30, y + 0.45, width - 0.62, 0.72, 31, "accent" if index % 2 == 0 else "accent2", bold=True, align="center")
                self._text(slide, _item_title(item), x + 0.28, top + 4.15, width - 0.58, 0.42, 13, "text", bold=True, align="center")
            return
        if variant == "blueprint":
            for index, item in enumerate(items):
                x = 0.70 + index * (11.9 / count)
                width = 11.5 / count
                self._shape(slide, MSO_SHAPE.RECTANGLE, x, top + 0.45, width, 3.4, "background", line="accent")
                self._text(slide, f"KPI-{index + 1:02d}", x + 0.18, top + 0.66, width - 0.36, 0.28, 10, "accent", bold=True)
                self._text(slide, _item_value(item), x + 0.18, top + 1.25, width - 0.36, 0.78, 31, "text", bold=True)
                self._text(slide, _item_title(item), x + 0.18, top + 2.28, width - 0.36, 0.45, 13, "text", bold=True)
                self._text(slide, _item_description(item), x + 0.18, top + 2.82, width - 0.36, 0.62, 10, "muted", max_lines=3)
            return
        width = 11.95 / count
        for index, item in enumerate(items):
            x = 0.66 + index * width
            if index:
                self._line(slide, x, top + 0.2, x, top + 3.75, "line", 1)
            self._text(
                slide, _item_value(item), x + 0.22, top + 0.55,
                width - 0.45, 1.10, 36, "accent" if index == 0 else "text",
                bold=True, min_size=25, max_lines=2,
            )
            self._text(
                slide, _item_title(item) or _item_description(item),
                x + 0.24, top + 1.82, width - 0.46, 0.58,
                15, "text", bold=True, min_size=12, max_lines=2,
            )
            description = _item_description(item)
            if description and description != _item_title(item):
                self._text(
                    slide, description, x + 0.24, top + 2.52,
                    width - 0.5, 0.95, 12, "muted",
                    min_size=10, max_lines=3,
                )

    def _layout_comparison(self, slide, spec) -> None:
        """对比版式：左右两侧对照（左右内容/标题同 ``_layout_two_column``，配色区分两侧）。"""
        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)
        nested = (
            items[0]
            if len(items) == 1
            and isinstance(items[0], dict)
            and str(items[0].get("type") or "").lower() == "comparison"
            else {}
        )
        left = spec.get("left") or nested.get("left") or (items[0] if items else {})
        right = spec.get("right") or nested.get("right") or (items[1] if len(items) > 1 else {})
        top = self._content_top(spec)
        variant = str(self.design.get("comparison") or "split")
        if variant == "rules":
            self._line(slide, 6.66, top, 6.66, top + 4.85, "line", 1.5)
        elif variant == "notes":
            left_note = self._shape(slide, MSO_SHAPE.RECTANGLE, 0.78, top + 0.10, 5.55, 4.55, "surface", line="line")
            right_note = self._shape(slide, MSO_SHAPE.RECTANGLE, 7.02, top + 0.05, 5.45, 4.55, "surface", line="line")
            left_note.rotation = -1.5
            right_note.rotation = 1.2
        for index, (data, x, color, label) in enumerate([
            (left, 0.68, "accent", spec.get("left_title") or "方案 A"),
            (right, 6.78, "accent2", spec.get("right_title") or "方案 B"),
        ]):
            if variant not in {"rules", "notes", "ink"}:
                shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if variant in {"split", "cards", "organic", "clinical"} else MSO_SHAPE.RECTANGLE
                fill_key = color if variant == "pop" else "surface"
                if variant == "boxed":
                    self._shape(slide, MSO_SHAPE.RECTANGLE, x + 0.10, top + 0.10, 5.76, 4.78, "accent2" if index == 0 else "accent", line="text")
                self._shape(slide, shape_type, x, top, 5.86, 4.88, fill_key, line="accent" if variant == "technical" else "line")
            if variant == "technical":
                self._text(slide, f"PATH-{index + 1:02d}", x + 4.15, top + 0.28, 1.25, 0.24, 9, color, bold=True, align="right")
            elif variant == "organic":
                self._shape(slide, MSO_SHAPE.OVAL, x + 4.95, top + 0.32, 0.42, 0.42, color, line=None)
            elif variant == "clinical":
                self._shape(slide, MSO_SHAPE.RECTANGLE, x + 5.1, top + 0.28, 0.10, 0.48, color, line=None)
                self._shape(slide, MSO_SHAPE.RECTANGLE, x + 4.91, top + 0.47, 0.48, 0.10, color, line=None)
            self._shape(slide, MSO_SHAPE.RECTANGLE, x, top, 5.86, 0.10, color, line=None)
            title = _item_title(data) or str(label)
            title_color = "background" if variant == "pop" else color
            body_color = "background" if variant == "pop" else "text"
            self._text(
                slide, title, x + 0.36, top + 0.40, 5.1, 0.48,
                19, title_color, bold=True,
            )
            values = _as_list(
                data.get("items") if isinstance(data, dict) else data
            )
            if not values and isinstance(data, dict):
                values = _as_list(data.get("text") or data.get("content"))
            if not values and isinstance(data, dict):
                values = _as_list(
                    data.get("description")
                    or data.get("summary")
                    or data.get("value")
                )
            for row, value in enumerate(values[:5]):
                self._text(
                    slide, "✓", x + 0.38, top + 1.22 + row * 0.66,
                    0.35, 0.32, 15, title_color, bold=True,
                )
                self._text(
                    slide, _item_text(value), x + 0.82,
                    top + 1.12 + row * 0.66, 4.55, 0.52,
                    14, body_color, min_size=11, max_lines=2,
                )

    def _layout_timeline(self, slide, spec) -> None:
        """时间线版式：按条目顺序沿横轴排节点，渲染步骤标题与说明（最多 5 个节点）。"""
        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)
        if len(items) == 1 and isinstance(items[0], dict):
            nested_steps = items[0].get("steps")
            if nested_steps is None and isinstance(items[0].get("value"), list):
                nested_steps = items[0].get("value")
            if nested_steps is None and isinstance(items[0].get("data"), list):
                nested_steps = items[0].get("data")
            if nested_steps:
                items = nested_steps
        items = items[:5]
        variant = str(self.design.get("timeline") or "line")
        if variant in {"blocks", "steps", "pills", "film"}:
            count = max(1, len(items))
            gap = 0.18
            width = (11.9 - gap * (count - 1)) / count
            top = self._content_top(spec) + 0.62
            for index, item in enumerate(items):
                x = 0.70 + index * (width + gap)
                if variant == "steps":
                    y = top + (index % 2) * 0.62
                    shape_type = MSO_SHAPE.CHEVRON
                elif variant == "pills":
                    y = top + 0.78
                    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE
                else:
                    y = top + 0.35
                    shape_type = MSO_SHAPE.RECTANGLE
                fill_key = "accent" if variant == "film" and index == 0 else "surface"
                self._shape(slide, shape_type, x, y, width, 2.55 if variant != "film" else 3.15, fill_key, line="accent" if variant == "film" else "line")
                number_color = "background" if fill_key == "accent" else "accent"
                self._text(slide, f"{index + 1:02d}", x + 0.20, y + 0.22, width - 0.40, 0.40, 18, number_color, bold=True)
                self._text(slide, _item_title(item) or _item_text(item), x + 0.20, y + 0.88, width - 0.42, 0.62, 14, "background" if fill_key == "accent" else "text", bold=True, min_size=11, max_lines=3)
                self._text(slide, _item_description(item), x + 0.20, y + 1.66, width - 0.42, 0.58, 10, "background" if fill_key == "accent" else "muted", min_size=9, max_lines=3)
            return
        top = self._content_top(spec) + 1.12
        start_x, end_x = 1.02, 12.15
        axis_color = "accent" if variant in {"dimension", "brush"} else "line"
        self._line(slide, start_x, top + 0.52, end_x, top + 0.52, axis_color, 2 if variant != "brush" else 4)
        for index, item in enumerate(items):
            x = start_x + (
                (end_x - start_x) * index / max(1, len(items) - 1)
            )
            node_type = MSO_SHAPE.DIAMOND if variant == "ticks" else MSO_SHAPE.OVAL
            node_size = 0.34 if variant == "dimension" else 0.44
            self._shape(slide, node_type, x - node_size / 2, top + 0.52 - node_size / 2, node_size, node_size, "accent" if index == 0 else "surface", line="accent")
            if variant == "dimension":
                self._line(slide, x, top + 0.12, x, top + 0.92, "accent", 0.8)
            self._text(
                slide, str(index + 1), x - 0.18, top + 0.36, 0.36, 0.24, 10,
                "background" if index == 0 else "accent",
                bold=True, align="center",
            )
            self._text(
                slide, _item_title(item) or _item_text(item),
                x - 0.88, top - 0.48 if index % 2 == 0 else top + 0.94,
                1.76, 0.55, 14, "text", bold=True,
                min_size=11, max_lines=2, align="center",
            )
            description = _item_description(item)
            if description:
                self._text(
                    slide, description,
                    x - 0.92, top - 1.12 if index % 2 == 0 else top + 1.54,
                    1.84, 0.62, 10, "muted",
                    min_size=9, max_lines=3, align="center",
                )

    def _layout_table(self, slide, spec) -> None:
        """表格版式：从 type=table 的条目取 rows，最多渲染 9 行；没有表格数据时退回正文版式。"""

        table_item = next(
            (
                item for item in _items(spec)
                if isinstance(item, dict) and item.get("type") == "table"
            ),
            {},
        )
        rows = table_item.get("rows") or spec.get("rows") or []
        if not rows:
            return self._layout_content(slide, spec)
        top = self._content_top(spec)
        self._table(slide, rows[:9], 0.70, top, 11.95, 4.95)

    def _layout_chart(self, slide, spec) -> None:
        """原生图表版式：插入 PPTX 原生图表，有 insight 时右侧加"核心洞察"栏与来源标注。

        没有图表数据时退回指标版式；原生图表 XML 需经 ``_normalize_pptx_package`` 修正
        axId 负数，否则部分 Office/WPS 会判定文件损坏。
        """

        chart_item = next(
            (
                item for item in _items(spec)
                if isinstance(item, dict) and item.get("type") == "chart"
            ),
            spec.get("chart") or {},
        )
        if not chart_item:
            return self._layout_metrics(slide, spec)
        top = self._content_top(spec)
        insight = str(
            chart_item.get("insight")
            or spec.get("insight")
            or spec.get("key_message")
            or ""
        )
        chart_width = 8.35 if insight else 11.75
        self._chart(slide, chart_item, 0.72, top, chart_width, 4.72)
        if insight:
            self._text(
                slide, "核心洞察", 9.40, top + 0.10, 2.45, 0.38,
                13, "accent", bold=True,
            )
            self._text(
                slide, insight, 9.38, top + 0.72, 2.75, 2.35,
                22, "text", bold=True, min_size=15, max_lines=5,
            )
            source = chart_item.get("source")
            if source:
                self._text(
                    slide, "来源：" + str(source),
                    9.40, top + 3.72, 2.55, 0.54,
                    10, "muted", min_size=9, max_lines=2,
                )

    def _layout_quote(self, slide, spec) -> None:
        """引语版式：大号引号 + 引言正文 + 署名线，用于客户原话或结论引用。"""

        items = _items(spec)
        quote_item = next(
            (
                item for item in items
                if isinstance(item, dict) and item.get("type") == "quote"
            ),
            items[0] if items else {},
        )
        quote = _item_text(quote_item)
        author = (
            quote_item.get("author") if isinstance(quote_item, dict) else ""
        ) or spec.get("author") or ""
        self._text(
            slide, "“", 0.72, 1.72, 1.0, 1.0,
            64, "accent", bold=True,
        )
        self._text(
            slide, quote, 1.52, 2.08, 9.85, 2.45,
            34, "text", bold=True, min_size=24, max_lines=5,
        )
        self._line(slide, 1.55, 5.05, 3.05, 5.05, "accent", 3)
        if author:
            self._text(
                slide, "— " + str(author), 3.28, 4.86, 5.2, 0.45,
                14, "muted",
            )

    def _layout_image(self, slide, spec) -> None:
        """图文版式：右侧插图 + 左侧说明文字；图片数据缺失或解码失败时退回观点页版式。"""

        image_item = next(
            (
                item for item in _items(spec)
                if isinstance(item, dict) and item.get("type") == "image"
            ),
            {},
        )
        image_data = _decode_image(image_item)
        top = self._content_top(spec)
        if image_data:
            self._picture(slide, image_data, 6.75, top, 5.72, 4.78)
            text = str(
                spec.get("statement")
                or spec.get("key_message")
                or image_item.get("caption")
                or ""
            )
            if text:
                self._text(
                    slide, text, 0.72, top + 0.38, 5.4, 2.7,
                    28, "text", bold=True, min_size=20, max_lines=5,
                )
        else:
            self._layout_statement(slide, spec)

    def _layout_matrix(self, slide, spec) -> None:
        """矩阵版式：固定 2×2 四象限，缺内容的象限仍画框并标"象限 N"，保证版式不塌陷。"""

        from pptx.enum.shapes import MSO_SHAPE

        items = _items(spec)[:4]
        top = self._content_top(spec)
        for index in range(4):
            row, col = divmod(index, 2)
            x, y = 0.72 + col * 6.0, top + row * 2.42
            self._shape(
                slide, MSO_SHAPE.RECTANGLE,
                x, y, 5.72, 2.12, "surface", line="line",
            )
            item = items[index] if index < len(items) else {}
            self._text(
                slide, _item_title(item) or f"象限 {index + 1}",
                x + 0.28, y + 0.26, 5.05, 0.42,
                16, "accent" if index in (0, 3) else "text", bold=True,
            )
            self._text(
                slide, _item_description(item) or _item_text(item),
                x + 0.28, y + 0.86, 5.05, 0.92,
                13, "muted", min_size=11, max_lines=4,
            )

    def _layout_closing(self, slide, spec) -> None:
        """空实现：结束页由 ``add_closing`` 统一生成，此处仅用于让 ``_infer_layout`` 可命中
        ``closing`` 而不至于回退到正文版式。"""

        pass

    def _shape(
        self, slide, shape_type, x, y, w, h, fill_key,
        *, line="line", radius=None,
    ):
        """画一个带主题色填充的形状（英寸坐标），``line=None`` 表示无描边。"""

        from pptx.util import Inches

        shape = slide.shapes.add_shape(
            shape_type,
            Inches(x * self.scale_x), Inches(y),
            Inches(w * self.scale_x), Inches(h),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = self.color(fill_key)
        if line:
            shape.line.color.rgb = self.color(line)
            shape.line.width = Inches(0.008)
        else:
            shape.line.fill.background()
        return shape

    def _line(self, slide, x1, y1, x2, y2, color, width_pt):
        """画一条直连接线（分隔线/引导线），宽度以磅为单位。"""

        from pptx.enum.shapes import MSO_CONNECTOR
        from pptx.util import Inches, Pt

        line = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            Inches(x1 * self.scale_x), Inches(y1),
            Inches(x2 * self.scale_x), Inches(y2),
        )
        line.line.color.rgb = self.color(color)
        line.line.width = Pt(width_pt)
        return line

    def _text(
        self, slide, text, x, y, w, h, size, color,
        *, bold=False, min_size=8, max_lines=None, align="left", spacing=0,
    ):
        """在给定矩形内放一段自适应文本（版式层唯一的文字出口）。

        字号先经 ``_fit_font_size`` 在 size→min_size 之间收敛，保证不超出 ``max_lines`` 与
        框高；中文按 1.0、非中文按 0.56 折算字宽，属于估算而非精确度量。字体按"粗体且字号
        ≥24 用标题字体，其余用正文字体"选择；``spacing`` 是字间距倍数（内部 ×100 写入 rPr，
        写入失败时忽略）。返回文本框，调用方可继续设旋转等属性。
        """

        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.util import Inches, Pt

        value = str(text or "")
        fitted = _fit_font_size(
            value, w * self.scale_x, h, size, min_size, max_lines
        )
        box = slide.shapes.add_textbox(
            Inches(x * self.scale_x), Inches(y),
            Inches(w * self.scale_x), Inches(h),
        )
        frame = box.text_frame
        frame.clear()
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = Inches(0.02)
        frame.margin_top = frame.margin_bottom = Inches(0.01)
        frame.vertical_anchor = MSO_ANCHOR.TOP
        paragraph = frame.paragraphs[0]
        paragraph.text = value
        paragraph.alignment = {
            "center": PP_ALIGN.CENTER,
            "right": PP_ALIGN.RIGHT,
        }.get(align, PP_ALIGN.LEFT)
        paragraph.space_after = Pt(0)
        paragraph.space_before = Pt(0)
        paragraph.line_spacing = 1.12
        for run in paragraph.runs:
            font_key = "title_font" if bold and fitted >= 24 else "body_font"
            run.font.name = self.typography.get(font_key) or self.typography.get(
                "body_font", "Microsoft YaHei"
            )
            run.font.size = Pt(fitted)
            run.font.bold = bold
            run.font.color.rgb = self.color(color)
            if spacing:
                try:
                    run._r.get_or_add_rPr().set("spc", str(int(spacing * 100)))
                except Exception:
                    pass
        return box

    def _table(self, slide, rows, x, y, w, h):
        """插入 PPTX 原生表格（``rows`` 第一行视为表头），列宽按各列最长文本加权分配。

        空行会被丢弃；列数取最长行的长度，短行缺列留空。原生表格在 WPS/Office 中均可编辑，
        因此优于把表格画成图片。
        """

        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.util import Inches, Pt

        rows = [list(row) for row in rows if row]
        if not rows:
            return
        cols = max(len(row) for row in rows)
        table = slide.shapes.add_table(
            len(rows), cols,
            Inches(x * self.scale_x), Inches(y),
            Inches(w * self.scale_x), Inches(h),
        ).table
        widths = _column_widths(rows, w * self.scale_x)
        for index, width in enumerate(widths):
            table.columns[index].width = Inches(width)
        for row_index, row in enumerate(rows):
            for col_index in range(cols):
                cell = table.cell(row_index, col_index)
                cell.text = str(row[col_index]) if col_index < len(row) else ""
                cell.margin_left = cell.margin_right = Inches(0.10)
                cell.margin_top = cell.margin_bottom = Inches(0.06)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                cell.fill.solid()
                cell.fill.fore_color.rgb = self.color(
                    "accent" if row_index == 0 else (
                        "surface" if row_index % 2 else "background"
                    )
                )
                for paragraph in cell.text_frame.paragraphs:
                    paragraph.alignment = (
                        PP_ALIGN.LEFT if col_index == 0 else PP_ALIGN.CENTER
                    )
                    for run in paragraph.runs:
                        run.font.name = self.typography.get(
                            "body_font", "Microsoft YaHei"
                        )
                        run.font.size = Pt(12 if row_index == 0 else 11)
                        run.font.bold = row_index == 0
                        run.font.color.rgb = self.color(
                            "background" if row_index == 0 else "text"
                        )

    def _chart(self, slide, item, x, y, w, h):
        """插入 PPTX 原生图表（柱/折线/饼等），数据一律取自 item 内的 series/categories。

        只接受本层支持的类型；生成后由 ``_normalize_pptx_package`` 统一修正图表 XML 的
        axId 取值，因此这里可以放心依赖 python-pptx 的默认属性。
        """
        from pptx.chart.data import ChartData
        from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
        from pptx.util import Inches, Pt

        categories = list(item.get("categories") or [])
        series = list(item.get("series") or [])
        if not categories or not series:
            return
        chart_data = ChartData()
        chart_data.categories = [str(value) for value in categories]
        for index, series_item in enumerate(series):
            if isinstance(series_item, dict):
                name = series_item.get("name") or f"系列 {index + 1}"
                values = series_item.get("values")
                if values is None:
                    values = series_item.get("data")
                values = values if isinstance(values, list) else []
            else:
                name, values = f"系列 {index + 1}", series_item
            normalized = [
                float(value) if value is not None else 0 for value in values
            ]
            if len(normalized) < len(categories):
                normalized.extend([0] * (len(categories) - len(normalized)))
            chart_data.add_series(str(name), normalized[:len(categories)])
        chart_type = {
            "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "horizontal_bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE_MARKERS,
            "pie": XL_CHART_TYPE.PIE,
            "doughnut": XL_CHART_TYPE.DOUGHNUT,
            "area": XL_CHART_TYPE.AREA,
        }.get(str(item.get("chart_type") or "column").lower(),
              XL_CHART_TYPE.COLUMN_CLUSTERED)
        chart = slide.shapes.add_chart(
            chart_type,
            Inches(x * self.scale_x), Inches(y),
            Inches(w * self.scale_x), Inches(h),
            chart_data,
        ).chart
        chart.has_title = False
        chart.has_legend = len(series) > 1
        if chart.has_legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False
            chart.legend.font.size = Pt(10)
        chart.chart_style = 10
        if chart_type not in {XL_CHART_TYPE.PIE, XL_CHART_TYPE.DOUGHNUT}:
            chart.value_axis.has_major_gridlines = True
            chart.value_axis.major_gridlines.format.line.color.rgb = self.color("line")
            chart.category_axis.tick_labels.font.size = Pt(10)
            chart.value_axis.tick_labels.font.size = Pt(9)
        palette = ["accent", "accent2", "muted"]
        for index, chart_series in enumerate(chart.series):
            chart_series.format.fill.solid()
            chart_series.format.fill.fore_color.rgb = self.color(
                palette[index % len(palette)]
            )

    def _picture(self, slide, image_data: bytes, x, y, w, h):
        """把图片字节插入给定框并按目标宽高比做**居中裁剪**（保留画面主体，不拉伸变形）。

        源图比例大于目标框时裁左右，小于时裁上下，裁剪量各留一半。
        """
        from PIL import Image
        from pptx.util import Inches

        stream = io.BytesIO(image_data)
        with Image.open(stream) as image:
            source_ratio = image.width / max(1, image.height)
        target_ratio = (w * self.scale_x) / h
        stream.seek(0)
        picture = slide.shapes.add_picture(
            stream,
            Inches(x * self.scale_x), Inches(y),
            Inches(w * self.scale_x), Inches(h),
        )
        if source_ratio > target_ratio:
            visible = target_ratio / source_ratio
            crop = (1 - visible) / 2
            picture.crop_left = picture.crop_right = crop
        else:
            visible = source_ratio / target_ratio
            crop = (1 - visible) / 2
            picture.crop_top = picture.crop_bottom = crop
        return picture

    def _notes(self, slide, value) -> None:
        """写入真实演讲备注；明细可能只保存在这里，失败必须中止交付以免静默丢失。"""

        text = str(value or "").strip()
        if not text:
            return
        frame = slide.notes_slide.notes_text_frame
        if frame is None:
            raise ValueError("PPT 备注页缺少正文框，无法完整保存备注和溢出明细")
        frame.text = text


def _items(spec: dict) -> list:
    """取页面的条目列表，兼容 ``items`` 与旧字段 ``content``（两者同义）。"""

    return list(spec.get("items") or spec.get("content") or [])


def _item_text(item) -> str:
    """把任意形态的条目压成一行文本（标题—描述 / 描述 / 标题 / 数值），供列表版式使用。

    当标题与描述不同时保留"标题 — 描述"形式，避免只有标题的信息量丢失。
    """

    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        direct = item.get("text") or item.get("content")
        if direct and not isinstance(direct, (dict, list)):
            return str(direct)
        title = _item_title(item).strip()
        description = _item_description(item).strip()
        if title and description and title != description:
            return f"{title} — {description}"
        value = _item_value(item).strip()
        if title and value and title != value:
            return f"{title} — {value}"
        return description or title or value
    return str(item or "")


def _item_title(item) -> str:
    """取条目标题，兼顾嵌套 value 对象（模型有时把标题放在 value.title/label/point）。"""

    if isinstance(item, dict):
        nested = item.get("value") if isinstance(item.get("value"), dict) else {}
        return str(
            item.get("title") or item.get("label") or item.get("name")
            or nested.get("title") or nested.get("label") or nested.get("name")
            or nested.get("point") or ""
        )
    return ""


def _item_description(item) -> str:
    """取条目描述，兼容 ``description/text/content/data/summary`` 及嵌套 value 字段；
    列表型描述按"；"连接后返回（不保留换行，避免撑破定高文本框）。"""

    if isinstance(item, dict):
        nested = item.get("value") if isinstance(item.get("value"), dict) else {}
        value = (
            item.get("description") or item.get("text") or item.get("content")
            or item.get("data") or item.get("summary")
            or nested.get("description") or nested.get("data")
            or nested.get("text") or nested.get("content")
            or nested.get("summary") or ""
        )
        return "；".join(str(part) for part in value) if isinstance(value, list) else str(value)
    return str(item or "")


def _item_value(item) -> str:
    """取条目数值文本，兼容嵌套 value 对象；**不做单位换算或格式化**，原样展示。"""

    if isinstance(item, dict):
        value = item.get("value")
        if isinstance(value, dict):
            value = _first_present(
                value.get("value"), value.get("number"), value.get("data"),
                value.get("title"), value.get("label"),
            )
        return str(_first_present(
            value, item.get("number"), item.get("title"), item.get("label"),
            default="",
        ))
    return str(item or "")


def _first_present(*values, default=None):
    """Return the first supplied value, preserving valid falsey values such as 0."""
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _has_metric_value(item: dict) -> bool:
    """判断条目是否带指标数值（用于区分 metrics 与 cards：没有数值就不能当指标卡）。"""

    return any(
        item.get(key) is not None and item.get(key) != ""
        for key in ("value", "number")
    )


def _as_list(value) -> list:
    """把多行字符串按行拆成列表（去掉行首的 •/- 记号），其余非空值包成单元素列表。"""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [line.strip("•- ").strip() for line in value.splitlines() if line.strip()]
    return [value]


def _fit_font_size(
    text: str,
    width_inches: float,
    height_inches: float,
    preferred: int,
    minimum: int,
    max_lines: int | None,
) -> int:
    """在 preferred→minimum 之间挑选能装进给定宽高的字号（纯估算，不做真实排版度量）。

    宽度按"中文 1.0 / 非中文 0.56"折算权重，再除以 0.94 的有效宽度系数估算每行可容纳的
    权重；高度按行高 1.24 倍字号校验，并保证不超过 ``max_lines``。逐号递减，找不到合适值
    就返回 ``minimum``（即宁可挤到最小字号，也不溢出文本框或删内容）。
    """

    if not text:
        return preferred
    weighted = sum(
        1.0 if ord(character) > 255 else 0.56
        for character in text
    )
    width_points = max(1.0, width_inches * 72 * 0.94)
    height_points = max(1.0, height_inches * 72 * 0.92)
    for size in range(preferred, minimum - 1, -1):
        estimated_lines = max(1, math.ceil(weighted * size / width_points))
        if "\n" in text:
            estimated_lines = max(estimated_lines, text.count("\n") + 1)
        if max_lines is not None and estimated_lines > max_lines:
            continue
        if estimated_lines * size * 1.24 <= height_points:
            return size
    return minimum


def _column_widths(rows: list[list], total_width: float) -> list[float]:
    """按各列最长文本（8 字符为 1 个单位，权重夹在 1.0–3.5）加权分配表格总宽。"""

    cols = max(len(row) for row in rows)
    weights = []
    for col in range(cols):
        maximum = max(
            len(str(row[col])) if col < len(row) else 0
            for row in rows
        )
        weights.append(min(3.5, max(1.0, maximum / 8)))
    total = sum(weights)
    return [total_width * weight / total for weight in weights]


def _decode_image(item: dict) -> bytes | None:
    """解出条目里的内联图片（``data:image/...;base64,`` 或 ``base64:`` 前缀）。

    只接受 base64 内联数据：**不允许 http(s) 直链**，避免渲染进程发起外部请求或被 SSRF
    利用；解码失败或不是字符串时返回 None，由调用方回退到文字版式。
    """

    value = item.get("image") or item.get("src") or item.get("data")
    if not isinstance(value, str):
        return None
    try:
        if value.startswith("data:") and "," in value:
            return base64.b64decode(value.split(",", 1)[1])
        if value.startswith("base64:"):
            return base64.b64decode(value[7:])
    except Exception:
        return None
    return None
