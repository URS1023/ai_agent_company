import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from enterprise_platform.application.assessments import AlertSpecification, AlertVariable
from enterprise_platform.application.managed_execution import ExecutionKey
from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.domain.rules import AlertRule


def settings(**changes):
    return Settings(
        database_url=SecretStr("postgresql+psycopg://u:p@localhost/enterprise_test"),
        dify_console_url="http://dify-api:5001/console/api",
        dify_service_url="http://dify-api:5001/v1",
        allowed_origins=("https://portal.test",),
        **changes,
    )


def key():
    return ExecutionKey(
        key_id="k", workspace_id="w", app_id="a", secret=SecretStr("s" * 48), node_ids=frozenset({"read-node"})
    )


def spec():
    return AlertSpecification(
        workspace_id="w",
        specification_revision="s1",
        variables=(AlertVariable(name="v", column="value", kind="decimal"),),
        rules=(AlertRule("high", "1", "v > 5", "warning", "High"),),
    )


def test_private_read_boundary_is_only_mounted_with_explicit_keys_and_specifications():
    default = create_runtime(settings())
    try:
        assert default.managed_execution is None
        with TestClient(default.app) as client:
            assert client.post("/enterprise/internal/v1/evaluate", json={}).status_code == 404
    finally:
        default.close()
    configured = create_runtime(settings(managed_execution_keys=(key(),), specifications=(spec(),)))
    try:
        assert configured.managed_execution is not None
        assert configured.managed_execution.capture is configured.input_capture
        with TestClient(configured.app) as client:
            assert client.post("/enterprise/internal/v1/evaluate", json={}).status_code == 401
        assert "/enterprise/internal/v1/evaluate" not in configured.app.openapi()["paths"]
    finally:
        configured.close()


def test_managed_configuration_requires_an_assessment_catalog():
    with pytest.raises(ValueError):
        create_runtime(settings(managed_execution_keys=(key(),)))


def test_operator_configuration_files_load_typed_models_and_keep_secrets_out_of_repr(tmp_path):
    keys = tmp_path / "node-keys.json"
    specs = tmp_path / "specifications.json"
    raw = key().model_dump(mode="json")
    raw["secret"] = "s" * 48
    keys.write_text(json.dumps([raw]), encoding="utf-8")
    specs.write_text("[" + spec().model_dump_json() + "]", encoding="utf-8")
    configured = Settings.from_environment(
        {
            "ENTERPRISE_DATABASE_URL": "postgresql+psycopg://u:p@localhost/enterprise_test",
            "ENTERPRISE_DIFY_CONSOLE_URL": "http://api/console/api",
            "ENTERPRISE_DIFY_SERVICE_URL": "http://api/v1",
            "ENTERPRISE_ALLOWED_ORIGINS": "https://portal.test",
            "ENTERPRISE_MANAGED_EXECUTION_KEYS_FILE": str(keys),
            "ENTERPRISE_SPECIFICATIONS_FILE": str(specs),
        }
    )
    assert configured.managed_execution_keys[0].secret.get_secret_value() == "s" * 48
    assert configured.specifications[0].specification_revision == "s1"
    assert "s" * 48 not in repr(configured)
