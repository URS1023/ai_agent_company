import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.app.apps.advanced_chat.generate_response_converter import AdvancedChatAppGenerateResponseConverter
from core.app.apps.agent_chat.generate_response_converter import AgentChatAppGenerateResponseConverter
from core.app.apps.chat.generate_response_converter import ChatAppGenerateResponseConverter
from core.app.entities.queue_entities import QueueErrorEvent
from core.app.entities.task_entities import ChatbotAppStreamResponse, ErrorStreamResponse
from core.app.task_pipeline.based_generate_task_pipeline import BasedGenerateTaskPipeline
from core.app.task_pipeline.workbench_terminal_metadata import with_terminal_metadata
from models.enums import MessageStatus
from models.model import Message


def test_error_metadata_contains_task_and_failed_outcome_not_exception_text():
    event = QueueErrorEvent(error=ValueError("private error text"))
    encoded = with_terminal_metadata('{"usage":{}}', task_id="task", event=event)
    assert json.loads(encoded)["enterprise_generation"] == {
        "version": 1,
        "task_id": "task",
        "outcome": "failed",
        "stop_reason": None,
    }
    assert "private error text" not in encoded


def test_native_error_save_records_terminal_in_same_session():
    app_config = SimpleNamespace(tenant_id="tenant", app_id="app", sensitive_word_avoidance=None)
    pipeline = BasedGenerateTaskPipeline(
        application_generate_entity=SimpleNamespace(task_id="task", app_config=app_config),
        queue_manager=Mock(),
        stream=True,
    )
    message = Message(id="message", status=MessageStatus.NORMAL, message_metadata='{"usage":{}}')
    session = Mock()
    session.scalar.return_value = message
    original = ValueError("original native error")
    assert (
        pipeline.handle_error(event=QueueErrorEvent(error=original), session=session, message_id="message") is original
    )
    assert message.status == MessageStatus.ERROR
    assert json.loads(message.message_metadata)["enterprise_generation"]["outcome"] == "failed"
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "converter",
    [ChatAppGenerateResponseConverter, AgentChatAppGenerateResponseConverter, AdvancedChatAppGenerateResponseConverter],
)
@pytest.mark.parametrize("method", ["convert_stream_full_response", "convert_stream_simple_response"])
def test_native_chat_error_preserves_task_identity(converter, method):
    chunk = ChatbotAppStreamResponse(
        conversation_id="conversation",
        message_id="message",
        created_at=1,
        stream_response=ErrorStreamResponse(task_id="task", err=ValueError("native error")),
    )
    result = list(getattr(converter, method)(iter([chunk])))[0]
    assert result["event"] == "error"
    assert result["task_id"] == "task"
    assert result["conversation_id"] == "conversation"
    assert result["message_id"] == "message"
