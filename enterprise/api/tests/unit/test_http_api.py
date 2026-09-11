from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID

import httpx
import pytest
from fastapi.openapi.models import OpenAPI
from fastapi.testclient import TestClient

from enterprise_platform.adapters.dify_identity import DifyIdentityClient
from enterprise_platform.application.contracts import Binding, Device, Page, Principal
from enterprise_platform.application.errors import (
    AccessDenied,
    Conflict,
    DependencyUnavailable,
    EnterpriseError,
    InvalidInput,
    InvalidState,
    NotFound,
    PersistenceError,
    Unauthenticated,
)
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.application.service import BusinessService
from enterprise_platform.http.app import create_app


class Identity:
    def __init__(self, role: str = "editor", *, authenticated: bool = True) -> None:
        self.role, self.authenticated = role, authenticated
        self.seen: list[tuple[str | None, str | None, str | None]] = []

    async def resolve(
        self, *, cookie_header: str | None, authorization: str | None, csrf_token: str | None
    ) -> Principal:
        self.seen.append((cookie_header, authorization, csrf_token))
        if not self.authenticated:
            raise Unauthenticated()
        return Principal.model_validate(
            {"actor_id": "a", "workspace_id": "w", "workspace_role": self.role, "display_name": "A"}
        )


def device() -> Device:
    return Device(
        id="d",
        workspace_id="w",
        device_code="0001",
        name="Device",
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_create_uses_original_identity_and_returns_revision() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.create_device.return_value = device()
    identity = Identity()
    with TestClient(create_app(BusinessService(repo), identity, allowed_origins=("http://localhost:3000",))) as client:
        response = client.post(
            "/enterprise/api/v1/devices",
            json={"device_code": "0001", "name": "Device"},
            headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "csrf", "Authorization": "Bearer session"},
        )
    assert response.status_code == 201
    assert response.headers["etag"] == '"1"'
    assert response.json()["device_code"] == "0001"
    assert identity.seen == [(None, "Bearer session", "csrf")]
    assert repo.create_device.call_args.kwargs["actor_id"] == "a"
    assert repo.create_device.call_args.args[0] == "w"


def test_unknown_workspace_body_and_untrusted_origin_are_rejected() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    with TestClient(
        create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    ) as client:
        assert (
            client.post(
                "/enterprise/api/v1/devices",
                json={"device_code": "1", "name": "D"},
                headers={"Origin": "https://different.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/enterprise/api/v1/devices",
                json={"device_code": "1", "name": "D", "workspace_id": "forged"},
                headers={"Origin": "http://localhost:3000"},
            ).status_code
            == 422
        )
    repo.create_device.assert_not_called()


def test_no_session_and_readonly_role_never_mutate() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    for identity, expected in [(Identity(authenticated=False), 401), (Identity("normal"), 403)]:
        with TestClient(
            create_app(BusinessService(repo), identity, allowed_origins=("http://localhost:3000",))
        ) as client:
            response = client.post(
                "/enterprise/api/v1/devices",
                json={"device_code": "1", "name": "D"},
                headers={"Origin": "http://localhost:3000"},
            )
        assert response.status_code == expected
    repo.create_device.assert_not_called()


def test_update_requires_a_precise_positive_if_match_revision() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.update_device.return_value = device()
    with TestClient(
        create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    ) as client:
        for revision in [None, "*", "0", "1.5", 'W/"1"']:
            headers = {"Origin": "http://localhost:3000"}
            if revision is not None:
                headers["If-Match"] = revision
            assert (
                client.put(
                    "/enterprise/api/v1/devices/d", json={"device_code": "0001", "name": "Device"}, headers=headers
                ).status_code
                == 428
            )
        assert (
            client.put(
                "/enterprise/api/v1/devices/d",
                json={"device_code": "0001", "name": "Device"},
                headers={"Origin": "http://localhost:3000", "If-Match": '"1"'},
            ).status_code
            == 200
        )
    assert repo.update_device.call_args.kwargs["expected_revision"] == 1


def test_lists_are_paginated_and_not_found_does_not_echo_private_exception_text() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.list_devices.return_value = Page[Device](items=(device(),), offset=0, limit=50, total=1)
    repo.get_device.side_effect = NotFound("private database URL")
    with TestClient(create_app(BusinessService(repo), Identity())) as client:
        assert client.get("/enterprise/api/v1/devices").json()["total"] == 1
        assert client.get("/enterprise/api/v1/devices?limit=101").status_code == 422
        response = client.get("/enterprise/api/v1/devices/other")
    assert response.status_code == 404
    assert "private" not in response.text


def test_openapi_does_not_expose_internal_worker_nonce() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    with TestClient(create_app(BusinessService(repo), Identity())) as client:
        schema = client.get("/enterprise/api/openapi.json").json()
    assert "dispatch_nonce" not in str(schema)
    assert "/enterprise/api/v1/devices/{device_id}/bindings/{scenario}/runs" in schema["paths"]


@pytest.mark.parametrize("method", ["put", "delete"])
def test_openapi_requires_revision_header_and_describes_precondition_error(method: str) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    schema = create_app(BusinessService(repo), Identity()).openapi()
    operation = schema["paths"]["/enterprise/api/v1/devices/{device_id}"][method]
    header = next(parameter for parameter in operation["parameters"] if parameter["name"].lower() == "if-match")
    assert header["required"] is True
    assert header["schema"]["type"] == "string"
    assert operation["responses"]["428"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_openapi_documents_etag_for_device_representations() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    schema = create_app(BusinessService(repo), Identity()).openapi()
    for path, method, status in [
        ("/enterprise/api/v1/devices", "post", "201"),
        ("/enterprise/api/v1/devices/{device_id}", "get", "200"),
        ("/enterprise/api/v1/devices/{device_id}", "put", "200"),
    ]:
        etag = schema["paths"][path][method]["responses"][status]["headers"]["ETag"]
        assert etag["schema"]["type"] == "string"


@pytest.mark.parametrize(
    "error",
    [
        AccessDenied(),
        Conflict(),
        DependencyUnavailable(),
        EnterpriseError(),
        InvalidInput(),
        InvalidState(),
        NotFound(),
        PersistenceError(),
        Unauthenticated(),
    ],
)
def test_domain_errors_match_documented_response_without_exception_text(error: EnterpriseError) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.get_device.side_effect = error
    app = create_app(BusinessService(repo), Identity())
    with TestClient(app) as client:
        response = client.get("/enterprise/api/v1/devices/d")
    assert response.status_code == error.status_code
    assert response.json() == {"code": error.code}
    operation = app.openapi()["paths"]["/enterprise/api/v1/devices/{device_id}"]["get"]
    assert operation["responses"][str(error.status_code)]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_request_validation_errors_use_the_public_error_dto_without_echoing_input() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    app = create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    with TestClient(app) as client:
        response = client.post(
            "/enterprise/api/v1/devices",
            json={"device_code": "0001", "name": "Device", "password": "fixture-value"},
            headers={"Origin": "http://localhost:3000"},
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    assert "fixture-value" not in response.text
    repo.create_device.assert_not_called()


@pytest.mark.parametrize("batch", [{"password": "fixture-value"}, "x" * 262144], ids=["credential", "oversize"])
def test_invalid_run_parameters_return_422_without_enqueue(batch: object) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.get_binding.return_value = Binding(
        id="b",
        workspace_id="w",
        device_id="d",
        scenario="alert",
        revision=1,
        app_id="app",
        workflow_id=UUID(int=1),
        specification_revision="spec-1",
        secret_ref="key-ref",
        source_id="source-1",
        source_revision="source-revision-1",
        read_id="read-query-1",
        read_revision="read-1",
        manifest={"input_keys": ["batch"]},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    app = create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/enterprise/api/v1/devices/d/bindings/alert/runs",
            json={"expected_binding_revision": 1, "parameters": {"batch": batch}},
            headers={"Origin": "http://localhost:3000", "Idempotency-Key": "request-1"},
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    repo.enqueue_run.assert_not_called()


def test_openapi_requires_native_session_and_csrf_together_even_for_reads() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    schema = create_app(BusinessService(repo), Identity()).openapi()
    schemes = schema["components"]["securitySchemes"]
    for name, cookie in [
        ("DifySession", "access_token"),
        ("DifyHostSession", "__Host-access_token"),
        ("DifyCSRFCookie", "csrf_token"),
        ("DifyHostCSRFCookie", "__Host-csrf_token"),
    ]:
        assert {key: schemes[name][key] for key in ["type", "in", "name"]} == {
            "type": "apiKey",
            "in": "cookie",
            "name": cookie,
        }
    assert schemes["DifyBearer"]["type"] == "http"
    assert schemes["DifyBearer"]["scheme"] == "bearer"
    assert schemes["DifyCSRFHeader"]["in"] == "header"
    assert schemes["DifyCSRFHeader"]["name"] == "X-CSRF-Token"
    expected = [
        {session: [], csrf_cookie: [], "DifyCSRFHeader": []}
        for session in ["DifySession", "DifyHostSession", "DifyBearer"]
        for csrf_cookie in ["DifyCSRFCookie", "DifyHostCSRFCookie"]
    ]
    for path, operations in schema["paths"].items():
        for operation in operations.values():
            assert operation.get("security", []) == (expected if path.startswith("/enterprise/api/v1/") else [])


def test_openapi_requires_an_allowlisted_origin_for_writes_not_reads() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    origins = ("http://localhost:3000", "https://portal.example")
    schema = create_app(BusinessService(repo), Identity(), allowed_origins=origins).openapi()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            headers = [
                parameter for parameter in operation.get("parameters", []) if parameter["name"].lower() == "origin"
            ]
            if path.startswith("/enterprise/api/v1/") and method in {"post", "put", "delete"}:
                assert len(headers) == 1
                assert headers[0]["required"] is True
                assert headers[0]["schema"]["enum"] == list(origins)
            else:
                assert not headers


def test_openapi_overlay_is_valid_and_does_not_duplicate_headers_on_repeat_calls() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    app = create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    first = deepcopy(app.openapi())
    assert app.openapi() == first
    OpenAPI.model_validate(first)


@pytest.mark.parametrize("host_cookies", [False, True])
@pytest.mark.parametrize("use_bearer", [False, True])
def test_native_401_is_preserved_after_forwarding_cookie_bearer_and_csrf(host_cookies: bool, use_bearer: bool) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    upstream: list[httpx.Request] = []

    def reject_session(request: httpx.Request) -> httpx.Response:
        upstream.append(request)
        return httpx.Response(401, json={"message": "fixture-private-upstream-message"})

    identity = DifyIdentityClient(
        base_url="https://dify.example/console/api", transport=httpx.MockTransport(reject_session)
    )
    cookie_prefix = "__Host-" if host_cookies else ""
    csrf_cookie = f"{cookie_prefix}csrf_token=csrf-fixture"
    headers = {"Cookie": csrf_cookie, "X-CSRF-Token": "csrf-fixture", "X-Workspace-ID": "forged"}
    if use_bearer:
        headers["Authorization"] = "Bearer session-fixture"
    else:
        headers["Cookie"] += f"; {cookie_prefix}access_token=session-fixture"
    with TestClient(create_app(BusinessService(repo), identity)) as client:
        response = client.get("/enterprise/api/v1/devices", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"code": "unauthenticated"}
    assert len(upstream) == 1
    assert upstream[0].headers["Cookie"] == headers["Cookie"]
    assert upstream[0].headers.get("Authorization") == headers.get("Authorization")
    assert upstream[0].headers["X-CSRF-Token"] == "csrf-fixture"
    assert "X-Workspace-ID" not in upstream[0].headers
    repo.list_devices.assert_not_called()


def test_nonfinite_request_json_is_a_sanitized_422_not_a_server_error() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    app = create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/enterprise/api/v1/devices/d/bindings/alert/runs",
            content='{"expected_binding_revision":1,"parameters":{"batch":NaN}}',
            headers={
                "Origin": "http://localhost:3000",
                "Idempotency-Key": "request-1",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    repo.get_binding.assert_not_called()
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize("revision", [None, '"1', '1"', "1000000000", "*", 'W/"1"'])
def test_delete_revision_validation_preserves_428_and_never_deletes(revision: str | None) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    app = create_app(BusinessService(repo), Identity(), allowed_origins=("http://localhost:3000",))
    headers = {"Origin": "http://localhost:3000"}
    if revision is not None:
        headers["If-Match"] = revision
    with TestClient(app) as client:
        response = client.delete("/enterprise/api/v1/devices/d", headers=headers)
    assert response.status_code == 428
    assert response.json() == {"code": "revision_precondition_required"}
    repo.delete_device.assert_not_called()
