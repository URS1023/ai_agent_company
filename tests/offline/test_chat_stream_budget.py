"""离线校验流式回答的总时限、工具载荷保留与取消收尾。

验证行为：心跳型（长时间无内容）流也受总时限约束；已完成流仍保留工具结果载荷；
provider 在取消后会关闭流。
隔离方式：直接调用 _llm_stream，模型层用 MagicMock 假流 + threading.Event 控制节奏，
不发起真实请求、不连库（用 run_python_checks.py 运行）。
边界：默认无总时限；显式截止仍可用，取消与完成不能丢已产出的工具结果。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from threading import Event

import pytest

from app.modules.runtime.agent import _llm_stream
from app.providers import model


def test_heartbeat_stream_has_total_deadline(monkeypatch):
    stopped = Event()

    def endless(*args, **kwargs):
        cancel = kwargs['cancel_event']
        assert kwargs['max_retries'] == 0
        while not cancel.wait(0.005):
            kwargs['on_delta']('fragment')
        stopped.set()

    monkeypatch.setattr(model, 'stream_chat', endless)
    run = SimpleNamespace(model_profile='reasoning', workspace_id='test')
    with pytest.raises(TimeoutError):
        list(_llm_stream(None, run, [], None, total_timeout=0.04))
    assert stopped.wait(1)


def test_completed_stream_preserves_tool_payload(monkeypatch):
    payload = {'content': '', 'tool_calls': [{'id': 'tool-1'}]}
    monkeypatch.setattr(model, 'stream_chat', lambda *a, **kw: kw['on_done'](payload))
    run = SimpleNamespace(model_profile='reasoning', workspace_id='test')
    assert list(_llm_stream(None, run, [], None)) == [('final', payload)]


def test_provider_closes_cancelled_stream(monkeypatch):
    stream = MagicMock()
    stream.__iter__.return_value = iter([SimpleNamespace(choices=[])])
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: dict(base_url='test', api_key='test', model='test'))
    monkeypatch.setattr(model, '_client', lambda *a: client)
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    monkeypatch.setattr(model, '_persist_usage', lambda **kw: None)
    cancel = Event()
    cancel.set()
    with pytest.raises(TimeoutError):
        model.stream_chat('test', [], cancel_event=cancel)
    client.chat.completions.create.assert_not_called()


def test_default_stream_survives_large_elapsed_time_and_keeps_cancellation(monkeypatch):
    from app.modules.runtime.task_control import TaskControl, current
    import time
    control = TaskControl(deadline=None)
    def delayed(*args, **kwargs):
        assert kwargs['request_timeout'] is None
        monkeypatch.setattr(time, 'monotonic', lambda: 10**12)
        kwargs['on_done']({'content': '长任务完整结果', 'tool_calls': []})
    monkeypatch.setattr(model, 'stream_chat', delayed)
    token = current.set(control)
    try:
        run = SimpleNamespace(model_profile='reasoning', workspace_id='test')
        assert list(_llm_stream(None, run, [], None))[-1][1]['content'] == '长任务完整结果'
        assert not control.cancelled.is_set()
    finally:
        current.reset(token)


def test_provider_returning_without_callback_fails_instead_of_hanging(monkeypatch):
    from app.errors import ModelResponseIncompleteError
    monkeypatch.setattr(model, 'stream_chat', lambda *args, **kwargs: None)
    run = SimpleNamespace(model_profile='reasoning', workspace_id='test')
    with pytest.raises(ModelResponseIncompleteError) as raised:
        list(_llm_stream(None, run, [], None))
    assert raised.value.extra['reason'] == 'missing_final'


def test_user_can_cancel_an_unlimited_stream(monkeypatch):
    from app.modules.runtime.task_control import TaskControl, ChatCancelled, current
    control = TaskControl(deadline=None)
    stopped = Event()
    def streaming(*args, **kwargs):
        control.cancelled.set()
        kwargs['cancel_event'].wait()
        stopped.set()
    monkeypatch.setattr(model, 'stream_chat', streaming)
    token = current.set(control)
    try:
        with pytest.raises(ChatCancelled):
            list(_llm_stream(None, SimpleNamespace(model_profile='reasoning', workspace_id='test'), [], None))
        assert stopped.wait(1)
    finally:
        current.reset(token)


def test_closing_consumer_cancels_unlimited_provider_worker(monkeypatch):
    from app.modules.runtime.task_control import TaskControl, current
    control = TaskControl(deadline=None)
    stopped = Event()
    def streaming(*args, **kwargs):
        kwargs['on_delta']('partial')
        kwargs['cancel_event'].wait(2)
        stopped.set()
    monkeypatch.setattr(model, 'stream_chat', streaming)
    token = current.set(control)
    try:
        stream = _llm_stream(None, SimpleNamespace(model_profile='reasoning', workspace_id='test'), [], None)
        assert next(stream) == ('delta', 'partial')
        stream.close()
        assert control.cancelled.is_set() and stopped.wait(1)
    finally:
        current.reset(token)
