"""Run configured quality graphs with real node execution and isolated evidence."""
from pathlib import Path
import sys
import time

import pytest

from app.modules.workflows.engine import active_parent_ids, select_inputs
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.nodes import execute_node
from app.modules.workflows.schemas import FlowGraph

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'configurations/szh_new'))
from quality_value_workflows import build_quality, SEASON_STANDARD


def run(rows, category=1, supplementary=None):
    keys = ('quality_items', 'quality_gas1', 'quality_gas2', 'quality_statistics')
    datasets = {key: {'id': 'd'*26, 'version': 1, 'parameters': {'columns': []}} for key in keys}
    graph = FlowGraph.model_validate(build_quality(datasets, {'id': 's'*26, 'version': 1}, category))
    inspected = inspect_graph(graph)
    assert inspected['valid'], inspected['errors']
    assert not any(n.kind in {'code', 'database-query', 'setvar', 'subflow', 'qc-evaluate'} for n in graph.nodes)
    states, budget = {}, RuleBudget(time.monotonic()+10)
    nodes = {n.id: n for n in graph.nodes}
    for key in inspected['order']:
        node = nodes[key]
        parents = active_parent_ids(graph, key, states)
        if node.kind != 'start' and not parents:
            states[key] = {'status': 'skipped'}
            continue
        if node.kind == 'read-values':
            assert node.config['parameterSources']['category'] == 'fixed'
            assert node.config['parameters']['category'] == category
            output = {'data': rows if key == 'quality_items_read' else (supplementary or {}).get(key, [])}
        else:
            arguments = {} if node.kind == 'start' else select_inputs(node, states, parents, budget, graph)
            output = execute_node(None, node, arguments, budget)
        states[key] = {'status': 'completed', 'output': output}
    return {key: states[key]['output']['data'] for key in ('end', 'measurements_end', 'gas1_end', 'gas2_end', 'statistics_end')}


def item(key='one', **changes):
    return {'recordKey': key, '单据标识': key, '质检单号': '单据'+key, '有效模板': 'template',
            '必检项数': 1, '原检验结果': '源库结果', '原合格状态': None, '原单据状态': None,
            '实测记录': key, '有效项目': 'project', '检验项目': '项目', '标准文本': '数值要求',
            '测量值': 10, '判定方式': 'lte', '下界': None, '阈值': 10, **changes}


@pytest.mark.parametrize('changes,expected', [
    ({}, 'normal'), ({'测量值': 11}, 'warning'), ({'测量值': 0}, 'normal'),
    ({'判定方式': 'gte', '测量值': 10}, 'normal'),
    ({'判定方式': 'gte', '测量值': 9, '下界': 1}, 'warning'),
    ({'判定方式': 'between_closed', '下界': 5, '测量值': 5}, 'normal'),
    ({'判定方式': 'between_closed', '下界': 5, '测量值': 10}, 'normal'),
    ({'判定方式': 'between_closed', '下界': 5, '测量值': 4}, 'warning'),
    ({'判定方式': 'between_closed', '下界': 11}, 'pending'),
    ({'判定方式': 'between_closed', '下界': None}, 'pending'),
    ({'测量值': None}, 'pending'), ({'测量值': '无实测'}, 'pending'),
    ({'实测记录': None}, 'pending'), ({'有效项目': None}, 'pending'),
    ({'阈值': None}, 'pending'), ({'判定方式': None}, 'pending'),
    ({'判定方式': 'unsupported'}, 'pending'),
    ({'标准文本': SEASON_STANDARD, '测量值': 12}, 'normal'),
    ({'标准文本': SEASON_STANDARD, '测量值': 14}, 'pending'),
    ({'标准文本': SEASON_STANDARD, '测量值': 14.01}, 'warning'),
    ({'标准文本': '夏季由现场确定', '测量值': 1}, 'pending'),
])
def test_measurement_boundaries_missing_evidence_and_seasons(changes, expected):
    result = run([item(**changes)])
    assert result['measurements_end'][0]['status'] == expected
    row = result['end'][0]
    assert row['status'] == expected
    assert row['结果'] == {'normal': '合格', 'warning': '不合格', 'pending': '待确认'}[expected]
    assert row['原检验结果'] == '源库结果'
    assert '不代表审批通过或正式放行' in row['复判范围']


def test_per_document_grouping_preserves_missing_required_items_and_failure_priority():
    rows = [item('a', 单据标识='mixed', 质检单号='M', 必检项数=2, 测量值=20),
            item('b', 单据标识='mixed', 质检单号='M', 必检项数=2, 实测记录=None),
            item('c', 单据标识='missing', 质检单号='N', 必检项数=2),
            item('d', 单据标识='missing', 质检单号='N', 必检项数=2, 实测记录=None), item('passed')]
    result = run(rows)
    assert len(result['measurements_end']) == 5
    docs = {r['单据标识']: r for r in result['end']}
    assert {k: r['status'] for k, r in docs.items()} == {'mixed': 'warning', 'missing': 'pending', 'passed': 'normal'}
    assert docs['mixed']['实测项数'] == 1
    assert docs['mixed']['不合格项数'] == '1' and docs['mixed']['待确认项数'] == '1'
    assert '让步' in docs['mixed']['说明'] and '不自动放行' in docs['mixed']['说明']


@pytest.mark.parametrize('changes', [{'有效模板': None}, {'必检项数': 0}])
def test_document_cannot_pass_without_a_valid_required_template(changes):
    result = run([item(**changes)])
    assert result['measurements_end'][0]['status'] == 'normal'
    assert result['end'][0]['status'] == 'pending'


@pytest.mark.parametrize('category', [1, 2, 3])
def test_empty_documents_and_unassessed_supplementary_measurements(category):
    supplementary = {key: [{'recordKey': key, '测量值': 123}]
                     for key in ('quality_gas1_read', 'quality_gas2_read', 'quality_statistics_read')}
    result = run([], category, supplementary)
    assert result['end'] == [] and result['measurements_end'] == []
    for key in ('gas1', 'gas2', 'statistics'):
        assert result[key+'_end'] == supplementary['quality_'+key+'_read']
