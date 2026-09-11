import asyncio
import json

import httpx
import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import intent

from enterprise_platform.adapters.workbench_context_client import ContextCheckRejected, DifyWorkbenchContextClient
from enterprise_platform.application.workbench_messages import create_message_intent


def body(count=1):
    return {
        "workspace_id": "w",
        "actor_id": "actor",
        "installed_app_id": str(intent().scope.installed_app_id),
        "file_count": count,
        "selected_files_verified": True,
        "input_files_verified": False,
        "branch_verified": False,
    }


def requested(files):
    original = intent()
    return create_message_intent(
        original.scope,
        original.client_message_id,
        {"query": "private prompt", "inputs": {"file": "private input"}, "files": files},
    )


def check(handler, *, command=None, **options):
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(
        client.check_selected_attachments(
            PRINCIPAL, SESSION, command or requested([{"transfer_method": "local_file", "upload_file_id": "selected"}])
        )
    )


def test_only_selected_files_cross_boundary_without_prompt_or_input_values():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body())

    assert check(handler) is None
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {"files": [{"transfer_method": "local_file", "upload_file_id": "selected"}]}
    assert calls[0].url.path.endswith("/enterprise/workbench/attachment-check")
    assert calls[0].headers["X-Enterprise-Expected-Actor"] == "actor"
    assert "refresh_token" not in calls[0].headers["cookie"]


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "other"},
        {"actor_id": "other"},
        {"installed_app_id": "other"},
        {"file_count": 0},
        {"file_count": True},
        {"selected_files_verified": False},
        {"selected_files_verified": 1},
        {"input_files_verified": True},
        {"branch_verified": True},
    ],
)
def test_rejects_mismatched_identity_count_or_authority_claim(change):
    with pytest.raises(ContextCheckRejected):
        check(lambda _: httpx.Response(200, json={**body(), **change}))


@pytest.mark.parametrize("files", [[], None])
def test_empty_or_null_files_are_checked_as_empty_selection(files):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body(0))

    check(handler, command=requested(files))
    assert json.loads(calls[0].content) == {"files": []}


@pytest.mark.parametrize("files", ["wrong", [1], [{}, "wrong"]])
def test_bad_file_shapes_stop_before_network(files):
    calls = []
    with pytest.raises(ContextCheckRejected):
        check(lambda request: calls.append(request), command=requested(files))
    assert calls == []


@pytest.mark.parametrize("status", [302, 403, 500])
def test_native_failure_is_not_retried_or_redirected(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.invalid"})

    with pytest.raises(ContextCheckRejected):
        check(handler)
    assert len(calls) == 1


def test_response_bound_is_applied():
    with pytest.raises(ContextCheckRejected):
        check(lambda _: httpx.Response(200, content=b"x" * 2048), max_response_bytes=1024)


def test_conversation_and_parent_are_forwarded_without_mutating_snapshot():
    original = intent()
    payload = {"files": [], "conversation_id": "00000000-0000-0000-0000-000000000003", "parent_message_id": ""}
    command = create_message_intent(original.scope, original.client_message_id, payload)
    before = command.payload_json
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body(0))

    check(handler, command=command)
    assert json.loads(calls[0].content) == payload
    assert command.payload_json == before


@pytest.mark.parametrize("field", ["workspace_id", "actor_id"])
def test_foreign_identity_stops_before_network(field):
    calls = []
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(lambda request: calls.append(request))
    )
    with pytest.raises(ContextCheckRejected):
        asyncio.run(client.check_selected_attachments(PRINCIPAL.model_copy(update={field: "other"}), SESSION, intent()))
    assert calls == []


def test_transport_timeout_is_opaque_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private upstream details", request=request)

    with pytest.raises(ContextCheckRejected) as caught:
        check(handler)
    assert "private upstream details" not in str(caught.value)
    assert len(calls) == 1
