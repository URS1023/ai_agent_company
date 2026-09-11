import asyncio
from unittest.mock import AsyncMock, create_autospec

import pytest
from pydantic import ValidationError
from test_business_service import principal
from test_schedule_tick_service import fixture as tick_fixture
from test_scheduling import START

from enterprise_platform.application.schedule_loop import ScheduleLoop, ScheduleLoopPolicy
from enterprise_platform.application.schedule_polling import SchedulePoller, SchedulePollItem


def execute(poll_results, *, identities=None, stop_before=False):
    async def scenario():
        stop = asyncio.Event()
        if stop_before:
            stop.set()
        poller = create_autospec(SchedulePoller, instance=True)
        poller.poll.side_effect = poll_results
        identity = AsyncMock(side_effect=identities) if identities is not None else AsyncMock(return_value=principal())
        reports = []

        async def report(value):
            reports.append(value)
            if len(reports) == len(poll_results):
                stop.set()

        loop = ScheduleLoop(
            poller,
            identity,
            report,
            workspace_id="w",
            actor_id="a",
            policy=ScheduleLoopPolicy(interval_seconds=2, max_backoff_seconds=8, batch_limit=4),
        )
        loop._wait = AsyncMock()
        await loop.run(stop)
        return poller, identity, reports, loop._wait

    return asyncio.run(scenario())


def test_loop_refreshes_identity_every_round_and_stops_without_extra_poll() -> None:
    poller, identity, reports, wait = execute([(), ()])
    assert identity.await_count == poller.poll.await_count == 2
    assert [r.status for r in reports] == ["ok", "ok"]
    poller.poll.assert_awaited_with(principal(), limit=4)
    assert wait.await_args.args[1] == 2


def test_pre_stopped_loop_has_no_identity_or_database_work() -> None:
    poller, identity, reports, _ = execute([()], stop_before=True)
    assert reports == []
    identity.assert_not_awaited()
    poller.poll.assert_not_awaited()


def test_failures_back_off_with_cap_and_success_resets_delay_without_exposing_error() -> None:
    _, _, reports, wait = execute([RuntimeError("credential-secret")] * 4 + [(), ()])
    assert [call.args[1] for call in wait.await_args_list] == [2, 4, 8, 8, 2]
    assert [r.status for r in reports] == ["error"] * 4 + ["ok", "ok"]
    assert "credential-secret" not in repr(reports)


def test_partial_batch_errors_trigger_backoff_and_preserve_successful_run_ids() -> None:
    batch = (SchedulePollItem("s1", "error"), SchedulePollItem("s2", "queued", "run-2"))
    _, _, reports, wait = execute([batch, batch, ()])
    assert reports[0].status == "degraded" and reports[0].items == batch
    assert [call.args[1] for call in wait.await_args_list] == [2, 4]


def test_changed_identity_scope_is_rejected_without_reusing_previous_identity() -> None:
    poller, identity, reports, _ = execute(
        [(), ()], identities=[principal(), principal().model_copy(update={"workspace_id": "other"})]
    )
    assert identity.await_count == 2 and poller.poll.await_count == 1
    assert [r.status for r in reports] == ["ok", "error"]


def test_cancellation_propagates_without_becoming_retryable_error() -> None:
    with pytest.raises(asyncio.CancelledError):
        execute([asyncio.CancelledError()])


@pytest.mark.parametrize(
    "patch",
    [{"interval_seconds": 0}, {"interval_seconds": float("nan")}, {"max_backoff_seconds": 1}, {"batch_limit": 1001}],
)
def test_invalid_loop_policy_is_rejected(patch) -> None:
    with pytest.raises(ValidationError):
        ScheduleLoopPolicy.model_validate({"interval_seconds": 2, "max_backoff_seconds": 8, "batch_limit": 4} | patch)


def test_wait_is_interrupted_by_stop_signal() -> None:
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(ScheduleLoop._wait(stop, 60))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def test_report_failure_stops_host_instead_of_silently_losing_receipt() -> None:
    async def scenario():
        poller = create_autospec(SchedulePoller, instance=True)
        poller.poll.return_value = ()
        report = AsyncMock(side_effect=RuntimeError("sink_failed"))
        loop = ScheduleLoop(
            poller,
            AsyncMock(return_value=principal()),
            report,
            workspace_id="w",
            actor_id="a",
            policy=ScheduleLoopPolicy(),
        )
        with pytest.raises(RuntimeError, match="sink_failed"):
            await loop.run(asyncio.Event())
        poller.poll.assert_awaited_once()

    asyncio.run(scenario())


def test_stop_during_identity_lookup_prevents_following_poll() -> None:
    async def scenario():
        stop = asyncio.Event()
        poller = create_autospec(SchedulePoller, instance=True)

        async def identity():
            stop.set()
            return principal()

        report = AsyncMock()
        loop = ScheduleLoop(poller, identity, report, workspace_id="w", actor_id="a", policy=ScheduleLoopPolicy())
        await loop.run(stop)
        poller.poll.assert_not_awaited()
        report.assert_not_awaited()

    asyncio.run(scenario())


def test_real_loop_poller_and_service_chain_enqueues_once_then_observes_advanced_cursor() -> None:
    async def scenario():
        service, repo, _, _, actor, current = tick_fixture()
        commit = repo.commit_tick.side_effect

        def persist(old, spec):
            result = commit(old, spec)
            repo.get.return_value = result.schedule
            return result

        repo.commit_tick.side_effect = persist
        repo.list_due.side_effect = lambda *args, **kwargs: (
            (repo.get.return_value,) if repo.get.return_value.next_due_at <= START else ()
        )
        stop = asyncio.Event()
        reports = []

        async def report(value):
            reports.append(value)
            if len(reports) == 2:
                stop.set()

        loop = ScheduleLoop(
            SchedulePoller(repo, service, clock=lambda: START),
            AsyncMock(return_value=actor),
            report,
            workspace_id=actor.workspace_id,
            actor_id=actor.actor_id,
            policy=ScheduleLoopPolicy(),
        )
        loop._wait = AsyncMock()
        await loop.run(stop)
        assert reports[0].items[0].status == "queued" and reports[1].items == ()
        assert reports[0].items[0].schedule_id == current.id
        repo.commit_tick.assert_called_once()

    asyncio.run(scenario())
