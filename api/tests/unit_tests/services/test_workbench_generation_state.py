from datetime import datetime
from importlib import import_module
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from graphon.enums import WorkflowExecutionStatus
from models import Account
from models.enums import MessageStatus
from models.model import App, Conversation, Message
from models.workflow import WorkflowRun
from repositories.api_workflow_run_repository import APIWorkflowRunRepository
from services.errors.message import MessageNotExistsError


@pytest.fixture
def setup():
    read = import_module("services.workbench_context_service").read_workbench_generation_state
    account = Account(name="User", email="user@example.test")
    account.id = "actor"
    app = App(id="app", tenant_id="tenant")
    message = Message(id="message", conversation_id="conversation", status=MessageStatus.NORMAL, workflow_run_id="run")
    session = Mock(spec=Session)
    session.scalar.side_effect = [Conversation(id="conversation"), message]
    runs = Mock(spec=APIWorkflowRunRepository)
    runs.get_workflow_run_by_id.return_value = WorkflowRun(
        id="run",
        tenant_id="tenant",
        app_id="app",
        created_by="actor",
        created_by_role="account",
        status=WorkflowExecutionStatus.SUCCEEDED,
        finished_at=datetime(2026, 9, 11),
    )
    return read, account, app, message, session, runs


def invoke(setup):
    read, account, app, _, session, runs = setup
    return read(
        app_model=app,
        user=account,
        session=session,
        workflow_runs=runs,
        conversation_id="conversation",
        message_id="message",
    )


def test_uses_scoped_native_repository_and_returns_no_content(setup):
    result = invoke(setup)
    assert result.workflow_finished is True
    assert result.workflow_status == WorkflowExecutionStatus.SUCCEEDED
    setup[5].get_workflow_run_by_id.assert_called_once_with(tenant_id="tenant", app_id="app", run_id="run")
    assert not hasattr(result, "answer")
    assert not hasattr(result, "inputs")
    setup[4].commit.assert_not_called()


@pytest.mark.parametrize("status", [WorkflowExecutionStatus.RUNNING, WorkflowExecutionStatus.PAUSED])
def test_nonterminal_run_never_finishes_even_with_timestamp(setup, status):
    setup[5].get_workflow_run_by_id.return_value.status = status
    assert invoke(setup).workflow_finished is False


def test_paused_message_does_not_finish_on_terminal_run_snapshot(setup):
    setup[3].status = MessageStatus.PAUSED
    assert invoke(setup).workflow_finished is False


def test_finished_timestamp_required(setup):
    setup[5].get_workflow_run_by_id.return_value.finished_at = None
    assert invoke(setup).workflow_finished is False


def test_normal_message_without_workflow_does_not_prove_completion(setup):
    setup[3].workflow_run_id = None
    result = invoke(setup)
    assert result.workflow_finished is False
    assert result.workflow_status is None
    setup[5].get_workflow_run_by_id.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "other"),
        ("app_id", "other"),
        ("created_by", "other"),
        ("created_by_role", "end_user"),
        ("id", "other"),
    ],
)
def test_foreign_run_projection_rejected(setup, field, value):
    setattr(setup[5].get_workflow_run_by_id.return_value, field, value)
    with pytest.raises(MessageNotExistsError):
        invoke(setup)


def test_different_conversation_rejected_before_workflow_lookup(setup):
    setup[3].conversation_id = "other"
    with pytest.raises(MessageNotExistsError):
        invoke(setup)
    setup[5].get_workflow_run_by_id.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.STOPPED, WorkflowExecutionStatus.PARTIAL_SUCCEEDED],
)
def test_all_native_terminal_workflow_outcomes_are_preserved(setup, status):
    setup[5].get_workflow_run_by_id.return_value.status = status
    result = invoke(setup)
    assert result.workflow_finished is True
    assert result.workflow_status == status


def test_missing_native_run_is_not_a_completion(setup):
    setup[5].get_workflow_run_by_id.return_value = None
    with pytest.raises(MessageNotExistsError):
        invoke(setup)


def test_saved_terminal_metadata_is_projected_for_owned_plain_chat(setup):
    from core.app.entities.queue_entities import QueueMessageEndEvent
    from core.app.task_pipeline.workbench_terminal_metadata import with_terminal_metadata

    setup[2].mode = "chat"
    setup[3].workflow_run_id = None
    setup[3].message_metadata = with_terminal_metadata("{}", task_id="task", event=QueueMessageEndEvent())
    result = invoke(setup)
    assert result.message_terminal.task_id == "task"
    assert result.message_terminal.outcome == "succeeded"
    assert result.workflow_finished is False


@pytest.mark.parametrize("mode", ["advanced-chat", "completion"])
def test_plain_marker_does_not_override_other_native_modes(setup, mode):
    setup[2].mode = mode
    setup[3].workflow_run_id = None
    setup[
        3
    ].message_metadata = (
        '{"enterprise_generation":{"version":1,"task_id":"task","outcome":"succeeded","stop_reason":null}}'
    )
    assert invoke(setup).message_terminal is None


@pytest.mark.parametrize("status", [MessageStatus.ERROR, MessageStatus.PAUSED])
def test_plain_marker_does_not_override_non_normal_message(setup, status):
    setup[2].mode = "chat"
    setup[3].workflow_run_id = None
    setup[3].status = status
    setup[
        3
    ].message_metadata = (
        '{"enterprise_generation":{"version":1,"task_id":"task","outcome":"succeeded","stop_reason":null}}'
    )
    assert invoke(setup).message_terminal is None


@pytest.mark.parametrize("metadata", [None, "{}", "bad", '{"enterprise_generation":{"version":2}}'])
def test_missing_or_invalid_marker_is_not_completion(setup, metadata):
    setup[2].mode = "chat"
    setup[3].workflow_run_id = None
    setup[3].message_metadata = metadata
    assert invoke(setup).message_terminal is None


def test_plain_failed_marker_is_returned_only_for_error_message(setup):
    setup[2].mode = "chat"
    setup[3].workflow_run_id = None
    setup[3].status = MessageStatus.ERROR
    setup[3].message_metadata = (
        '{"enterprise_generation":{"version":1,"task_id":"task","outcome":"failed","stop_reason":null}}'
    )
    result = invoke(setup)
    assert result.message_terminal.outcome == "failed"
    assert result.message_terminal.task_id == "task"
