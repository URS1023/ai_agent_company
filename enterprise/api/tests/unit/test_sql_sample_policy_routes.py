import pytest
from test_dashboard_routes import HEADERS
from test_sql_sample_policy import setup as policy_setup
from test_sql_trial_history_routes import HISTORY, setup

from enterprise_platform.application.dashboard_sql_sample_policy import apply_sample_policy
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable, Unauthenticated

URL = HISTORY + "/sample-check"
QUERY = {"policy_id": "device-count", "policy_revision": "v1"}


def test_automatic_check_requires_no_policy_selection_and_returns_provenance():
    client, runner, store, evidence, identity = setup()
    result = apply_sample_policy(*policy_setup())
    runner.auto_check_sample_policy.return_value = result
    response = client.get(URL + "/automatic", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    runner.auto_check_sample_policy.assert_awaited_once_with(identity.resolve.return_value, evidence)
    runner.check_sample_policy.assert_not_awaited()
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


def test_sample_check_is_private_and_returns_policy_provenance_without_execution():
    client, runner, store, evidence, identity = setup()
    result = apply_sample_policy(*policy_setup())
    runner.check_sample_policy.return_value = result
    response = client.get(URL, params=QUERY, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    runner.check_sample_policy.assert_awaited_once_with(identity.resolve.return_value, evidence, "device-count", "v1")
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"policy_id": "x"},
        {"policy_revision": "v1"},
        dict(QUERY, policy_id=""),
        dict(QUERY, policy_revision="x" * 129),
    ],
)
def test_invalid_policy_selection_never_loads_evidence(query):
    client, runner, store, evidence, identity = setup()
    assert client.get(URL, params=query, headers=HEADERS).status_code == 422
    store.get.assert_not_called()
    runner.check_sample_policy.assert_not_awaited()


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("error,status", [(AccessDenied, 403), (Conflict, 409), (DependencyUnavailable, 503)])
def test_policy_failure_is_private_and_opaque(error, status, automatic):
    client, runner, store, evidence, identity = setup()
    operation = runner.auto_check_sample_policy if automatic else runner.check_sample_policy
    operation.side_effect = error("private configuration details")
    response = client.get(URL + ("/automatic" if automatic else ""), params={} if automatic else QUERY, headers=HEADERS)
    assert response.status_code == status
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("suffix", ["", "/automatic"])
def test_unauthenticated_request_never_loads_evidence(suffix):
    client, runner, store, evidence, identity = setup()
    identity.resolve.side_effect = Unauthenticated()
    assert client.get(URL + suffix, params=QUERY, headers=HEADERS).status_code == 401
    store.get.assert_not_called()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("suffix", ["", "/automatic"])
def test_mutation_methods_do_not_invoke_service(method, suffix):
    client, runner, store, evidence, identity = setup()
    assert getattr(client, method)(URL + suffix, params=QUERY, headers=HEADERS).status_code == 405
    store.get.assert_not_called()
    runner.check_sample_policy.assert_not_awaited()
    runner.auto_check_sample_policy.assert_not_awaited()
    runner.run.assert_not_awaited()
