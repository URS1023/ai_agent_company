"""An empty database receipt cannot authorize fabricated analysis or exports.

中文说明：空查询回执不能授权凭空编写的分析或导出；空证据不得阻塞真实数据/空白与指定模板的正常流程。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连业务库、不调用模型。
边界：无数据就必须承认无数据，不能补出数值。
"""
import json

import pytest

from app.modules.runtime import agent
from app.providers.tool.base import ToolResult
from test_chat_query_evidence import conversation_with_query


@pytest.mark.parametrize('fabricator,arguments', [
    ('python_execute', {'code': "result = [{'区域': '石灰窑', '数量': 22}]"}),
    ('render_spreadsheet', {'spec': {'columns': ['区域', '数量'], 'data': [['石灰窑', 22]]}}),
])
@pytest.mark.parametrize('intervening_read', [False, True])
def test_empty_query_cannot_authorize_handwritten_rows(
    client, administrator, db, monkeypatch, fabricator, arguments, intervening_read,
):
    cid = conversation_with_query(client, administrator, db)
    fabricated = '已查询到石灰窑采购计划5条，采购数量合计22。'
    calls = [('query_business_data', {'query': '石灰窑采购计划', 'mode': 'natural_language'})]
    if intervening_read:
        calls.append(('current_datetime', {}))
    calls.append((fabricator, arguments))
    turns, executed = [], []

    def respond(*args, **kwargs):
        turns.append(True)
        if len(turns) <= len(calls):
            name, params = calls[len(turns) - 1]
            yield 'final', {'tool_calls': [{'id': str(len(turns)), 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(params, ensure_ascii=False)}}]}
        else:
            yield 'delta', fabricated
            yield 'final', {'content': fabricated, 'tool_calls': [], 'finish_reason': 'stop'}

    def execute(self, workspace, name, params, context):
        executed.append(name)
        if name == 'query_business_data':
            return ToolResult(status='success', data={'queryId': 'empty-current-query', 'data': []})
        return ToolResult(status='success', data={'message': 'computed'})

    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    sent = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
        json={'content': '查询最近90天石灰窑区域的采购计划，整理分析并导出'})
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert detail['last_run']['status'] == 'completed'
    assert '当前筛选范围内未返回记录' in detail['messages'][-1]['content']
    assert fabricated not in sent.text and fabricated not in detail['messages'][-1]['content']
    assert fabricator not in executed, 'An empty receipt must not authorize invented records'
    assert executed.count('query_business_data') == 1

@pytest.mark.parametrize('scenario', ['new_nonempty_query', 'blank_spreadsheet', 'new_turn_template', 'report_source', 'file_source', 'knowledge_source'])
def test_empty_evidence_does_not_block_real_data_or_blank_and_requested_templates(
    client, administrator, db, monkeypatch, scenario,
):
    cid = conversation_with_query(client, administrator, db)
    calls = []
    if scenario != 'new_turn_template':
        calls.append(('query_business_data', {'query': '旧范围', 'mode': 'natural_language'}))
    if scenario == 'new_nonempty_query':
        calls.append(('query_business_data', {'query': '新范围', 'mode': 'natural_language'}))
    if scenario == 'report_source':
        calls.append(('query_workflow_results', {'kind': 'alert'}))
    elif scenario == 'file_source':
        calls.append(('read_file', {'file_id': '01M2N95K0Z2VHTS3X39D8X8ERE'}))
    elif scenario == 'knowledge_source':
        calls.append(('search_knowledge', {'query': '仪表清单'}))
    calls.append(('render_spreadsheet', {'spec': {'columns': ['区域', '数量'],
        'data': [] if scenario == 'blank_spreadsheet' else [['示例区域', 1]]}}))
    turns, executed = [], []

    def respond(*args, **kwargs):
        turns.append(True)
        if len(turns) <= len(calls):
            name, params = calls[len(turns) - 1]
            yield 'final', {'tool_calls': [{'id': str(len(turns)), 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(params, ensure_ascii=False)}}]}
        else:
            yield 'final', {'content': '表格已保存，示例内容不代表实际采购记录。', 'tool_calls': [], 'finish_reason': 'stop'}

    def execute(self, workspace, name, params, context):
        executed.append(name)
        if name == 'query_business_data':
            return ToolResult(status='success', data={'queryId': params['query'],
                'data': [{'区域': '示例区域', '数量': 1}] if params['query'] == '新范围' else []})
        if name == 'query_workflow_results':
            return ToolResult(status='success', data={'message': '已读取本轮保存报告。', 'items': [{'id': 'report'}]})
        return ToolResult(status='success', data={'message': '表格已保存'})

    monkeypatch.setattr(agent, '_llm_stream', respond)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    sent = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
        json={'content': '生成空白模板或标明示例的采购表'})
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert detail['last_run']['status'] == 'completed'
    assert executed == [name for name, _ in calls]
    assert 'query_result_empty' not in sent.text