# -*- coding: utf-8 -*-
"""Text extraction for uploaded files (returns blocks with origin metadata)."""
from __future__ import annotations

import csv
import io
import os
from typing import List

NL = chr(10)  # newline used as separator (avoids escape ambiguity)


class UnsupportedDocumentError(ValueError):
    """A retained upload needs conversion before it can become knowledge."""


def extract_text(filename: str, mime_type: str, path: str) -> List[dict]:
    """Return list of blocks: {text, page?, sheet?, slide?, table?} preserving origin."""
    ext = os.path.splitext(filename)[1].lower()
    legacy = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}
    legacy_mimes = {"application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"}
    # 旧版 OLE 复合文档（.doc/.xls/.ppt）不解析正文，只按扩展名或 MIME 提示用户另存后重传；
    # 原文件仍保留在资料库可下载，因此这里抛业务异常而不是删文件。
    # 注意：扩展名与 MIME 任一命中即拒绝，避免用户把 .doc 改名成 .docx 绕过校验。
    if ext in legacy or mime_type in legacy_mimes:
        target = legacy.get(ext, "DOCX、XLSX 或 PPTX")
        raise UnsupportedDocumentError(f"旧版 Office 文件暂不支持正文解析，请另存为 {target} 后重新上传；原文件仍可下载。")
    try:
        # 图片不走这里的正文提取，交给 files/vision.py 的 extract_visual_assets 单独识别，
        # 避免同一张图既被当作正文文本又被当作视觉资产重复入库。
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff") or mime_type.startswith("image/"):
            return []
        if ext == ".pdf" or mime_type == "application/pdf":
            return _from_pdf(path)
        if ext in (".docx",) or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return _from_docx(path)
        if ext in (".xlsx",) or mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return _from_xlsx(path)
        if ext == ".pptx" or mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
            return _from_pptx(path)
        if ext == ".csv" or mime_type == "text/csv":
            return _from_csv(path)
        # 已知缺口：任何未识别扩展名（.txt/.md/.json/.log 之外的 .zip、.msg、无后缀文件等）
        # 都会落到这里的纯文本兜底分支，即"按 UTF-8 解码 + 二进制控制字符校验"处理，
        # 既不做格式嗅探也不做结构解析。后果是：伪装扩展名的 Office 二进制可能抛出
        # _read_text 的控制字符异常，而能通过校验的未知格式只会得到一段无页码语义的纯文本。
        text = _read_text(path)
        return [{"text": text, "page": 0}]
    except Exception as exc:
        raise ValueError(f"文件解析失败 [{filename}]: {exc}")


def _read_text(path: str) -> str:
    """按 UTF-8（带 BOM 识别 UTF-16）严格解码，并拒绝二进制内容。

    这是未识别扩展名的兜底读取器，也是 CSV 的读取器。使用 ``errors="strict"`` 是刻意选择：
    宁可让调用方收到"解析失败"并提示用户重新导出，也不要用 replace 造出一堆乱码进入索引。
    含 Ascii 控制字符（除 Tab/换行/回车/换页）时视为二进制文件并抛 ValueError。
    """
    with open(path, "rb") as fh:
        data = fh.read()
    # Recognize explicit UTF-16 BOMs, but never replace undecodable bytes or
    # accept binary control characters as indexed document content.
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = data.decode(encoding, errors="strict")
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        raise ValueError("文件含有二进制控制字符，请确认格式或导出为 UTF-8 文本")
    return text


def _from_pdf(path):
    """抽取 PDF 每页文本，返回带 0 基页码 ``page`` 的块；无文本页（纯扫描页）直接跳过。

    pypdf 为可选项，故在函数内按需导入，避免未安装时影响其它格式的解析。
    """
    from pypdf import PdfReader
    reader = PdfReader(path)
    blocks = []
    for i, page in enumerate(reader.pages):
        t = (page.extract_text() or "").strip()
        if t:
            blocks.append({"text": t, "page": i})
    return blocks


def _from_docx(path):
    """抽取 DOCX 段落与表格。

    段落与表格都按 ``page=0`` 上报：Word 文档没有稳定页码概念（分页由渲染器决定），
    所以引用定位只到文档级，不去猜页码。表格额外带 ``table`` 序号用于前端标注第 N 张表。
    """
    from docx import Document
    doc = Document(path)
    blocks = []
    for para in doc.paragraphs:
        if para.text.strip():
            blocks.append({"text": para.text.strip(), "page": 0})
    for idx, table in enumerate(doc.tables):
        rows = []
        for row in table.rows:
            rows.append(" | ".join(c.text.strip() for c in row.cells))
        if rows:
            blocks.append({"text": NL.join(rows), "page": 0, "table": idx})
    return blocks


def _from_xlsx(path):
    """按工作表抽取 XLSX：每行用 Tab 拼接，每张表一个块并带 ``sheet`` 名。

    ``read_only=True`` 避免大表把整个工作簿读进内存；``data_only=True`` 取公式的缓存计算值，
    因此未在 Excel 中打开过、没有缓存值的公式会读出 None（静默变成空串）。
    行尾空单元格会被裁掉，减少表格右侧的无效制表符噪声。
    """
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    blocks = []
    TAB = chr(9)
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            vals = [("" if v is None else str(v)) for v in row]
            while vals and vals[-1] == "":
                vals.pop()
            if any(vals):
                lines.append(TAB.join(vals))
        if lines:
            blocks.append({"text": NL.join(lines), "sheet": ws.title, "page": 0})
    return blocks


def _from_pptx(path):
    """按幻灯片抽取 PPTX 文本框与表格，页码同时写入 ``slide`` 与 ``page``。

    两个字段都写是因为检索结果展示与引用跳转历史上分别读过这两个键；保持一致可让旧前端
    不改代码就拿到正确页码。``slide`` 为 0 基，展示层负责 +1。
    """
    from pptx import Presentation
    prs = Presentation(path)
    blocks = []
    for i, slide in enumerate(prs.slides):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        texts.append(t)
            if shape.has_table:
                for row in shape.table.rows:
                    texts.append(" | ".join(cell.text.strip() for cell in row.cells))
        if texts:
            blocks.append({"text": NL.join(texts), "slide": i, "page": i})
    return blocks


def _from_csv(path):
    """逐行抽取 CSV：每个非空行作为一个块，行内字段再用逗号重新拼回一行文本。

    这里刻意用 csv.reader 解析后再 join，而不是直接按行返回原文，是为了让带引号、
    带内嵌逗号的字段在切块时保持语义完整；代价是原始引号会被去掉。
    """
    blocks = []
    with io.StringIO(_read_text(path), newline="") as fh:
        for row in csv.reader(fh):
            line = ",".join(row)
            if line.strip():
                blocks.append({"text": line, "page": 0})
    return blocks
