from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from enterprise_platform.application.scheduling import IntervalSchedule, plan_tick

START = datetime(2026, 9, 10, tzinfo=UTC)


def schedule(**changes: object) -> IntervalSchedule:
    return IntervalSchedule.model_validate(
        {
            "id": "schedule-1",
            "workspace_id": "workspace-1",
            "revision": 1,
            "binding_id": "binding-1",
            "binding_revision": 2,
            "device_id": "001",
            "scenario": "alert",
            "service_actor_id": "service-1",
            "anchor_at": START,
            "next_due_at": START,
            "interval_seconds": 60,
            "window_seconds": 300,
            "grace_seconds": 10,
            "missed_policy": "skip",
            "enabled": True,
            **changes,
        }
    )


def test_early_and_paused_ticks_do_not_advance_cursor() -> None:
    for value, now in [(schedule(), START - timedelta(seconds=1)), (schedule(enabled=False), START)]:
        decision = plan_tick(value, now)
        assert decision.occurrence is None
        assert decision.next_due_at == START
        assert decision.discarded_ticks == 0


def test_due_tick_freezes_binding_and_half_open_measurement_window() -> None:
    decision = plan_tick(schedule(), START)
    occurrence = decision.occurrence
    assert occurrence is not None
    assert occurrence.schedule.device_id == "001"
    assert occurrence.schedule.binding_revision == 2
    assert occurrence.window_start == START - timedelta(seconds=300)
    assert occurrence.window_end == START
    assert decision.next_due_at == START + timedelta(seconds=60)
    assert decision.discarded_ticks == 0


@pytest.mark.parametrize("delay,expected", [(10, True), (11, False), (59, False), (60, True), (70, True), (71, False)])
def test_skip_policy_uses_latest_tick_with_inclusive_grace(delay: int, expected: bool) -> None:
    decision = plan_tick(schedule(), START + timedelta(seconds=delay))
    assert (decision.occurrence is not None) is expected
    assert decision.next_due_at == START + timedelta(seconds=(delay // 60 + 1) * 60)
    assert decision.discarded_ticks == delay // 60 + (not expected)


def test_coalesce_emits_one_latest_window_after_long_outage_not_a_backlog() -> None:
    now = START + timedelta(days=1000, seconds=59)
    decision = plan_tick(schedule(missed_policy="coalesce"), now)
    assert decision.occurrence is not None
    assert decision.occurrence.window_end == now - timedelta(seconds=59)
    assert decision.discarded_ticks == 1000 * 24 * 60
    assert decision.next_due_at > now


def test_same_occurrence_has_stable_identity_across_poll_times_and_timezone_offsets() -> None:
    first = plan_tick(schedule(), START).occurrence
    second = plan_tick(schedule(), (START + timedelta(seconds=5)).astimezone(timezone(timedelta(hours=8)))).occurrence
    assert first is not None and second is not None
    assert first.id == second.id
    assert first.window_start == second.window_start
    other = plan_tick(schedule(workspace_id="workspace-2"), START).occurrence
    changed = plan_tick(schedule(revision=2), START).occurrence
    assert other is not None and changed is not None
    assert len({first.id, other.id, changed.id}) == 3


def test_advancing_the_cursor_prevents_reemitting_the_same_tick() -> None:
    original = schedule()
    first = plan_tick(original, START)
    advanced = schedule(next_due_at=first.next_due_at)
    assert plan_tick(advanced, START).occurrence is None


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_seconds": 0},
        {"interval_seconds": True},
        {"window_seconds": 0},
        {"grace_seconds": 60},
        {"grace_seconds": -1},
        {"revision": 0},
        {"anchor_at": START.replace(tzinfo=None)},
        {"next_due_at": START + timedelta(seconds=1)},
        {"next_due_at": START - timedelta(seconds=60)},
        {"missed_policy": "replay_all"},
        {"scenario": "other"},
        {"binding_revision": 0},
    ],
)
def test_invalid_or_unaligned_schedules_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schedule(**changes)


def test_naive_clock_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_tick(schedule(), START.replace(tzinfo=None))


def test_model_copy_cannot_bypass_schedule_validation_at_planning_boundary() -> None:
    with pytest.raises(ValidationError):
        plan_tick(schedule().model_copy(update={"interval_seconds": 0}), START)


def test_schedule_offsets_normalize_without_changing_elapsed_interval() -> None:
    shifted = START.astimezone(timezone(timedelta(hours=8)))
    value = schedule(anchor_at=shifted, next_due_at=shifted, scenario="quality")
    assert value.anchor_at.tzinfo is UTC
    assert value.next_due_at.tzinfo is UTC
    decision = plan_tick(value, START)
    assert decision.occurrence is not None
    assert decision.occurrence.schedule.scenario == "quality"
    assert decision.next_due_at == START + timedelta(seconds=60)


def test_skipped_cursor_is_not_recounted_at_the_same_clock_time() -> None:
    now = START + timedelta(seconds=11)
    first = plan_tick(schedule(), now)
    assert first.discarded_ticks == 1
    second = plan_tick(schedule(next_due_at=first.next_due_at), now)
    assert second.discarded_ticks == 0
    assert second.occurrence is None


@pytest.mark.parametrize(
    "anchor,now",
    [
        (datetime.min.replace(tzinfo=UTC), datetime.min.replace(tzinfo=UTC)),
        (START, datetime.max.replace(tzinfo=UTC)),
    ],
)
def test_unrepresentable_windows_fail_without_advancing_a_cursor(anchor: datetime, now: datetime) -> None:
    value = schedule(anchor_at=anchor, next_due_at=anchor, missed_policy="coalesce")
    with pytest.raises(ValueError, match="datetime range"):
        plan_tick(value, now)
    assert value.next_due_at == anchor
