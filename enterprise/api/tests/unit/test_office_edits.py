from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

import pytest

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, PersistenceError
from enterprise_platform.application.office_edits import (
    OfficeEditCommand,
    OfficeEditReceipt,
    OfficeEditService,
    OfficeFileRecord,
    OfficeGrant,
)
from enterprise_platform.domain.office_content import TableData
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeText, OfficeUnit, UnitReplacement

FILE, UNIT, REQUEST = UUID(int=1), UUID(int=2), UUID(int=3)


def setup() -> tuple[OfficeEditService, Mock, Mock, Principal, OfficeFileRecord, OfficeEditCommand]:
    actor = Principal(actor_id="actor", workspace_id="workspace", workspace_role="normal", display_name="User")
    record = OfficeFileRecord(
        workspace_id="workspace",
        template_id="template",
        template_revision=1,
        source_snapshot_ids=("snapshot-1",),
        content=OfficeRevision(
            file_id=FILE,
            revision=1,
            kind="presentation",
            units=(OfficeUnit(unit_id=UNIT, kind="slide", content=(OfficeText(text="Original"),)),),
        ),
    )
    command = OfficeEditCommand(
        request_id=REQUEST,
        expected_revision=1,
        replacements=(UnitReplacement(unit_id=UNIT, content=(OfficeText(text="Changed"),)),),
    )
    repository, policy = Mock(), Mock()
    policy.authorize.return_value = OfficeGrant("workspace", "actor", FILE, "edit", 4)
    repository.get.return_value = record
    repository.find_receipt.return_value = None
    repository.commit.side_effect = lambda **kwargs: OfficeEditReceipt(
        workspace_id="workspace",
        actor_id="actor",
        file_id=FILE,
        request_id=REQUEST,
        command_hash=kwargs["command_hash"],
        record=kwargs["candidate"],
    )
    return OfficeEditService(repository, policy), repository, policy, actor, record, command


def test_edit_authorizes_and_passes_expected_revision_and_bindings_to_commit() -> None:
    service, repository, policy, actor, original, command = setup()
    result = service.edit(actor, FILE, command)
    policy.authorize.assert_called_once_with(actor, FILE, "edit")
    args = repository.commit.call_args.kwargs
    assert args["expected"] == original
    assert args["grant"].acl_revision == 4
    assert args["candidate"].source_snapshot_ids == original.source_snapshot_ids
    assert args["candidate"].template_id == original.template_id
    assert result.record.content.revision == 2
    assert original.content.revision == 1


def test_denial_happens_before_read_or_receipt_lookup() -> None:
    service, repository, policy, actor, _, command = setup()
    policy.authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        service.edit(actor, FILE, command)
    assert repository.mock_calls == []


def test_wrong_scope_grant_is_rejected_before_read() -> None:
    service, repository, policy, actor, _, command = setup()
    policy.authorize.return_value = OfficeGrant("other", "actor", FILE, "edit", 4)
    with pytest.raises(AccessDenied):
        service.edit(actor, FILE, command)
    assert repository.mock_calls == []


def test_wrong_scope_record_is_rejected_without_commit() -> None:
    service, repository, _, actor, record, command = setup()
    repository.get.return_value = replace(record, workspace_id="other")
    with pytest.raises(AccessDenied):
        service.edit(actor, FILE, command)
    repository.commit.assert_not_called()


def test_stale_edit_and_concurrent_commit_conflict_are_not_retried() -> None:
    service, repository, _, actor, _, command = setup()
    stale = OfficeEditCommand(request_id=REQUEST, expected_revision=2, replacements=command.replacements)
    with pytest.raises(Conflict):
        service.edit(actor, FILE, stale)
    repository.commit.assert_not_called()
    repository.commit.side_effect = Conflict()
    with pytest.raises(Conflict):
        service.edit(actor, FILE, command)
    assert repository.commit.call_count == 1


def test_matching_retry_returns_original_receipt_without_reapplying_edit() -> None:
    service, repository, policy, actor, _, command = setup()
    saved = service.edit(actor, FILE, command)
    repository.reset_mock()
    repository.find_receipt.return_value = saved
    assert service.edit(actor, FILE, command) == saved
    repository.get.assert_not_called()
    repository.commit.assert_not_called()
    assert policy.authorize.call_count == 2


def test_identical_request_committed_between_receipt_lookup_and_file_read_replays() -> None:
    service, repository, _, actor, _, command = setup()
    saved = service.edit(actor, FILE, command)
    repository.reset_mock()
    repository.find_receipt.side_effect = [None, saved]
    repository.get.return_value = saved.record
    assert service.edit(actor, FILE, command) == saved
    repository.commit.assert_not_called()


def test_reused_request_id_with_changed_payload_conflicts() -> None:
    service, repository, _, actor, _, command = setup()
    repository.find_receipt.return_value = service.edit(actor, FILE, command)
    repository.reset_mock()
    changed = OfficeEditCommand(request_id=REQUEST, expected_revision=2, replacements=command.replacements)
    with pytest.raises(Conflict):
        service.edit(actor, FILE, changed)
    repository.commit.assert_not_called()


def test_mismatched_commit_receipt_is_not_reported_as_success() -> None:
    service, repository, _, actor, _, command = setup()
    saved = service.edit(actor, FILE, command)
    repository.commit.side_effect = None
    repository.commit.return_value = replace(saved, actor_id="other")
    with pytest.raises(PersistenceError):
        service.edit(actor, FILE, command)


def test_read_only_retrieves_existing_record_without_committing_or_replaying() -> None:
    service, repository, policy, actor, record, _ = setup()
    policy.authorize.return_value = OfficeGrant("workspace", "actor", FILE, "read", 4)
    assert service.read(actor, FILE) == record
    policy.authorize.assert_called_once_with(actor, FILE, "read")
    repository.commit.assert_not_called()
    repository.find_receipt.assert_not_called()


def test_denied_read_never_loads_content() -> None:
    service, repository, policy, actor, _, _ = setup()
    policy.authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        service.read(actor, FILE)
    assert repository.mock_calls == []


def test_revocation_between_policy_check_and_repository_read_propagates() -> None:
    service, repository, _, actor, _, command = setup()
    repository.find_receipt.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        service.edit(actor, FILE, command)
    repository.get.assert_not_called()
    repository.commit.assert_not_called()


@pytest.mark.parametrize("version", [0, -1, True])
def test_invalid_access_version_is_rejected_before_repository_access(version: int) -> None:
    service, repository, policy, actor, _, command = setup()
    policy.authorize.return_value = OfficeGrant("workspace", "actor", FILE, "edit", version)
    with pytest.raises(AccessDenied):
        service.edit(actor, FILE, command)
    assert repository.mock_calls == []


def test_replay_cannot_return_another_actors_saved_revision() -> None:
    service, repository, _, actor, _, command = setup()
    receipt = service.edit(actor, FILE, command)
    repository.reset_mock()
    repository.find_receipt.return_value = replace(receipt, actor_id="other")
    with pytest.raises(PersistenceError):
        service.edit(actor, FILE, command)
    repository.get.assert_not_called()
    repository.commit.assert_not_called()


def test_changed_template_or_snapshot_in_commit_receipt_is_rejected() -> None:
    service, repository, _, actor, _, command = setup()
    receipt = service.edit(actor, FILE, command)
    repository.commit.side_effect = None
    for changed in [replace(receipt.record, template_revision=2), replace(receipt.record, source_snapshot_ids=())]:
        repository.commit.return_value = replace(receipt, record=changed)
        with pytest.raises(PersistenceError):
            service.edit(actor, FILE, command)


@pytest.mark.parametrize("changed_value", [1, Decimal("1.00")])
def test_commit_receipt_preserves_decimal_cell_type_and_scale(changed_value: int | Decimal) -> None:
    service, repository, _, actor, _, _ = setup()
    table = TableData(table_id="quality", headers=("Value",), rows=((Decimal("1.0"),),))
    command = OfficeEditCommand(
        request_id=REQUEST, expected_revision=1, replacements=(UnitReplacement(unit_id=UNIT, content=(table,)),)
    )
    saved = service.edit(actor, FILE, command)
    changed = TableData(table_id="quality", headers=("Value",), rows=((changed_value,),))
    changed_content = OfficeRevision(
        file_id=FILE,
        revision=2,
        kind="presentation",
        units=(OfficeUnit(unit_id=UNIT, kind="slide", content=(changed,)),),
    )
    repository.commit.side_effect = None
    repository.commit.return_value = replace(saved, record=replace(saved.record, content=changed_content))
    with pytest.raises(PersistenceError):
        service.edit(actor, FILE, command)
