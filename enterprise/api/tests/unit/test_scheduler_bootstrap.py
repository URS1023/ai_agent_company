from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from test_bootstrap import settings
from test_source_bootstrap import environment
from test_workflow_activation_bootstrap import enabled

from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.scheduler_runtime import SchedulerConfiguration


def configuration(tmp_path):
    return SchedulerConfiguration(
        workspace_id="11111111-1111-4111-8111-111111111111",
        actor_id="44444444-4444-4444-8444-444444444444",
        session_file=tmp_path / "session.json",
        grants_file=tmp_path / "grants.json",
    )


def test_scheduler_default_is_not_enabled_or_started() -> None:
    runtime = create_runtime(settings())
    try:
        assert runtime.scheduler is None
    finally:
        runtime.close()


def test_scheduler_requires_activation_configuration(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(settings().model_dump() | {"scheduler": configuration(tmp_path)})


def test_real_scheduler_composition_shares_database_and_read_catalog_without_io(tmp_path) -> None:
    value = enabled().model_copy(update={"scheduler": configuration(tmp_path)})
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("No HTTP")),
        patch("pathlib.Path.open", side_effect=AssertionError("No credential file read")),
        patch("asyncio.create_task", side_effect=AssertionError("No task starts")),
    ):
        runtime = create_runtime(value)
        try:
            scheduler = runtime.scheduler
            assert scheduler is not None
            assert scheduler.service._service_identity is scheduler.identity
            assert scheduler.service._repository._sessions is runtime.repository._sessions
            assert scheduler.service._reads is runtime.input_capture.registry
            assert scheduler.poller._service is scheduler.service
            loop = scheduler.create_loop(AsyncMock())
            assert loop._poller is scheduler.poller
            assert loop._identity is scheduler.identity
        finally:
            runtime.close()


@pytest.mark.parametrize(
    "patch", [{"session_file": "relative.json"}, {"grants_file": "relative.json"}, {"workspace_id": "not-native"}]
)
def test_scheduler_rejects_ambiguous_paths_and_identity(tmp_path, patch) -> None:
    with pytest.raises(ValidationError):
        SchedulerConfiguration.model_validate(configuration(tmp_path).model_dump() | patch)


def test_scheduler_rejects_reusing_grant_file_as_session_file(tmp_path) -> None:
    config = configuration(tmp_path)
    with pytest.raises(ValidationError):
        SchedulerConfiguration.model_validate(config.model_dump() | {"session_file": config.grants_file})


def test_environment_scheduler_configuration_is_not_silently_ignored(tmp_path) -> None:
    with pytest.raises(ValidationError, match="Scheduling requires workflow activation"):
        Settings.from_environment(
            environment() | {"ENTERPRISE_SCHEDULER_JSON": configuration(tmp_path).model_dump_json()}
        )


def test_invalid_environment_scheduler_document_reports_only_setting_name() -> None:
    with pytest.raises(ValueError, match="Invalid ENTERPRISE_SCHEDULER_JSON"):
        Settings.from_environment(environment() | {"ENTERPRISE_SCHEDULER_JSON": "malformed-private-content"})


def test_scheduler_composition_failure_disposes_shared_engine(tmp_path) -> None:
    value = enabled().model_copy(update={"scheduler": configuration(tmp_path)})
    with (
        patch("enterprise_platform.bootstrap.create_scheduler", side_effect=ValueError("bad configuration")),
        patch("sqlalchemy.engine.Engine.dispose") as dispose,
    ):
        with pytest.raises(ValueError, match="bad configuration"):
            create_runtime(value)
        dispose.assert_called_once()
