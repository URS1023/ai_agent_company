import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_bootstrap import settings
from test_source_bootstrap import environment
from test_workflow_gateway import run_record

from enterprise_platform.application.dispatcher import WorkflowPreparationFailed
from enterprise_platform.application.errors import NotFound
from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.persistence.workflow_credentials import SqlAlchemyWorkflowCredentialVault


def configured():
    env = environment()
    env["ENTERPRISE_WORKFLOW_CREDENTIAL_KEYS_JSON"] = json.dumps(
        {"active_key_id": "key-1", "keys": [{"key_id": "key-1", "key": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="}]}
    )
    return Settings.from_environment(env)


def test_vault_is_only_enabled_by_its_dedicated_redacted_configuration() -> None:
    value = configured()
    assert value.workflow_credential_keyring.active_key_id == "key-1"
    assert "YWFhYWFh" not in repr(value)
    assert "YWFhYWFh" not in value.model_dump_json()
    assert settings().workflow_credential_keyring is None


@pytest.mark.parametrize("raw", ["invalid-sensitive-key", " " * 65537], ids=["malformed", "oversized"])
def test_invalid_key_configuration_does_not_echo_secret(raw: str) -> None:
    env = {**environment(), "ENTERPRISE_WORKFLOW_CREDENTIAL_KEYS_JSON": raw}
    with pytest.raises(ValueError) as failure:
        Settings.from_environment(env)
    assert raw not in str(failure.value)


def test_vault_and_static_token_configurations_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                **settings().model_dump(),
                "workflow_credential_keyring": configured().workflow_credential_keyring,
                "workflow_credentials": [
                    {"workspace_id": "w", "app_id": "a", "secret_ref": "r", "api_key": "fixture-key"}
                ],
            }
        )


def test_vault_runtime_constructs_real_resolver_without_connecting_or_migrating() -> None:
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database during construction")):
        runtime = create_runtime(configured())
        try:
            assert isinstance(runtime.workflow_credential_vault, SqlAlchemyWorkflowCredentialVault)
            with patch.object(runtime.workflow_credential_vault, "resolve", side_effect=NotFound()) as resolve:
                with pytest.raises(WorkflowPreparationFailed):
                    runtime.dispatcher.gateway.run(run_record(), {}, on_started=lambda event: None)
            resolve.assert_called_once_with("w", "app", "secret-ref")
            assert not any("credential" in path for path in runtime.app.openapi()["paths"])
        finally:
            runtime.close()


def test_default_runtime_keeps_static_gateway_without_vault_connections() -> None:
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database during construction")):
        runtime = create_runtime(settings())
        try:
            assert runtime.workflow_credential_vault is None
        finally:
            runtime.close()
