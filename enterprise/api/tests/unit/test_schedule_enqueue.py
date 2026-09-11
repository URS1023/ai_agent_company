import json
from datetime import timedelta

import pytest
from test_run_enqueue_transaction import fixture as run_fixture
from test_scheduling import START, schedule

from enterprise_platform.application.errors import Conflict, InvalidInput
from enterprise_platform.application.scheduling import plan_tick
from enterprise_platform.persistence.schedule_mapping import schedule_row
from enterprise_platform.persistence.schedules import SqlAlchemyScheduleRepository


def fixture(delay: int = 0):
    _, sessions, session, binding, spec, _ = run_fixture()
    manifest = {"input_keys": ["window_start", "window_end"]}
    binding.binding_json = json.dumps(json.loads(binding.binding_json) | {"manifest": manifest})
    spec = spec.model_copy(update={"manifest": manifest})
    value = schedule(workspace_id="w", binding_id="b", device_id="d", service_actor_id="service")
    row = schedule_row(value, START, START)
    device = list(session.scalar.side_effect)[2]
    session.scalar.side_effect = [row, None, binding, device]
    sessions.begin.return_value.__enter__.return_value = session
    session.connection.return_value.dialect.name = "postgresql"
    repo = SqlAlchemyScheduleRepository(sessions, clock=lambda: START + timedelta(seconds=delay))
    occurrence = plan_tick(value, START).occurrence
    assert occurrence is not None
    spec = spec.model_copy(
        update={
            "parameters": {
                "window_start": occurrence.window_start.isoformat(),
                "window_end": occurrence.window_end.isoformat(),
            }
        }
    )
    return repo, sessions, session, row, value, spec


def test_tick_cursor_and_existing_run_ledger_share_one_transaction() -> None:
    repo, sessions, session, _, value, spec = fixture()
    result = repo.commit_tick(value, spec)
    assert result.schedule.next_due_at == START + timedelta(seconds=60)
    assert result.schedule.revision == value.revision
    assert result.run is not None and result.run.actor_id == "service"
    assert result.run.spec == spec
    assert result.run.request_key == plan_tick(value, START).occurrence.id
    assert result.run.input_snapshot is None
    sessions.begin.assert_called_once()
    assert session.add.call_count == 3
    session.commit.assert_not_called()


def test_skip_advances_cursor_without_creating_a_run() -> None:
    repo, sessions, session, _, value, _ = fixture(delay=11)
    result = repo.commit_tick(value, None)
    assert result.run is None and result.discarded_ticks == 1
    assert result.schedule.next_due_at == START + timedelta(seconds=60)
    assert session.add.call_count == 1
    sessions.begin.assert_called_once()


def test_stale_schedule_cannot_enqueue_or_advance_cursor() -> None:
    repo, _, session, _, value, spec = fixture()
    with pytest.raises(Conflict):
        repo.commit_tick(value.model_copy(update={"revision": 2}), spec)
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"parameters": {}},
        {"binding_revision": 9},
        {"device_id": "other"},
        {"recompute_of": "old-run"},
    ],
)
def test_unmatched_prepared_run_does_not_advance_cursor(patch: dict[str, object]) -> None:
    repo, _, session, _, value, spec = fixture()
    with pytest.raises(InvalidInput):
        repo.commit_tick(value, spec.model_copy(update=patch))
    assert session.execute.call_count == 1  # device lock only, no cursor CAS
    session.add.assert_not_called()


def test_missing_spec_for_due_tick_is_not_silently_skipped() -> None:
    repo, _, session, _, value, _ = fixture()
    with pytest.raises(InvalidInput):
        repo.commit_tick(value, None)
    session.add.assert_not_called()


def test_atomic_entry_rejects_undeclared_inputs_even_when_preparation_is_bypassed() -> None:
    repo, _, session, _, value, spec = fixture()
    spec = spec.model_copy(update={"parameters": spec.parameters | {"unregistered": "value"}})
    with pytest.raises(InvalidInput):
        repo.commit_tick(value, spec)
    session.add.assert_not_called()
