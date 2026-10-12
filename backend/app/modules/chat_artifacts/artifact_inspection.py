"""只核验附件的可读取结构和实际内容；业务数字与图表含义另由来源回执证明。"""
from __future__ import annotations

import hashlib
from io import BytesIO
import math
import posixpath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from PIL import Image


_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PPT = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CHART = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"


def _image(data: bytes) -> dict:
    """排除损坏、微型、全透明及纯色图片；这一步不判断图片是不是业务图表。"""
    from PIL import ImageStat

    with Image.open(BytesIO(data)) as source:
        width, height = source.size
        if width < 160 or height < 100 or width * height > 40_000_000:
            return {}
        source.verify()
    with Image.open(BytesIO(data)) as source:
        picture = source.convert("RGBA")
        # 按白色画布合成后检查，透明像素中的隐藏 RGB 值不能冒充可见内容。
        background = Image.new("RGBA", picture.size, "white")
        background.alpha_composite(picture)
        sample = background.convert("RGB")
        sample.thumbnail((640, 640))
        statistics = ImageStat.Stat(sample)
        if max(statistics.stddev) < 2.0 or max(high - low for low, high in sample.getextrema()) < 12:
            return {}
    return {"kind": "image", "digest": hashlib.sha256(data).hexdigest(), "width": width, "height": height}


def _xml(archive: ZipFile, name: str):
    """有界读取 XML，拒绝通过单个异常超大分片消耗整个会话。"""
    if archive.getinfo(name).file_size > 12 * 1024 * 1024:
        raise ValueError("Office XML 分片超过核验范围")
    return ET.fromstring(archive.read(name))


def _relations(archive: ZipFile, marker: str) -> dict:
    """保留关系的真实目标；使用方需核对引用目标存在，不能悄悄跳过损坏引用。"""
    filename = posixpath.join(posixpath.dirname(marker), "_rels", posixpath.basename(marker) + ".rels")
    if filename not in archive.namelist():
        return {}
    relations = {}
    for relation in _xml(archive, filename):
        key = relation.get("Id")
        if not key or key in relations:
            raise ValueError("Office 关系标识为空或重复")
        target = relation.get("Target") or ""
        external = relation.get("TargetMode") == "External"
        if not external:
            target = (target.lstrip("/") if target.startswith("/") else
                      posixpath.normpath(posixpath.join(posixpath.dirname(marker), target)))
        relations[key] = {"target": target, "type": relation.get("Type", ""), "external": external}
    return relations


def _target(archive: ZipFile, relations: dict, key: str, relation_type: str) -> str:
    """内容引用必须指向包中真实存在的对应部件，外部链接不算已交付的内容。"""
    relation = relations.get(key) or {}
    target = relation.get("target") or ""
    if (relation.get("external") or not relation.get("type", "").endswith("/" + relation_type)
            or target not in archive.namelist()):
        raise ValueError("Office 引用部件缺失或类型不符")
    return target


def _visuals(archive: ZipFile, marker: str, document) -> tuple[list[str], int]:
    """只计正文中实际引用且可读取的图片/图表，压缩包里的孤立文件不计入内容。"""
    relations = _relations(archive, marker)
    images = []
    for node in [*document.iter(_DRAWING + "blip"), *document.iter("{urn:schemas-microsoft-com:vml}imagedata")]:
        key = node.get(_REL + "embed") or node.get(_REL + "id")
        if not key:
            # 外链图片并未随附件保存，不构成可离线交付的图片。
            if node.get(_REL + "link"):
                raise ValueError("Office 图片只保留了外部链接")
            continue
        target = _target(archive, relations, key, "image")
        data = archive.read(target)
        try:
            evidence = _image(data)
        except (OSError, ValueError):
            evidence = {}
        if evidence:
            images.append(evidence["digest"])
        else:
            # 小图标可合法存在；能否支持文档内容由正文文字或其它有效图像决定。
            with Image.open(BytesIO(data)) as image:
                image.verify()
    charts = 0
    for node in document.iter(_CHART + "chart"):
        target = _target(archive, relations, node.get(_REL + "id"), "chart")
        chart = _xml(archive, target)
        points = [item.text for item in chart.iter(_CHART + "v") if item.text and item.text.strip()]
        if not points:
            raise ValueError("Office 图表没有实际数据内容")
        charts += 1
    return images, charts


def _office(kind: str, data: bytes) -> dict:
    """核验可达的 Office 内容，既不靠 ZIP 条目数量也不依赖扩展名声称完成。"""
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (len(entries) > 12_000 or len(names) != len(set(names))
                or sum(item.file_size for item in entries) > 256 * 1024 * 1024):
            return {}
        marker = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[kind]
        if marker not in names or "[Content_Types].xml" not in names or "_rels/.rels" not in names:
            return {}
        _xml(archive, "[Content_Types].xml")
        package_relations = _relations(archive, "")
        if not any(relation["target"] == marker and not relation["external"]
                   and relation["type"].endswith("/officeDocument") for relation in package_relations.values()):
            return {}
        document = _xml(archive, marker)
        result = {"kind": kind, "validation": "structure_and_nonempty_content"}
        if kind == "docx":
            if document.tag != _WORD + "document":
                return {}
            text = "".join(node.text or "" for node in document.iter(_WORD + "t")).strip()
            images, charts = _visuals(archive, marker, document)
            if not text and not images and not charts:
                return {}
            return {**result, "images": images, "text_characters": len(text)}
        relationships = _relations(archive, marker)
        if kind == "pptx":
            if document.tag != _PPT + "presentation":
                return {}
            slides = document.findall("./" + _PPT + "sldIdLst/*")
            if not slides:
                return {}
            targets = []
            for slide in slides:
                target = _target(archive, relationships, slide.get(_REL + "id"), "slide")
                if target in targets:
                    return {}
                targets.append(target)
                page = _xml(archive, target)
                if page.tag != _PPT + "sld":
                    return {}
                text = "".join(node.text or "" for node in page.iter(_DRAWING + "t")).strip()
                images, charts = _visuals(archive, target, page)
                if not text and not images and not charts:
                    return {}
            return {**result, "pages": len(slides), "nonempty_pages": len(slides)}
        if document.tag != _SHEET + "workbook":
            return {}
        sheets = document.findall("./" + _SHEET + "sheets/*")
        if not sheets:
            return {}
        strings = []
        shared = [relation for relation in relationships.values() if relation["type"].endswith("/sharedStrings")]
        if shared:
            target = shared[0]["target"]
            if shared[0]["external"] or target not in names:
                return {}
            strings = ["".join(node.text or "" for node in item.iter(_SHEET + "t"))
                       for item in _xml(archive, target).findall(_SHEET + "si")]
        nonempty_cells = formulas = 0
        targets = []
        for sheet in sheets:
            target = _target(archive, relationships, sheet.get(_REL + "id"), "worksheet")
            if target in targets:
                return {}
            targets.append(target)
            content = _xml(archive, target)
            if content.tag != _SHEET + "worksheet":
                return {}
            for cell in content.findall("./" + _SHEET + "sheetData/" + _SHEET + "row/" + _SHEET + "c"):
                value = cell.findtext(_SHEET + "v", "").strip()
                cell_type = cell.get("t", "n")
                if cell_type == "e":
                    return {}
                formula = cell.findtext(_SHEET + "f", "").strip()
                if formula:
                    formulas += 1
                if cell_type == "s":
                    if not value.isdigit() or int(value) >= len(strings):
                        return {}
                    value = strings[int(value)].strip()
                elif cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(_SHEET + "t")).strip()
                elif cell_type == "n" and value and not math.isfinite(float(value)):
                    return {}
                if value or formula:
                    nonempty_cells += 1
        if not nonempty_cells:
            return {}
        return {**result, "sheets": len(sheets), "nonempty_cells": nonempty_cells, "formula_cells": formulas}


def inspect_file(name: str, data: bytes) -> dict:
    """返回结构证据；损坏/空白附件返回空对象，不将结构合格解释为业务结论准确。"""
    kind = name.lower().rsplit(".", 1)[-1]
    if not data:
        return {}
    try:
        if kind in {"png", "jpg", "jpeg", "webp"}:
            return _image(data)
        if kind in {"docx", "xlsx", "pptx"}:
            return _office(kind, data)
        if kind == "pdf":
            from pypdf import PdfReader
            from pypdf.errors import PyPdfError
            try:
                document = PdfReader(BytesIO(data), strict=True)
                if document.is_encrypted or not document.pages:
                    return {}
                if not any((page.extract_text() or "").strip() or len(page.images) for page in document.pages):
                    return {}
                return {"kind": "pdf", "pages": len(document.pages), "validation": "structure_and_nonempty_content"}
            except PyPdfError:
                return {}
    except (OSError, ValueError, KeyError, IndexError, RuntimeError, BadZipFile, ET.ParseError, Image.DecompressionBombError):
        return {}
    return {}
