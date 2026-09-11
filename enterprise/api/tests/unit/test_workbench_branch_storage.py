from hashlib import sha256
from importlib import import_module
from uuid import UUID

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from test_workbench_messages import scope

from enterprise_platform.application.contracts import canonical_json
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workbench_branches import BranchContext, bind_fork_context, fork_branch


@pytest.fixture
def storage():
    return import_module("enterprise_platform.persistence.workbench_branches")


def branch():
    return BranchContext(scope=scope(), state="ready", conversation_id=UUID(int=3), head_message_id=UUID(int=4))


def test_database_keys_isolate_branch_and_prevent_shared_native_conversation(storage):
    table = storage.BranchContextRow.__table__
    assert list(table.primary_key.columns.keys()) == ["workspace_id", "actor_id", "installed_app_id", "branch_id"]
    unique = [list(item.columns.keys()) for item in table.constraints if isinstance(item, UniqueConstraint)]
    assert unique == [["workspace_id", "installed_app_id", "conversation_id"]]
    assert table.c.conversation_id.nullable
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "CHECK (revision > 0)" in sql
    assert "head_message_id IS NULL OR conversation_id IS NOT NULL" in sql


def test_fork_storage_roundtrip_preserves_origin_and_is_detached(storage):
    fork = bind_fork_context(fork_branch(branch(), "fork", UUID(int=4)), UUID(int=5), UUID(int=6))
    row = storage.branch_context_row(fork)
    loaded = storage.as_branch_context(row, fork.scope)
    assert loaded == fork and loaded is not fork
    assert loaded.origin.conversation_id == UUID(int=3)
    assert row.conversation_id == str(UUID(int=5))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "foreign"),
        ("actor_id", "foreign"),
        ("installed_app_id", str(UUID(int=9))),
        ("branch_id", "foreign"),
        ("revision", 8),
        ("state", "archived"),
        ("conversation_id", str(UUID(int=8))),
        ("head_message_id", None),
        ("document_hash", "0" * 64),
    ],
)
def test_corrupt_indexes_or_hash_rejected(storage, field, value):
    row = storage.branch_context_row(branch())
    setattr(row, field, value)
    with pytest.raises(PersistenceError, match="^branch_context_storage_invalid$"):
        storage.as_branch_context(row, scope())


def test_caller_scope_must_match_even_with_valid_document(storage):
    with pytest.raises(PersistenceError):
        storage.as_branch_context(
            storage.branch_context_row(branch()), scope().model_copy(update={"actor_id": "other"})
        )


def test_valid_hash_does_not_allow_invalid_preparing_state(storage):
    row = storage.branch_context_row(branch())
    row.state = "preparing"
    row.document_json = canonical_json({**branch().model_dump(mode="json"), "state": "preparing"})
    row.document_hash = sha256(row.document_json.encode()).hexdigest()
    with pytest.raises(PersistenceError):
        storage.as_branch_context(row, scope())


def test_noncanonical_and_oversized_documents_rejected(storage):
    for document in [branch().model_dump_json(indent=2), " " * 65537]:
        row = storage.branch_context_row(branch())
        row.document_json = document
        row.document_hash = sha256(document.encode()).hexdigest()
        with pytest.raises(PersistenceError):
            storage.as_branch_context(row, scope())
