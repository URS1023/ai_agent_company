import pytest
from test_dashboard_routes import HEADERS
from test_sql_trial_assessment import fixture
from test_sql_trial_history_routes import HISTORY, setup

from enterprise_platform.application.dashboard_sql_trial_preview import preview_sql_trial
from enterprise_platform.application.errors import (
    AccessDenied,
    Conflict,
    DependencyUnavailable,
    InvalidInput,
    Unauthenticated,
)

URL = HISTORY + "/preview"


def test_get_returns_private_data_only_preview_without_executing_sql():
    client, runner, store, evidence, identity = setup()
    result = preview_sql_trial(*fixture([{"kind": "integer", "value": "18446744073709551616"}]))
    runner.preview_evidence.return_value = result
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    runner.preview_evidence.assert_awaited_once_with(identity.resolve.return_value, evidence)
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


@pytest.mark.parametrize(
    "error,status", [(AccessDenied, 403), (Conflict, 409), (InvalidInput, 422), (DependencyUnavailable, 503)]
)
def test_preview_errors_are_private_and_opaque(error, status):
    client, runner, store, evidence, identity = setup()
    runner.preview_evidence.side_effect = error("private source details")
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == status
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"


def test_unauthenticated_preview_never_reads_evidence():
    client, runner, store, evidence, identity = setup()
    identity.resolve.side_effect = Unauthenticated()
    assert client.get(URL, headers=HEADERS).status_code == 401
    store.get.assert_not_called()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_preview_mutations_never_reach_service(method):
    client, runner, store, evidence, identity = setup()
    assert getattr(client, method)(URL, headers=HEADERS).status_code == 405
    runner.preview_evidence.assert_not_awaited()
    runner.run.assert_not_awaited()
    store.get.assert_not_called()
