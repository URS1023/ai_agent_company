from dataclasses import replace
from unittest.mock import Mock
from uuid import UUID

import pytest
from test_office_export import setup

from enterprise_platform.application.errors import AccessDenied, InvalidInput, PersistenceError
from enterprise_platform.application.office_directory import OfficeCandidates, OfficeDirectoryService
from enterprise_platform.application.office_edits import OfficeEditService, OfficeGrant


def directory():
    _, principal, repository, policy, _, record = setup()
    candidates = Mock()
    candidates.candidates.return_value = OfficeCandidates((UUID(int=1),), False)
    files = OfficeEditService(repository, policy)
    return OfficeDirectoryService(candidates, files), candidates, principal, repository, policy, record


def test_directory_checks_current_permissions_and_returns_metadata_only():
    service, candidates, principal, _, _, _ = directory()
    page = service.list_files(principal)
    assert page.items[0].file_id == UUID(int=1)
    assert page.items[0].revision == "1"
    assert page.next_offset is None
    assert "content" not in page.items[0].model_dump()
    assert "source_snapshot_ids" not in page.items[0].model_dump()
    candidates.candidates.assert_called_once_with(principal, offset=0, limit=50)


def test_revoked_sources_are_omitted_but_scan_continues_with_bounded_cursor():
    service, candidates, principal, repository, policy, original = directory()
    candidates.candidates.return_value = OfficeCandidates((UUID(int=1), UUID(int=2)), True)
    policy.authorize.side_effect = [AccessDenied(), OfficeGrant("workspace", "actor", UUID(int=2), "read", 1)]
    repository.get.return_value = replace(
        original, content=original.content.model_copy(update={"file_id": UUID(int=2)})
    )
    page = service.list_files(principal, offset=50)
    assert [item.file_id for item in page.items] == [UUID(int=2)]
    assert page.next_offset == 52


def test_dependency_failure_is_not_disguised_as_empty_directory():
    service, _, principal, repository, _, _ = directory()
    repository.get.side_effect = PersistenceError()
    with pytest.raises(PersistenceError):
        service.list_files(principal)


@pytest.mark.parametrize("offset", [True, -1, 1.0, "1", 2147483648])
def test_invalid_offset_does_not_scan(offset):
    service, candidates, principal, _, _, _ = directory()
    with pytest.raises(InvalidInput):
        service.list_files(principal, offset=offset)
    candidates.candidates.assert_not_called()


def test_all_denied_page_has_no_metadata_but_can_continue_scan():
    service, candidates, principal, _, policy, _ = directory()
    candidates.candidates.return_value = OfficeCandidates((UUID(int=1),), True)
    policy.authorize.side_effect = AccessDenied()
    page = service.list_files(principal)
    assert page.items == ()
    assert page.next_offset == 1


def test_directory_does_not_return_a_continuation_outside_supported_range():
    service, candidates, principal, _, _, _ = directory()
    candidates.candidates.return_value = OfficeCandidates((UUID(int=1),), True)
    with pytest.raises(InvalidInput, match="office_directory_offset_exhausted"):
        service.list_files(principal, offset=2147483647)
