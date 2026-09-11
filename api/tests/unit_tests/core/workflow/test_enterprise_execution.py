"""Pure managed-tool policy tests; no Flask, graphon, database or plugin daemon."""

import json
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from core.workflow.enterprise_execution import (
    ManagedExecutionConfigurationError,
    ManagedExecutionIdentityError,
    ManagedToolRegistry,
)

WORKFLOW = "00000000-0000-4000-8000-000000000001"
RUN = "00000000-0000-4000-8000-000000000002"
EXECUTION = "00000000-0000-4000-8000-000000000003"
CREDENTIAL = "00000000-0000-4000-8000-000000000004"


def registration() -> dict[str, str]:
    return {
        "workspace_id": "workspace-1",
        "app_id": "app-1",
        "workflow_id": WORKFLOW,
        "provider_id": "enterprise/managed/managed",
        "tool_name": "assess",
        "credential_id": CREDENTIAL,
        "node_id": "node-1",
    }


def registry() -> ManagedToolRegistry:
    return ManagedToolRegistry.from_json(json.dumps([registration()]))


def invocation() -> dict[str, str | None]:
    return {**registration(), "node_execution_id": EXECUTION, "invoke_from": "service-api"}


@pytest.mark.parametrize("raw", ["", "[]", " \r\n\t "])
def test_empty_policy_preserves_native_execution_without_reading_identity(raw: str) -> None:
    getter = Mock(side_effect=AssertionError("Should not inspect native state"))
    policy = ManagedToolRegistry.from_json(raw)
    assert not policy.registrations
    assert policy.metadata_for(**invocation(), native_run_id_getter=getter) is None
    getter.assert_not_called()


def test_registered_service_execution_produces_only_plain_metadata() -> None:
    getter = Mock(return_value=RUN)
    metadata = registry().metadata_for(**invocation(), native_run_id_getter=getter)
    assert metadata == {
        "workspace_id": "workspace-1",
        "app_id": "app-1",
        "workflow_id": WORKFLOW,
        "native_run_id": RUN,
        "node_id": "node-1",
        "node_execution_id": EXECUTION,
        "invoke_from": "service-api",
    }
    assert json.loads(json.dumps(metadata)) == metadata
    getter.assert_called_once_with()


def test_policy_is_immutable_and_each_metadata_document_is_detached() -> None:
    policy = registry()
    with pytest.raises(FrozenInstanceError):
        policy.registrations[0].app_id = "other"  # type: ignore[misc]
    first = policy.metadata_for(**invocation(), native_run_id_getter=lambda: RUN)
    assert first is not None
    first["app_id"] = "forged"
    second = policy.metadata_for(**invocation(), native_run_id_getter=lambda: RUN)
    assert second is not None
    assert second["app_id"] == "app-1"


@pytest.mark.parametrize("field", list(registration()))
def test_every_registration_dimension_is_exact_with_no_wildcard_or_fallback(field: str) -> None:
    values = invocation()
    values[field] = "other" if field not in {"workflow_id", "credential_id"} else RUN
    getter = Mock(side_effect=AssertionError("Unregistered execution"))
    assert registry().metadata_for(**values, native_run_id_getter=getter) is None
    getter.assert_not_called()


@pytest.mark.parametrize("origin", ["debugger", "web-app", "openapi", "trigger", "explore", None])
def test_registered_tool_only_accepts_the_service_api_entry(origin: str | None) -> None:
    values = invocation()
    values["invoke_from"] = origin
    getter = Mock(return_value=RUN)
    with pytest.raises(ManagedExecutionIdentityError, match="^enterprise_execution_identity_invalid$"):
        registry().metadata_for(**values, native_run_id_getter=getter)
    getter.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "not-a-uuid", "00000000-0000-0000-0000-000000000000", "token\r\nsecret"])
@pytest.mark.parametrize("field", ["node_execution_id", "native_run_id"])
def test_registered_tool_requires_real_execution_identifiers(field: str, value: str | None) -> None:
    values = invocation()
    if field == "node_execution_id":
        values[field] = value
    with pytest.raises(ManagedExecutionIdentityError) as exc:
        registry().metadata_for(**values, native_run_id_getter=lambda: value if field == "native_run_id" else RUN)
    assert str(exc.value) == "enterprise_execution_identity_invalid"


def test_missing_workflow_id_or_getter_fails_closed_for_a_registered_tool() -> None:
    with pytest.raises(ManagedExecutionIdentityError):
        registry().metadata_for(**invocation(), native_run_id_getter=None)
    values = invocation()
    values["workflow_id"] = None
    with pytest.raises(ManagedExecutionIdentityError):
        registry().metadata_for(**values, native_run_id_getter=lambda: RUN)


def test_getter_failure_is_redacted_and_is_not_retried() -> None:
    getter = Mock(side_effect=RuntimeError("private-secret"))
    with pytest.raises(ManagedExecutionIdentityError) as exc:
        registry().metadata_for(**invocation(), native_run_id_getter=getter)
    assert str(exc.value) == "enterprise_execution_identity_invalid"
    getter.assert_called_once_with()


@pytest.mark.parametrize("raw", ["{", "{}", "null", "1", '[{"secret":"private-secret"}]', "[[]]", "[true]"])
def test_invalid_policy_is_redacted_and_never_partially_enabled(raw: str) -> None:
    with pytest.raises(ManagedExecutionConfigurationError) as exc:
        ManagedToolRegistry.from_json(raw)
    assert str(exc.value) == "enterprise_managed_tools_configuration_invalid"


@pytest.mark.parametrize("field", list(registration()))
@pytest.mark.parametrize("value", [None, 1, "", "*", "secret\r\n", "x" * 257])
def test_registration_values_require_explicit_bounded_identity(field: str, value: object) -> None:
    document: dict[str, object] = {**registration(), field: value}
    with pytest.raises(ManagedExecutionConfigurationError):
        ManagedToolRegistry.from_json(json.dumps([document]))


def test_noncanonical_uuid_would_select_native_default_credentials_so_is_rejected() -> None:
    document = {**registration(), "credential_id": "default"}
    with pytest.raises(ManagedExecutionConfigurationError):
        ManagedToolRegistry.from_json(json.dumps([document]))


def test_unknown_keys_duplicate_json_keys_and_duplicate_registrations_are_rejected() -> None:
    for raw in (
        json.dumps([{**registration(), "secret": "private-secret"}]),
        json.dumps([registration(), registration()]),
        json.dumps([registration()]).replace('"workspace_id":', '"workspace_id":"other","workspace_id":'),
    ):
        with pytest.raises(ManagedExecutionConfigurationError):
            ManagedToolRegistry.from_json(raw)


def test_policy_has_a_utf8_byte_limit_before_parsing() -> None:
    with pytest.raises(ManagedExecutionConfigurationError):
        ManagedToolRegistry.from_json(" " * 65537)
    with pytest.raises(ManagedExecutionConfigurationError):
        ManagedToolRegistry.from_json(json.dumps([registration()]) + "界" * 22000)


def test_policy_has_a_registration_count_limit() -> None:
    documents = [
        {
            **registration(),
            "workspace_id": "w",
            "app_id": "a",
            "provider_id": "a/b/c",
            "tool_name": "t",
            "node_id": str(i),
        }
        for i in range(257)
    ]
    assert len(json.dumps(documents, separators=(",", ":")).encode()) < 65536
    assert len(ManagedToolRegistry.from_json(json.dumps(documents[:256])).registrations) == 256
    with pytest.raises(ManagedExecutionConfigurationError):
        ManagedToolRegistry.from_json(json.dumps(documents, separators=(",", ":")))
