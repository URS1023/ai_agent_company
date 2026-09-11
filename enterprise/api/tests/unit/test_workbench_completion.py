import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from test_workbench_context_client import PRINCIPAL, SESSION
from test_workbench_generation_client import body
from test_workbench_messages import receipt
from test_workbench_stream_identity import event

from enterprise_platform.adapters.workbench_context_client import GenerationStateObservation
from enterprise_platform.adapters.workbench_stream import ChatStreamInvalid


def workflow_event(kind, status="succeeded", run_id=None):
    run_id = run_id or str(UUID(int=5))
    return event(kind, workflow_run_id=run_id, data={"id": run_id, "status": status, "finished_at": 123})


def collector(outcome="succeeded"):
    from enterprise_platform.adapters.workbench_completion import ChatStreamCompletion

    value = ChatStreamCompletion(receipt())
    value.observe(workflow_event("workflow_started"))
    if outcome != "failed":
        value.observe(event("message_end"))
    value.observe(workflow_event("workflow_finished", outcome))
    if outcome == "failed":
        failure = event("error")
        del failure["task_id"]
        value.observe(failure)
    return value


def reconcile(value, **changes):
    reader = Mock(read_generation_state=AsyncMock(return_value=GenerationStateObservation(**{**body(), **changes})))
    return asyncio.run(value.reconcile(PRINCIPAL, SESSION, reader)), reader


@pytest.mark.parametrize("outcome", ["succeeded", "partial-succeeded", "failed", "stopped"])
def test_terminal_requires_matching_stream_and_persisted_workflow(outcome):
    result, reader = reconcile(
        collector(outcome), workflow_status=outcome, message_status="error" if outcome == "failed" else "normal"
    )
    assert result.receipt == receipt() and result.outcome == outcome
    reader.read_generation_state.assert_awaited_once_with(PRINCIPAL, SESSION, receipt())


def test_plain_message_end_is_not_workflow_completion():
    from enterprise_platform.adapters.workbench_completion import ChatStreamCompletion

    value = ChatStreamCompletion(receipt())
    value.observe(event("message_end"))
    result, reader = reconcile(value)
    assert result is None
    reader.read_generation_state.assert_awaited_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_status": "running", "workflow_finished": False},
        {"workflow_status": "paused", "workflow_finished": False, "message_status": "paused"},
        {"workflow_run_id": str(UUID(int=6))},
        {"workflow_status": "stopped"},
        {"workflow_run_id": None, "workflow_status": None, "workflow_finished": False},
        {"message_id": str(UUID(int=7))},
        {"actor_id": "other"},
    ],
)
def test_incomplete_or_mismatched_observation_never_releases(changes):
    result, _ = reconcile(collector(), **changes)
    assert result is None


def test_paused_stream_holds_branch_even_when_later_observation_is_terminal():
    value = collector()
    value.observe(event("workflow_paused"))
    result, reader = reconcile(value)
    assert result is None
    reader.read_generation_state.assert_not_called()


@pytest.mark.parametrize(
    "changed",
    [
        event(task_id="other"),
        workflow_event("workflow_finished", run_id=str(UUID(int=9))),
        workflow_event("workflow_finished", status="failed"),
        {
            **workflow_event("workflow_finished"),
            "data": {"id": str(UUID(int=8)), "status": "succeeded", "finished_at": 123},
        },
    ],
)
def test_changed_stream_identity_or_conflicting_finish_is_rejected(changed):
    value = collector()
    with pytest.raises(ChatStreamInvalid):
        value.observe(changed)


def test_workflow_finish_without_message_end_holds_successful_branch():
    from enterprise_platform.adapters.workbench_completion import ChatStreamCompletion

    value = ChatStreamCompletion(receipt())
    value.observe(workflow_event("workflow_started"))
    value.observe(workflow_event("workflow_finished"))
    result, reader = reconcile(value)
    assert result is None
    reader.read_generation_state.assert_not_called()


def test_success_with_late_error_is_not_released():
    value = collector()
    value.observe(event("error"))
    result, reader = reconcile(value)
    assert result is None
    reader.read_generation_state.assert_not_called()


@pytest.mark.parametrize("outcome, reason", [("succeeded", None), ("stopped", "user_manual")])
def test_plain_chat_requires_persisted_task_terminal(outcome, reason):
    from enterprise_platform.adapters.workbench_completion import ChatStreamCompletion

    value = ChatStreamCompletion(receipt())
    value.observe(event("message_end"))
    result, _ = reconcile(
        value,
        workflow_run_id=None,
        workflow_status=None,
        workflow_finished=False,
        message_terminal={"version": 1, "task_id": receipt().task_id, "outcome": outcome, "stop_reason": reason},
    )
    assert result.receipt == receipt() and result.outcome == outcome


def test_plain_error_requires_matching_failed_marker():
    from enterprise_platform.adapters.workbench_completion import ChatStreamCompletion

    value = ChatStreamCompletion(receipt())
    value.observe(event("error"))
    result, _ = reconcile(
        value,
        workflow_run_id=None,
        workflow_status=None,
        workflow_finished=False,
        message_status="error",
        message_terminal={"version": 1, "task_id": receipt().task_id, "outcome": "failed", "stop_reason": None},
    )
    assert result.receipt == receipt() and result.outcome == "failed"
