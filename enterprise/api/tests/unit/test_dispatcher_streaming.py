import json
from collections.abc import Callable, Iterator
from unittest.mock import create_autospec
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from test_dify_streaming import Chunks, frame
from test_dispatcher import run_record

from enterprise_platform.adapters.workflow_gateway import NativeWorkflowGateway, WorkflowCredential
from enterprise_platform.application.contracts import JsonObject, Run
from enterprise_platform.application.dispatcher import RunDispatcher
from enterprise_platform.application.ports import EnterpriseRepository


def test_native_started_is_associated_before_consuming_later_frames_and_survives_timeout() -> None:
    current = run_record()
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.claim_run.return_value = current

    def associate(ws: str, run_id: str, nonce: str, native_id: str, *, actor_id: str) -> Run:
        nonlocal current
        assert (ws, run_id, nonce) == (current.workspace_id, current.id, "nonce-1")
        current = current.model_copy(update={"status": "dispatched", "dify_run_id": native_id})
        return current

    def uncertain(ws: str, run_id: str, nonce: str, reason: str, *, actor_id: str) -> Run:
        nonlocal current
        current = current.model_copy(update={"status": "uncertain", "reason_code": reason})
        return current

    repo.mark_dispatched.side_effect = associate
    repo.mark_uncertain.side_effect = uncertain

    class TimedOut(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            yield frame("workflow_started", workflow_id=str(current.spec.workflow_id))
            assert current.dify_run_id == "run-1"
            assert current.status == "dispatched"
            raise httpx.ReadTimeout("private key and native detail")

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=TimedOut(()))

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credentials=(
            WorkflowCredential(
                workspace_id=current.workspace_id,
                app_id=current.spec.app_id,
                secret_ref=current.spec.secret_ref,
                api_key=SecretStr("test-key"),
            ),
        ),
        transport=httpx.MockTransport(handle),
    )
    result = RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch(current.workspace_id, current.id)

    assert result.status == "uncertain"
    assert result.dify_run_id == "run-1"
    repo.mark_dispatched.assert_called_once()
    repo.complete_run.assert_not_called()
    repo.fail_run.assert_not_called()
    assert len(requests) == 1
    assert b"nonce-1" not in requests[0].content
    assert b"test-key" not in requests[0].content


@pytest.mark.parametrize("case", ["missing", "duplicate", "wrong_workflow", "wrong_terminal"])
def test_dispatcher_does_not_trust_gateway_result_without_unique_matching_started(case: str) -> None:
    from test_dispatcher import output

    from enterprise_platform.adapters.dify_workflows import WorkflowResult, WorkflowStarted
    from enterprise_platform.application.dispatcher import WorkflowGateway

    run = run_record()
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    repo.claim_run.return_value = repo.get_run.return_value = repo.mark_dispatched.return_value = run
    native = output(run)

    def invoke(record: Run, inputs: JsonObject, *, on_started: Callable[[WorkflowStarted], None]) -> WorkflowResult:
        started = WorkflowStarted(run_id=native.run_id, task_id=native.task_id, workflow_id=native.workflow_id)
        if case == "wrong_workflow":
            started = started.model_copy(update={"workflow_id": UUID(int=2)})
        if case != "missing":
            on_started(started)
        if case == "duplicate":
            on_started(started)
        return native.model_copy(update={"run_id": "wrong"}) if case == "wrong_terminal" else native

    gateway.run.side_effect = invoke
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch(run.workspace_id, run.id)
    repo.mark_uncertain.assert_called_once()
    repo.complete_run.assert_not_called()
    repo.fail_run.assert_not_called()
    assert repo.mark_dispatched.call_count == (1 if case in {"duplicate", "wrong_terminal"} else 0)


@pytest.mark.parametrize("duplicate_terminal", [False, True])
def test_real_gateway_final_is_committed_once_only_after_unique_complete_stream(duplicate_terminal: bool) -> None:
    from test_dispatcher import output

    current = run_record()
    native = output(current)
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.claim_run.return_value = current

    def associate(ws: str, run_id: str, nonce: str, native_id: str, *, actor_id: str) -> Run:
        nonlocal current
        current = current.model_copy(update={"status": "dispatched", "dify_run_id": native_id})
        return current

    repo.mark_dispatched.side_effect = associate
    repo.get_run.side_effect = lambda *args: current
    terminal = (
        "data: "
        + json.dumps(
            {
                "event": "workflow_finished",
                "workflow_run_id": native.run_id,
                "task_id": native.task_id,
                "data": {
                    "id": native.run_id,
                    "workflow_id": str(native.workflow_id),
                    "status": native.status,
                    "outputs": native.outputs,
                },
            }
        )
        + "\n\n"
    ).encode()

    class Ordered(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            yield frame("workflow_started", native_id=native.run_id, workflow_id=str(native.workflow_id))
            assert current.dify_run_id == native.run_id
            repo.mark_dispatched.assert_called_once()
            repo.complete_run.assert_not_called()
            yield terminal
            repo.complete_run.assert_not_called()
            if duplicate_terminal:
                yield terminal

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credentials=(
            WorkflowCredential(
                workspace_id=current.workspace_id,
                app_id=current.spec.app_id,
                secret_ref=current.spec.secret_ref,
                api_key=SecretStr("test-key"),
            ),
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Ordered(()))
        ),
    )
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch(current.workspace_id, current.id)
    repo.mark_dispatched.assert_called_once()
    repo.fail_run.assert_not_called()
    if duplicate_terminal:
        repo.mark_uncertain.assert_called_once()
        repo.complete_run.assert_not_called()
    else:
        repo.complete_run.assert_called_once()
        repo.mark_uncertain.assert_not_called()


def test_failed_started_cas_aborts_stream_and_keeps_execution_uncertain() -> None:
    from enterprise_platform.application.errors import Conflict

    current = run_record()
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.claim_run.return_value = current
    repo.mark_dispatched.side_effect = Conflict("private detail")

    class NoFurtherRead(Chunks):
        def __iter__(self) -> Iterator[bytes]:
            yield frame("workflow_started", workflow_id=str(current.spec.workflow_id))
            pytest.fail("failed association must abort consumption")

    gateway = NativeWorkflowGateway(
        base_url="https://dify.test/v1",
        credentials=(
            WorkflowCredential(
                workspace_id=current.workspace_id,
                app_id=current.spec.app_id,
                secret_ref=current.spec.secret_ref,
                api_key=SecretStr("test-key"),
            ),
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=NoFurtherRead(()))
        ),
    )
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch(current.workspace_id, current.id)
    repo.mark_dispatched.assert_called_once()
    repo.mark_uncertain.assert_called_once()
    repo.complete_run.assert_not_called()
    repo.fail_run.assert_not_called()
