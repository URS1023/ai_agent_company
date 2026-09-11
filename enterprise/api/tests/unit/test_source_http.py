from unittest.mock import create_autospec

from fastapi.testclient import TestClient
from test_http_api import Identity
from test_source_contracts import db_draft

from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.application.service import BusinessService
from enterprise_platform.http.app import create_app


def test_schema_always_declares_typed_source_routes_even_without_runtime_configuration() -> None:
    app = create_app(BusinessService(create_autospec(EnterpriseRepository, instance=True)), Identity("admin"))
    schema = app.openapi()
    assert "/enterprise/api/v1/sources" in schema["paths"]
    assert schema["paths"]["/enterprise/api/v1/sources"]["post"]["requestBody"]["required"] is True
    with TestClient(app) as client:
        response = client.get("/enterprise/api/v1/sources/capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "can_manage": True,
            "write_enabled": False,
            "reason_code": "source_management_unavailable",
        }
        assert response.headers["cache-control"] == "private, no-store"
        assert client.get("/enterprise/api/v1/sources").status_code == 503


def test_source_write_auth_origin_idempotency_and_revision_are_real_requirements() -> None:
    app = create_app(
        BusinessService(create_autospec(EnterpriseRepository, instance=True)),
        Identity("admin"),
        allowed_origins=("http://localhost:3000",),
    )
    with TestClient(app) as client:
        assert client.post("/enterprise/api/v1/sources", json=db_draft()).status_code == 403
        response = client.post(
            "/enterprise/api/v1/sources", json=db_draft(), headers={"Origin": "http://localhost:3000"}
        )
        assert response.status_code == 422
        assert "fixture-password" not in response.text
    operation = app.openapi()["paths"]["/enterprise/api/v1/sources/{source_id}"]["put"]
    assert any(p["name"].lower() == "if-match" and p["required"] for p in operation["parameters"])


def test_http_routes_use_real_source_service_and_never_echo_secret_or_duplicate_save() -> None:
    from test_source_service import http_draft, service

    from enterprise_platform.application.contracts import Principal

    sources, repository, devices, cipher = service()

    class SourceIdentity:
        async def resolve(self, **kwargs):
            return Principal(
                workspace_id="workspace-1", actor_id="actor-1", workspace_role="owner", display_name="User"
            )

    app = create_app(
        BusinessService(create_autospec(EnterpriseRepository, instance=True)),
        SourceIdentity(),
        allowed_origins=("https://portal.test",),
        sources=sources,
    )
    headers = {"Origin": "https://portal.test", "Idempotency-Key": "request-1"}
    with TestClient(app) as client:
        data = http_draft()
        data["connection"]["pagination"] = {"parameter": "cursor", "next_cursor_path": ["next"]}
        first = client.post("/enterprise/api/v1/sources", json=data, headers=headers)
        assert first.status_code == 201, first.text
        assert "fixture-token" not in first.text and "headers" not in first.json()["connection"]
        assert first.json()["connection_status"] == "not_tested"
        assert first.headers["etag"] == '"1"'
        assert client.post("/enterprise/api/v1/sources", json=data, headers=headers).json() == first.json()
        path = "/enterprise/api/v1/sources/" + first.json()["source_id"]
        assert client.get(path).json() == first.json()
        data["name"] = "renamed"
        data["connection"].pop("headers")
        assert client.put(path, json=data, headers=headers).status_code == 428
        updated = client.put(path, json=data, headers={**headers, "If-Match": '"1"'})
        assert updated.status_code == 200, updated.text
        assert updated.json()["revision"] == 2
        assert client.put(path, json=data, headers={**headers, "If-Match": '"1"'}).status_code == 409
        assert len(repository.versions) == 2
        assert client.get("/enterprise/api/v1/sources").json()["total"] == 1
