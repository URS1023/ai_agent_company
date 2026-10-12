"""Handling claims need actual alert evidence, even after a qualified clause.

中文说明：即便回答里带限定条件（"若…则认为已处理"），宣称"已处理"也必须有真实预警证据。
隔离方式：离线纯函数/桩断言，不连库、不调用模型（经 tests/offline/run_python_checks.py 运行）。
边界：无证据不得给出处理结论，措辞限定不能替代证据。
"""
import pytest

from app.modules.runtime.report_grounding import report_answer_error


@pytest.mark.parametrize('evidence', [None, {'resultType': 'workflow_reports', 'evidenceSummary': {'failedItems': 0}}])
@pytest.mark.parametrize('claim', [
    '该液压问题已经解决。',
    '无法确认维修详情，但该液压问题已经解决。',
    '巡检流程执行完成，液压故障持续恶化。',
])
def test_other_report_or_qualified_clause_cannot_certify_handling(claim, evidence):
    assert report_answer_error(claim, evidence)


@pytest.mark.parametrize('text', [
    '当前已完成空白表格制作。',
    '如何确认该问题已经解决？',
    '这个问题是否已解决仍需查明。',
    '流程执行完成不表示问题已解决。',
])
def test_other_deliverables_and_honest_questions_do_not_claim_resolution(text):
    assert report_answer_error(text) == ''