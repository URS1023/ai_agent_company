from unittest.mock import create_autospec

import pytest
from test_dashboard_routes import HEADERS, URL, fixture
from test_sql_trial_history import fixture as history_fixture
from test_sql_trial_list_service import summary

from enterprise_platform.application.dashboard_sql_trial import DashboardSqlTrial
from enterprise_platform.application.dashboard_sql_trial_assessment import SqlTrialAssessment
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable, Unauthenticated

HISTORY = URL + "/sql-proposals/draft-1/trials/trial-1"
LIST = URL + "/sql-proposals/draft-1/trials"


def setup():
    _, _, store, evidence = history_fixture()
    runner = create_autospec(DashboardSqlTrial, instance=True)
    client, _, refresh, identity = fixture(sql_trial=runner, sql_trial_evidence=store)
    return client, runner, store, evidence, identity


def test_assessment_get_returns_unreviewed_types_not_a_publication_receipt():
    client, runner, store, evidence, identity = setup()
    result = SqlTrialAssessment(trial_id="trial-1", slot_id="a", status="sample_compatible", issues=())
    runner.assess_evidence.return_value = result
    response = client.get(HISTORY + "/assessment", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    runner.assess_evidence.assert_awaited_once_with(identity.resolve.return_value, evidence)
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


def test_assessment_get_hides_revoked_evidence():
    client, runner, store, evidence, identity = setup()
    runner.assess_evidence.side_effect = AccessDenied("private details")
    response = client.get(HISTORY + "/assessment", headers=HEADERS)
    assert response.status_code == 403
    assert response.json() == {"code": AccessDenied.code}


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_assessment_mutation_methods_do_not_execute(method):
    client, runner, store, evidence, identity = setup()
    assert getattr(client, method)(HISTORY + "/assessment", headers=HEADERS).status_code == 405
    runner.run.assert_not_awaited()


def test_list_returns_private_metadata_without_authorizing_or_running_sql():
    client, runner, store, evidence, identity = setup()
    store.list.return_value = (summary(evidence, "trial-2"), summary(evidence, "trial-1"))
    response = client.get(LIST + "?limit=1", headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["next_cursor"] == "trial-2"
    assert set(response.json()["items"][0]) == {
        "workspace_id",
        "dashboard_id",
        "draft_id",
        "trial_id",
        "actor_id",
        "recorded_at",
    }
    runner.run.assert_not_awaited()
    runner.authorize_result.assert_not_awaited()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "after=", "after=" + "a" * 129])
def test_invalid_list_query_does_not_read_storage(query):
    client, runner, store, evidence, identity = setup()
    response = client.get(LIST + "?" + query, headers=HEADERS)
    assert response.status_code == 422
    store.list.assert_not_called()


def test_get_returns_saved_private_evidence_without_execution():
    client, runner, store, evidence, identity = setup()
    response = client.get(HISTORY, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == evidence.model_dump(mode="json")
    runner.authorize_result.assert_awaited_once_with(identity.resolve.return_value, evidence.result)
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


@pytest.mark.parametrize("error,status", [(AccessDenied, 403), (Conflict, 409), (DependencyUnavailable, 503)])
def test_history_errors_are_private_and_opaque(error, status):
    client, runner, store, evidence, identity = setup()
    runner.authorize_result.side_effect = error("private details")
    response = client.get(HISTORY, headers=HEADERS)
    assert response.status_code == status
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"


def test_unauthenticated_history_never_reads_storage():
    client, runner, store, evidence, identity = setup()
    identity.resolve.side_effect = Unauthenticated()
    assert client.get(HISTORY, headers=HEADERS).status_code == 401
    store.get.assert_not_called()


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_history_mutation_methods_never_execute(method):
    client, runner, store, evidence, identity = setup()
    assert getattr(client, method)(HISTORY, headers=HEADERS).status_code == 405
    runner.run.assert_not_awaited()
    store.get.assert_not_called()
