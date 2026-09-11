import pytest
from pydantic import TypeAdapter, ValidationError

from enterprise_platform.application.source_contracts import SourceDraft, SourceView


def db_draft() -> dict[str, object]:
    return {
        "name": "采集库",
        "device_ids": ["device-1"],
        "device_parameter": "device",
        "device_column": "device_code",
        "read_only_confirmed": True,
        "connection": {
            "kind": "db",
            "dialect": "postgresql",
            "host": "collector.test",
            "port": 5432,
            "database": "measurements",
            "username": "reader",
            "password": "fixture-password",
            "allowed_tables": ["measurements"],
            "sql": "SELECT * FROM measurements WHERE device_code = :device",
        },
    }


def test_database_form_is_typed_without_user_controlled_server_identity() -> None:
    value = SourceDraft.model_validate(db_draft())
    assert value.connection.kind == "db"
    assert value.connection.password.get_secret_value() == "fixture-password"
    for key in ("workspace_id", "actor_id", "source_id", "source_revision", "read_id", "read_revision", "revision"):
        with pytest.raises(ValidationError):
            SourceDraft.model_validate({**db_draft(), key: "forged"})
    assert "fixture-password" not in repr(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"device_ids": []},
        {"device_ids": ["device-1", "device-1"]},
        {"read_only_confirmed": False},
        {"device_parameter": "bad-name"},
    ],
)
def test_source_form_rejects_invalid_or_unconfirmed_scope(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SourceDraft.model_validate({**db_draft(), **changes})


def test_http_form_has_no_duplicate_parameter_names_and_accepts_explicit_read_policy() -> None:
    value = SourceDraft.model_validate(
        {
            **db_draft(),
            "connection": {
                "kind": "http",
                "url": "https://collector.test/measurements",
                "method": "GET",
                "rows_path": ["items"],
                "headers": [{"name": "Authorization", "value": "Bearer fixture-token"}],
            },
        }
    )
    assert value.connection.kind == "http"
    assert "fixture-token" not in repr(value)
    with pytest.raises(ValidationError):
        SourceDraft.model_validate(
            {**db_draft(), "parameters": [{"input_key": "sample", "parameter_name": "device", "kind": "string"}]}
        )


def test_source_output_schema_contains_no_credential_fields() -> None:
    schema = TypeAdapter(SourceView).json_schema()
    for definition in schema["$defs"].values():
        fields = definition.get("properties", {})
        assert not {"username", "password", "connection_url", "headers", "secret", "ciphertext"} & fields.keys()
    draft_schema = SourceDraft.model_json_schema()
    assert draft_schema["properties"]["connection"]["discriminator"]["propertyName"] == "kind"
    assert draft_schema["$defs"]["DbSourceDraft"]["properties"]["password"]["anyOf"][0]["writeOnly"] is True
