from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from test_office_edits import FILE, REQUEST, UNIT, setup

from enterprise_platform.http.app import create_app

URL = f"/enterprise/api/v1/office/files/{FILE}/edits"
BODY = {
    "request_id": str(REQUEST),
    "expected_revision": "1",
    "replacements": [
        {
            "unit_id": str(UNIT),
            "content": [
                {
                    "table_id": "values",
                    "headers": ["large", "exact", "code"],
                    "rows": [[{"integer": "9007199254740993"}, {"decimal": "1.2300000000000000001"}, "0007"]],
                }
            ],
        }
    ],
}


def client_for():
    service, repository, policy, actor, record, _ = setup()
    identity = Mock(resolve=AsyncMock(return_value=actor))
    app = create_app(Mock(), identity, allowed_origins=("https://portal",), office_files=service)
    return TestClient(app), repository, policy, record


def test_edit_translates_exact_wire_values_and_returns_new_saved_revision():
    client, repository, _, original = client_for()
    with client:
        response = client.post(URL, json=BODY, headers={"origin": "https://portal"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    args = repository.commit.call_args.kwargs
    assert args["request_id"] == REQUEST
    table = args["candidate"].content.units[0].content[0]
    assert table.rows == ((9007199254740993, Decimal("1.2300000000000000001"), "0007"),)
    assert args["candidate"].template_id == original.template_id
    assert args["candidate"].source_snapshot_ids == original.source_snapshot_ids
    assert response.json()["revision"] == "2"
    assert response.json()["units"][0]["content"][0]["rows"] == BODY["replacements"][0]["content"][0]["rows"]


@pytest.mark.parametrize(
    "cell", [1, 1.5, True, {"integer": 1}, {"integer": "1.0"}, {"decimal": "NaN"}, {"decimal": "Infinity"}]
)
def test_invalid_numeric_input_is_private_and_never_committed(cell):
    client, repository, _, _ = client_for()
    body = deepcopy(BODY)
    body["replacements"][0]["content"][0]["rows"][0][0] = cell
    with client:
        response = client.post(URL, json=body, headers={"origin": "https://portal"})
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input"}
    assert response.headers["cache-control"] == "private, no-store"
    repository.commit.assert_not_called()


def test_edit_rejects_untrusted_origin_before_file_access():
    client, repository, policy, _ = client_for()
    with client:
        response = client.post(URL, json=BODY, headers={"origin": "https://other"})
    assert response.status_code == 403
    policy.authorize.assert_not_called()
    repository.commit.assert_not_called()


def test_edit_cannot_change_template_or_source_bindings():
    client, repository, _, _ = client_for()
    with client:
        response = client.post(URL, json={**BODY, "template_id": "other"}, headers={"origin": "https://portal"})
    assert response.status_code == 422
    repository.commit.assert_not_called()


def test_edit_replay_returns_saved_receipt_without_committing_again():
    from enterprise_platform.application.office_edits import OfficeEditReceipt

    client, repository, _, _ = client_for()
    with client:
        first = client.post(URL, json=BODY, headers={"origin": "https://portal"})
        args = repository.commit.call_args.kwargs
        repository.find_receipt.return_value = OfficeEditReceipt(
            workspace_id="workspace",
            actor_id="actor",
            file_id=FILE,
            request_id=REQUEST,
            command_hash=args["command_hash"],
            record=args["candidate"],
        )
        repository.commit.reset_mock()
        second = client.post(URL, json=BODY, headers={"origin": "https://portal"})
    assert second.status_code == 200
    assert second.json() == first.json()
    repository.commit.assert_not_called()


def test_stale_edit_does_not_overwrite_current_file():
    client, repository, _, _ = client_for()
    with client:
        response = client.post(URL, json={**BODY, "expected_revision": "2"}, headers={"origin": "https://portal"})
    assert response.status_code == 409
    assert response.json() == {"code": "conflict"}
    repository.commit.assert_not_called()


def test_chart_edit_converts_only_exact_decimal_strings():
    client, repository, _, _ = client_for()
    body = deepcopy(BODY)
    body["replacements"][0]["content"] = [
        {"categories": ["A", "B"], "series": [{"name": "values", "values": ["1E-20", None]}]}
    ]
    with client:
        response = client.post(URL, json=body, headers={"origin": "https://portal"})
    assert response.status_code == 200
    chart = repository.commit.call_args.kwargs["candidate"].content.units[0].content[0]
    assert chart.series[0].values == (Decimal("1E-20"), None)
    assert response.json()["units"][0]["content"] == body["replacements"][0]["content"]
