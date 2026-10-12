"""JSON generation regressions with no model, network or database access.

中文说明：大屏生成中模型 JSON 解析/修复的回归（含非法 JSON 的一次修复路径）。
隔离方式：SimpleNamespace + MagicMock，无模型、无网络、无数据库。
边界：修复只针对格式，不得放宽业务校验。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.errors import ModelError, ModelResponseIncompleteError
from app.modules.reports import generation
from app.providers import model


@pytest.mark.parametrize('invalid', ['{}', '{"widgets":[]}', '{"widgets":[null]}', '{"widgets":[{}],"canvas":[]}'])
def test_parseable_but_invalid_layout_is_repaired_before_queries(monkeypatch, invalid):
    call = MagicMock(side_effect=[{'content': invalid}, {'content': '{"widgets":[{"component":"JBar"}]}'}])
    monkeypatch.setattr(generation.model, 'chat_for_run', call)
    candidate, usage = generation._call_model(SimpleNamespace(workspace_id='workspace'), 'Return JSON',
        {'kind': 'screen', 'componentTypes': ['JBar'], 'templateSlots': []})
    assert candidate['widgets'] == [{'component': 'JBar'}]
    assert call.call_count == 2 and usage['attempts'] == 2
    assert 'widgets' in call.call_args.args[1][-1]['content']


def test_missing_layout_stops_at_existing_three_attempt_budget(monkeypatch):
    call = MagicMock(return_value={'content': '{}'})
    monkeypatch.setattr(generation.model, 'chat_for_run', call)
    with pytest.raises(ModelError):
        generation._call_model(SimpleNamespace(workspace_id='workspace'), 'Return JSON',
            {'kind': 'screen', 'componentTypes': ['JBar'], 'templateSlots': []})
    assert call.call_count == 3


def invoke(monkeypatch, responses):
    call = MagicMock(side_effect=responses)
    monkeypatch.setattr(generation.model, "chat_for_run", call)
    return call, lambda: generation._call_model(
        SimpleNamespace(workspace_id="workspace"), "Return JSON", {"templateId": "locked"})


def test_invalid_json_is_repaired_with_json_mode(monkeypatch):
    call, run = invoke(monkeypatch, [
        {"content": '{"bindings": [', "finish_reason": "stop"},
        {"content": '{"bindings": []}', "finish_reason": "stop"},
    ])
    candidate, usage = run()
    assert candidate == {"bindings": []}
    assert usage["attempts"] == 2
    assert all(c.kwargs["response_format"] == {"type": "json_object"} for c in call.call_args_list)
    assert "json_line" in call.call_args_list[1].args[1][-1]["content"]


def test_truncated_output_is_never_accepted_even_when_parseable(monkeypatch):
    call, run = invoke(monkeypatch, [{"content": '{"bindings": []}', "finish_reason": "length"}] * 3)
    with pytest.raises(ModelError) as raised:
        run()
    assert raised.value.extra["finish_reason"] == "length"
    assert raised.value.extra["retryable"] is False
    assert call.call_count == 3


def test_invalid_output_stops_at_budget_with_diagnostics(monkeypatch):
    call, run = invoke(monkeypatch, [{"content": '{"bindings": invalid}', "finish_reason": "stop"}] * 3)
    with pytest.raises(ModelError) as raised:
        run()
    assert call.call_count == 3
    assert raised.value.extra["json_line"] == 1
    assert raised.value.extra["json_column"] > 0


def test_explicit_unsupported_json_mode_has_bounded_fallback(monkeypatch):
    error = RuntimeError("response_format json_object is not supported")
    error.status_code = 400
    call, run = invoke(monkeypatch, [error, {"content": '{"bindings": []}'}])
    assert run()[0] == {"bindings": []}
    assert call.call_args_list[1].kwargs["response_format"] is None


def test_network_failure_is_not_retried(monkeypatch):
    call, run = invoke(monkeypatch, [TimeoutError("timeout")])
    with pytest.raises(ModelError):
        run()
    assert call.call_count == 1
    assert call.call_args.kwargs["max_retries"] == 0
    assert call.call_args.kwargs["request_timeout"] is None


def test_json_repairs_keep_attempt_budget_without_wall_clock_cutoff(monkeypatch):
    call, run = invoke(monkeypatch, [{"content": "invalid"}, {"content": '{"bindings": []}'}])
    monkeypatch.setattr(generation.time, "monotonic", lambda: 999999999)
    assert run()[0] == {"bindings": []}
    assert call.call_count == 2
    assert all(c.kwargs['request_timeout'] is None for c in call.call_args_list)


def test_provider_incomplete_output_uses_existing_json_repair_attempt(monkeypatch):
    call, run = invoke(monkeypatch, [
        ModelResponseIncompleteError('未完整结束', reason='truncated_response', retryable=True),
        {"content": '{"bindings": []}'},
    ])
    assert run()[0] == {"bindings": []}
    assert call.call_count == 2
    assert '完整合法JSON' in call.call_args_list[-1].args[1][-1]['content']


@pytest.mark.parametrize("json_mode", [None, {"type": "json_object"}])
def test_provider_forwards_format_and_rejects_truncated_response(monkeypatch, json_mode):
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "base_url": "https://unused.invalid", "api_key": "fake", "model": "fake"})
    monkeypatch.setattr(model, "_governed_call", lambda *args: None)
    monkeypatch.setattr(model, "_persist_usage", lambda **kwargs: None)
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(usage=None, choices=[
        SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="{}", tool_calls=None))])
    monkeypatch.setattr(model, "_client", lambda *args: client)
    with pytest.raises(ModelResponseIncompleteError) as raised:
        model.chat_for_run("reasoning", [], response_format=json_mode)
    assert raised.value.extra["reason"] == "truncated_response"
    kwargs = client.chat.completions.create.call_args.kwargs
    if json_mode is None:
        assert "response_format" not in kwargs
    else:
        assert kwargs["response_format"] == json_mode


def test_provider_uses_request_scoped_timeout_and_retry_options(monkeypatch):
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "base_url": "https://unused.invalid", "api_key": "fake", "model": "fake"})
    monkeypatch.setattr(model, "_governed_call", lambda *args: None)
    monkeypatch.setattr(model, "_persist_usage", lambda **kwargs: None)
    client = MagicMock()
    scoped = client.with_options.return_value
    scoped.chat.completions.create.return_value = SimpleNamespace(usage=None, choices=[
        SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}", tool_calls=None))])
    monkeypatch.setattr(model, "_client", lambda *args: client)
    model.chat_for_run("reasoning", [], request_timeout=42, max_retries=0)
    client.with_options.assert_called_once_with(timeout=42, max_retries=0)
    client.chat.completions.create.assert_not_called()
