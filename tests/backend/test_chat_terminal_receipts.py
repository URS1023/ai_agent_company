"""A failed deliverable cannot become a successful conversation turn.

中文说明：失败的交付不能变成"成功的对话轮次"：失败的业务工具要留下持久失败回复、
真实澄清仍可继续被回答、执行者崩溃后只保留一条持久化回复（不重复也不丢）。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连业务库与外部模型。
"""
import json

import pytest
from sqlalchemy import select

from app.models import Message, Run, ToolCall
from app.modules.runtime import chat_liveness
from app.modules.openwebui import runtime as native_runtime
from chat_native_fakes import block_live_native, scripted_native
from app.providers.tool.base import ToolResult


@pytest.mark.parametrize('tool,arguments,failure', [
    ('create_business_dashboard', {'requirement': '库存大屏'},
     ToolResult(status='failed', error_code='model_error', error_summary='模型没有返回有效组件列表')),
    ('query_business_data', {'query': '库存', 'mode': 'natural_language'},
     ToolResult(status='failed', error_code='model_error', error_summary='模型没有生成可执行的 SQL')),
])
def test_failed_business_tool_has_durable_failed_reply(client, administrator, db, scripted_native, tool, arguments, failure):
    protocol = scripted_native([{'name': tool, 'args': arguments, 'result': failure}],
                               final_reply=failure.error_summary)
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '帮我做成数字大屏' if 'dashboard' in tool else '查询库存'})
    assert 'event: done' in response.text and 'event: run.failed' in response.text
    assert 'event: run.completed' not in response.text
    assert failure.error_summary in response.text
    db.expire_all()
    run = db.scalar(select(Run).where(Run.conversation_id == cid))
    assert run.status == 'failed' and run.error_code == 'delivery_incomplete'
    messages = db.scalars(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant')).all()
    assert len(messages) == 1 and messages[0].status == 'failed'
    assert failure.error_summary in json.dumps(messages[0].parts_json, ensure_ascii=False)
    call = db.scalar(select(ToolCall).where(ToolCall.run_id == run.id))
    assert call.status == 'failed' and call.error_code == 'model_error'
    assert call.error_summary == failure.error_summary
    assert protocol.calls == [tool], '切换原生补交消息不能重做已知失败的业务调用'
    assert run.context_snapshot['tool_failure_check']['unresolved'][0]['name'] == tool
    assert '想继续' not in messages[0].content


def test_real_business_clarification_remains_answerable(client, administrator, db, scripted_native):
    from io import BytesIO
    from docx import Document
    from app.modules.artifact.service import create_artifact, artifact_reference

    def saved_document(context):
        document = Document()
        document.add_paragraph('已整理的查询范围说明，保留现有工作。')
        buffer = BytesIO()
        document.save(buffer)
        row = create_artifact(context['db'], workspace_id=context['workspace_id'], run_id=context['run_id'],
            owner_user_id=context['user_id'], type='document', name='查询范围说明.docx', data=buffer.getvalue(),
            metadata={'tool': 'render_document'})
        return ToolResult(status='success', artifacts=[artifact_reference(row)])

    question = '您想查看收入还是回款？'
    protocol = scripted_native([
        {'name': 'render_document', 'args': {'format': 'docx'}, 'result': saved_document},
        {'name': 'query_business_data', 'args': {'query': '查看金额'}, 'result': ToolResult(
            status='failed', error_code='validation_error', metadata={'requires_user_input': True},
            data={'clarification': question, 'options': ['收入', '回款']})},
    ], final_reply=question)
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '查看金额并给我Word'})
    assert 'event: run.failed' in response.text and 'event: done' in response.text
    assert 'event: run.completed' not in response.text
    assert question in response.text
    db.expire_all()
    run = db.scalar(select(Run).where(Run.conversation_id == cid))
    assert run.status == 'failed'
    message = db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant'))
    saved_parts = json.dumps(message.parts_json, ensure_ascii=False)
    assert question in saved_parts and '收入' in saved_parts and '回款' in saved_parts
    assert '查询范围说明.docx' in saved_parts, '等待澄清不能丢弃已经完成的文档'
    assert protocol.calls.count('query_business_data') == 1

    # 用户补齐业务口径后必须能再次发送，不能被上一轮等待输入永久锁住。
    protocol.steps = [{'name': 'query_business_data', 'args': {'query': '查看收入'},
                       'result': ToolResult(status='success', data={'收入': 100})}]
    protocol.final_reply = '本次合成数据中的收入为100。'
    followup = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                           json={'content': '查看收入'})
    assert 'event: run.completed' in followup.text and 'event: run.failed' not in followup.text
    db.expire_all()
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 2


def test_executor_crash_leaves_one_persisted_reply(client, administrator, db, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError('isolated worker crash before first reply')
        yield
    monkeypatch.setattr(native_runtime, 'run_chat', crash)
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    response = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator, json={'content': '你好'})
    assert 'event: run.failed' in response.text and 'event: done' in response.text
    db.expire_all()
    run = db.scalar(select(Run).where(Run.conversation_id == cid))
    assert run.status == 'failed'
    assert not chat_liveness.mark_interrupted(db, run)
    messages = db.scalars(select(Message).where(Message.conversation_id == cid, Message.role == 'assistant')).all()
    assert len(messages) == 1 and messages[0].status == 'failed'
    assert '中断' in messages[0].content
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert any(m['role'] == 'assistant' and '中断' in m['content'] for m in detail['messages'])
