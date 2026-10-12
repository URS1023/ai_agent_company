"""No services or database connections: test MES rule boundaries and graph shape.

中文说明：MES 场景规则的边界与流程图结构校验（不启动服务、不连数据库）。
隔离方式：纯函数 + 本地计时，不连接任何数据库与外部服务。
边界：规则上下界与分支必须与配置一致，图要有限可执行。
"""
import sys
import time
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'backend')]
from configurations.szh_new.scenario_rules import standard_conditions
from configurations.szh_new.scenario_workflows import build_six
from app.modules.workflows.expressions import RuleBudget, evaluate
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.schemas import FlowGraph


def classify(rule, actual, low=10, high=20):
    row = {'判定方式': rule, '测量数值': actual, '下限数值': low, '上限数值': high}
    invalid, violation = standard_conditions()
    budget = RuleBudget(time.monotonic() + 1)
    return 'pending' if evaluate(invalid, row, budget) else 'warning' if evaluate(violation, row, budget) else 'normal'


@pytest.mark.parametrize('rule,at_min,at_max', [
    ('between_closed', 'normal', 'normal'), ('between_open', 'warning', 'warning'),
    ('between_leftOpen', 'warning', 'normal'), ('between_rightOpen', 'normal', 'warning'),
])
def test_range_endpoints_and_inner_outer_values(rule, at_min, at_max):
    assert classify(rule, 10) == at_min
    assert classify(rule, 20) == at_max
    assert classify(rule, 15) == 'normal'
    assert classify(rule, 9) == 'warning'
    assert classify(rule, 21) == 'warning'
    assert classify(rule, 10, low=20, high=10) == 'pending'


@pytest.mark.parametrize('rule,below,equal,above', [
    ('gt', 'warning', 'warning', 'normal'), ('gte', 'warning', 'normal', 'normal'),
    ('lt', 'normal', 'warning', 'warning'), ('lte', 'normal', 'normal', 'warning'),
    ('eq', 'warning', 'normal', 'warning'), ('neq', 'normal', 'warning', 'normal'),
])
def test_single_boundaries(rule, below, equal, above):
    assert classify(rule, 19) == below
    assert classify(rule, 20) == equal
    assert classify(rule, 21) == above


@pytest.mark.parametrize('rule,value,low,high', [
    ('lte', None, None, 20), ('lte', 10, None, None), ('unknown', 10, 0, 20),
    ('between_open', 10, None, 20), ('between_open', 10, 10, 10),
    ('between_leftOpen', 10, 10, 10), ('between_rightOpen', 10, 10, 10),
])
def test_incomplete_or_empty_ranges_never_pass(rule, value, low, high):
    assert classify(rule, value, low, high) == 'pending'


def test_all_six_graphs_have_editable_nodes_and_bound_arrows():
    workflows = build_six({'id': 'local-source', 'version': 1})
    assert len(workflows) == 6
    for key, item in workflows.items():
        graph = FlowGraph.model_validate(item['graph'])
        result = inspect_graph(graph)
        assert result['valid'], (key, result)
        kinds = {node.kind for node in graph.nodes}
        assert not kinds & {'code', 'database', 'sub-workflow'}
        for node in graph.nodes:
            if node.kind == 'read-values':
                assert node.config['datasourceId'] == 'local-source'
                assert node.config['readMode'] == 'table'
        for edge in graph.edges:
            assert edge.source_side == 'right'
            assert edge.target_side == 'left'
            parent = next(node for node in graph.nodes if node.id == edge.source)
            child = next(node for node in graph.nodes if node.id == edge.target)
            assert not (parent.kind == child.kind == 'output-values')


def test_quality_keeps_complete_stored_standard_and_independent_sample_paths():
    graph = build_six({'id': 'local-source', 'version': 1})['incoming']['graph']
    nodes = {node['id']: node for node in graph['nodes']}
    for item in nodes['standard_saved']['config']['calculations'][:4]:
        assert item['operation'] == 'copy'
        assert item['left']['field'].startswith('单据')
    assert not any(edge['source'] == 'gas_before' and edge['target'] == 'gas_after' for edge in graph['edges'])
    assert nodes['gas_before']['config']['fieldAliases']['incomingMaterialQcItemDetailsId'] == '样本标识'
    assert nodes['gas_after']['config']['fieldAliases']['incomingMaterialQcItemDetails2Id'] == '样本标识'
    assert nodes['start']['config']['variables'][0]['required'] is True
