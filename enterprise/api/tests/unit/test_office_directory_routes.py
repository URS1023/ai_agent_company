from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from test_office_directory import directory

from enterprise_platform.http.app import create_app

URL = "/enterprise/api/v1/office/files"


def test_directory_http_returns_only_authorized_metadata_with_private_cache():
    service, candidates, principal, _, _, _ = directory()
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity, office_directory=service)) as client:
        response = client.get(URL, params={"offset": 50})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert set(response.json()["items"][0]) == {"file_id", "revision", "kind", "template_id", "template_revision"}
    candidates.candidates.assert_called_once_with(principal, offset=50, limit=50)


def test_directory_http_unconfigured_is_private_dependency_error():
    _, _, principal, _, _, _ = directory()
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity)) as client:
        response = client.get(URL)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"


def test_directory_http_invalid_offset_never_scans():
    service, candidates, principal, _, _, _ = directory()
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity, office_directory=service)) as client:
        response = client.get(URL, params={"offset": -1})
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    assert response.headers["cache-control"] == "private, no-store"
    candidates.candidates.assert_not_called()
