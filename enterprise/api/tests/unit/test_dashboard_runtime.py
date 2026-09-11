from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_bootstrap import settings

from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.dashboard_runtime import DashboardConfiguration


def test_template_catalog_composition_is_explicit_and_has_no_startup_io(tmp_path: Path) -> None:
    # Load the driver before guarding application I/O; binary discovery reads package metadata.
    __import__("psycopg")
    configured = Settings.model_validate(
        {
            **settings().model_dump(),
            "dashboards": {
                "approvals_file": tmp_path / "approvals.json",
                "templates": {"file": tmp_path / "templates.json", "sha256": "a" * 64},
            },
        }
    )
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")),
        patch.object(Path, "open", side_effect=AssertionError("Unexpected file read")),
    ):
        runtime = create_runtime(configured)
        try:
            assert runtime.dashboards.service._templates._path == tmp_path / "templates.json"
            assert runtime.dashboards.service._templates._sha256 == "a" * 64
        finally:
            runtime.close()


@pytest.mark.parametrize(
    "template",
    [
        {"file": "relative.json", "sha256": "a" * 64},
        {"file": "C:/catalog.json"},
        {"file": "C:/catalog.json", "sha256": "invalid"},
    ],
)
def test_catalog_configuration_rejects_unpinned_files(tmp_path: Path, template) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                **settings().model_dump(),
                "dashboards": {
                    "approvals_file": tmp_path / "approvals.json",
                    "templates": template,
                },
            }
        )


def test_dashboard_runtime_is_disabled_by_default() -> None:
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")):
        runtime = create_runtime(settings())
        try:
            assert runtime.dashboards is None
        finally:
            runtime.close()


def test_explicit_dashboard_configuration_composes_without_io(tmp_path: Path) -> None:
    configured = Settings.model_validate(
        {**settings().model_dump(), "dashboards": {"approvals_file": tmp_path / "approvals.json"}}
    )
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")),
        patch.object(Path, "open", side_effect=AssertionError("Unexpected file read")),
    ):
        runtime = create_runtime(configured)
        try:
            assert runtime.dashboards is not None
            assert runtime.dashboards.service is not None
            assert runtime.dashboards.repository is not None
            assert runtime.dashboards.repository._sessions is runtime.repository._sessions
            assert runtime.dashboards.queries._registry._devices is runtime.repository
            assert runtime.dashboards.queries._registry._reads is runtime.input_capture.registry
            assert runtime.dashboards.bindings._registry is runtime.dashboards.queries._registry
            assert runtime.dashboards.bindings._repository is runtime.dashboards.repository
            assert runtime.dashboards.service._binding_service is runtime.dashboards.bindings
            assert runtime.dashboards.service._query_discovery is runtime.dashboards.queries._registry
        finally:
            runtime.close()


def test_dashboard_configuration_rejects_relative_authority_path() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings().model_dump(), "dashboards": {"approvals_file": "relative.json"}})


def test_dashboard_configuration_requires_explicit_environment_key(tmp_path: Path) -> None:
    import json

    env = {
        "ENTERPRISE_DATABASE_URL": "postgresql+psycopg://app:secret@db.test/enterprise_business",
        "ENTERPRISE_DIFY_CONSOLE_URL": "https://dify.test",
        "ENTERPRISE_DIFY_SERVICE_URL": "https://dify.test",
        "ENTERPRISE_ALLOWED_ORIGINS": "https://portal.test",
        "ENTERPRISE_DASHBOARD_JSON": json.dumps({"approvals_file": str(tmp_path / "approvals.json")}),
    }
    parsed = Settings.from_environment(env)
    assert parsed.dashboards.approvals_file == tmp_path / "approvals.json"


def test_dashboard_factory_failure_disposes_shared_engine(tmp_path: Path) -> None:
    configured = Settings.model_validate(
        {**settings().model_dump(), "dashboards": {"approvals_file": tmp_path / "approvals.json"}}
    )
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")),
        patch(
            "enterprise_platform.bootstrap.create_dashboards", side_effect=ValueError("injected configuration failure")
        ),
        patch("sqlalchemy.engine.Engine.dispose") as dispose,
    ):
        with pytest.raises(ValueError, match="injected configuration failure"):
            create_runtime(configured)
        dispose.assert_called_once()


def generation_configuration(tmp_path):
    return {
        "approvals_file": tmp_path / "approvals.json",
        "sql_generation": {
            "grants_file": tmp_path / "schema-grants.json",
            "workspace_id": "workspace-1",
            "app_id": "app-1",
            "workflow_id": "f045ecf1-5c4d-4396-9e94-caa9cc265057",
            "secret_ref": "sql-generator",
        },
    }


def test_sql_generation_composes_real_adapters_without_startup_io(tmp_path):
    from test_workflow_credential_bootstrap import configured

    __import__("psycopg")
    config = configured().model_copy(
        update={"dashboards": DashboardConfiguration.model_validate(generation_configuration(tmp_path))}
    )
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")),
        patch.object(Path, "open", side_effect=AssertionError("Unexpected file read")),
    ):
        runtime = create_runtime(config)
        try:
            generation = runtime.dashboards.service._sql_generation
            assert generation._repository is runtime.dashboards.repository
            assert generation._schemas._reads is runtime.input_capture.registry
            assert generation._schemas._grants._path == tmp_path / "schema-grants.json"
            assert generation._generator._credentials is runtime.workflow_credential_vault
            assert generation._generator._target.workspace_id == "workspace-1"
            assert (
                str(generation._generator._target.workflow_id)
                == generation_configuration(tmp_path)["sql_generation"]["workflow_id"]
            )
        finally:
            runtime.close()


def test_generation_without_credential_vault_fails_configuration_and_disposes_engine(tmp_path):
    config = settings().model_copy(
        update={"dashboards": DashboardConfiguration.model_validate(generation_configuration(tmp_path))}
    )
    with patch("sqlalchemy.engine.Engine.dispose") as dispose:
        with pytest.raises(ValueError, match="SQL generation requires"):
            create_runtime(config)
        dispose.assert_called_once()


@pytest.mark.parametrize(
    "field,value",
    [("grants_file", "relative.json"), ("workflow_id", "latest"), ("workspace_id", ""), ("secret_ref", "")],
)
def test_sql_generation_configuration_requires_explicit_pinned_context(tmp_path, field, value):
    config = generation_configuration(tmp_path)
    config["sql_generation"][field] = value
    with pytest.raises(ValidationError):
        DashboardConfiguration.model_validate(config)


def trial_configuration(tmp_path):
    return {
        "approvals_file": tmp_path / "approvals.json",
        "sql_trial": {
            "grants_file": tmp_path / "trial-grants.json",
            "schema_grants_file": tmp_path / "schema-grants.json",
        },
    }


def test_trial_runtime_is_disabled_without_explicit_configuration(tmp_path):
    configured = settings().model_copy(
        update={"dashboards": DashboardConfiguration(approvals_file=tmp_path / "approvals.json")}
    )
    runtime = create_runtime(configured)
    try:
        assert runtime.dashboards.service._sql_trial is None
        assert runtime.dashboards.service._sql_trial_evidence is None
    finally:
        runtime.close()


@pytest.mark.parametrize("with_sample_policies", [False, True])
def test_trial_composes_without_model_credentials_or_startup_io(tmp_path, with_sample_policies):
    import importlib

    # Driver module loading may read its bundled library; runtime composition must not read grants.
    importlib.import_module("psycopg")
    value = trial_configuration(tmp_path)
    if with_sample_policies:
        value["sql_trial"]["sample_policies_file"] = tmp_path / "sample-policies.json"
    config = settings().model_copy(update={"dashboards": DashboardConfiguration.model_validate(value)})
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")),
        patch.object(Path, "open", side_effect=AssertionError("Unexpected file read")),
    ):
        runtime = create_runtime(config)
        try:
            trial = runtime.dashboards.service._sql_trial
            assert runtime.dashboards.service._sql_trial_evidence._sessions is runtime.dashboards.repository._sessions
            assert runtime.dashboards.service._sql_generation is None
            assert trial._repository is runtime.dashboards.repository
            assert trial._executor._drafts is trial._drafts
            assert trial._executor._authorizer is trial._authorizer
            assert trial._executor._reads is runtime.input_capture.registry
            assert trial._schemas._reads is runtime.input_capture.registry
            assert trial._schemas._grants._path == tmp_path / "schema-grants.json"
            assert trial._authorizer._grants._path == tmp_path / "trial-grants.json"
            if with_sample_policies:
                from enterprise_platform.adapters.dashboard_sql_sample_policies import FileSqlSamplePolicies

                assert isinstance(trial._sample_policies, FileSqlSamplePolicies)
                assert trial._sample_policies._path == tmp_path / "sample-policies.json"
            else:
                assert trial._sample_policies is None
        finally:
            runtime.close()


@pytest.mark.parametrize(
    "case", ["relative_trial", "relative_schema", "same_file", "relative_policy", "policy_trial", "policy_schema"]
)
def test_trial_requires_distinct_absolute_authority_files(tmp_path, case):
    config = trial_configuration(tmp_path)
    if case == "relative_trial":
        config["sql_trial"]["grants_file"] = "relative.json"
    elif case == "relative_schema":
        config["sql_trial"]["schema_grants_file"] = "relative.json"
    elif case == "relative_policy":
        config["sql_trial"]["sample_policies_file"] = "relative.json"
    elif case == "policy_trial":
        config["sql_trial"]["sample_policies_file"] = config["sql_trial"]["grants_file"]
    elif case == "policy_schema":
        config["sql_trial"]["sample_policies_file"] = config["sql_trial"]["schema_grants_file"]
    else:
        config["sql_trial"]["grants_file"] = config["sql_trial"]["schema_grants_file"]
    with pytest.raises(ValidationError):
        DashboardConfiguration.model_validate(config)


def test_trial_configuration_without_source_storage_fails_before_io(tmp_path):
    from unittest.mock import create_autospec

    from sqlalchemy.orm import sessionmaker

    from enterprise_platform.application.input_capture import RegisteredReadRegistry
    from enterprise_platform.application.source_service import DeviceLookup
    from enterprise_platform.dashboard_runtime import create_dashboards

    with (
        patch.object(Path, "open", side_effect=AssertionError("Unexpected file read")),
        pytest.raises(ValueError, match="SQL trials require source storage"),
    ):
        create_dashboards(
            DashboardConfiguration.model_validate(trial_configuration(tmp_path)),
            sessions=sessionmaker(),
            reads=create_autospec(RegisteredReadRegistry, instance=True),
            devices=create_autospec(DeviceLookup, instance=True),
        )


def test_generation_and_trial_share_draft_storage_without_enabling_io(tmp_path):
    from test_workflow_credential_bootstrap import configured

    value = generation_configuration(tmp_path)
    value["sql_trial"] = trial_configuration(tmp_path)["sql_trial"]
    config = configured().model_copy(update={"dashboards": DashboardConfiguration.model_validate(value)})
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected DB connection")),
        patch.object(Path, "open", side_effect=AssertionError("Unexpected file read")),
    ):
        runtime = create_runtime(config)
        try:
            service = runtime.dashboards.service
            assert service._sql_trial._drafts is service._sql_generation._drafts
            assert service._sql_trial._drafts._sessions is runtime.repository._sessions
        finally:
            runtime.close()
