"""Report queries must be selected and interpreted by the model, never shortcuts.

中文说明：报表查询必须由模型选择与解释，不允许用硬编码捷径替代（避免"看起来对"的固定答案）。
隔离方式：SimpleNamespace + MagicMock，离线运行，不连库、不调用模型。
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from app.modules.runtime import agent
from app.providers.tool.base import ToolResult, ToolSpec
from app.modules.runtime.report_grounding import HISTORY_MARKER


@pytest.mark.parametrize('question,arguments', [
    ('查看110kV站主变压器的历史巡检记录', {'kind':'alert','keyword':'110kV站主变压器','category':'inspection','history':True}),
    ('帮我查看质检的数据', {'kind':'quality','limit':5}),
])
@pytest.mark.parametrize('bad_turn', [None, 'before_query', 'after_query'])
def test_model_selects_scoped_tool_then_reads_evidence_to_answer(monkeypatch,question,arguments,bad_turn,
        tool_name='query_workflow_results', answer_text='以下是指定范围内的历史检查记录。', report_history=False):
    gateway=MagicMock()
    gateway.list_available.return_value=[ToolSpec(name=tool_name,description='报告',provider='builtin')]
    gateway.execute.return_value=ToolResult(status='success', data={
        'resultType':'workflow_reports','message':'已读取历史报告证据','items':[{'workflowId':'selected'}]})
    monkeypatch.setattr(agent,'ToolGateway',lambda *_:gateway)
    for name in ('_load_plugin_context','_kb_descriptions','_attached_file_summaries','_conversation_messages','_persist_result_artifacts'):
        monkeypatch.setattr(agent,name,lambda *a,**kw:[])
    if bad_turn or report_history:
        monkeypatch.setattr(agent,'_conversation_messages',lambda *a,**kw:[{'role':'system','content':HISTORY_MARKER+'上一轮质检查询'}])
    for name in ('_record_model_call','finish_tool_call','fail_step','complete_step','record_citations','finalize_run','touch_conversation'):
        monkeypatch.setattr(agent,name,lambda *a,**kw:None)
    for name in ('start_step','record_tool_call','create_message'):
        monkeypatch.setattr(agent,name,lambda *a,**kw:SimpleNamespace(id='fake'))
    monkeypatch.setattr(agent,'build_context',lambda **kw:{})
    monkeypatch.setattr(agent,'_prepare_messages_for_model',lambda m:(m,{'compacted':False}))
    monkeypatch.setattr(agent,'_generate_followups',lambda *a:([],''))
    monkeypatch.setattr(agent,'validate_arguments',lambda args,*a:args)
    turns=[]
    def completion(db, run, messages, *args, **kwargs):
        turns.append([dict(m) for m in messages])
        if (bad_turn=='before_query' and len(turns)==1) or (bad_turn=='after_query' and len(turns)==2):
            bad='已为您重新查询，全部质检工作流状态正常。'
            return iter([('delta',bad),('final',{'content':bad,'tool_calls':[]})])
        if len(turns)==(2 if bad_turn=='before_query' else 1):
            return iter([('final',{'tool_calls':[{'id':'query','type':'function','function':{
                'name':tool_name,'arguments':json.dumps(arguments,ensure_ascii=False)}}]})])
        return iter([('delta',answer_text),('final',{'content':answer_text,'tool_calls':[]})])
    monkeypatch.setattr(agent,'_llm_stream',completion)
    db=MagicMock();db.execute.return_value.scalars.return_value.all.return_value=[]
    run=SimpleNamespace(id='r',workspace_id='w',started_by_user_id='u',conversation_id='c',context_snapshot={},plugin_version_id=None)
    events=list(agent.agent_run(db,run,SimpleNamespace(name='test'),None,content=question,events=[]))
    assert len(turns)==(3 if bad_turn else 2)
    assert gateway.execute.call_args.args[2]==arguments
    assert any(m['role']=='tool' and '已读取历史报告证据' in m['content'] for m in turns[-1])
    answer=''.join(e.get('payload',{}).get('delta','') for e in events)
    assert answer==answer_text
    assert events[-1]['payload']['status']=='completed'


def test_report_history_does_not_block_a_new_business_data_query(monkeypatch):
    test_model_selects_scoped_tool_then_reads_evidence_to_answer(
        monkeypatch, '帮我查看本期收款', {'query': '本期收款'}, None,
        tool_name='query_business_data', answer_text='已为您重新查询本期收款，共 10 笔。', report_history=True)
