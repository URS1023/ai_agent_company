"""Regressions from conversation 01M2YBEC: a claim is not a query receipt.

中文说明：来自真实对话（01M2YBEC）的回归——"声称查过"不等于有查询回执。
验证行为：未执行的结论绝不流式输出或落库；模型可改为真的查询，且只发出有证据的回答；
无需查询的澄清/说明照常给出；部分查询要用当前值回答且不重复执行 SQL；重复回执强制给出回答；
模型额度不足时只交付当前真实查询且不重试；空结果不能变成 null/0。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连业务库与外部模型。
"""
import json
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import Message, Run, ToolCall
from app.modules.runtime import agent
from app.providers.tool.base import ToolResult


def conversation_with_query(client, administrator, db):
    conversation = client.post('/api/chat/conversations', headers=administrator,
                               json={'title': '查询证据回归'}).json()
    db.add(Message(id='evidence-history', workspace_id=administrator['X-Workspace-Id'],
                   conversation_id=conversation['id'], sequence=1, role='assistant',
                   content='上一轮返回了库存记录。', status='completed', created_at=datetime.utcnow(),
                   parts_json=[{'type': 'tool_call', 'id': 'old', 'name': 'query_business_data',
                                'arguments': {'query': '库存', 'mode': 'natural_language'}},
                               {'type': 'tool_result', 'id': 'old', 'tool': 'query_business_data',
                                'result': {'status': 'success', 'data': {'queryId': 'old-query', 'data': []}}}]))
    db.commit()
    return conversation['id']


@pytest.mark.parametrize('fabrication', [
    '已为您查询到备品备件仓的出库记录，共 10 条记录：张三、李四。',
    '根据数据库查询结果，无法确定退货去向。数据库中没有退货供应商字段。',
    '查询到出库记录的完整字段信息了！退货供应商：山西某钢材有限公司；物流单号：SF1234567890。',
])
def test_unexecuted_claim_never_streamed_or_saved(client, administrator, db, monkeypatch, fabrication):
    cid = conversation_with_query(client, administrator, db)
    turns = []
    def respond(*args, **kwargs):
        turns.append(args[2])
        yield 'delta', fabrication
        yield 'final', {'content': fabrication, 'tool_calls': [], 'usage': {}}
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '你还是没查'})
    assert fabrication not in response.text
    assert 'business_evidence_missing' in response.text and 'run.failed' in response.text
    assert len(turns) == 3
    run = db.scalar(select(Run).where(Run.conversation_id == cid))
    assert run.status == 'failed' and run.error_code == 'business_evidence_missing'
    assert not db.scalars(select(ToolCall).where(ToolCall.run_id == run.id)).all()
    saved = db.scalars(select(Message).where(Message.conversation_id == cid)).all()
    assert all(fabrication not in m.content for m in saved)


def test_model_can_repair_by_querying_and_only_evidenced_answer_is_emitted(client, administrator, db, monkeypatch):
    cid = conversation_with_query(client, administrator, db)
    bad = '已查询到 10 条出库记录。'
    good = '本次查询返回 5 条执行记录；尚未确认它们均属于指定仓库。'
    turns = []
    def respond(*args, **kwargs):
        turns.append(args[2])
        n = len(turns)
        if n == 2:
            yield 'final', {'content': '现在已查到全部结果。', 'tool_calls': [{
                'id': 'fresh-query', 'type': 'function', 'function': {'name': 'query_business_data',
                'arguments': json.dumps({'query': '备品备件仓的出库记录', 'mode': 'natural_language'})}}]}
        else:
            text = bad if n == 1 else good
            yield 'delta', text
            yield 'final', {'content': text, 'tool_calls': []}
    executed = []
    def execute(self, workspace, name, args, ctx):
        executed.append((name, args))
        return ToolResult(status='success', data={'success': True, 'queryId': 'new-query',
            'data': [{'出库数量': 1}] * 5, 'totalCount': 5})
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '查询一下出库记录'})
    assert bad not in response.text and '现在已查到全部结果' not in response.text
    assert good in response.text and 'run.completed' in response.text
    assert len(turns) == 3 and len(executed) == 1
    history = agent._conversation_messages(db, cid)
    assert any('old-query' in m['content'] for m in history if m['role'] == 'system')


@pytest.mark.parametrize('answer', [
    '本轮还没有查询数据库，不能确认出库记录。',
    '请问要查看哪个仓库的出库记录？',
    '库存快照与出库流水是不同口径，不能直接互相推导。',
])
def test_honest_limitations_clarification_and_explanations_need_no_query(client, administrator, db, monkeypatch, answer):
    cid = conversation_with_query(client, administrator, db)
    def respond(*args, **kwargs):
        yield 'final', {'content': answer, 'tool_calls': []}
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '解释一下口径'})
    assert answer in response.text and 'run.completed' in response.text


@pytest.mark.parametrize('reason, text', [('length', '查询条件：'), ('stop', '查询条件：\n\n**'), (None, '查询条件：')])
def test_partial_query_prose_delivers_current_values_without_reexecuting_sql(client, administrator, db, monkeypatch, reason, text):
    cid = conversation_with_query(client, administrator, db)
    turns = []
    def respond(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1:
            yield 'final', {'tool_calls': [{'id': 'query', 'type': 'function', 'function': {
                'name': 'query_business_data', 'arguments': '{"query":"库存数量","mode":"natural_language"}'}}]}
        else:
            yield 'delta', text
            yield 'final', {'content': text, 'tool_calls': [], 'finish_reason': reason}
    calls = []
    def execute(*args):
        calls.append(True)
        return ToolResult(status='success', data={'success': True, 'queryId': 'current', 'data': [{'count': 0}]})
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator, json={'content': '查询库存数量'})
    assert '实际查询值：0' in response.text and 'run.completed' in response.text
    assert text not in response.text and len(calls) == 1 and len(turns) == 2
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert '实际查询值：0' in detail['messages'][-1]['content']


@pytest.mark.parametrize('raw_xml', [False, True])
def test_repeated_receipt_forces_answer_and_rejects_unoffered_tools(client, administrator, db, monkeypatch, raw_xml):
    cid = conversation_with_query(client, administrator, db)
    turns, executions = [], []
    def respond(*args, **kwargs):
        turns.append(args[3])
        if len(turns) == 3 and raw_xml:
            yield 'final', {'content': '<tool_call>\n<function=query_business_data>guess</function>\n</tool_call>', 'tool_calls': []}
            return
        yield 'final', {'tool_calls': [{'id': str(len(turns)), 'type': 'function', 'function': {
            'name': 'query_business_data', 'arguments': '{"query":"库存数量","mode":"natural_language"}'}}]}
    def execute(*args):
        executions.append(True)
        return ToolResult(status='success', data={'queryId': 'current', 'data': [{'count': 0}]},
            metadata={'same_turn_result_reused': len(executions) > 1})
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator, json={'content': '查询库存数量'})
    assert '实际查询值：0' in response.text and 'run.completed' in response.text
    assert len(executions) == 2 and len(turns) == 3 and turns[-1] is None
    assert '<tool_call>' not in response.text


@pytest.mark.parametrize('queried', [False, True])
def test_model_limit_delivers_only_a_current_real_query_and_never_retries(client, administrator, db, monkeypatch, queried):
    from app.errors import ModelRateLimitError
    cid = conversation_with_query(client, administrator, db)
    turns, executions = [], []
    def respond(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1 and queried:
            yield 'final', {'tool_calls': [{'id': 'query', 'type': 'function', 'function': {
                'name': 'query_business_data', 'arguments': '{"query":"库存数量","mode":"natural_language"}'}}]}
        else:
            raise ModelRateLimitError('当前模型服务限流', retryable=False)
    def execute(*args):
        executions.append(True)
        return ToolResult(status='success', data={'queryId': 'current', 'data': [{'count': 0}]})
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: pytest.fail('Unavailable model must not be called for suggestions'))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator, json={'content': '查询库存数量'})
    assert ('run.completed' in response.text) is queried
    assert ('实际查询值：0' in response.text) is queried
    assert len(executions) == int(queried) and len(turns) == 1 + int(queried)
    if queried:
        assert '模型补充说明暂时不可用' in response.text
    else:
        assert 'model_rate_limited' in response.text and '限流' in response.text


@pytest.mark.parametrize('rows, prose, expected', [
    ([], '核验值=NULL。返回0行记录，模板编码字段值为空。', '当前筛选范围内未返回记录'),
    ([], '没有匹配记录，所以实际查询值：0。', '当前筛选范围内未返回记录'),
    ([{'value': None}], '实际查询值：NULL（数据库空值）', '实际查询值：NULL（数据库空值）'),
    ([{'value': 0}], '实际查询值：0', '实际查询值：0'),
])
def test_empty_result_does_not_become_a_null_or_zero_value(client, administrator, db, monkeypatch, rows, prose, expected):
    cid = conversation_with_query(client, administrator, db)
    turns, executions = [], []
    def respond(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1:
            yield 'final', {'tool_calls': [{'id': 'query', 'type': 'function', 'function': {
                'name': 'query_business_data', 'arguments': '{"query":"查询模板编码","mode":"natural_language"}'}}]}
        else:
            yield 'delta', prose
            yield 'final', {'content': prose, 'tool_calls': [], 'finish_reason': 'stop'}
    def execute(*args):
        executions.append(True)
        return ToolResult(status='success', data={'queryId': 'current', 'data': rows})
    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
        json={'content': '查询模板编码'})
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert 'run.completed' in response.text and expected in response.text
    assert expected in detail['messages'][-1]['content']
    assert len(turns) == 2 and len(executions) == 1
    if not rows:
        assert prose not in response.text and prose not in detail['messages'][-1]['content']
        assert '核验值=NULL' not in detail['messages'][-1]['content']
        assert '实际查询值：0' not in detail['messages'][-1]['content']
