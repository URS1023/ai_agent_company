from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID

import pytest

from enterprise_platform.application.contracts import Binding, JsonObject, Principal, Run, RunSpec
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.application.service import BusinessService, RunRequest


def principal(role: str = "editor") -> Principal:
    return Principal.model_validate({"actor_id": "a", "workspace_id": "w", "workspace_role": role, "display_name": "A"})


def binding() -> Binding:
    return Binding(
        id="b",
        workspace_id="w",
        device_id="d",
        scenario="alert",
        revision=2,
        app_id="app",
        workflow_id=UUID(int=1),
        specification_revision="spec-1",
        secret_ref="key-ref",
        source_id="source-1",
        source_revision="source-v1",
        read_id="read-1",
        read_revision="read-1",
        manifest={"input_keys": ["batch"]},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_enqueue_freezes_binding_and_uses_server_identity_not_input_identity() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    service = BusinessService(repo)
    service.enqueue(
        principal(), "d", "alert", "request-1", RunRequest(expected_binding_revision=2, parameters={"batch": "0001"})
    )
    args, kwargs = repo.enqueue_run.call_args
    assert args[0] == "w"
    assert args[3].workflow_id == UUID(int=1)
    assert args[3].parameters == {"batch": "0001"}
    assert args[4] is None
    assert kwargs["actor_id"] == "a"


def test_readonly_member_is_denied_before_repository_mutation() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    with pytest.raises(AccessDenied):
        BusinessService(repo).enqueue(
            principal("normal"), "d", "alert", "request-1", RunRequest(expected_binding_revision=2)
        )
    repo.get_binding.assert_not_called()
    repo.enqueue_run.assert_not_called()


def test_stale_binding_does_not_silently_execute_new_revision() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    with pytest.raises(Conflict):
        BusinessService(repo).enqueue(principal(), "d", "alert", "request-1", RunRequest(expected_binding_revision=1))
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize(
    "parameters", [{"device_id": "forged"}, {"enterprise_context": "forged"}, {"batch": float("nan")}]
)
def test_unregistered_or_nonfinite_parameters_are_rejected(parameters: JsonObject) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    with pytest.raises((InvalidInput, ValueError)):
        BusinessService(repo).enqueue(
            principal(), "d", "alert", "request-1", RunRequest(expected_binding_revision=2, parameters=parameters)
        )
    repo.enqueue_run.assert_not_called()


def test_recompute_uses_old_captured_input_and_never_reads_a_source() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    from enterprise_platform.application.contracts import RunSpec

    repo.get_run.return_value = Run(
        id="old",
        workspace_id="w",
        actor_id="a",
        request_key="old-key",
        payload_hash="hash",
        spec=RunSpec.from_binding(binding()),
        input_snapshot={"snapshot_id": "captured-1", "value": "85.0001"},
        status="succeeded",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    BusinessService(repo).enqueue(
        principal(), "d", "alert", "new-key", RunRequest(expected_binding_revision=2, recompute_of="old")
    )
    args, _ = repo.enqueue_run.call_args
    assert args[4] == {"snapshot_id": "captured-1", "value": "85.0001"}
    repo.get_run.assert_called_once_with("w", "old")


def previous_run() -> Run:
    return Run(
        id="old",
        workspace_id="w",
        actor_id="a",
        request_key="old-key",
        payload_hash="hash",
        spec=RunSpec.model_validate({**RunSpec.from_binding(binding()).model_dump(), "parameters": {"batch": "old"}}),
        input_snapshot={"rows": [{"batch": "old", "value": "85.0001"}]},
        status="succeeded",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_recompute_inherits_acquisition_parameters_and_records_ancestry() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    repo.get_run.return_value = previous_run()
    BusinessService(repo).enqueue(
        principal(), "d", "alert", "new", RunRequest(expected_binding_revision=2, recompute_of="old")
    )
    spec = repo.enqueue_run.call_args.args[3]
    assert spec.parameters == {"batch": "old"}
    assert spec.recompute_of == "old"


@pytest.mark.parametrize("changed_field", ["source_revision", "read_id", "read_revision"])
def test_recompute_rejects_changed_read_identity(changed_field: str) -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding().model_copy(update={changed_field: "different"})
    repo.get_run.return_value = previous_run()
    with pytest.raises(InvalidInput):
        BusinessService(repo).enqueue(
            principal(), "d", "alert", "new", RunRequest(expected_binding_revision=2, recompute_of="old")
        )
    repo.enqueue_run.assert_not_called()


def test_recompute_rejects_changed_acquisition_parameters() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    repo.get_run.return_value = previous_run()
    with pytest.raises(InvalidInput):
        BusinessService(repo).enqueue(
            principal(),
            "d",
            "alert",
            "new",
            RunRequest(expected_binding_revision=2, recompute_of="old", parameters={"batch": "new"}),
        )
    repo.enqueue_run.assert_not_called()


def test_mutated_request_document_is_revalidated_at_service_boundary() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = None
    repo.get_binding.return_value = binding()
    request = RunRequest(expected_binding_revision=2, parameters={"batch": {"id": "old"}})
    request.parameters["batch"] = {"password": "embedded"}
    with pytest.raises(InvalidInput):
        BusinessService(repo).enqueue(principal(), "d", "alert", "new", request)
    repo.enqueue_run.assert_not_called()


def test_manual_requests_cannot_claim_scheduler_occurrence_keys() -> None:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    with pytest.raises(InvalidInput):
        BusinessService(repo).enqueue(
            principal(),
            "d",
            "alert",
            "scheduled-" + "a" * 64,
            RunRequest(expected_binding_revision=2),
        )
    repo.find_run_by_request_key.assert_not_called()
    repo.enqueue_run.assert_not_called()
