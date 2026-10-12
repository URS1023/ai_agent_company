"""Saved-result compatibility checks; no database or provider is contacted.

中文说明：已保存结果的兼容投影（历史运行记录的字段兼容），不连数据库、不访问提供方。
隔离方式：SimpleNamespace + 纯函数断言，离线运行。
边界：兼容读取不得改写原始记录。
"""
from datetime import datetime
from types import SimpleNamespace

from app.modules.workflows.business_results import projected_report, tool_result


def execution(nodes, outputs=None):
    return SimpleNamespace(
        id='execution', workflow_id='workflow', run_id='run', revision=8,
        status='completed', created_at=datetime(2026, 9, 17),
        snapshot={'name': '兰炭进厂质检', 'domains': ['quality']},
        result_json={'nodes': nodes, 'outputs': outputs or {}},
    )


def node(kind, rows):
    return {'kind': kind, 'status': 'completed', 'output': {'data': rows}}


def test_completed_empty_historical_run_is_still_a_readable_report(monkeypatch):
    from app.modules.workflows import business_results

    report = projected_report(execution({'end': node('end', []), 'results': node('merge', [])}))
    assert report['executionId'] == 'execution'
    assert report['outcome'] == 'no_data' and report['summary']['items'] == 0
    assert '不能据此认定全部合格' in report['content']
    monkeypatch.setattr(business_results, 'list_reports', lambda *args, **kwargs: {'kind': 'quality', 'items': [report]})
    receipt = tool_result(None, {'reference': 'execution'})
    assert '这次运行已完成' in receipt['message']
    assert '没有可判定的记录' in receipt['message']
    assert '没有可查看' not in receipt['message']


def test_aggregate_decisions_win_over_later_sorted_partial_branch():
    rows = [{'质检单号': 'QC-1', '检验项目': '项目一', '结果': '合格'},
            {'质检单号': 'QC-1', '检验项目': '项目二', '结果': '不合格'}]
    for names in [('results', 'warning'), ('warning', 'results')]:
        nodes = {'results': node('merge', rows), 'warning': node('transform', rows[1:])}
        report = projected_report(execution({name: nodes[name] for name in names}))
        assert report['summary']['items'] == 2 and report['summary']['issues'] == 1


def test_terminal_output_is_preserved_without_rejudging_measurements():
    rows = [{'质检单号': 'QC-1', '检验项目': '旧判定', '测量值': 0, '阈值': 100, '结果': '不合格'}]
    report = projected_report(execution({'forms': node('database-query', [])}, {'end': {'data': rows}}))
    assert report['summary']['issues'] == 1
    assert report['data'][0]['结果'] == '不合格'


def test_empty_source_query_alone_does_not_masquerade_as_a_saved_business_report():
    assert projected_report(execution({'forms': node('database-query', [])})) is None
