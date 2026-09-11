import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from test_business_service import principal

from enterprise_platform import scheduler_worker
from enterprise_platform.application.schedule_loop import ScheduleLoop
from enterprise_platform.application.schedule_polling import SchedulePoller, SchedulePollItem
from enterprise_platform.bootstrap import Runtime
from enterprise_platform.scheduler_runtime import SchedulerRuntime


@pytest.fixture
def composition():
    runtime = create_autospec(Runtime, instance=True)
    scheduler = create_autospec(SchedulerRuntime, instance=True)
    scheduler.identity = AsyncMock(return_value=principal())
    scheduler.configuration = SimpleNamespace(policy=SimpleNamespace(batch_limit=10))
    scheduler.poller = create_autospec(SchedulePoller, instance=True)
    scheduler.poller.poll.return_value = (SchedulePollItem("schedule-1", "queued", "run-1"),)
    scheduler.create_loop.return_value = create_autospec(ScheduleLoop, instance=True)
    runtime.scheduler = scheduler
    with (
        patch.object(scheduler_worker.Settings, "from_environment"),
        patch.object(scheduler_worker, "create_runtime", return_value=runtime),
    ):
        yield runtime, scheduler


def test_single_poll_prints_enqueue_receipt_after_cleanup(composition, capsys):
    runtime, scheduler = composition
    runtime.close.side_effect = lambda: print("closed")
    assert scheduler_worker.main(["poll"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "closed"
    result = json.loads(lines[1])
    assert result["status"] == "ok" and result["items"][0]["run_id"] == "run-1"
    scheduler.poller.poll.assert_awaited_once_with(principal(), limit=10)
    scheduler.create_loop.assert_not_called()


def test_partial_poll_has_distinct_exit_status_and_keeps_successful_receipts(composition, capsys):
    _, scheduler = composition
    scheduler.poller.poll.return_value += (SchedulePollItem("schedule-2", "error"),)
    assert scheduler_worker.main(["poll"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "degraded"


def test_disabled_scheduler_never_queries_or_runs(composition, capsys):
    runtime, scheduler = composition
    runtime.scheduler = None
    assert scheduler_worker.main(["poll"]) == 1
    assert "scheduler_disabled" in capsys.readouterr().err
    runtime.close.assert_called_once()
    scheduler.identity.assert_not_awaited()


def test_operation_failure_is_redacted_and_closes_runtime(composition, capsys):
    runtime, scheduler = composition
    scheduler.identity.side_effect = RuntimeError("credential-secret")
    assert scheduler_worker.main(["poll"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "credential-secret" not in output.err
    runtime.close.assert_called_once()


def test_cancellation_closes_runtime_and_has_interrupt_exit_code(composition):
    runtime, scheduler = composition
    scheduler.poller.poll.side_effect = asyncio.CancelledError()
    assert scheduler_worker.main(["poll"]) == 130
    runtime.close.assert_called_once()


def test_continuous_run_installs_and_restores_signal_handlers(composition):
    runtime, scheduler = composition
    with patch.object(scheduler_worker.signal, "signal") as signals:
        assert scheduler_worker.main(["run"]) == 0
    term_calls = [call for call in signals.call_args_list if call.args[0] == scheduler_worker.signal.SIGTERM]
    assert len(term_calls) == 2
    assert term_calls[-1].args[1] is signals.return_value
    scheduler.create_loop.return_value.run.assert_awaited_once()
    runtime.close.assert_called_once()


def test_invalid_arguments_do_not_echo_values_or_load_configuration(capsys):
    with patch.object(scheduler_worker.Settings, "from_environment") as settings:
        with pytest.raises(SystemExit) as error:
            scheduler_worker.main(["poll", "--token", "credential-secret"])
    assert error.value.code == 2
    assert "credential-secret" not in capsys.readouterr().err
    settings.assert_not_called()


def test_shutdown_failure_suppresses_success_receipt(composition, capsys):
    runtime, _ = composition
    runtime.close.side_effect = RuntimeError("private-driver-details")
    assert scheduler_worker.main(["poll"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": "scheduler_shutdown_failed"}


def test_poll_output_failure_is_a_nonzero_result_after_cleanup(composition, capsys):
    runtime, _ = composition
    with patch.object(scheduler_worker, "_emit", side_effect=BrokenPipeError()):
        assert scheduler_worker.main(["poll"]) == 1
    runtime.close.assert_called_once()
    assert "scheduler_report_failed" in capsys.readouterr().err


def test_sigterm_handler_requests_graceful_loop_stop(composition):
    runtime, scheduler = composition
    installed = {}

    def install(signum, handler):
        previous = installed.get(signum, scheduler_worker.signal.SIG_DFL)
        installed[signum] = handler
        return previous

    async def wait_for_stop(stop):
        installed[scheduler_worker.signal.SIGTERM](scheduler_worker.signal.SIGTERM, None)
        await asyncio.wait_for(stop.wait(), timeout=1)

    scheduler.create_loop.return_value.run.side_effect = wait_for_stop
    with patch.object(scheduler_worker.signal, "signal", side_effect=install):
        assert scheduler_worker.main(["run"]) == 0
    assert installed[scheduler_worker.signal.SIGTERM] == scheduler_worker.signal.SIG_DFL
    runtime.close.assert_called_once()
