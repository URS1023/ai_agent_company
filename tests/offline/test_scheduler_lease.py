"""Repeatable offline checks for scheduler lease/thread lifetime semantics.

中文说明：调度器租约与线程生命周期的可重复离线检查（谁持有租约、何时释放、线程如何收尾）。
隔离方式：threading 事件/线程 + 临时目录，不起服务、不连库。
边界：租约释放与线程退出必须成对，避免"幽灵调度"。
"""

from threading import Event, Thread
from pathlib import Path
import subprocess
import sys

from app.modules.system_tools.scheduler_lease import LeaseUnavailable, SchedulerLease


def test_kernel_lease_excludes_other_process_and_recovers_after_process_exit(tmp_path):
    path = tmp_path / "child-owner.lock"
    backend = Path(__file__).resolve().parents[2] / "backend"
    script = (
        "import os, sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); "
        "from app.modules.system_tools.scheduler_lease import SchedulerLease; "
        "lease=SchedulerLease(Path(sys.argv[1])); assert lease.acquire(); "
        "print('lease-held', flush=True); sys.stdin.readline(); os._exit(7)"
    )
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", script, str(path), str(backend)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = []
    ready = Thread(target=lambda: output.append(process.stdout.readline()), daemon=True)
    contender = SchedulerLease(path)
    try:
        ready.start()
        ready.join(5)
        assert output == ["lease-held\n"], "Child did not acquire the lease within the test budget"
        assert process.poll() is None
        assert not contender.acquire()
        # Windows virtualenv launchers can spawn another interpreter process;
        # trigger an abrupt exit in the process that actually holds the lock.
        process.stdin.write("exit\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 7
        assert contender.acquire()
    finally:
        contender.release()
        process.stdin.close()
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for pipe in (process.stdout, process.stderr):
            pipe.close()


def test_release_does_not_allow_takeover_while_poll_thread_is_running(tmp_path):
    path = tmp_path / "scheduler.lock"
    owner = SchedulerLease(path)
    contender = SchedulerLease(path)
    assert owner.acquire()

    entered = Event()
    finish = Event()
    errors: list[BaseException] = []

    def poll():
        def callback():
            entered.set()
            assert finish.wait(5), "poll callback did not receive the test release"

        try:
            owner.run(callback)
        except BaseException as exc:  # pragma: no cover - diagnostic only
            errors.append(exc)

    thread = Thread(target=poll)
    thread.start()
    assert entered.wait(5)

    # This models lifespan cancellation: the await may be cancelled while the
    # worker thread still owns the kernel lock and is finishing its poll.
    owner.release()
    assert not contender.acquire()
    finish.set()
    thread.join(5)
    assert not thread.is_alive()
    assert not errors

    # Only after the callback returns can a second process take over.
    assert contender.acquire()
    contender.release()


def test_queued_poll_rejected_after_release(tmp_path):
    owner = SchedulerLease(tmp_path / "scheduler.lock")
    assert owner.acquire()
    owner.release()

    called = Event()
    errors: list[BaseException] = []

    def queued_poll():
        try:
            owner.run(called.set)
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=queued_poll)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert not called.is_set()
    assert len(errors) == 1
    assert isinstance(errors[0], LeaseUnavailable)
