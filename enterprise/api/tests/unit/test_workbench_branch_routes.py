from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_workbench_context_client import PRINCIPAL
from test_workbench_messages import scope
from test_workbench_routes import HEADERS

from enterprise_platform.application.errors import AccessDenied
from enterprise_platform.application.workbench_branches import create_root_branch
from enterprise_platform.http.app import create_app

COLLECTION = f"/enterprise/api/v1/workbench/apps/{UUID(int=1)}/branches"


def test_listing_uses_derived_actor_and_private_response():
    from enterprise_platform.application.workbench_branch_listing import BranchPage

    page = BranchPage(items=(create_root_branch(scope()),))
    service = Mock(list_branches=AsyncMock(return_value=page))
    with client_for(service) as client:
        response = client.get(COLLECTION, params={"after": "previous", "limit": 10}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == page.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    assert service.list_branches.call_args.args[0] == PRINCIPAL
    assert service.list_branches.call_args.kwargs == {"installed_app_id": UUID(int=1), "after": "previous", "limit": 10}


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": "oops"}, {"after": ""}])
def test_listing_rejects_invalid_page_parameters(params):
    service = Mock(list_branches=AsyncMock())
    with client_for(service) as client:
        response = client.get(COLLECTION, params=params, headers=HEADERS)
    assert response.status_code == 422
    service.list_branches.assert_not_called()


def client_for(service):
    return TestClient(
        create_app(
            Mock(),
            Mock(resolve=AsyncMock(return_value=PRINCIPAL)),
            allowed_origins=("https://portal",),
            workbench_branches=service,
        )
    )


def test_create_and_read_use_session_and_derived_actor():
    branch = create_root_branch(scope())
    service = Mock(create_root=AsyncMock(return_value=branch), get=AsyncMock(return_value=branch))
    with client_for(service) as client:
        created = client.post(COLLECTION, json={"branch_id": "branch"}, headers=HEADERS)
        read = client.get(COLLECTION + "/branch", headers=HEADERS)
    assert created.status_code == read.status_code == 200
    assert created.json() == read.json() == branch.model_dump(mode="json")
    assert created.headers["cache-control"] == read.headers["cache-control"] == "private, no-store"
    assert service.create_root.call_args.args[0] == PRINCIPAL
    assert service.create_root.call_args.args[1].csrf_token == "csrf"
    assert service.create_root.call_args.kwargs == {"installed_app_id": UUID(int=1), "branch_id": "branch"}


@pytest.mark.parametrize(
    "body",
    [
        {"branch_id": "branch", "actor_id": "other"},
        {"branch_id": ""},
        {"branch_id": "branch", "conversation_id": str(UUID(int=3))},
    ],
)
def test_root_creation_rejects_client_native_binding_or_identity(body):
    service = Mock(create_root=AsyncMock())
    with client_for(service) as client:
        response = client.post(COLLECTION, json=body, headers=HEADERS)
    assert response.status_code == 422
    service.create_root.assert_not_called()


def test_native_branch_denial_returns_private_error():
    service = Mock(create_root=AsyncMock(side_effect=AccessDenied("private-detail")))
    with client_for(service) as client:
        response = client.post(COLLECTION, json={"branch_id": "branch"}, headers=HEADERS)
    assert response.status_code == 403 and response.json() == {"code": "access_denied"}
    assert response.headers["cache-control"] == "private, no-store"


def test_disabled_branch_service_returns_503():
    with client_for(None) as client:
        response = client.post(COLLECTION, json={"branch_id": "branch"}, headers=HEADERS)
    assert response.status_code == 503
