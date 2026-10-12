"""Retry the substantive request, preserving newer business changes and refusals.

中文说明：重试要针对"实质请求"本身，并保留期间发生的业务变更与模型拒绝（不得用旧快照覆盖）。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连业务库与外部模型。
边界：重试不得回退已验证的数据范围，也不得把拒绝当成失败重跑。
"""
import pytest

from app.modules.runtime import agent, service as runtime


@pytest.mark.parametrize('intervening, expected', [
    ('谢谢', '查询设备部门的金额明细'),
    ('好的，谢谢！', '查询设备部门的金额明细'),
    ('明白了', '查询设备部门的金额明细'),
    ('谢谢，请改查财务部门的金额明细', '谢谢，请改查财务部门的金额明细'),
    ('不要再查设备部门', '不要再查设备部门'),
])
def test_retry_uses_substantive_user_turn(client, administrator, db, monkeypatch, intervening, expected):
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    for text in ('查询设备部门的金额明细', intervening):
        runtime.create_message(db, workspace_id=administrator['X-Workspace-Id'],
            conversation_id=cid, role='user', content=text)
    db.commit()
    observed = []
    def respond(db, run, messages, tools, temperature=0.2, **kwargs):
        observed.append(dict(run.context_snapshot))
        yield 'final', {'content': '已收到本轮问题。', 'tool_calls': [], 'usage': {}, 'model': 'isolated'}
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent, '_generate_followups', lambda *args, **kwargs: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '重试'})
    assert response.status_code == 200, response.text
    assert observed and observed[0]['user_request'] == expected
    assert observed[0]['user_message_sequence'] == 3
    assert observed[0]['user_message_id']
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    user_messages = [m for m in detail['messages'] if m['role'] == 'user']
    assert user_messages[-1]['content'] == '重试'
