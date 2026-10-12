"""离线校验质检报告结论的取证规则（哪些回答算有据、哪些属于越权宣称）。

验证行为：无变更不能宣称全部合格；执行成功不能掩盖失败结论；报告数与工作流数不混用；
刷新结论需要新证据而历史解释不需要；报告范围在历史对话中保持且不重放旧结论；
质检总览按阶段去重与过滤。
隔离方式：纯函数断言（report_grounding/business_results），用 Mock 构造消息，不连库、不调用模型。
边界：缺少证据时不得给出肯定结论；统计口径不得用工作流数量顶替报告数量。
"""
from app.modules.runtime.report_grounding import report_answer_error, report_history_note
from app.modules.workflows.business_results import report_evidence_summary, tool_result
from unittest.mock import Mock


def report(outcome='no_changes', items=0, issues=0, workflow='one'):
    return {'workflowId':workflow,'workflowName':workflow,'status':'completed','outcome':outcome,
            'reportUrl':'/report','summary':{'items':items,'issues':issues,'pending':0},'data':[]}


def test_no_changes_cannot_support_all_passed():
    facts=report_evidence_summary([report(),report(workflow='two')])
    assert facts['checkedItems']==0 and facts['notEvaluatedReports']==2
    assert facts['allReturnedChecksPassed'] is False
    assert report_answer_error('全部质检工作流状态正常，未发现不合格品。',{'evidenceSummary':facts})
    assert not report_answer_error('本期无新增记录，不能认定全部合格。',{'evidenceSummary':facts})


def test_failure_cannot_be_hidden_by_execution_success():
    facts=report_evidence_summary([report('issues',12,1)])
    assert facts['failedItems']==1 and not facts['allReturnedChecksPassed']
    assert report_answer_error('全部正常。',{'evidenceSummary':facts})
    assert report_answer_error('流程执行完成，12项检查完成。',{'evidenceSummary':facts})
    assert not report_answer_error('检查12项，11项通过、1项不合格。',{'evidenceSummary':facts})


def test_report_count_is_not_workflow_count():
    facts=report_evidence_summary([report(),report()])
    assert facts['reportCount']==2 and facts['workflowCount']==1


def test_refresh_claim_requires_new_evidence_but_past_explanation_does_not():
    assert report_answer_error('已为您重新查询成品质检数据，结果与上次一致。')
    assert not report_answer_error('上次报告中的这个数值是测量结果。')


def test_report_scope_survives_history_without_replaying_old_conclusions():
    note=report_history_note([{'type':'tool_call','name':'query_workflow_results',
                              'arguments':{'kind':'quality','quality_stage':'finished'}}],'2026-09-17')
    assert 'finished' in note['content'] and '重新调用工具' in note['content']


def test_quality_overview_deduplicates_and_filters_stage(monkeypatch):
    from app.modules.workflows import business_results
    listing=Mock(return_value={'kind':'quality','items':[report('issues',12,1)]})
    monkeypatch.setattr(business_results,'list_reports',listing)
    out=tool_result(None,{'kind':'quality','quality_stage':'finished'})
    assert listing.call_args.kwargs['latest_per_workflow'] is True
    assert listing.call_args.kwargs['quality_stage']=='finished'
    assert out['evidenceSummary']['failedItems']==1
    assert out['items'][0]['executionStatus']=='completed' and out['items'][0]['businessStatus']=='issues'
