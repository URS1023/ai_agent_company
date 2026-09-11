import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from enterprise_platform.adapters.workflow_gateway import NativeWorkflowGateway, WorkflowCredential
from enterprise_platform.application.contracts import Run, RunSpec
from enterprise_platform.application.dispatcher import WorkflowPreparationFailed
from enterprise_platform.application.errors import DependencyUnavailable, NotFound, PersistenceError


def run_record() -> Run:
    return Run(
        id="run-1",
        workspace_id="w",
        actor_id="actor",
        request_key="key",
        payload_hash="digest",
        spec=RunSpec(
            binding_id="b",
            binding_revision=1,
            device_id="d",
            scenario="alert",
            app_id="app",
            workflow_id=UUID(int=1),
            specification_revision="v1",
            secret_ref="secret-ref",
            source_id="s",
            source_revision="s1",
            read_id="read",
            read_revision="r1",
        ),
        status="claimed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def credential() -> WorkflowCredential:
    return WorkflowCredential(workspace_id="w", app_id="app", secret_ref="secret-ref", api_key=SecretStr("fixture-key"))


def native_stream(native_id: str) -> httpx.Response:
    frames = []
    for event in ("workflow_started", "workflow_finished"):
        payload = {
            "event": event,
            "workflow_run_id": native_id,
            "task_id": "task-1",
            "data": {"id": native_id, "workflow_id": str(UUID(int=1)), "status": "succeeded", "outputs": None},
        }
        frames.append("data: " + json.dumps(payload) + "\n\n")
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(frames))


def test_gateway_uses_server_scoped_credential_and_pinned_native_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return native_stream("native-1")

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1", credentials=(credential(),), transport=httpx.MockTransport(handler)
    )
    result = gateway.run(run_record(), {"batch": "0001"}, on_started=lambda event: None)
    assert result.run_id == "native-1"
    assert requests[0].url.path == f"/v1/workflows/{UUID(int=1)}/run"
    assert requests[0].headers["Authorization"] == "Bearer fixture-key"
    assert b'"user":"actor"' in requests[0].content
    assert b"secret-ref" not in requests[0].content


@pytest.mark.parametrize("field", ["workspace_id", "app_id", "secret_ref"])
def test_credential_scope_mismatch_stops_before_transport(field: str) -> None:
    requests: list[httpx.Request] = []
    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credentials=(credential().model_copy(update={field: "other"}),),
        transport=httpx.MockTransport(lambda request: (requests.append(request), httpx.Response(500))[1]),
    )
    with pytest.raises(WorkflowPreparationFailed):
        gateway.run(run_record(), {}, on_started=lambda event: None)
    assert requests == []


def test_duplicate_credential_keys_are_rejected_at_configuration_time() -> None:
    with pytest.raises(ValueError):
        NativeWorkflowGateway(base_url="https://dify.test/v1", credentials=(credential(), credential()))


def test_reconcile_only_reads_existing_native_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "native-1", "workflow_id": str(UUID(int=1)), "status": "paused"})

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1", credentials=(credential(),), transport=httpx.MockTransport(handler)
    )
    result = gateway.get_run(run_record().model_copy(update={"dify_run_id": "native-1"}))
    assert result.status == "paused"
    assert [(request.method, request.url.path) for request in requests] == [("GET", "/v1/workflows/run/native-1")]


def test_missing_native_identity_fails_without_io() -> None:
    gateway = NativeWorkflowGateway(base_url="https://dify.test/v1", credentials=(credential(),))
    with pytest.raises(WorkflowPreparationFailed):
        gateway.get_run(run_record())


@pytest.mark.parametrize(
    "key", ["中文", " fixture-key", "fixture-key ", "fixture\tkey", "fixture\x00key", "fixture\x7fkey"]
)
def test_malformed_credentials_are_rejected_before_claim_or_transport(key: str) -> None:
    with pytest.raises(ValueError):
        WorkflowCredential(workspace_id="w", app_id="app", secret_ref="ref", api_key=SecretStr(key))


def test_existing_invalid_native_id_is_preparation_failure_not_raw_value_error() -> None:
    gateway = NativeWorkflowGateway(base_url="https://dify.test/v1", credentials=(credential(),))
    with pytest.raises(WorkflowPreparationFailed):
        gateway.get_run(run_record().model_copy(update={"dify_run_id": "native/invalid"}))


@pytest.mark.parametrize("native_id", ["native/invalid", "x" * 129, "native?other=1"])
def test_dispatch_rejects_native_identity_that_cannot_be_reconciled(native_id: str) -> None:
    from enterprise_platform.adapters.dify_workflows import DifyProtocolError

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credentials=(credential(),),
        transport=httpx.MockTransport(lambda request: native_stream(native_id)),
    )
    with pytest.raises(DifyProtocolError):
        gateway.run(run_record(), {}, on_started=lambda event: None)


class Resolver:
    def __init__(self, error: Exception | None = None, token: str = "vault-key") -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error = error
        self.token = token

    def resolve(self, workspace_id: str, app_id: str, secret_ref: str) -> SecretStr:
        self.calls.append((workspace_id, app_id, secret_ref))
        if self.error:
            raise self.error
        return SecretStr(self.token)


def test_vault_resolver_uses_exact_pinned_scope_for_dispatch_and_recovery() -> None:
    requests = []
    resolver = Resolver()

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return native_stream("native-1")
        return httpx.Response(200, json={"id": "native-1", "workflow_id": str(UUID(int=1)), "status": "paused"})

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1", credential_resolver=resolver, transport=httpx.MockTransport(handle)
    )
    gateway.run(run_record(), {}, on_started=lambda event: None)
    gateway.get_run(run_record().model_copy(update={"dify_run_id": "native-1"}))
    assert resolver.calls == [("w", "app", "secret-ref"), ("w", "app", "secret-ref")]
    assert [r.headers["Authorization"] for r in requests] == ["Bearer vault-key", "Bearer vault-key"]


@pytest.mark.parametrize("error_type", [NotFound, DependencyUnavailable, PersistenceError])
def test_vault_missing_revoked_or_corrupt_credential_stops_before_native_http(error_type) -> None:
    requests = []
    resolver = Resolver(error_type("sensitive-diagnostic"))
    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credential_resolver=resolver,
        transport=httpx.MockTransport(lambda request: (requests.append(request), native_stream("native-1"))[1]),
    )
    with pytest.raises(WorkflowPreparationFailed) as failure:
        gateway.run(run_record(), {}, on_started=lambda event: None)
    assert "sensitive" not in str(failure.value)
    assert requests == []


def test_vault_cannot_be_combined_with_silent_static_fallback() -> None:
    with pytest.raises(ValueError):
        NativeWorkflowGateway(
            base_url="https://dify.test/v1", credentials=(credential(),), credential_resolver=Resolver()
        )


def test_vault_returned_token_is_validated_before_transport() -> None:
    requests = []
    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credential_resolver=Resolver(token="bad\nkey"),
        transport=httpx.MockTransport(lambda request: (requests.append(request), native_stream("native-1"))[1]),
    )
    with pytest.raises(WorkflowPreparationFailed):
        gateway.run(run_record(), {}, on_started=lambda event: None)
    assert requests == []
