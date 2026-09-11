from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec
from uuid import UUID

import pytest

from enterprise_platform.adapters.dify_workflows import DifyRejected, DispatchUncertain, WorkflowResult, WorkflowStarted
from enterprise_platform.application.contracts import JsonObject, Run, RunSpec, canonical_hash
from enterprise_platform.application.dispatcher import RunDispatcher, WorkflowGateway, WorkflowPreparationFailed
from enterprise_platform.application.input_capture import encode_rows
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.domain.data_sources import FrozenRows, PageEvidence, SourceRef


def run_record(*, snapshot: bool = True) -> Run:
    captured = {
        "schema_version": 1,
        "device_id": "device-1",
        "parameters_digest": canonical_hash({}),
        "scope_column": "device",
        "scope_value": "device-1",
        "data": encode_rows(
            FrozenRows(
                SourceRef(workspace_id="workspace-1", source_id="s", revision="source-v1"),
                "read-1",
                "r",
                "1" * 64,
                datetime.now(UTC),
                ("device", "value"),
                (("device-1", Decimal("85.0001")),),
                (PageEvidence(1, 1, "2" * 64),),
            )
        ),
    }
    return Run(
        id="run-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        request_key="key-1",
        payload_hash="digest",
        spec=RunSpec(
            binding_id="binding-1",
            binding_revision=1,
            device_id="device-1",
            scenario="alert",
            app_id="app-1",
            workflow_id=UUID(int=1),
            specification_revision="spec-1",
            secret_ref="scoped-ref",
            source_id="s",
            source_revision="source-v1",
            read_id="read-1",
            read_revision="r",
        ),
        input_snapshot=captured if snapshot else None,
        status="claimed",
        dispatch_nonce="nonce-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def output(run: Run) -> WorkflowResult:
    return WorkflowResult(
        run_id="dify-run-1",
        task_id="task-1",
        workflow_id=UUID(int=1),
        status="succeeded",
        outputs={
            "result": {
                "run_id": run.id,
                "device_id": run.spec.device_id,
                "specification_revision": "spec-1",
                "input_snapshot_digest": canonical_hash(run.input_snapshot),
                "result": {
                    "scenario": "alert",
                    "conclusion": "issues",
                    "complete": True,
                    "evidence": {"value": "85.0001"},
                },
            }
        },
    )


def streamed_result(result: WorkflowResult) -> Callable[..., WorkflowResult]:
    def invoke(run: Run, inputs: JsonObject, *, on_started: Callable[[WorkflowStarted], None]) -> WorkflowResult:
        on_started(WorkflowStarted(run_id=result.run_id, task_id=result.task_id, workflow_id=result.workflow_id))
        return result

    return invoke


def test_dispatch_claims_once_and_commits_verified_result_without_exposing_nonce_to_workflow() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record()
    repo.claim_run.return_value = repo.get_run.return_value = repo.mark_dispatched.return_value = run
    gateway.run.side_effect = streamed_result(output(run))
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    repo.claim_run.assert_called_once()
    gateway.run.assert_called_once()
    inputs = gateway.run.call_args.args[1]
    assert "nonce-1" not in str(inputs)
    assert "scoped-ref" not in str(inputs)
    assert repo.complete_run.call_args.args[3].conclusion == "issues"


def test_timeout_stays_uncertain_and_is_not_retried_or_committed_as_business_failure() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    repo.claim_run.return_value = run_record()
    gateway.run.side_effect = DispatchUncertain("interrupted")
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    gateway.run.assert_called_once()
    repo.mark_uncertain.assert_called_once()
    repo.complete_run.assert_not_called()
    repo.fail_run.assert_not_called()


def test_definite_native_rejection_fails_execution_without_business_verdict() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    repo.claim_run.return_value = run_record()
    gateway.run.side_effect = DifyRejected(403)
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    assert repo.fail_run.call_args.args[3] == "dify_rejected_403"
    repo.complete_run.assert_not_called()


def test_preparation_failure_releases_claim_without_calling_it_a_business_verdict() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    repo.claim_run.return_value = run_record()
    gateway.run.side_effect = WorkflowPreparationFailed("Missing server configuration")
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    assert repo.fail_run.call_args.args[3] == "workflow_configuration_unavailable"
    repo.mark_dispatched.assert_not_called()
    repo.complete_run.assert_not_called()


def test_gateway_workflow_identity_mismatch_keeps_lane_uncertain_without_accepting_native_id() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    repo.claim_run.return_value = repo.get_run.return_value = run_record()
    gateway.run.side_effect = streamed_result(output(run_record()).model_copy(update={"workflow_id": UUID(int=2)}))
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    repo.mark_uncertain.assert_called_once()
    repo.mark_dispatched.assert_not_called()
    repo.complete_run.assert_not_called()


def test_reconcile_never_replaces_known_native_run_identity() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record().model_copy(update={"status": "dispatched", "dify_run_id": "known-native"})
    repo.get_run.return_value = run
    gateway.get_run.return_value = output(run)
    RunDispatcher(repo, gateway).reconcile("workspace-1", "run-1")
    repo.mark_uncertain.assert_called_once()
    repo.mark_dispatched.assert_not_called()
    repo.complete_run.assert_not_called()
    gateway.run.assert_not_called()


@pytest.mark.parametrize("conclusion", ["normal", "issues"])
def test_empty_source_rows_never_become_normal_or_findings(conclusion: str) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record()
    snapshot = run.model_dump(mode="json")["input_snapshot"]
    snapshot["data"]["rows"] = []
    snapshot["data"]["pages"][0]["row_count"] = 0
    run = run.model_copy(update={"input_snapshot": snapshot})
    repo.claim_run.return_value = repo.get_run.return_value = run
    response = output(run).model_dump()
    response["outputs"]["result"]["result"]["conclusion"] = conclusion
    gateway.run.side_effect = streamed_result(WorkflowResult.model_validate(response))
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    assert repo.fail_run.call_args.args[3] == "invalid_business_output"
    repo.complete_run.assert_not_called()


def test_empty_source_rows_can_complete_execution_with_explicit_no_data_conclusion() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record()
    snapshot = run.model_dump(mode="json")["input_snapshot"]
    snapshot["data"]["rows"] = []
    snapshot["data"]["pages"][0]["row_count"] = 0
    run = run.model_copy(update={"input_snapshot": snapshot})
    repo.claim_run.return_value = repo.get_run.return_value = run
    response = output(run).model_dump()
    response["outputs"]["result"]["result"] = {"scenario": "alert", "conclusion": "no_data", "complete": False}
    gateway.run.side_effect = streamed_result(WorkflowResult.model_validate(response))
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    assert repo.complete_run.call_args.args[3].conclusion == "no_data"
    repo.fail_run.assert_not_called()


def test_matching_digest_does_not_make_malformed_snapshot_trustworthy() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record().model_copy(update={"input_snapshot": {"invented": "snapshot"}})
    repo.claim_run.return_value = repo.get_run.return_value = run
    gateway.run.side_effect = streamed_result(output(run))
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    assert repo.fail_run.call_args.args[3] == "invalid_business_output"
    repo.complete_run.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other-run"),
        ("device_id", "other-device"),
        ("specification_revision", "old"),
        ("input_snapshot_digest", "wrong"),
    ],
)
def test_mismatched_final_output_never_enters_reports(field: str, value: str) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record()
    repo.claim_run.return_value = repo.get_run.return_value = repo.mark_dispatched.return_value = run
    native = output(run)
    body = native.outputs["result"]
    assert isinstance(body, dict)
    body[field] = value
    gateway.run.side_effect = streamed_result(native)
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    repo.complete_run.assert_not_called()
    assert repo.fail_run.call_args.args[3] == "invalid_business_output"


def test_missing_captured_input_never_becomes_a_report() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    gateway = create_autospec(WorkflowGateway, instance=True, spec_set=True)
    run = run_record(snapshot=False)
    repo.claim_run.return_value = repo.get_run.return_value = repo.mark_dispatched.return_value = run
    gateway.run.side_effect = streamed_result(output(run))
    RunDispatcher(repo, gateway, nonce_factory=lambda: "nonce-1").dispatch("workspace-1", "run-1")
    repo.complete_run.assert_not_called()
    assert repo.fail_run.call_args.args[3] == "missing_input_snapshot"
