from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from test_bootstrap import settings
from test_native_registration import PATH, TOKEN
from test_source_bootstrap import environment
from test_workflow_activation_bootstrap import enabled

from enterprise_platform.bootstrap import Settings, create_runtime


def configured():
    value = enabled()
    return Settings.model_validate(
        value.model_dump()
        | {
            "workflow_plugin_profiles": value.workflow_plugin_profiles,
            "workflow_credential_keyring": value.workflow_credential_keyring,
            "specifications": value.specifications,
            "native_registration_token": SecretStr(TOKEN),
        }
    )


def test_registration_token_requires_activation():
    with pytest.raises(ValidationError):
        Settings.model_validate(settings().model_dump() | {"native_registration_token": SecretStr(TOKEN)})


def test_token_is_excluded_from_settings_serialization():
    value = configured()
    assert value.native_registration_token.get_secret_value() == TOKEN
    assert "native_registration_token" not in value.model_dump()
    assert TOKEN not in repr(value) and TOKEN not in value.model_dump_json()


def test_internal_route_only_mounted_with_explicit_token():
    for value, status in ((enabled(), 404), (configured(), 401)):
        with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No DB")):
            runtime = create_runtime(value)
            try:
                http = TestClient(runtime.app)
                assert http.get(PATH).status_code == status
                assert PATH not in runtime.app.openapi()["paths"]
            finally:
                runtime.close()


def test_environment_does_not_ignore_invalid_registration_token():
    with pytest.raises(ValueError):
        Settings.from_environment(environment() | {"ENTERPRISE_NATIVE_REGISTRATION_TOKEN": "short"})
