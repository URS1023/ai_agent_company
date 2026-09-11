import json
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock, create_autospec, patch
from uuid import UUID

import pytest
from pydantic import SecretStr

from enterprise_platform import worker
from enterprise_platform.application.contracts import Run, RunSpec, RunStatus
from enterprise_platform.application.dispatcher import RunDispatcher
from enterprise_platform.application.errors import AccessDenied, InvalidState, PersistenceError
from enterprise_platform.bootstrap import Runtime, Settings


def arguments(action: str = "dispatch") -> list[str]:
    return [action, "--workspace-id", "workspace-1", "--run-id", "run-1"]


def run_record(status: RunStatus = "dispatched") -> Run:
    return Run(
        id="run-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        request_key="key-1",
        payload_hash="hash",
        spec=RunSpec(
            binding_id="binding-1",
            binding_revision=1,
            device_id="device-1",
            scenario="alert",
            app_id="app-1",
            workflow_id=UUID(int=1),
            specification_revision="spec-1",
            secret_ref="hidden-credential-reference",
            source_id="source-1",
            source_revision="v1",
            read_id="read-1",
            read_revision="v1",
            parameters={"private_parameter": "hidden-command"},
        ),
        input_snapshot={"private_sample": "hidden-snapshot"},
        status=status,
        dispatch_nonce="hidden-worker-nonce",
        reason_code="hidden-internal-reason",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def composition() -> Iterator[tuple[MagicMock, MagicMock, MagicMock]]:
    runtime = create_autospec(Runtime, instance=True)
    runtime.dispatcher = create_autospec(RunDispatcher, instance=True)
    runtime.dispatcher.dispatch.return_value = run_record()
    runtime.dispatcher.reconcile.return_value = run_record("succeeded")
    settings = Settings(
        database_url=SecretStr("postgresql+psycopg://worker:hidden-password@db.test/enterprise_business"),
        dify_console_url="https://dify.test/console/api",
        dify_service_url="https://dify.test/v1",
        allowed_origins=("https://portal.test",),
    )
    with (
        patch.object(worker.Settings, "from_environment", return_value=settings) as load,
        patch.object(worker, "create_runtime", return_value=runtime) as create,
    ):
        yield runtime, load, create


@pytest.mark.parametrize("action", ["dispatch", "reconcile"])
def test_cli_executes_only_one_selected_operation_and_closes_runtime(
    action: str, composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, load, create = composition

    code = worker.main(arguments(action))

    assert code == 0
    load.assert_called_once_with()
    create.assert_called_once_with(load.return_value)
    getattr(runtime.dispatcher, action).assert_called_once_with("workspace-1", "run-1")
    getattr(runtime.dispatcher, "reconcile" if action == "dispatch" else "dispatch").assert_not_called()
    runtime.close.assert_called_once_with()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "run_id": "run-1",
        "status": "dispatched" if action == "dispatch" else "succeeded",
    }
    assert captured.err == ""
    assert "hidden" not in captured.out


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("queued", 0),
        ("claimed", 0),
        ("dispatched", 0),
        ("succeeded", 0),
        ("uncertain", 3),
        ("failed", 4),
        ("cancelled", 4),
    ],
)
def test_observed_status_has_explicit_exit_code_without_retry(
    status: RunStatus,
    expected_code: int,
    composition: tuple[MagicMock, MagicMock, MagicMock],
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime, _, _ = composition
    runtime.dispatcher.dispatch.return_value = run_record(status)

    assert worker.main(arguments()) == expected_code

    assert json.loads(capsys.readouterr().out) == {"run_id": "run-1", "status": status}
    runtime.dispatcher.dispatch.assert_called_once()
    runtime.dispatcher.reconcile.assert_not_called()
    runtime.close.assert_called_once()


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["dispatch"],
        ["dispatch", "--workspace-id", "workspace-1"],
        ["dispatch", "--run-id", "run-1"],
        arguments("hidden-invalid-action"),
        ["dispatch", "--workspace", "workspace-1", "--run-id", "run-1"],
        arguments() + ["--scan"],
        arguments() + ["--retry", "hidden-retry-value"],
    ],
)
def test_usage_rejects_missing_ambiguous_or_unregistered_commands_without_initialization_or_echo(
    argv: list[str], composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, load, create = composition

    with pytest.raises(SystemExit) as stopped:
        worker.main(argv)

    assert stopped.value.code == 2
    assert capsys.readouterr().err == '{"code":"worker_invalid_arguments"}\n'
    load.assert_not_called()
    create.assert_not_called()


@pytest.mark.parametrize("option", ["--workspace-id", "--run-id"])
@pytest.mark.parametrize("value", ["", "x" * 129, "hidden secret", " hidden", "hidden\nsecret", "Bearer hidden", "x/y"])
def test_identifiers_are_bounded_without_echoing_sensitive_values(
    option: str, value: str, composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, load, create = composition
    argv = arguments()
    argv[argv.index(option) + 1] = value

    with pytest.raises(SystemExit) as stopped:
        worker.main(argv)

    assert stopped.value.code == 2
    assert capsys.readouterr().err == '{"code":"worker_invalid_arguments"}\n'
    load.assert_not_called()
    create.assert_not_called()


@pytest.mark.parametrize("stage", ["settings", "composition"])
def test_configuration_failure_is_sanitized_without_dispatch(
    stage: str, composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, load, create = composition
    (load if stage == "settings" else create).side_effect = ValueError("hidden-password")

    assert worker.main(arguments()) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": "worker_configuration_invalid"}
    runtime.dispatcher.dispatch.assert_not_called()
    runtime.close.assert_not_called()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AccessDenied("hidden-token"), "access_denied"),
        (InvalidState("hidden-nonce"), "invalid_state"),
        (PersistenceError("hidden-database"), "persistence_error"),
        (RuntimeError("hidden-driver"), "worker_operation_failed"),
        (KeyboardInterrupt("hidden-interrupt"), "worker_interrupted"),
    ],
)
def test_operation_error_closes_resources_and_outputs_only_a_public_code(
    error: BaseException,
    code: str,
    composition: tuple[MagicMock, MagicMock, MagicMock],
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime, _, _ = composition
    runtime.dispatcher.dispatch.side_effect = error

    assert worker.main(arguments()) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": code}
    runtime.close.assert_called_once()
    runtime.dispatcher.dispatch.assert_called_once()
    runtime.dispatcher.reconcile.assert_not_called()


def test_cleanup_error_is_sanitized_before_emitting_success(
    composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, _, _ = composition
    runtime.close.side_effect = RuntimeError("hidden-cleanup")

    assert worker.main(arguments()) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": "worker_operation_failed"}
    runtime.close.assert_called_once()


@pytest.mark.parametrize("field", ["id", "workspace_id"])
def test_returned_identity_mismatch_is_rejected_without_disclosing_the_other_run(
    field: str, composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, _, _ = composition
    runtime.dispatcher.dispatch.return_value = run_record().model_copy(update={field: "hidden-other-resource"})

    assert worker.main(arguments()) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": "invalid_state"}
    runtime.close.assert_called_once()


def test_help_does_not_construct_runtime_or_read_environment(
    composition: tuple[MagicMock, MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, load, create = composition

    with pytest.raises(SystemExit) as stopped:
        worker.main(["--help"])

    assert stopped.value.code == 0
    assert "dispatch" in capsys.readouterr().out
    load.assert_not_called()
    create.assert_not_called()
