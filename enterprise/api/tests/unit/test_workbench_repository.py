from unittest.mock import DEFAULT, MagicMock, create_autospec
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from test_workbench_messages import intent, receipt

from enterprise_platform.application.errors import Conflict, InvalidState, NotFound, PersistenceError
from enterprise_platform.application.workbench_branches import create_root_branch
from enterprise_platform.application.workbench_messages import acknowledge_message, claim_message
from enterprise_platform.persistence.workbench_branches import BranchContextRow, branch_context_row
from enterprise_platform.persistence.workbench_messages import message_intent_row
from enterprise_platform.persistence.workbench_repository import SqlAlchemyMessageIntentRepository


def setup(branch_row=None):
    sessions = MagicMock(spec=sessionmaker)
    session = create_autospec(Session, instance=True)
    sessions.begin.return_value.__enter__.return_value = session
    root = branch_row if branch_row is not None else branch_context_row(create_root_branch(intent().scope))

    def read(statement):
        descriptions = getattr(statement, "column_descriptions", [])
        if descriptions and descriptions[0].get("entity") is BranchContextRow:
            return root
        return DEFAULT

    session.scalar.side_effect = read
    return SqlAlchemyMessageIntentRepository(sessions), session, sessions


def test_create_uses_scoped_on_conflict_and_metadata_only_audit() -> None:
    repo, session, sessions = setup()
    saved = intent()
    session.scalar.side_effect = [str(saved.client_message_id), message_intent_row(saved)]
    assert repo.create_or_get(saved) == saved
    sql = str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (workspace_id, actor_id, installed_app_id, branch_id, client_message_id) DO NOTHING" in sql
    assert "FOR UPDATE" in str(session.scalar.call_args_list[1].args[0].compile(dialect=postgresql.dialect()))
    session.add.assert_called_once()
    assert "设备分析" not in session.add.call_args.args[0].detail_json
    sessions.begin.return_value.__exit__.assert_called_once()


def test_duplicate_returns_current_acknowledgement_without_a_new_audit() -> None:
    repo, session, _ = setup()
    saved = acknowledge_message(claim_message(intent()), receipt())
    session.scalar.side_effect = [None, message_intent_row(saved)]
    assert repo.create_or_get(intent()) == saved
    session.add.assert_not_called()


def test_duplicate_with_changed_payload_conflicts() -> None:
    repo, session, _ = setup()
    session.scalar.side_effect = [None, message_intent_row(intent())]
    requested = intent().model_copy(update={"payload_json": '{"query":"different"}'})
    with pytest.raises(Conflict):
        repo.create_or_get(requested)
    session.add.assert_not_called()


def test_claim_locks_checks_revision_and_updates_snapshot() -> None:
    repo, session, sessions = setup()
    saved = intent()
    row = message_intent_row(saved)
    session.scalar.return_value = row
    result = repo.claim(saved.scope, saved.client_message_id, expected_revision=1)
    assert result.status == "dispatched"
    assert row.revision == 2
    assert row.document_json == message_intent_row(result).document_json
    assert "FOR UPDATE" in str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    session.flush.assert_called_once()
    sessions.begin.return_value.__exit__.assert_called_once()


def test_stale_claim_and_already_dispatched_state_cannot_send_again() -> None:
    repo, session, _ = setup()
    saved = claim_message(intent())
    session.scalar.return_value = message_intent_row(saved)
    with pytest.raises(Conflict):
        repo.claim(saved.scope, saved.client_message_id, expected_revision=1)
    with pytest.raises(InvalidState):
        repo.claim(saved.scope, saved.client_message_id, expected_revision=2)
    session.flush.assert_not_called()


def test_missing_scoped_record_is_not_found() -> None:
    repo, session, _ = setup()
    saved = intent()
    session.scalar.return_value = None
    with pytest.raises(NotFound):
        repo.get(saved.scope, saved.client_message_id)


def test_commit_failure_does_not_return_a_successful_claim() -> None:
    repo, session, sessions = setup()
    saved = intent()
    session.scalar.return_value = message_intent_row(saved)
    sessions.begin.return_value.__exit__.side_effect = SQLAlchemyError("private driver details")
    with pytest.raises(PersistenceError, match="^persistence_error$"):
        repo.claim(saved.scope, saved.client_message_id, expected_revision=1)


def test_uncertainty_and_acknowledgement_use_versioned_transitions() -> None:
    repo, session, _ = setup()
    saved = claim_message(intent())
    row = message_intent_row(saved)
    session.scalar.return_value = row
    uncertain = repo.mark_uncertain(saved.scope, saved.client_message_id, expected_revision=2)
    assert uncertain.status == "uncertain"
    accepted = repo.acknowledge(receipt(), expected_revision=3)
    assert accepted.receipt == receipt()
    assert accepted.revision == 4


def test_create_does_not_accept_already_dispatched_input() -> None:
    repo, session, sessions = setup()
    with pytest.raises(Conflict):
        repo.create_or_get(claim_message(intent()))
    sessions.begin.assert_not_called()
    session.scalar.assert_not_called()


def test_duplicate_acknowledgement_does_not_rewrite_or_duplicate_audit() -> None:
    repo, session, _ = setup()
    saved = acknowledge_message(claim_message(intent()), receipt())
    session.scalar.return_value = message_intent_row(saved)
    assert repo.acknowledge(receipt(), expected_revision=saved.revision) == saved
    session.flush.assert_not_called()
    session.add.assert_not_called()


def test_read_query_includes_every_identity_dimension() -> None:
    repo, session, _ = setup()
    saved = intent()
    session.scalar.return_value = message_intent_row(saved)
    assert repo.get(saved.scope, saved.client_message_id) == saved
    compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert compiled.params == {
        "workspace_id_1": saved.scope.workspace_id,
        "actor_id_1": saved.scope.actor_id,
        "installed_app_id_1": str(saved.scope.installed_app_id),
        "branch_id_1": saved.scope.branch_id,
        "client_message_id_1": str(saved.client_message_id),
    }


def test_claim_reserves_branch_in_same_transaction_as_message():
    root = branch_context_row(create_root_branch(intent().scope))
    repo, session, sessions = setup(root)
    session.scalar.return_value = message_intent_row(intent())
    result = repo.claim(intent().scope, intent().client_message_id, expected_revision=1)
    assert root.revision == 2
    assert root.inflight_client_message_id == str(result.client_message_id)
    assert str(result.client_message_id) in root.document_json
    assert "enterprise_chat_branches" in str(session.scalar.call_args_list[0].args[0])
    sessions.begin.assert_called_once()


def test_other_pending_message_blocks_claim_without_mutating_ledger():
    root = branch_context_row(
        create_root_branch(intent().scope).model_copy(update={"inflight_client_message_id": UUID(int=9)})
    )
    repo, session, _ = setup(root)
    row = message_intent_row(intent())
    session.scalar.return_value = row
    with pytest.raises(Conflict, match="^branch_busy$"):
        repo.claim(intent().scope, intent().client_message_id, expected_revision=1)
    assert row.status == "queued" and row.revision == 1
    session.flush.assert_not_called()


def test_native_acknowledgement_does_not_release_generation_occupancy():
    root = branch_context_row(create_root_branch(intent().scope))
    repo, session, _ = setup(root)
    session.scalar.return_value = message_intent_row(intent())
    repo.claim(intent().scope, intent().client_message_id, expected_revision=1)
    accepted = repo.acknowledge(receipt(), expected_revision=2)
    assert accepted.status == "accepted"
    assert root.inflight_client_message_id == str(intent().client_message_id)
