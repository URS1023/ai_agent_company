import asyncio
from unittest.mock import AsyncMock

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from test_source_service import principal
from test_workflow_setup_service import harness, start

from enterprise_platform.application.errors import AccessDenied
from enterprise_platform.http.workflow_setup_routes import add_workflow_setup_routes

COLLECTION = "/enterprise/api/v1/devices/device-1/bindings/alert/workflow-setups"


class Error(BaseModel):
    code: str


def client(service, *, deny=False):
    app = FastAPI()
    router = APIRouter()

    async def actor(request: Request):
        if deny:
            raise AccessDenied()
        return principal()

    add_workflow_setup_routes(router, service, actor, Error)
    app.include_router(router)
    return TestClient(app)


def test_http_start_uses_only_identity_and_auth_headers_for_session():
    service, _, _, _, _, request = harness()
    result = asyncio.run(start(service, request))
    injected = AsyncMock()
    injected.start.return_value = result
    response = client(injected).post(
        COLLECTION,
        json=request.model_dump(),
        headers={
            "Idempotency-Key": "http-key",
            "Cookie": "session=private",
            "Authorization": "Bearer private",
            "X-CSRF-Token": "csrf-private",
            "X-Workspace-Id": "forged",
        },
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == result.model_dump(mode="json")
    args = injected.start.call_args.args
    assert args[0] == principal()
    assert args[1].cookie_header == "session=private"
    assert args[1].authorization == "Bearer private"
    assert args[1].csrf_token == "csrf-private"
    assert args[2:] == ("device-1", "alert", request, "http-key")
    assert "private" not in response.text


def test_http_get_and_list_use_typed_private_projections():
    service, _, _, _, _, request = harness()
    result = asyncio.run(start(service, request))
    http = client(service)
    response = http.get(COLLECTION + "?offset=0&limit=1")
    assert response.status_code == 200
    assert response.json()["items"] == [result.model_dump(mode="json")]
    response = http.get("/enterprise/api/v1/workflow-setups/" + result.id)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["state"] == "draft_ready"


def test_http_requires_key_and_rejects_extra_identity_without_service_io():
    service = AsyncMock()
    http = client(service)
    body = {"source_id": "source", "expected_source_revision": 1}
    responses = [
        http.post(COLLECTION, json=body),
        http.post(COLLECTION, json=body | {"workspace_id": "forged"}, headers={"Idempotency-Key": "key"}),
        http.get(COLLECTION + "?limit=0"),
    ]
    for response in responses:
        assert response.status_code == 422
        assert response.headers["cache-control"] == "private, no-store"
        assert "forged" not in response.text
    service.start.assert_not_called()
    service.list.assert_not_called()


def test_missing_service_returns_503_and_native_identity_denial_is_preserved():
    http = client(None)
    for response in (
        http.get(COLLECTION),
        http.get("/enterprise/api/v1/workflow-setups/setup"),
        http.post(
            COLLECTION, json={"source_id": "source", "expected_source_revision": 1}, headers={"Idempotency-Key": "key"}
        ),
    ):
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"
    injected = AsyncMock()
    response = client(injected, deny=True).post(
        COLLECTION, json={"source_id": "source", "expected_source_revision": 1}, headers={"Idempotency-Key": "key"}
    )
    assert response.status_code == 403
    injected.start.assert_not_called()
