from importlib import import_module
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_workbench_messages import scope

from enterprise_platform.application.errors import Conflict, InvalidState
from enterprise_platform.application.workbench_messages import create_message_intent


@pytest.fixture
def branches():
    return import_module("enterprise_platform.application.workbench_branches")


def parent(branches):
    return branches.BranchContext(
        scope=scope(), state="ready", conversation_id=UUID(int=3), head_message_id=UUID(int=4)
    )


def test_root_context_accepts_only_empty_native_context(branches):
    root = branches.create_root_branch(scope())
    branches.require_branch_context(root, create_message_intent(scope(), UUID(int=2), {"query": "q"}))
    with pytest.raises(Conflict):
        branches.require_branch_context(
            root, create_message_intent(scope(), UUID(int=2), {"conversation_id": str(UUID(int=3))})
        )


def test_fork_starts_unbound_and_cannot_send(branches):
    original = parent(branches)
    fork = branches.fork_branch(original, "fork", UUID(int=4))
    assert fork.scope.branch_id == "fork" and fork.state == "preparing"
    assert fork.conversation_id is None and fork.head_message_id is None
    assert fork.origin.scope == original.scope
    assert fork.origin.message_id == UUID(int=4)
    assert original == parent(branches)
    with pytest.raises(InvalidState):
        branches.require_branch_context(fork, create_message_intent(fork.scope, UUID(int=5), {}))


def test_rebuilt_fork_requires_different_conversation_and_exact_head(branches):
    fork = branches.fork_branch(parent(branches), "fork", UUID(int=4))
    with pytest.raises(ValidationError):
        branches.bind_fork_context(fork, UUID(int=3), UUID(int=6))
    ready = branches.bind_fork_context(fork, UUID(int=7), UUID(int=6))
    assert ready.revision == fork.revision + 1
    assert ready.state == "ready"
    request = create_message_intent(
        ready.scope, UUID(int=5), {"conversation_id": str(UUID(int=7)), "parent_message_id": str(UUID(int=6))}
    )
    branches.require_branch_context(ready, request)
    with pytest.raises(InvalidState):
        branches.bind_fork_context(ready, UUID(int=8), UUID(int=6))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"conversation_id": str(UUID(int=3))},
        {"conversation_id": str(UUID(int=3)), "parent_message_id": str(UUID(int=9))},
        {"conversation_id": str(UUID(int=8)), "parent_message_id": str(UUID(int=4))},
        {"conversation_id": str(UUID(int=3)), "parent_message_id": False},
    ],
)
def test_send_rejects_missing_stale_or_foreign_context(branches, payload):
    with pytest.raises(Conflict):
        branches.require_branch_context(parent(branches), create_message_intent(scope(), UUID(int=2), payload))


def test_foreign_scope_and_archived_branch_rejected(branches):
    request = create_message_intent(scope(), UUID(int=2), {})
    foreign = parent(branches).model_copy(update={"scope": scope().model_copy(update={"actor_id": "other"})})
    with pytest.raises(Conflict):
        branches.require_branch_context(foreign, request)
    archived = parent(branches).model_copy(update={"state": "archived"})
    with pytest.raises(InvalidState):
        branches.require_branch_context(archived, request)


def test_fork_rejects_self_and_unbound_parent(branches):
    with pytest.raises(ValidationError):
        branches.fork_branch(parent(branches), "branch", UUID(int=4))
    with pytest.raises(InvalidState):
        branches.fork_branch(branches.create_root_branch(scope()), "fork", UUID(int=4))


def test_loaded_fork_cannot_claim_ready_without_independent_context(branches):
    fork = branches.fork_branch(parent(branches), "fork", UUID(int=4))
    with pytest.raises(ValidationError):
        branches.BranchContext.model_validate({**fork.model_dump(), "state": "ready"})


@pytest.mark.parametrize("parent_id", [None, "", str(UUID(int=0))])
def test_root_accepts_native_root_markers(branches, parent_id):
    branches.require_branch_context(
        branches.create_root_branch(scope()),
        create_message_intent(scope(), UUID(int=2), {"parent_message_id": parent_id}),
    )


@pytest.mark.parametrize(
    "field,value", [("actor_id", "other"), ("workspace_id", "other"), ("installed_app_id", UUID(int=9))]
)
def test_loaded_fork_cannot_cross_owner_workspace_or_app(branches, field, value):
    fork = branches.fork_branch(parent(branches), "fork", UUID(int=4))
    document = fork.model_dump()
    document["scope"][field] = value
    with pytest.raises(ValidationError):
        branches.BranchContext.model_validate(document)


@pytest.mark.parametrize("field", ["conversation_id", "head_message_id"])
def test_stored_context_does_not_accept_nil_native_id(branches, field):
    with pytest.raises(ValidationError):
        branches.BranchContext.model_validate({**parent(branches).model_dump(), field: UUID(int=0)})
