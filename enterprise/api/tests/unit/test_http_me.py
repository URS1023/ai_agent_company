from unittest.mock import create_autospec

import pytest
from fastapi.testclient import TestClient

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import Unauthenticated
from enterprise_platform.application.ports import EnterpriseRepository, IdentityProvider
from enterprise_platform.application.service import BusinessService
from enterprise_platform.http.app import create_app


@pytest.mark.parametrize(
    "role,manage,review",
    [
        ("owner", True, True),
        ("admin", True, True),
        ("editor", True, False),
        ("normal", False, False),
        ("dataset_operator", False, False),
    ],
)
def test_current_business_access_is_derived_from_native_server_principal(role: str, manage: bool, review: bool) -> None:
    identity = create_autospec(IdentityProvider, instance=True, spec_set=True)
    identity.resolve.return_value = Principal.model_validate(
        {
            "actor_id": "actor",
            "workspace_id": "workspace",
            "workspace_role": role,
            "display_name": "Name",
        }
    )
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    with TestClient(create_app(BusinessService(repo), identity)) as client:
        response = client.get("/enterprise/api/v1/me", headers={"X-Workspace-ID": "forged", "X-CSRF-Token": "csrf"})
    assert response.status_code == 200
    assert response.json() == {
        "actor_id": "actor",
        "workspace_id": "workspace",
        "display_name": "Name",
        "permissions": {"read": True, "manage": manage, "run": manage, "review": review},
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert repo.mock_calls == []
    identity.resolve.assert_awaited_once_with(cookie_header=None, authorization=None, csrf_token="csrf")


def test_current_business_access_never_falls_back_to_an_anonymous_role() -> None:
    identity = create_autospec(IdentityProvider, instance=True, spec_set=True)
    identity.resolve.side_effect = Unauthenticated()
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    with TestClient(create_app(BusinessService(repo), identity)) as client:
        response = client.get("/enterprise/api/v1/me")
    assert response.status_code == 401
    assert response.json() == {"code": "unauthenticated"}


def test_current_access_has_generated_schema_and_native_security_requirements() -> None:
    identity = create_autospec(IdentityProvider, instance=True, spec_set=True)
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    schema = create_app(BusinessService(repo), identity).openapi()
    operation = schema["paths"]["/enterprise/api/v1/me"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/BusinessAccess")
    assert all("DifyCSRFHeader" in requirement for requirement in operation["security"])
