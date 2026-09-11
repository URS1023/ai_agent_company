import asyncio
from datetime import timedelta
from unittest.mock import create_autospec

import pytest
from pydantic import ValidationError
from test_business_service import binding, principal
from test_schedule_read_preflight import registration
from test_scheduling import START

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.schedule_service import (
    CreateSchedule,
    ScheduleAuthority,
    ScheduleRepository,
    ScheduleService,
    ServiceGrant,
)
from enterprise_platform.application.scheduling import IntervalSchedule
from enterprise_platform.application.service import BusinessService


def fixture():
    repo = create_autospec(ScheduleRepository, instance=True)
    business = create_autospec(BusinessService, instance=True)
    business.get_binding.return_value = binding().model_copy(
        update={"manifest": {"input_keys": ["window_start", "window_end"]}}
    )
    authority = create_autospec(ScheduleAuthority, instance=True)
    authority.get_grant.return_value = ServiceGrant(
        principal=Principal(actor_id="service", workspace_id="w", workspace_role="editor", display_name="Worker"),
        device_ids=("d",),
        scenarios=("alert",),
        enabled=True,
        expires_at=START + timedelta(hours=1),
    )
    authority.has_native_timer.return_value = False
    repo.create.side_effect = lambda value, **kwargs: value
    service = ScheduleService(
        repo,
        business,
        authority,
        reads=ImmutableReadCatalog((registration(business.get_binding.return_value),)),
        clock=lambda: START,
        id_factory=lambda: "schedule-1",
    )
    payload = CreateSchedule(
        expected_binding_revision=2,
        service_actor_id="service",
        anchor_at=START,
        interval_seconds=60,
        window_seconds=300,
        grace_seconds=10,
        missed_policy="skip",
    )
    return service, repo, business, authority, payload


def create(service, payload) -> IntervalSchedule:
    return asyncio.run(service.create(principal(), "d", "alert", payload))


def test_create_uses_server_scope_and_always_registers_paused() -> None:
    service, repo, business, authority, payload = fixture()
    value = create(service, payload)
    assert (value.id, value.workspace_id, value.device_id, value.binding_id) == ("schedule-1", "w", "d", "b")
    assert value.revision == 1 and not value.enabled
    assert value.next_due_at == START
    repo.create.assert_called_once_with(value, actor_id="a")
    assert authority.get_grant.call_count == 2
    authority.get_grant.assert_called_with("w", "service")
    authority.has_native_timer.assert_called_once_with(business.get_binding.return_value)


@pytest.mark.parametrize("role", ["normal", "dataset_operator"])
def test_denied_manager_never_queries_or_creates(role: str) -> None:
    service, repo, business, authority, payload = fixture()
    with pytest.raises(AccessDenied):
        asyncio.run(service.create(principal(role), "d", "alert", payload))
    repo.create.assert_not_called()
    business.get_binding.assert_not_called()
    authority.get_grant.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"enabled": False},
        {"device_ids": ("other",)},
        {"scenarios": ("quality",)},
        {"expires_at": START},
    ],
)
def test_disabled_expired_or_out_of_scope_service_grants_do_not_create(patch: dict[str, object]) -> None:
    service, repo, _, authority, payload = fixture()
    authority.get_grant.return_value = authority.get_grant.return_value.model_copy(update=patch)
    with pytest.raises(AccessDenied):
        create(service, payload)
    repo.create.assert_not_called()


@pytest.mark.parametrize("patch", [{"workspace_id": "other"}, {"actor_id": "other"}, {"workspace_role": "normal"}])
def test_service_principal_must_match_requested_scope_and_have_run_permission(patch: dict[str, object]) -> None:
    service, repo, _, authority, payload = fixture()
    old = authority.get_grant.return_value
    authority.get_grant.return_value = old.model_copy(update={"principal": old.principal.model_copy(update=patch)})
    with pytest.raises(AccessDenied):
        create(service, payload)
    repo.create.assert_not_called()


def test_native_timer_or_stale_binding_prevents_schedule_creation() -> None:
    service, repo, business, authority, payload = fixture()
    authority.has_native_timer.return_value = True
    with pytest.raises(Conflict):
        create(service, payload)
    authority.has_native_timer.return_value = False
    business.get_binding.return_value = binding().model_copy(update={"revision": 3})
    with pytest.raises(Conflict):
        create(service, payload)
    repo.create.assert_not_called()


def test_pausing_remains_available_after_grant_revocation_or_native_outage() -> None:
    service, repo, business, authority, payload = fixture()
    original = create(service, payload).model_copy(update={"enabled": True, "revision": 2})
    repo.get.return_value = original
    repo.set_enabled.return_value = original.model_copy(update={"enabled": False, "revision": 3})
    business.get_binding.reset_mock()
    authority.get_grant.side_effect = RuntimeError("offline")
    value = asyncio.run(service.set_enabled(principal(), original.id, expected_revision=2, enabled=False))
    assert not value.enabled
    business.get_binding.assert_not_called()


def test_enabling_rechecks_service_grant_before_writing() -> None:
    service, repo, _, authority, payload = fixture()
    original = create(service, payload)
    repo.get.return_value = original
    authority.get_grant.return_value = None
    with pytest.raises(AccessDenied):
        asyncio.run(service.set_enabled(principal(), original.id, expected_revision=1, enabled=True))
    repo.set_enabled.assert_not_called()


def test_foreign_or_mismatched_repository_receipt_is_not_returned() -> None:
    service, repo, _, _, payload = fixture()
    original = create(service, payload)
    repo.get.return_value = original.model_copy(update={"workspace_id": "other"})
    with pytest.raises(AccessDenied):
        asyncio.run(service.get(principal(), original.id))
    repo.create.side_effect = lambda value, **kwargs: value.model_copy(update={"enabled": True})
    with pytest.raises(Conflict):
        create(service, payload)


@pytest.mark.parametrize("state", [None, 0, "false"])
def test_uncertain_native_timer_state_is_not_treated_as_unowned(state: object) -> None:
    service, repo, _, authority, payload = fixture()
    authority.has_native_timer.return_value = state
    with pytest.raises(Conflict):
        create(service, payload)
    repo.create.assert_not_called()


def test_resume_rejects_replaced_binding_even_if_revision_is_equal() -> None:
    service, repo, business, authority, payload = fixture()
    value = create(service, payload)
    repo.get.return_value = value
    business.get_binding.return_value = binding().model_copy(update={"id": "replacement"})
    authority.get_grant.reset_mock()
    with pytest.raises(Conflict):
        asyncio.run(service.set_enabled(principal(), value.id, expected_revision=1, enabled=True))
    authority.get_grant.assert_not_called()
    repo.set_enabled.assert_not_called()


def test_stale_state_command_does_not_reauthorize_or_write() -> None:
    service, repo, _, authority, payload = fixture()
    value = create(service, payload)
    repo.get.return_value = value
    authority.get_grant.reset_mock()
    with pytest.raises(Conflict):
        asyncio.run(service.set_enabled(principal(), value.id, expected_revision=2, enabled=True))
    authority.get_grant.assert_not_called()
    repo.set_enabled.assert_not_called()


@pytest.mark.parametrize(
    "extra", [{"workspace_id": "other"}, {"enabled": True}, {"binding_id": "other"}, {"id": "chosen"}]
)
def test_create_payload_has_no_client_selected_scope_or_lifecycle(extra: dict[str, object]) -> None:
    _, _, _, _, payload = fixture()
    with pytest.raises(ValidationError):
        CreateSchedule.model_validate(payload.model_dump() | extra)


def test_valid_resume_preserves_configuration_and_records_human_actor() -> None:
    service, repo, _, authority, payload = fixture()
    value = create(service, payload)
    repo.get.return_value = value
    repo.set_enabled.return_value = value.model_copy(update={"revision": 2, "enabled": True})
    authority.get_grant.reset_mock()
    result = asyncio.run(service.set_enabled(principal(), value.id, expected_revision=1, enabled=True))
    assert result.next_due_at == value.next_due_at
    assert authority.get_grant.call_count == 2
    authority.get_grant.assert_called_with("w", "service")
    repo.set_enabled.assert_called_once_with("w", value.id, expected_revision=1, enabled=True, actor_id="a")


def test_missing_registered_window_mapping_rejects_configuration_before_write() -> None:
    service, repo, _, _, payload = fixture()
    service._reads = ImmutableReadCatalog(())
    with pytest.raises(InvalidInput):
        create(service, payload)
    repo.create.assert_not_called()


def test_pause_remains_available_after_source_registration_is_removed() -> None:
    service, repo, _, _, payload = fixture()
    value = create(service, payload).model_copy(update={"enabled": True})
    repo.get.return_value = value
    repo.set_enabled.return_value = value.model_copy(update={"enabled": False, "revision": 2})
    service._reads = ImmutableReadCatalog(())
    result = asyncio.run(service.set_enabled(principal(), value.id, expected_revision=1, enabled=False))
    assert not result.enabled
