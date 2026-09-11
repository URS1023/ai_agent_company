from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_assessments import alert_spec
from test_bootstrap import settings
from test_source_bootstrap import environment
from test_workflow_enrollment_bootstrap import enabled as enrollment_enabled

from enterprise_platform.application.workflow_activation_service import WorkflowActivationService
from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.persistence.workflow_activation_lookup import SqlAlchemyActiveExecutionKeyLookup


def enabled():
    value = enrollment_enabled()
    return Settings.model_validate(
        value.model_dump()
        | {
            "workflow_activation_enabled": True,
            "workflow_plugin_profiles": value.workflow_plugin_profiles,
            "workflow_credential_keyring": value.workflow_credential_keyring,
            "specifications": (alert_spec(),),
        }
    )


def test_default_keeps_activation_and_dynamic_runtime_disabled():
    assert settings().workflow_activation_enabled is False
    runtime = create_runtime(settings())
    try:
        assert runtime.workflow_activation is None
        assert runtime.managed_execution is None
    finally:
        runtime.close()


@pytest.mark.parametrize("missing", ["workflow_enrollment_enabled", "specifications"])
def test_activation_requires_enrollment_and_specifications(missing):
    value = enabled()
    data = value.model_dump() | {
        "workflow_plugin_profiles": value.workflow_plugin_profiles,
        "workflow_credential_keyring": value.workflow_credential_keyring,
        "specifications": value.specifications,
        missing: False if missing.endswith("enabled") else (),
    }
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_runtime_wires_real_activation_and_dynamic_lookup_without_io():
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("No native HTTP")),
    ):
        runtime = create_runtime(enabled())
        try:
            assert isinstance(runtime.workflow_activation, WorkflowActivationService)
            assert runtime.workflow_activation._repository._sessions is runtime.repository._sessions
            assert runtime.workflow_activation._enrollment._sessions is runtime.repository._sessions
            assert runtime.managed_execution is not None
            assert isinstance(runtime.managed_execution.authenticator._active_keys, SqlAlchemyActiveExecutionKeyLookup)
        finally:
            runtime.close()


def test_invalid_environment_flag_is_rejected():
    with pytest.raises(ValueError):
        Settings.from_environment(environment() | {"ENTERPRISE_WORKFLOW_ACTIVATION_ENABLED": "yes"})
