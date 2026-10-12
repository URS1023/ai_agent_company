"""Each source requirement must be assessed even if another lacks evidence.

中文说明：某条来源要求缺少证据时，其余要求仍必须被逐条评估（不能整批跳过）。
隔离方式：本地夹具 + 纯判定函数（脚本自行加入仓库根路径），不连库、不调用模型。
"""
from pathlib import Path
import sys
import time

import pytest

from app.modules.workflows.engine import active_parent_ids, select_inputs
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.nodes import execute_node
from app.modules.workflows.schemas import FlowGraph
from app.modules.workflows.value_nodes import calculate_one
from app.modules.workflows.value_schemas import ValueCalculation

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'configurations/szh_new'))
from maintenance_required_checks import rebuild_belt_maintenance


def execute(rows):
    original = {'nodes': [
        {'id': 'start', 'kind': 'start', 'title': '开始', 'config': {}, 'position': {'x': 0, 'y': 0}},
        {'id': 'maintenance_read', 'kind': 'read-values', 'title': '读取值', 'position': {'x': 340, 'y': 0},
         'config': {'datasourceId': 's' * 26, 'templateId': 't' * 26, 'templateVersion': 2}},
        {'id': 'results', 'kind': 'merge', 'title': '汇总'},
        {'id': 'end', 'kind': 'end', 'title': '结束'}],
        'edges': [{'id': 'start_read', 'source': 'start', 'target': 'maintenance_read', 'port': 'out'},
                  {'id': 'result_end', 'source': 'results', 'target': 'end', 'port': 'out'}]}
    configured = rebuild_belt_maintenance(original)
    assert rebuild_belt_maintenance(configured) == configured
    graph = FlowGraph.model_validate(configured)
    check = inspect_graph(graph)
    assert check['valid'], check
    states = {}
    budget = RuleBudget(time.monotonic() + 10)
    for key in check['order']:
        node = next(n for n in graph.nodes if n.id == key)
        parents = active_parent_ids(graph, key, states)
        if node.kind != 'start' and not parents:
            states[key] = {'status': 'skipped'}
            continue
        if node.kind == 'read-values':
            output = {'data': rows}
        else:
            inputs = {} if node.kind == 'start' else select_inputs(node, states, parents, budget, graph)
            output = execute_node(None, node, inputs, budget)
        states[key] = {'status': 'completed', 'output': output}
    return states['end']['output']['data'], states


def source_row(**changes):
    return {'recordKey': 'point-1', '同名计划数': 1, '周期天数': 30,
            '上次完成时间': '2026-09-01', '本次完成时间': '2026-09-01', '参考时间': '2026-09-02',
            '任务状态': 1, '点位状态': 1, '要求油品': '3#锂基脂', '记录油品': '3#锂基脂',
            '要求方式': '加油枪注入', '记录方式': '加油枪注入',
            '要求用量': '0.02-0.04Kg', '记录用量': '0.03Kg', **changes}


def conclusions(row):
    rows, states = execute([row])
    assert len(rows) == 4
    assert {r['recordKey'] for r in rows} == {'point-1'}
    assert all(states[f'maintcheck_{k}_values']['status'] == 'completed' for k in ('cycle', 'oil', 'method', 'amount'))
    return {r['检查项目']: r['status'] for r in rows}


def test_missing_previous_date_does_not_block_other_completed_checks():
    assert conclusions(source_row(上次完成时间=None)) == {
        '保养超期': 'pending', '润滑油品': 'normal', '保养方式': 'normal', '润滑用量': 'normal'}


def test_overdue_does_not_hide_other_violations():
    assert conclusions(source_row(上次完成时间='2026-07-01', 记录油品='错误油品', 记录方式='错误方式', 记录用量='0.05kg')) == {
        '保养超期': 'warning', '润滑油品': 'warning', '保养方式': 'warning', '润滑用量': 'warning'}


def test_missing_period_does_not_block_oil_or_method():
    assert conclusions(source_row(周期天数=None, 记录油品='错误油品')) == {
        '保养超期': 'pending', '润滑油品': 'warning', '保养方式': 'normal', '润滑用量': 'normal'}


@pytest.mark.parametrize('actual,expected', [
    ('0.02Kg', 'normal'), ('0.04kg', 'normal'), ('20g', 'normal'), ('40G', 'normal'),
    ('19g', 'warning'), ('41g', 'warning'), ('0.03', 'pending'), (None, 'pending'),
    ('0.02-0.04Kg', 'pending'), ('30g左右', 'pending'), ('-1g', 'warning')])
def test_quantity_uses_source_bounds_and_converts_explicit_units(actual, expected):
    assert conclusions(source_row(记录用量=actual))['润滑用量'] == expected


def test_source_bounds_are_dynamic_not_hardcoded():
    assert conclusions(source_row(要求用量='100-200g', 记录用量='0.15kg'))['润滑用量'] == 'normal'
    assert conclusions(source_row(要求用量='100-200g', 记录用量='0.03kg'))['润滑用量'] == 'warning'


@pytest.mark.parametrize('requirement', ['根据实际情况', '20-50g不等', '0.02-0.04', '40-20g', None])
def test_unclear_requirement_never_invents_a_quantity_rule(requirement):
    assert conclusions(source_row(要求用量=requirement))['润滑用量'] == 'pending'


@pytest.mark.parametrize('changes', [{'任务状态': 2}, {'点位状态': 2}, {'同名计划数': 2},
                                    {'本次完成时间': None}, {'本次完成时间': '2026-10-01'}])
def test_uncompleted_or_ambiguous_records_do_not_prove_compliance(changes):
    result = conclusions(source_row(**changes))
    assert all(result[k] == 'pending' for k in ('润滑油品', '保养方式', '润滑用量'))


def test_current_source_missing_evidence_produces_four_clear_checks_per_point():
    rows = [source_row(recordKey=f'point-{i}', 上次完成时间=None, 本次完成时间=None,
                       任务状态=None, 点位状态=None, 记录油品=None, 记录方式=None, 记录用量=None) for i in range(12)]
    output, states = execute(rows)
    assert len(output) == 48
    assert all(r['status'] == 'pending' for r in output)
    assert {r['检查要求'] for r in output} == {30, '3#锂基脂', '加油枪注入', '0.02-0.04Kg'}
    assert all(r['实测值'] is None for r in output)
    assert all(states[f'maintcheck_{k}_values']['status'] == 'completed' for k in ('cycle', 'oil', 'method', 'amount'))


def test_empty_source_does_not_create_results():
    assert execute([])[0] == []


@pytest.mark.parametrize('operation,value,expected', [
    ('range_lower', '0.02-0.04Kg', '0.02'), ('range_upper', '0.02-0.04Kg', '0.04'),
    ('range_unit', '0.02-0.04Kg', 'kg'), ('range_lower', '20 至 40 g', '20'),
    ('range_upper', '-2~-1g', '-1'), ('range_lower', '40-20g', None),
    ('range_unit', '20g-40kg', None), ('range_upper', '20-50g不等', None),
    ('range_lower', None, None), ('range_lower', '0.03kg', None)])
def test_range_calculations(operation, value, expected):
    config = ValueCalculation(name='结果', operation=operation, left={'source': 'constant', 'value': value})
    assert calculate_one(config, {}) == expected
