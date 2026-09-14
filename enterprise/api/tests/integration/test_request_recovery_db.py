"""Durable request recovery against disposable CI SQLite/PostgreSQL repositories only."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from database_environment import database_tests_enabled
from test_repository import lane
from test_repository import repository as repository

from enterprise_platform.application.contracts import Binding, BindingWrite, JsonObject, Principal, Scenario
from enterprise_platform.application.errors import Conflict, NotFound
from enterprise_platform.application.service import BusinessService, RunRequest
from enterprise_platform.persistence.repository import SqlAlchemyRepository

pytestmark = pytest.mark.skipif(
    not database_tests_enabled(os.environ), reason="Explicit disposable database test execution required"
)


def actor(workspace: str = "w") -> Principal:
    return Principal(actor_id="actor", workspace_id=workspace, workspace_role="editor", display_name="Operator")


def advance_binding(repository: SqlAlchemyRepository, binding: Binding) -> Binding:
    payload = BindingWrite.model_validate(
        {
            **binding.model_dump(include=set(BindingWrite.model_fields)),
            "specification_revision": f"spec-{binding.revision + 1}",
            "manifest": {"input_keys": ["batch"]},
        }
    )
    return repository.put_binding(
        binding.workspace_id,
        binding.device_id,
        binding.scenario,
        payload,
        expected_revision=binding.revision,
        actor_id="actor",
    )


def test_lost_response_then_capture_and_binding_change_recovers_original_run(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    service = BusinessService(repository)
    request = RunRequest(expected_binding_revision=binding.revision)
    original = service.enqueue(actor(), binding.device_id, "alert", "request / 0001%?", request)
    repository.claim_run("w", original.id, "nonce-1", actor_id="worker")
    captured = repository.capture_input("w", original.id, "nonce-1", {"measurement": "085.0001"}, actor_id="reader")
    advance_binding(repository, binding)
    before_events = repository.list_events("w", original.id)

    recovered = service.enqueue(actor(), binding.device_id, "alert", original.request_key, request)

    assert recovered == captured
    assert repository.find_run_by_request_key("w", original.request_key) == recovered
    assert repository.list_runs("w", binding.device_id, "alert").total == 1
    assert repository.list_events("w", original.id) == before_events


@pytest.mark.parametrize(
    ("device", "scenario", "revision_delta", "parameters", "recompute_of"),
    [
        ("other-device", "alert", 0, {}, None),
        (None, "quality", 0, {}, None),
        (None, "alert", 1, {}, None),
        (None, "alert", 0, {"batch": "0001"}, None),
        (None, "alert", 0, {}, "other-run"),
    ],
)
def test_conflicting_original_key_never_creates_another_run(
    repository: SqlAlchemyRepository,
    device: str | None,
    scenario: Scenario,
    revision_delta: int,
    parameters: JsonObject,
    recompute_of: str | None,
) -> None:
    binding = lane(repository)
    service = BusinessService(repository)
    original = service.enqueue(
        actor(),
        binding.device_id,
        "alert",
        "request",
        RunRequest(expected_binding_revision=binding.revision),
    )
    advance_binding(repository, binding)
    with pytest.raises(Conflict, match="request_key_payload_conflict"):
        service.enqueue(
            actor(),
            device or binding.device_id,
            scenario,
            original.request_key,
            RunRequest(
                expected_binding_revision=binding.revision + revision_delta,
                parameters=parameters,
                recompute_of=recompute_of,
            ),
        )
    assert repository.list_runs("w", binding.device_id, "alert").total == 1
    assert len(repository.list_events("w", original.id)) == 1


def test_same_key_is_workspace_scoped_and_other_workspace_cannot_recover_original(
    repository: SqlAlchemyRepository,
) -> None:
    binding = lane(repository)
    service = BusinessService(repository)
    original = service.enqueue(
        actor(),
        binding.device_id,
        "alert",
        "shared-key",
        RunRequest(expected_binding_revision=binding.revision),
    )
    assert repository.find_run_by_request_key("other-workspace", original.request_key) is None
    with pytest.raises(NotFound):
        service.get_run_by_request_key(actor("other-workspace"), original.request_key)
    with pytest.raises(NotFound):
        service.enqueue(
            actor("other-workspace"),
            binding.device_id,
            "alert",
            original.request_key,
            RunRequest(expected_binding_revision=binding.revision),
        )
    other = lane(repository, workspace="other-workspace")
    separate = service.enqueue(
        actor("other-workspace"),
        other.device_id,
        "alert",
        original.request_key,
        RunRequest(expected_binding_revision=other.revision),
    )
    assert separate.id != original.id
    assert service.get_run_by_request_key(actor(), original.request_key) == original
    assert service.get_run_by_request_key(actor("other-workspace"), original.request_key) == separate


def test_terminal_request_recovery_survives_soft_deleted_device(repository: SqlAlchemyRepository) -> None:
    binding = lane(repository)
    service = BusinessService(repository)
    request = RunRequest(expected_binding_revision=binding.revision)
    original = service.enqueue(actor(), binding.device_id, "alert", "request", request)
    repository.claim_run("w", original.id, "nonce", actor_id="worker")
    terminal = repository.fail_run("w", original.id, "nonce", "execution_failed", actor_id="worker")
    repository.delete_device("w", binding.device_id, expected_revision=1, actor_id="actor")
    assert service.get_run_by_request_key(actor(), original.request_key) == terminal
    assert service.enqueue(actor(), binding.device_id, "alert", original.request_key, request) == terminal
    assert repository.list_runs("w", binding.device_id, "alert").total == 1


def test_recompute_inherited_parameters_survive_binding_change_and_concurrent_retry(
    repository: SqlAlchemyRepository,
) -> None:
    binding = advance_binding(repository, lane(repository))
    service = BusinessService(repository)
    original = service.enqueue(
        actor(),
        binding.device_id,
        "alert",
        "first",
        RunRequest(expected_binding_revision=binding.revision, parameters={"batch": "0001"}),
    )
    repository.claim_run("w", original.id, "nonce", actor_id="worker")
    repository.capture_input("w", original.id, "nonce", {"measurement": "001.2500"}, actor_id="reader")
    repository.fail_run("w", original.id, "nonce", "execution_failed", actor_id="worker")
    request = RunRequest(expected_binding_revision=binding.revision, recompute_of=original.id)
    recomputed = service.enqueue(actor(), binding.device_id, "alert", "recompute", request)
    advance_binding(repository, binding)

    def retry(index: int) -> str:
        parameters: JsonObject = {} if index == 0 else {"batch": "0001"}
        return service.enqueue(
            actor(),
            binding.device_id,
            "alert",
            "recompute",
            RunRequest(expected_binding_revision=binding.revision, recompute_of=original.id, parameters=parameters),
        ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(retry, range(2))) == [recomputed.id, recomputed.id]
    assert recomputed.spec.parameters == {"batch": "0001"}
    assert repository.list_runs("w", binding.device_id, "alert").total == 2
    assert len(repository.list_events("w", recomputed.id)) == 1
