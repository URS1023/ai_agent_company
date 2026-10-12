"""No database or model calls: change-window SQL and report-selection contracts.

中文说明：设备适用性相关的变更窗口 SQL 与报告选择契约（不连库、不调用模型）。
隔离方式：Mock + SimpleNamespace 断言生成的 SQL 与选择分支。
边界：窗口边界与筛选条件必须精确，避免把不适用设备纳入报告。
"""
from unittest.mock import Mock
from types import SimpleNamespace

from app.modules.alerts.monitoring import changed_task_query, snapshot_queries
from app.modules.workflows import business_results
from app.modules.workflows.business_report import report_node


def test_changed_tasks_union_all_real_relationships_without_correlated_scans():
    query = changed_task_query(['device-1'], 'inspection')
    assert 'EXISTS' not in query and 'LEFT JOIN' not in query
    assert query.count(' UNION ') == 2
    for alias in ('m','d','a'):
        assert f'COALESCE({alias}.modifiedOn,{alias}.createdOn) >= :windowStart' in query
        assert f'COALESCE({alias}.modifiedOn,{alias}.createdOn) < :windowEnd' in query
    assert 'd.c_shebei' in query and 'a.c_shebei' in query
    assert 'd.isDeleted=0' in query and 'a.isDeleted=0' in query
    maintenance = changed_task_query(['device-1'], 'maintenance')
    assert maintenance.count(' UNION ') == 1 and 't_quyuleixingshebeibaoyangmingxi' not in maintenance


def test_repair_window_collects_late_updates_to_existing_tasks():
    query = snapshot_queries(['device-1'], '2026-09-17 10:00:00', '2026-09-17 11:00:00')['repair']
    assert "COALESCE(m.modifiedOn,m.createdOn) >= '2026-09-17 10:00:00'" in query
    assert "COALESCE(m.modifiedOn,m.createdOn) < '2026-09-17 11:00:00'" in query
    assert 'm.c_weixiuzhuangtai IN (1,2,4)' in query


def test_current_alert_overview_excludes_disabled_and_duplicate_workflows(monkeypatch):
    listing = Mock(return_value={'kind':'alert','items':[]})
    monkeypatch.setattr(business_results,'list_reports',listing)
    business_results.tool_result(None,{'kind':'alert'})
    assert listing.call_args.kwargs['active_only'] and listing.call_args.kwargs['latest_per_workflow']
    business_results.tool_result(None,{'kind':'alert','reference':'old-execution'})
    assert not listing.call_args.kwargs['active_only']


def test_empty_repairs_have_no_pending_items_or_ai_call():
    window={'start':'2026-09-17T10:00:00+08:00','end':'2026-09-17T11:00:00+08:00','timeZone':'Asia/Shanghai','mode':'scheduled'}
    execution=SimpleNamespace(result_json={'collectionWindow':window},snapshot={'graph':{'nodes':[
        {'config':{'toolName':'collect_device_monitoring_snapshot','arguments':{'category':'repair'}}}]}})
    ctx=SimpleNamespace(db=SimpleNamespace(get=lambda *args:execution))
    result=report_node(ctx,SimpleNamespace(kind='alert',title='维修报告',ai_analysis=True),{'data':[]},execution_id='e',node_id='report')
    assert result['activity']=='no_tasks' and result['summary']['pending']==0
    assert '本期没有需要检查的维修任务' in result['content']
    assert 'analysis' not in result


def test_old_rule_report_is_not_presented_as_current_pending(monkeypatch):
    monkeypatch.setattr(business_results, 'list_reports', lambda *args, **kwargs: {
        'kind': 'alert', 'items': [{'workflowName': '机器人保养', 'reportUrl': '/report',
        'historicalRevision': True, 'summary': {'pending': 126},
        'data': [{'设备': '机器人', '结果': '待确认', '说明': '旧版重复问题'}]}]})
    result = business_results.tool_result(None, {'kind': 'alert'})
    assert '等待下一次定时检查' in result['message']
    assert '126' not in result['message'] and '旧版重复问题' not in result['message']
