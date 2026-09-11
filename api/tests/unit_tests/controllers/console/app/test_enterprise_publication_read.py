"""Exact publication metadata read, isolated from native DB and projection."""

from copy import deepcopy
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

from controllers.console.app import workflow as controller

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
WF = "33333333-3333-4333-8333-333333333333"
CRED = "44444444-4444-4444-8444-444444444444"
HASH = "a" * 64
PROVIDER = "enterprise/enterprise_device_assessment/enterprise_device"
HEADERS = {
    "X-Enterprise-Expected-Workspace": WS,
    "X-Enterprise-Expected-Workflow": WF,
    "X-Enterprise-Expected-Graph-Hash": HASH,
    "X-Enterprise-Publication-Operation": "read-assessment-publication",
}
GRAPH = {
    "nodes": [
        {
            "id": "assessment",
            "data": {
                "type": "tool",
                "provider_type": "builtin",
                "provider_id": PROVIDER,
                "tool_name": "evaluate_device",
                "credential_id": CRED,
            },
        }
    ]
}


@pytest.fixture
def native(monkeypatch):
    workflow = SimpleNamespace(
        id=WF, tenant_id=WS, app_id=APP, version="published", unique_hash=HASH, graph_dict=deepcopy(GRAPH)
    )
    service = MagicMock()
    service.get_published_workflow_by_id.return_value = workflow
    factory = MagicMock(return_value=service)
    session = MagicMock()
    monkeypatch.setattr(controller, "WorkflowService", factory)
    monkeypatch.setattr(controller, "db", SimpleNamespace(session=session))
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True))
    return service, factory, session, workflow


def read(headers, tenant=WS):
    api = controller.PublishedWorkflowApi()
    with Flask(__name__).test_request_context("/", headers=headers):
        return unwrap(api.get)(api, SimpleNamespace(id=APP, tenant_id=tenant))


def test_exact_publication_receipt_without_latest_or_graph_serialization(native):
    service, _, session, _ = native
    assert read(HEADERS) == (
        {
            "app_id": APP,
            "workflow_id": WF,
            "hash": HASH,
            "provider_id": PROVIDER,
            "tool_name": "evaluate_device",
            "credential_id": CRED,
            "node_id": "assessment",
        },
        200,
        {"X-Enterprise-Workspace": WS, "Cache-Control": "private, no-store"},
    )
    service.get_published_workflow.assert_not_called()
    call = service.get_published_workflow_by_id.call_args
    assert call.kwargs["workflow_id"] == WF
    assert call.kwargs["app_model"].id == APP
    assert call.kwargs["session"] is session.return_value
    session.return_value.commit.assert_not_called()


@pytest.mark.parametrize("header", list(HEADERS))
def test_each_partial_header_fails_before_service(native, header):
    with pytest.raises(BadRequest):
        read({header: HEADERS[header]})
    native[1].assert_not_called()


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Enterprise-Expected-Workspace", "00000000-0000-0000-0000-000000000000"),
        ("X-Enterprise-Expected-Workspace", ""),
        ("X-Enterprise-Expected-Workflow", WF.replace("-", "")),
        ("X-Enterprise-Expected-Workflow", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        ("X-Enterprise-Expected-Graph-Hash", "A" * 64),
        ("X-Enterprise-Expected-Graph-Hash", "a" * 63),
        ("X-Enterprise-Publication-Operation", "other"),
    ],
)
def test_invalid_headers_fail_before_service(native, header, value):
    with pytest.raises(BadRequest):
        read({**HEADERS, header: value})
    native[1].assert_not_called()


@pytest.mark.parametrize(("enabled", "tenant", "error"), [(False, WS, Forbidden), (True, APP, Conflict)])
def test_disabled_or_scope_mismatch_before_service(native, enabled, tenant, error):
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    with pytest.raises(error):
        read(HEADERS, tenant)
    native[1].assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", APP), ("tenant_id", APP), ("app_id", WF), ("version", "draft"), ("unique_hash", "b" * 64)],
)
def test_returned_publication_must_match_exact_scope(native, field, value):
    setattr(native[3], field, value)
    with pytest.raises(Conflict):
        read(HEADERS)


@pytest.mark.parametrize(
    "graph",
    [
        None,
        {},
        {"nodes": []},
        {"nodes": [None]},
        {"nodes": GRAPH["nodes"] * 2},
        {"nodes": GRAPH["nodes"] + [{"id": "other", "data": GRAPH["nodes"][0]["data"]}]},
    ],
)
def test_malformed_or_ambiguous_graph_rejected(native, graph):
    native[3].graph_dict = graph
    with pytest.raises(Conflict):
        read(HEADERS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "other"),
        ("provider_type", "api"),
        ("tool_name", "other"),
        ("type", "agent"),
        ("credential_id", ""),
        ("credential_id", "00000000-0000-0000-0000-000000000000"),
    ],
)
def test_exact_tool_and_canonical_credential_required(native, field, value):
    native[3].graph_dict["nodes"][0]["data"][field] = value
    with pytest.raises(Conflict):
        read(HEADERS)


def test_missing_exact_publication_is_not_found(native):
    native[0].get_published_workflow_by_id.return_value = None
    with pytest.raises(NotFound):
        read(HEADERS)


@pytest.mark.parametrize("enabled", [False, True])
def test_ordinary_get_unchanged(native, monkeypatch, enabled):
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    dump = MagicMock(return_value={"graph": "ordinary"})
    monkeypatch.setattr(controller, "dump_response", dump)
    assert read({}) == {"graph": "ordinary"}
    native[0].get_published_workflow_by_id.assert_not_called()
    dump.assert_called_once_with(controller.WorkflowResponse, native[0].get_published_workflow.return_value)
    native[0].get_published_workflow.return_value = None
    assert read({}) is None


def test_native_draft_lookup_exception_becomes_scope_conflict(native):
    native[0].get_published_workflow_by_id.side_effect = controller.IsDraftWorkflowError("draft")
    with pytest.raises(Conflict):
        read(HEADERS)


def test_assessment_node_id_is_exact_and_graph_unchanged(native):
    graph = native[3].graph_dict
    read(HEADERS)
    assert graph == GRAPH
    graph["nodes"][0]["id"] = "other"
    with pytest.raises(Conflict):
        read(HEADERS)


def test_publication_response_schema_registered():
    assert "AssessmentPublicationReadResponse" in controller.console_ns.models
