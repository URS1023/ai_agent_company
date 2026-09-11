"""ACL mutation orchestration; real transaction checks live in CI."""

from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.office_permissions import OfficeManagementContext, OfficePermissionChange
from enterprise_platform.persistence.office_acl import SqlAlchemyOfficeAcl
from enterprise_platform.persistence.office_models import OfficeFileRow, OfficeGrantRow


def setup_acl():
    sessions, session, policy = MagicMock(), MagicMock(), Mock()
    sessions.begin.return_value.__enter__.return_value = session
    session.connection.return_value.dialect.name = "postgresql"
    head = OfficeFileRow(workspace_id="workspace", file_id=str(UUID(int=1)), current_revision=3, acl_revision=4)
    permission = OfficeGrantRow(
        workspace_id="workspace", file_id=str(UUID(int=1)), actor_id="reader", can_read=True, can_edit=True
    )
    session.scalar.side_effect = [head, permission]
    principal = Principal(workspace_id="workspace", actor_id="owner", workspace_role="normal", display_name="Owner")
    command = OfficePermissionChange(actor_id="reader", expected_acl_revision=4, can_read=False, can_edit=False)
    return SqlAlchemyOfficeAcl(sessions, policy), session, policy, head, permission, principal, command


def test_revoke_advances_acl_without_touching_content_revision():
    store, session, policy, head, permission, principal, command = setup_acl()
    assert store.change(principal, UUID(int=1), command) == 5
    assert (head.current_revision, head.acl_revision) == (3, 5)
    assert (permission.can_read, permission.can_edit) == (False, False)
    policy.require.assert_called_once_with(
        session, principal, OfficeManagementContext(head.workspace_id, UUID(int=1), head.created_by, 4), command
    )
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0])
    assert session.add.call_count == 1


def test_stale_acl_change_conflicts_without_mutating_permissions():
    store, session, _, head, permission, principal, command = setup_acl()
    head.acl_revision = 5
    with pytest.raises(Conflict):
        store.change(principal, UUID(int=1), command)
    assert permission.can_edit
    assert session.scalar.call_count == 1
    session.add.assert_not_called()


def test_denied_management_cannot_read_target_permission_or_write():
    store, session, policy, _, _, principal, command = setup_acl()
    policy.require.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        store.change(principal, UUID(int=1), command)
    assert session.scalar.call_count == 1
    session.add.assert_not_called()


def test_same_permissions_do_not_advance_acl_or_create_audit():
    store, session, _, head, permission, principal, command = setup_acl()
    permission.can_read = permission.can_edit = False
    assert store.change(principal, UUID(int=1), command) == 4
    assert head.acl_revision == 4
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "fields",
    [
        {"can_read": False, "can_edit": True},
        {"expected_acl_revision": True},
        {"can_read": 1},
        {"actor_id": ""},
        {"expected_acl_revision": 0},
    ],
)
def test_acl_contract_rejects_invalid_permission_changes(fields):
    values = {"actor_id": "reader", "expected_acl_revision": 4, "can_read": True, "can_edit": False}
    with pytest.raises(ValidationError):
        OfficePermissionChange(**(values | fields))


def test_grant_to_new_actor_creates_only_permission_and_audit():
    store, session, _, head, _, principal, command = setup_acl()
    session.scalar.side_effect = [head, None]
    command = command.model_copy(update={"can_read": True})
    assert store.change(principal, UUID(int=1), command) == 5
    rows = [c.args[0] for c in session.add.call_args_list]
    assert len(rows) == 2
    permission = next(row for row in rows if isinstance(row, OfficeGrantRow))
    assert (permission.workspace_id, permission.file_id, permission.actor_id) == (
        "workspace",
        str(UUID(int=1)),
        "reader",
    )
    assert (permission.can_read, permission.can_edit) == (True, False)


def test_revoking_absent_grant_does_not_create_row():
    store, session, _, head, _, principal, command = setup_acl()
    session.scalar.side_effect = [head, None]
    assert store.change(principal, UUID(int=1), command) == 4
    session.add.assert_not_called()


def test_acl_revision_overflow_never_mutates_grant():
    store, session, _, head, permission, principal, command = setup_acl()
    head.acl_revision = 9223372036854775807
    command = command.model_copy(update={"expected_acl_revision": head.acl_revision})
    with pytest.raises(Conflict, match="office_acl_revision_exhausted"):
        store.change(principal, UUID(int=1), command)
    assert permission.can_edit
    session.add.assert_not_called()


def test_management_context_cannot_be_mutated_by_policy():
    from dataclasses import FrozenInstanceError

    context = OfficeManagementContext("workspace", UUID(int=1), "owner", 4)
    with pytest.raises(FrozenInstanceError):
        context.acl_revision = 5
