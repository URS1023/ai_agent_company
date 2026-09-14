"""PostgreSQL ledger contention and rollback checks in explicitly enabled disposable schemas."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from database_environment import database_tests_enabled
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_repository import repository as repository

from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.workbench_branches import create_root_branch
from enterprise_platform.application.workbench_messages import (
    ChatSendIntent,
    MessageScope,
    NativeGenerationTerminal,
    NativeMessageReceipt,
    create_message_intent,
)
from enterprise_platform.persistence.migrate_chat_branches import chat_branches_statements, read_chat_branches_sql
from enterprise_platform.persistence.migrate_chat_messages import chat_messages_statements, read_chat_messages_sql
from enterprise_platform.persistence.models import AuditEventRow
from enterprise_platform.persistence.repository import SqlAlchemyRepository
from enterprise_platform.persistence.workbench_branch_repository import SqlAlchemyBranchRepository
from enterprise_platform.persistence.workbench_branches import BranchContextBase, BranchContextRow, branch_context_row
from enterprise_platform.persistence.workbench_messages import MessageIntentBase, MessageIntentRow
from enterprise_platform.persistence.workbench_repository import SqlAlchemyMessageIntentRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
    ),
    pytest.mark.parametrize("repository", ["postgresql"], indirect=True),
]


@pytest.fixture(params=["model", "migration-ddl"])
def messages(repository: SqlAlchemyRepository, request: pytest.FixtureRequest) -> SqlAlchemyMessageIntentRepository:
    bind = repository._sessions.kw["bind"]
    assert bind.dialect.name == "postgresql"
    if request.param == "model":
        MessageIntentBase.metadata.create_all(bind)
        BranchContextBase.metadata.create_all(bind)
    else:
        read_chat_messages_sql()
        read_chat_branches_sql()
        schema = bind.get_execution_options()["schema_translate_map"][None]
        assert schema.startswith("enterprise_test_")
        assert len(schema.removeprefix("enterprise_test_")) == 32
        assert all(character in "0123456789abcdef" for character in schema.removeprefix("enterprise_test_"))
        quoted_schema = bind.dialect.identifier_preparer.quote_schema(schema)
        with bind.begin() as connection:
            for statement in (*chat_messages_statements(), *chat_branches_statements()):
                # Raw DDL ignores schema_translate_map; never touch the shared public schema.
                connection.exec_driver_sql(statement.replace("public.", f"{quoted_schema}."))
    with repository._sessions.begin() as session:
        session.add(branch_context_row(create_root_branch(message_request().scope)))
    return SqlAlchemyMessageIntentRepository(repository._sessions)


def message_request(query: str = "Analyze equipment") -> ChatSendIntent:
    scope = MessageScope(workspace_id="w", actor_id="actor", installed_app_id=UUID(int=1), branch_id="branch")
    return create_message_intent(scope, UUID(int=2), {"query": query, "inputs": {}})


def counts(repository: SqlAlchemyRepository) -> tuple[int, int]:
    with repository._sessions() as session:
        rows = session.scalar(select(func.count()).select_from(MessageIntentRow))
        audits = session.scalar(
            select(func.count()).select_from(AuditEventRow).where(AuditEventRow.resource_type == "chat_send")
        )
        assert rows is not None and audits is not None
        return rows, audits


def test_deployed_tables_preserve_branch_constraints_and_nullable_occupancy(messages, repository):
    bind = repository._sessions.kw["bind"]
    schema = bind.get_execution_options()["schema_translate_map"][None]
    inspector = inspect(bind)
    assert inspector.get_pk_constraint("enterprise_chat_branches", schema=schema)["constrained_columns"] == [
        "workspace_id",
        "actor_id",
        "installed_app_id",
        "branch_id",
    ]
    unique = inspector.get_unique_constraints("enterprise_chat_branches", schema=schema)
    assert any(
        constraint["name"] == "uq_chat_branch_conversation"
        and constraint["column_names"] == ["workspace_id", "installed_app_id", "conversation_id"]
        for constraint in unique
    )
    checks = inspector.get_check_constraints("enterprise_chat_branches", schema=schema)
    assert {constraint["name"] for constraint in checks} == {
        "ck_chat_branch_head",
        "ck_chat_branch_revision",
        "ck_chat_branch_state",
    }
    columns = {column["name"]: column for column in inspector.get_columns("enterprise_chat_branches", schema=schema)}
    assert columns["inflight_client_message_id"]["nullable"] is True
    assert columns["conversation_id"]["nullable"] is True
    assert columns["head_message_id"]["nullable"] is True
    assert counts(repository) == (0, 0)


def test_concurrent_duplicate_creation_has_one_row_and_audit(messages, repository):
    barrier = Barrier(4)
    command = message_request()

    def create() -> ChatSendIntent:
        barrier.wait(timeout=30)
        return messages.create_or_get(command)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(create) for _ in range(4)]
        results = [future.result(timeout=30) for future in futures]

    assert results == [command] * 4
    assert counts(repository) == (1, 1)


def test_unbound_branches_coexist_but_native_conversation_binding_is_unique(messages, repository):
    original = message_request().scope
    second = original.model_copy(update={"branch_id": "second"})
    with repository._sessions.begin() as session:
        session.add(branch_context_row(create_root_branch(second)))
    conversation = UUID(int=10)
    first_bound = create_root_branch(original).model_copy(update={"conversation_id": conversation})
    with repository._sessions.begin() as session:
        session.merge(branch_context_row(first_bound))
    second_bound = create_root_branch(second).model_copy(update={"conversation_id": conversation})
    with pytest.raises(IntegrityError):
        with repository._sessions.begin() as session:
            session.merge(branch_context_row(second_bound))
            session.flush()
    branches = SqlAlchemyBranchRepository(repository._sessions)
    assert branches.get(original).conversation_id == conversation
    assert branches.get(second).conversation_id is None
    assert counts(repository) == (0, 0)


def test_concurrent_claim_has_exactly_one_winner(messages, repository):
    command = messages.create_or_get(message_request())
    barrier = Barrier(4)

    def claim() -> str:
        barrier.wait(timeout=30)
        try:
            messages.claim(command.scope, command.client_message_id, expected_revision=1)
            return "claimed"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(claim) for _ in range(4)]
        results = [future.result(timeout=30) for future in futures]

    assert results.count("claimed") == 1
    assert results.count("conflict") == 3
    assert messages.get(command.scope, command.client_message_id).revision == 2
    assert counts(repository) == (1, 2)


def fail_pending_audit(session: Session, flush_context: object, instances: object) -> None:
    if any(isinstance(row, AuditEventRow) and row.resource_type == "chat_send" for row in session.new):
        session.connection().execute(text("SELECT 1 / 0"))


@pytest.mark.parametrize("operation", ["create", "claim"])
def test_database_failure_rolls_back_intent_and_audit(messages, repository, operation):
    command = message_request()
    if operation == "claim":
        messages.create_or_get(command)
    event.listen(Session, "before_flush", fail_pending_audit)
    try:
        with pytest.raises(PersistenceError):
            if operation == "create":
                messages.create_or_get(command)
            else:
                messages.claim(command.scope, command.client_message_id, expected_revision=1)
    finally:
        event.remove(Session, "before_flush", fail_pending_audit)

    if operation == "create":
        assert counts(repository) == (0, 0)
        with pytest.raises(NotFound):
            messages.get(command.scope, command.client_message_id)
    else:
        assert messages.get(command.scope, command.client_message_id) == command
        assert counts(repository) == (1, 1)
        with repository._sessions() as session:
            stored_branch = session.scalar(select(BranchContextRow))
            assert stored_branch is not None
            assert stored_branch.inflight_client_message_id is None
            assert stored_branch.revision == 1


def test_changed_request_conflicts_without_modifying_original(messages, repository):
    command = messages.create_or_get(message_request())
    with pytest.raises(Conflict):
        messages.create_or_get(message_request("Different query"))
    assert messages.get(command.scope, command.client_message_id) == command
    assert counts(repository) == (1, 1)


def test_uncertain_send_receipt_survives_a_new_repository_instance(messages, repository):
    command = messages.create_or_get(message_request())
    messages.claim(command.scope, command.client_message_id, expected_revision=1)
    messages.mark_uncertain(command.scope, command.client_message_id, expected_revision=2)
    restarted = SqlAlchemyMessageIntentRepository(repository._sessions)
    assert restarted.create_or_get(command).status == "uncertain"
    with pytest.raises(Conflict):
        restarted.claim(command.scope, command.client_message_id, expected_revision=3)
    receipt = NativeMessageReceipt(
        scope=command.scope,
        client_message_id=command.client_message_id,
        conversation_id=UUID(int=3),
        message_id=UUID(int=4),
        task_id="task-1",
    )
    accepted = restarted.acknowledge(receipt, expected_revision=3)
    assert messages.create_or_get(command) == accepted
    assert restarted.acknowledge(receipt, expected_revision=4) == accepted
    assert counts(repository) == (1, 4)


def test_foreign_user_or_branch_cannot_read_another_intent(messages):
    command = messages.create_or_get(message_request())
    for update in ({"actor_id": "other"}, {"branch_id": "other"}, {"workspace_id": "other"}):
        foreign = MessageScope.model_validate({**command.scope.model_dump(), **update})
        with pytest.raises(NotFound):
            messages.get(foreign, command.client_message_id)


def test_different_client_ids_on_same_branch_cannot_claim_concurrently(messages, repository):
    first = messages.create_or_get(message_request())
    second = messages.create_or_get(create_message_intent(first.scope, UUID(int=9), {"query": "Next", "inputs": {}}))
    barrier = Barrier(2)

    def claim(command):
        barrier.wait(timeout=30)
        try:
            return messages.claim(command.scope, command.client_message_id, expected_revision=1)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, command) for command in (first, second)]
        results = [future.result(timeout=30) for future in futures]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    with repository._sessions() as session:
        stored_branch = session.scalar(select(BranchContextRow))
        assert stored_branch is not None
        assert stored_branch.inflight_client_message_id == str(winners[0].client_message_id)
        assert stored_branch.revision == 2
    assert sorted(messages.get(command.scope, command.client_message_id).status for command in (first, second)) == [
        "dispatched",
        "queued",
    ]
    assert counts(repository) == (2, 3)


def acknowledged_generation(messages):
    command = messages.create_or_get(message_request())
    messages.claim(command.scope, command.client_message_id, expected_revision=1)
    receipt = NativeMessageReceipt(
        scope=command.scope,
        client_message_id=command.client_message_id,
        conversation_id=UUID(int=3),
        message_id=UUID(int=4),
        task_id="task-1",
    )
    accepted = messages.acknowledge(receipt, expected_revision=2)
    return accepted, NativeGenerationTerminal(receipt=receipt, outcome="succeeded")


@pytest.mark.parametrize("outcome", ["succeeded", "partial-succeeded", "failed", "stopped"])
def test_terminal_advances_head_and_old_receipt_cannot_release_next_message(messages, repository, outcome):
    accepted, terminal = acknowledged_generation(messages)
    terminal = NativeGenerationTerminal(receipt=terminal.receipt, outcome=outcome)
    finished = messages.finish_generation(terminal, expected_revision=3)
    branches = SqlAlchemyBranchRepository(repository._sessions)
    branch = branches.get(accepted.scope)
    assert branch.revision == 3
    assert branch.inflight_client_message_id is None
    assert branch.conversation_id == terminal.receipt.conversation_id
    assert branch.head_message_id == terminal.receipt.message_id
    assert finished.terminal == terminal
    assert counts(repository) == (1, 4)
    next_request = create_message_intent(
        accepted.scope,
        UUID(int=9),
        {
            "query": "Next",
            "inputs": {},
            "conversation_id": str(branch.conversation_id),
            "parent_message_id": str(branch.head_message_id),
        },
    )
    messages.create_or_get(next_request)
    messages.claim(next_request.scope, next_request.client_message_id, expected_revision=1)
    restarted = SqlAlchemyMessageIntentRepository(repository._sessions)
    assert restarted.finish_generation(terminal, expected_revision=3) == finished
    branch = branches.get(accepted.scope)
    assert branch.revision == 4
    assert branch.inflight_client_message_id == next_request.client_message_id
    assert counts(repository) == (2, 6)


def test_terminal_database_failure_rolls_back_head_occupancy_and_receipt(messages, repository):
    accepted, terminal = acknowledged_generation(messages)
    branches = SqlAlchemyBranchRepository(repository._sessions)
    before = branches.get(accepted.scope)
    event.listen(Session, "before_flush", fail_pending_audit)
    try:
        with pytest.raises(PersistenceError):
            messages.finish_generation(terminal, expected_revision=3)
    finally:
        event.remove(Session, "before_flush", fail_pending_audit)
    assert branches.get(accepted.scope) == before
    assert messages.get(accepted.scope, accepted.client_message_id) == accepted
    assert counts(repository) == (1, 3)
    finished = messages.finish_generation(terminal, expected_revision=3)
    assert finished.terminal == terminal
    assert branches.get(accepted.scope).inflight_client_message_id is None
    assert counts(repository) == (1, 4)


def test_concurrent_terminal_duplicates_advance_once(messages, repository):
    accepted, terminal = acknowledged_generation(messages)
    barrier = Barrier(4)

    def finish():
        barrier.wait(timeout=30)
        return messages.finish_generation(terminal, expected_revision=3)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(finish) for _ in range(4)]
        results = [future.result(timeout=30) for future in futures]
    assert all(result == results[0] for result in results)
    assert results[0].terminal == terminal
    branch = SqlAlchemyBranchRepository(repository._sessions).get(accepted.scope)
    assert branch.revision == 3
    assert branch.inflight_client_message_id is None
    assert counts(repository) == (1, 4)


def test_conflicting_terminal_outcome_never_rewrites_committed_receipt(messages, repository):
    accepted, terminal = acknowledged_generation(messages)
    finished = messages.finish_generation(terminal, expected_revision=3)
    with pytest.raises(Conflict):
        messages.finish_generation(
            NativeGenerationTerminal(receipt=terminal.receipt, outcome="failed"), expected_revision=4
        )
    assert messages.get(accepted.scope, accepted.client_message_id) == finished
    assert counts(repository) == (1, 4)
