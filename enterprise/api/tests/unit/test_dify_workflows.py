import json
from collections.abc import Callable
from typing import TypedDict
from uuid import UUID

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from enterprise_platform.adapters.dify_workflows import (
    DifyProtocolError,
    DifyRejected,
    DifyWorkflowClient,
    DispatchUncertain,
    WorkflowBinding,
)

WORKFLOW_ID = UUID("f045ecf1-5c4d-4396-9e94-caa9cc265057")


class RunDataPayload(TypedDict):
    id: str
    workflow_id: str
    status: str
    outputs: dict[str, JsonValue] | None


class BlockingPayload(TypedDict):
    workflow_run_id: str
    task_id: str
    data: RunDataPayload


def binding(workspace_id: str = "workspace-1", app_id: str = "app-1") -> WorkflowBinding:
    return WorkflowBinding(workspace_id, "device-0001", "alert", app_id, WORKFLOW_ID, "spec-v1")


def response_payload(status: str = "succeeded", workflow_id: str = str(WORKFLOW_ID)) -> BlockingPayload:
    return {
        "workflow_run_id": "run-1",
        "task_id": "task-1",
        "data": {
            "id": "run-1",
            "workflow_id": workflow_id,
            "status": status,
            "outputs": {"result": '{"verdict":"alert"}'},
        },
    }


def client(handler: Callable[[httpx.Request], httpx.Response]) -> DifyWorkflowClient:
    return DifyWorkflowClient(
        base_url="http://dify.internal:5001/v1",
        workspace_id="workspace-1",
        app_id="app-1",
        api_key=SecretStr("app-private-key"),
        transport=httpx.MockTransport(handler),
    )


def test_executes_only_pinned_workflow_and_preserves_input_ids() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response_payload())

    with client(handle) as adapter:
        result = adapter.run(binding(), actor_id="actor-1", inputs={"device_id": "0001", "value": "85.0000000000001"})

    assert result.workflow_id == WORKFLOW_ID
    assert result.run_id == "run-1"
    assert result.status == "succeeded"
    assert requests[0].url.path == f"/v1/workflows/{WORKFLOW_ID}/run"
    assert requests[0].headers["authorization"] == "Bearer app-private-key"
    payload = json.loads(requests[0].content)
    assert payload["inputs"]["device_id"] == "0001"
    assert payload["response_mode"] == "blocking"
    assert payload["user"] == "actor-1"


@pytest.mark.parametrize("workspace_id,app_id", [("other", "app-1"), ("workspace-1", "other")])
def test_binding_must_match_configured_workspace_and_application(workspace_id: str, app_id: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("mismatched binding must never send a request")

    with client(handle) as adapter, pytest.raises(ValueError, match="binding"):
        adapter.run(binding(workspace_id, app_id), actor_id="actor-1", inputs={})


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429])
def test_rejection_does_not_fall_back_to_latest_or_retry(code: int) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(code, json={"message": "private database string app-private-key"})

    with client(handle) as adapter, pytest.raises(DifyRejected) as error:
        adapter.run(binding(), actor_id="actor-1", inputs={})
    assert error.value.status_code == code
    assert "private" not in str(error.value)
    assert calls == 1


@pytest.mark.parametrize("failure", ["timeout", "server-error", "redirect"])
def test_ambiguous_dispatch_is_not_reported_as_business_failure_or_retried(failure: str) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("credential-like upstream detail", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "http://other.example/"})
        return httpx.Response(503)

    with client(handle) as adapter, pytest.raises(DispatchUncertain) as error:
        adapter.run(binding(), actor_id="actor-1", inputs={})
    assert "credential" not in str(error.value)
    assert calls == 1


def test_returned_workflow_revision_must_match_pinned_revision() -> None:
    with client(lambda request: httpx.Response(200, json=response_payload(workflow_id=str(UUID(int=1))))) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run(binding(), actor_id="actor-1", inputs={})


@pytest.mark.parametrize(
    "payload",
    [{}, {"data": {"status": "success"}}, {"workflow_run_id": "wrong", **{"data": response_payload()["data"]}}],
)
def test_malformed_response_never_becomes_a_success(payload: dict[str, JsonValue]) -> None:
    with client(lambda request: httpx.Response(200, json=payload)) as adapter, pytest.raises(DifyProtocolError):
        adapter.run(binding(), actor_id="actor-1", inputs={})


def test_failed_execution_remains_distinct_from_a_successful_business_verdict() -> None:
    with client(lambda request: httpx.Response(200, json=response_payload(status="failed"))) as adapter:
        result = adapter.run(binding(), actor_id="actor-1", inputs={})
    assert result.status == "failed"
    assert result.outputs == {}


def test_run_detail_validates_binding_and_run_identity() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=response_payload()["data"])

    with client(handle) as adapter:
        assert adapter.get_run(binding(), "run-1").run_id == "run-1"
    assert seen == ["/v1/workflows/run/run-1"]


def test_oversized_response_is_rejected() -> None:
    with client(lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))) as adapter:
        with pytest.raises(DifyProtocolError):
            adapter.run(binding(), actor_id="actor-1", inputs={})


@pytest.mark.parametrize("status", ["failed", "stopped", "paused", "succeeded"])
def test_native_null_outputs_preserve_execution_status(status: str) -> None:
    payload = response_payload(status=status)
    payload["data"]["outputs"] = None
    with client(lambda request: httpx.Response(200, json=payload)) as adapter:
        result = adapter.run(binding(), actor_id="actor-1", inputs={})
    assert result.status == status
    assert result.outputs == {}


def test_invalid_compression_is_a_protocol_error_without_sensitive_detail() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("app-private-key upstream content", request=request)

    with client(handle) as adapter, pytest.raises(DifyProtocolError) as error:
        adapter.run(binding(), actor_id="actor-1", inputs={})
    assert "private" not in str(error.value)
