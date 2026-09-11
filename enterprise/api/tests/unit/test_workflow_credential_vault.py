import base64
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from enterprise_platform.adapters.workflow_credential_encryption import (
    AesGcmCredentialCipher,
    CredentialKey,
    CredentialKeyring,
)
from enterprise_platform.application.errors import DependencyUnavailable, PersistenceError
from enterprise_platform.application.workflow_credential_contracts import CredentialView, WorkflowCredential


def cipher(key_id="one", material=b"a" * 32):
    return AesGcmCredentialCipher(
        CredentialKeyring(
            active_key_id=key_id,
            keys=(CredentialKey(key_id=key_id, key=SecretStr(base64.urlsafe_b64encode(material).decode())),),
        )
    )


def view():
    return CredentialView(
        workspace_id="workspace",
        app_id="app",
        secret_ref="ref",
        revision=1,
        active=True,
        created_at=datetime(2026, 9, 9, tzinfo=UTC),
        updated_at=datetime(2026, 9, 9, tzinfo=UTC),
    )


def test_roundtrip_randomized_and_no_secret_serialization():
    token = SecretStr("native-secret-token")
    first = cipher().seal(view(), token)
    assert cipher().open(view(), first) == token
    assert first != cipher().seal(view(), token)
    credential = WorkflowCredential(workspace_id="workspace", app_id="app", secret_ref="ref", api_key=token)
    assert token.get_secret_value() not in repr(credential) + credential.model_dump_json() + repr(first)


@pytest.mark.parametrize(
    "change",
    [{"workspace_id": "other"}, {"app_id": "other"}, {"secret_ref": "other"}, {"revision": 2}, {"active": False}],
)
def test_aad_substitution(change):
    sealed = cipher().seal(view(), SecretStr("token"))
    with pytest.raises(PersistenceError):
        cipher().open(CredentialView.model_validate(view().model_dump() | change), sealed)


def test_tamper_and_unavailable_key():
    sealed = cipher().seal(view(), SecretStr("token"))
    with pytest.raises(PersistenceError):
        cipher().open(view(), replace(sealed, ciphertext="A" + sealed.ciphertext[1:-1] + "B"))
    with pytest.raises(DependencyUnavailable):
        cipher("two").open(view(), sealed)


def repository_with(row):
    from unittest.mock import MagicMock, create_autospec

    from sqlalchemy import CursorResult
    from sqlalchemy.orm import Session

    from enterprise_platform.persistence.workflow_credentials import SqlAlchemyWorkflowCredentialVault

    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    session.scalar.return_value = row
    result = create_autospec(CursorResult, instance=True)
    result.rowcount = 1
    session.execute.return_value = result
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    return SqlAlchemyWorkflowCredentialVault(sessions, cipher()), session, sessions


def credential():
    return WorkflowCredential(workspace_id="workspace", app_id="app", secret_ref="ref", api_key=SecretStr("token"))


def stored_row():
    from enterprise_platform.persistence.workflow_credentials import credential_row

    return credential_row(view(), cipher().seal(view(), SecretStr("token")))


def test_register_atomic_replay_and_conflict():
    from enterprise_platform.application.errors import Conflict
    from enterprise_platform.persistence.models import AuditEventRow
    from enterprise_platform.persistence.workflow_credential_models import WorkflowCredentialRow

    repo, session, sessions = repository_with(None)
    result = repo.register(credential(), actor_id="actor")
    assert result.revision == 1 and result.active
    assert [type(call.args[0]) for call in session.add.call_args_list] == [WorkflowCredentialRow, AuditEventRow]
    assert "token" not in str(session.add.call_args_list[0].args[0].__dict__)
    session.reset_mock()
    session.scalar.return_value = stored_row()
    assert repo.register(credential(), actor_id="actor") == view()
    session.add.assert_not_called()
    with pytest.raises(Conflict):
        repo.register(
            WorkflowCredential(
                workspace_id="workspace", app_id="app", secret_ref="ref", api_key=SecretStr("different")
            ),
            actor_id="actor",
        )


def test_resolve_scope_corruption_missing_and_revoke():
    from enterprise_platform.application.errors import Conflict, NotFound

    repo, session, _ = repository_with(stored_row())
    assert repo.resolve("workspace", "app", "ref") == SecretStr("token")
    with pytest.raises(PersistenceError):
        repo.resolve("other", "app", "ref")
    updated = repo.revoke("workspace", "app", "ref", expected_revision=1, actor_id="actor")
    assert not updated.active and updated.revision == 2
    from enterprise_platform.persistence.workflow_credentials import credential_row

    session.scalar.return_value = credential_row(updated, cipher().seal(updated, SecretStr("token")))
    with pytest.raises(Conflict, match="workflow_credential_revoked"):
        repo.resolve("workspace", "app", "ref")
    with pytest.raises(Conflict):
        repo.register(credential(), actor_id="actor")
    session.scalar.return_value = None
    with pytest.raises(NotFound):
        repo.resolve("workspace", "app", "ref")


def test_reseal_cas_and_no_audit_on_lost_revision():
    from enterprise_platform.application.errors import Conflict

    repo, session, sessions = repository_with(stored_row())
    updated = repo.reseal("workspace", "app", "ref", expected_revision=1, actor_id="actor")
    assert updated.active and updated.revision == 2
    assert session.execute.call_count == 1
    assert all(
        name in str(session.execute.call_args.args[0]).split("WHERE")[1]
        for name in ("workspace_id", "app_id", "secret_ref", "revision", "active")
    )
    session.reset_mock()
    session.execute.return_value.rowcount = 0
    with pytest.raises(Conflict):
        repo.reseal("workspace", "app", "ref", expected_revision=1, actor_id="actor")
    session.add.assert_not_called()
    assert sessions.begin.return_value.__exit__.call_args.args[0] is Conflict


def test_duplicate_insert_race_rolls_back_and_replays_exact_token_once():
    from sqlalchemy.exc import IntegrityError

    repo, session, sessions = repository_with(None)
    session.scalar.side_effect = [None, stored_row()]
    session.flush.side_effect = IntegrityError("private", {}, Exception("private"))
    assert repo.register(credential(), actor_id="actor") == view()
    assert sessions.begin.call_count == 2 and session.add.call_count == 1
    assert sessions.begin.return_value.__exit__.call_args_list[0].args[0] is IntegrityError


@pytest.mark.parametrize(
    "name,value",
    [
        ("workspace_id", "other"),
        ("app_id", "other"),
        ("secret_ref", "other"),
        ("revision", 2),
        ("active", False),
        ("public_json", "{}"),
    ],
)
def test_public_index_substitution(name, value):
    row = stored_row()
    setattr(row, name, value)
    repo, _, _ = repository_with(row)
    with pytest.raises(PersistenceError):
        repo.resolve("workspace", "app", "ref")


def test_boundary_validation_before_session_and_plaintext_validation():
    from pydantic import ValidationError

    from enterprise_platform.application.errors import InvalidInput

    repo, _, sessions = repository_with(None)
    with pytest.raises(InvalidInput):
        repo.resolve("", "app", "ref")
    sessions.begin.assert_not_called()
    for raw in ("", "with space", "a\n", "é", "a" * 4097):
        with pytest.raises(ValidationError):
            WorkflowCredential(workspace_id="workspace", app_id="app", secret_ref="ref", api_key=SecretStr(raw))


def test_key_id_binding_and_rotation_preserves_token_not_revocation():
    from enterprise_platform.persistence.workflow_credentials import SqlAlchemyWorkflowCredentialVault

    first = CredentialKey(key_id="one", key=SecretStr(base64.urlsafe_b64encode(b"a" * 32).decode()))
    second = CredentialKey(key_id="two", key=SecretStr(base64.urlsafe_b64encode(b"b" * 32).decode()))
    rotating = AesGcmCredentialCipher(CredentialKeyring(active_key_id="two", keys=(first, second)))
    sealed = cipher().seal(view(), SecretStr("token"))
    with pytest.raises(PersistenceError):
        rotating.open(view(), replace(sealed, key_id="two"))
    _, session, sessions = repository_with(stored_row())
    repo = SqlAlchemyWorkflowCredentialVault(sessions, rotating)
    result = repo.reseal("workspace", "app", "ref", expected_revision=1, actor_id="actor")
    values = session.execute.call_args.args[0].compile().params
    from enterprise_platform.application.workflow_credential_ports import SealedCredential

    rotated = SealedCredential(values["key_id"], values["nonce"], values["ciphertext"])
    assert cipher("two", b"b" * 32).open(result, rotated) == SecretStr("token")
    assert rotated.key_id == "two"
    from enterprise_platform.persistence.workflow_credentials import credential_row

    revoked = CredentialView.model_validate(view().model_dump() | {"active": False})
    session.scalar.return_value = credential_row(revoked, cipher().seal(revoked, SecretStr("token")))
    assert not repo.reseal("workspace", "app", "ref", expected_revision=1, actor_id="actor").active


def test_credential_schema_is_separate_with_complete_composite_key():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from enterprise_platform.persistence.workflow_credential_models import CredentialBase

    assert set(CredentialBase.metadata.tables) == {"enterprise_workflow_credentials"}
    ddl = str(CreateTable(next(iter(CredentialBase.metadata.tables.values()))).compile(dialect=postgresql.dialect()))
    assert "PRIMARY KEY (workspace_id, app_id, secret_ref)" in ddl
    assert "revision > 0" in ddl and "ciphertext" in ddl


def test_invalid_snapshot_has_credential_specific_error_code():
    row = stored_row()
    row.public_json = "{}"
    repo, _, _ = repository_with(row)
    with pytest.raises(PersistenceError, match="^workflow_credential_corrupt$"):
        repo.resolve("workspace", "app", "ref")
