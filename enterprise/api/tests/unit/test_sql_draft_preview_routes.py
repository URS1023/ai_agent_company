import pytest
from test_dashboard_routes import HEADERS, URL
from test_sql_draft_preview import fixture
from test_sql_trial_history_routes import setup

from enterprise_platform.application.dashboard_sql_trial_preview import preview_sql_draft

PATH = URL + "/sql-proposals/draft-1/preview"


def test_selected_previews_are_loaded_from_storage_not_request_rows():
    client, runner, store, _, identity = setup()
    evidence, draft, design = fixture()
    store.get.side_effect = list(evidence)
    runner.preview_draft_evidence.return_value = preview_sql_draft(evidence, draft, design)
    response = client.post(PATH, json={"trial_ids": [item.trial_id for item in evidence]}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "preview_only"
    assert response.json()["snapshot_status"] == "independent_trials"
    assert response.headers["cache-control"] == "private, no-store"
    runner.preview_draft_evidence.assert_awaited_once_with(identity.resolve.return_value, evidence)
    runner.run.assert_not_awaited()
    store.create.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"trial_ids": []},
        {"trial_ids": ["same", "same"]},
        {"trial_ids": ["x"] * 101},
        {"trial_ids": [""]},
        {"trial_ids": ["x"], "sql": "SELECT 1"},
        {"trial_ids": ["x"], "rows": []},
    ],
)
def test_invalid_or_extra_preview_inputs_are_rejected_before_evidence_loading(payload):
    client, runner, store, _, identity = setup()
    response = client.post(PATH, json=payload, headers=HEADERS)
    assert response.status_code == 422
    store.get.assert_not_called()


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_other_methods_do_not_read_or_execute(method):
    client, runner, store, _, identity = setup()
    assert getattr(client, method)(PATH, headers=HEADERS).status_code == 405
    store.get.assert_not_called()
    runner.run.assert_not_awaited()
