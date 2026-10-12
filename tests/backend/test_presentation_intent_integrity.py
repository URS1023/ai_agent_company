"""保留的 PPT 模板不得把咨询或拒绝误判为制作请求，验收使用当前 Worker 契约。"""
import pytest
from sqlalchemy import select

from app.business_worker import chat
from app.models import Artifact, ToolCall
from test_business_worker import worker_context, prepare, request


@pytest.mark.parametrize('question', ['怎么生成PPT？', '别做PPT', '先不要生成PPT，只解释步骤',
    '说明一下制作PPT的流程', 'How do I create a PowerPoint?', "Don't generate slides"])
def test_template_selected_questions_remain_normal_conversation(worker_context, db, question):
    prepared = prepare(worker_context, question, template_id='executive-ivory')
    assert prepared['resources']['template_id'] == 'executive-ivory'
    assert prepared['task']['required_deliveries'] == []
    answer = '可以在模板中心选择版式，再提供要讲的内容。'
    reply = chat.settle(prepared['run_id'], 'completed', answer)
    assert reply['status'] == 'completed' and 'error' not in reply, reply
    assert reply['content'] == answer
    assert reply['task']['required_deliveries'] == []
    assert not reply['task'].get('missing_deliveries') and reply['deliveries'] == []
    headers, cid = worker_context
    history = chat.history(request(headers, f'/api/chat/conversations/{cid}'), cid)
    assert history['messages'][-1]['content'] == answer
    assert history['messages'][-1]['status'] == 'completed'
    assert not db.scalars(select(ToolCall).where(ToolCall.run_id == prepared['run_id'])).all()
    assert not db.scalars(select(Artifact).where(Artifact.run_id == prepared['run_id'])).all()


@pytest.mark.parametrize('question', ['按模板生成PPT', '把这份材料转成幻灯片', 'make a ppt', 'generate slides'])
def test_explicit_creation_keeps_staged_delivery(worker_context, db, question):
    prepared = prepare(worker_context, question, template_id='executive-ivory')
    assert prepared['task']['required_deliveries'] == [{'type': 'presentation', 'count': 1}]
    reply = chat.settle(prepared['run_id'], 'completed', '演示文稿已制作完成。')
    assert reply['status'] == 'partial' and reply['error']['code'] == 'delivery_incomplete'
    assert reply['task']['missing_deliveries'][0]['type'] == 'presentation'
    assert not db.scalars(select(ToolCall).where(ToolCall.run_id == prepared['run_id'])).all()
    assert not db.scalars(select(Artifact).where(Artifact.run_id == prepared['run_id'])).all()
