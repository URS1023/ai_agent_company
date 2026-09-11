"""Office creation orchestration tests; no database execution."""

from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest
from test_office_repository import setup as office_setup

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, InvalidInput
from enterprise_platform.persistence.office_creation import SqlAlchemyOfficeFileCreator
from enterprise_platform.persistence.office_models import OfficeFileRow, OfficeGrantRow, OfficeRevisionRow


def setup_creator():
    _, _, _, _, original, _, _ = office_setup()
    sessions, session, policy = MagicMock(), MagicMock(), Mock()
    sessions.begin.return_value.__enter__.return_value = session
    session.connection.return_value.dialect.name = "postgresql"
    session.scalar.return_value = None
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="normal", display_name="Actor")
    return SqlAlchemyOfficeFileCreator(sessions, policy), session, policy, principal, original


def test_create_persists_initial_revision_owner_and_head_atomically():
    creator, session, policy, principal, record = setup_creator()
    assert creator.create(principal, record).fingerprint() == record.fingerprint()
    policy.require.assert_called_once_with(session, principal, record)
    rows = [call.args[0] for call in session.add.call_args_list]
    head = next(row for row in rows if isinstance(row, OfficeFileRow))
    assert (head.created_by, head.current_revision, head.acl_revision) == ("actor", 1, 1)
    owner = next(row for row in rows if isinstance(row, OfficeGrantRow))
    assert (owner.actor_id, owner.can_read, owner.can_edit) == ("actor", True, True)
    history = next(row for row in rows if isinstance(row, OfficeRevisionRow))
    assert history.revision == 1
    assert len(rows) == 4
    session.delete.assert_not_called()
    assert session.flush.call_count == 1


def test_create_wrong_workspace_denied_before_transaction_content_lookup():
    creator, session, policy, principal, record = setup_creator()
    with pytest.raises(AccessDenied):
        creator.create(principal, replace(record, workspace_id="other"))
    policy.require.assert_not_called()
    session.scalar.assert_not_called()
    session.add.assert_not_called()


def test_create_requires_initial_revision():
    creator, session, policy, principal, record = setup_creator()
    with pytest.raises(InvalidInput):
        creator.create(principal, replace(record, content=record.content.model_copy(update={"revision": 2})))
    policy.require.assert_not_called()
    session.add.assert_not_called()


def test_creation_policy_denial_prevents_content_grants_and_audit():
    creator, session, policy, principal, record = setup_creator()
    policy.require.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        creator.create(principal, record)
    assert session.scalar.call_count == 1
    assert session.add.call_count == 1
    assert isinstance(session.add.call_args.args[0], OfficeFileRow)


def replay_rows(record, *, creator="actor", can_edit=True):
    import hashlib

    from enterprise_platform.persistence.office_documents import encode_office_record

    document = encode_office_record(record)
    return [
        OfficeFileRow(created_by=creator, current_revision=5, acl_revision=2),
        OfficeGrantRow(can_read=True, can_edit=can_edit),
        OfficeRevisionRow(document_json=document, document_hash=hashlib.sha256(document.encode()).hexdigest()),
    ]


def test_replay_returns_initial_record_not_advanced_head_without_writes():
    creator, session, policy, principal, record = setup_creator()
    session.scalar.side_effect = replay_rows(record)
    actual = creator.create(principal, record)
    assert actual.content.revision == 1
    assert actual.fingerprint() == record.fingerprint()
    policy.require.assert_called_once_with(session, principal, record)
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("owner,editable", [("other", True), ("actor", False)])
def test_replay_denies_other_creator_or_revoked_edit(owner, editable):
    creator, session, _, principal, record = setup_creator()
    session.scalar.side_effect = replay_rows(record, creator=owner, can_edit=editable)
    with pytest.raises(AccessDenied):
        creator.create(principal, record)
    session.add.assert_not_called()


def test_creation_id_reuse_rejects_changed_template():
    from enterprise_platform.application.errors import Conflict

    creator, session, _, principal, record = setup_creator()
    session.scalar.side_effect = replay_rows(record)
    with pytest.raises(Conflict, match="office_creation_reused"):
        creator.create(principal, replace(record, template_revision=2))
    session.add.assert_not_called()


def test_insert_race_rereads_winner_in_new_transaction_and_reauthorizes():
    from sqlalchemy.exc import IntegrityError

    creator, session, policy, principal, record = setup_creator()
    session.scalar.side_effect = [None, *replay_rows(record)]
    session.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    assert creator.create(principal, record).fingerprint() == record.fingerprint()
    assert policy.require.call_count == 1
    assert creator._sessions.begin.call_count == 2
    assert session.add.call_count == 1


def test_unrelated_integrity_failure_does_not_repeat_insert():
    from sqlalchemy.exc import IntegrityError

    from enterprise_platform.application.errors import Conflict

    creator, session, policy, principal, record = setup_creator()
    session.scalar.return_value = None
    session.flush.side_effect = IntegrityError("insert", {}, Exception("invalid"))
    with pytest.raises(Conflict, match="office_creation_conflict"):
        creator.create(principal, record)
    assert session.add.call_count == 1
    policy.require.assert_not_called()


def test_replay_locks_file_before_source_policy():
    creator, session, policy, principal, record = setup_creator()
    session.scalar.side_effect = replay_rows(record)
    events = []
    session.scalar.side_effect = lambda statement: (events.append("file_or_content"), rows.pop(0))[1]
    rows = replay_rows(record)
    policy.require.side_effect = lambda *args: events.append("source_policy")
    creator.create(principal, record)
    assert events[0] == "file_or_content"
    assert events[1] == "source_policy"
