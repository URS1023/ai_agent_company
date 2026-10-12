# -*- coding: utf-8 -*-
"""Sandbox provider — executes Python in an isolated subprocess worker.

The worker (app/workers/sandbox_runner.py) runs with:
  * a dedicated per-run working directory
  * timeout enforced by the supervisor
  * network disabled (socket patched) unless allowed
  * file APIs restricted by the worker's audit policy (OS isolation is separate)
  * stdout/stderr captured and returned

中文补充（沙箱边界与已知缺口）：
- 本 provider 只负责"起进程 + 收结果"：把代码与开关写进 job.json，用
  ``sys.executable`` 拉起 ``app/workers/sandbox_runner.py`` 子进程执行，
  因此真正的隔离策略集中在 sandbox_runner（_harden / _install_file_audit）。
- 网络开关判定链：调用方 ctx["allow_network"]（由 gateway.build_context 传入，
  其值来自调用方：HTTP/MCP 连接器场景为 spec.provider ∈ {http, mcp}，工作流
  code 节点固定 False）→ job["allow_network"] → 工作进程 _harden(allow_network)
  → 关闭时把 socket.create_connection/getaddrinfo 与 socket.connect/send 系列
  替换为拒绝实现。该链任一环为 False 都会断网，不存在"局部放行"。
- 超时：入参 timeout_seconds 与 settings.SANDBOX_TIMEOUT_SECONDS 取小值，
  且 supervisor 侧额外留 15 秒宽限（超时后按 SandboxError 报错）。
- 产物回收：只接受子进程 workdir 内、且能通过 ``relative_to`` 校验的文件路径，
  越界路径直接跳过，防止符号链接把宿主文件带出来。
- 进程执行入口由 worker 的 _harden 全量替换，并由 audit hook 复核，
  ctypes/_ctypes/_winapi 对用户代码显式禁用；受信任依赖初始化所需的 ctypes
  动态库事件按调用栈放行。这里仍不是恶意代码的强隔离边界，生产环境必须叠加
  一次性容器/VM、只读镜像、非 root UID、seccomp/AppArmor 与 egress 防火墙；
  native 扩展本身不可能仅靠 Python 代码完全限制。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

from ...config import settings
from ...errors import SandboxError
from .base import ToolResult
from .normalizer import normalize

_BASE = Path(__file__).resolve().parents[3]  # backend/


def _runner_path() -> Path:
    """工作进程脚本路径：backend/app/workers/sandbox_runner.py。

    用 __file__ 反推而不是取 settings，保证子进程入口与 provider 同版本，
    避免部署时配置指向旧脚本导致隔离策略不一致。
    """
    return _BASE / "app" / "workers" / "sandbox_runner.py"


def invoke(spec, args: dict, ctx: dict) -> ToolResult:
    """在受限子进程中执行一段 Python 代码并回收结果与产物。

    参数：``args["code"]``（或 ``script``）为待执行代码；``args["timeout_seconds"]``
    可缩短执行时限（会被上限截断，不允许放大）；``args["inputs"]`` 存在时表示
    工作流代码节点，子进程会把 result/main 的输出作为结构化结果返回；
    ``ctx["run_id"]`` 决定运行目录 ``storage/sandbox/run_<id>``；
    ``ctx["allow_network"]`` 决定是否放行网络。
    返回：status=success 的 ToolResult，data 含 result/stdout/文件清单，
    artifacts 为越界校验通过后的文件列表（path/size）。
    异常：缺少代码、时限非有限正数、子进程非 0 退出、结果不是合法 JSON 或
    子进程状态非法，均抛 SandboxError；执行超时也抛 SandboxError。
    副作用：在 storage/sandbox 下创建运行目录、写 job.json、启动子进程，
    并可能产生用户代码生成的文件（不写业务库）。
    """
    code = args.get("code") or args.get("script") or ""
    if not code.strip():
        raise SandboxError("缺少要执行的 Python 代码")
    try:
        requested_timeout = args.get("timeout_seconds")
        timeout = float(requested_timeout if requested_timeout is not None
                        else spec.timeout_seconds or settings.SANDBOX_TIMEOUT_SECONDS)
    except (TypeError, ValueError) as exc:
        raise SandboxError("Python 执行时限必须为正数") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise SandboxError("Python 执行时限必须为有限正数")
    timeout = min(timeout, float(settings.SANDBOX_TIMEOUT_SECONDS))
    workspace_root = Path(settings.STORAGE_ROOT) / "sandbox" / f"run_{ctx.get('run_id')}"
    workspace_root.mkdir(parents=True, exist_ok=True)

    job = {
        "code": code,
        "cwd": str(workspace_root),
        "allow_network": bool(ctx.get("allow_network", False)),
        "timeout_seconds": timeout,
    }
    if "inputs" in args:
        job["inputs"] = args["inputs"]
    job_file = workspace_root / "job.json"
    job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    python = sys.executable
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            [python, str(_runner_path()), str(job_file)],
            capture_output=True, text=True, timeout=timeout + 15, cwd=str(workspace_root),
            encoding="utf-8", errors="replace", env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"Python 执行超时（>{int(timeout)}s）")
    except Exception as exc:
        raise SandboxError(f"无法启动沙箱进程: {exc}")

    if proc.returncode != 0:
        raise SandboxError(f"Python 执行进程异常退出（退出码 {proc.returncode}）")
    try:
        result = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SandboxError("Python 执行进程未返回有效结果") from exc
    if not isinstance(result, dict) or result.get("status") not in {"success", "failed"}:
        raise SandboxError("Python 执行进程返回了无效结果状态")

    if result.get("status") == "failed":
        raise SandboxError(result.get("error") or result.get("stderr") or "Python 执行失败")

    files = []
    meta = {}
    if result.get("output_files"):
        for fname in result["output_files"]:
            p = (workspace_root / fname).resolve()
            try:
                p.relative_to(workspace_root.resolve())
            except ValueError:
                continue
            if p.is_file():
                files.append({"name": fname, "path": str(p), "size": p.stat().st_size})
    for key in ("stdout", "stderr"):
        if result.get(key):
            meta[key] = result[key][:8000]
    out = normalize({
        "status": "success",
        "data": {"result": result.get("result"), "stdout": result.get("stdout", ""),
                 "files": [f["name"] for f in files]},
        "metadata": meta,
    }, tool_name=spec.name)
    out.artifacts = files
    return out
