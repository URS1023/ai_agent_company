from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from test_business_service import principal
from test_schedule_actor_choices import configured
from test_schedule_lookup import device
from test_schedule_service import fixture as service_fixture
from test_scheduling import schedule

from enterprise_platform.application.errors import Unauthenticated
from enterprise_platform.http.app import create_app

CREATE = "/enterprise/api/v1/devices/d/alert/schedule"
ITEM = "/enterprise/api/v1/schedules/schedule-1"
HEADERS = {"Origin": "https://console.test", "Authorization": "Bearer private", "X-CSRF-Token": "csrf"}


def test_actor_choices_expose_only_id_and_label_with_native_authentication() -> None:
    service, _, _, _, worker = configured()
    identity = AsyncMock(return_value=principal())
    identity.resolve.return_value = principal()
    http = TestClient(create_app(MagicMock(), identity, schedules=service))
    response = http.get(CREATE + "/actors", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == [{"actor_id": "service", "display_name": "Worker"}]
    assert response.headers["cache-control"] == "private, no-store"
    worker.assert_awaited_once()
    identity.resolve.return_value = principal("normal")
    assert http.get(CREATE + "/actors", headers=HEADERS).status_code == 403
    assert worker.await_count == 1


@pytest.mark.parametrize("exists", [True, False])
def test_device_schedule_lookup_is_nullable_and_private(exists: bool) -> None:
    service, repo, business, _, _ = service_fixture()
    business.get_device.return_value = device()
    value = schedule(workspace_id="w", device_id="d", enabled=False) if exists else None
    repo.find_for_device.return_value = value
    identity = AsyncMock()
    identity.resolve.return_value = principal("normal")
    http = TestClient(create_app(MagicMock(), identity, schedules=service))
    response = http.get(CREATE, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == (value.model_dump(mode="json") if value else None)
    assert response.headers["cache-control"] == "private, no-store"
    repo.find_for_device.assert_called_once_with("w", "d", "alert")


def fixture():
    service, repo, _, _, payload = service_fixture()
    identity = AsyncMock()
    identity.resolve.return_value = principal()
    http = TestClient(create_app(MagicMock(), identity, allowed_origins=("https://console.test",), schedules=service))
    return http, repo, identity, payload


def test_create_read_and_pause_use_real_scoped_service_and_private_responses() -> None:
    http, repo, _, payload = fixture()
    created = http.post(CREATE, json=payload.model_dump(mode="json"), headers=HEADERS)
    assert created.status_code == 201
    assert created.json()["workspace_id"] == "w" and created.json()["enabled"] is False
    value = repo.create.call_args.args[0]
    repo.get.return_value = value
    assert http.get(ITEM, headers=HEADERS).json() == created.json()
    repo.set_enabled.return_value = value
    response = http.post(ITEM + "/state", json={"expected_revision": 1, "enabled": False}, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "private" not in response.text


def test_readonly_actor_cannot_create_schedule() -> None:
    http, repo, identity, payload = fixture()
    identity.resolve.return_value = principal("normal")
    response = http.post(CREATE, json=payload.model_dump(mode="json"), headers=HEADERS)
    assert response.status_code == 403
    repo.create.assert_not_called()


def test_mutation_without_allowed_origin_is_denied_before_identity_lookup() -> None:
    http, repo, identity, payload = fixture()
    response = http.post(CREATE, json=payload.model_dump(mode="json"))
    assert response.status_code == 403
    identity.resolve.assert_not_awaited()
    repo.create.assert_not_called()


def test_unauthenticated_read_does_not_query_repository() -> None:
    http, repo, identity, _ = fixture()
    identity.resolve.side_effect = Unauthenticated()
    response = http.get(ITEM, headers=HEADERS)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    repo.get.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": True, "enabled": False},
        {"expected_revision": 1, "enabled": "false"},
        {"expected_revision": 1, "enabled": False, "token": "private"},
    ],
)
def test_invalid_state_command_is_sanitized_before_service(body) -> None:
    http, repo, _, _ = fixture()
    response = http.post(ITEM + "/state", json=body, headers=HEADERS)
    assert response.status_code == 422 and response.json() == {"code": "invalid_input"}
    repo.set_enabled.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"interval_seconds": 0},
        {"window_seconds": 0},
        {"grace_seconds": 60},
        {"service_actor_id": ""},
        {"anchor_at": "not-a-time"},
    ],
)
def test_create_validation_errors_never_reach_schedule_persistence(patch) -> None:
    http, repo, _, payload = fixture()
    response = http.post(CREATE, json=payload.model_dump(mode="json") | patch, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    repo.create.assert_not_called()


def test_missing_feature_is_explicitly_unavailable_not_a_fake_success() -> None:
    identity = AsyncMock()
    identity.resolve.return_value = principal()
    http = TestClient(create_app(MagicMock(), identity))
    assert http.get(ITEM, headers=HEADERS).status_code == 503


def test_no_browser_tick_or_poll_endpoints_are_exposed() -> None:
    http, repo, _, _ = fixture()
    assert http.post(ITEM + "/tick", headers=HEADERS).status_code == 404
    assert http.post("/enterprise/api/v1/schedules/poll", headers=HEADERS).status_code == 405
    assert "/enterprise/api/v1/schedules/poll" not in http.get("/enterprise/api/openapi.json").json()["paths"]
    repo.commit_tick.assert_not_called()
