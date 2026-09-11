import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_bootstrap import settings
from test_source_bootstrap import environment

from enterprise_platform.bootstrap import Settings, create_runtime


def profile():
    return dict(
        workspace_id="11111111-1111-4111-8111-111111111111",
        config_ref="default",
        config_revision=1,
        origin="https://enterprise.internal",
        expected_plugin_unique_identifier="plugin@version",
        master_key_id="master-v1",
        master_secret="private-master-material-12345678901234567890",
    )


def test_provisioning_requires_explicit_flag_setup_and_profiles():
    assert settings().workflow_provisioning_enabled is False
    for extra in (
        {"workflow_provisioning_enabled": True},
        {"workflow_provisioning_enabled": True, "workflow_setup_enabled": True},
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate(settings().model_dump() | extra)


def test_profiles_load_only_from_dedicated_file_and_never_serialize_secrets(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([profile()]), encoding="utf-8")
    env = environment() | {
        "ENTERPRISE_WORKFLOW_SETUP_ENABLED": "true",
        "ENTERPRISE_WORKFLOW_PROVISIONING_ENABLED": "true",
        "ENTERPRISE_WORKFLOW_PLUGIN_PROFILES_FILE": str(path),
    }
    value = Settings.from_environment(env)
    assert value.workflow_provisioning_enabled
    assert len(value.workflow_plugin_profiles) == 1
    assert profile()["master_secret"] not in value.model_dump_json()
    assert profile()["master_secret"] not in repr(value)


def test_enabled_runtime_composes_real_service_without_database_or_http():
    value = Settings.model_validate(
        settings().model_dump()
        | dict(workflow_setup_enabled=True, workflow_provisioning_enabled=True, workflow_plugin_profiles=[profile()])
    )
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database during construction")):
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("No HTTP during construction")):
            runtime = create_runtime(value)
            try:
                assert runtime.workflow_provisioning is not None
                assert (
                    "/enterprise/api/v1/workflow-provisioning/{provisioning_id}/advance"
                    in runtime.app.openapi()["paths"]
                )
            finally:
                runtime.close()


def test_default_runtime_does_not_enable_provisioning():
    runtime = create_runtime(settings())
    try:
        assert runtime.workflow_provisioning is None
    finally:
        runtime.close()


def test_invalid_enable_flag_is_rejected():
    with pytest.raises(ValueError):
        Settings.from_environment(environment() | {"ENTERPRISE_WORKFLOW_PROVISIONING_ENABLED": "yes"})


def test_enabled_runtime_wires_public_profile_registry():
    value = Settings.model_validate(
        settings().model_dump()
        | dict(workflow_setup_enabled=True, workflow_provisioning_enabled=True, workflow_plugin_profiles=[profile()])
    )
    runtime = create_runtime(value)
    try:
        assert runtime.workflow_provisioning is not None
        registry = runtime.workflow_provisioning._profiles
        assert registry is not None
        assert registry.list_profiles(profile()["workspace_id"])[0].config_ref == "default"
    finally:
        runtime.close()


def test_bootstrapped_discovery_and_history_use_real_http_service_without_external_io():
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.testclient import TestClient
    from test_workflow_provisioning_contracts import initial, setup

    from enterprise_platform.application.contracts import Page, Principal

    selected = profile() | {"workspace_id": setup().workspace_id, "display_name": "Factory"}
    value = Settings.model_validate(
        settings().model_dump()
        | dict(workflow_setup_enabled=True, workflow_provisioning_enabled=True, workflow_plugin_profiles=[selected])
    )
    actor = Principal(workspace_id=setup().workspace_id, actor_id="actor", workspace_role="owner", display_name="Owner")
    page = Page(items=(initial().view,), total=1, offset=0, limit=20)
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("No native HTTP")),
        patch("enterprise_platform.bootstrap.DifyIdentityClient.resolve", new=AsyncMock(return_value=actor)),
        patch("enterprise_platform.bootstrap.WorkflowSetupService.get", new=AsyncMock(return_value=setup())),
        patch(
            "enterprise_platform.bootstrap.BusinessService.get_device",
            return_value=MagicMock(workspace_id=actor.workspace_id, id="device", deleted_at=None),
        ),
        patch(
            "enterprise_platform.bootstrap.SqlAlchemyWorkflowProvisioningRepository.list", return_value=page
        ) as history,
    ):
        runtime = create_runtime(value)
        try:
            http = TestClient(runtime.app)
            choices = http.get("/enterprise/api/v1/workflow-setups/setup/provisioning-profiles")
            assert choices.status_code == 200
            assert choices.json() == [dict(config_ref="default", config_revision=1, display_name="Factory")]
            response = http.get("/enterprise/api/v1/workflow-setups/setup/provisioning")
            assert response.status_code == 200
            assert response.json() == page.model_dump(mode="json")
            assert response.headers["cache-control"] == choices.headers["cache-control"] == "private, no-store"
            history.assert_called_once_with(actor.workspace_id, setup_id="setup", actor_id="actor", offset=0, limit=20)
            assert selected["master_secret"] not in choices.text + response.text
        finally:
            runtime.close()
