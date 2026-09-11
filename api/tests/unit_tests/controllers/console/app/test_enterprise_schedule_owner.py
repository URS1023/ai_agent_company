from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from controllers.console.app import enterprise_schedule as controller
from services.enterprise_schedule_inspection import ScheduleInspection, ScheduleInspectionError

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
WF = "33333333-3333-4333-8333-333333333333"
HEADERS = {
    "X-Enterprise-Expected-Workspace": WS,
    "X-Enterprise-Expected-Workflow": WF,
    "X-Enterprise-Expected-Graph-Hash": "a" * 64,
    "X-Enterprise-Schedule-Operation": "inspect-native-owner",
}


@pytest.fixture
def native(monkeypatch):
    inspect = MagicMock(return_value=ScheduleInspection(WS, APP, WF, "a" * 64, True))
    monkeypatch.setattr(controller, "inspect_schedule_owner", inspect)
    monkeypatch.setattr(controller, "db", SimpleNamespace(session=MagicMock()))
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    return inspect


def read(headers=HEADERS, tenant=WS):
    api = controller.EnterpriseScheduleOwnerApi()
    with Flask(__name__).test_request_context("/", headers=headers):
        return unwrap(api.get)(api, SimpleNamespace(id=APP, tenant_id=tenant))


def test_owner_response_is_scoped_compact_and_not_cacheable(native):
    body, code, headers = read()
    assert code == 200
    assert body == {
        "workspace_id": WS,
        "app_id": APP,
        "workflow_id": WF,
        "graph_hash": "a" * 64,
        "native_owner_present": True,
    }
    assert headers == {"X-Enterprise-Workspace": WS, "Cache-Control": "private, no-store"}
    assert native.call_args.kwargs == {"workspace_id": WS, "app_id": APP, "workflow_id": WF, "expected_hash": "a" * 64}


@pytest.mark.parametrize("missing", list(HEADERS))
def test_partial_headers_are_rejected_before_database_access(native, missing):
    with pytest.raises(BadRequest):
        read({key: value for key, value in HEADERS.items() if key != missing})
    native.assert_not_called()


def test_feature_disabled_does_not_inspect(native):
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = False
    with pytest.raises(Forbidden):
        read()
    native.assert_not_called()


def test_foreign_app_scope_is_rejected_before_inspection(native):
    with pytest.raises(Conflict):
        read(tenant=APP)
    native.assert_not_called()


def test_uninspectable_publication_returns_conflict_not_false(native):
    native.side_effect = ScheduleInspectionError("malformed")
    with pytest.raises(Conflict):
        read()


def test_foreign_receipt_is_rejected(native):
    native.return_value = ScheduleInspection(APP, APP, WF, "a" * 64, False)
    with pytest.raises(Conflict):
        read()
