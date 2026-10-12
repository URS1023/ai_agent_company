"""Usage can accompany the final content/tool delta, including DeepSeek streams.

中文说明：用量（usage）可以随最后一段内容/工具增量一起到达，DeepSeek 流式响应也必须正确解析。
隔离方式：SimpleNamespace + MagicMock 构造流式分片，离线运行，不发请求。
"""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
import pytest

from app.errors import ModelUnavailableError, ModelResponseIncompleteError
from app.providers import model


@pytest.mark.parametrize('has_choices', [True, False])
def test_stream_usage_is_saved_with_or_without_choices(monkeypatch, has_choices):
    config = {'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com', 'api_key': 'synthetic'}
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: config)
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    persisted = MagicMock()
    monkeypatch.setattr(model, '_persist_usage', persisted)
    chunk = NS(usage=NS(prompt_tokens=17, completion_tokens=9, total_tokens=26),
        choices=[NS(delta=NS(content='OK', tool_calls=None), finish_reason='stop')] if has_choices else [])
    stream = MagicMock()
    stream.__iter__.return_value = iter([chunk] if has_choices else [
        NS(choices=[NS(delta=NS(content='OK', tool_calls=None), finish_reason='stop')]), chunk])
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(model, '_client', lambda *a: client)
    final = model.stream_chat('reasoning', [])
    assert final['usage']['total_tokens'] == 26
    assert persisted.call_args.kwargs['usage']['total_tokens'] == 26
    assert final['content'] == 'OK'
    stream.close.assert_called_once()


@pytest.mark.parametrize('reason', ['stop', 'length', 'tool_calls', None])
def test_stream_rejects_incomplete_terminal_reason(monkeypatch, reason):
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'model': 'synthetic', 'base_url': 'https://synthetic.invalid', 'api_key': 'synthetic'})
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    monkeypatch.setattr(model, '_persist_usage', lambda **kw: None)
    stream = MagicMock()
    stream.__iter__.return_value = iter([
        NS(choices=[NS(delta=NS(content='partial', tool_calls=None), finish_reason=None)]),
        NS(choices=[NS(delta=NS(content=None, tool_calls=None), finish_reason=reason)]),
        NS(choices=[], usage=NS(prompt_tokens=1, completion_tokens=2, total_tokens=3)),
    ])
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(model, '_client', lambda *a: client)
    if reason == 'stop':
        result = model.stream_chat('reasoning', [])
        assert result['finish_reason'] == reason and result['content'] == 'partial'
    else:
        with pytest.raises(ModelResponseIncompleteError):
            model.stream_chat('reasoning', [])
    stream.close.assert_called_once()


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError])
def test_midstream_transport_failure_is_typed_without_replaying_or_finishing(monkeypatch, error_type):
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'model': 'synthetic', 'base_url': 'https://synthetic.invalid', 'api_key': 'synthetic'})
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    persisted = MagicMock()
    monkeypatch.setattr(model, '_persist_usage', persisted)
    failure = error_type('private transport diagnostic')
    def chunks():
        yield NS(choices=[NS(delta=NS(content='partial', tool_calls=None), finish_reason=None)])
        raise failure
    stream = MagicMock()
    stream.__iter__.return_value = chunks()
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(model, '_client', lambda *a: client)
    done = MagicMock()
    with pytest.raises(ModelUnavailableError) as raised:
        model.stream_chat('reasoning', [], on_done=done)
    assert raised.value.__cause__ is failure
    assert 'private transport diagnostic' not in raised.value.message
    assert raised.value.extra['retryable'] is False
    assert client.chat.completions.create.call_count == 1
    assert persisted.call_args.kwargs['status'] == 'failed'
    done.assert_not_called()
    stream.close.assert_called_once()


@pytest.mark.parametrize('failure', [TimeoutError('cancelled'), ValueError('invalid callback')])
def test_stream_cancellation_and_programming_failures_are_not_model_availability(monkeypatch, failure):
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'model': 'synthetic', 'base_url': 'https://synthetic.invalid', 'api_key': 'synthetic'})
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    monkeypatch.setattr(model, '_persist_usage', lambda **kw: None)
    stream = MagicMock()
    stream.__iter__.side_effect = failure
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(model, '_client', lambda *a: client)
    with pytest.raises(type(failure)) as raised:
        model.stream_chat('reasoning', [])
    assert raised.value is failure
    stream.close.assert_called_once()
