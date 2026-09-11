import asyncio
from unittest.mock import create_autospec

import pytest
from test_dashboard_refresh_service import principal
from test_dashboard_sql_trial import setup
from test_dashboard_sql_trial_result import capture

from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.dashboard_sql_trial import DashboardSqlTrial
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidenceRepository
from enterprise_platform.application.errors import AccessDenied, PersistenceError


def fixture():
    parts = setup()
    runner = create_autospec(DashboardSqlTrial, instance=True)
    runner.run.return_value = capture()
    store = create_autospec(SqlTrialEvidenceRepository, instance=True)
    store.create.side_effect = lambda evidence: evidence
    service = DashboardService(
        parts[1], create_autospec(DashboardRefreshService, instance=True), sql_trial=runner, sql_trial_evidence=store
    )
    return service, runner, store, parts[-1]


def test_success_returns_only_after_storing_server_owned_evidence():
    service, runner, store, command = fixture()
    result = asyncio.run(service.trial_sql(principal(), "dashboard-1", "draft-1", command))
    saved = store.create.call_args.args[0]
    assert saved.result == result
    assert saved.actor_id == principal().actor_id
    assert saved.trial_id
    assert saved.recorded_at >= result.captured_at
    runner.run.assert_awaited_once()
    store.create.assert_called_once()


def test_storage_failure_is_not_reported_as_trial_success_or_retried():
    service, runner, store, command = fixture()
    store.create.side_effect = PersistenceError()
    with pytest.raises(PersistenceError):
        asyncio.run(service.trial_sql(principal(), "dashboard-1", "draft-1", command))
    runner.run.assert_awaited_once()
    store.create.assert_called_once()


def test_mismatched_storage_receipt_is_not_returned():
    service, runner, store, command = fixture()
    store.create.side_effect = lambda evidence: evidence.model_copy(update={"actor_id": "other"})
    with pytest.raises(PersistenceError):
        asyncio.run(service.trial_sql(principal(), "dashboard-1", "draft-1", command))


def test_membership_failure_never_executes_or_stores():
    service, runner, store, command = fixture()
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.trial_sql(
                principal().model_copy(update={"workspace_role": "normal"}), "dashboard-1", "draft-1", command
            )
        )
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


def test_execution_failure_never_stores_evidence():
    service, runner, store, command = fixture()
    runner.run.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        asyncio.run(service.trial_sql(principal(), "dashboard-1", "draft-1", command))
    store.create.assert_not_called()
