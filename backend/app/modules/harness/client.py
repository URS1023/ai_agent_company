"""通过受控本机子进程使用逐行 JSON-RPC，不代理模型请求。"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from app.config import settings
from app.modules.runtime.task_control import TaskControl
from app.modules.system_tools.scheduler_lease import SchedulerLease

logger = logging.getLogger(__name__)

# 活跃会话注册表：供归档清理判断目录是否仍被进程占用（R20），避免删除正在使用的会话目录。
_ACTIVE_SESSIONS: set = set()
_ACTIVE_LOCK = threading.Lock()


def active_session(identity: str) -> bool:
    """判断该会话是否仍有启动中的原生进程（供归档清理使用）。"""
    with _ACTIVE_LOCK:
        return identity in _ACTIVE_SESSIONS


class HarnessUnavailable(RuntimeError):
    """运行时不可用或未能证明原生任务已完成。"""


@lru_cache(maxsize=4)
def _node_supported(binary: str) -> bool:
    """只读取 Node 版本，不导入上游模块；支持范围与固定上游一致。"""
    try:
        value = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True).stdout.strip().lstrip("v")
        major, minor, *_ = [int(part) for part in value.split(".")]
        return major >= 24 or (major == 22 and minor >= 19)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def runtime_health() -> dict:
    """只检查本机运行文件，不启动进程或触发模型及数据库。"""
    launcher = Path(settings.HARNESS_LAUNCHER)
    upstream = Path(settings.HARNESS_UPSTREAM_ROOT)
    node = shutil.which(settings.HARNESS_NODE)
    checks = {"node": bool(node and _node_supported(node)), "launcher": launcher.is_file(),
              "upstream_build": (upstream / "apps/cli/lib/bin.js").is_file(),
              "company_plugin": (launcher.parent / "lib/company-runtime.js").is_file(),
              "company_preset": (launcher.parent / "lib/company-preset.js").is_file(),
              "company_compaction": (launcher.parent / "lib/company-compaction.js").is_file(),
              "client_registry": (upstream / "packages/typert/registry/lib/index.js").is_file(),
              "client_connection": (upstream / "packages/client/connection/lib/index.js").is_file(),
              "client_file_upload": (upstream / "packages/client/file-upload/lib/index.js").is_file()}
    ready = all(checks.values())
    return {"engine": "harness", "status": "ready" if ready else "unavailable", "ready": ready,
            "scope": "installation", "checks": checks,
            "error_code": None if ready else "harness_runtime_missing"}


class HarnessClient:
    """每个会话独占进程与目录；机器文件租约将并行进程数限制为两个。"""

    def __init__(self, identity: str, control: TaskControl):
        self.identity = identity
        self.control = control
        self.home = Path(settings.HARNESS_HOME_ROOT).resolve() / "sessions" / identity
        self.cwd = self.home / "workspace"
        self.events: queue.Queue = queue.Queue()
        self.process = None
        self._pending: dict[str, Future] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._counter = 0
        self._lease = None
        self._session_lease = None
        self._cancel_sent = False
        self._session_id = identity

    def start(self) -> None:
        """等待可用本机槽位并持有会话级互斥，随后以隐藏窗口启动固定的 Node 入口。

        会话级互斥（评审 R13）：同一会话（身份）只允许一个原生进程，两个并发 Run
        不能同时打开同一 session JSONL；先取会话锁、再取全局槽位，均可取消等待。
        """
        if not runtime_health()["ready"]:
            raise HarnessUnavailable("Harness 运行文件尚未构建，不能启动本次对话")
        while self._lease is None:
            self.control.check()
            if self._session_lease is None:
                session_lease = SchedulerLease(self.home / "session.lock")
                if session_lease.acquire():
                    self._session_lease = session_lease
            if self._session_lease is not None:
                for index in range(settings.HARNESS_MAX_PROCESSES):
                    lease = SchedulerLease(Path(settings.HARNESS_HOME_ROOT) / "slots" / f"{index}.lock")
                    if lease.acquire():
                        self._lease = lease
                        break
            if self._lease is None:
                self.control.cancelled.wait(.2)
        try:
            self.cwd.mkdir(parents=True, exist_ok=True)
            with _ACTIVE_LOCK:
                _ACTIVE_SESSIONS.add(self.identity)
            # 显式清除模型和数据库变量，凭据仅随初始化请求通过私有管道送入。
            allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                       "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS"}
            env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
            env.update({"DSH_HOME": str(self.home), "DSH_UPSTREAM_ROOT": settings.HARNESS_UPSTREAM_ROOT,
                        "DSH_TELEMETRY_DISABLED": "1", "DSH_TELEMETRY_MODE": "DISABLED",
                        "DSH_PRIMARY_RUNTIME": "", "DSH_AGENTS_HOME": str(self.home / "disabled-agents")})
            self.process = subprocess.Popen([settings.HARNESS_NODE, str(Path(settings.HARNESS_LAUNCHER).resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(self.cwd), env=env, text=True, encoding="utf-8", errors="strict", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._read, name="harness-rpc-" + self.identity, daemon=True).start()
            threading.Thread(target=self._drain_stderr, name="harness-stderr-" + self.identity, daemon=True).start()
            threading.Thread(target=self._watch_cancel, name="harness-cancel-" + self.identity, daemon=True).start()
        except Exception:
            self.close()
            raise

    def _drain_stderr(self) -> None:
        """持续排空诊断管道；按会话目录落一份大小受限的审计日志（评审 R5）。

        上游错误可能包含请求信息：不回显到产品或日志；落盘时逐行截断并设总量上限，
        排障可用且不引入敏感内容外泄（路径在会话目录内，随会话隔离）。
        """
        log_path = self.home / "stderr.audit.log"
        limit = 4 * 1024 * 1024
        try:
            with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
                size = fh.seek(0, 2)
                while self.process and self.process.stderr.readable():
                    line = self.process.stderr.readline(8192)
                    if not line:
                        break
                    if size < limit:
                        snippet = line if len(line) <= 512 else line[:512] + "…[truncated]"
                        fh.write(snippet + "\n")
                        size = fh.tell()
        except (OSError, ValueError):
            pass

    def _read(self) -> None:
        """分离通知和响应，使工具执行期间仍可接收取消。"""
        try:
            while self.process:
                line = self.process.stdout.readline(16 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 16 * 1024 * 1024:
                    raise HarnessUnavailable("Harness 事件超过管道大小限制")
                packet = json.loads(line)
                if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0":
                    raise HarnessUnavailable("Harness 返回了无效协议消息")
                if "method" in packet:
                    if packet["method"] == "tool.cancel":
                        self.control.cancelled.set()
                    self.events.put(packet)
                else:
                    with self._lock:
                        future = self._pending.pop(str(packet.get("id")), None)
                    if future and not future.done():
                        if "error" in packet:
                            future.set_exception(HarnessUnavailable("Harness 请求被拒绝"))
                        else:
                            future.set_result(packet.get("result"))
        except (OSError, ValueError, UnicodeError, HarnessUnavailable):
            self.events.put({"method": "transport.failed", "params": {"code": "harness_protocol_error"}})
        finally:
            with self._lock:
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(HarnessUnavailable("Harness 进程连接已关闭"))
                self._pending.clear()
            self.events.put({"method": "transport.closed", "params": {}})

    def _watch_cancel(self) -> None:
        """取消可独立于正在运行的业务工具发送，终态仍等待工具真实结束。"""
        while not self._closed.wait(.1):
            try:
                self.control.check()
            except Exception:
                self.cancel()
                return

    def _write(self, packet: dict) -> None:
        with self._lock:
            if not self.process or self.process.poll() is not None:
                raise HarnessUnavailable("Harness 运行进程已退出")
            self.process.stdin.write(json.dumps(packet, ensure_ascii=False, allow_nan=False) + "\n")
            self.process.stdin.flush()

    def submit(self, method: str, params: dict) -> Future:
        """提交请求并返回响应句柄，通知始终由运行投影独立消费。"""
        with self._lock:
            self._counter += 1
            request_id = "python-" + str(self._counter)
            future = Future()
            self._pending[request_id] = future
            try:
                self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            except Exception:
                self._pending.pop(request_id, None)
                raise
            return future

    def request(self, method: str, params: dict, timeout: float | None = None):
        """有界等待启动或控制请求；整轮生成没有固定的短总时限。"""
        try:
            return self.submit(method, params).result(timeout=timeout or settings.HARNESS_START_TIMEOUT_SECONDS)
        except FutureTimeout as exc:
            raise HarnessUnavailable("Harness 控制请求超时") from exc

    def respond(self, request_id, result: dict) -> None:
        """返回已完成并持久化的工具回执，不额外触发模型调用。"""
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def cancel(self) -> None:
        """只提交一次取消请求，不能以此请求本身证明任务已经停止。"""
        with self._lock:
            if self._cancel_sent or self._closed.is_set():
                return
            self._cancel_sent = True
            try:
                self.submit("session.cancel", {"sessionId": self._session_id})
            except (OSError, HarnessUnavailable):
                pass

    def close(self) -> None:
        """先请求原生释放，再终止残留子进程，进程退出后才释放并发槽位。"""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            with _ACTIVE_LOCK:
                _ACTIVE_SESSIONS.discard(self.identity)
            if self.process and self.process.poll() is None:
                try:
                    self.request("shutdown", {}, timeout=5)
                    self.process.wait(timeout=5)
                except (OSError, HarnessUnavailable, subprocess.TimeoutExpired):
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10, check=False)
                    else:
                        self.process.kill()
                    self.process.wait(timeout=10)
        finally:
            if self.process and self.process.poll() is None:
                # 终止失败时持有槽位，既不冒充退出，也不关闭仍被读取线程占用的管道。
                logger.error("Harness 进程尚未退出，保留并发槽位等待真实退出")
                threading.Thread(target=self._wait_for_exit, name="harness-exit-" + self.identity, daemon=True).start()
            else:
                self._release_exited_process()

    def _wait_for_exit(self) -> None:
        """只观察未退出进程，不自动重复终止或恢复任何工具调用。"""
        try:
            self.process.wait()
        finally:
            if self.process.poll() is not None:
                self._release_exited_process()

    def _release_exited_process(self) -> None:
        """确认进程退出后再关闭管道和释放本机并发槽位。"""
        if self.process and self.process.poll() is None:
            return
        if self.process:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self._lease:
            self._lease.release()
            self._lease = None
        if self._session_lease:
            self._session_lease.release()
            self._session_lease = None
