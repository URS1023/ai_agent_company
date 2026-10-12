"""Exercise saved business configuration branches, not copied assessor code."""
from pathlib import Path
import sys
import time

from app.modules.workflows.engine import active_parent_ids, select_inputs
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.nodes import execute_node
from app.modules.workflows.schemas import FlowGraph

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'configurations/szh_new'))
from value_workflows import build


def run(scene, rows, cycle=None):
    datasets = {key: {'id': 'd'*26, 'version': 1, 'parameters': {'columns': []}}
                for key in ('inspection', 'cycle', 'maintenance', 'repair')}
    graph = FlowGraph.model_validate(build(datasets, {'id': 's'*26, 'version': 1}, [scene], ['无渗漏']))
    inspected = inspect_graph(graph)
    assert inspected['valid'], inspected['errors']
    assert not any(n.kind in {'code', 'database-query', 'setvar', 'subflow'} for n in graph.nodes)
    states, budget = {}, RuleBudget(time.monotonic()+10)
    nodes = {n.id:n for n in graph.nodes}
    for key in inspected['order']:
        node = nodes[key]
        parents = active_parent_ids(graph, key, states)
        if node.kind != 'start' and not parents:
            states[key] = {'status': 'skipped'}
            continue
        if node.kind == 'read-values':
            output = {'data': cycle or [] if key == 'cycle_read' else rows}
        else:
            arguments = {} if node.kind == 'start' else select_inputs(node, states, parents, budget, graph)
            output = execute_node(None, node, arguments, budget)
        states[key] = {'status': 'completed', 'output': output}
    return states['end']['output']['data']


def test_each_lubrication_point_uses_its_own_period():
    base = {'上次完成时间': '2026-09-01', '参考时间': '2026-09-10', '同名计划数': 1,
            '记录油品': None, '要求油品': 'L-CKD320', '记录方式': None, '要求方式': '加注'}
    rows = [{**base, 'recordKey': key, '周期天数': days} for key, days in [('week', 7), ('month', 30), ('unknown', None)]]
    result = {r['recordKey']: r for r in run('maintenance', rows)}
    assert result['week']['status'] == 'warning'
    assert result['month']['status'] == 'pending'  # A recent date is not evidence of actual oil/amount.
    assert result['unknown']['status'] == 'pending'
    assert result['week']['已过天数'] == 9


def test_numeric_measurement_is_distinct_from_copied_standard():
    base = {'同名计划数': 1, '匹配记录数': 1, '任务状态': 1, '检查结果': 1, '记录时间': '2026-09-01',
            '参考时间': '2026-09-02', '要求': '21V以上', '记录要求': '21V以上', '指标': '数值'}
    rows = [{**base, 'recordKey': key, '实际值': actual} for key, actual in [('boundary', '21V'), ('below', '20V'), ('copied', '21V以上'), ('missing', None), ('wrong_unit', '25°C')]]
    result = {r['recordKey']: r['status'] for r in run('inspection', rows)}
    assert result == {'boundary': 'normal', 'below': 'warning', 'copied': 'pending', 'missing': 'pending', 'wrong_unit': 'pending'}


def test_qualitative_latest_unfinished_and_compound_requirements():
    base = {'同名计划数': 1, '匹配记录数': 1, '任务状态': 1, '检查结果': 1, '记录时间': '2026-09-01',
            '参考时间': '2026-09-02', '要求': None, '记录要求': None, '指标': '正常', '实际值': None}
    rows = [{**base, 'recordKey': 'manual'}, {**base, 'recordKey': 'unfinished', '任务状态': 2},
            {**base, 'recordKey': 'compound', '要求': '油位50%以上、无渗漏', '记录要求': '油位50%以上、无渗漏'},
            {**base, 'recordKey': 'abnormal', '检查结果': 2}, {**base, 'recordKey': 'duplicate', '匹配记录数': 2}]
    result = {r['recordKey']: r['status'] for r in run('inspection', rows)}
    assert result == {'manual': 'normal', 'unfinished': 'pending', 'compound': 'pending', 'abnormal': 'warning', 'duplicate': 'pending'}


def test_completed_repairs_do_not_remain_current_overdue():
    base = {'预计完成时间': '2026-09-01', '实际完成时间': '2026-09-02', '参考时间': '2026-09-10',
            '维修状态': 3, '验收状态': 1, '安装状态': None}
    rows = [{**base, 'recordKey': 'done'}, {**base, 'recordKey': 'acceptance_missing', '验收状态': None},
            {**base, 'recordKey': 'active_late', '维修状态': 2, '实际完成时间': None},
            {**base, 'recordKey': 'rejected', '验收状态': 2}, {**base, 'recordKey': 'bad_date', '实际完成时间': 'invalid'}]
    result = {r['recordKey']: r for r in run('repair', rows)}
    assert {k:r['status'] for k,r in result.items()} == {'done': 'normal', 'acceptance_missing': 'pending', 'active_late': 'warning', 'rejected': 'warning', 'bad_date': 'pending'}
    assert result['done']['完成时超期小时'] == 24
