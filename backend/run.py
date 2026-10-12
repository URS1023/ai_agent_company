# -*- coding: utf-8 -*-
"""统一启动 DSH 企业平台宿主，Python 业务进程由宿主管理。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

# 配置只读取环境；启动器不导入数据库、业务路由或旧聊天引擎。
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def host_command() -> tuple[list[str], dict[str, str]]:
    """解析固定宿主环境，模型仍由已登录工作区在运行时选择。"""
    node = shutil.which(os.environ.get("DSH_NODE") or settings.HARNESS_NODE)
    if not node:
        raise RuntimeError("未找到 Node.js，请安装 Node 22.19+（22.x）或 Node 24+。")
    entry = ROOT / "services" / "dsh_host" / "launch.mjs"
    if not entry.is_file():
        raise RuntimeError(f"DSH 平台入口不存在：{entry}")
    env = dict(os.environ)
    env.update(DSH_PROJECT_ROOT=str(ROOT), DSH_PYTHON=sys.executable,
               DSH_UPSTREAM_ROOT=env.get("DSH_UPSTREAM_ROOT") or settings.HARNESS_UPSTREAM_ROOT,
               PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    env.setdefault("BUSINESS_APPS_MANIFEST", str(ROOT / "configurations" / "business_apps.json"))
    env.setdefault("HOST", "0.0.0.0")
    env.setdefault("PORT", "8000")
    env.setdefault("DSH_TELEMETRY_DISABLED", "1")
    if not 0 <= int(env["PORT"]) <= 65535:
        raise RuntimeError("PORT 必须位于 0 至 65535；0 仅供本机隔离验收分配端口。")
    cert, key = (env.get(name, "").strip() for name in ("SSL_CERTFILE", "SSL_KEYFILE"))
    if bool(cert) != bool(key):
        raise RuntimeError("HTTPS 必须同时设置 SSL_CERTFILE 和 SSL_KEYFILE。")
    for name, value in (("SSL_CERTFILE", cert), ("SSL_KEYFILE", key)):
        if value:
            path = Path(value).resolve()
            if not path.is_file():
                raise RuntimeError(f"HTTPS 文件不存在：{path}")
            env[name] = str(path)
    return [node, str(entry)], env


def check_installation(command: list[str], env: dict[str, str]) -> dict:
    """检查源快照版本与构建文件，不连接模型或数据库。"""
    raw = subprocess.check_output([command[0], "--version"], text=True).strip()
    major, minor, *_ = map(int, raw.lstrip("v").split("."))
    if not (major >= 24 or major == 22 and minor >= 19):
        raise RuntimeError(f"Node 版本不支持 DSH：{raw}")
    upstream = Path(env["DSH_UPSTREAM_ROOT"]).resolve()
    for relative in ("package.json", "apps/cli/lib/bin.js", "packages/api/session-controller/lib/index.js"):
        if not (upstream / relative).is_file():
            raise RuntimeError(f"DSH 构建缺失：{relative}；请执行 python scripts/install_harness.py。")
    version = json.loads((upstream / "package.json").read_text(encoding="utf-8"))["version"]
    lock = json.loads((ROOT / "services/deepseek_harness/UPSTREAM.json").read_text(encoding="utf-8"))
    if version != lock["version"]:
        raise RuntimeError(f"DSH 上游版本不匹配：期望 {lock['version']}，实际 {version}。")
    manifest = json.loads(Path(env["BUSINESS_APPS_MANIFEST"]).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not manifest.get("plugins"):
        raise RuntimeError("业务插件目录无效。")
    return {"engine": "dsh", "node": raw, "upstream_version": version,
            "plugins": len(manifest["plugins"]), "worker": env["DSH_PYTHON"],
            "host": env["HOST"], "port": int(env["PORT"])}


def _ensure_opencode_proxy() -> subprocess.Popen | None:
    """兼容已配置的本机模型代理；本次创建的进程随宿主关闭。"""
    import socket
    try:
        with socket.create_connection((settings.OPCODE_PROXY_HOST, settings.OPCODE_PROXY_PORT), timeout=1):
            return None
    except OSError:
        pass
    # 代理只需要目标地址，不继承平台数据库或模型凭据。
    proxy_env = {name: value for name, value in os.environ.items()
                 if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "PYTHONUTF8"}}
    return subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "services/opencode_proxy.py"),
         "--host", settings.OPCODE_PROXY_HOST, "--port", str(settings.OPCODE_PROXY_PORT),
         "--target", settings.OPCODE_PROXY_TARGET], env=proxy_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True,
    )


def stop_host(process: subprocess.Popen) -> None:
    """先通知宿主收敛会话与 Worker，超时后只终止本次启动的进程树。"""
    if process.poll() is not None:
        return
    try:
        if process.stdin:
            process.stdin.write('{"method":"shutdown"}\n')
            process.stdin.flush()
            process.stdin.close()
        process.wait(timeout=15)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(argv: list[str] | None = None) -> int:
    """保留原启动脚本入口；检查模式不启动任何业务服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="仅检查 DSH 安装和配置，不连接数据库")
    args = parser.parse_args(argv)
    command, env = host_command()
    installation = check_installation(command, env)
    if args.check:
        print(json.dumps(installation, ensure_ascii=False))
        return 0
    proxy = None
    process = None
    try:
        if settings.OPCODE_PROXY_ENABLED:
            proxy = _ensure_opencode_proxy()
        scheme = "https" if env.get("SSL_CERTFILE") else "http"
        print(f"DSH 企业平台：{scheme}://127.0.0.1:{env['PORT']}（{installation['plugins']} 个业务插件）", flush=True)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                   text=True, encoding="utf-8", bufsize=1,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process is not None:
            stop_host(process)
        if proxy is not None and proxy.poll() is None:
            proxy.terminate()
            proxy.wait(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"平台启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
