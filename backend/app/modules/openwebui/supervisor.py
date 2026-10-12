"""启动同仓库的独立 Open WebUI 进程，禁止静默回退到旧执行引擎。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import httpx

ROOT = Path(__file__).resolve().parents[4]


def backend_ready() -> bool:
    """验证内部认证健康接口，避免误把同端口的其他服务当作 Open WebUI。"""
    data = Path(os.environ.get("OPENWEBUI_DATA_DIR", ROOT / "storage/openwebui"))
    try:
        key = (data / "bridge.key").read_text(encoding="utf-8").strip()
        port = int(os.environ.get("OPENWEBUI_PORT", "8012"))
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2, trust_env=False,
                             headers={"X-Enterprise-Bridge-Key": key})
        return response.status_code == 200 and response.json().get("status") is True
    except (OSError, ValueError, httpx.HTTPError):
        return False


def ensure_backend():
    """复用健康实例或启动完整后端；启动等待上限只针对进程初始化。"""
    if backend_ready():
        return None
    interpreter = ROOT / (".venv_openwebui/Scripts/python.exe" if os.name == "nt" else ".venv_openwebui/bin/python")
    if not interpreter.is_file():
        raise RuntimeError("缺少 Open WebUI 独立环境，请先运行 scripts/install_openwebui.ps1")
    # 子进程只继承系统运行环境；业务密钥与数据库变量不进入独立后端。
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
               "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
               "OPENWEBUI_PORT", "OPENWEBUI_DATA_DIR"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONUTF8"] = "1"
    process = subprocess.Popen([str(interpreter), "-X", "utf8", str(ROOT / "services/openwebui/serve.py")],
        cwd=str(ROOT), env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Open WebUI 启动失败，请查看 storage/openwebui/logs")
        if backend_ready():
            return process
        time.sleep(0.5)
    process.terminate()
    raise RuntimeError("Open WebUI 初始化未完成，请查看启动日志；尚未切换到旧引擎")
