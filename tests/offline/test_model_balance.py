"""Billing failures are not JSON repair candidates at any chat entry point.

中文说明：额度/计费类失败不属于"可修复的 JSON 问题"，在任意对话入口都不得被重试修复。
隔离方式：SimpleNamespace + MagicMock，离线运行，不发请求。
边界：计费错误要直接、明确地告知用户。
"""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
from openai import APIStatusError
import pytest

from app.errors import ModelBalanceError, ModelRateLimitError
from app.modules.analytics.queries import natural_language_sql
from app.modules.analytics.schemas import QueryInput
from app.modules.reports.generation import _call_model
from app.modules.runtime.agent import _customer_tool_error
from app.providers import model
from app.providers.tool.base import ToolResult


@pytest.mark.parametrize('entry', ['chat', 'stream', 'sql', 'dashboard'])
@pytest.mark.parametrize('status,error_type,label', [(402, ModelBalanceError, '余额不足'), (429, ModelRateLimitError, '限流')])
def test_billing_error_is_not_retried_as_malformed_output(monkeypatch, entry, status, error_type, label):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://api.deepseek.com/chat/completions'))
    failure = APIStatusError('private-upstream-diagnostic', response=response, body={'error': 'billing'})
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.side_effect = failure
    monkeypatch.setattr(model, '_client', lambda *a: client)
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'base_url': 'https://api.deepseek.com', 'api_key': 'synthetic', 'model': 'deepseek-flash'})
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    monkeypatch.setattr(model, '_persist_usage', lambda **kw: None)
    ctx = NS(workspace_id='w')
    with pytest.raises(error_type) as raised:
        if entry == 'sql':
            natural_language_sql(ctx, NS(id='s', provider='mysql', connection_config={}),
                QueryInput(datasourceId='s', query='查询金额明细'), [])
        elif entry == 'dashboard':
            _call_model(ctx, 'Return JSON', {'kind': 'screen', 'componentTypes': ['JBar']})
        else:
            getattr(model, 'stream_chat' if entry == 'stream' else 'chat_for_run')('reasoning', [])
    assert client.chat.completions.create.call_count == 1
    assert raised.value.extra['retryable'] is False
    assert 'private-upstream-diagnostic' not in raised.value.message
    reply = _customer_tool_error(ToolResult(status='failed', error_code=raised.value.code,
                                           error_summary=raised.value.message))
    assert label in reply and '切换' in reply
