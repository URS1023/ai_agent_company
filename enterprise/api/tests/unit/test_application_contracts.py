import pytest
from pydantic import ValidationError

from enterprise_platform.application.contracts import BusinessResult, DeviceCreate, Principal, canonical_hash


def test_device_codes_retain_leading_zeroes_and_ignore_no_unknown_fields() -> None:
    assert DeviceCreate(device_code="0001", name="Compressor").device_code == "0001"
    with pytest.raises(ValidationError):
        DeviceCreate.model_validate({"device_code": "0001", "name": "Compressor", "workspace_id": "forged"})


def test_hash_is_stable_and_rejects_nonfinite_numbers() -> None:
    assert canonical_hash({"b": 2, "a": "0001"}) == canonical_hash({"a": "0001", "b": 2})
    with pytest.raises(ValueError):
        canonical_hash({"value": float("nan")})


def test_result_conclusion_must_match_scenario_and_completeness() -> None:
    assert BusinessResult(scenario="alert", conclusion="issues", complete=False).conclusion == "issues"
    for fields in [
        {"scenario": "alert", "conclusion": "passed", "complete": True},
        {"scenario": "quality", "conclusion": "normal", "complete": True},
        {"scenario": "quality", "conclusion": "passed", "complete": False},
        {"scenario": "alert", "conclusion": "normal", "complete": False},
        {"scenario": "alert", "conclusion": "no_data", "complete": True},
    ]:
        with pytest.raises(ValidationError):
            BusinessResult.model_validate(fields)


def test_principal_actions_are_explicit_not_truthy_role_strings() -> None:
    principal = Principal(
        actor_id="account-1", workspace_id="workspace-1", workspace_role="normal", display_name="User"
    )
    assert not principal.can("manage")
    assert principal.can("read")


def test_nested_credentials_are_not_allowed_in_persisted_binding_manifest() -> None:
    from uuid import UUID

    from enterprise_platform.application.contracts import BindingWrite

    fields = {
        "app_id": "app",
        "workflow_id": UUID(int=1),
        "specification_revision": "v1",
        "secret_ref": "vault-ref",
        "source_id": "s",
        "source_revision": "source-v1",
        "read_id": "read-1",
        "read_revision": "r",
    }
    with pytest.raises(ValidationError):
        BindingWrite(**fields, manifest={"nodes": [{"Authorization": "Bearer raw-secret"}]})
