import asyncio

import pytest
from test_dashboard_refresh_service import principal
from test_sql_trial_history import fixture

from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialSummary
from enterprise_platform.application.errors import AccessDenied, InvalidInput, PersistenceError


def summary(evidence, trial_id):
    return SqlTrialSummary(
        workspace_id=evidence.result.workspace_id,
        dashboard_id=evidence.result.dashboard_id,
        draft_id=evidence.result.draft_id,
        trial_id=trial_id,
        actor_id=evidence.actor_id,
        recorded_at=evidence.recorded_at,
    )


def test_list_returns_metadata_only_and_lookahead_cursor():
    service, parts, store, evidence = fixture()
    store.list.return_value = tuple(summary(evidence, name) for name in ("trial-3", "trial-2", "trial-1"))
    result = asyncio.run(service.list_sql_trials(principal(), "dashboard-1", "draft-1", limit=2))
    assert [item.trial_id for item in result.items] == ["trial-3", "trial-2"]
    assert result.next_cursor == "trial-2"
    assert "rows" not in result.model_dump_json()
    store.list.assert_called_once_with("workspace-1", "dashboard-1", "draft-1", after=None, limit=3)
    parts[5].execute.assert_not_awaited()


def test_empty_page_and_end_cursor_are_explicit():
    service, parts, store, evidence = fixture()
    store.list.return_value = ()
    result = asyncio.run(service.list_sql_trials(principal(), "dashboard-1", "draft-1", after="trial-1"))
    assert result.items == () and result.next_cursor is None
    store.list.assert_called_once_with("workspace-1", "dashboard-1", "draft-1", after="trial-1", limit=21)


def test_readonly_membership_denies_before_list_query():
    service, parts, store, evidence = fixture()
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.list_sql_trials(
                principal().model_copy(update={"workspace_role": "normal"}), "dashboard-1", "draft-1"
            )
        )
    store.list.assert_not_called()


@pytest.mark.parametrize("limit", [True, 0, 101])
def test_invalid_public_limits_are_rejected(limit):
    service, parts, store, evidence = fixture()
    with pytest.raises(InvalidInput):
        asyncio.run(service.list_sql_trials(principal(), "dashboard-1", "draft-1", limit=limit))
    store.list.assert_not_called()


def test_foreign_summary_is_not_returned():
    service, parts, store, evidence = fixture()
    store.list.return_value = (summary(evidence, "trial-1").model_copy(update={"draft_id": "other"}),)
    with pytest.raises(AccessDenied):
        asyncio.run(service.list_sql_trials(principal(), "dashboard-1", "draft-1"))


def test_duplicate_identifiers_are_not_returned_as_a_valid_page():
    service, parts, store, evidence = fixture()
    store.list.return_value = (summary(evidence, "trial-1"),) * 2
    with pytest.raises(PersistenceError):
        asyncio.run(service.list_sql_trials(principal(), "dashboard-1", "draft-1"))
