import asyncio
from datetime import timedelta
from unittest.mock import create_autospec

import pytest
from test_schedule_tick_service import fixture as tick_fixture
from test_scheduling import START

from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, PersistenceError
from enterprise_platform.application.schedule_polling import SchedulePoller
from enterprise_platform.application.schedule_service import ScheduleRepository, ScheduleService


def fixture():
    real, _, _, _, principal, value = tick_fixture()
    committed = asyncio.run(real.tick(principal, value.id))
    repo = create_autospec(ScheduleRepository, instance=True)
    repo.list_due.return_value = (value,)
    service = create_autospec(ScheduleService, instance=True)
    service.tick.return_value = committed
    poller = SchedulePoller(repo, service, clock=lambda: START)
    return poller, repo, service, principal, value


def test_poll_queries_actor_scope_and_returns_only_compact_run_receipt() -> None:
    poller, repo, service, principal, value = fixture()
    result = asyncio.run(poller.poll(principal, limit=7))
    repo.list_due.assert_called_once_with("w", now=START, limit=7, service_actor_id="service", after=None)
    service.tick.assert_awaited_once_with(principal, value.id)
    assert len(result) == 1 and result[0].status == "queued" and result[0].run_id == "run-1"
    assert not hasattr(result[0], "spec") and not hasattr(result[0], "parameters")


@pytest.mark.parametrize(
    "error,status",
    [(Conflict("private"), "conflict"), (AccessDenied("private"), "denied"), (RuntimeError("secret-token"), "error")],
)
def test_one_item_failure_is_reported_without_retry_and_next_item_continues(error, status) -> None:
    poller, repo, service, principal, value = fixture()
    repo.list_due.return_value = (value, value.model_copy(update={"id": "schedule-2"}))
    good = service.tick.return_value
    service.tick.side_effect = [error, good]
    result = asyncio.run(poller.poll(principal))
    assert [item.status for item in result] == [status, "queued"]
    assert service.tick.await_count == 2
    assert "secret-token" not in repr(result) and "private" not in repr(result)


def test_cancelled_poll_propagates_instead_of_continuing_batch() -> None:
    poller, _, service, principal, _ = fixture()
    service.tick.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(poller.poll(principal))
    service.tick.assert_awaited_once()


@pytest.mark.parametrize("patch", [{"workspace_id": "other"}, {"service_actor_id": "other"}])
def test_foreign_scan_receipts_abort_before_any_ticks(patch) -> None:
    poller, repo, service, principal, value = fixture()
    repo.list_due.return_value = (value, value.model_copy(update=patch))
    with pytest.raises(PersistenceError):
        asyncio.run(poller.poll(principal))
    service.tick.assert_not_awaited()


def test_invalid_batch_limit_is_rejected_before_query() -> None:
    poller, repo, _, principal, _ = fixture()
    with pytest.raises(InvalidInput):
        asyncio.run(poller.poll(principal, limit=0))
    repo.list_due.assert_not_called()


def test_scan_failure_is_not_misreported_as_an_empty_batch() -> None:
    poller, repo, service, principal, _ = fixture()
    repo.list_due.side_effect = PersistenceError("database_unavailable")
    with pytest.raises(PersistenceError):
        asyncio.run(poller.poll(principal))
    service.tick.assert_not_awaited()


def test_readonly_actor_is_denied_before_scanning() -> None:
    poller, repo, _, principal, _ = fixture()
    with pytest.raises(AccessDenied):
        asyncio.run(poller.poll(principal.model_copy(update={"workspace_role": "normal"})))
    repo.list_due.assert_not_called()


def test_failed_full_batch_advances_scan_cursor_and_keeps_cutoff_until_sweep_ends() -> None:
    poller, repo, service, principal, value = fixture()
    second = value.model_copy(update={"id": "schedule-2"})
    repo.list_due.side_effect = [(value,), (second,), (), ()]
    service.tick.side_effect = Conflict("persistent_failure")
    times = iter([START, START + timedelta(seconds=60), START + timedelta(seconds=120), START + timedelta(seconds=180)])
    poller._clock = lambda: next(times)

    async def scenario():
        for _ in range(4):
            await poller.poll(principal, limit=1)

    asyncio.run(scenario())
    calls = repo.list_due.call_args_list
    assert calls[1].kwargs["after"].schedule_id == value.id
    assert calls[2].kwargs["after"].schedule_id == second.id
    assert [call.kwargs["now"] for call in calls[:3]] == [START] * 3
    assert calls[3].kwargs["after"] is None
    assert calls[3].kwargs["now"] == START + timedelta(seconds=180)
    assert service.tick.await_count == 2


def test_keyset_scan_receipt_cannot_repeat_previous_failed_candidate() -> None:
    poller, repo, service, principal, _ = fixture()
    service.tick.side_effect = Conflict("persistent_failure")

    async def scenario():
        await poller.poll(principal, limit=1)
        with pytest.raises(PersistenceError):
            await poller.poll(principal, limit=1)

    asyncio.run(scenario())
    assert service.tick.await_count == 1


def test_scope_change_does_not_reuse_another_actors_scan_cursor() -> None:
    poller, repo, service, principal, value = fixture()
    repo.list_due.side_effect = [(value,), ()]
    service.tick.side_effect = Conflict("persistent_failure")

    async def scenario():
        await poller.poll(principal, limit=1)
        await poller.poll(principal.model_copy(update={"actor_id": "other"}), limit=1)

    asyncio.run(scenario())
    assert repo.list_due.call_args.kwargs["after"] is None
    assert repo.list_due.call_args.kwargs["service_actor_id"] == "other"


def test_unsorted_page_is_rejected_before_any_schedule_execution() -> None:
    poller, repo, service, principal, value = fixture()
    repo.list_due.return_value = (value.model_copy(update={"id": "schedule-2"}), value)
    with pytest.raises(PersistenceError):
        asyncio.run(poller.poll(principal))
    service.tick.assert_not_awaited()
