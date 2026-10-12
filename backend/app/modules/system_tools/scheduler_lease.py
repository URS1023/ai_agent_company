"""Single-process lease for the business scheduler.

The HTTP and HTTPS launchers may point at the same project and database.  A
kernel file lock lets either listener run the scheduler while preventing both
from dispatching the same due jobs on one host.  The lock is released by the
operating system when its owning process exits, including an unclean exit.

中文补充（调度器租约/互斥）：
- 用途：HTTP 与 HTTPS 两个启动器可能指向同一项目与同一数据库，调度器必须保证
  同一台主机上只有一个监听进程真正派发到期任务；租约就是这道互斥。
- 机制：``.implementation/scheduler.lock`` 上的内核文件锁（Windows msvcrt /
  POSIX flock），非阻塞获取——拿不到就说明另一进程已在跑调度，本进程跳过。
  锁随进程退出（含异常退出）由操作系统释放，因此不会残留死锁。
- 生命周期约定：``acquire()`` 成功后必须保证 ``release()``（或用 with）；
  ``run()`` 会在执行期间把租约置为"活跃"，release 请求遇到活跃计数不为零时延后
  到回调返回后才真正解锁，避免轮询执行到一半锁被撤走。
- 线程安全：内部 _state_lock 保护全部状态；acquire/release 可能被不同线程调用。
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Callable, TypeVar

from app.config import PROJECT_ROOT


LOCK_PATH = PROJECT_ROOT / ".implementation" / "scheduler.lock"
T = TypeVar("T")


class LeaseUnavailable(RuntimeError):
    """Raised when a queued poll starts after this lease was released.

    中文补充：表示"这条排队的轮询在租约释放后才开始执行"——属于正常的关闭竞态，
    调用方应按"跳过本轮"处理（记录日志即可），不要当作错误重试。
    """


class SchedulerLease:
    """Non-blocking cross-platform process lease.

    中文补充：代表"本进程是否持有调度权"。acquire 非阻塞、拿不到立即返回 False
    （调用方负责决定跳过还是退出），不做等待重试——调度器宁可少跑一轮，
    也不要多个进程同时派发同一批任务。
    关键不变式：``acquired`` 为真时一定持有内核锁；``_active`` 记录正在执行的轮询数，
    只有它为 0 时才真正释放锁。
    """

    def __init__(self, path: Path = LOCK_PATH):
        """初始化租约状态；默认锁文件位于项目 .implementation 目录（运行期数据，不提交）。"""
        self.path = Path(path)
        self._handle = None
        self.acquired = False
        self._state_lock = Lock()
        self._active = 0
        self._release_requested = False

    def acquire(self) -> bool:
        """尝试非阻塞获取租约；已持有则返回当前是否仍有效。

        返回 True 表示可以开始派发任务，False 表示其它进程持有锁或文件不可用
        （打不开锁文件时按"拿不到"处理，绝不降级为无锁运行）。
        副作用：创建并锁定锁文件（首次写入一个空字节以兼容 Windows 区域锁定）。
        """
        with self._state_lock:
            if self.acquired:
                return not self._release_requested
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
        except (OSError, ValueError):
            return False
        try:
            # Reserve one byte so Windows msvcrt.locking also works for a new
            # lock file.  The byte has no application meaning.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError):
            handle.close()
            return False
        with self._state_lock:
            # A same-instance release can race the filesystem operation. Do
            # not resurrect a lease whose owner already requested shutdown.
            if self._release_requested:
                self._unlock_handle(handle)
                return False
            self._handle = handle
            self.acquired = True
            self._active = 0
            return True

    def run(self, callback: Callable[[], T]) -> T:
        """Run one poll while keeping the OS lease until the thread returns.

        中文补充：执行一次轮询，期间保持租约不被释放（_active 计数 +1）；
        租约已释放或未持有时抛 LeaseUnavailable，由调用方按"跳过本轮"处理。
        返回：callback 的返回值；finally 中收尾，最后一个活跃轮询结束后才真正解锁。
        """
        with self._state_lock:
            if not self.acquired or self._release_requested:
                raise LeaseUnavailable("scheduler lease is not available")
            self._active += 1
        try:
            return callback()
        finally:
            with self._state_lock:
                self._active -= 1
                if self._release_requested and self._active == 0:
                    self._close_locked()

    def release(self) -> None:
        """请求释放租约：没有轮询在执行时立即解锁，否则等最后一轮结束后再解。

        可以重复调用（幂等）；未持有租约时直接返回。释放后本对象不可再用于 run()。
        """
        with self._state_lock:
            if not self.acquired:
                return
            self._release_requested = True
            if self._active:
                return
            self._close_locked()

    def _close_locked(self) -> None:
        """（需持 _state_lock）清空状态并解锁句柄；即使解锁失败也要关闭描述符。"""
        handle = self._handle
        self._handle = None
        self.acquired = False
        self._release_requested = False
        if handle is None:
            return
        self._unlock_handle(handle)

    @staticmethod
    def _unlock_handle(handle) -> None:
        """释放内核锁并关闭句柄；解锁失败不抛错（关闭描述符同样会释放锁）。"""
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            # Closing the descriptor still releases the kernel lease.  There
            # is no useful recovery action for a lock that is already closed.
            pass
        finally:
            handle.close()

    def __enter__(self) -> "SchedulerLease":
        """支持 with 语法；注意 acquire 的返回值被忽略，拿不到租约时仍是进入状态，
        因此调用方在 with 内应检查 ``self.acquired``（或用 run() 让它抛 LeaseUnavailable）。"""
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        """退出 with 时释放租约（异常退出同样释放，保证不会残留锁）。"""
        self.release()
