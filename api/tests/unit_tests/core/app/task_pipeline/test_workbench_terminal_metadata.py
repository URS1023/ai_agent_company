import json
from importlib import import_module

import pytest

from core.app.entities.queue_entities import QueueMessageEndEvent, QueueStopEvent


def encode(metadata: str, event: QueueMessageEndEvent | QueueStopEvent | None, task_id: str = "task") -> str:
    module = import_module("core.app.task_pipeline.workbench_terminal_metadata")
    return module.with_terminal_metadata(metadata, task_id=task_id, event=event)


def test_no_terminal_event_preserves_original_metadata_bytes():
    original = '{ "usage": {"total_tokens": 3} }'
    assert encode(original, None) == original


def test_message_end_adds_only_server_terminal_record():
    original = {"usage": {"total_tokens": 3}, "retriever_resources": []}
    result = json.loads(encode(json.dumps(original), QueueMessageEndEvent()))
    assert result.pop("enterprise_generation") == {
        "version": 1,
        "task_id": "task",
        "outcome": "succeeded",
        "stop_reason": None,
    }
    assert result == original


@pytest.mark.parametrize("reason", list(QueueStopEvent.StopBy))
def test_native_stop_reason_is_persisted_without_success_inference(reason):
    result = json.loads(encode("{}", QueueStopEvent(stopped_by=reason)))
    assert result["enterprise_generation"] == {
        "version": 1,
        "task_id": "task",
        "outcome": "stopped",
        "stop_reason": reason.value,
    }


@pytest.mark.parametrize("task_id", ["", " ", "x" * 129])
def test_invalid_task_identity_is_not_recorded(task_id):
    with pytest.raises(ValueError):
        encode("{}", QueueMessageEndEvent(), task_id)


@pytest.mark.parametrize(
    ("event", "outcome"),
    [(QueueMessageEndEvent(), "succeeded"), (QueueStopEvent(stopped_by=QueueStopEvent.StopBy.USER_MANUAL), "stopped")],
)
def test_real_save_includes_terminal_metadata_with_existing_fields(monkeypatch, event, outcome):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from models.model import AppMode
    from tests.unit_tests.core.app.task_pipeline.test_easy_ui_based_generate_task_pipeline_core import (
        _make_conversation,
        _make_message,
        _make_pipeline,
    )

    pipeline, _ = _make_pipeline()
    pipeline._workbench_terminal = event
    pipeline._model_config = SimpleNamespace(mode="chat")
    message = _make_message()
    session = Mock()
    session.scalar.side_effect = [message, _make_conversation(AppMode.CHAT)]
    monkeypatch.setattr("core.app.task_pipeline.easy_ui_based_generate_task_pipeline.message_was_created.send", Mock())
    pipeline._save_message(session=session)
    metadata = json.loads(message.message_metadata)
    assert metadata["enterprise_generation"]["outcome"] == outcome
    assert metadata["enterprise_generation"]["task_id"] == pipeline._application_generate_entity.task_id
    assert "usage" in metadata
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "event", [QueueMessageEndEvent(), QueueStopEvent(stopped_by=QueueStopEvent.StopBy.USER_MANUAL)]
)
def test_queue_terminal_is_selected_before_message_save(monkeypatch, event):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, Mock

    from core.app.entities.task_entities import MessageEndStreamResponse
    from tests.unit_tests.core.app.task_pipeline.test_easy_ui_based_generate_task_pipeline_core import (
        _make_pipeline,
        _queue_message,
        _set_queue_events,
    )

    pipeline, _ = _make_pipeline()
    _set_queue_events(pipeline, [_queue_message(event)])
    pipeline._handle_stop = Mock()
    pipeline.handle_output_moderation_when_task_finished = Mock(return_value=None)
    captured = []
    pipeline._save_message = Mock(side_effect=lambda **kwargs: captured.append(pipeline._workbench_terminal))
    pipeline._message_end_to_stream_response = Mock(return_value=MessageEndStreamResponse(task_id="task", id="message"))
    monkeypatch.setattr(
        "core.app.task_pipeline.easy_ui_based_generate_task_pipeline.db", SimpleNamespace(engine=Mock())
    )
    monkeypatch.setattr("core.app.task_pipeline.easy_ui_based_generate_task_pipeline.sessionmaker", MagicMock())
    list(pipeline._process_stream_response(publisher=None, trace_manager=None))
    assert captured == [event]


@pytest.mark.parametrize(
    "marker",
    [
        {"version": True, "task_id": "task", "outcome": "succeeded", "stop_reason": None},
        {"version": 1, "task_id": "task", "outcome": "stopped", "stop_reason": None},
        {"version": 1, "task_id": "task", "outcome": "succeeded", "stop_reason": "user_manual"},
        {"version": 1, "task_id": "task", "outcome": "stopped", "stop_reason": "unknown"},
    ],
)
def test_malformed_terminal_marker_is_unproven(marker):
    from core.app.task_pipeline.workbench_terminal_metadata import read_terminal_metadata

    assert read_terminal_metadata(json.dumps({"enterprise_generation": marker})) is None
