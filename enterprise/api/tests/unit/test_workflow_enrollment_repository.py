from unittest.mock import MagicMock, create_autospec

import pytest
from pydantic import SecretStr
from sqlalchemy import CursorResult
from sqlalchemy.orm import Session
from test_workflow_enrollment_contracts import (
    APP,
    ENROLLMENT,
    NONCE,
    TOKEN,
    WS,
    advance,
    claimed_token,
    claimed_verify,
    enrollment,
    publication,
    setup,
)

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.workflow_credential_ports import CredentialCipher, SealedCredential
from enterprise_platform.application.workflow_setup_ports import StoredSetup
from enterprise_platform.application.workflow_token_execution import NativeWorkflowTokenOutcome
from enterprise_platform.persistence.workflow_credential_models import WorkflowCredentialRow
from enterprise_platform.persistence.workflow_enrollment_mapping import enrollment_row
from enterprise_platform.persistence.workflow_enrollment_repository import SqlAlchemyWorkflowEnrollmentRepository
from enterprise_platform.persistence.workflow_provisioning import provisioning_row
from enterprise_platform.persistence.workflow_setups import setup_row


def fixture():
    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    changed = create_autospec(CursorResult, instance=True)
    changed.rowcount = 1
    session.execute.return_value = changed
    session.scalar.side_effect = [enrollment_row(claimed_token()), None]
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    cipher = create_autospec(CredentialCipher, instance=True)
    cipher.seal.return_value = SealedCredential("key", "nonce", "ciphertext")
    return SqlAlchemyWorkflowEnrollmentRepository(sessions, cipher), session, sessions, cipher


def issued():
    return NativeWorkflowTokenOutcome(
        state="issued", workspace_id=WS, app_id=APP, token_id=TOKEN, token=SecretStr("app-" + "a" * 24)
    )


def args():
    return dict(issued=issued(), nonce=NONCE, expected_revision=4, actor_id="actor")


def test_store_encrypts_and_finalizes_in_one_transaction():
    repo, session, sessions, cipher = fixture()
    result = repo.store_issued_token(WS, ENROLLMENT, **args())
    assert result.view.state == "token_stored"
    assert result.view.native_token_id == TOKEN
    assert sessions.begin.call_count == 1
    cipher.seal.assert_called_once()
    rows = [call.args[0] for call in session.add.call_args_list]
    encrypted = next(row for row in rows if isinstance(row, WorkflowCredentialRow))
    assert encrypted.ciphertext == "ciphertext"
    assert issued().token.get_secret_value() not in encrypted.public_json
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0])
    assert len(rows) == 3  # Encrypted credential and both audit records.


@pytest.mark.parametrize(
    "field,value,error",
    [("actor_id", "other", AccessDenied), ("nonce", APP, Conflict), ("expected_revision", 3, Conflict)],
)
def test_invalid_claim_never_encrypts_or_writes(field, value, error):
    repo, session, _, cipher = fixture()
    with pytest.raises(error):
        repo.store_issued_token(WS, ENROLLMENT, **(args() | {field: value}))
    cipher.seal.assert_not_called()
    session.add.assert_not_called()


def test_existing_vault_reference_is_not_adopted():
    repo, session, _, cipher = fixture()
    session.scalar.side_effect = [enrollment_row(claimed_token()), WorkflowCredentialRow()]
    with pytest.raises(Conflict):
        repo.store_issued_token(WS, ENROLLMENT, **args())
    cipher.seal.assert_not_called()
    session.add.assert_not_called()


def test_cipher_failure_exits_transaction_without_journal_write():
    repo, session, sessions, cipher = fixture()
    cipher.seal.side_effect = Conflict("encryption_failed")
    with pytest.raises(Conflict):
        repo.store_issued_token(WS, ENROLLMENT, **args())
    session.add.assert_not_called()
    session.execute.assert_not_called()
    assert sessions.begin.return_value.__exit__.call_args.args[0] is Conflict


def test_compare_and_swap_loss_aborts_vault_transaction():
    repo, session, sessions, _ = fixture()
    session.execute.return_value.rowcount = 0
    with pytest.raises(Conflict):
        repo.store_issued_token(WS, ENROLLMENT, **args())
    assert sessions.begin.call_count == 1
    assert sessions.begin.return_value.__exit__.call_args.args[0] is Conflict
    session.commit.assert_not_called()


def test_audit_failure_aborts_same_transaction():
    repo, session, sessions, _ = fixture()
    session.add.side_effect = [None, Conflict("audit_failed")]
    with pytest.raises(Conflict):
        repo.store_issued_token(WS, ENROLLMENT, **args())
    assert sessions.begin.call_count == 1
    assert sessions.begin.return_value.__exit__.call_args.args[0] is Conflict
    session.commit.assert_not_called()


@pytest.mark.parametrize("field,value", [("workspace_id", APP), ("app_id", WS)])
def test_mismatched_issued_scope_has_no_write(field, value):
    repo, session, _, cipher = fixture()
    bad = issued().model_copy(update={field: value})
    with pytest.raises(Conflict):
        repo.store_issued_token(WS, ENROLLMENT, **(args() | {"issued": bad}))
    cipher.seal.assert_not_called()
    session.add.assert_not_called()


def test_uncertain_token_is_not_stored():
    repo, session, sessions, cipher = fixture()
    uncertain = NativeWorkflowTokenOutcome(state="uncertain", reason_code="lost_response")
    with pytest.raises(Conflict):
        repo.store_issued_token(WS, ENROLLMENT, **(args() | {"issued": uncertain}))
    sessions.begin.assert_not_called()
    cipher.seal.assert_not_called()
    session.add.assert_not_called()


def test_create_pins_completed_provisioning_and_audits():
    repo, session, sessions, _ = fixture()
    session.scalar.side_effect = [
        provisioning_row(advance(4)),
        setup_row(StoredSetup(setup(), "actor", "key", "a" * 64)),
        None,
    ]
    assert repo.create(enrollment(), actor_id="actor") == enrollment()
    assert session.add.call_count == 2
    assert sessions.begin.call_count == 1


def test_get_and_find_use_exact_scope():
    repo, session, _, _ = fixture()
    session.scalar.side_effect = [enrollment_row(enrollment()), enrollment_row(enrollment()), None]
    assert repo.get(WS, ENROLLMENT) == enrollment()
    assert repo.find_provisioning(WS, enrollment().view.provisioning.id) == enrollment()
    assert repo.find_provisioning(WS, enrollment().view.provisioning.id) is None


def test_claim_validates_live_dependencies_before_committing():
    repo, session, sessions, _ = fixture()
    session.scalar.side_effect = [
        enrollment_row(enrollment()),
        provisioning_row(advance(4)),
        setup_row(StoredSetup(setup(), "actor", "key", "a" * 64)),
    ]
    result = repo.claim(WS, ENROLLMENT, phase="verify_publication", nonce=NONCE, expected_revision=1, actor_id="actor")
    assert result.view.state == "verification_claimed"
    assert sessions.begin.call_count == 1
    assert "enterprise_devices" in str(session.execute.call_args_list[0].args[0])


def test_finish_verification_and_failure_need_no_live_device():
    repo, session, _, cipher = fixture()
    session.scalar.side_effect = [enrollment_row(claimed_verify()), enrollment_row(claimed_token())]
    result = repo.finish_verification(
        WS, ENROLLMENT, publication=publication(), nonce=NONCE, expected_revision=2, actor_id="actor"
    )
    assert result.view.state == "verified"
    failed = repo.finish_token_failure(
        WS,
        ENROLLMENT,
        state="uncertain",
        reason_code="lost_response",
        nonce=NONCE,
        expected_revision=4,
        actor_id="actor",
    )
    assert failed.view.state == "uncertain"
    cipher.seal.assert_not_called()
    assert session.scalar.call_count == 2


def test_create_replay_returns_original_identity_without_insert():
    repo, session, _, _ = fixture()
    session.scalar.side_effect = [
        provisioning_row(advance(4)),
        setup_row(StoredSetup(setup(), "actor", "key", "a" * 64)),
        enrollment_row(claimed_token()),
    ]
    assert repo.create(enrollment(), actor_id="actor") == claimed_token()
    session.add.assert_not_called()


def test_changed_provisioning_blocks_creation():
    repo, session, _, _ = fixture()
    session.scalar.side_effect = [provisioning_row(advance(3))]
    with pytest.raises(Conflict):
        repo.create(enrollment(), actor_id="actor")
    session.add.assert_not_called()


def test_changed_setup_blocks_claim_without_journal_write():
    repo, session, _, _ = fixture()
    changed = setup().model_copy(update={"revision": 99})
    session.scalar.side_effect = [
        enrollment_row(enrollment()),
        provisioning_row(advance(4)),
        setup_row(StoredSetup(changed, "actor", "key", "a" * 64)),
    ]
    with pytest.raises(Conflict):
        repo.claim(WS, ENROLLMENT, phase="verify_publication", nonce=NONCE, expected_revision=1, actor_id="actor")
    session.add.assert_not_called()
    assert session.execute.call_count == 1  # Device lock only, no enrollment UPDATE.


def test_wrong_actor_creation_does_not_enter_transaction():
    repo, _, sessions, _ = fixture()
    with pytest.raises(Conflict):
        repo.create(enrollment(), actor_id="other")
    sessions.begin.assert_not_called()


def test_claim_cannot_take_over_existing_claim():
    repo, session, _, _ = fixture()
    session.scalar.side_effect = [enrollment_row(claimed_verify())]
    with pytest.raises(Conflict):
        repo.claim(WS, ENROLLMENT, phase="verify_publication", nonce=APP, expected_revision=2, actor_id="actor")
    session.execute.assert_not_called()
    session.add.assert_not_called()
