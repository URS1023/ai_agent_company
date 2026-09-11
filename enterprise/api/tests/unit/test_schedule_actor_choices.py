import asyncio
from unittest.mock import AsyncMock

import pytest
from test_business_service import principal
from test_schedule_lookup import device
from test_schedule_service import fixture
from test_scheduling import START

from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable, Unauthenticated


def configured():
    service, repo, business, authority, _ = fixture()
    business.get_device.return_value = device()
    identity = AsyncMock(return_value=authority.get_grant.return_value.principal)
    service._service_identity = identity
    return service, repo, business, authority, identity


def test_choices_project_only_current_configured_actor_and_label() -> None:
    service, repo, business, authority, identity = configured()
    result = asyncio.run(service.actor_choices(principal(), "d", "alert"))
    assert [item.model_dump() for item in result] == [{"actor_id": "service", "display_name": "Worker"}]
    identity.assert_awaited_once()
    authority.get_grant.assert_called_once_with("w", "service")
    authority.has_native_timer.assert_not_called()
    business.get_binding.assert_not_called()
    repo.create.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"enabled": False},
        {"expires_at": START},
        {"device_ids": ("other",)},
        {"scenarios": ("quality",)},
    ],
)
def test_disabled_expired_or_unscoped_grants_are_not_choices(patch: dict[str, object]) -> None:
    service, _, _, authority, _ = configured()
    authority.get_grant.return_value = authority.get_grant.return_value.model_copy(update=patch)
    assert asyncio.run(service.actor_choices(principal(), "d", "alert")) == ()


def test_revocation_is_observed_on_next_discovery() -> None:
    service, _, _, authority, identity = configured()
    assert asyncio.run(service.actor_choices(principal(), "d", "alert"))
    authority.get_grant.return_value = None
    assert asyncio.run(service.actor_choices(principal(), "d", "alert")) == ()
    assert identity.await_count == 2


def test_readonly_caller_does_not_query_device_identity_or_grants() -> None:
    service, _, business, authority, identity = configured()
    with pytest.raises(AccessDenied):
        asyncio.run(service.actor_choices(principal("normal"), "d", "alert"))
    business.get_device.assert_not_called()
    identity.assert_not_awaited()
    authority.get_grant.assert_not_called()


def test_missing_identity_provider_is_explicitly_unavailable() -> None:
    service, _, business, _, _ = fixture()
    business.get_device.return_value = device()
    with pytest.raises(DependencyUnavailable):
        asyncio.run(service.actor_choices(principal(), "d", "alert"))


def test_worker_session_failure_is_not_reported_as_viewer_login_expiry() -> None:
    service, _, _, authority, identity = configured()
    identity.side_effect = Unauthenticated("private worker detail")
    with pytest.raises(DependencyUnavailable, match="schedule_identity_unavailable"):
        asyncio.run(service.actor_choices(principal(), "d", "alert"))
    authority.get_grant.assert_not_called()


@pytest.mark.parametrize("patch", [{"workspace_id": "other"}, {"workspace_role": "normal"}])
def test_wrong_scope_or_readonly_native_worker_is_not_a_choice(patch: dict[str, object]) -> None:
    service, _, _, authority, identity = configured()
    identity.return_value = identity.return_value.model_copy(update=patch)
    assert asyncio.run(service.actor_choices(principal(), "d", "alert")) == ()
    authority.get_grant.assert_not_called()
