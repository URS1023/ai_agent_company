# -*- coding: utf-8 -*-
"""Extract and normalize visual assets from documents for VLM analysis."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import zipfile
from typing import Optional

from ...config import settings

logger = logging.getLogger("file-vision")

DIRECT_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}


def extract_visual_assets(
    filename: str,
    mime_type: str,
    path: str,
    *,
    max_images: Optional[int] = None,
) -> tuple[list[dict], list[str]]:
    """Return normalized images plus non-fatal extraction warnings."""
    # 中文说明：由 knowledge/service.py 的入库管线调用，产出的资产交给 VLM 识别后追加为文本块，
    # 因此本函数只做"取图 + 归一化"：不写库、不调用模型、不改动入参文件。
    #
    # 参数：
    #   filename / mime_type / path：上传文件的原始名、MIME 与存储路径；扩展名与 MIME 二者
    #     任一命中即判定格式，所以调用方必须传原始文件名而不是存储 key。
    #   max_images：本次最多返回多少张；不传则取 settings.VISION_MAX_IMAGES_PER_DOCUMENT。
    # 返回：(normalized, warnings)。normalized 每项含 data(内联字节)、mime_type、width、
    #   height，以及 name/location；location 里的 page/slide/sheet/cell/image_index/source
    #   是检索引用的定位来源，展示层依赖它回跳到原文位置。
    # 异常与降级：本函数不向上抛异常。视觉关闭（VISION_ENABLED=False）或上限为 0 时直接返回空；
    #   单张图解码失败、某种格式整体提取失败都只记 warning 后继续。调用方必须把 warnings
    #   透传给用户，否则"确实没有图片"与"识图失败"无法区分。
    # 副作用：只读文件；warning 文案进入 logger，并最终写入知识文档的 error_summary。
    #
    # 视觉能力是开关式特性：关掉后整条图片链路（提取 + 模型识别）一起停用，
    # 保证离线/无视觉模型部署下不会产生半成品文本块。
    if not settings.VISION_ENABLED:
        return [], []
    limit = max(0, max_images or settings.VISION_MAX_IMAGES_PER_DOCUMENT)
    if limit == 0:
        return [], []
    ext = os.path.splitext(filename)[1].lower()
    warnings: list[str] = []
    try:
        if ext == ".pdf" or mime_type == "application/pdf":
            raw_assets = _from_pdf(path, limit, warnings)
        elif ext == ".docx":
            raw_assets = _from_docx(path, limit, warnings)
        elif ext == ".xlsx":
            raw_assets = _from_xlsx(path, limit, warnings)
        elif ext == ".pptx":
            raw_assets = _from_pptx(path, limit, warnings)
        elif ext in DIRECT_IMAGE_EXTENSIONS or mime_type.startswith("image/"):
            # 用户直接上传的图片本身就是一张视觉资产，不需要从容器里解包。
            with open(path, "rb") as file_handle:
                raw_assets = [
                    {
                        "data": file_handle.read(),
                        "name": os.path.basename(filename),
                        "location": {"page": 0, "visual_type": "image_file"},
                    }
                ]
        else:
            # 纯文本、CSV 等无内嵌图片的格式在这里短路，不产生警告。
            raw_assets = []
    except Exception as exc:
        warnings.append(f"图片提取失败：{type(exc).__name__}: {exc}")
        logger.warning("visual extraction failed for %s: %s", filename, exc)
        raw_assets = []

    normalized: list[dict] = []
    # 用内容哈希去重：模板类文档里同一张 LOGO/水印会被复用几十次，
    # 若按出现次数送模型会重复计费并让引用里堆满同名图片。
    seen: set[str] = set()
    for image_index, asset in enumerate(raw_assets):
        if len(normalized) >= limit:
            break
        raw = asset.get("data") or b""
        digest = hashlib.sha256(raw).hexdigest()
        if not raw or digest in seen:
            continue
        seen.add(digest)
        try:
            prepared = _normalize_image(raw)
        except Exception as exc:
            name = asset.get("name") or f"图片 {image_index + 1}"
            warnings.append(f"{name} 无法识别其图片格式：{type(exc).__name__}")
            continue
        if not prepared:
            # _normalize_image 返回 None 表示"尺寸过小，按噪声丢弃"，不算失败，故不告警。
            continue
        prepared.update(
            {
                "name": asset.get("name") or f"图片 {image_index + 1}",
                "location": {
                    **(asset.get("location") or {}),
                    "image_index": image_index,
                    "source": "vision",
                },
            }
        )
        normalized.append(prepared)
    # 只告警不报错：超限是有意的成本控制，抽取到的前 limit 张仍然生效。
    if len(raw_assets) > limit:
        warnings.append(f"文档图片超过上限，仅识别前 {limit} 张")
    return normalized, warnings


def _normalize_image(data: bytes) -> Optional[dict]:
    """把任意来源的图片统一成 VLM 可直接消费的 JPEG 字节。

    关键处理与约束：
        - exif_transpose：手机拍摄/截图常带 EXIF 旋转标记，不先纠正会让模型看到横躺的图。
        - 动图（GIF）只取第 0 帧，避免多帧被当成多张图重复识别。
        - 透明通道会被合成到白底：JPEG 不支持 alpha，直接 convert 会把透明区变黑。
        - 等比例缩放到 VISION_MAX_IMAGE_EDGE 以内，控制模型入参体积。
    返回：
        含 data/mime_type/width/height 的 dict；图片长边小于 VISION_MIN_IMAGE_SIDE 时返回 None
        （调用方按"尺寸过小忽略"处理，不视为失败）。
    异常：
        PIL 无法解码、安装缺失等会抛出，由 extract_visual_assets 捕获并转为 warning。
    """
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened)
        if getattr(image, "is_animated", False):
            image.seek(0)
        image.load()
        width, height = image.size
        if (
            width < settings.VISION_MIN_IMAGE_SIDE
            or height < settings.VISION_MIN_IMAGE_SIDE
        ):
            return None
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        image.thumbnail(
            (settings.VISION_MAX_IMAGE_EDGE, settings.VISION_MAX_IMAGE_EDGE),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90, optimize=True)
        return {
            "data": output.getvalue(),
            "mime_type": "image/jpeg",
            "width": image.width,
            "height": image.height,
        }


def _from_pdf(path: str, limit: int, warnings: list[str]) -> list[dict]:
    """抽取 PDF 视觉资产：优先整页渲染，PyMuPDF 不可用时退化为内嵌图片。

    整页渲染（而非只取内嵌图片）是因为工程图、扫描件、图表里的信息往往由矢量线条和
    版面共同承载，只抓内嵌位图会丢内容；对应的代价是按 VISION_PDF_DPI 渲染较慢，故只对
    "有图 / 文字极少(<30 字) / 绘制元素>=5" 的页面渲染，纯文字页跳过以省成本。

    warnings 为就地追加：PDF 渲染不可用、某一页图片抽取失败都会写入，但函数本身不抛异常。
    返回的 location 带 0 基 page 与 visual_type(pdf_page / pdf_image)，用于引用定位。
    """
    assets: list[dict] = []
    try:
        import pymupdf as fitz

        document = fitz.open(path)
        try:
            for page_index, page in enumerate(document):
                if len(assets) >= limit:
                    break
                text = page.get_text("text").strip()
                image_count = len(page.get_images(full=True))
                try:
                    drawing_count = len(page.get_drawings())
                except Exception:
                    # get_drawings 在个别损坏页上会失败；拿不到就当作无矢量图，
                    # 仍按文字/内嵌图片这两个信号决定是否渲染，不中断整篇解析。
                    drawing_count = 0
                if not (image_count or len(text) < 30 or drawing_count >= 5):
                    continue
                pixmap = page.get_pixmap(dpi=settings.VISION_PDF_DPI, alpha=False)
                assets.append(
                    {
                        "data": pixmap.tobytes("png"),
                        "name": f"PDF 第 {page_index + 1} 页",
                        "location": {
                            "page": page_index,
                            "visual_type": "pdf_page",
                        },
                    }
                )
        finally:
            document.close()
        return assets
    except Exception as exc:
        warnings.append(f"PDF 页面渲染不可用，改用内嵌图片提取：{type(exc).__name__}")

    # 降级路径：只取 PDF 里内嵌的位图对象，丢失矢量与版面信息，但保证 PDF 仍有视觉输入。
    from pypdf import PdfReader

    reader = PdfReader(path)
    for page_index, page in enumerate(reader.pages):
        if len(assets) >= limit:
            break
        try:
            for image_index, image_file in enumerate(page.images):
                if len(assets) >= limit:
                    break
                assets.append(
                    {
                        "data": image_file.data,
                        "name": image_file.name or f"PDF 图片 {image_index + 1}",
                        "location": {
                            "page": page_index,
                            "visual_type": "pdf_image",
                        },
                    }
                )
        except Exception as exc:
            warnings.append(f"PDF 第 {page_index + 1} 页图片提取失败：{type(exc).__name__}")
    return assets


def _from_docx(path: str, limit: int, warnings: list[str]) -> list[dict]:
    """从 DOCX 的 zip 容器里直接读 ``word/media/`` 下的图片。

    绕过 python-docx 直接解包，是因为需要拿到原始字节而不是已被包装的图片对象，
    且这样对损坏文档更宽容。代价是拿不到图片在正文中的顺序/锚点，故 location 只记
    image_index（同一文件的稳定序号）；Word 无稳定页码，page 固定为 0。
    """
    assets: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        media_names = sorted(
            name
            for name in archive.namelist()
            if name.lower().startswith("word/media/") and not name.endswith("/")
        )
        for image_index, name in enumerate(media_names[:limit]):
            try:
                assets.append(
                    {
                        "data": archive.read(name),
                        "name": os.path.basename(name),
                        "location": {
                            "page": 0,
                            "image_index": image_index,
                            "visual_type": "docx_image",
                        },
                    }
                )
            except Exception as exc:
                warnings.append(f"Word 图片 {image_index + 1} 提取失败：{type(exc).__name__}")
    return assets


def _from_xlsx(path: str, limit: int, warnings: list[str]) -> list[dict]:
    """抽取 XLSX 各工作表内嵌的图片，并记录锚点单元格。

    ``read_only=False``：只读模式拿不到 ``worksheet._images``，因此这里必须用普通模式加载
    （大表会占用更多内存，但图片锚点信息只有普通模式才有）。
    ``_images`` 是 openpyxl 的非公开属性，跨版本可能变动，故取用与解析都按"可能缺失"处理。
    返回的 location 带 sheet 与 cell（如 "C5"），用于让模型/引用知道图片画在哪一格。
    """
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    workbook = load_workbook(path, read_only=False, data_only=True)
    assets: list[dict] = []
    try:
        for worksheet in workbook.worksheets:
            for image_index, image in enumerate(getattr(worksheet, "_images", [])):
                if len(assets) >= limit:
                    return assets
                try:
                    anchor = getattr(image, "anchor", None)
                    marker = getattr(anchor, "_from", None)
                    cell = None
                    if marker is not None:
                        # openpyxl 的列/行都是 0 基，对外文案按 Excel 习惯转成 1 基。
                        cell = (
                            f"{get_column_letter(marker.col + 1)}"
                            f"{marker.row + 1}"
                        )
                    location = {
                        "sheet": worksheet.title,
                        "page": 0,
                        "image_index": image_index,
                        "visual_type": "xlsx_image",
                    }
                    if cell:
                        location["cell"] = cell
                    assets.append(
                        {
                            "data": image._data(),
                            "name": f"{worksheet.title} 图片 {image_index + 1}",
                            "location": location,
                        }
                    )
                except Exception as exc:
                    warnings.append(
                        f"工作表 {worksheet.title} 图片 {image_index + 1} 提取失败："
                        f"{type(exc).__name__}"
                    )
    finally:
        workbook.close()
    return assets


def _from_pptx(path: str, limit: int, warnings: list[str]) -> list[dict]:
    """逐页抽取 PPTX 中的图片，递归展开组合形状。

    必须递归：设计稿里图片常被放进组合/占位组，只看顶层 shapes 会漏掉大部分图。
    只有 PICTURE 类型会被采集，图示(SmartArt)/图表/形状填充图不在范围内。
    page 与 slide 同时写入且都取 0 基，保持与 files/extractor.py 一致，
    让引用跳转对"幻灯片第 N 页"的展示口径统一。
    """
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = Presentation(path)
    assets: list[dict] = []

    def walk_shapes(shapes):
        """深度遍历形状树，产出非分组形状（生成器，避免构造中间列表）。"""
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk_shapes(shape.shapes)
            else:
                yield shape

    for slide_index, slide in enumerate(presentation.slides):
        for image_index, shape in enumerate(walk_shapes(slide.shapes)):
            if len(assets) >= limit:
                return assets
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                continue
            try:
                assets.append(
                    {
                        "data": shape.image.blob,
                        "name": shape.name or f"幻灯片图片 {image_index + 1}",
                        "location": {
                            "slide": slide_index,
                            "page": slide_index,
                            "image_index": image_index,
                            "visual_type": "pptx_image",
                        },
                    }
                )
            except Exception as exc:
                warnings.append(
                    f"PPT 第 {slide_index + 1} 页图片提取失败：{type(exc).__name__}"
                )
    return assets
