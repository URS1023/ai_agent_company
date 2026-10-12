"""Conversation recovery delivers actual results without replaying executed tools.

All providers are stubbed; the standard backend fixtures use disposable SQLite.
"""
import json

import pytest
from sqlalchemy import select

from app.errors import ModelResponseIncompleteError
from app.models import Message, Run
from app.modules.runtime import agent
from app.providers.tool.base import ToolResult


def call(name, arguments, identity='attempt'):
    return {'id': identity, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


def send(client, administrator, monkeypatch, text='查询库存数量'):
    monkeypatch.setattr(agent, '_generate_followups', lambda *a, **kw: ([], ''))
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': text})
    return cid, response


@pytest.mark.parametrize('broken', ['empty', 'truncated', 'missing_terminal'])
def test_query_result_survives_model_reply_failure(client, administrator, db, monkeypatch, broken):
    turns, executions = [], []
    def reply(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1:
            yield 'final', {'tool_calls': [call('query_business_data', {'query': '库存数量'})]}
        elif broken == 'empty':
            yield 'final', {'content': '', 'tool_calls': []}
        else:
            raise ModelResponseIncompleteError('未完整结束', reason=broken, retryable=True)
    def execute(*args, **kwargs):
        executions.append(True)
        return ToolResult(status='success', data={'queryId': 'fresh', 'success': True,
                                                  'data': [{'库存数量': 0}]})
    monkeypatch.setattr(agent, '_llm_stream', reply)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    cid, response = send(client, administrator, monkeypatch)
    assert 'run.completed' in response.text and 'run.failed' not in response.text
    assert '实际查询值：0' in response.text
    assert len(executions) == 1 and len(turns) == 2
    message = db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant'))
    assert '实际查询值：0' in message.content


def test_incomplete_model_attempt_retries_without_replaying_tool(client, administrator, db, monkeypatch):
    turns, executions = [], []
    def reply(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1:
            yield 'delta', '不完整的前半句'
            raise ModelResponseIncompleteError('流提前关闭', reason='missing_finish_reason', retryable=True)
        yield 'final', {'content': '已完整回答：可以在右侧选择模型。', 'tool_calls': []}
    monkeypatch.setattr(agent, '_llm_stream', reply)
    monkeypatch.setattr(agent.ToolGateway, 'execute', lambda *a, **kw: executions.append(True))
    cid, response = send(client, administrator, monkeypatch, '怎么切换模型')
    assert 'event: message.reset' in response.text and 'run.completed' in response.text
    assert len(turns) == 2 and executions == []
    saved = db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant'))
    assert saved.content == '已完整回答：可以在右侧选择模型。'


def test_repeated_incomplete_output_is_not_reported_as_completed(client, administrator, db, monkeypatch):
    turns = []
    def reply(*args, **kwargs):
        turns.append(True)
        raise ModelResponseIncompleteError('流提前关闭', reason='missing_finish_reason', retryable=True)
        yield
    monkeypatch.setattr(agent, '_llm_stream', reply)
    cid, response = send(client, administrator, monkeypatch, '你好')
    assert len(turns) == 2 and 'run.completed' not in response.text
    run = db.scalar(select(Run).where(Run.conversation_id == cid))
    assert run.status == 'failed' and run.error_code == 'model_response_incomplete'
    assert db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant')).content


@pytest.mark.parametrize('repair', [True, False])
def test_query_schema_is_repaired_before_execution(client, administrator, db, monkeypatch, repair):
    turns, executions = [], []
    def reply(*args, **kwargs):
        turns.append(True)
        if repair and len(turns) == 3:
            yield 'final', {'content': '库存数量为0。', 'tool_calls': []}
        else:
            arguments = {'query': '库存数量'} if repair and len(turns) == 2 else {'query': '库存数量', 'unexpected': True}
            yield 'final', {'tool_calls': [call('query_business_data', arguments, str(len(turns)))]}
    def execute(*args, **kwargs):
        executions.append(args[3])
        return ToolResult(status='success', data={'queryId': 'fresh', 'data': [{'数量': 0}]})
    monkeypatch.setattr(agent, '_llm_stream', reply)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    cid, response = send(client, administrator, monkeypatch)
    assert len(turns) == 3
    assert len(executions) == int(repair)
    assert ('run.completed' in response.text) is repair
    if repair:
        assert executions == [{'query': '库存数量'}]
    else:
        assert 'run.failed' in response.text


@pytest.mark.parametrize('duplicate', [False, True])
def test_dashboard_plus_document_completes_each_deliverable_once(client, administrator, db, monkeypatch, duplicate):
    turns, executions = [], []
    def reply(*args, **kwargs):
        names = {t['function']['name'] for t in (args[3] or [])}
        turns.append(names)
        n = len(turns)
        if n == 1 or (duplicate and n == 2):
            if n > 1:
                assert 'create_business_dashboard' not in names
            yield 'final', {'tool_calls': [call('create_business_dashboard', {'requirement': '库存大屏'}, str(n))]}
        elif n == 2 + int(duplicate):
            assert 'create_business_dashboard' not in names
            yield 'final', {'tool_calls': [call('render_document', {'document_spec': {'title': '库存报告'}}, str(n))]}
        else:
            yield 'final', {'content': '大屏和Word报告均已保存。', 'tool_calls': []}
    def execute(self, workspace, name, args, ctx):
        executions.append(name)
        if name == 'create_business_dashboard':
            return ToolResult(status='success', data={'reportId': 'saved-draft'})
        from app.modules.artifact.service import create_artifact
        from app.modules.artifact.renderers import render_document
        data = render_document({'title': '库存报告', 'sections': [{'heading': '结果', 'body': '库存数量为0。'}]})
        artifact = create_artifact(db, workspace_id=ctx['workspace_id'], run_id=ctx['run_id'],
                                  owner_user_id=ctx['user_id'], type='docx', name='库存报告.docx', data=data)
        return ToolResult(status='success', artifacts=[{'id': artifact.id}])
    monkeypatch.setattr(agent, '_llm_stream', reply)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    cid, response = send(client, administrator, monkeypatch, '帮我生成库存大屏和Word文档')
    assert executions == ['create_business_dashboard', 'render_document'], response.text
    assert 'run.completed' in response.text and 'run.failed' not in response.text, response.text
    saved = db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant'))
    assert any(p.get('type') == 'artifact' for p in saved.parts_json)


def test_missing_requested_document_cannot_claim_full_success(client, administrator, db, monkeypatch):
    turns = []
    def reply(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1:
            yield 'final', {'tool_calls': [call('create_business_dashboard', {'requirement': '库存大屏'})]}
        else:
            yield 'final', {'content': '大屏和Word报告均已保存。', 'tool_calls': []}
    monkeypatch.setattr(agent, '_llm_stream', reply)
    monkeypatch.setattr(agent.ToolGateway, 'execute', lambda *a, **kw: ToolResult(status='success', data={'reportId':'draft'}))
    cid, response = send(client, administrator, monkeypatch, '帮我生成库存大屏和Word文档')
    assert 'run.completed' not in response.text and len(turns) == 3
    saved = db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant'))
    assert '大屏和Word报告均已保存' not in saved.content
    assert any('数字大屏草稿已创建' in p.get('text','') for p in saved.parts_json)


@pytest.mark.parametrize('text, expected', [
    ('帮我生成Word和设备大屏', {'docx', 'dashboard'}),
    ('生成大屏，不需要Word/PDF', {'dashboard'}),
    ('怎么生成Word和大屏？', set()),
    ('大屏生成失败的原因', set()),
    ('我想生成大屏，但先看看有哪些数据', set()),
    ('请导出PDF和Excel', {'pdf', 'xlsx'}),
])
def test_completion_requirements_respect_actual_request(text, expected):
    assert agent._requested_deliverables(text) == expected


@pytest.mark.parametrize('tool_failed', [False, True])
def test_document_alone_or_failed_artifact_cannot_complete_compound_request(client, administrator, db, monkeypatch, tool_failed):
    turns = []
    def reply(*args, **kwargs):
        turns.append(True)
        if len(turns) == 1:
            yield 'final', {'tool_calls': [call('render_document', {'document_spec': {'title': '报告'}})]}
        else:
            yield 'final', {'content': 'Word和大屏全部完成。', 'tool_calls': []}
    def execute(self, workspace, name, args, ctx):
        from app.modules.artifact.service import create_artifact
        from app.modules.artifact.renderers import render_document
        row = create_artifact(db, workspace_id=ctx['workspace_id'], run_id=ctx['run_id'],
            owner_user_id=ctx['user_id'], type='docx', name='报告.docx', data=render_document({'title':'报告'}))
        return ToolResult(status='failed' if tool_failed else 'success', artifacts=[{'id':row.id}],
                          error_summary='生成中断' if tool_failed else None)
    monkeypatch.setattr(agent, '_llm_stream', reply)
    monkeypatch.setattr(agent.ToolGateway, 'execute', execute)
    text = '请生成Word文档' if tool_failed else '帮我生成Word和设备大屏'
    cid, response = send(client, administrator, monkeypatch, text)
    assert 'run.completed' not in response.text and len(turns) == 3
    # Failed output remains traceable but does not satisfy successful delivery.
    assert 'Word和大屏全部完成' not in response.text
