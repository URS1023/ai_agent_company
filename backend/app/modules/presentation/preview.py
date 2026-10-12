"""Real PPTX thumbnail rendering and caching for the template catalog."""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import json
from pathlib import Path
from zipfile import ZipFile

from ...config import PROJECT_ROOT, settings

PREVIEW_WIDTH = 800
PREVIEW_HEIGHT = 450
PREVIEW_SUFFIX = ".preview.webp"
BUILTIN_PREVIEW_DIR = (
    Path(PROJECT_ROOT) / "apps" / "web" / "assets" / "presentation-templates"
)


def builtin_preview_url(template_id: str) -> str | None:
    """内置模板预览图的前端地址；磁盘上没有该图时返回 ``None``（绝不返回 404 链接）。"""

    path = BUILTIN_PREVIEW_DIR / f"{template_id}.webp"
    return f"/assets/presentation-templates/{template_id}.webp" if path.exists() else None


def custom_preview_key(storage_key: str) -> str:
    """客户模板预览在对象存储中的键：与源 PPTX 同前缀加 ``.preview.webp``，
    保证删除/覆盖 PPTX 时预览能靠同一前缀被识别出来。"""

    return storage_key + PREVIEW_SUFFIX


def _normalize_image(data: bytes) -> bytes | None:
    """把任意渲染输出统一成 800×450 WebP（等比内缩 + 灰底居中，不裁切不拉伸）。

    有意的"空白检测"：抽样统计非白像素比例，低于 0.1% 视为渲染失败返回 None，
    让调用方回退到结构化预览而不是给用户一块白板。任何解码异常同样返回 None。
    """

    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
            contained = ImageOps.contain(
                image, (PREVIEW_WIDTH, PREVIEW_HEIGHT), method=Image.Resampling.LANCZOS
            )
            canvas = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), "#f4f4f4")
            canvas.paste(
                contained,
                ((PREVIEW_WIDTH - contained.width) // 2, (PREVIEW_HEIGHT - contained.height) // 2),
            )
            # Some local Office renderers report success but return a nearly
            # uniform white page. Treat that as unavailable so callers can
            # show a truthful structural preview instead of a blank panel.
            sample = canvas.resize((100, 56), Image.Resampling.BILINEAR)
            pixels = sample.load()
            meaningful = sum(
                1 for y in range(sample.height) for x in range(sample.width)
                if min(pixels[x, y]) < 235
            )
            if meaningful / max(1, sample.width * sample.height) < 0.001:
                return None
            output = io.BytesIO()
            canvas.save(output, format="WEBP", quality=86, method=6)
            return output.getvalue()
    except Exception:
        return None


def _embedded_thumbnail(pptx_path: str) -> bytes | None:
    """读取 PPTX 包内自带的 ``docProps/thumbnail.*``（作者保存时嵌入的缩略图）。

    这是最省成本的预览来源（无需渲染进程），但可能是旧版本内容，因此调用方可用
    ``allow_embedded_thumbnail`` 控制优先级。
    """

    try:
        with ZipFile(pptx_path) as package:
            names = {name.lower(): name for name in package.namelist()}
            for candidate in (
                "docprops/thumbnail.jpeg",
                "docprops/thumbnail.jpg",
                "docprops/thumbnail.png",
            ):
                if candidate in names:
                    return package.read(names[candidate])
    except Exception:
        return None
    return None


def _runtime_paths() -> tuple[Path | None, Path | None]:
    """定位本地渲染运行时，返回 ``(node 可执行文件, node_modules 目录)``。

    优先用配置/环境变量，其次探测 ``~/.cache/codex-runtimes/...`` 的随运行时安装；
    三者（node、node_modules、artifact-tool 入口）缺任一项即返回 ``(None, None)``，
    调用方据此判定"本机不能渲染预览"，改为回退方案而不是抛错。
    """

    node_value = settings.PRESENTATION_PREVIEW_NODE or os.environ.get("RUNTIME_NODE")
    modules_value = settings.PRESENTATION_PREVIEW_NODE_MODULES or os.environ.get("RUNTIME_NODE_MODULES")
    node = Path(node_value) if node_value else None
    modules = Path(modules_value) if modules_value else None
    runtime_root = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
    if not node or not node.exists():
        windows_node = runtime_root / "node" / "bin" / "node.exe"
        unix_node = runtime_root / "node" / "bin" / "node"
        node = windows_node if windows_node.exists() else unix_node if unix_node.exists() else None
    if not modules or not modules.exists():
        candidate = runtime_root / "node" / "node_modules"
        modules = candidate if candidate.exists() else None
    artifact_entry = modules / "@oai" / "artifact-tool" / "dist" / "artifact_tool.mjs" if modules else None
    if not node or not node.exists() or not artifact_entry or not artifact_entry.exists():
        return None, None
    return node, modules


def render_pptx_preview(
    source: bytes | str | Path,
    *,
    slide_number: int = 1,
    allow_embedded_thumbnail: bool = True,
) -> bytes | None:
    """Render one real PPTX page to a normalized WebP thumbnail.

    优先级：首页且允许时先用 PPTX 内嵌缩略图（快、无副作用），失败再调用本地 node 渲染脚本
    ``render_preview.mjs``（子进程，75 秒超时）。字节输入会落到临时文件并在 finally 中清理；
    **任何失败都返回 None 而不抛异常**，并保留原始 PPTX 不受影响。
    """
    temporary_input: Path | None = None
    try:
        if isinstance(source, bytes):
            handle = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
            handle.write(source)
            handle.close()
            temporary_input = Path(handle.name)
            input_path = temporary_input
        else:
            input_path = Path(source)
        if allow_embedded_thumbnail and slide_number == 1:
            embedded = _embedded_thumbnail(str(input_path))
            if embedded:
                normalized = _normalize_image(embedded)
                if normalized:
                    return normalized
        node, modules = _runtime_paths()
        if not node or not modules:
            return None
        with tempfile.TemporaryDirectory(prefix="ppt-real-preview-") as temp_dir:
            output_path = Path(temp_dir) / "preview.png"
            environment = dict(os.environ)
            environment["ARTIFACT_TOOL_MODULES"] = str(modules)
            command = [
                str(node),
                str(Path(__file__).with_name("render_preview.mjs")),
                "--input", str(input_path),
                "--output", str(output_path),
                "--slide", str(max(1, slide_number)),
                "--scale", "0.72",
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=75,
                check=False,
                env=environment,
            )
            if completed.returncode or not output_path.exists():
                return None
            return _normalize_image(output_path.read_bytes())
    except Exception:
        return None
    finally:
        if temporary_input:
            temporary_input.unlink(missing_ok=True)


def render_pptx_preview_sheet(
    source: bytes | str | Path,
    *,
    slide_numbers: tuple[int, ...] = (1, 4, 8, 10),
) -> bytes | None:
    """Render real slides once and compose a compact full-bleed contact sheet.

    一次子进程渲染多页（默认第 1/4/8/10 页，90 秒超时），再拼成 1000×563 的 2×2 拼版缩略图
    （页数必须与请求一致，否则视为失败）。用于"一次看到多页风格"的模板预览；
    失败同样返回 None。
    """
    temporary_input: Path | None = None
    try:
        if isinstance(source, bytes):
            handle = tempfile.NamedTemporaryFile(suffix=".pptx", delete=False)
            handle.write(source)
            handle.close()
            temporary_input = Path(handle.name)
            input_path = temporary_input
        else:
            input_path = Path(source)
        node, modules = _runtime_paths()
        if not node or not modules:
            return None
        with tempfile.TemporaryDirectory(prefix="ppt-preview-sheet-") as temp_dir:
            output_dir = Path(temp_dir) / "pages"
            environment = dict(os.environ)
            environment["ARTIFACT_TOOL_MODULES"] = str(modules)
            command = [
                str(node), str(Path(__file__).with_name("render_preview.mjs")),
                "--input", str(input_path), "--output", str(output_dir),
                "--slides", ",".join(str(value) for value in slide_numbers),
                "--scale", "0.72",
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False, env=environment)
            if completed.returncode:
                return None
            payload = json.loads(completed.stdout)
            paths = [Path(value) for value in payload.get("paths") or []]
            if len(paths) != len(slide_numbers) or not all(path.exists() for path in paths):
                return None
            from PIL import Image

            canvas = Image.new("RGB", (1000, 563), "#E3E3E3")
            placements = (
                (8, 8, 488, 271), (504, 8, 488, 271),
                (8, 287, 488, 268), (504, 287, 488, 268),
            )
            for path, (x, y, width, height) in zip(paths, placements):
                with Image.open(path) as raw:
                    page = raw.convert("RGB")
                    page.thumbnail((width, height), Image.Resampling.LANCZOS)
                    frame = Image.new("RGB", (width, height), "#FFFFFF")
                    frame.paste(page, ((width - page.width) // 2, (height - page.height) // 2))
                    canvas.paste(frame, (x, y))
            output = io.BytesIO()
            canvas.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except Exception:
        return None
    finally:
        if temporary_input:
            temporary_input.unlink(missing_ok=True)


def cache_custom_preview(storage, storage_key: str, pptx_data: bytes) -> bool:
    """渲染首页预览并写入对象存储；返回是否成功。

    上传客户模板时同步调用，缓存意味着后续目录列表不必再起渲染进程。渲染不出真实预览时
    返回 False（上传仍然成功，前端回退到占位配色预览）。
    """
    preview = render_pptx_preview(pptx_data, slide_number=1)
    if not preview:
        return False
    storage.put_bytes(custom_preview_key(storage_key), preview)
    return True
