from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from test_workflow_enrollment_contracts import enrollment
from test_workflow_enrollment_service import PRINCIPAL

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.http.app import create_app

START = "/enterprise/api/v1/workflow-provisioning/" + enrollment().view.provisioning.id + "/enrollment"
ITEM = "/enterprise/api/v1/workflow-enrollments/" + enrollment().view.id
HEADERS = {
    "Origin": "https://console.test",
    "Authorization": "Bearer private",
    "Cookie": "session=private",
    "X-CSRF-Token": "private",
}


def client(service, identity=None):
    identity = identity or AsyncMock()
    identity.resolve.return_value = PRINCIPAL
    return TestClient(
        create_app(MagicMock(), identity, allowed_origins=("https://console.test",), workflow_enrollment=service)
    )


def injected():
    service = AsyncMock()
    for method in (service.start, service.get, service.advance):
        method.return_value = enrollment().view
    return service


def test_start_get_and_advance_have_private_public_projection():
    service = injected()
    http = client(service)
    response = http.post(START, json={}, headers=HEADERS)
    assert response.status_code == 201
    service.start.assert_awaited_once_with(PRINCIPAL, enrollment().view.provisioning.id)
    assert http.get(ITEM, headers=HEADERS).json() == enrollment().view.model_dump(mode="json")
    response = http.post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 200
    session = service.advance.call_args.args[1]
    assert session.authorization == "Bearer private" and session.csrf_token == "private"
    assert response.headers["cache-control"] == "private, no-store"
    assert "claim_nonce" not in response.text and "private" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": True},
        {"expected_revision": 0},
        {"expected_revision": "1"},
        {"expected_revision": 1, "token": "private"},
    ],
)
def test_invalid_advance_is_sanitized(body):
    service = injected()
    response = client(service).post(ITEM + "/advance", json=body, headers=HEADERS)
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    service.advance.assert_not_called()


def test_start_rejects_injected_secret():
    service = injected()
    assert client(service).post(START, json={"secret_ref": "private"}, headers=HEADERS).status_code == 422
    service.start.assert_not_called()


def test_lookup_is_nullable_and_does_not_create():
    service = injected()
    service.find.return_value = None
    http = client(service)
    response = http.get(START, headers=HEADERS)
    assert response.status_code == 200 and response.json() is None
    service.find.return_value = enrollment().view
    assert http.get(START, headers=HEADERS).json() == enrollment().view.model_dump(mode="json")
    service.start.assert_not_called()
    service.advance.assert_not_called()


def test_disabled_routes_preserve_health():
    http = client(None)
    assert http.post(START, json={}, headers=HEADERS).status_code == 503
    assert http.get(ITEM, headers=HEADERS).status_code == 503
    assert http.post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS).status_code == 503
    assert http.get("/enterprise/api/health").json() == {"status": "alive"}


def test_identity_failure_has_no_service_call():
    identity, service = AsyncMock(), injected()
    identity.resolve.side_effect = AccessDenied("private")
    response = client(service, identity).post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 403 and "private" not in response.text
    service.advance.assert_not_called()


def test_conflict_is_code_only():
    service = injected()
    service.advance.side_effect = Conflict("private")
    response = client(service).post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 409 and "private" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_missing_origin_prevents_mutation():
    service = injected()
    response = client(service).post(START, json={}, headers={"Authorization": "Bearer private"})
    assert response.status_code == 403
    service.start.assert_not_called()


def test_malformed_identity_is_sanitized():
    service = injected()
    response = client(service).get("/enterprise/api/v1/workflow-enrollments/not-a-uuid", headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    service.get.assert_not_called()
