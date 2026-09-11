from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_workflow_activation_contracts import create

from enterprise_platform.application.native_registration import NativeRegistrationService
from enterprise_platform.http.native_registration import create_registration_router

TOKEN = "registration-fixture-" + "a" * 40
PATH = "/enterprise/internal/v1/workflow-registration"


def harness():
    value = create().enrollment.publication
    lookup = MagicMock()
    lookup.find_registration.return_value = value
    service = NativeRegistrationService(lookup, SecretStr(TOKEN))
    app = FastAPI()
    app.include_router(create_registration_router(service))
    params = dict(workspace_id=value.workspace_id, app_id=value.app_id, workflow_id=value.workflow_id)
    return TestClient(app), lookup, value, params


def test_authenticated_exact_registration_is_public_and_not_cached():
    http, lookup, value, params = harness()
    response = http.get(PATH, params=params, headers={"X-Enterprise-Registration-Token": TOKEN})
    assert response.status_code == 200
    assert response.json() == value.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    assert TOKEN not in response.text
    lookup.find_registration.assert_called_once()
    assert PATH not in http.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer " + TOKEN},
        {"X-Enterprise-Registration-Token": "wrong"},
        {"X-Enterprise-Registration-Token": TOKEN, "Origin": "https://console.test"},
    ],
)
def test_browser_or_invalid_credentials_never_reach_lookup(headers):
    http, lookup, _, params = harness()
    response = http.get(PATH, params=params, headers=headers)
    assert response.status_code == 401 and response.json() == {"code": "unauthenticated"}
    lookup.find_registration.assert_not_called()


def test_unregistered_workflow_is_nullable():
    http, lookup, _, params = harness()
    lookup.find_registration.return_value = None
    response = http.get(PATH, params=params, headers={"X-Enterprise-Registration-Token": TOKEN})
    assert response.status_code == 200 and response.json() is None


def test_malformed_identity_is_sanitized_without_storage_access():
    http, lookup, _, params = harness()
    response = http.get(
        PATH, params=params | {"app_id": "private-invalid"}, headers={"X-Enterprise-Registration-Token": TOKEN}
    )
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    lookup.find_registration.assert_not_called()


def test_storage_scope_mismatch_is_not_returned():
    http, _, _, params = harness()
    response = http.get(
        PATH,
        params=params | {"app_id": "00000000-0000-0000-0000-000000000099"},
        headers={"X-Enterprise-Registration-Token": TOKEN},
    )
    assert response.status_code == 503 and response.json() == {"code": "persistence_error"}


@pytest.mark.parametrize("token", ["short", "x" * 129, "a" * 32 + "\n"])
def test_invalid_service_secret_is_rejected(token):
    with pytest.raises(ValueError):
        NativeRegistrationService(MagicMock(), SecretStr(token))


@pytest.mark.parametrize("change", ["duplicate", "extra", "missing"])
def test_query_shape_is_exact_before_storage(change):
    http, lookup, _, params = harness()
    query = list(params.items())
    if change == "duplicate":
        query.append(("app_id", params["app_id"]))
    elif change == "extra":
        query.append(("token", "private"))
    else:
        query.pop()
    response = http.get(PATH, params=query, headers={"X-Enterprise-Registration-Token": TOKEN})
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    lookup.find_registration.assert_not_called()


def test_storage_exception_is_sanitized_and_not_retried():
    http, lookup, _, params = harness()
    lookup.find_registration.side_effect = RuntimeError("private database details")
    response = http.get(PATH, params=params, headers={"X-Enterprise-Registration-Token": TOKEN})
    assert response.status_code == 503 and response.json() == {"code": "registration_unavailable"}
    assert response.headers["cache-control"] == "private, no-store"
    lookup.find_registration.assert_called_once()
