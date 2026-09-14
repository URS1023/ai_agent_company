from io import BytesIO
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from test_office_export import FILE, setup

from enterprise_platform.application.errors import AccessDenied, Unauthenticated
from enterprise_platform.http.app import create_app

URL = f"/enterprise/api/v1/office/files/{FILE}/document"


def client_for(service, principal):
    identity = Mock(resolve=AsyncMock(return_value=principal))
    return TestClient(create_app(Mock(), identity, office_exports=service)), identity


def test_download_returns_real_docx_with_private_attachment_headers():
    service, principal, _, _, renderer, _ = setup()
    client, identity = client_for(service, principal)
    with client:
        response = client.get(URL, params={"expected_revision": "1"}, headers={"authorization": "Bearer session"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == f'attachment; filename="office-{FILE}-r1.docx"'
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with ZipFile(BytesIO(response.content)) as package:
        assert package.testzip() is None
        assert b"Quality report" in package.read("word/document.xml")
    renderer.assert_called_once()
    identity.resolve.assert_awaited_once_with(cookie_header=None, authorization="Bearer session", csrf_token=None)


@pytest.mark.parametrize("error", [Unauthenticated("secret"), AccessDenied("secret")])
def test_identity_denial_is_private_and_precedes_render(error):
    service, principal, _, _, renderer, _ = setup()
    client, identity = client_for(service, principal)
    identity.resolve.side_effect = error
    with client:
        response = client.get(URL, params={"expected_revision": "1"})
    assert response.status_code == error.status_code
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"
    renderer.assert_not_called()


@pytest.mark.parametrize("revision", [None, "0", "-1", "1.0", "true", "1e0", "secret", "9" * 20])
def test_invalid_revision_is_private_and_not_echoed(revision):
    service, principal, _, _, renderer, _ = setup()
    client, _ = client_for(service, principal)
    with client:
        response = client.get(URL, params={} if revision is None else {"expected_revision": revision})
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    assert response.headers["cache-control"] == "private, no-store"
    renderer.assert_not_called()


def test_stale_revision_returns_conflict_without_docx():
    service, principal, _, _, renderer, _ = setup()
    client, _ = client_for(service, principal)
    with client:
        response = client.get(URL, params={"expected_revision": "2"})
    assert response.status_code == 409
    assert response.json() == {"code": "conflict"}
    assert response.headers["cache-control"] == "private, no-store"
    assert "content-disposition" not in response.headers
    renderer.assert_not_called()


def test_unconfigured_exports_fail_closed_after_identity():
    _, principal, _, _, _, _ = setup()
    client, identity = client_for(None, principal)
    with client:
        response = client.get(URL, params={"expected_revision": "1"})
    assert response.status_code == 503
    assert response.json() == {"code": "dependency_unavailable"}
    assert response.headers["cache-control"] == "private, no-store"
    identity.resolve.assert_awaited_once()


def test_openapi_documents_binary_download_not_json():
    service, principal, _, _, _, _ = setup()
    client, _ = client_for(service, principal)
    operation = client.app.openapi()["paths"]["/enterprise/api/v1/office/files/{file_id}/document"]["get"]
    content = operation["responses"]["200"]["content"]
    assert "application/json" not in content
    assert content["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
