"""Explicit enqueue scheduler CLI: poll once or run until SIGINT/SIGTERM.

Only server ENTERPRISE_* configuration selects identity and files. This command
does not dispatch workflows, migrate, create grants, or start with missing config.
Exit 0 is successful poll/graceful stop, not business assessment success; 1 is
configuration/operation failure, 2 argument error, 3 partial poll, 130 interruption.
Periodic reports stream during execution; single-poll output follows cleanup.
Ownership snapshot checks do not replace the pending durable lease integration.
"""

import argparse
import asyncio
import json
import signal
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import asdict
from types import FrameType
from typing import NoReturn

from enterprise_platform.application.schedule_loop import ScheduleLoopReport
from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.scheduler_runtime import SchedulerRuntime


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, '{"code":"scheduler_invalid_arguments"}\n')


def _emit(report: ScheduleLoopReport) -> None:
    sys.stdout.write(json.dumps(asdict(report), separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def _execute(scheduler: SchedulerRuntime, action: str) -> ScheduleLoopReport | None:
    if action == "poll":
        principal = await scheduler.identity()
        items = await scheduler.poller.poll(principal, limit=scheduler.configuration.policy.batch_limit)
        failed = any(item.status in {"error", "denied", "conflict"} for item in items)
        return ScheduleLoopReport("degraded" if failed else "ok", items)

    stop = asyncio.Event()
    event_loop = asyncio.get_running_loop()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        event_loop.call_soon_threadsafe(stop.set)

    async def report(value: ScheduleLoopReport) -> None:
        _emit(value)

    with ExitStack() as handlers:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous = signal.signal(signum, request_stop)
            handlers.callback(signal.signal, signum, previous)
        await scheduler.create_loop(report).run(stop)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        description="Poll or run enterprise enqueue scheduling; no workflow dispatch", allow_abbrev=False
    )
    parser.add_argument("action", choices=("poll", "run"))
    args = parser.parse_args(argv)
    try:
        runtime = create_runtime(Settings.from_environment())
    except KeyboardInterrupt:
        return 130
    except Exception:
        sys.stderr.write('{"code":"scheduler_configuration_invalid"}\n')
        return 1

    result = None
    error: str | None = None
    code = 0
    try:
        if runtime.scheduler is None:
            error, code = "scheduler_disabled", 1
        else:
            result = asyncio.run(_execute(runtime.scheduler, args.action))
    except (KeyboardInterrupt, asyncio.CancelledError):
        error, code = "scheduler_interrupted", 130
    except Exception:
        error, code = "scheduler_operation_failed", 1
    finally:
        try:
            runtime.close()
        except Exception:
            error, code = "scheduler_shutdown_failed", 1
    if error is not None:
        sys.stderr.write(json.dumps({"code": error}, separators=(",", ":")) + "\n")
        return code
    if result is not None:
        try:
            _emit(result)
        except (OSError, ValueError):
            sys.stderr.write('{"code":"scheduler_report_failed"}\n')
            return 1
        return 3 if result.status != "ok" else 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
