# -*- coding: utf-8 -*-
"""Document / Presentation / Spreadsheet / PDF renderers.

The LLM produces a JSON *Spec*, the renderer owns all layout/formatting —
never let the LLM touch raw office formats directly (§23/§24 doctrine).
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Dict, List


# ------------------------------------------------------------------
# DOCX
# ------------------------------------------------------------------
def _item_text(item: Any) -> str:
    """取列表项的文本（兼容 dict 的 text/content/title/description 与裸字符串）。"""

    if isinstance(item, dict):
        return str(item.get("text") or item.get("content") or item.get("title") or item.get("description") or "")
    return str(item or "")


def _block_items(block: dict) -> list[str]:
    """取块内的条目列表；没有 items 时把块自身文本当成单条（bullet/numbered 共用）。"""

    items = block.get("items")
    if isinstance(items, list):
        values = [_item_text(item) for item in items]
        return [value for value in values if value]
    text = _item_text(block)
    return [text] if text else []


def _block_table_rows(block: dict) -> List[list]:
    """把表格块的多种书写方式（table 对象/二维数组/字典行）统一成"表头 + 数据行"的二维列表。

    字典行按 ``headers`` 取值（缺省用首个字典的键），并在需要时把表头补成第一行，
    保证 DOCX/PDF 两个渲染分支拿到同一形状而不必各自兼容。
    """

    table = block.get("table")
    headers = block.get("headers") or []
    rows = block.get("rows") or []
    if isinstance(table, dict):
        headers = table.get("headers") or headers
        rows = table.get("rows") or rows
    elif isinstance(table, list) and not rows:
        rows = table
    if not isinstance(rows, list):
        return []
    normalized: List[list] = []
    if rows and all(isinstance(row, dict) for row in rows):
        headers = list(headers) or list(rows[0].keys())
        normalized = [[row.get(column, "") for column in headers] for row in rows]
    else:
        normalized = [list(row) if isinstance(row, (list, tuple)) else [row] for row in rows]
    header_row = list(headers) if isinstance(headers, (list, tuple)) else []
    if header_row and (not normalized or normalized[0] != header_row):
        normalized.insert(0, header_row)
    return normalized


def render_document(spec: dict) -> bytes:
    """把文档 spec 渲染为 .docx 字节（标题 + 署名 + 各章节块），最后统一规范字体。

    仅处理语义块（标题/段落/列表/引用/表格/代码/分页/图片），导出前调用
    ``normalize_office_fonts`` 统一字体，避免客户端缺字体导致排版错乱。
    """

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document()
    title = spec.get("title") or "文档"
    doc.add_heading(title, level=0)
    meta = spec.get("metadata") or {}
    if meta.get("author"):
        p = doc.add_paragraph(meta["author"])
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for section in spec.get("sections") or []:
        sec_title = section.get("title")
        if sec_title:
            doc.add_heading(sec_title, level=1)
        for block in section.get("blocks") or []:
            _add_docx_block(doc, block)
    buf = io.BytesIO()
    doc.save(buf)
    from ...common.artifact_fonts import normalize_office_fonts
    return normalize_office_fonts(buf.getvalue(), ".docx")


def _add_docx_block(doc, block: dict):
    """按 ``block["type"]`` 往文档追加一个块；未知类型直接落成段落而不是丢弃内容。"""

    kind = block.get("type", "p")
    text = str(block.get("text") or block.get("content") or "")
    if kind in ("h1", "h2", "h3", "h4", "h5"):
        doc.add_heading(text or block.get("title", ""), level=int(kind[1]))
    elif kind == "p":
        doc.add_paragraph(text)
    elif kind == "bullet":
        for item in _block_items(block):
            doc.add_paragraph(item, style="List Bullet")
    elif kind == "numbered":
        for item in _block_items(block):
            doc.add_paragraph(item, style="List Number")
    elif kind == "quote":
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        p = doc.add_paragraph(text)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif kind == "table":
        _add_table(doc, _block_table_rows(block))
    elif kind == "code":
        p = doc.add_paragraph(text)
        for run in p.runs:
            run.font.name = "Consolas"
    elif kind == "page_break":
        doc.add_page_break()
    elif kind == "image":
        _maybe_add_image_docx(doc, block)
    else:
        doc.add_paragraph(str(block))


def _add_table(doc, rows: List[list]):
    """往 DOCX 插入原生表格；列数取最长行，短行缺列留空（不补 0，避免伪造数值）。"""

    if not rows:
        return
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.style = "Light Grid Accent 1"
    for ri, row in enumerate(rows):
        for ci in range(ncols):
            cell = table.cell(ri, ci)
            cell.text = str(row[ci]) if ci < len(row) else ""


def _maybe_add_image_docx(doc, block: dict):
    """往 DOCX 插入图片（图注可选）。

    宽度按"页面可用宽度"与"按 8 英寸等比缩放"取小值，因此竖图不会被拉成横版；
    图片字节统一由 ``document_images.image_bytes`` 解析（支持内联 base64 与受控本地路径），
    该函数抛错时由调用方决定是否降级，这里不吞异常以免生成一张缺图的文档。
    """

    from .document_images import image_bytes
    from PIL import Image
    from docx.shared import Inches
    data = image_bytes(block)
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        max_width = (doc.sections[-1].page_width - doc.sections[-1].left_margin
                     - doc.sections[-1].right_margin) / 914400
        inches = min(max_width, 8.0 * width / max(height, 1))
    doc.add_picture(io.BytesIO(data), width=Inches(inches))
    if block.get("caption"):
        doc.add_paragraph(str(block["caption"]), style="Caption")


# ------------------------------------------------------------------
# PPTX
# ------------------------------------------------------------------
def _render_uploaded_presentation(spec: dict, template_path: str) -> bytes:
    """Create slides from an uploaded deck while preserving its masters/layouts/theme.

    客户模板路径（与内置渲染器的根本差异）：以客户上传的 PPTX 为底稿 ``Presentation(template_path)``
    新建，**母版、版式、主题与品牌元素原样保留**，因此这里不能像内置渲染器那样自绘一切：

    1. 页数先与内置一致地处理（``_fit_slides_to_requested_total`` + 缺结束页时补一页），
       页数不匹配直接抛 ``ValueError``（含"请提供恰好 N 个内容页"的修复指引）；
    2. 版式可用性探测：有"含 TITLE 且占位符 ≥2"的版式时走占位符填充（原生排版、可继续编辑），
       否则用 ``extract_visual_tokens`` 从前 4 页抽取背景/强调色/正文字体/品牌名，在空白版式上
       自绘——即"客户模板无可用版式"时的降级路径，视觉近似而非精确复刻；
    3. 内容装配后清理未使用的占位符、必要时把正文占位符合并成一条，表格用原生 table 形状插入
       （比图片更利于后续编辑）。

    返回 PPTX 字节；不写盘、不改动上传的模板文件。
    """
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_COLOR_TYPE, MSO_FILL
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt
    from ..presentation.engine import (
        _compact_slides_for_exact_count,
        _fit_slides_to_requested_total,
        _infer_layout,
        _requested_page_count,
    )

    spec = dict(spec or {})
    include_cover = bool(spec.get("include_cover", True))
    content_slides = [dict(item or {}) for item in (spec.get("slides") or [])]
    requested_pages = _requested_page_count(spec.get("page_count"))
    if requested_pages:
        content_slides = _fit_slides_to_requested_total(
            content_slides,
            requested_pages,
            include_cover=include_cover,
            include_closing=bool(spec.get("include_closing", True)),
        )
        content_slides = _compact_slides_for_exact_count(content_slides)
    if spec.get("include_closing", True) and (
        not content_slides or _infer_layout(content_slides[-1]) != "closing"
    ):
        content_slides.append({
            "layout": "closing",
            "title": str(spec.get("closing_title") or "谢谢"),
            "items": [str(spec.get("closing_subtitle") or "期待进一步交流")],
            "notes": spec.get("closing_notes"),
        })
    spec["slides"] = content_slides
    actual_pages = int(include_cover) + len(content_slides)
    if requested_pages and actual_pages != requested_pages:
        raise ValueError(
            f"指定总页数为 {requested_pages} 页（含已启用的封面与结束页），"
            f"当前内容会生成 {actual_pages} 页，请核对正文与封尾开关。"
        )
    prs = Presentation(template_path)

    def write_notes(slide, value):
        """客户模板也必须保留真实备注，保证表格溢出明细没有被静默丢弃。"""
        text = str(value or "").strip()
        if not text:
            return
        frame = slide.notes_slide.notes_text_frame
        if frame is None:
            raise ValueError("客户模板备注页缺少正文框，无法保存溢出明细")
        frame.text = text

    def hex_color(color, default: str) -> str:
        """安全取色：非 RGB 颜色或读取失败时返回 ``default``。"""
        try:
            if color.type == MSO_COLOR_TYPE.RGB and color.rgb:
                return str(color.rgb)
        except Exception:
            pass
        return default

    def extract_visual_tokens() -> dict:
        """扫描客户模板前几页提取视觉变量（背景/强调色/正文色/字体/品牌名）。

        仅当母版版式不可用时用于降级自绘；全部读取都做异常保护，取值失败即用默认值，
        因此损坏的模板不会让渲染直接失败。
        """
        background = "FAF7F2"
        accent = "9D2C2C"
        text = "2A2020"
        muted = "6E6060"
        font_name = "Microsoft YaHei"
        brand = "客户模板"
        if prs.slides:
            first = prs.slides[0]
            try:
                fill = first.background.fill
                if fill.type == MSO_FILL.SOLID:
                    background = hex_color(fill.fore_color, background)
            except Exception:
                pass
            fill_candidates = []
            text_candidates = []
            brand_candidates = []
            for slide_index in range(min(4, len(prs.slides))):
                slide = prs.slides[slide_index]
                for shape in slide.shapes:
                    try:
                        if shape.fill.type == MSO_FILL.SOLID:
                            value = hex_color(shape.fill.fore_color, "")
                            if value and value not in (background, "FFFFFF", "000000"):
                                fill_candidates.append(value)
                    except Exception:
                        pass
                    if not getattr(shape, "has_text_frame", False) or not (shape.text or "").strip():
                        continue
                    max_size = 0
                    chosen_color = ""
                    chosen_font = ""
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            if run.font.size:
                                max_size = max(max_size, run.font.size.pt)
                            if run.font.color:
                                chosen_color = hex_color(run.font.color, chosen_color)
                            if run.font.name:
                                chosen_font = run.font.name
                    text_candidates.append((max_size, chosen_color, chosen_font, shape.text.strip()))
                    if shape.top < prs.slide_height * 0.24 and len(shape.text.strip()) <= 30 and max_size <= 24:
                        brand_candidates.append((shape.top, shape.left, shape.text.strip()))
            if fill_candidates:
                accent = fill_candidates[0]
            if text_candidates:
                largest = max(text_candidates, key=lambda item: item[0])
                text = largest[1] or text
                font_name = largest[2] or font_name
            if brand_candidates:
                brand = sorted(brand_candidates)[0][2]
        return {"background": background, "surface": "FFFFFF", "accent": accent,
                "text": text, "muted": muted, "font": font_name, "brand": brand}

    def has_usable_layouts() -> bool:
        """判断客户模板是否提供了"带标题占位符、占位符 ≥2"的可用版式。

        这项探测决定走哪条渲染分支：有可用版式就用原生占位符填充（保留客户排版），
        没有才降级为按视觉变量自绘。
        """
        for layout in prs.slide_layouts:
            types = {str(ph.placeholder_format.type) for ph in layout.placeholders}
            if len(layout.placeholders) >= 2 and any("TITLE" in item for item in types):
                return True
        return False

    def render_from_visual_tokens() -> bytes:
        """降级渲染路径：清空模板原有页面后，用提取出的视觉变量在空白版式上自绘整套幻灯片。

        适用于"客户模板有母版但没有可用标题版式"的情况；产物视觉近似客户风格，
        但**不沿用其版式结构**（因此不如占位符路径保真）。
        """
        tokens = extract_visual_tokens()
        while len(prs.slides):
            slide_id = prs.slides._sldIdLst[0]
            prs.part.drop_rel(slide_id.rId)
            del prs.slides._sldIdLst[0]
        blank = prs.slide_layouts[-1]
        width, height = prs.slide_width, prs.slide_height

        def rgb(value: str) -> RGBColor:
            """把 ``#RRGGBB`` 十六进制串转成 RGBColor（提取出的视觉变量用）。"""
            value = value.lstrip("#")
            return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

        def set_background(slide) -> None:
            """用提取出的客户模板背景色填充整页底色。"""
            fill = slide.background.fill
            fill.solid(); fill.fore_color.rgb = rgb(tokens["background"])

        def add_text(slide, value: str, left: float, top: float, w: float, h: float,
                     size: int, *, color: str = "text", bold: bool = False,
                     alignment=PP_ALIGN.LEFT):
            """在相对坐标（0–1 比例 × 客户画布尺寸）处插入文本框，自动换行。"""
            box = slide.shapes.add_textbox(int(width * left), int(height * top), int(width * w), int(height * h))
            frame = box.text_frame; frame.clear(); frame.word_wrap = True
            paragraph = frame.paragraphs[0]; paragraph.text = str(value or ""); paragraph.alignment = alignment
            for run in paragraph.runs:
                run.font.name = tokens["font"]; run.font.size = Pt(size); run.font.bold = bold
                run.font.color.rgb = rgb(tokens.get(color, color))
            return box

        def add_brand(slide, page: int | None = None) -> None:
            """在页角写入从客户模板提取的品牌名与页码（降级自绘路径）。"""
            add_text(slide, tokens["brand"], 0.064, 0.055, 0.24, 0.05, 13, color="accent", bold=True)
            rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, int(width * 0.064), int(height * 0.115), int(width * 0.09), int(height * 0.006))
            rule.fill.solid(); rule.fill.fore_color.rgb = rgb(tokens["accent"]); rule.line.fill.background()
            if page is not None:
                add_text(slide, f"{page:02d}", 0.92, 0.94, 0.04, 0.03, 9, color="muted", alignment=PP_ALIGN.RIGHT)

        if include_cover:
            cover = prs.slides.add_slide(blank); set_background(cover); add_brand(cover)
            add_text(cover, spec.get("title") or "演示文稿", 0.07, 0.28, 0.72, 0.25, 42, bold=True)
            if spec.get("subtitle"):
                add_text(cover, spec["subtitle"], 0.072, 0.59, 0.68, 0.09, 20, color="muted")
            add_text(cover, "由客户上传模板生成", 0.072, 0.83, 0.4, 0.04, 11, color="accent", bold=True)
            write_notes(cover, spec.get("cover_notes"))

        for page, slide_spec in enumerate(spec.get("slides") or [], start=1 + int(include_cover)):
            slide = prs.slides.add_slide(blank); set_background(slide); add_brand(slide, page)
            add_text(slide, slide_spec.get("title") or f"第 {page} 页", 0.064, 0.15, 0.84, 0.1, 29, bold=True)
            items = slide_spec.get("items") or slide_spec.get("content") or []
            texts, tables = [], []
            for item in items:
                if isinstance(item, dict) and item.get("type") == "table" and item.get("rows"):
                    tables.append(item["rows"])
                elif isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict):
                    texts.append(item.get("text") or item.get("content") or item.get("title") or "")
            top = 0.30
            for index, value in enumerate([item for item in texts if item][:6]):
                add_text(slide, "•  " + value, 0.08, top + index * 0.095, 0.78, 0.075, 18)
            if tables:
                rows = tables[0][:9 if requested_pages else 8]
                cols = max(len(row) for row in rows)
                table_top = 0.53 if texts else 0.29
                table = slide.shapes.add_table(len(rows), cols, int(width * 0.08), int(height * table_top), int(width * 0.84), int(height * min(0.37, 0.06 * len(rows)))).table
                for ri, row in enumerate(rows):
                    for ci in range(cols):
                        cell = table.cell(ri, ci); cell.text = str(row[ci]) if ci < len(row) else ""
                        cell.fill.solid(); cell.fill.fore_color.rgb = rgb(tokens["accent"] if ri == 0 else tokens["surface"])
                        for paragraph in cell.text_frame.paragraphs:
                            for run in paragraph.runs:
                                run.font.name = tokens["font"]; run.font.size = Pt(12 if ri else 13)
                                run.font.bold = ri == 0; run.font.color.rgb = rgb("FFFFFF" if ri == 0 else tokens["text"])
            write_notes(slide, slide_spec.get("notes"))
        output = io.BytesIO(); prs.save(output); return output.getvalue()

    if not has_usable_layouts():
        return render_from_visual_tokens()

    # Keep every master/layout/theme relationship, but remove sample slides.
    while len(prs.slides):
        slide_id = prs.slides._sldIdLst[0]  # noqa: SLF001 - python-pptx has no public delete API
        prs.part.drop_rel(slide_id.rId)
        del prs.slides._sldIdLst[0]

    def choose_layout(words: tuple[str, ...], fallback: int):
        """按角色关键词在客户模板版式里选最匹配的一个（找不到用 ``fallback`` 索引）。"""
        for layout in prs.slide_layouts:
            name = (layout.name or "").lower()
            if any(word in name for word in words):
                return layout
        return prs.slide_layouts[min(fallback, len(prs.slide_layouts) - 1)]

    def remove_shape(shape) -> None:
        """从幻灯片 XML 中移除形状（用于清掉未使用的占位符，不留空框）。"""
        element = shape._element
        element.getparent().remove(element)

    def title_shape(slide):
        """取该页的标题形状（优先原生标题占位符，其次按占位符类型识别）。"""
        if slide.shapes.title:
            return slide.shapes.title
        return slide.shapes.add_textbox(Inches(0.8), Inches(0.55), Inches(11.7), Inches(0.8))

    def body_shape(slide):
        """取该页的正文占位符（第一个非标题的文本占位符）。"""
        for shape in slide.placeholders:
            if shape == slide.shapes.title:
                continue
            if getattr(shape, "has_text_frame", False):
                return shape
        return slide.shapes.add_textbox(Inches(0.95), Inches(1.55), Inches(11.3), Inches(4.9))

    def clear_unused_placeholders(slide, used: set[int]) -> None:
        """删除本页未被填充的占位符，避免导出后出现"点击此处添加文本"的空框。"""
        for shape in list(slide.placeholders):
            if id(shape) in used:
                continue
            if getattr(shape, "has_text_frame", False) and not (shape.text or "").strip():
                remove_shape(shape)

    cover_layout = choose_layout(("title slide", "cover", "封面", "标题幻灯片"), 0)
    content_layout = choose_layout(("title and content", "content", "内容", "标题和内容"), 1)

    if include_cover:
        cover = prs.slides.add_slide(cover_layout)
        used: set[int] = set()
        title = title_shape(cover)
        title.text = str(spec.get("title") or "演示文稿")
        used.add(id(title))
        subtitle_text = str(spec.get("subtitle") or "")
        if subtitle_text:
            subtitle = body_shape(cover)
            subtitle.text = subtitle_text
            used.add(id(subtitle))
        clear_unused_placeholders(cover, used)
        write_notes(cover, spec.get("cover_notes"))

    role_keywords = {
        "agenda": ("agenda", "toc", "目录", "议程"),
        "section": ("section", "chapter", "章节", "过渡"),
        "two_column": ("two", "comparison", "双", "比较", "对比"),
        "comparison": ("comparison", "two", "比较", "对比"),
        "cards": ("multi", "grid", "多内容", "卡片"),
        "metrics": ("metric", "number", "kpi", "指标", "数字"),
        "image": ("picture", "image", "图片"),
        "statement": ("title only", "statement", "大标题"),
    }

    def layouts_for_role(slide_spec):
        """把语义版式映射成该客户模板里可能承载它的版式名关键词（用于挑选版式）。"""
        role = _infer_layout(slide_spec)
        words = role_keywords.get(role, ("content", "内容", "正文"))
        return choose_layout(words, 1), role

    def text_placeholders(slide):
        """列出该页所有带文本的占位符，供正文填充与区域合并使用。"""
        return [
            shape for shape in slide.placeholders
            if shape != slide.shapes.title
            and getattr(shape, "has_text_frame", False)
        ]

    def fill_frame(shape, values):
        """把值列表写进形状文本框（按行填充），沿用该占位符自身的字体与段落样式。"""
        frame = shape.text_frame
        frame.clear()
        for index, value in enumerate(values):
            paragraph = (
                frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            )
            paragraph.text = str(value)
            paragraph.level = 0
            paragraph.alignment = PP_ALIGN.LEFT

    for slide_spec in spec.get("slides") or []:
        selected_layout, role = layouts_for_role(slide_spec)
        slide = prs.slides.add_slide(selected_layout)
        used = set()
        heading = title_shape(slide)
        heading.text = str(slide_spec.get("title") or "")
        used.add(id(heading))
        items = slide_spec.get("items") or slide_spec.get("content") or []
        text_items = []
        table_items = []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "table" and item.get("rows"):
                table_items.append(item)
            elif isinstance(item, str):
                text_items.append(item)
            elif isinstance(item, dict):
                text_items.append(item.get("text") or item.get("content") or item.get("title") or "")
        bodies = text_placeholders(slide)
        if not bodies and text_items:
            bodies = [body_shape(slide)]
        values = [item for item in text_items if item]
        if role in {"two_column", "comparison", "cards", "metrics", "agenda"} and len(bodies) >= 2:
            chunks = [[] for _ in bodies]
            for index, value in enumerate(values):
                chunks[index % len(bodies)].append(value)
            for body, chunk in zip(bodies, chunks):
                used.add(id(body))
                fill_frame(body, chunk)
        elif bodies and values:
            body = bodies[0]
            used.add(id(body))
            fill_frame(body, values[:8])
            for extra in bodies[1:]:
                if not (extra.text or "").strip():
                    remove_shape(extra)
        if not text_items and table_items:
            for body in bodies:
                remove_shape(body)
                used.discard(id(body))
        for table_index, item in enumerate(table_items[:1]):
            rows = item["rows"][:9 if requested_pages else 8]
            if not rows:
                continue
            cols = max(len(row) for row in rows)
            table_shape = slide.shapes.add_table(
                len(rows), cols, Inches(0.95), Inches(3.75 if text_items else 1.65),
                Inches(11.35), Inches(min(2.6, 0.43 * len(rows))),
            )
            table = table_shape.table
            for ri, row in enumerate(rows):
                for ci in range(cols):
                    table.cell(ri, ci).text = str(row[ci]) if ci < len(row) else ""
        clear_unused_placeholders(slide, used)
        write_notes(slide, slide_spec.get("notes"))

    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()


def _render_legacy_builtin_presentation(spec: dict) -> bytes:
    """旧版内置渲染器（自绘封面/正文，无模板主题细节），当前保留作兼容回退。

    与 ``render_presentation`` 的关系：带 ``_template_path`` 时同样转交客户模板路径；
    其余情况直接用内置目录模板的 theme 色板硬编码坐标自绘，**不处理精确页数**（表格截 6 行、
    正文超出 6.15 英寸即中断）。新代码不应再直接调用它，仅历史调用点或对比测试使用。
    """

    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    from ..presentation.service import get_template

    spec = dict(spec or {})
    if spec.get("_template_path"):
        return _render_uploaded_presentation(spec, spec["_template_path"])
    template = get_template(spec.get("template_id"))
    theme = template["theme"]

    def rgb(key: str) -> RGBColor:
        """按模板主题键取色（旧版内置渲染路径使用，键必须存在）。"""
        value = theme[key].lstrip("#")
        return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    def set_background(slide, color_key: str = "background") -> None:
        """把整页底色设为指定主题色（旧版内置渲染路径）。"""
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = rgb(color_key)

    def style_run(run, size: int, color_key: str = "text", bold: bool = False) -> None:
        """统一设置字体、字号、粗体与颜色（旧版内置渲染路径固定使用微软雅黑）。"""
        run.font.name = "Microsoft YaHei"
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color_key)

    def add_text(slide, text: str, left, top, width, height, *, size=18,
                 color="text", bold=False):
        """插入一个文本框并应用统一样式（旧版内置渲染路径的文字出口）。"""
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.clear()
        frame.word_wrap = True
        paragraph = frame.paragraphs[0]
        paragraph.text = str(text or "")
        style_run(paragraph.runs[0], size, color, bold)
        return box

    def add_accent(slide, top=Inches(0.34), width=Inches(1.05)) -> None:
        """画标题上方的小色条（旧版内置渲染路径的品牌装饰）。"""
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.72), top, width, Inches(0.07))
        bar.fill.solid(); bar.fill.fore_color.rgb = rgb("accent")
        bar.line.fill.background()

    prs = Presentation()
    if spec.get("aspect_ratio") == "4:3":
        prs.slide_width, prs.slide_height = Inches(10), Inches(7.5)
    else:
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[5]

    # Cover
    slide = prs.slides.add_slide(blank)
    set_background(slide)
    add_accent(slide, top=Inches(1.15), width=Inches(1.35))
    add_text(slide, template["name"].upper(), Inches(1), Inches(1.38), Inches(5), Inches(0.35), size=10, color="muted", bold=True)
    add_text(slide, spec.get("title") or "演示文稿", Inches(1), Inches(2.05), Inches(10.8), Inches(1.55), size=40, color="text", bold=True)
    subtitle = spec.get("subtitle")
    if subtitle:
        add_text(slide, subtitle, Inches(1.02), Inches(3.75), Inches(9.8), Inches(0.8), size=18, color="muted")
    add_text(slide, "智慧云脑 Cloud Brain", Inches(1.02), Inches(6.65), Inches(4), Inches(0.3), size=9, color="muted")

    # Content slides
    for slide_index, slide_spec in enumerate(spec.get("slides") or [], start=1):
        s = prs.slides.add_slide(blank)
        set_background(s)
        add_accent(s)
        add_text(s, slide_spec.get("title") or f"第 {slide_index} 页", Inches(0.72), Inches(0.55), Inches(11.8), Inches(0.75), size=26, color="text", bold=True)
        surface = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.72), Inches(1.52), Inches(11.9), Inches(5.2))
        surface.fill.solid(); surface.fill.fore_color.rgb = rgb("surface")
        surface.line.fill.background()
        items = slide_spec.get("items") or slide_spec.get("content") or []
        y = 1.83
        for item in items[:8]:
            if isinstance(item, dict) and item.get("type") == "table" and item.get("rows"):
                rows = item["rows"][:6]
                cols = max(len(row) for row in rows)
                table = s.shapes.add_table(len(rows), cols, Inches(1.02), Inches(y), Inches(11.25), Inches(min(2.6, 0.42 * len(rows)))).table
                for ri, row in enumerate(rows):
                    for ci in range(cols):
                        cell = table.cell(ri, ci)
                        cell.text = str(row[ci]) if ci < len(row) else ""
                        for paragraph in cell.text_frame.paragraphs:
                            for run in paragraph.runs:
                                style_run(run, 11, "text", bold=ri == 0)
                y += min(2.8, 0.46 * len(rows))
                continue
            if isinstance(item, str):
                text, kind = item, "bullet"
            else:
                text = item.get("text") or item.get("content") or item.get("title") or ""
                kind = item.get("type") or "bullet"
            prefix = "•  " if kind in ("bullet", "p", "text") else ""
            size = 16 if kind not in ("h1", "h2") else 20
            add_text(s, prefix + text, Inches(1.08), Inches(y), Inches(10.95), Inches(0.52), size=size, color="text", bold=kind in ("h1", "h2"))
            y += 0.58 if size == 16 else 0.7
            if y > 6.15:
                break
        add_text(s, f"{slide_index:02d}", Inches(11.92), Inches(6.86), Inches(0.45), Inches(0.25), size=9, color="muted")
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def render_presentation(spec: dict) -> bytes:
    """Render PresentationSpec v2 or preserve a customer PPTX template.

    PPT 渲染的唯一分发点：spec 带 ``_template_path`` 时走客户模板路径
    （``_render_uploaded_presentation``，保留母版与版式），否则取内置模板主题交给
    ``presentation.engine.render_builtin_presentation``（原生图表 + 精确页数控制）。
    注意 ``get_template`` 在 template_id 缺失/未知时回退到内置目录第一套——需要"必须显式选模板"
    的调用方应在这之前自行校验 template_id。
    """
    from ..presentation.engine import render_builtin_presentation
    from ..presentation.service import get_template

    normalized = dict(spec or {})
    if normalized.get("_template_path"):
        return _render_uploaded_presentation(
            normalized, normalized["_template_path"]
        )
    template = get_template(normalized.get("template_id"))
    return render_builtin_presentation(normalized, template)


# ------------------------------------------------------------------
# XLSX
# ------------------------------------------------------------------
def render_spreadsheet(spec: dict) -> bytes:
    """把表格 spec 渲染为 .xlsx 字节（多工作表，最多 100 个）。

    安全约束：以 ``= + - @`` 开头的单元格会被加上前置单引号（``safe_value``），防止导出文件
    在 Excel 中当作公式执行。工作表名按 Excel 规则清洗非法字符并去重、截断到 31 字符；
    列宽按内容长度自动收敛到 10–48。导出前同样统一字体。
    """

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    def safe_value(value):
        """给以 ``= + - @`` 开头的文本加前置单引号，防止 Excel/WPS 把导出内容当公式执行。"""
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
            return "'" + value
        return value

    def fill_sheet(ws, sheet_spec: dict) -> None:
        """写入一个工作表的表头与数据行（所有值都过 ``safe_value``），并按内容设置列宽与冻结行。"""
        rows = sheet_spec.get("rows") or sheet_spec.get("data") or []
        headers = sheet_spec.get("columns")
        if headers:
            ws.append([safe_value(value) for value in headers])
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="2364C6")
        for row in rows:
            if isinstance(row, dict):
                columns = headers or list(row.keys())
                ws.append([safe_value(row.get(column, "")) for column in columns])
            else:
                ws.append([safe_value(value) for value in row])
        ws.freeze_panes = "A2" if headers else None
        for column in ws.columns:
            width = min(48, max(10, max((len(str(cell.value or "")) for cell in column), default=10) + 2))
            ws.column_dimensions[column[0].column_letter].width = width

    wb = Workbook()
    sheets = spec.get("sheets")
    if isinstance(sheets, list) and sheets:
        wb.remove(wb.active)
        used_names: set[str] = set()
        for index, sheet in enumerate(sheets[:100], start=1):
            if not isinstance(sheet, dict):
                continue
            base = re.sub(r"[\\/*?:\[\]]", "_", str(sheet.get("name") or sheet.get("sheet_name") or f"Sheet{index}"))[:31] or f"Sheet{index}"
            name = base
            suffix = 2
            while name in used_names:
                marker = f"_{suffix}"
                name = base[:31 - len(marker)] + marker
                suffix += 1
            used_names.add(name)
            fill_sheet(wb.create_sheet(name), sheet)
        if not wb.sheetnames:
            fill_sheet(wb.create_sheet("Sheet1"), {})
    else:
        default_ws = wb.active
        default_ws.title = re.sub(r"[\\/*?:\[\]]", "_", str(spec.get("sheet_name") or "Sheet1"))[:31] or "Sheet1"
        fill_sheet(default_ws, spec)
    buf = io.BytesIO()
    wb.save(buf)
    from ...common.artifact_fonts import normalize_office_fonts
    return normalize_office_fonts(buf.getvalue(), ".xlsx")


# ------------------------------------------------------------------
# PDF
# ------------------------------------------------------------------
def render_pdf(spec: dict) -> bytes:
    """把文档 spec 渲染为 A4 PDF 字节；使用随包宋体保证中文不出现方框。

    所有文本都经 ``xml_escape`` 转义后再作为段落标记解析，因此正文里的 ``<``/``&`` 不会破坏
    版式（也避免内容被当标签注入）；表格用 ``repeatRows=1`` 让表头跨页重复，图片按页宽等比缩放。
    """

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.enums import TA_CENTER
    from xml.sax.saxutils import escape as xml_escape

    buf = io.BytesIO()
    from reportlab.pdfbase.ttfonts import TTFont
    from ...common.artifact_fonts import song_font_path
    font_name = "AppSimSun"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(song_font_path()), subfontIndex=0))
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("H", parent=styles["Title"], fontName=font_name, fontSize=22)
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName=font_name, fontSize=16)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName=font_name, fontSize=13)
    body = ParagraphStyle("B", parent=styles["BodyText"], fontName=font_name, fontSize=10.5, leading=16)
    doc = SimpleDocTemplate(buf, pagesize=A4)
    story = []
    story.append(Paragraph(xml_escape(str(spec.get("title") or "文档")), title_style))
    story.append(Spacer(1, 12))
    for section in spec.get("sections") or []:
        if section.get("title"):
            story.append(Paragraph(xml_escape(str(section["title"])), h1))
        for block in section.get("blocks") or []:
            kind = block.get("type", "p")
            text = str(block.get("text") or block.get("content") or "")
            if kind in ("h1", "h2"):
                story.append(Paragraph(xml_escape(str(text or block.get("title", ""))), h2))
            elif kind in {"p", "bullet", "numbered", "quote"}:
                values = _block_items(block) if kind in {"bullet", "numbered"} else [text]
                for index, value in enumerate(values, start=1):
                    prefix = "• " if kind == "bullet" else (f"{index}. " if kind == "numbered" else "")
                    story.append(Paragraph(xml_escape(prefix + value), body))
            elif kind == "image":
                from .document_images import image_bytes
                from PIL import Image as PillowImage
                from reportlab.platypus import Image as PDFImage
                image_data = image_bytes(block)
                with PillowImage.open(io.BytesIO(image_data)) as image:
                    width, height = image.size
                scale = min(doc.width / width, 480 / height)
                story.append(PDFImage(io.BytesIO(image_data), width=width * scale, height=height * scale))
                if block.get("caption"):
                    story.append(Paragraph(xml_escape(str(block["caption"])), body))
            elif kind == "table":
                rows = _block_table_rows(block)
                if rows:
                    table = Table([[Paragraph(xml_escape(str(cell)), body) for cell in row] for row in rows], repeatRows=1)
                    table.setStyle(TableStyle([
                        ("FONTNAME", (0, 0), (-1, -1), font_name),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF1FB")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C5D6")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]))
                    story.append(table)
                    story.append(Spacer(1, 8))
            else:
                story.append(Paragraph(xml_escape(text), body))
    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------
# JSON / CSV helpers
# ------------------------------------------------------------------
def render_json(data: Any) -> bytes:
    """结构化导出：UTF-8 JSON（保留中文，缩进 2）。"""

    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def render_csv(rows: List[list], columns=None) -> bytes:
    """导出 CSV，UTF-8 **带 BOM**（``utf-8-sig``）以兼容 Excel 直接打开时的中文显示。"""

    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    if columns:
        w.writerow(list(columns))
    for r in rows:
        if isinstance(r, dict):
            cols = columns or list(r.keys())
            w.writerow([r.get(c, "") for c in cols])
        else:
            w.writerow(list(r))
    return buf.getvalue().encode("utf-8-sig")


RENDERERS = {
    # 输出类型 → 渲染器；新增类型必须同时登记这里，否则调用方按"不支持的类型"拒绝。
    "document": render_document,
    "presentation": render_presentation,
    "spreadsheet": render_spreadsheet,
    "pdf": render_pdf,
    "json": render_json,
    "csv": render_csv,
}
