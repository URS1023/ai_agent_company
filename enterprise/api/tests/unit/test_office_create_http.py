from unittest.mock import create_autospec
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_http_api import Identity

from enterprise_platform.application.office_edits import OfficeFileCreator
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.application.service import BusinessService
from enterprise_platform.http.app import create_app


def test_creation_route_is_declared_and_fails_closed_without_creator():
    app = create_app(
        BusinessService(create_autospec(EnterpriseRepository, instance=True)),
        Identity("admin"),
        allowed_origins=("http://localhost:3000",),
    )
    with TestClient(app) as client:
        response = client.post(
            "/enterprise/api/v1/office/files",
            headers={"Origin": "http://localhost:3000"},
            json={
                "expected_actor_id": "a",
                "expected_workspace_id": "w",
                "file_id": str(UUID(int=1)),
                "kind": "document",
                "template_id": "document-default",
                "template_revision": "1",
                "source_snapshot_ids": [],
                "units": [{"unit_id": str(UUID(int=2)), "kind": "paragraph", "content": [{"text": "Report"}]}],
            },
        )
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("expected_actor,expected_workspace", [("other", "w"), ("a", "other"), ("", "w"), ("a", "")])
def test_creation_rejects_stale_scope_before_persistence(expected_actor: str, expected_workspace: str):
    creator = create_autospec(OfficeFileCreator, instance=True)
    app = create_app(
        BusinessService(create_autospec(EnterpriseRepository, instance=True)),
        Identity("admin"),
        allowed_origins=("http://localhost:3000",),
        office_creator=creator,
    )
    with TestClient(app) as client:
        response = client.post(
            "/enterprise/api/v1/office/files",
            headers={"Origin": "http://localhost:3000"},
            json={
                "expected_actor_id": expected_actor,
                "expected_workspace_id": expected_workspace,
                "file_id": str(UUID(int=1)),
                "kind": "document",
                "template_id": "document-default",
                "template_revision": "1",
                "units": [{"unit_id": str(UUID(int=2)), "kind": "paragraph", "content": [{"text": "Report"}]}],
            },
        )
    assert response.status_code == (403 if expected_actor and expected_workspace else 422)
    creator.create.assert_not_called()
