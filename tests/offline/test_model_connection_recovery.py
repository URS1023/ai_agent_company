"""离线校验模型连接异常的重试预算与边界（不重放已执行的工具）。

验证行为：一轮对话共享同一次连接恢复且绝不重放工具调用；HTTP 状态错误不消耗连接恢复额度；
接近任务总时限时不再重试；流式线程继承同一套取消与恢复预算。
隔离方式：用 httpx 与 openai 的真实异常类型构造失败，模型/会话以 MagicMock 替换，
不发起网络请求、不连库。
边界：连接类错误才可恢复；工具已执行后不得再重试。
"""
import time
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
from openai import APIConnectionError, APIStatusError
import pytest

from app.providers.model import _create_chat_completion
from app.modules.runtime.task_control import TaskControl, current
from app.modules.runtime.agent import _llm_stream
from app.providers import model


def test_connection_recovery_is_shared_by_the_turn_and_never_replays_tools():
    failure=APIConnectionError(request=httpx.Request('POST','https://synthetic.invalid'))
    client=MagicMock()
    client.with_options.return_value=client
    client.chat.completions.create.side_effect=[failure, 'response', failure]
    control=TaskControl(deadline=time.monotonic()+120)
    token=current.set(control)
    try:
        assert _create_chat_completion(client, messages=[{'role':'user','content':'query'}])=='response'
        with pytest.raises(APIConnectionError):
            _create_chat_completion(client, messages=[])
        assert client.chat.completions.create.call_count==3
        assert control.model_connection_retries==1
    finally:
        current.reset(token)


@pytest.mark.parametrize('status', [401,402,403,429,500])
def test_http_status_failures_do_not_consume_connection_recovery(status):
    client=MagicMock()
    client.chat.completions.create.side_effect=APIStatusError('synthetic',
        response=httpx.Response(status,request=httpx.Request('POST','https://synthetic.invalid')),body={})
    control=TaskControl(deadline=time.monotonic()+120)
    token=current.set(control)
    try:
        with pytest.raises(APIStatusError):_create_chat_completion(client)
        assert client.chat.completions.create.call_count==1 and control.model_connection_retries==0
    finally:current.reset(token)


def test_no_recovery_when_task_deadline_is_near():
    control=TaskControl(deadline=time.monotonic()+10)
    assert not control.claim_model_connection_retry()


def test_stream_thread_inherits_the_same_cancellation_and_recovery_budget(monkeypatch):
    control=TaskControl(deadline=time.monotonic()+120)
    def stream(*args,**kw):
        assert current.get() is control
        assert control.claim_model_connection_retry()
        kw['on_done']({'content':'done','tool_calls':[]})
    monkeypatch.setattr(model,'stream_chat',stream)
    token=current.set(control)
    try:
        assert list(_llm_stream(None,NS(model_profile='reasoning',workspace_id='w'),[],None))[-1][0]=='final'
        assert not control.claim_model_connection_retry()
    finally:current.reset(token)
