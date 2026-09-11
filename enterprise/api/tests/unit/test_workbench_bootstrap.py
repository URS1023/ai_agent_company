import json
from unittest.mock import patch

import pytest
from test_bootstrap import settings
from test_workflow_setup_bootstrap import environment

from enterprise_platform.bootstrap import Settings, create_runtime


def configuration(**changes):
    from enterprise_platform.workbench_runtime import WorkbenchConfiguration

    return WorkbenchConfiguration(**changes)


def test_workbench_is_opt_in_with_bounded_default_settings():
    assert settings().workbench is None
    config = Settings.from_environment({**environment(), "ENTERPRISE_WORKBENCH_JSON": "{}"})
    assert config.workbench == configuration()
    assert config.workbench.generation_timeout_seconds == 300
    assert config.workbench.preflight_timeout_seconds == 30


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        "true",
        '{"api_key":"private-value"}',
        '{"generation_timeout_seconds":0}',
        '{"generation_timeout_seconds":true}',
        '{"generation_timeout_seconds":1801}',
        '{"preflight_timeout_seconds":"30"}',
        '{"max_response_bytes":2097153}',
    ],
)
def test_invalid_configuration_fails_without_echoing_input(raw):
    with pytest.raises(ValueError, match="ENTERPRISE_WORKBENCH_JSON") as caught:
        Settings.from_environment({**environment(), "ENTERPRISE_WORKBENCH_JSON": raw})
    assert "private-value" not in str(caught.value)


def test_real_workbench_composition_is_lazy_and_uses_one_shared_enterprise_session_factory():
    from enterprise_platform.adapters.workbench_context_client import DifyWorkbenchContextClient
    from enterprise_platform.adapters.workbench_dispatch import WorkbenchChatDispatcher
    from enterprise_platform.application.workbench_service import WorkbenchSendAuthority, WorkbenchSendService
    from enterprise_platform.persistence.workbench_branch_repository import SqlAlchemyBranchRepository
    from enterprise_platform.persistence.workbench_repository import SqlAlchemyMessageIntentRepository

    config = Settings.model_validate(
        {
            **settings().model_dump(),
            "workbench": {
                "generation_timeout_seconds": 480,
                "preflight_timeout_seconds": 15,
                "max_response_bytes": 1048576,
            },
        }
    )
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected database I/O")):
        runtime = create_runtime(config)
        try:
            workbench = runtime.workbench
            assert workbench is not None
            assert isinstance(workbench.dispatcher, WorkbenchChatDispatcher)
            assert isinstance(workbench.messages, SqlAlchemyMessageIntentRepository)
            assert isinstance(workbench.branches, SqlAlchemyBranchRepository)
            assert workbench.messages._sessions is workbench.branches._sessions
            assert workbench.messages._sessions.kw["bind"] is runtime.engine
            sender = workbench.dispatcher._preparer
            assert isinstance(sender, WorkbenchSendService)
            assert sender._repository is workbench.messages
            assert isinstance(sender._authority, WorkbenchSendAuthority)
            assert sender._authority._branches is workbench.branches
            assert sender._authority._inputs is sender._context_checker
            assert workbench.branch_service._repository is workbench.branches
            assert workbench.branch_service._access is sender._context_checker
            assert workbench.dispatcher._completion_reader is sender._context_checker
            assert isinstance(sender._context_checker, DifyWorkbenchContextClient)
            assert sender._context_checker._timeout_seconds == 15
            assert workbench.dispatcher._native._timeout_seconds == 480
            assert workbench.dispatcher._native._max_response_bytes == 1048576
            assert str(workbench.dispatcher._native._base_url).rstrip("/") == config.dify_console_url
            operation = runtime.app.openapi()["paths"][
                "/enterprise/api/v1/workbench/apps/{installed_app_id}/branches/{branch_id}/messages"
            ]["post"]
            assert "text/event-stream" in operation["responses"]["200"]["content"]
        finally:
            runtime.close()


def test_disabled_workbench_constructs_no_adapters():
    with patch("enterprise_platform.bootstrap.create_workbench") as factory:
        runtime = create_runtime(settings())
        try:
            assert runtime.workbench is None
            factory.assert_not_called()
        finally:
            runtime.close()


def test_composition_failure_disposes_engine():
    from sqlalchemy.engine import Engine

    config = Settings.from_environment({**environment(), "ENTERPRISE_WORKBENCH_JSON": json.dumps({})})
    with (
        patch("enterprise_platform.bootstrap.create_workbench", side_effect=ValueError("fixture")),
        patch.object(Engine, "dispose", autospec=True) as dispose,
    ):
        with pytest.raises(ValueError, match="fixture"):
            create_runtime(config)
    dispose.assert_called_once()


@pytest.mark.parametrize("enabled", [False, True])
def test_runtime_http_route_uses_configured_dispatcher_after_native_identity(enabled):
    from unittest.mock import AsyncMock
    from uuid import UUID

    from fastapi.testclient import TestClient
    from test_workbench_context_client import PRINCIPAL

    from enterprise_platform.application.errors import AccessDenied

    config = Settings.model_validate({**settings().model_dump(), "workbench": {} if enabled else None})
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("Unexpected database I/O")),
        patch(
            "enterprise_platform.bootstrap.DifyIdentityClient.resolve", new_callable=AsyncMock, return_value=PRINCIPAL
        ) as resolve,
        patch(
            "enterprise_platform.application.workbench_service.WorkbenchSendService.prepare",
            new_callable=AsyncMock,
            side_effect=AccessDenied("private-detail"),
        ) as prepare,
    ):
        runtime = create_runtime(config)
        try:
            with TestClient(runtime.app) as client:
                response = client.post(
                    f"/enterprise/api/v1/workbench/apps/{UUID(int=1)}/branches/branch/messages",
                    json={"client_message_id": str(UUID(int=2)), "payload": {"query": "hello", "inputs": {}}},
                    headers={
                        "origin": "https://portal.test",
                        "cookie": "csrf_token=csrf",
                        "authorization": "Bearer session",
                        "x-csrf-token": "csrf",
                    },
                )
            resolve.assert_awaited_once()
            assert prepare.await_count == int(enabled)
            assert response.status_code == (200 if enabled else 503)
            if enabled:
                assert prepare.call_args.args[0] == PRINCIPAL
                assert '"code":"access_denied"' in response.text
            assert "private-detail" not in response.text
        finally:
            runtime.close()
