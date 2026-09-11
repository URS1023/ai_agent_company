import asyncio
from threading import get_ident
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import scope

from enterprise_platform.application.errors import AccessDenied, PersistenceError
from enterprise_platform.application.workbench_branches import create_root_branch


def setup():
    from enterprise_platform.application.workbench_branch_service import WorkbenchBranchService

    repository = Mock()
    repository.create_root.return_value = create_root_branch(scope())
    repository.get.return_value = create_root_branch(scope())
    access = Mock(check_branch_access=AsyncMock())
    return WorkbenchBranchService(repository, access), repository, access


def run(service, method="create_root"):
    return asyncio.run(getattr(service, method)(PRINCIPAL, SESSION, installed_app_id=UUID(int=1), branch_id="branch"))


def test_native_app_access_precedes_off_loop_root_creation():
    service, repo, access = setup()
    main_thread = get_ident()
    order = []
    access.check_branch_access.side_effect = lambda *args: order.append("access")

    def create(actual_scope):
        assert get_ident() != main_thread and actual_scope == scope()
        order.append("create")
        return create_root_branch(actual_scope)

    repo.create_root.side_effect = create
    assert run(service).scope == scope()
    assert order == ["access", "create"]
    access.check_branch_access.assert_awaited_once_with(PRINCIPAL, SESSION, scope())


@pytest.mark.parametrize("method", ["create_root", "get"])
def test_denied_access_never_reads_or_writes_branch(method):
    service, repo, access = setup()
    access.check_branch_access.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        run(service, method)
    assert repo.mock_calls == []


def test_repeated_root_creation_preserves_head_revision_and_busy_state():
    service, repo, _ = setup()
    existing = create_root_branch(scope()).model_copy(
        update={
            "revision": 8,
            "conversation_id": UUID(int=3),
            "head_message_id": UUID(int=4),
            "inflight_client_message_id": UUID(int=5),
        }
    )
    repo.create_root.return_value = existing
    assert run(service) == existing
    assert run(service) == existing


@pytest.mark.parametrize("method", ["create_root", "get"])
def test_foreign_repository_scope_is_not_returned(method):
    service, repo, _ = setup()
    getattr(repo, method).return_value = create_root_branch(scope().model_copy(update={"actor_id": "other"}))
    with pytest.raises(PersistenceError):
        run(service, method)


def test_read_returns_current_context_without_mutating_it():
    service, repo, _ = setup()
    assert run(service, "get") == repo.get.return_value
    repo.create_root.assert_not_called()
