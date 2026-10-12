"""离线校验模块工作流图的修复结果（质检范围/设备范围与数值节点）。

验证行为：质检图按产品与已保存的检验项标准限定范围；设备图限定设备范围且数值结果不产生笛卡尔积。
隔离方式：只读 configurations/szh_new/module_workflow_repair 的建图函数，用 FlowGraph 校验结构，
不连库、不执行工作流（脚本自行把仓库根加入 sys.path）。
边界：JOIN 不得放大行数；范围条件必须落实在图中而不是靠调用方补。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from configurations.szh_new.module_workflow_repair import quality_module_graph, device_module_graph
from app.modules.workflows.schemas import FlowGraph
from app.modules.workflows.graph import inspect_graph
from app.modules.workflows.expressions import evaluate, RuleBudget


SOURCE = {'id': '01M24QGNNN404ZF3A9XB48FRY5', 'version': None}


def test_quality_scoped_product_and_saved_item_standard():
    graph = quality_module_graph(SOURCE, {'categoryValue': 1, 'productId': 'real-product'})
    assert inspect_graph(FlowGraph.model_validate(graph))['valid']
    nodes = {n['id']: n for n in graph['nodes']}
    assert 't.c_product=:product' in nodes['forms']['config']['query']
    assert nodes['forms']['config']['parameters']['product'] == 'real-product'
    assert nodes['standard_saved']['config']['calculations'][0]['left']['field'] == '单据判断方式'
    row = {'测量数值': 80, '上限数值': 82, '判定方式': 'gte', '有效模板': 't', '有效项目': 'p', '主要项目系数': 1}
    assert evaluate(nodes['standard_test']['config']['expression'], row, RuleBudget(time.monotonic() + 2)) is True
    row.update(测量数值=82)
    assert evaluate(nodes['standard_test']['config']['expression'], row, RuleBudget(time.monotonic() + 2)) is False
    row.update(主要项目系数=None)
    assert evaluate(nodes['standard_ready']['config']['expression'], row, RuleBudget(time.monotonic() + 2)) is True


def test_device_scopes_and_numeric_result_no_cartesian_join():
    for name in ['110kV站主变压器监控工作流', '冶炼出炉机器人监控工作流', '集控室DCS机柜巡检']:
        graph = device_module_graph(SOURCE, name)
        assert inspect_graph(FlowGraph.model_validate(graph))['valid']
        assert len(graph['nodes']) <= 100
        nodes = {n['id']: n for n in graph['nodes']}
        sql = nodes['actual_read']['config']['query']
        assert 'dm.deviceManagementId=COALESCE(NULLIF(d.c_shebei' in sql
        assert 'mdDeviceInspectionMaintenanceId' in sql
        mapping = nodes['actual_values']['config']['calculations'][0]['mapping']
        assert mapping == {'1': '正常', '2': '异常'}
        assert all(n['kind'] != 'llm' for n in graph['nodes'])
