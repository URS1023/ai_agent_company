import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest
from test_dashboard_refresh_service import principal
from test_dashboard_sql_trial import setup, trial

from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidence, SqlTrialEvidenceRepository
from enterprise_platform.application.dashboard_sql_trial_result import as_sql_trial_result
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable


def fixture():
    parts = setup()
    capture = trial(parts)
    result = as_sql_trial_result(principal(), "dashboard-1", "draft-1", parts[-1], capture)
    evidence = SqlTrialEvidence(trial_id="trial-1", actor_id="actor-1", recorded_at=datetime.now(UTC), result=result)
    store = create_autospec(SqlTrialEvidenceRepository, instance=True)
    store.get.return_value = evidence
    service = DashboardService(
        parts[1], create_autospec(DashboardRefreshService, instance=True), sql_trial=parts[0], sql_trial_evidence=store
    )
    parts[5].reset_mock()
    return service, parts, store, evidence


def read(service, actor=None):
    return asyncio.run(service.inspect_sql_trial(actor or principal(), "dashboard-1", "draft-1", "trial-1"))


def test_history_reauthorizes_without_reexecuting_or_recording():
    service, parts, store, evidence = fixture()
    assert read(service) == evidence
    store.get.assert_called_once_with("workspace-1", "trial-1")
    parts[5].execute.assert_not_awaited()
    store.create.assert_not_called()


def test_readonly_user_never_reads_stored_results():
    service, parts, store, evidence = fixture()
    with pytest.raises(AccessDenied):
        read(service, principal().model_copy(update={"workspace_role": "normal"}))
    store.get.assert_not_called()


def test_revoked_trial_permission_hides_history():
    service, parts, store, evidence = fixture()
    parts[4].authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        read(service)
    parts[5].execute.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("workspace_id", "other"), ("dashboard_id", "other"), ("draft_id", "other")])
def test_foreign_result_never_returns(field, value):
    service, parts, store, evidence = fixture()
    store.get.return_value = evidence.model_copy(update={"result": evidence.result.model_copy(update={field: value})})
    with pytest.raises(AccessDenied):
        read(service)


@pytest.mark.parametrize(
    "field,value", [("draft_hash", "f" * 64), ("read_fingerprint", "f" * 64), ("columns", ("other",))]
)
def test_mismatched_saved_capture_is_not_returned(field, value):
    service, parts, store, evidence = fixture()
    store.get.return_value = evidence.model_copy(update={"result": evidence.result.model_copy(update={field: value})})
    with pytest.raises(DependencyUnavailable):
        read(service)
    parts[5].execute.assert_not_awaited()


def test_stale_parent_does_not_present_history_as_current():
    service, parts, store, evidence = fixture()
    parts[1].get.return_value = replace(parts[1].get.return_value, revision=2)
    with pytest.raises(Conflict):
        read(service)
