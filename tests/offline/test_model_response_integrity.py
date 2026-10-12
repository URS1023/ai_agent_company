"""Protocol-level reply integrity and unlimited model reads; no DB or network."""
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
import pytest

from app.errors import ModelResponseIncompleteError, ModelUnavailableError
from app.modules.runtime.task_control import TaskControl, current
from app.providers import model

TOOLS = [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object'}}}]


def chunk(content=None, *, finish=None, calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=calls), finish_reason=finish)])


def tool_delta(index=0, *, call_id=None, name=None, arguments=None):
    return NS(index=index, id=call_id, type='function', function=NS(name=name, arguments=arguments))


@pytest.fixture
def gateway(monkeypatch):
    client = MagicMock()
    client.with_options.return_value = client
    persisted = MagicMock()
    monkeypatch.setattr(model, '_client', lambda *a: client)
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'base_url': 'https://synthetic.invalid', 'api_key': 'synthetic', 'model': 'synthetic'})
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    monkeypatch.setattr(model, '_persist_usage', persisted)
    return client, persisted


def streaming(gateway, chunks):
    client, persisted = gateway
    stream = MagicMock()
    stream.__iter__.return_value = iter(chunks)
    client.chat.completions.create.return_value = stream
    return stream


@pytest.mark.parametrize('content,finish,reason', [
    ('', 'stop', 'empty_response'), ('  \n', 'stop', 'empty_response'),
    ('partial answer', None, 'missing_finish_reason'), ('partial answer', 'length', 'truncated_response'),
    ('', 'tool_calls', 'invalid_tool_call'), ('filtered', 'content_filter', 'content_filter'),
])
@pytest.mark.parametrize('entry', ['stream', 'chat'])
def test_invalid_responses_are_failed_and_never_delivered(gateway, content, finish, reason, entry):
    client, persisted = gateway
    done = MagicMock()
    if entry == 'stream':
        stream = streaming(gateway, [chunk(content, finish=finish)])
        invoke = lambda: model.stream_chat('reasoning', [], on_done=done)
    else:
        client.chat.completions.create.return_value = NS(choices=[
            NS(message=NS(content=content, tool_calls=None, reasoning_content='hidden thinking'), finish_reason=finish)],
            usage=NS(prompt_tokens=3, completion_tokens=2, total_tokens=5))
        invoke = lambda: model.chat_for_run('reasoning', [])
    with pytest.raises(ModelResponseIncompleteError) as raised:
        invoke()
    assert raised.value.extra['reason'] == reason
    assert raised.value.extra['retryable'] is (reason != 'content_filter')
    assert persisted.call_args.kwargs['status'] == 'failed'
    assert persisted.call_args.kwargs['error_code'] == 'model_response_incomplete'
    assert client.chat.completions.create.call_count == 1
    done.assert_not_called()
    client.close.assert_called_once()
    if entry == 'stream':
        stream.close.assert_called_once()
    else:
        assert persisted.call_args.kwargs['usage']['total_tokens'] == 5


def test_empty_choices_records_usage_and_a_protocol_error(gateway):
    client, persisted = gateway
    client.chat.completions.create.return_value = NS(choices=[], usage=NS(total_tokens=7))
    with pytest.raises(ModelResponseIncompleteError):
        model.chat_for_run('reasoning', [])
    assert persisted.call_args.kwargs['usage']['total_tokens'] == 7
    assert persisted.call_args.kwargs['status'] == 'failed'


@pytest.mark.parametrize('arguments', ['{"q":', 'null', '[]', 'true', '{"n":NaN}', '{"q":1,"q":2}'])
def test_malformed_or_ambiguous_tool_json_is_never_delivered(gateway, arguments):
    stream = streaming(gateway, [chunk(finish='tool_calls', calls=[
        tool_delta(call_id='call_1', name='lookup', arguments=arguments)])])
    done = MagicMock()
    with pytest.raises(ModelResponseIncompleteError) as raised:
        model.stream_chat('reasoning', [], tools=TOOLS, on_done=done)
    assert raised.value.extra['reason'] == 'invalid_tool_arguments'
    done.assert_not_called()
    stream.close.assert_called_once()


def test_late_ids_fragmented_names_and_parallel_calls_keep_their_own_arguments(gateway):
    streaming(gateway, [
        chunk(calls=[tool_delta(1, name='look', arguments='{"b":')]),
        chunk(calls=[tool_delta(0, call_id='call_', name='lookup', arguments='{"a":1}')]),
        chunk(calls=[tool_delta(1, call_id='call_2', name='up', arguments='2}')]),
        chunk(calls=[tool_delta(0, call_id='1')]),
        chunk(calls=[tool_delta(1, call_id='call_2')], finish='tool_calls'),
    ])
    payload = model.stream_chat('reasoning', [], tools=TOOLS)
    assert [call['id'] for call in payload['tool_calls']] == ['call_1', 'call_2']
    assert [call['function']['arguments'] for call in payload['tool_calls']] == ['{"a":1}', '{"b":2}']
    assert gateway[1].call_args.kwargs['status'] == 'completed'


@pytest.mark.parametrize('tool_list', [None, []])
def test_unsolicited_tool_call_is_rejected(gateway, tool_list):
    streaming(gateway, [chunk(finish='tool_calls', calls=[
        tool_delta(call_id='call_1', name='lookup', arguments='{}')])])
    with pytest.raises(ModelResponseIncompleteError) as raised:
        model.stream_chat('reasoning', [], tools=tool_list)
    assert raised.value.extra['reason'] == 'invalid_tool_call'


def test_no_model_read_deadline_preserves_connection_limits(gateway):
    streaming(gateway, [chunk('完整结果', finish='stop')])
    assert model.stream_chat('reasoning', [])['content'] == '完整结果'
    options = gateway[0].with_options.call_args.kwargs
    assert options['timeout'].read is None
    assert options['timeout'].connect == 15
    assert options['timeout'].pool == 15
    assert options['max_retries'] == 0


def test_unlimited_chat_propagates_to_nonstream_and_connection_recovery(gateway):
    client, _ = gateway
    client.chat.completions.create.return_value = NS(choices=[
        NS(message=NS(content='完成', tool_calls=[]), finish_reason='stop')], usage=None)
    token = current.set(TaskControl(deadline=None))
    try:
        assert model.chat_for_run('reasoning', [])['content'] == '完成'
    finally:
        current.reset(token)
    assert client.with_options.call_args.kwargs['timeout'].read is None
    assert client.with_options.call_args.kwargs['max_retries'] == 0


def test_explicit_request_limit_is_not_removed(gateway):
    streaming(gateway, [chunk('done', finish='stop')])
    model.stream_chat('reasoning', [], request_timeout=7)
    assert gateway[0].with_options.call_args.kwargs['timeout'] == 7


def test_user_cancellation_closes_client_while_waiting_for_next_chunk(gateway):
    client, persisted = gateway
    cancel = Event()
    client_closed = Event()
    client.close.side_effect = client_closed.set
    def blocked_stream():
        yield chunk('unfinished')
        cancel.set()
        assert client_closed.wait(1), 'cancel must release a blocked model connection'
        raise httpx.ReadError('closed by user')
    stream = streaming(gateway, [])
    stream.__iter__.return_value = blocked_stream()
    with pytest.raises(TimeoutError, match='cancelled'):
        model.stream_chat('reasoning', [], cancel_event=cancel)
    assert persisted.call_args.kwargs['status'] == 'failed'
    client.close.assert_called_once()
    stream.close.assert_called_once()


def test_failed_stream_cleanup_does_not_replace_transport_error(gateway):
    stream = streaming(gateway, [])
    stream.__iter__.side_effect = httpx.ReadError('private connection failure')
    stream.close.side_effect = RuntimeError('cleanup failure')
    with pytest.raises(ModelUnavailableError):
        model.stream_chat('reasoning', [])
    assert gateway[1].call_args.kwargs['status'] == 'failed'


def test_default_nonstream_read_wait_is_unlimited_without_chat_context(gateway):
    client, _ = gateway
    client.chat.completions.create.return_value = NS(choices=[
        NS(message=NS(content='完成', tool_calls=[]), finish_reason='stop')], usage=None)
    assert current.get() is None
    assert model.chat_for_run('reasoning', [])['content'] == '完成'
    assert client.with_options.call_args.kwargs['timeout'].read is None
