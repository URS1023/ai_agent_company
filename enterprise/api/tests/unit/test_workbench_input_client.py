import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_messages import intent

from enterprise_platform.adapters.workbench_context_client import ContextCheckRejected, DifyWorkbenchContextClient
from enterprise_platform.application.workbench_branches import create_root_branch
from enterprise_platform.application.workbench_messages import claim_message, create_message_intent
from enterprise_platform.application.workbench_service import WorkbenchSendAuthority, WorkbenchSendService


def body():
    return {
        "workspace_id": "w",
        "actor_id": "actor",
        "installed_app_id": str(intent().scope.installed_app_id),
        "inputs_verified": True,
        "selected_files_verified": False,
        "branch_verified": False,
    }


def check(handler, command=None, **options):
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(handler), **options
    )
    return asyncio.run(client.check_inputs(PRINCIPAL, SESSION, command or intent()))


def test_only_inputs_and_context_are_forwarded_without_generation_fields():
    original = intent()
    values = {"document": {"transfer_method": "local_file", "upload_file_id": "selected"}, "number": "0"}
    command = create_message_intent(
        original.scope,
        original.client_message_id,
        {
            "inputs": values,
            "query": "private prompt",
            "files": [{"private": "selection"}],
            "conversation_id": "00000000-0000-0000-0000-000000000003",
            "parent_message_id": "",
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body())

    assert check(handler, command) is None
    assert json.loads(calls[0].content) == {
        "inputs": values,
        "conversation_id": "00000000-0000-0000-0000-000000000003",
        "parent_message_id": "",
    }
    assert calls[0].url.path.endswith("/enterprise/workbench/input-check")
    assert "refresh_token" not in calls[0].headers["cookie"]
    assert calls[0].headers["X-Enterprise-Expected-Actor"] == "actor"


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "other"},
        {"actor_id": "other"},
        {"installed_app_id": "other"},
        {"inputs_verified": False},
        {"inputs_verified": 1},
        {"selected_files_verified": True},
        {"branch_verified": True},
    ],
)
def test_identity_and_scope_claims_must_match(change):
    with pytest.raises(ContextCheckRejected):
        check(lambda _: httpx.Response(200, json={**body(), **change}))


@pytest.mark.parametrize("inputs", [None, [], "bad"])
def test_invalid_inputs_stop_before_network(inputs):
    calls = []
    original = intent()
    command = create_message_intent(original.scope, original.client_message_id, {"inputs": inputs})
    with pytest.raises(ContextCheckRejected):
        check(lambda request: calls.append(request), command)
    assert calls == []


@pytest.mark.parametrize("status", [302, 403, 500])
def test_failure_is_not_retried_or_redirected(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.invalid"})

    with pytest.raises(ContextCheckRejected):
        check(handler)
    assert len(calls) == 1


def test_response_limit_is_enforced():
    with pytest.raises(ContextCheckRejected):
        check(lambda _: httpx.Response(200, content=b"x" * 2048), max_response_bytes=1024)


@pytest.mark.parametrize("input_status", [200, 403])
def test_real_clients_and_authority_gate_send_preparation(input_status):
    paths = []
    branch = create_root_branch(intent().scope)
    branches = Mock()
    branches.get.return_value = branch
    ledger = Mock()
    ledger.create_or_get.return_value = intent()
    ledger.claim.return_value = claim_message(intent())

    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        paths.append(path)
        identity = {"workspace_id": "w", "actor_id": "actor", "installed_app_id": str(intent().scope.installed_app_id)}
        if path == "context-check":
            return httpx.Response(
                200,
                json={**identity, "context_verified": True, "attachments_verified": False, "branch_verified": False},
            )
        if path == "attachment-check":
            return httpx.Response(
                200,
                json={
                    **identity,
                    "file_count": 0,
                    "selected_files_verified": True,
                    "input_files_verified": False,
                    "branch_verified": False,
                },
            )
        assert path == "input-check"
        branches.get.assert_called_once_with(intent().scope)
        assert not ledger.mock_calls
        return httpx.Response(input_status, json=body())

    client = DifyWorkbenchContextClient(base_url="https://native/console/api", transport=httpx.MockTransport(handler))
    service = WorkbenchSendService(ledger, WorkbenchSendAuthority(branches, client), client)

    async def prepare():
        return await service.prepare(
            PRINCIPAL,
            SESSION,
            installed_app_id=intent().scope.installed_app_id,
            branch_id="branch",
            client_message_id=intent().client_message_id,
            payload=json.loads(intent().payload_json),
        )

    if input_status == 200:
        assert asyncio.run(prepare()).dispatch_claimed is True
        ledger.claim.assert_called_once()
    else:
        with pytest.raises(ContextCheckRejected):
            asyncio.run(prepare())
        assert not ledger.mock_calls
    assert paths == ["context-check", "attachment-check", "input-check"]


@pytest.mark.parametrize("field", ["workspace_id", "actor_id"])
def test_foreign_principal_is_rejected_before_any_http(field):
    calls = []
    client = DifyWorkbenchContextClient(
        base_url="https://native/console/api", transport=httpx.MockTransport(lambda request: calls.append(request))
    )
    with pytest.raises(ContextCheckRejected):
        asyncio.run(client.check_inputs(PRINCIPAL.model_copy(update={field: "other"}), SESSION, intent()))
    assert calls == []
