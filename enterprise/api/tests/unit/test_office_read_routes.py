from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from test_office_export import FILE, setup

from enterprise_platform.application.errors import AccessDenied
from enterprise_platform.application.office_edits import OfficeEditService
from enterprise_platform.domain.office_content import TableData
from enterprise_platform.domain.office_revision import OfficeUnit
from enterprise_platform.http.app import create_app

URL = f"/enterprise/api/v1/office/files/{FILE}"


def test_read_returns_lossless_saved_content_without_rendering_or_writing():
    _, principal, repository, policy, renderer, record = setup()
    table = TableData(
        table_id="values",
        headers=("code", "large", "decimal", "missing"),
        rows=(("0007", 9007199254740993, Decimal("1.2300000000000000001"), None),),
    )
    unit = OfficeUnit(unit_id=FILE, kind="table", content=(table,))
    repository.get.return_value = replace(
        record,
        template_revision=9007199254740993,
        content=record.content.model_copy(update={"revision": 9007199254740993, "units": (unit,)}),
    )
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity, office_files=OfficeEditService(repository, policy))) as client:
        response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == "9007199254740993"
    assert body["template_revision"] == "9007199254740993"
    assert body["units"][0]["content"][0]["rows"] == [
        [
            "0007",
            {"integer": "9007199254740993"},
            {"decimal": "1.2300000000000000001"},
            None,
        ]
    ]
    assert body["file_id"] == str(FILE)
    assert "workspace_id" not in body
    assert response.headers["cache-control"] == "private, no-store"
    repository.commit.assert_not_called()
    renderer.assert_not_called()


def test_read_denied_source_access_never_returns_document():
    _, principal, repository, policy, _, _ = setup()
    policy.authorize.side_effect = AccessDenied("private source detail")
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity, office_files=OfficeEditService(repository, policy))) as client:
        response = client.get(URL)
    assert response.status_code == 403
    assert response.json() == {"code": "access_denied"}
    assert response.headers["cache-control"] == "private, no-store"
    repository.get.assert_not_called()


def test_read_unconfigured_service_does_not_fabricate_file():
    _, principal, _, _, _, _ = setup()
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity)) as client:
        response = client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"code": "dependency_unavailable"}
    assert response.headers["cache-control"] == "private, no-store"


def test_read_preserves_chart_precision_and_stable_slide_identity():
    from enterprise_platform.domain.office_content import ChartData, ChartSeries
    from enterprise_platform.domain.office_revision import OfficeText

    _, principal, repository, policy, _, record = setup()
    chart = ChartData(
        categories=("A", "B"),
        series=(ChartSeries(name="measurements", values=(Decimal("0.00000000000000000001"), None)),),
    )
    unit = OfficeUnit(unit_id=FILE, kind="slide", content=(OfficeText(text="Overview"), chart))
    repository.get.return_value = replace(
        record,
        source_snapshot_ids=("snapshot-1",),
        content=record.content.model_copy(update={"kind": "presentation", "units": (unit,)}),
    )
    identity = Mock(resolve=AsyncMock(return_value=principal))
    with TestClient(create_app(Mock(), identity, office_files=OfficeEditService(repository, policy))) as client:
        response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "presentation"
    assert body["source_snapshot_ids"] == ["snapshot-1"]
    assert body["units"][0]["unit_id"] == str(FILE)
    assert body["units"][0]["content"] == [
        {"text": "Overview"},
        {"categories": ["A", "B"], "series": [{"name": "measurements", "values": ["1E-20", None]}]},
    ]
