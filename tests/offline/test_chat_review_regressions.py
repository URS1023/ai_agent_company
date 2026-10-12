"""离线回归：评审中发现的聊天意图/口径/追问问题（不调用模型与数据库）。

验证行为：保留的 PPT 模板不会把普通提问改判成生成任务；总览类问答保持对话语气；
样本金额不被替代成臆造值；追问会剔除管理员视角请求并限制模型调用次数；
声称「已生成大屏」必须真的产生创建动作。
隔离方式：SimpleNamespace + MagicMock 替换模型与会话，断言纯函数与 agent 决策，不连库。
边界：意图不被上下文残留污染；无真实产物时不得宣称已创建。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from app.modules.runtime import agent
from app.modules.analytics.business_overview import wants_overview, sample_amount_totals

@pytest.mark.parametrize('question,expected', [
    ('帮我做成数字大屏', False), ('查看财务数据', False),
    ('不要PPT，做成大屏', False), ('PPT是什么', False),
    ('帮我做成ppt', True), ('ppt', True), ('生成演示文稿', True),
])
def test_retained_ppt_template_does_not_change_intent(question, expected):
    assert agent._presentation_request(question) is expected

@pytest.mark.parametrize('question', ['帮我查看一下财务数据', '请看一下付款与应付数据', '帮我查一下财务数据'])
def test_conversational_overview(question):
    assert wants_overview(question)

def test_sample_amounts_are_not_substituted():
    result = sample_amount_totals([{'apply': '100.10', 'unpaid': '0'}, {'apply': '0.20', 'unpaid': None}], [
        {'columnName':'apply', 'comment':'申请金额'}, {'columnName':'unpaid', 'comment':'未付金额'},
    ])
    assert result['amounts']['apply']['sum'] == '100.30'
    assert result['amounts']['unpaid']['sum'] == '0'
    assert result['amounts']['unpaid']['valueCount'] == 1

def test_followups_drop_admin_requests_and_use_bounded_model_call(monkeypatch):
    reply = MagicMock(return_value={'content':'["请联系管理员检查权限", "查看付款计划", "查看财务成本报表"]'})
    monkeypatch.setattr(agent, 'chat_for_run', reply)
    monkeypatch.setattr(agent, '_record_model_call', lambda *a, **kw: None)
    questions, error = agent._generate_followups(None, SimpleNamespace(id='r', workspace_id='w', model_profile='reasoning'), '查看财务', '当前可查付款计划、成本', [], {})
    assert questions[:2] == ['查看付款计划', '查看财务成本报表'] and len(questions) == 3 and not error
    assert reply.call_args.kwargs['max_retries'] == 0
    assert reply.call_args.kwargs['request_timeout'] == 20

@pytest.mark.parametrize('calls_tool', [False, True])
def test_dashboard_claims_require_actual_creation(monkeypatch, calls_tool):
    from app.providers.tool.base import ToolSpec, ToolResult
    gateway = MagicMock()
    gateway.list_available.return_value = [ToolSpec(name='create_business_dashboard', description='create', provider='builtin')]
    gateway.execute.return_value = ToolResult(status='success', data={'reportId':'saved'})
    monkeypatch.setattr(agent, 'ToolGateway', lambda *_: gateway)
    for name in ('_load_plugin_context', '_kb_descriptions', '_attached_file_summaries', '_conversation_messages', '_persist_result_artifacts'):
        monkeypatch.setattr(agent, name, lambda *a, **kw: [])
    for name in ('_record_model_call', 'finish_tool_call', 'fail_step', 'complete_step', 'record_citations', 'finalize_run', 'touch_conversation'):
        monkeypatch.setattr(agent, name, lambda *a, **kw: None)
    for name in ('start_step', 'record_tool_call', 'create_message'):
        monkeypatch.setattr(agent, name, lambda *a, **kw: SimpleNamespace(id='fake'))
    monkeypatch.setattr(agent, 'build_context', lambda **kw: {})
    monkeypatch.setattr(agent, '_prepare_messages_for_model', lambda m: (m, {'compacted':False}))
    monkeypatch.setattr(agent, '_generate_followups', lambda *a: ([], ''))
    reply = {'content':'大屏创建成功！实时刷新已验证。', 'tool_calls': []}
    if calls_tool:
        reply['tool_calls'] = [{'id':'a', 'type':'function', 'function': {'name':'create_business_dashboard','arguments':'{}'}}]
    model = MagicMock(side_effect=lambda *a, **kw: iter([('delta',reply['content']), ('final',reply)]))
    monkeypatch.setattr(agent, '_llm_stream', model)
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []
    run = SimpleNamespace(id='r', workspace_id='w', started_by_user_id='u', conversation_id='c', context_snapshot={}, plugin_version_id=None)
    events = list(agent.agent_run(db, run, SimpleNamespace(name='test'), None, content='帮我做成数字大屏', events=[]))
    rendered = ''.join(e.get('payload',{}).get('delta','') for e in events)
    assert '实时刷新已验证' not in rendered
    assert gateway.execute.call_count == int(calls_tool)
    assert 1 <= model.call_count <= 2
    if calls_tool:
        assert '草稿已创建' in rendered
    else:
        assert '草稿已创建' not in rendered
        assert any(event['type'] == 'run.failed' for event in events)
