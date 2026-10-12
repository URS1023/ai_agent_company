"""通过当前 DSH 宿主串行验收完整业务页面；每套使用独立临时平台与受控回环桩。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

from real_platform_smoke import Host, ROOT, PASSWORD


SUITES = ("system", "content", "capability", "graph", "analytics", "alert", "quality", "workflow", "ai_documents", "permissions")
BROWSER_PORT_MIN = 49152
BROWSER_PORT_MAX = 65535
BROWSER_PORT_ATTEMPTS = 32


def browser_host_port():
    """只探测高位回环端口，避免 PORT=0 被系统分配到 6666 等浏览器禁用端口。

    不修改浏览器安全设置，也不触碰占用端口的进程。探测结束后宿主仍需实际 bind；
    若交接期间被其他进程抢占，保持本轮首失败停止，不自动重跑业务套件。
    """
    last_error = None
    for _ in range(BROWSER_PORT_ATTEMPTS):
        port = BROWSER_PORT_MIN + secrets.randbelow(BROWSER_PORT_MAX - BROWSER_PORT_MIN + 1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                # Windows 探测也必须排他，不能通过复用端口误判已有服务为空闲。
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                last_error = error
                continue
            return port
    raise RuntimeError(f"未能在 {BROWSER_PORT_ATTEMPTS} 次探测内取得浏览器可用的本机高位端口") from last_error


def now():
    """证据使用带时区的 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()


class WindowsProcessJob:
    """持有本轮 Node 及后代的内核容器，父进程退出后仍能安全回收 Edge。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [("times", ctypes.c_longlong * 4), ("page_faults", wintypes.DWORD),
                        ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result
        self.ctypes, self.kernel, self.accounting_type = ctypes, kernel, Accounting
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        # 不允许后代脱离本轮容器；关闭句柄即回收所有仍存活的后代。
        limits.basic.flags = 0x2000
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise error

    def assign(self, process):
        """启动许可发出前绑定持有的进程句柄，不依赖可能复用的 PID。"""
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def active(self):
        """读取容器真实存活数，作为清理完成的证据。"""
        value = self.accounting_type()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(value), self.ctypes.sizeof(value), None):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return value.active

    def close(self):
        """只终止本轮容器，并等待后代消失；无论校验结果如何都关闭句柄。"""
        if not self.handle:
            return {"method": "windows_job", "already_closed": True}
        try:
            before = self.active()
            if before and not self.kernel.TerminateJobObject(self.handle, 1):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            deadline = time.monotonic() + 10
            remaining = self.active()
            while remaining and time.monotonic() < deadline:
                time.sleep(0.05)
                remaining = self.active()
            if remaining:
                raise RuntimeError(f"本轮进程容器仍有 {remaining} 个后代未退出")
            return {"method": "windows_job", "active_before": before, "active_after": remaining}
        finally:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def stop_test_process(process, job=None):
    """只回收本次创建的测试进程树，避免超时后遗留 Edge；不按进程名称批量终止。"""
    if job is not None:
        try:
            return job.close()
        finally:
            if process is not None:
                # 赋予容器失败时 Node 仍在等待许可；使用 Popen 持有的句柄结束它。
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
    if process is None:
        return {"method": "not_started"}
    if process.poll() is not None:
        return {"method": "process", "exit_code": process.returncode}
    if os.name == "nt":
        # 仅用于尚未取得启动许可的 Node；此时不得已有浏览器后代。
        process.kill()
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    return {"method": "unreleased_process" if os.name == "nt" else "process_group", "exit_code": process.returncode}


def check_network_evidence(path):
    """浏览器背景候选只单列；未知目标、业务请求和观察器失效均使本轮失败。"""
    evidence = json.loads(path.read_text(encoding="utf-8"))
    required = ("allowed_requests", "blocked_requests", "unexpected_requests", "application_blocked_requests", "background_candidate_requests")
    if any(not isinstance(evidence.get(key), int) or evidence[key] < 0 for key in required):
        raise ValueError("网络隔离证据缺少有效计数")
    if evidence["unexpected_requests"] or evidence["application_blocked_requests"] or evidence.get("observer_errors") or evidence.get("upstream_errors"):
        raise RuntimeError("本轮包含业务所需的未授权网络目标或网络观察器失败，详见 network.json")
    return {key: evidence[key] for key in required}


def finish_case(check, *, child, job, host, case_dir, started):
    """各项清理相互独立；清理失败仍形成完整终态，交由调用方始终写入 manifest。"""
    cleanup_errors = []

    def attempt(label, action):
        try:
            return action()
        except BaseException as error:
            cleanup_errors.append(f"{label}: {type(error).__name__}: {error}")

    check["process_cleanup"] = attempt("browser_process_cleanup", lambda: stop_test_process(child, job))
    if host is not None:
        attempt("host_cleanup", host.close)
        attempt("host_diagnostics", lambda: (case_dir / "host-diagnostics.log").write_text(
            "\n".join(host.diagnostics), encoding="utf-8"))
    if child is not None:
        check["network"] = attempt("network_evidence", lambda: check_network_evidence(case_dir / "network.json"))
    if cleanup_errors:
        check.update(status="failed", cleanup_errors=cleanup_errors)
    if check.get("status") == "running":
        check["status"] = "failed"
    check.update(finished_at=now(), seconds=round(time.monotonic() - started, 3))


def main():
    """先跑单套/单轮，失败即停；不自动重试，不使用部署口令、业务库或真实模型。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suites", nargs="+", choices=SUITES, default=["system"])
    parser.add_argument("--iterations", type=int, choices=range(1, 101), default=1, metavar="1..100")
    parser.add_argument("--case-timeout", type=int, default=900)
    args = parser.parse_args()
    if not 30 <= args.case_timeout <= 1800:
        parser.error("单套观察预算应在 30–1800 秒之间；这不是产品请求时限")
    node = shutil.which("node")
    if not node:
        raise SystemExit("需要已安装的 Node，不自动安装或跳过测试")
    output = ROOT / ".implementation" / "full-business-browser" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    manifest = {"started_at": now(), "status": "running", "mode": "isolated-full-business-browser",
        "scope": "真实Edge+当前DSH宿主/Python Worker；临时SQLite/合成文件/回环设备和模型桩；不连接szh_new或外部模型",
        "iterations": args.iterations, "suites": args.suites, "checks": [],
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True, encoding="utf-8")}

    def save():
        """每完成一套立即落盘，保留失败和未完成记录。"""
        temporary = output / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output / "manifest.json")

    save()
    print(f"证据目录：{output}", flush=True)
    for iteration in range(1, args.iterations + 1):
        for suite in args.suites:
            case_dir = output / f"{iteration:03d}-{suite}"
            case_dir.mkdir()
            check = {"iteration": iteration, "suite": suite, "started_at": now(), "status": "running"}
            manifest["checks"].append(check)
            save()
            started = time.monotonic()
            print(f"开始 第{iteration}轮 {suite}", flush=True)
            # 保留本轮平台状态供失败复核；所有文件仅在忽略的证据目录内，绝不复用在用 storage。
            host = None
            child = None
            job = None
            try:
                platform = case_dir / "platform"
                platform.mkdir()
                host = Host(platform, 9, env_overrides={"PORT": str(browser_host_port())})
                host.ready()
                check["base_url"] = host.base
                permit = case_dir / "process-ready"
                if os.name == "nt":
                    job = WindowsProcessJob()
                env = {**os.environ, "TEST5_E2E_BASE_URL": host.base, "TEST5_E2E_PASSWORD": PASSWORD,
                    # 文件核验沿用本轮隔离解释器，不依赖工作树中的虚拟环境目录或调用者额外导出。
                    "TEST5_E2E_PYTHON": str(Path(sys.executable).resolve()),
                    "TEST5_E2E_ALLOW_WRITE": "isolated", "TEST5_E2E_NETWORK_REPORT": str(case_dir / "network.json"),
                    "TEST5_E2E_PROCESS_GATE": str(permit),
                    "NODE_OPTIONS": "", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
                command = [node, "--import", (ROOT / "tests/dsh/local_network_only.mjs").as_uri(),
                    "--require", str(ROOT / "tests/ui/isolated_browser_guard.cjs"),
                    str(ROOT / "tests/ui" / f"full_browser_{suite}_acceptance.js")]
                with (case_dir / "browser.log").open("w", encoding="utf-8") as log:
                    child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
                    if job is not None:
                        job.assign(child)
                    check["process_boundary"] = "windows_job" if job is not None else "posix_session"
                    permit.write_text("ready", encoding="ascii")
                    try:
                        code = child.wait(timeout=args.case_timeout)
                    except subprocess.TimeoutExpired:
                        code = 124
                    check.update(exit_code=code, status="passed" if code == 0 else "failed")
            except BaseException as error:
                check.update(status="failed", error=f"{type(error).__name__}: {error}")
                if isinstance(error, KeyboardInterrupt):
                    check["interrupted"] = True
            finally:
                try:
                    finish_case(check, child=child, job=job, host=host, case_dir=case_dir, started=started)
                except BaseException as error:
                    check.update(status="failed", cleanup_error=f"{type(error).__name__}: {error}", finished_at=now(),
                                 seconds=round(time.monotonic() - started, 3))
                finally:
                    if check.get("status") != "passed":
                        check["status"] = "failed"
                        manifest.update(status="failed", finished_at=now())
                    save()
            print(f"{check['status']} 第{iteration}轮 {suite}：{check['seconds']}秒", flush=True)
            if check["status"] != "passed":
                log_path = case_dir / "browser.log"
                if log_path.exists():
                    print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
                print(f"首个失败后停止，未执行后续套件：{output / 'manifest.json'}", flush=True)
                return 1
    manifest.update(status="passed", finished_at=now())
    save()
    print(f"全部 {len(manifest['checks'])} 套通过：{output / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
