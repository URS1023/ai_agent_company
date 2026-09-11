from decimal import Decimal
from unittest.mock import create_autospec

import pytest
from test_dashboard_routes import HEADERS, URL, fixture
from test_dashboard_sql_trial import setup
from test_dashboard_sql_trial_result import capture

from enterprise_platform.application.dashboard_sql_trial import DashboardSqlTrial
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable, Unauthenticated

TRIAL_URL = URL + "/sql-proposals/draft-1/trial"


def trial_fixture():
    runner = create_autospec(DashboardSqlTrial, instance=True)
    runner.run.return_value = capture((2**64, Decimal("0.0000000000000000001"), None, 0))
    client, repository, refresh, identity = fixture(sql_trial=runner)
    command = setup()[-1]
    return client, runner, command, identity, refresh


def test_trial_post_returns_private_lossless_result_not_published_data():
    client, runner, command, identity, refresh = trial_fixture()
    response = client.post(TRIAL_URL, json=command.model_dump(mode="json"), headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    value = response.json()
    assert value["status"] == "trial_only"
    assert value["semantic_status"] == "not_reviewed"
    assert value["rows"][0][0] == {"kind": "integer", "value": str(2**64)}
    assert value["rows"][0][1] == {"kind": "decimal", "value": "1E-19"}
    runner.run.assert_awaited_once_with(identity.resolve.return_value, "dashboard-1", "draft-1", command)
    refresh.refresh.assert_not_awaited()


@pytest.mark.parametrize("field", ["sql", "source_id", "limits", "plan_budget", "workspace_id"])
def test_trial_rejects_browser_execution_context(field):
    client, runner, command, _, _ = trial_fixture()
    response = client.post(TRIAL_URL, json={**command.model_dump(), field: "forged"}, headers=HEADERS)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
    runner.run.assert_not_awaited()


@pytest.mark.parametrize("case,status", [("readonly", 403), ("origin", 403), ("login", 401)])
def test_trial_requires_native_login_membership_and_origin(case, status):
    client, runner, command, identity, _ = trial_fixture()
    headers = dict(HEADERS)
    if case == "readonly":
        identity.resolve.return_value = identity.resolve.return_value.model_copy(update={"workspace_role": "normal"})
    elif case == "origin":
        headers["Origin"] = "https://other.test"
    else:
        identity.resolve.side_effect = Unauthenticated()
    response = client.post(TRIAL_URL, json=command.model_dump(), headers=headers)
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    runner.run.assert_not_awaited()


@pytest.mark.parametrize("error,status", [(AccessDenied, 403), (Conflict, 409), (DependencyUnavailable, 503)])
def test_trial_errors_hide_sql_and_connection_details(error, status):
    client, runner, command, _, _ = trial_fixture()
    runner.run.side_effect = error("private SQL and connection")
    response = client.post(TRIAL_URL, json=command.model_dump(), headers=HEADERS)
    assert response.status_code == status
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"


def test_unconfigured_trial_reports_unavailable():
    client, _, _, _ = fixture()
    response = client.post(TRIAL_URL, json=setup()[-1].model_dump(), headers=HEADERS)
    assert response.status_code == 503


def test_get_does_not_trigger_a_trial():
    client, runner, _, _, _ = trial_fixture()
    assert client.get(TRIAL_URL, headers=HEADERS).status_code == 405
    runner.run.assert_not_awaited()
