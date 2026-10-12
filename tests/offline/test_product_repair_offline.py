"""Regressions for the reported dashboard, report, scan and resume failures.

中文说明：已上报的大屏/报表/扫描/恢复类问题的离线回归。
隔离方式：SimpleNamespace + MagicMock，离线运行，无数据库与网络。
边界：只覆盖前端/服务端的解析与控制流分支，不替代真实数据的端到端验收。
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
import time
import pytest

from app.modules.business_ai.dashboard_actions import apply_theme
from app.modules.runtime.agent import _dashboard_action
from app.modules.workflows.business_report import build_report, _analysis_lines
from app.modules.workflows.scanning import scan_output
from app.modules.workflows import nodes
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.schemas import FlowGraph, FlowNode


def test_theme_preserves_metrics_bindings_and_geometry():
    original = {"canvas": {"theme": {"text": "#ffffff"}, "width": 1920}, "widgets": [
        {"id": "metric", "x": 20, "y": 50, "width": 400, "height": 300,
         "binding": {"query": "SELECT amount FROM payments", "parameters": {"color": "#ffffff"}},
         "config": {"data": [{"name": "#ffffff"}], "textStyle": {"color": "#ffffff"}}}]}
    before = deepcopy(original)
    changed = apply_theme(original)
    assert original == before
    assert changed['widgets'][0]['binding'] == original['widgets'][0]['binding']
    assert changed['widgets'][0]['config']['data'] == original['widgets'][0]['config']['data']
    assert changed['widgets'][0]['x'] == 20 and changed['canvas']['width'] == 1920
    assert changed['canvas']['themeId'] == 'dark-tech'


@pytest.mark.parametrize('question,action', [
    ('把大屏改成深色科技风主题', 'update_business_dashboard'),
    ('用深色科技风模板重新生成这个付款应付情况大屏', 'create_business_dashboard'),
    ('检查大屏模板库里有哪些深色科技风格可选', 'list_dashboard_templates'),
    ('把大屏改成浅色主题', ''),
    ('PPT 模板有哪些可以选择', ''),
    ('检查幻灯片模板库里有哪些深色科技风格可选', ''),
    ('怎么生成大屏？', ''),
    ('不要生成大屏', ''),
    ('可以生成大屏吗', ''),
    ('无需修改大屏主题，解释深色主题的作用', ''),
    ('帮我解释如何创建 dashboard', ''),
    ('先别制作大屏，查看当前数据', ''),
    ('大屏生成失败的原因', ''),
    ('大屏生成要多久', ''),
    ('数字看板的制作流程', ''),
    ('我想生成大屏，但先看看有哪些数据', ''),
    ('先了解大屏生成步骤', ''),
    ('帮我生成故障原因分析大屏', 'create_business_dashboard'),
    ('大屏生成失败了，请重新生成', 'create_business_dashboard'),
])
def test_dashboard_routes(question, action):
    assert _dashboard_action(question) == action


def test_quality_report_counts_documents_and_preserves_failed_evidence():
    rows = [{'质检单号': f'Q{doc}', '检验项目': f'项目{item}', '标准原文': '≤1.0%',
             '测量值': '2.000000' if (doc, item) == (5, 1) else '.400000',
             '结果': '不合格' if (doc, item) == (5, 1) else '合格', '说明': '来自单据标准'}
            for doc in range(1, 6) for item in range(1, 6)]
    report = build_report(rows, 'quality', '质量检查')
    assert report['summary'] == {'records': 5, 'items': 25, 'issues': 1, 'pending': 0,
                                'affectedRecords': 1, 'pendingRecords': 0, 'normalItems': 24}
    assert '| 单据 | 项目 | 标准 | 实测值 | 结果 | 说明 |\n| ---' in report['content']
    assert '实测 2' in report['content'] and 'Q5' in report['content']
    assert build_report([], 'quality', '无记录')['outcome'] == 'no_data'


def test_device_missing_record_never_invents_conflicting_plans_or_period_policy():
    report = build_report([{'deviceName':'1#主变', 'planName':'巡检', '结果':'待确认',
        '说明':'计划不唯一、缺少完成记录或周期未明确', 'planMatches':1,
        'recordMatches':0, '周期小时':4}], 'alert', '巡检报告')
    assert '计划不唯一' not in report['content'] and '多个' not in report['content']
    assert '没有找到可关联的完成记录' in report['content']
    assert report['data'][0]['计划周期'] == '4 小时'
    assert not _analysis_lines([{'index':0,'action':'schedule'}], report['data'], 'alert')
    assert _analysis_lines([{'index':0,'action':'evidence'}], report['data'], 'alert')


def test_scan_covers_sixth_record_and_wraps_for_modified_old_rows():
    conf = SimpleNamespace(scan_key='序号')
    state = {'afterKey':0}
    all_keys = list(range(1, 28))
    seen = []
    for _ in range(3):
        page = [k for k in all_keys if k > state['afterKey']][:20]
        out = scan_output(conf, [{'序号':k} for k in page], state)
        seen.extend(page); state = {'afterKey':out['nextKey']}
    assert seen == all_keys and state['afterKey'] == 0
    assert scan_output(conf, [{'序号':1}], state)['nextKey'] == 1
    with pytest.raises(Exception):
        scan_output(conf, [{'序号':2},{'序号':1}], state)


def test_loop_wait_restores_iteration_without_repeating_prior_tool(monkeypatch):
    graph = FlowGraph.model_validate({'nodes': [
        {'id':'s','kind':'start','title':'开始'}, {'id':'op','kind':'tool','title':'动作','config':{'toolName':'offline'}},
        {'id':'q','kind':'question','title':'确认','config':{'prompt':'请确认','answerKey':'answer'}},
        {'id':'e','kind':'end','title':'结束'}],
        'edges':[{'id':a+b,'source':a,'target':b} for a,b in [('s','op'),('op','q'),('q','e')]]})
    loop = FlowNode(id='loop',kind='loop',title='循环',config={'iterations':2, 'subgraph':graph.model_dump(by_alias=True)})
    real = nodes.execute_node
    calls = []
    def execute(ctx, node, inputs, budget, **kw):
        if node.kind == 'tool':
            calls.append(node.id)
            return deepcopy(inputs['input'])
        return real(ctx,node,inputs,budget,**kw)
    monkeypatch.setattr(nodes,'execute_node',execute)
    wait = None
    for iteration in range(3):
        try:
            result = real(None,loop,{'input':{}},RuleBudget(time.monotonic()+3),execution_id='e',
                wait_context=wait,resume_value='确认' if wait else nodes.NO_RESUME)
        except nodes.WorkflowPause as pause:
            wait = pause.payload
            assert len(calls) == iteration+1
        else:
            assert result['iterations'] == 2 and len(calls) == 2
    assert len(set(calls)) == 2


def test_ppt_resume_preserves_written_slides(monkeypatch):
    from app.modules.presentation import draft
    stored = {}
    storage = SimpleNamespace(exists=lambda k:k in stored, read_text=lambda k:stored[k].decode(),
        put_bytes=lambda k,b:stored.__setitem__(k,b))
    monkeypatch.setattr(draft,'get_storage',lambda:storage)
    source = {'schema':'presentation_draft.v1','template_id':'t','title':'付款报告',
        'slides':[{'title':'已完成','written':True,'bullets':['真实证据']},{'title':'待完成','written':False}]}
    draft._save('w','old',source)
    draft.resume_draft('w','old','new',template_id='t')
    copied = draft.load_draft('w','new')
    assert copied['slides'] == source['slides'] and copied['resumed_from_run_id'] == 'old'
    assert draft.load_draft('w','old') == source
    with pytest.raises(Exception): draft.resume_draft('w','old','other',template_id='different')


def test_total_task_deadline_and_cancel():
    from app.modules.runtime.task_control import TaskControl, current, checkpoint, remaining_timeout, ChatCancelled
    control = TaskControl(time.monotonic()+10)
    token = current.set(control)
    try:
        assert 0 < remaining_timeout(120) <= 10
        control.cancelled.set()
        with pytest.raises(ChatCancelled): checkpoint()
        control.cancelled.clear(); control.deadline = time.monotonic()-1
        with pytest.raises(TimeoutError): checkpoint()
    finally: current.reset(token)


def test_reranking_preserves_document_identity_and_falls_back_on_invalid_provider(monkeypatch):
    from app.providers import model
    from app.modules.knowledge import reranking
    candidates = [{'src':{'knowledge_base_id':'kb','content':text},'score':i} for i,text in enumerate(['甲','乙','丙'])]
    monkeypatch.setattr(model,'_profile_config',lambda *a,**kw:{'source':'database','capabilities':{'capabilities':['rerank']},'api_key':'test-only','base_url':'https://invalid.test/v1','model':'test'})
    monkeypatch.setattr(model,'_governed_call',lambda *a:None)
    statuses=[]
    monkeypatch.setattr(model,'_persist_usage',lambda **kw:statuses.append(kw['status']))
    response=MagicMock()
    response.json.return_value={'results':[{'index':2,'relevance_score':.9},{'index':0,'relevance_score':.8}]}
    http=MagicMock()
    http.__enter__.return_value.post.return_value=response
    monkeypatch.setattr(reranking.httpx,'Client',lambda **kw:http)
    ranked=reranking.rank_candidates('w','rerank','问题',candidates)
    assert [r['src']['content'] for r in ranked]==['丙','甲','乙']
    assert candidates[0]['src']['content']=='甲' and statuses==['completed']
    response.json.return_value={'results':[{'index':9,'relevance_score':1}]}
    db=MagicMock()
    db.scalars.return_value.all.return_value=[SimpleNamespace(rerank_model='rerank')]
    unchanged,status=reranking.rerank(db,'w','问题',candidates)
    assert status=='fallback' and unchanged==candidates and statuses[-1]=='failed'


def test_dark_template_search_excludes_light_template_with_blue_name(monkeypatch):
    from app.modules.business_ai import dashboard_actions as actions
    monkeypatch.setattr(actions.templates,'list_templates',lambda ctx:[
        {'id':'light','name':'蓝色科技驾驶舱','content':{'canvas':{'backgroundColor':'#FFFFFF'}}},
        {'id':'dark','name':'深蓝科技驾驶舱','content':{'canvas':{'backgroundColor':'#061735'}}}])
    assert [t['id'] for t in actions.template_candidates(None,'深色 科技')]==['dark']


def test_semantic_review_uses_bound_metadata_without_sample_rows():
    from app.modules.reports.generation import _review_sources
    schemas=[{'id':'s','dialect':'mysql','tables':[
        {'tableName':'payment','tableComment':'付款','columns':[{'columnName':'amount','dataType':'decimal','comment':'申请支付金额'}], 'sampleRows':[{'amount':123}]},
        {'tableName':'unrelated','columns':[{'columnName':'name'}]}]}]
    compact=_review_sources(schemas,[{'query':{'datasourceId':'s','sql':'SELECT SUM(amount) FROM payment'}}])
    assert [t['tableName'] for t in compact[0]['tables']]==['payment']
    assert compact[0]['tables'][0]['columns'][0]['comment']=='申请支付金额'
    assert 'sampleRows' not in json.dumps(compact)


def test_guard_row_limit_never_renames_whole_payment_composition_to_top_1001():
    from app.modules.reports.generation import _harmonize_widget_semantics
    widget={'component':'JRing','metricName':'已付与未付金额构成','generatedSql':"SELECT '已付' status, SUM(paid) amount FROM payments UNION ALL SELECT '未付', SUM(unpaid) FROM payments LIMIT 1001"}
    _harmonize_widget_semantics([widget],[],style_locked=True)
    assert widget['metricName']=='已付与未付金额构成'


def test_table_aliases_receive_only_unambiguous_source_labels():
    from app.modules.reports.generation_bindings import query_column_labels
    schemas=[{'id':'s','dialect':'mysql','tables':[{'tableName':'payments','columns':[
        {'columnName':'payee','comment':'收款单位'},{'columnName':'amount','comment':'本次申请支付金额'}]}]}]
    labels=query_column_labels('SELECT ROW_NUMBER() OVER (ORDER BY amount) seq, payee AS vendor, SUM(amount) AS total FROM payments GROUP BY payee',schemas,'s')
    assert labels=={'seq':'序号','vendor':'收款单位','total':'本次申请支付金额'}
    assert query_column_labels("SELECT amount AS value FROM payments UNION ALL SELECT payee FROM payments",schemas,'s')=={}
