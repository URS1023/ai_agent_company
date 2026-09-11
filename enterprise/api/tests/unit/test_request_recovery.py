"""Request recovery exercises the real service/HTTP boundary, never a database."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, create_autospec
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from enterprise_platform.application.contracts import (
    Action,
    Binding,
    JsonObject,
    Principal,
    Run,
    RunSpec,
    Scenario,
    canonical_hash,
)
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, NotFound, Unauthenticated
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.application.service import BusinessService, RunRequest
from enterprise_platform.http.app import RunView, create_app


def actor(workspace: str = "w", role: str = "editor") -> Principal:
    return Principal.model_validate(
        {"actor_id": "actor", "workspace_id": workspace, "workspace_role": role, "display_name": "Operator"}
    )


def binding(revision: int = 2) -> Binding:
    return Binding(
        id="binding",
        workspace_id="w",
        device_id="device",
        scenario="alert",
        revision=revision,
        app_id="app",
        workflow_id=UUID(int=1),
        specification_revision=f"spec-{revision}",
        secret_ref="private-secret-reference",
        source_id="source",
        source_revision="source-v1",
        read_id="read",
        read_revision="read-v1",
        manifest={"input_keys": ["batch"]},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def saved_run(parameters: JsonObject | None = None, *, recompute_of: str | None = None) -> Run:
    spec = RunSpec.model_validate(
        {
            **RunSpec.from_binding(binding()).model_dump(),
            "parameters": parameters if parameters is not None else {"batch": "0001"},
            "recompute_of": recompute_of,
        }
    )
    return Run(
        id="original-run",
        workspace_id="w",
        actor_id="actor",
        request_key="request / 0001%?",
        payload_hash=canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": None}),
        spec=spec,
        input_snapshot={"private-raw-snapshot": "measurement"},
        status="dispatched",
        dispatch_nonce="private-nonce",
        dify_run_id="private-native-id",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def repository(existing: Run | None = None) -> MagicMock:
    repo = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repo.find_run_by_request_key.return_value = existing
    repo.get_binding.return_value = binding(3)
    return repo


def test_lost_enqueue_response_retries_frozen_request_after_binding_changed() -> None:
    original = saved_run()
    repo = repository(original)
    result = BusinessService(repo).enqueue(
        actor(),
        "device",
        "alert",
        original.request_key,
        RunRequest(expected_binding_revision=2, parameters={"batch": "0001"}),
    )
    assert result is original
    repo.find_run_by_request_key.assert_called_once_with("w", original.request_key)
    repo.get_binding.assert_not_called()
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize(
    ("device", "scenario", "revision", "parameters", "recompute_of"),
    [
        ("another-device", "alert", 2, {"batch": "0001"}, None),
        ("device", "quality", 2, {"batch": "0001"}, None),
        ("device", "alert", 3, {"batch": "0001"}, None),
        ("device", "alert", 2, {"batch": "0002"}, None),
        ("device", "alert", 2, {}, None),
        ("device", "alert", 2, {"batch": "0001"}, "different-original"),
    ],
)
def test_reused_key_with_different_request_conflicts_without_new_enqueue(
    device: str,
    scenario: Scenario,
    revision: int,
    parameters: JsonObject,
    recompute_of: str | None,
) -> None:
    original = saved_run()
    repo = repository(original)
    with pytest.raises(Conflict, match="request_key_payload_conflict"):
        BusinessService(repo).enqueue(
            actor(),
            device,
            scenario,
            original.request_key,
            RunRequest(expected_binding_revision=revision, parameters=parameters, recompute_of=recompute_of),
        )
    repo.get_binding.assert_not_called()
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize("parameters", [{"batch": True}, {"batch": 1.0}])
def test_parameter_identity_uses_canonical_json_not_python_numeric_equality(parameters: JsonObject) -> None:
    original = saved_run({"batch": 1})
    repo = repository(original)
    with pytest.raises(Conflict, match="request_key_payload_conflict"):
        BusinessService(repo).enqueue(
            actor(),
            "device",
            "alert",
            original.request_key,
            RunRequest(expected_binding_revision=2, parameters=parameters),
        )
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize("parameters", [{}, {"batch": "0001"}])
def test_recompute_retry_accepts_inherited_or_explicit_frozen_parameters(parameters: JsonObject) -> None:
    original = saved_run(recompute_of="historical-run")
    repo = repository(original)
    result = BusinessService(repo).enqueue(
        actor(),
        "device",
        "alert",
        original.request_key,
        RunRequest(expected_binding_revision=2, parameters=parameters, recompute_of="historical-run"),
    )
    assert result is original
    repo.get_run.assert_not_called()
    repo.get_binding.assert_not_called()
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize("recompute_of", [None, "different-original"])
def test_recompute_empty_parameters_do_not_hide_changed_ancestry(recompute_of: str | None) -> None:
    original = saved_run(recompute_of="historical-run")
    repo = repository(original)
    with pytest.raises(Conflict, match="request_key_payload_conflict"):
        BusinessService(repo).enqueue(
            actor(),
            "device",
            "alert",
            original.request_key,
            RunRequest(expected_binding_revision=2, recompute_of=recompute_of),
        )
    repo.enqueue_run.assert_not_called()


def test_retry_rechecks_run_permission_before_even_looking_up_existing_request() -> None:
    original = saved_run()
    repo = repository(original)
    with pytest.raises(AccessDenied):
        BusinessService(repo).enqueue(
            actor(role="normal"),
            "device",
            "alert",
            original.request_key,
            RunRequest(expected_binding_revision=2),
        )
    repo.find_run_by_request_key.assert_not_called()


def test_retry_revalidates_mutated_command_before_historical_fast_path() -> None:
    original = saved_run()
    repo = repository(original)
    request = RunRequest(expected_binding_revision=2, parameters={"batch": "0001"})
    request.parameters["batch"] = {"password": "private-fixture"}
    with pytest.raises(InvalidInput):
        BusinessService(repo).enqueue(actor(), "device", "alert", original.request_key, request)
    repo.find_run_by_request_key.assert_not_called()


@pytest.mark.parametrize("parameters", [{"batch": True}, {"batch": 1.0}])
def test_new_recompute_also_rejects_numeric_type_changed_parameters(parameters: JsonObject) -> None:
    original = saved_run({"batch": 1})
    repo = repository()
    repo.get_binding.return_value = binding()
    repo.get_run.return_value = original
    with pytest.raises(InvalidInput, match="recompute_parameters_mismatch"):
        BusinessService(repo).enqueue(
            actor(),
            "device",
            "alert",
            "new-key",
            RunRequest(expected_binding_revision=2, parameters=parameters, recompute_of=original.id),
        )
    repo.enqueue_run.assert_not_called()


def test_cross_workspace_retry_cannot_recover_other_workspace_run_or_create_against_its_device() -> None:
    repo = repository()
    repo.get_binding.side_effect = NotFound("binding_not_found")
    with pytest.raises(NotFound):
        BusinessService(repo).enqueue(
            actor("other-workspace"),
            "device",
            "alert",
            saved_run().request_key,
            RunRequest(expected_binding_revision=2, parameters={"batch": "0001"}),
        )
    repo.find_run_by_request_key.assert_called_once_with("other-workspace", saved_run().request_key)
    repo.get_binding.assert_called_once_with("other-workspace", "device", "alert")
    repo.enqueue_run.assert_not_called()


@pytest.mark.parametrize("conflict_at", ["binding", "enqueue"])
def test_concurrent_original_commit_is_recovered_after_a_new_request_cas_conflict(conflict_at: str) -> None:
    original = saved_run()
    repo = repository()
    repo.find_run_by_request_key.side_effect = [None, original]
    if conflict_at == "enqueue":
        repo.get_binding.return_value = binding()
        repo.enqueue_run.side_effect = Conflict("binding_snapshot_changed")
    result = BusinessService(repo).enqueue(
        actor(),
        "device",
        "alert",
        original.request_key,
        RunRequest(expected_binding_revision=2, parameters={"batch": "0001"}),
    )
    assert result is original
    assert repo.find_run_by_request_key.call_count == 2


def test_concurrent_same_key_with_other_payload_still_conflicts_after_cas_conflict() -> None:
    original = saved_run({"batch": "0002"})
    repo = repository()
    repo.find_run_by_request_key.side_effect = [None, original]
    with pytest.raises(Conflict, match="request_key_payload_conflict"):
        BusinessService(repo).enqueue(
            actor(),
            "device",
            "alert",
            original.request_key,
            RunRequest(expected_binding_revision=2, parameters={"batch": "0001"}),
        )
    repo.enqueue_run.assert_not_called()


def test_lookup_absence_and_cross_workspace_are_identical_not_found() -> None:
    original = saved_run()
    repo = repository()
    repo.find_run_by_request_key.side_effect = lambda ws, key: (
        original if (ws, key) == ("w", original.request_key) else None
    )
    service = BusinessService(repo)
    assert service.get_run_by_request_key(actor(role="normal"), original.request_key) is original
    for principal, key in [(actor("another"), original.request_key), (actor(), "missing")]:
        with pytest.raises(NotFound):
            service.get_run_by_request_key(principal, key)
    repo.get_run.assert_not_called()


@pytest.mark.parametrize("key", ["", " padded", "padded ", "x" * 129])
def test_lookup_rejects_invalid_key_without_normalizing_it(key: str) -> None:
    repo = repository()
    with pytest.raises(InvalidInput):
        BusinessService(repo).get_run_by_request_key(actor(), key)
    repo.find_run_by_request_key.assert_not_called()


class Identity:
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    async def resolve(
        self,
        *,
        cookie_header: str | None,
        authorization: str | None,
        csrf_token: str | None,
    ) -> Principal:
        if self.principal is None:
            raise Unauthenticated()
        return self.principal


def test_public_lookup_is_read_only_scoped_and_returns_only_run_view() -> None:
    original = saved_run()
    repo = repository(original)
    with TestClient(create_app(BusinessService(repo), Identity(actor(role="normal")))) as client:
        response = client.get(
            "/enterprise/api/v1/run-requests/lookup",
            params={"request_key": original.request_key},
            headers={"X-Workspace-ID": "forged"},
        )
    assert response.status_code == 200
    assert response.json() == RunView.from_run(original).model_dump(mode="json")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert all(
        value not in response.text
        for value in ["private-nonce", "private-native-id", "private-secret-reference", "private-raw-snapshot"]
    )
    repo.find_run_by_request_key.assert_called_once_with("w", original.request_key)
    repo.enqueue_run.assert_not_called()


class ReadRevokedPrincipal(Principal):
    def can(self, action: Action) -> bool:
        return False


@pytest.mark.parametrize(("principal", "status"), [(None, 401), (ReadRevokedPrincipal(**actor().model_dump()), 403)])
def test_lookup_checks_native_identity_and_business_read_permission(principal: Principal | None, status: int) -> None:
    repo = repository(saved_run())
    with TestClient(create_app(BusinessService(repo), Identity(principal))) as client:
        response = client.get("/enterprise/api/v1/run-requests/lookup", params={"request_key": "request"})
    assert response.status_code == status
    repo.find_run_by_request_key.assert_not_called()


def test_public_lookup_missing_is_sanitized_404_and_invalid_query_is_422() -> None:
    repo = repository()
    with TestClient(create_app(BusinessService(repo), Identity(actor()))) as client:
        missing = client.get("/enterprise/api/v1/run-requests/lookup", params={"request_key": "private-missing-key"})
        invalid = client.get("/enterprise/api/v1/run-requests/lookup", params={"request_key": " "})
    assert missing.status_code == 404
    assert missing.json() == {"code": "not_found"}
    assert invalid.status_code == 422
    assert invalid.json() == {"code": "invalid_input"}
    repo.find_run_by_request_key.assert_called_once_with("w", "private-missing-key")


def test_existing_enqueue_endpoint_recovers_original_instead_of_returning_stale_binding_conflict() -> None:
    original = saved_run()
    repo = repository(original)
    with TestClient(
        create_app(BusinessService(repo), Identity(actor()), allowed_origins=("https://portal.example",))
    ) as client:
        response = client.post(
            "/enterprise/api/v1/devices/device/bindings/alert/runs",
            json={"expected_binding_revision": 2, "parameters": {"batch": "0001"}},
            headers={"Origin": "https://portal.example", "Idempotency-Key": original.request_key},
        )
    assert response.status_code == 202
    assert response.json() == RunView.from_run(original).model_dump(mode="json")
    repo.get_binding.assert_not_called()
    repo.enqueue_run.assert_not_called()


def test_lookup_scope_is_from_native_identity_not_workspace_header_or_query() -> None:
    original = saved_run()
    repo = repository()
    repo.find_run_by_request_key.side_effect = lambda ws, key: original if ws == "w" else None
    with TestClient(create_app(BusinessService(repo), Identity(actor("other-workspace")))) as client:
        response = client.get(
            "/enterprise/api/v1/run-requests/lookup",
            params={"request_key": original.request_key, "workspace_id": "w"},
            headers={"X-Workspace-ID": "w"},
        )
    assert response.status_code == 404
    assert response.json() == {"code": "not_found"}
    repo.find_run_by_request_key.assert_called_once_with("other-workspace", original.request_key)
