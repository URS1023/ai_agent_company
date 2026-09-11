import asyncio

import pytest
from test_business_service import principal
from test_schedule_persistence import fixture as persistence_fixture
from test_schedule_service import fixture as service_fixture
from test_scheduling import START, schedule

from enterprise_platform.application.contracts import Device
from enterprise_platform.application.errors import AccessDenied, NotFound, PersistenceError
from enterprise_platform.persistence.schedule_mapping import schedule_row


def device() -> Device:
    return Device(
        id="d", workspace_id="w", device_code="001", name="Pump", revision=1, created_at=START, updated_at=START
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_repository_finds_enabled_and_paused_owner_without_cursor_mutation(enabled: bool) -> None:
    repo, session, _ = persistence_fixture()
    value = schedule(enabled=enabled)
    session.scalar.return_value = schedule_row(value, START, START)
    assert repo.find_for_device("workspace-1", "001", "alert") == value
    query = session.scalar.call_args.args[0].compile()
    assert set(query.params.values()) == {"workspace-1", "001", "alert"}
    session.execute.assert_not_called()
    session.add.assert_not_called()


def test_repository_absence_is_nullable_and_scope_mismatch_is_rejected() -> None:
    repo, session, _ = persistence_fixture()
    session.scalar.return_value = None
    assert repo.find_for_device("workspace-1", "001", "alert") is None
    session.scalar.return_value = persistence_fixture()[2]
    for scope in [("other", "001", "alert"), ("workspace-1", "other", "alert"), ("workspace-1", "001", "quality")]:
        with pytest.raises(PersistenceError):
            repo.find_for_device(*scope)


@pytest.mark.parametrize("exists", [True, False])
def test_reader_discovers_schedule_without_grant_or_binding_checks(exists: bool) -> None:
    service, repo, business, authority, _ = service_fixture()
    business.get_device.return_value = device()
    value = schedule(workspace_id="w", device_id="d", enabled=False) if exists else None
    repo.find_for_device.return_value = value
    assert asyncio.run(service.find_for_device(principal("normal"), "d", "alert")) == value
    repo.find_for_device.assert_called_once_with("w", "d", "alert")
    business.get_binding.assert_not_called()
    authority.get_grant.assert_not_called()


def test_missing_device_stops_lookup() -> None:
    service, repo, business, _, _ = service_fixture()
    business.get_device.side_effect = NotFound()
    with pytest.raises(NotFound):
        asyncio.run(service.find_for_device(principal(), "d", "alert"))
    repo.find_for_device.assert_not_called()


@pytest.mark.parametrize("patch", [{"workspace_id": "other"}, {"id": "other"}, {"deleted_at": START}])
def test_wrong_scope_or_deleted_device_stops_lookup(patch: dict[str, object]) -> None:
    service, repo, business, _, _ = service_fixture()
    business.get_device.return_value = device().model_copy(update=patch)
    with pytest.raises(AccessDenied):
        asyncio.run(service.find_for_device(principal(), "d", "alert"))
    repo.find_for_device.assert_not_called()


@pytest.mark.parametrize("patch", [{"workspace_id": "other"}, {"device_id": "other"}, {"scenario": "quality"}])
def test_service_rejects_mismatched_schedule_receipt(patch: dict[str, object]) -> None:
    service, repo, business, _, _ = service_fixture()
    business.get_device.return_value = device()
    repo.find_for_device.return_value = schedule(workspace_id="w", device_id="d").model_copy(update=patch)
    with pytest.raises(AccessDenied):
        asyncio.run(service.find_for_device(principal(), "d", "alert"))
