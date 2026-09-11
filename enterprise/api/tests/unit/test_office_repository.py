"""Repository orchestration with session doubles, not database concurrency evidence."""

import hashlib
from dataclasses import replace
from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest

from enterprise_platform.application.errors import AccessDenied, Conflict, PersistenceError
from enterprise_platform.application.office_edits import OfficeFileRecord, OfficeGrant
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeText, OfficeUnit
from enterprise_platform.persistence.office_documents import encode_office_record
from enterprise_platform.persistence.office_models import OfficeFileRow, OfficeGrantRow, OfficeRevisionRow
from enterprise_platform.persistence.office_repository import SqlAlchemyOfficeEditRepository


def setup():
    grant = OfficeGrant("workspace", "actor", UUID(int=1), "edit", 4)
    original = OfficeFileRecord(
        "workspace",
        "template",
        1,
        (),
        OfficeRevision(
            file_id=grant.file_id,
            revision=1,
            kind="document",
            units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Original"),)),),
        ),
    )
    candidate = replace(
        original,
        content=OfficeRevision(
            file_id=grant.file_id,
            revision=2,
            kind="document",
            units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Changed"),)),),
        ),
    )
    document = encode_office_record(original)
    head = OfficeFileRow(workspace_id="workspace", file_id=str(grant.file_id), current_revision=1, acl_revision=4)
    permission = OfficeGrantRow(
        workspace_id="workspace", file_id=str(grant.file_id), actor_id="actor", can_read=True, can_edit=True
    )
    history = OfficeRevisionRow(
        workspace_id="workspace",
        file_id=str(grant.file_id),
        revision=1,
        document_json=document,
        document_hash=hashlib.sha256(document.encode()).hexdigest(),
    )
    sessions, session, sources = MagicMock(), MagicMock(), Mock()
    sessions.begin.return_value.__enter__.return_value = session
    session.connection.return_value.dialect.name = "postgresql"
    session.scalar.side_effect = [head, permission, history, None]
    return SqlAlchemyOfficeEditRepository(sessions, sources), session, sources, grant, original, candidate, head


def test_commit_locks_checks_and_appends_without_replacing_history() -> None:
    repository, session, sources, grant, original, candidate, head = setup()
    receipt = repository.commit(
        grant=grant, expected=original, candidate=candidate, request_id=UUID(int=3), command_hash="a" * 64
    )
    assert receipt.record.fingerprint() == candidate.fingerprint()
    assert head.current_revision == 2
    assert session.add.call_count >= 2
    session.delete.assert_not_called()
    sources.require.assert_called_once_with(session, grant, original)
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0])


def test_revoked_acl_version_never_reads_content_or_writes() -> None:
    repository, session, sources, grant, original, candidate, _ = setup()
    with pytest.raises(AccessDenied):
        repository.commit(
            grant=replace(grant, acl_revision=3),
            expected=original,
            candidate=candidate,
            request_id=UUID(int=3),
            command_hash="a" * 64,
        )
    assert session.scalar.call_count == 1
    session.add.assert_not_called()
    sources.require.assert_not_called()


def test_wrong_expected_record_conflicts_without_advancing_head() -> None:
    repository, session, _, grant, original, candidate, head = setup()
    with pytest.raises(Conflict):
        repository.commit(
            grant=grant,
            expected=replace(original, template_revision=2),
            candidate=candidate,
            request_id=UUID(int=3),
            command_hash="a" * 64,
        )
    assert head.current_revision == 1
    session.add.assert_not_called()


def test_corrupted_history_is_never_returned() -> None:
    repository, session, _, grant, _, _, head = setup()
    permission = OfficeGrantRow(can_read=True, can_edit=True)
    history = OfficeRevisionRow(document_json="sensitive corrupted document", document_hash="0" * 64)
    session.scalar.side_effect = [head, permission, history]
    with pytest.raises(PersistenceError, match="^office_history_invalid$"):
        repository.get(grant)


def test_authorize_reads_current_scoped_grant_and_source_access() -> None:
    from enterprise_platform.application.contracts import Principal

    repository, session, sources, grant, original, _, head = setup()
    permission = OfficeGrantRow(can_read=True, can_edit=True)
    document = encode_office_record(original)
    history = OfficeRevisionRow(document_json=document, document_hash=hashlib.sha256(document.encode()).hexdigest())
    session.scalar.side_effect = [head, permission, history]
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="normal", display_name="Actor")
    actual = repository.authorize(principal, grant.file_id, "edit")
    assert actual == grant
    sources.require.assert_called_once_with(session, grant, original)
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0])
    session.add.assert_not_called()


@pytest.mark.parametrize("action", ["read", "edit"])
def test_authorize_unknown_file_denies_without_source_or_content_access(action: str) -> None:
    from enterprise_platform.application.contracts import Principal

    repository, session, sources, grant, _, _, _ = setup()
    session.scalar.side_effect = [None]
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="owner", display_name="Actor")
    with pytest.raises(AccessDenied):
        repository.authorize(principal, grant.file_id, action)
    assert session.scalar.call_count == 1
    sources.require.assert_not_called()


def test_authorize_edit_denied_for_read_only_file_grant() -> None:
    from enterprise_platform.application.contracts import Principal

    repository, session, sources, grant, _, _, head = setup()
    session.scalar.side_effect = [head, OfficeGrantRow(can_read=True, can_edit=False)]
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="admin", display_name="Actor")
    with pytest.raises(AccessDenied):
        repository.authorize(principal, grant.file_id, "edit")
    assert session.scalar.call_count == 2
    sources.require.assert_not_called()


def test_authorize_source_denial_never_returns_a_grant() -> None:
    from enterprise_platform.application.contracts import Principal

    repository, session, sources, grant, original, _, head = setup()
    document = encode_office_record(original)
    session.scalar.side_effect = [
        head,
        OfficeGrantRow(can_read=True, can_edit=True),
        OfficeRevisionRow(document_json=document, document_hash=hashlib.sha256(document.encode()).hexdigest()),
    ]
    sources.require.side_effect = AccessDenied()
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="editor", display_name="Actor")
    with pytest.raises(AccessDenied):
        repository.authorize(principal, grant.file_id, "read")
    session.add.assert_not_called()


@pytest.mark.parametrize("action", ["delete", "", None, True])
def test_authorize_invalid_action_rejected_before_database_access(action: object) -> None:
    from enterprise_platform.application.contracts import Principal

    repository, session, sources, grant, _, _, _ = setup()
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="owner", display_name="Actor")
    with pytest.raises(AccessDenied):
        repository.authorize(principal, grant.file_id, action)
    session.scalar.assert_not_called()
    sources.require.assert_not_called()


def test_authorize_queries_are_scoped_to_server_principal() -> None:
    from enterprise_platform.application.contracts import Principal

    repository, session, _, grant, original, _, head = setup()
    document = encode_office_record(original)
    session.scalar.side_effect = [
        head,
        OfficeGrantRow(can_read=True, can_edit=False),
        OfficeRevisionRow(document_json=document, document_hash=hashlib.sha256(document.encode()).hexdigest()),
    ]
    principal = Principal(workspace_id="workspace", actor_id="actor", workspace_role="normal", display_name="Actor")
    actual = repository.authorize(principal, grant.file_id, "read")
    assert actual.action == "read"
    assert session.scalar.call_count == 3
    for call in session.scalar.call_args_list:
        params = call.args[0].compile().params
        assert params["workspace_id_1"] == principal.workspace_id
        assert params["file_id_1"] == str(grant.file_id)
    assert session.scalar.call_args_list[1].args[0].compile().params["actor_id_1"] == principal.actor_id
