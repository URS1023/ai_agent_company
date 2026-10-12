# -*- coding: utf-8 -*-
"""Fill user-uploaded template files (xlsx/docx) in place while preserving
their original structure, fonts and formatting.

Design: openpyxl/python-docx load the ORIGINAL template, we only set .value
on the target cells/runs (styles stay untouched unless the caller explicitly
overrides font). Output is a NEW artifact; the uploaded file is never mutated.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from ...errors import ValidationError


# ------------------------------------------------------------------
# xlsx
# ------------------------------------------------------------------
def _find_cell_by_label(ws, label: str):
    """按"单元格文本等于标签"在整张活动表里线性查找，返回命中的 cell 或 None。

    用于"标签 + 右侧/下方填值"的模板约定（见 fill_xlsx 的 by_label）。精确匹配前会 strip 两侧
    空白，避免模板里的空格导致找不到锚点；重复标签时取第一个（按行优先顺序）。
    """
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None and str(cell.value).strip() == str(label).strip():
                return cell
    return None


def fill_xlsx(
    template_path: str,
    out_path: str,
    *,
    cells: Optional[dict] = None,
    by_label: Optional[list] = None,
    tables: Optional[list] = None,
    font: Optional[dict] = None,
) -> dict:
    """把数据写进 xlsx 模板并另存为新文件，返回 {"written": 实际写入单元格数, "format": "xlsx"}。

    三种写入口可同时使用，按顺序执行：cells（精确引用，如 {"B4": "值"}）、by_label（按标签定位，
    写到右侧或下方）、tables（从某引用起的二维区域，逐行展开）。

    参数：
        template_path / out_path：模板路径与输出路径；输出用 "写临时文件 + os.replace" 落盘，
            保证下游拿到的永远是完整文件（避免边写边被读取）。
        font：可选字体覆盖，形如 {"name","size","bold","italic","color"}；不传则完全保留模板样式。

    权限与副作用：不做权限/归属校验，由调用方（files/service.py 的模板填充入口）负责；
        只读模板，不改原文件；空值/非法引用等静默跳过并计入返回的 written 差值，不抛异常。
    注意：这里只写活动工作表（wb.active），多 sheet 模板的非活动表不会被填充。
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    wb = load_workbook(template_path)  # keeps styles/formats
    ws = wb.active
    written = 0

    def _configured_font() -> Optional[Font]:
        """把用户传入的字体字典转成 openpyxl Font；未配置或转换失败都返回 None（=保留原样式）。

        颜色统一补上 "FF" alpha 前缀并用 FF 开头的 ARGB；传入的可能是 "#RRGGBB" 或 "RRGGBB"。
        转换异常整体吞掉：字体是装饰性参数，不该因为一个非法颜色值让填充失败。
        """
        if not font:
            return None
        try:
            return Font(
                name=font.get("name"),
                size=float(font["size"]) if font.get("size") is not None else None,
                bold=bool(font.get("bold")) if "bold" in font else None,
                italic=bool(font.get("italic")) if "italic" in font else None,
                color=("FF" + str(font["color"]).strip("#")) if font.get("color") else None,
            )
        except Exception:
            return None

    override_font = _configured_font()

    def _resolve_cell(row, column):
        """MergedCell is read-only: always write to the top-left of a merged range."""
        # 中文补充：合并区域里只有左上角是真正的可写 Cell，其余都是只读的 MergedCell，
        # 直接赋值会抛 AttributeError（或静默无效），所以任何定位都要先归一到左上角。
        for rng in ws.merged_cells.ranges:
            if rng.min_row <= row <= rng.max_row and rng.min_col <= column <= rng.max_col:
                return ws.cell(rng.min_row, rng.min_col)
        return ws.cell(row, column)

    def _apply(ref_or_cell, value, *, row=None, column=None):
        """写入一个单元格，返回 1 表示成功、0 表示失败；失败一律静默（不中断整份填充）。

        两种调用形态：给了 row/column 就按坐标走（会归一到合并区左上角），否则按引用字符串
        取值。异常吞掉是有意设计：模板单元格可能被保护、只读或已被合并，个别失败不应让
        其余字段全部丢失，调用方通过返回的 written 计数就能发现"没写进去"。
        """
        if row is not None and column is not None:
            cell = _resolve_cell(row, column)
        else:
            cell = ws[ref_or_cell]
            # handle merged cell passed by direct ref
            try:
                cell = _resolve_cell(cell.row, cell.column)
            except Exception:
                cell = ws[ref_or_cell]
        try:
            cell.value = value
        except Exception:
            return 0
        if override_font is not None:
            cell.font = override_font
        return 1

    # 1) exact cell refs {B4: value}
    for ref, value in (cells or {}).items():
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string as _cidx
        try:
            col_letter, row = coordinate_from_string(str(ref))
            col = _cidx(col_letter)
        except Exception:
            continue
        written += _apply(ref, value, row=int(row), column=col)

    # 2) by label: find the header cell, place value to the right / below
    for item in by_label or []:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        value = item.get("value")
        direction = (item.get("direction") or "right").lower()
        found = _find_cell_by_label(ws, label)
        if found is None:
            continue
        if direction == "right":
            written += _apply(None, value, row=found.row, column=found.column + 1)
        else:  # below
            written += _apply(None, value, row=found.row + 1, column=found.column)

    # 3) append table rows starting at a ref
    for item in tables or []:
        start = str(item.get("start") or "").strip()
        if not start:
            continue
        try:
            from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
            col_letter, row = coordinate_from_string(start)
            start_col = column_index_from_string(col_letter)
            start_row = int(row)
        except Exception:
            continue
        for r_offset, row_data in enumerate(item.get("rows") or []):
            for c_offset, value in enumerate(row_data):
                written += _apply(None, value, row=start_row + r_offset, column=start_col + c_offset)

    out_tmp = out_path + ".tmp"
    wb.save(out_tmp)
    os.replace(out_tmp, out_path)
    return {"written": written, "format": "xlsx"}


# ------------------------------------------------------------------
# docx (placeholder / label replacement, keeps run styles by default)
# ------------------------------------------------------------------
def fill_docx(
    template_path: str,
    out_path: str,
    *,
    replace: Optional[dict] = None,
    by_label: Optional[list] = None,
    font: Optional[dict] = None,
) -> dict:
    """把数据写进 docx 模板并另存为新文件，返回 {"written": 实际改写的段落数, "format": "docx"}。

    两种写入口，按顺序执行：replace（占位符替换，同时支持双花括号 ``{{key}}`` 与单花括号
    ``{key}`` 两种写法）、by_label（当整段文本恰好等于标签时，用值替换该段）。

    参数：
        template_path / out_path：模板与输出路径；输出直接 doc.save(out_path)。
        font：可选字体覆盖 {"name","size","bold","italic","color"}；不传则保留模板原有字体。
            颜色解析失败会被忽略（不改颜色但仍写入文本），避免一个错色值让整段丢失。

    扫描范围：正文段落 + 所有表格单元格内的段落（_iter_paragraphs），页眉/页脚与文本框不覆盖。
    副作用：只读模板，不改原文件；被改写的段落只保留首个 run 的样式，其余 run 文本清空，
        这是为了在保留段落级格式的前提下替换整段文字。
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt
    import math

    font_size = None
    if font and font.get("size") is not None:
        try:
            value = font["size"]
            points = float(value)
            if isinstance(value, bool) or not math.isfinite(points) or points < 0.5:
                raise ValueError("字号不能舍入为零")
            font_size = Pt(points)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValidationError("模板字号必须是至少 0.5 磅的有限数值") from exc

    doc = Document(template_path)
    written = 0

    def _apply_font(run):
        """逐项覆盖 run 的字体属性；字典里没给的项保持模板原值，颜色非法时只跳过颜色。"""
        if not font:
            return
        if font.get("name"):
            run.font.name = font["name"]
            # 中文使用东亚字体槽；清掉主题引用，避免宋体被模板主题替换。
            fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
            fonts.set(qn("w:eastAsia"), font["name"])
            fonts.attrib.pop(qn("w:eastAsiaTheme"), None)
        if font_size is not None:
            # python-docx 接收 EMU 长度，用户输入的字号单位是磅。
            run.font.size = font_size
        if "bold" in font:
            run.font.bold = bool(font["bold"])
        if "italic" in font:
            run.font.italic = bool(font["italic"])
        if font.get("color"):
            try:
                from docx.shared import RGBColor
                run.font.color.rgb = RGBColor.from_string(str(font["color"]).strip("#"))
            except Exception:
                pass

    def _set_paragraph_text(paragraph, new_text):
        """替换整段文字并递增 written（闭包读写外层计数）。

        段落存在 run 时只改第一个 run、清空其余 run，这样段落会保留第一个 run 的字号/字体/
        加粗等格式；若整段没有任何 run（例如空段落），则新建一个 run 再套字体。
        直接改 paragraph.text 会丢掉全部样式，故不采用。
        """
        nonlocal written
        if not paragraph.runs:
            paragraph.add_run(new_text)
            if paragraph.runs:
                _apply_font(paragraph.runs[-1])
            written += 1
            return
        paragraph.runs[0].text = new_text
        _apply_font(paragraph.runs[0])
        for extra in paragraph.runs[1:]:
            extra.text = ""
        written += 1

    def _iter_paragraphs():
        """按"正文段落 → 各表格单元格内段落"的顺序产出所有可替换段落。

        表格是逐格展开的，合并单元格会让同一段文字被多次产出；替换是幂等的（已替换过的
        文本不会再匹配到原标签），所以重复遍历不会造成重复写入。
        注意：页眉/页脚、文本框、脚注不在遍历范围内。
        """
        for p in doc.paragraphs:
            yield p
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        yield p

    # 1) placeholder replace: {{key}} and {key}
    placeholder_map = {str(k): str(v) for k, v in (replace or {}).items()}
    for paragraph in _iter_paragraphs():
        full = paragraph.text
        new_full = full
        for key, value in placeholder_map.items():
            new_full = new_full.replace("{{" + key + "}}", value).replace("{" + key + "}", value)
        if new_full != full and new_full != "":
            _set_paragraph_text(paragraph, new_full)

    # 2) by label: replace exact paragraph text (or leading label prefix)
    for item in by_label or []:
        label = str(item.get("label") or "").strip()
        value = item.get("value")
        if not label:
            continue
        for paragraph in _iter_paragraphs():
            if paragraph.text.strip() == label:
                _set_paragraph_text(paragraph, str(value))
                break

    doc.save(out_path)
    return {"written": written, "format": "docx"}


def fill_template_file(
    template_path: str,
    out_path: str,
    *,
    ext: str,
    cells=None, by_label=None, tables=None, replace=None, font: Optional[dict] = None,
) -> dict:
    """按扩展名分派到 xlsx/docx 填充实现，是模板填充的统一入口。

    参数：
        ext：目标扩展名，大小写不敏感；.xlsm 与 .xlsx 同路，.doc 与 .docx 同路
            （.doc/.xls 的"支持"只是把参数传下去，实际能否打开取决于上游是否已转换）。
        cells/by_label/tables 仅对 xlsx 生效，replace 仅对 docx 生效；不匹配的参数会被静默忽略。

    异常：
        ValidationError：扩展名不在支持列表内。文案面向用户，列出的是实际支持范围。
    副作用：会创建/覆盖 out_path；调用方需自行保证该路径在允许的存储区内。
    """
    ext = ext.lower()
    if ext in (".xlsx", ".xlsm"):
        return fill_xlsx(template_path, out_path, cells=cells, by_label=by_label, tables=tables, font=font)
    if ext in (".docx", ".doc"):
        return fill_docx(template_path, out_path, replace=replace, by_label=by_label, font=font)
    raise ValidationError(f"模板填充暂不支持 {ext}（支持 .xlsx / .docx）")
