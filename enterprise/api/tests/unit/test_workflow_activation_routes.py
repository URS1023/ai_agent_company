from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from test_workflow_activation_contracts import create as activation
from test_workflow_enrollment_service import PRINCIPAL

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.http.app import create_app

START = "/enterprise/api/v1/workflow-enrollments/" + activation().enrollment.id + "/activation"
ITEM = "/enterprise/api/v1/workflow-activations/" + activation().id
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
        create_app(MagicMock(), identity, allowed_origins=("https://console.test",), workflow_activation=service)
    )


def injected():
    service = AsyncMock()
    for method in (service.start, service.get, service.revoke):
        method.return_value = activation()
    return service


def test_start_get_and_revoke_have_private_public_projection():
    service = injected()
    http = client(service)
    response = http.post(START, json={"specification_revision": "spec-1"}, headers=HEADERS)
    assert response.status_code == 201
    service.start.assert_awaited_once_with(PRINCIPAL, activation().enrollment.id, specification_revision="spec-1")
    assert http.get(ITEM, headers=HEADERS).json() == activation().model_dump(mode="json")
    response = http.post(ITEM + "/revoke", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 200
    service.revoke.assert_awaited_once_with(PRINCIPAL, activation().id, expected_revision=1)
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
def test_invalid_revoke_is_sanitized(body):
    service = injected()
    response = client(service).post(ITEM + "/revoke", json=body, headers=HEADERS)
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    service.revoke.assert_not_called()


def test_start_rejects_injected_secret():
    service = injected()
    response = client(service).post(
        START, json={"specification_revision": "spec-1", "secret_ref": "private"}, headers=HEADERS
    )
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    service.start.assert_not_called()


def test_lookup_is_nullable_and_does_not_create():
    service = injected()
    service.find.return_value = None
    http = client(service)
    response = http.get(START, headers=HEADERS)
    assert response.status_code == 200 and response.json() is None
    service.find.return_value = activation()
    assert http.get(START, headers=HEADERS).json() == activation().model_dump(mode="json")
    service.start.assert_not_called()
    service.revoke.assert_not_called()


def test_disabled_routes_preserve_health():
    http = client(None)
    assert http.post(START, json={"specification_revision": "spec-1"}, headers=HEADERS).status_code == 503
    assert http.get(ITEM, headers=HEADERS).status_code == 503
    assert http.post(ITEM + "/revoke", json={"expected_revision": 1}, headers=HEADERS).status_code == 503
    assert http.get("/enterprise/api/health").json() == {"status": "alive"}


def test_identity_failure_has_no_service_call():
    identity, service = AsyncMock(), injected()
    identity.resolve.side_effect = AccessDenied("private")
    response = client(service, identity).post(ITEM + "/revoke", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 403 and "private" not in response.text
    service.revoke.assert_not_called()


def test_conflict_is_code_only():
    service = injected()
    service.revoke.side_effect = Conflict("private")
    response = client(service).post(ITEM + "/revoke", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 409 and "private" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_missing_origin_prevents_mutation():
    service = injected()
    response = client(service).post(
        START, json={"specification_revision": "spec-1"}, headers={"Authorization": "Bearer private"}
    )
    assert response.status_code == 403
    service.start.assert_not_called()


def test_malformed_identity_is_sanitized():
    service = injected()
    response = client(service).get("/enterprise/api/v1/workflow-activations/not-a-uuid", headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    service.get.assert_not_called()


def test_specification_choices_use_owned_enrollment_and_private_response():
    service = injected()
    service.specifications.return_value = ("spec-1", "spec-2")
    response = client(service).get(START + "-specifications", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"items": ["spec-1", "spec-2"]}
    assert response.headers["cache-control"] == "private, no-store"
    service.specifications.assert_awaited_once_with(PRINCIPAL, activation().enrollment.id)
