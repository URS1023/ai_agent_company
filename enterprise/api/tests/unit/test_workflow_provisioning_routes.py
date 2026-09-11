from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from test_source_service import principal
from test_workflow_provisioning_contracts import initial

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.http.app import create_app

START = "/enterprise/api/v1/workflow-setups/setup/provisioning"
ITEM = "/enterprise/api/v1/workflow-provisioning/" + initial().view.id
HEADERS = {
    "Origin": "https://console.test",
    "Idempotency-Key": "request-key",
    "Cookie": "session=private",
    "Authorization": "Bearer private",
    "X-CSRF-Token": "csrf-private",
}
BODY = {"config_ref": "config", "config_revision": 1}


def client(service, identity=None):
    identity = identity or AsyncMock()
    identity.resolve.return_value = principal()
    return TestClient(
        create_app(MagicMock(), identity, allowed_origins=("https://console.test",), workflow_provisioning=service)
    )


def injected():
    service = AsyncMock()
    for method in (service.start, service.get, service.advance):
        method.return_value = initial().view
    return service


def test_start_has_no_native_session_and_advance_has_ephemeral_headers():
    service = injected()
    http = client(service)
    response = http.post(START, json=BODY, headers=HEADERS)
    assert response.status_code == 201
    service.start.assert_awaited_once_with(
        principal(), "setup", config_ref="config", config_revision=1, request_key="request-key"
    )
    response = http.post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 200
    args = service.advance.call_args.args
    assert args[0] == principal() and args[2] == initial().view.id
    assert args[1].cookie_header == "session=private"
    assert args[1].authorization == "Bearer private"
    assert args[1].csrf_token == "csrf-private"
    assert service.advance.call_args.kwargs == {"expected_revision": 1}
    assert "private" not in response.text and "claim_nonce" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_get_returns_public_journal_only():
    service = injected()
    response = client(service).get(ITEM, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == initial().view.model_dump(mode="json")
    service.get.assert_awaited_once_with(principal(), initial().view.id)


@pytest.mark.parametrize(
    "body",
    [
        BODY | {"workspace_id": "forged"},
        BODY | {"config_revision": True},
        BODY | {"config_revision": "1"},
        BODY | {"config_revision": 0},
        BODY | {"session": "private"},
    ],
)
def test_invalid_start_body_is_sanitized_without_io(body):
    service = injected()
    response = client(service).post(START, json=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    assert response.headers["cache-control"] == "private, no-store"
    service.start.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": True},
        {"expected_revision": "1"},
        {"expected_revision": 0},
        {"expected_revision": 1, "credential_id": "forged"},
    ],
)
def test_invalid_advance_body_never_advances(body):
    service = injected()
    response = client(service).post(ITEM + "/advance", json=body, headers=HEADERS)
    assert response.status_code == 422
    service.advance.assert_not_called()


def test_missing_key_and_origin_never_start():
    service = injected()
    http = client(service)
    assert http.post(START, json=BODY, headers={"Origin": HEADERS["Origin"]}).status_code == 422
    assert http.post(START, json=BODY, headers={"Idempotency-Key": "key"}).status_code == 403
    service.start.assert_not_called()


def test_identity_failure_prevents_native_advance():
    identity = AsyncMock()
    identity.resolve.side_effect = AccessDenied("private")
    service = injected()
    response = client(service, identity).post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 403 and "private" not in response.text
    service.advance.assert_not_called()


def test_optional_service_is_503_and_old_health_unchanged():
    http = client(None)
    responses = [
        http.get(ITEM, headers=HEADERS),
        http.post(START, json=BODY, headers=HEADERS),
        http.post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS),
    ]
    assert all(response.status_code == 503 for response in responses)
    assert all(response.headers["cache-control"] == "private, no-store" for response in responses)
    assert http.get("/enterprise/api/health").json() == {"status": "alive"}


def test_service_conflict_is_private_and_sanitized():
    service = injected()
    service.advance.side_effect = Conflict("private-driver-details")
    response = client(service).post(ITEM + "/advance", json={"expected_revision": 1}, headers=HEADERS)
    assert response.status_code == 409 and "private-driver-details" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "identifier", ["not-uuid", "00000000-0000-0000-0000-000000000000", "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"]
)
def test_noncanonical_operation_path_rejected_without_service(identifier):
    service = injected()
    http = client(service)
    path = "/enterprise/api/v1/workflow-provisioning/" + identifier
    assert http.get(path, headers=HEADERS).status_code == 422
    assert http.post(path + "/advance", json={"expected_revision": 1}, headers=HEADERS).status_code == 422
    service.get.assert_not_called()
    service.advance.assert_not_called()


def test_history_reads_use_exact_setup_and_bounded_page_without_session():
    from enterprise_platform.application.contracts import Page

    service = injected()
    page = Page(items=(initial().view,), total=23, offset=20, limit=3)
    service.list.return_value = page
    response = client(service).get(START + "?offset=20&limit=3", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == page.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    service.list.assert_awaited_once_with(principal(), "setup", offset=20, limit=3)


def test_history_defaults_and_profile_choices_are_public():
    from enterprise_platform.application.contracts import Page
    from enterprise_platform.application.workflow_plugin_profiles import PluginProfileChoice

    service = injected()
    service.list.return_value = Page(items=(), total=0, offset=0, limit=20)
    choice = PluginProfileChoice(config_ref="profile", config_revision=2, display_name="Production")
    service.list_profiles.return_value = (choice,)
    http = client(service)
    assert http.get(START, headers=HEADERS).status_code == 200
    service.list.assert_awaited_once_with(principal(), "setup", offset=0, limit=20)
    response = http.get(START + "-profiles", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == [choice.model_dump(mode="json")]
    assert response.headers["cache-control"] == "private, no-store"
    service.list_profiles.assert_awaited_once_with(principal(), "setup")
    assert "private" not in response.text


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=101", "offset=invalid"])
def test_history_invalid_page_rejects_before_service(query):
    service = injected()
    response = client(service).get(START + "?" + query, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    assert response.headers["cache-control"] == "private, no-store"
    service.list.assert_not_called()


@pytest.mark.parametrize("path", [START, START + "-profiles"])
def test_discovery_and_history_optional_service_and_auth(path):
    response = client(None).get(path, headers=HEADERS)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    identity = AsyncMock()
    identity.resolve.side_effect = AccessDenied("private")
    service = injected()
    response = client(service, identity).get(path, headers=HEADERS)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"
    service.list.assert_not_called()
    service.list_profiles.assert_not_called()
