import asyncio
from threading import get_ident
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from test_workbench_context_client import PRINCIPAL, SESSION, response_body
from test_workbench_messages import scope

from enterprise_platform.adapters.workbench_context_client import DifyWorkbenchContextClient
from enterprise_platform.application.errors import AccessDenied, PersistenceError
from enterprise_platform.application.workbench_branch_listing import BranchListQuery, BranchPage
from enterprise_platform.application.workbench_branch_service import WorkbenchBranchService
from enterprise_platform.application.workbench_branches import create_root_branch


def setup():
    repository = Mock(list_branches=Mock(return_value=BranchPage(items=(create_root_branch(scope()),))))
    access = Mock(check_listing_access=AsyncMock())
    return WorkbenchBranchService(repository, access), repository, access


def run(service, **options):
    return asyncio.run(service.list_branches(PRINCIPAL, SESSION, installed_app_id=UUID(int=1), **options))


def test_access_precedes_off_loop_listing_without_synthetic_branch_identity():
    service, repository, access = setup()
    order = []
    main_thread = get_ident()
    access.check_listing_access.side_effect = lambda *args: order.append("access")

    def read(query):
        assert get_ident() != main_thread
        assert query.workspace_id == PRINCIPAL.workspace_id and query.actor_id == PRINCIPAL.actor_id
        assert not hasattr(query, "branch_id")
        order.append("read")
        return BranchPage(items=(create_root_branch(scope()),))

    repository.list_branches.side_effect = read
    assert len(run(service).items) == 1
    assert order == ["access", "read"]


def test_denied_app_access_does_not_touch_directory():
    service, repository, access = setup()
    access.check_listing_access.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        run(service)
    assert repository.mock_calls == []


def test_foreign_page_and_invalid_continuation_are_rejected():
    service, repository, _ = setup()
    foreign = create_root_branch(scope().model_copy(update={"actor_id": "other"}))
    repository.list_branches.return_value = BranchPage(items=(foreign,))
    with pytest.raises(PersistenceError):
        run(service)
    repository.list_branches.return_value = BranchPage(items=(), next_after="invented")
    with pytest.raises(PersistenceError):
        run(service)


def test_listing_access_uses_native_context_check_without_sending_cursor_or_fake_branch():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response_body())

    client = DifyWorkbenchContextClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handle)
    )
    query = BranchListQuery(
        workspace_id=PRINCIPAL.workspace_id, actor_id=PRINCIPAL.actor_id, installed_app_id=UUID(int=1), after="cursor"
    )
    asyncio.run(client.check_listing_access(PRINCIPAL, SESSION, query))
    assert len(requests) == 1 and requests[0].content == b"{}"
    assert requests[0].headers["X-Enterprise-Expected-Actor"] == PRINCIPAL.actor_id
    assert "refresh_token" not in requests[0].headers.get("cookie", "")


def test_foreign_listing_scope_is_rejected_before_native_http():
    from enterprise_platform.adapters.workbench_context_client import ContextCheckRejected

    handler = Mock()
    client = DifyWorkbenchContextClient(
        base_url="https://native.internal/console/api", transport=httpx.MockTransport(handler)
    )
    query = BranchListQuery(workspace_id=PRINCIPAL.workspace_id, actor_id="other", installed_app_id=UUID(int=1))
    with pytest.raises(ContextCheckRejected):
        asyncio.run(client.check_listing_access(PRINCIPAL, SESSION, query))
    handler.assert_not_called()


def test_invalid_limit_does_not_check_access_or_read_storage():
    from enterprise_platform.application.errors import InvalidInput

    service, repository, access = setup()
    with pytest.raises(InvalidInput):
        run(service, limit=101)
    assert repository.mock_calls == []
    access.check_listing_access.assert_not_called()
