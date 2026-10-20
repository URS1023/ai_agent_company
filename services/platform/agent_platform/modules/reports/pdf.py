"""将已转义的独立HTML交给本机浏览器打印，不访问外部URL或数据源。"""
import json
import os
import signal
import shutil
import subprocess

from agent_platform.core.errors import DomainError
from agent_platform.core.settings import PROJECT_ROOT


def render_pdf(content: str, *, format="pdf", width=1440, height=900) -> bytes:
    script = PROJECT_ROOT / "tools" / "render_report.mjs"
    node = shutil.which("node")
    if not node or not script.is_file():
        raise DomainError(503, "pdf_renderer_not_configured", "PDF渲染服务尚未配置")
    if format not in ("pdf", "png"):
        raise DomainError(422, "render_format_invalid", "请选择PDF或PNG")
    if width > 4096 or height > 16000:
        raise DomainError(422, "render_canvas_too_large", "画布超过渲染尺寸预算")
    encoded = json.dumps({"html": content, "format": format, "width": width, "height": height}, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 5 * 1024 * 1024:
        raise DomainError(422, "pdf_content_too_large", "PDF内容超过预算，请缩小数据范围")
    try:
        process = subprocess.Popen([node, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(PROJECT_ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
        try:
            output, _ = process.communicate(encoded, timeout=25)
        except subprocess.TimeoutExpired:
            # 超时终止整个本机渲染进程树，防止Chromium留在后台继续占用资源。
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            raise
    except subprocess.TimeoutExpired as exc:
        raise DomainError(504, "pdf_render_timeout", "PDF渲染超时，请减少报表内容") from exc
    except OSError as exc:
        raise DomainError(503, "pdf_renderer_unavailable", "PDF渲染服务暂不可用") from exc
    header = b"%PDF-" if format == "pdf" else b"\x89PNG\r\n\x1a\n"
    if process.returncode or not output.startswith(header) or len(output) > 20 * 1024 * 1024:
        raise DomainError(502, "pdf_render_failed", "PDF生成失败，请检查渲染服务")
    return output
