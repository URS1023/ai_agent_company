import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.openapi.models import OpenAPI

from enterprise_platform.application.contracts import JsonValue
from enterprise_platform.export_openapi import build_openapi, main, serialize_openapi


def test_export_uses_real_schema_without_runtime_dependencies() -> None:
    original_getenv = os.getenv

    def reject_deployment_setting(name: str, default: str | None = None) -> str | None:
        if name.startswith(("ENTERPRISE_", "DATABASE_", "DB_", "DIFY_")):
            raise AssertionError("Deployment environment read during export")
        return original_getenv(name, default)

    with (
        patch("os.getenv", side_effect=reject_deployment_setting),
        patch("enterprise_platform.bootstrap.Settings.from_environment", side_effect=AssertionError("Settings read")),
        patch("enterprise_platform.export_openapi._SchemaOnlyIdentity.resolve", side_effect=AssertionError("Identity")),
        patch("sqlalchemy.create_engine", side_effect=AssertionError("Engine creation during export")),
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Database connection during export")),
        patch("sqlalchemy.orm.Session.__init__", side_effect=AssertionError("Session creation during export")),
        patch("httpx.Client.send", side_effect=AssertionError("HTTP request during export")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("HTTP request during export")),
        patch("socket.create_connection", side_effect=AssertionError("Network connection during export")),
    ):
        document = build_openapi()
    OpenAPI.model_validate(document)
    assert "/enterprise/api/v1/devices/{device_id}/bindings/{scenario}/runs" in document["paths"]


def test_serialized_export_is_repeatable_and_contains_no_deployment_values() -> None:
    with patch.dict(
        "os.environ",
        {
            "ENTERPRISE_DATABASE_URL": "fixture-private-database-value",
            "ENTERPRISE_ALLOWED_ORIGINS": "https://fixture-private-deployment.example",
            "ENTERPRISE_WORKFLOW_CREDENTIALS_FILE": "fixture-private-credentials-path",
        },
    ):
        first = serialize_openapi()
        second = serialize_openapi()
    assert first == second
    assert first.endswith("\n")
    assert "fixture-private" not in first
    assert "dispatch_nonce" not in first
    assert "servers" not in json.loads(first)


def test_every_exported_reference_resolves_inside_the_document() -> None:
    document = build_openapi()
    pending: list[JsonValue] = [document]
    references: set[str] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                references.add(reference)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert references
    for reference in references:
        assert reference.startswith("#/")
        target: JsonValue = document
        for segment in reference[2:].split("/"):
            assert isinstance(target, dict)
            target = target[segment.replace("~1", "/").replace("~0", "~")]


def test_normalization_removes_origin_enum_but_keeps_required_header_contract() -> None:
    from enterprise_platform.http.app import create_app

    def deployment_app(*args: object, **kwargs: object):
        app = create_app(*args, **kwargs)
        app.allowed_origins = ("https://fixture-deployment.example",)
        return app

    with patch("enterprise_platform.export_openapi.create_app", side_effect=deployment_app):
        document = build_openapi()
    operation = document["paths"]["/enterprise/api/v1/devices"]["post"]
    origin = next(parameter for parameter in operation["parameters"] if parameter["name"] == "Origin")
    assert origin["required"] is True
    assert origin["description"]
    assert "enum" not in origin["schema"]
    assert "fixture-deployment" not in json.dumps(document)


def test_run_view_never_includes_internal_snapshot_or_secret_reference() -> None:
    document = build_openapi()
    properties = document["components"]["schemas"]["RunView"]["properties"]
    assert not {"dispatch_nonce", "secret_ref", "input_snapshot", "spec"} & properties.keys()
    assert {"status", "has_input_snapshot", "input_snapshot_digest", "parameters"} <= properties.keys()


def test_cli_writes_exact_utf8_document_to_an_explicit_path(tmp_path: Path) -> None:
    target = tmp_path / "contracts" / "business-openapi.json"
    assert main(["--output", str(target)]) == 0
    assert target.read_bytes() == serialize_openapi().encode("utf-8")
    assert main(["--output", str(target), "--check"]) == 0


def test_cli_check_reports_drift_without_overwriting_the_file(tmp_path: Path) -> None:
    target = tmp_path / "business-openapi.json"
    target.write_text("{}\n", encoding="utf-8")
    assert main(["--output", str(target), "--check"]) == 1
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_cli_requires_an_explicit_output_destination() -> None:
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
