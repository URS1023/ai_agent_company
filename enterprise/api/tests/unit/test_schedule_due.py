from datetime import timedelta

import pytest
from test_schedule_persistence import fixture
from test_scheduling import START, schedule

from enterprise_platform.application.errors import InvalidInput, PersistenceError
from enterprise_platform.application.scheduling import ScheduleScanCursor
from enterprise_platform.persistence.schedule_mapping import schedule_row


def test_due_scan_is_scoped_bounded_and_stably_ordered() -> None:
    repo, session, row = fixture()
    session.scalars.return_value.all.return_value = [row]
    assert repo.list_due("workspace-1", now=START, limit=20) == (schedule(),)
    statement = session.scalars.call_args.args[0]
    compiled = statement.compile()
    assert compiled.params == {"workspace_id_1": "workspace-1", "next_due_at_1": START, "param_1": 20}
    sql = str(compiled)
    assert "enabled IS true" in sql
    assert "ORDER BY enterprise_schedules.next_due_at, enterprise_schedules.schedule_id" in sql
    session.add.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_due_scan_rejects_invalid_bounds_before_io(limit: int) -> None:
    repo, session, _ = fixture()
    with pytest.raises(InvalidInput):
        repo.list_due("workspace-1", now=START, limit=limit)
    session.scalars.assert_not_called()


def test_due_scan_requires_an_aware_cutoff() -> None:
    repo, session, _ = fixture()
    with pytest.raises(InvalidInput):
        repo.list_due("workspace-1", now=START.replace(tzinfo=None))
    session.scalars.assert_not_called()


@pytest.mark.parametrize(
    "patch", [{"workspace_id": "other"}, {"enabled": False}, {"next_due_at": START + timedelta(seconds=60)}]
)
def test_due_scan_revalidates_returned_scope_and_eligibility(patch: dict[str, object]) -> None:
    repo, session, _ = fixture()
    row = schedule_row(schedule().model_copy(update=patch), START, START)
    session.scalars.return_value.all.return_value = [row]
    with pytest.raises(PersistenceError):
        repo.list_due("workspace-1", now=START)


def test_due_scan_empty_result_is_not_an_error() -> None:
    repo, session, _ = fixture()
    session.scalars.return_value.all.return_value = []
    assert repo.list_due("workspace-1", now=START) == ()


def test_worker_scan_filters_service_actor_in_sql_before_limit() -> None:
    repo, session, row = fixture()
    session.scalars.return_value.all.return_value = [row]
    assert repo.list_due("workspace-1", now=START, service_actor_id="service-1") == (schedule(),)
    statement = session.scalars.call_args.args[0]
    assert statement.compile().params["service_actor_id_1"] == "service-1"


def test_worker_scan_rejects_foreign_service_receipt() -> None:
    repo, session, row = fixture()
    session.scalars.return_value.all.return_value = [row]
    with pytest.raises(PersistenceError):
        repo.list_due("workspace-1", now=START, service_actor_id="other-service")


def test_keyset_scan_uses_due_time_and_id_for_stable_tie_breaking() -> None:
    repo, session, row = fixture()
    session.scalars.return_value.all.return_value = [row]
    after = ScheduleScanCursor(next_due_at=START, schedule_id="schedule-0")
    assert repo.list_due("workspace-1", now=START, after=after) == (schedule(),)
    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile())
    assert "next_due_at >" in sql and "schedule_id >" in sql and " OR " in sql
    assert "schedule-0" in statement.compile().params.values()


def test_keyset_rejects_out_of_order_receipt() -> None:
    repo, session, row = fixture()
    session.scalars.return_value.all.return_value = [row]
    with pytest.raises(PersistenceError):
        repo.list_due("workspace-1", now=START, after=ScheduleScanCursor(next_due_at=START, schedule_id="schedule-1"))
