import asyncio
import json

import httpx
import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import receipt

from enterprise_platform.adapters.workbench_context_client import ContextCheckRejected, DifyWorkbenchContextClient


def body():
    return {
        "workspace_id": "w",
        "actor_id": "actor",
        "installed_app_id": str(receipt().scope.installed_app_id),
        "conversation_id": str(receipt().conversation_id),
        "message_id": str(receipt().message_id),
        "message_status": "normal",
        "workflow_run_id": "00000000-0000-0000-0000-000000000005",
        "workflow_status": "succeeded",
        "workflow_finished": True,
    }


def read(handler, **options):
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(client.read_generation_state(PRINCIPAL, SESSION, receipt()))


def test_reads_only_expected_message_with_same_native_identity():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body())

    result = read(handler)
    assert result.workflow_status == "succeeded"
    assert result.workflow_finished is True
    assert len(calls) == 1
    assert calls[0].url.path.endswith("/enterprise/workbench/generation-state")
    assert json.loads(calls[0].content) == {
        "conversation_id": str(receipt().conversation_id),
        "message_id": str(receipt().message_id),
    }
    assert calls[0].headers["X-Enterprise-Expected-Actor"] == "actor"
    assert "refresh_token" not in calls[0].headers["cookie"]


@pytest.mark.parametrize("field", ["workspace_id", "actor_id", "installed_app_id", "conversation_id", "message_id"])
def test_every_returned_identity_must_match(field):
    with pytest.raises(ContextCheckRejected):
        read(lambda _: httpx.Response(200, json={**body(), field: "other"}))


@pytest.mark.parametrize(
    "change",
    [
        {"message_status": "paused"},
        {"workflow_status": "running"},
        {"workflow_status": "paused"},
        {"workflow_run_id": None},
        {"workflow_finished": 1},
        {"workflow_run_id": "not-uuid"},
    ],
)
def test_inconsistent_finished_projection_is_rejected(change):
    with pytest.raises(ContextCheckRejected):
        read(lambda _: httpx.Response(200, json={**body(), **change}))


def test_plain_normal_message_is_returned_as_unproven():
    result = read(
        lambda _: httpx.Response(
            200, json={**body(), "workflow_run_id": None, "workflow_status": None, "workflow_finished": False}
        )
    )
    assert result.workflow_finished is False


@pytest.mark.parametrize("status", [302, 403, 404, 500])
def test_http_failure_never_retries_or_follows_redirect(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.invalid"})

    with pytest.raises(ContextCheckRejected):
        read(handler)
    assert len(calls) == 1


def test_response_limit_applies_to_state_reads():
    with pytest.raises(ContextCheckRejected):
        read(lambda _: httpx.Response(200, content=b"x" * 2048), max_response_bytes=1024)


@pytest.mark.parametrize("field", ["workspace_id", "actor_id"])
def test_foreign_principal_is_rejected_before_network(field):
    calls = []
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    principal = PRINCIPAL.model_copy(update={field: "other"})
    with pytest.raises(ContextCheckRejected):
        asyncio.run(client.read_generation_state(principal, SESSION, receipt()))
    assert calls == []


@pytest.mark.parametrize("status", ["succeeded", "partial-succeeded", "failed", "stopped"])
def test_preserves_each_native_terminal_outcome_without_synthesizing_receipt(status):
    result = read(lambda _: httpx.Response(200, json={**body(), "workflow_status": status}))
    assert result.workflow_status == status
    assert "receipt" not in result.model_fields_set


def test_terminal_status_without_finished_timestamp_stays_unproven():
    result = read(lambda _: httpx.Response(200, json={**body(), "workflow_finished": False}))
    assert result.workflow_finished is False


def test_scheduled_workflow_is_a_valid_nonterminal_observation():
    result = read(
        lambda _: httpx.Response(200, json={**body(), "workflow_status": "scheduled", "workflow_finished": False})
    )
    assert result.workflow_status == "scheduled"
    assert result.workflow_finished is False


@pytest.mark.parametrize("content", [b"not-json", b"[]", b"{}"])
def test_malformed_response_is_opaque(content):
    with pytest.raises(ContextCheckRejected):
        read(lambda _: httpx.Response(200, content=content))


def test_transport_timeout_is_opaque_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private upstream details", request=request)

    with pytest.raises(ContextCheckRejected) as caught:
        read(handler)
    assert "private upstream details" not in str(caught.value)
    assert len(calls) == 1


def plain_body(**marker_changes):
    return {
        **body(),
        "workflow_run_id": None,
        "workflow_status": None,
        "workflow_finished": False,
        "message_terminal": {
            "version": 1,
            "task_id": receipt().task_id,
            "outcome": "succeeded",
            "stop_reason": None,
            **marker_changes,
        },
    }


def test_plain_terminal_metadata_is_read_as_scoped_evidence():
    result = read(lambda _: httpx.Response(200, json=plain_body()))
    assert result.message_terminal.task_id == receipt().task_id
    assert result.message_terminal.outcome == "succeeded"


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"task_id": "other"},
        {"outcome": "stopped"},
        {"outcome": "succeeded", "stop_reason": "user_manual"},
    ],
)
def test_invalid_or_foreign_plain_terminal_is_rejected(changes):
    with pytest.raises(ContextCheckRejected):
        read(lambda _: httpx.Response(200, json=plain_body(**changes)))


def test_failed_plain_terminal_requires_native_error_status():
    failed = {**plain_body(outcome="failed"), "message_status": "error"}
    result = read(lambda _: httpx.Response(200, json=failed))
    assert result.message_terminal.outcome == "failed"
    with pytest.raises(ContextCheckRejected):
        read(lambda _: httpx.Response(200, json={**failed, "message_status": "normal"}))
