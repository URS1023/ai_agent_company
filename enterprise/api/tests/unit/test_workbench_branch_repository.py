from importlib import import_module
from unittest.mock import MagicMock, create_autospec
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from test_workbench_branch_storage import branch
from test_workbench_messages import scope

from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.application.workbench_branches import bind_fork_context, create_root_branch, fork_branch
from enterprise_platform.persistence.workbench_branches import branch_context_row


@pytest.fixture
def setup():
    module = import_module("enterprise_platform.persistence.workbench_branch_repository")
    sessions = MagicMock(spec=sessionmaker)
    session = create_autospec(Session, instance=True)
    sessions.begin.return_value.__enter__.return_value = session
    return module.SqlAlchemyBranchRepository(sessions), session, sessions


def test_create_root_is_atomic_and_scoped(setup):
    repo, session, sessions = setup
    root = create_root_branch(scope())
    session.scalar.side_effect = [root.scope.branch_id, branch_context_row(root)]
    assert repo.create_root(scope()) == root
    sql = str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (workspace_id, actor_id, installed_app_id, branch_id) DO NOTHING" in sql
    session.add.assert_called_once()
    sessions.begin.return_value.__exit__.assert_called_once()


def test_duplicate_root_returns_current_state_without_reset_or_audit(setup):
    repo, session, _ = setup
    session.scalar.side_effect = [None, branch_context_row(branch())]
    assert repo.create_root(scope()) == branch()
    session.add.assert_not_called()


def test_get_scopes_all_identity_dimensions(setup):
    repo, session, _ = setup
    session.scalar.return_value = branch_context_row(branch())
    assert repo.get(scope()) == branch()
    params = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()).params
    assert set(params.values()) == {"w", "actor", str(UUID(int=1)), "branch"}


def test_missing_get_is_not_found(setup):
    repo, session, _ = setup
    session.scalar.return_value = None
    with pytest.raises(NotFound):
        repo.get(scope())


def test_fork_locks_and_checks_parent_before_creating_pending_child(setup):
    repo, session, _ = setup
    child = fork_branch(branch(), "child", UUID(int=4))
    session.scalar.side_effect = [branch_context_row(branch()), "child", branch_context_row(child)]
    assert repo.fork(scope(), "child", UUID(int=4), expected_revision=1) == child
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    session.add.assert_called_once()


def test_stale_parent_prevents_fork_insert(setup):
    repo, session, _ = setup
    session.scalar.return_value = branch_context_row(branch())
    with pytest.raises(Conflict):
        repo.fork(scope(), "child", UUID(int=4), expected_revision=2)
    assert session.scalar.call_count == 1
    session.add.assert_not_called()


def test_bind_fork_updates_all_snapshot_indexes_and_audit_in_transaction(setup):
    repo, session, sessions = setup
    child = fork_branch(branch(), "child", UUID(int=4))
    row = branch_context_row(child)
    session.scalar.return_value = row
    result = repo.bind_fork(child.scope, UUID(int=5), UUID(int=6), expected_revision=1)
    assert result == bind_fork_context(child, UUID(int=5), UUID(int=6))
    assert row.conversation_id == str(UUID(int=5)) and row.head_message_id == str(UUID(int=6))
    assert row.document_json == branch_context_row(result).document_json
    session.flush.assert_called_once()
    session.add.assert_called_once()
    sessions.begin.return_value.__exit__.assert_called_once()


def test_commit_failure_never_returns_success(setup):
    repo, session, sessions = setup
    root = create_root_branch(scope())
    session.scalar.side_effect = [root.scope.branch_id, branch_context_row(root)]
    sessions.begin.return_value.__exit__.side_effect = SQLAlchemyError("private database details")
    with pytest.raises(PersistenceError, match="^persistence_error$"):
        repo.create_root(scope())


def test_duplicate_branch_with_different_fork_origin_is_rejected(setup):
    repo, session, _ = setup
    existing = fork_branch(branch(), "child", UUID(int=8))
    session.scalar.side_effect = [branch_context_row(branch()), None, branch_context_row(existing)]
    with pytest.raises(Conflict):
        repo.fork(scope(), "child", UUID(int=4), expected_revision=1)
    session.add.assert_not_called()


def test_database_conversation_uniqueness_failure_is_opaque(setup):
    repo, session, _ = setup
    child = fork_branch(branch(), "child", UUID(int=4))
    session.scalar.return_value = branch_context_row(child)
    session.flush.side_effect = IntegrityError("private statement", {}, Exception("private details"))
    with pytest.raises(Conflict, match="^database_constraint_conflict$"):
        repo.bind_fork(child.scope, UUID(int=5), UUID(int=6), expected_revision=1)


@pytest.mark.parametrize("revision", [0, -1, True])
def test_invalid_revision_rejected_before_transaction(setup, revision):
    repo, _, sessions = setup
    with pytest.raises(InvalidInput):
        repo.fork(scope(), "child", UUID(int=4), expected_revision=revision)
    sessions.begin.assert_not_called()


def test_duplicate_fork_returns_bound_child_without_resetting_it(setup):
    repo, session, _ = setup
    child = bind_fork_context(fork_branch(branch(), "child", UUID(int=4)), UUID(int=5), UUID(int=6))
    session.scalar.side_effect = [branch_context_row(branch()), None, branch_context_row(child)]
    assert repo.fork(scope(), "child", UUID(int=4), expected_revision=1) == child
    session.add.assert_not_called()
