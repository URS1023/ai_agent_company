from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_bootstrap import settings
from test_source_bootstrap import environment
from test_workflow_credential_bootstrap import configured
from test_workflow_provisioning_bootstrap import profile

from enterprise_platform.application.workflow_enrollment_service import WorkflowEnrollmentService
from enterprise_platform.bootstrap import Settings, create_runtime


def enabled():
    return Settings.model_validate(
        settings().model_dump()
        | {
            "workflow_setup_enabled": True,
            "workflow_provisioning_enabled": True,
            "workflow_enrollment_enabled": True,
            "workflow_plugin_profiles": [profile()],
            "workflow_credential_keyring": configured().workflow_credential_keyring,
        }
    )


def test_default_has_no_enrollment():
    assert settings().workflow_enrollment_enabled is False
    runtime = create_runtime(settings())
    try:
        assert runtime.workflow_enrollment is None
    finally:
        runtime.close()


@pytest.mark.parametrize("missing", ["workflow_provisioning_enabled", "workflow_credential_keyring"])
def test_enable_requires_provisioning_and_encryption(missing):
    value = enabled()
    fields = value.model_dump() | {
        "workflow_plugin_profiles": value.workflow_plugin_profiles,
        "workflow_credential_keyring": value.workflow_credential_keyring,
    }
    fields[missing] = False if missing.endswith("enabled") else None
    with pytest.raises(ValidationError):
        Settings.model_validate(fields)


def test_runtime_composes_real_service_without_database_or_http():
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("No native HTTP")),
    ):
        runtime = create_runtime(enabled())
        try:
            assert isinstance(runtime.workflow_enrollment, WorkflowEnrollmentService)
            assert runtime.workflow_enrollment._provisioning is runtime.workflow_provisioning
            assert runtime.workflow_enrollment._repository._sessions is runtime.repository._sessions
        finally:
            runtime.close()


def test_invalid_flag_rejected():
    with pytest.raises(ValueError):
        Settings.from_environment(environment() | {"ENTERPRISE_WORKFLOW_ENROLLMENT_ENABLED": "yes"})
