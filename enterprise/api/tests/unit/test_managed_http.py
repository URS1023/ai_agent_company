import hashlib
import hmac
import json
from unittest.mock import create_autospec

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_managed_execution import claims, dependencies

from enterprise_platform.application.managed_execution import ExecutionAssociationPending, ManagedExecutionService
from enterprise_platform.http.internal import create_managed_router


def test_private_route_uses_real_auth_and_stays_out_of_public_openapi():
    service, lookup, _, capture, _ = dependencies()
    app = FastAPI()
    app.include_router(create_managed_router(service))
    body = json.dumps(claims()).encode()
    signature = hmac.new(("s" * 48).encode(), body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        assert "/enterprise/internal/v1/evaluate" not in client.get("/openapi.json").json()["paths"]
        denied = client.post(
            "/enterprise/internal/v1/evaluate", content=body, headers={"Content-Type": "application/json"}
        )
        assert denied.status_code == 401
        lookup.find_native_run.assert_not_called()
        response = client.post(
            "/enterprise/internal/v1/evaluate",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Enterprise-Key-ID": "key-1",
                "X-Enterprise-Signature": signature,
                "X-Workspace-ID": "forged",
            },
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["run_id"] == "run-1"
    assert "nonce" not in response.text
    capture.capture.assert_awaited_once()


def test_private_route_limits_bytes_and_content_type_before_service_invocation():
    service = create_autospec(ManagedExecutionService, instance=True, spec_set=True)
    app = FastAPI()
    app.include_router(create_managed_router(service))
    with TestClient(app) as client:
        assert client.post("/enterprise/internal/v1/evaluate", content="{}").status_code == 415
        response = client.post(
            "/enterprise/internal/v1/evaluate", content=b"x" * 8193, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413
    service.evaluate.assert_not_awaited()


def test_private_route_exposes_only_retryable_association_code_or_sanitized_failure():
    service = create_autospec(ManagedExecutionService, instance=True, spec_set=True)
    service.evaluate.side_effect = [ExecutionAssociationPending(), RuntimeError("password=private")]
    app = FastAPI()
    app.include_router(create_managed_router(service))
    with TestClient(app) as client:
        pending = client.post("/enterprise/internal/v1/evaluate", json={})
        failed = client.post("/enterprise/internal/v1/evaluate", json={})
    assert pending.status_code == 409
    assert pending.json() == {"code": "execution_association_pending"}
    assert failed.status_code == 503
    assert failed.json() == {"code": "managed_execution_failed"}
    assert service.evaluate.await_count == 2
