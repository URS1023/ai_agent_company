"""An unavailable paid model gives one actionable reply and does not lock the chat.

中文说明：付费模型不可用时只给一条可操作的提示，并且不锁死对话（后续仍可继续）。
隔离方式：monkeypatch + MagicMock + httpx 桩，client 夹具提供 HTTP 入口，不发起真实请求。
"""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIStatusError

from app.models import Run
from app.modules.runtime import agent
from app.providers import model
from app.providers.tool.base import ToolResult


def test_balance_failure_is_identifiable_persisted_and_next_turn_can_complete(
    client, administrator, db, monkeypatch,
):
    fake = MagicMock()
    response = httpx.Response(402, request=httpx.Request('POST', 'https://api.deepseek.com/chat/completions'))
    fake.chat.completions.create.side_effect = APIStatusError('synthetic billing failure', response=response,
        body={'error': {'code': 'invalid_request_error', 'message': 'Insufficient Balance'}})
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com', 'api_key': 'synthetic'})
    monkeypatch.setattr(model, '_client', lambda *a: fake)
    fake.with_options.return_value = fake
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    first = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator, json={'content': '你好'})
    assert 'event: run.failed' in first.text
    assert 'model_balance_exhausted' in first.text and '余额不足' in first.text
    assert '右侧' in first.text and '切换' in first.text
    assert fake.chat.completions.create.call_count == 1
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert '余额不足' in detail['messages'][-1]['content']
    assert detail['last_run']['error_code'] == 'model_balance_exhausted'

    stream = MagicMock()
    stream.__iter__.return_value = iter([NS(choices=[NS(delta=NS(content='现在可以继续处理。', tool_calls=None), finish_reason='stop')],
        usage=NS(prompt_tokens=12, completion_tokens=8, total_tokens=20))])
    fake.chat.completions.create.side_effect = None
    fake.chat.completions.create.return_value = stream
    second = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator, json={'content': '你好'})
    assert 'event: run.completed' in second.text and '现在可以继续处理' in second.text
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    db.expire_all()
    assert db.get(Run, detail['last_run']['id']).model_usage['total_tokens'] == 20
    assert fake.chat.completions.create.call_count == 2


@pytest.mark.parametrize('rows', [None, [], [{'count': 0}]])
def test_midstream_disconnect_preserves_only_current_query_values(client, administrator, monkeypatch, rows):
    fake = MagicMock()
    fake.with_options.return_value = fake
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'model': 'synthetic', 'base_url': 'https://synthetic.invalid', 'api_key': 'synthetic'})
    monkeypatch.setattr(model, '_client', lambda *a: fake)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: pytest.fail('Do not call unavailable model again'))
    broken = MagicMock()
    def interrupted():
        yield NS(choices=[NS(delta=NS(content='正在整理查询结果', tool_calls=None), finish_reason=None)])
        raise httpx.ReadTimeout('private upstream transport failure')
    broken.__iter__.return_value = interrupted()
    replies = []
    if rows is not None:
        query = MagicMock()
        query.__iter__.return_value = iter([NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(
            index=0, id='query', type='function', function=NS(name='query_business_data',
            arguments='{"query":"库存数量","mode":"natural_language"}'))]), finish_reason='tool_calls')])])
        replies.append(query)
    replies.append(broken)
    fake.chat.completions.create.side_effect = replies
    executed = []
    def execute(*args):
        executed.append(True)
        return ToolResult(status='success', data={'queryId': 'current-query', 'data': rows})
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
        json={'content': '查询库存数量'})
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert 'private upstream transport failure' not in response.text
    assert len(executed) == int(rows is not None)
    assert fake.chat.completions.create.call_count == 1 + int(rows is not None)
    broken.close.assert_called_once()
    if rows is None:
        assert detail['last_run']['status'] == 'failed'
        assert detail['last_run']['error_code'] == 'model_unavailable'
    else:
        assert detail['last_run']['status'] == 'completed'
        assert '模型补充说明暂时不可用' in detail['messages'][-1]['content']
        assert ('实际查询值：0' if rows else '当前筛选范围内未返回记录') in detail['messages'][-1]['content']
