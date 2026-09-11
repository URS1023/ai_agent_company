from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from enterprise_platform.application.input_capture import InputCaptureService
from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.persistence.repository import SqlAlchemyRepository


def settings() -> Settings:
    return Settings(
        database_url=SecretStr("postgresql+psycopg://app:secret@db.test/enterprise_business"),
        dify_console_url="https://dify.test/console/api",
        dify_service_url="https://dify.test/v1",
        allowed_origins=("https://portal.test",),
    )


def test_runtime_constructs_real_adapters_without_database_connection_or_migrations() -> None:
    with patch(
        "sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Database connection during composition")
    ):
        runtime = create_runtime(settings())
        try:
            assert isinstance(runtime.repository, SqlAlchemyRepository)
            assert isinstance(runtime.input_capture, InputCaptureService)
            assert runtime.input_capture.repository is runtime.repository
            assert runtime.app.openapi()["info"]["title"]
        finally:
            runtime.close()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://app:secret@db.test/dify",
        "postgresql+psycopg://app:secret@db.test/postgres",
        "postgresql+psycopg://app:secret@db.test/business",
        "postgresql+psycopg://app:secret@db.test/enterprise_UPPER",
        "postgresql+psycopg://app:secret@db.test/enterprise_" + "x" * 49,
        "postgresql+psycopg://app:secret@db.test/enterprise_business?application_name=migration-incompatible",
        "sqlite:///enterprise.db",
        "postgresql+psycopg://app:secret@db.test/enterprise_business?options=-csearch_path%3Dnative",
    ],
)
def test_production_settings_require_dedicated_enterprise_database(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings().model_dump(), "database_url": SecretStr(url)})


@pytest.mark.parametrize("origin", ["*", "https://portal.test/path", "https://user:password@portal.test", "null"])
def test_origins_are_exact_server_owned_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings().model_dump(), "allowed_origins": (origin,)})


def test_environment_loader_never_falls_back_to_native_database_url() -> None:
    with pytest.raises(ValueError, match="ENTERPRISE_DATABASE_URL"):
        Settings.from_environment({"DATABASE_URL": "postgresql://native/native"})


def test_environment_loader_uses_explicit_names_and_does_not_echo_secrets() -> None:
    value = Settings.from_environment(
        {
            "ENTERPRISE_DATABASE_URL": "postgresql+psycopg://app:hidden-password@db.test/enterprise_business",
            "ENTERPRISE_DIFY_CONSOLE_URL": "https://dify.test/console/api",
            "ENTERPRISE_DIFY_SERVICE_URL": "https://dify.test/v1",
            "ENTERPRISE_ALLOWED_ORIGINS": "https://portal.test,https://portal2.test",
        }
    )
    assert value.allowed_origins == ("https://portal.test", "https://portal2.test")
    assert "hidden-password" not in repr(value)


def test_credentials_are_loaded_only_from_explicit_server_file(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text('[{"workspace_id":"w","app_id":"a","secret_ref":"ref","api_key":"fixture-key"}]', encoding="utf-8")
    config = Settings.from_environment(
        {
            "ENTERPRISE_DATABASE_URL": "postgresql+psycopg://app:secret@db.test/enterprise_business",
            "ENTERPRISE_DIFY_CONSOLE_URL": "https://dify.test/console/api",
            "ENTERPRISE_DIFY_SERVICE_URL": "https://dify.test/v1",
            "ENTERPRISE_ALLOWED_ORIGINS": "https://portal.test",
            "ENTERPRISE_WORKFLOW_CREDENTIALS_FILE": str(path),
        }
    )
    assert config.workflow_credentials[0].secret_ref == "ref"
    assert "fixture-key" not in repr(config)


def test_read_catalog_is_loaded_explicitly_without_exposing_connection_secrets(tmp_path) -> None:
    path = tmp_path / "read-catalog.json"
    path.write_text(
        """[{
      "connection": {
        "source": {"workspace_id": "w", "source_id": "s", "revision": "s1"},
        "dialect": "postgresql", "connection_url": "postgresql+psycopg://reader:hidden-source-secret@source/measurements",
        "allowed_tables": ["measurements"], "read_only_role": true
      },
      "read": {
        "source": {"workspace_id": "w", "source_id": "s", "revision": "s1"},
        "read_id": "r", "revision": "r1", "sql": "SELECT device_code FROM measurements WHERE device_code = :device"
      },
      "device_ids": ["d"], "device_parameter": "device", "device_column": "device_code"
    }]""",
        encoding="utf-8",
    )
    config = Settings.from_environment(
        {
            "ENTERPRISE_DATABASE_URL": "postgresql+psycopg://app:secret@db.test/enterprise_business",
            "ENTERPRISE_DIFY_CONSOLE_URL": "https://dify.test/console/api",
            "ENTERPRISE_DIFY_SERVICE_URL": "https://dify.test/v1",
            "ENTERPRISE_ALLOWED_ORIGINS": "https://portal.test",
            "ENTERPRISE_READ_CATALOG_FILE": str(path),
        }
    )
    assert config.registered_reads[0].read.read_id == "r"
    assert "hidden-source-secret" not in repr(config)
