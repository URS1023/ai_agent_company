"""离线校验紧凑版设备监控的采集、判定与业务隔离。

验证行为：采集保留原始查询并在截断时停止；数据源周期与缺数不被判为正常；
已完成的维修不计入当前超期；可视化图先保存快照再调用模型且不改数据源；
按分类取数只读自身数据集，判定不串到其它业务。
隔离方式：monkeypatch + MagicMock 替换会话与模型，图结构用 inspect_graph 静态校验，不连库。
边界：结果截断必须停止而非继续；跨业务域数据不得混入判定。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.errors import ValidationError
from app.modules.alerts import monitoring
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.schemas import FlowGraph
from configurations.szh_new.compact_device_workflow import build_graph
from configurations.szh_new.compact_device_assessment import main, due_at, stamp, amount_ok


def test_collector_preserves_queries_and_stops_on_truncation(monkeypatch):
    monkeypatch.setattr(monitoring, 'get_source', lambda *a, **kw: SimpleNamespace(provider='mysql'))
    query = MagicMock(return_value=SimpleNamespace(data=[], truncated=False, queryId='q', runId='r'))
    monkeypatch.setattr(monitoring, 'execute_query', query)
    result = monitoring.collect_snapshot(None, 'source', {'data': [{'deviceKey': 'device-1'}]})
    assert len(result['queries']) == 3 and query.call_count == 3
    assert all(not c.args[1].need_analysis and c.args[1].mode == 'sql' for c in query.call_args_list)
    query.reset_mock()
    query.return_value.truncated = True
    with pytest.raises(ValidationError):
        monitoring.collect_snapshot(None, 'source', {'data': [{'deviceKey': 'device-1'}]})
    assert query.call_count == 1


def test_source_cycle_and_missing_data_are_not_normal():
    snapshot = {'asOf': '2026-09-17 12:00:00', 'windowStart': '2026-08-18 12:00:00',
                'datasourceId': 's', 'scope': '30 days', 'devices': [{'deviceKey': 'd'}],
                'datasets': {'plans': [{'deviceKey': 'd', 'planId': 'p', 'planName': '巡检', 'planType': 1, 'cycleCode': 2}],
                             'inspection': [{'deviceKey': 'd', 'planName': '巡检', 'planType': 1, 'cycleCode': 2,
                                             'taskStatus': 1, 'completedAt': '2026-09-17 07:00:00', 'result': 2, 'recordId': 'r'}]}}
    result = main(snapshot)
    assert result['summary']['warning'] == 2
    assert result['data'][0]['dueAt'] == '2026-09-17 11:00:00'
    snapshot['datasets']['inspection'] = []
    assert main(snapshot)['summary'] == {'normal': 0, 'warning': 0, 'pending': 1}
    assert due_at(stamp('2026-01-31 00:00:00'), '14', True) == stamp('2026-02-28 00:00:00')
    assert due_at(stamp('2026-01-31 00:00:00'), '20', True) is None
    assert amount_ok('0.03kg', '20-50g') is True


def test_completed_repairs_not_counted_as_current_overdue():
    result = main({'asOf': '2026-09-17 12:00:00', 'windowStart': '', 'datasourceId': 's', 'scope': '',
                   'devices': [{'deviceKey': 'd'}], 'datasets': {'repair': [{'deviceKey': 'd', 'status': 3,
                   'completedAt': '2026-09-16 12:00:00', 'deadline': '2026-09-15 12:00:00', 'acceptance': 1}]}})
    assert result['summary']['warning'] == 0


def test_visual_graph_saves_snapshot_before_model_and_keeps_source():
    graph = FlowGraph.model_validate(build_graph({'id': 's', 'version': None}, ['电石炉', '相变压器']))
    result = inspect_graph(graph)
    assert result['valid'] and result['nodeCount'] == 18
    assert result['order'].index('snapshot') < result['order'].index('report')
    assert next(n for n in graph.nodes if n.id == 'devices').config['datasourceId'] == 's'
    assert not any(n.kind == 'code' for n in graph.nodes)
    assert next(n for n in graph.nodes if n.id == 'cycle_values').inputs['input'].path == '$.data.visualRows.plans'


@pytest.mark.parametrize('category,expected', [
    ('inspection', {'plans', 'inspection', 'areaInspection'}),
    ('maintenance', {'plans', 'maintenanceRequirements', 'maintenance'}),
    ('repair', {'repair'}),
])
def test_category_reads_only_its_own_datasets(monkeypatch, category, expected):
    monkeypatch.setattr(monitoring, 'get_source', lambda *a, **kw: SimpleNamespace(provider='mysql'))
    def execute(ctx, query):
        rows = [{'planType': 1}, {'planType': 2}, {'planType': 3}] if 'SELECT i.c_device deviceKey' in query.query else []
        return SimpleNamespace(data=rows, truncated=False, queryId='q', runId='r')
    monkeypatch.setattr(monitoring, 'execute_query', execute)
    result = monitoring.collect_snapshot(None, 's', {'data': [{'deviceKey': 'd'}]}, category)
    assert set(result['queries']) == expected
    assert result['category'] == category
    graph = build_graph({'id': 's', 'version': None}, '主变', category=category)
    assert graph['nodes'][2]['config']['arguments']['category'] == category


@pytest.mark.parametrize('category', ['inspection', 'maintenance', 'repair'])
def test_category_assessment_does_not_leak_other_business(category):
    snapshot = {'asOf': '2026-09-17 12:00:00', 'windowStart': '', 'datasourceId': 's', 'scope': '',
                'category': category, 'devices': [{'deviceKey': 'd'}], 'datasets': {
                    'plans': [{'deviceKey': 'd', 'planId': 'p1', 'planName': '巡检', 'planType': 1, 'cycleCode': 2},
                              {'deviceKey': 'd', 'planId': 'p2', 'planName': '保养', 'planType': 2, 'maintenanceCycle': '13'}],
                    'repair': [{'deviceKey': 'd', 'status': 4, 'recordId': 'r'}]}}
    result = main(snapshot)
    assert {r['category'] for r in result['data']} == {
        'inspection': {'巡检周期'}, 'maintenance': {'保养周期'}, 'repair': {'维修'},
    }[category]
