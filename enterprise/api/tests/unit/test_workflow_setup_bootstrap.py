from unittest.mock import patch

import pytest
from test_bootstrap import settings

from enterprise_platform.bootstrap import Settings, create_runtime


def environment():
    return {
        "ENTERPRISE_DATABASE_URL": "postgresql+psycopg://app:secret@db.test/enterprise_business",
        "ENTERPRISE_DIFY_CONSOLE_URL": "https://dify.test/console/api",
        "ENTERPRISE_DIFY_SERVICE_URL": "https://dify.test/v1",
        "ENTERPRISE_ALLOWED_ORIGINS": "https://portal.test",
    }


def test_workflow_setup_is_disabled_by_default_and_explicitly_parsed() -> None:
    assert settings().workflow_setup_enabled is False
    assert Settings.from_environment(environment()).workflow_setup_enabled is False
    assert Settings.from_environment(
        {**environment(), "ENTERPRISE_WORKFLOW_SETUP_ENABLED": "true"}
    ).workflow_setup_enabled
    assert not Settings.from_environment(
        {**environment(), "ENTERPRISE_WORKFLOW_SETUP_ENABLED": "false"}
    ).workflow_setup_enabled
    with pytest.raises(ValueError):
        Settings.from_environment({**environment(), "ENTERPRISE_WORKFLOW_SETUP_ENABLED": "enable-it"})


@pytest.mark.parametrize("enabled", [False, True])
def test_setup_composition_preserves_no_connect_no_migration_and_routes_always_have_contract(enabled) -> None:
    config = Settings.model_validate({**settings().model_dump(), "workflow_setup_enabled": enabled})
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("no DB connection")),
        patch("enterprise_platform.bootstrap.WorkflowSetupService") as service,
    ):
        runtime = create_runtime(config)
        try:
            paths = runtime.app.openapi()["paths"]
            assert "/enterprise/api/v1/devices/{device_id}/bindings/{scenario}/workflow-setups" in paths
            assert "/enterprise/api/v1/workflow-setups/{setup_id}" in paths
            assert service.call_count == int(enabled)
        finally:
            runtime.close()
