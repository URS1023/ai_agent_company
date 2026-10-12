"""Inspection records retain their dates and never imply lubrication completion.

中文说明：验证质检记录保留各自日期，且不会把"已巡检"误判成"已完成润滑保养"。
隔离方式：monkeypatch 替换会话与模型，只走工作流执行与判定逻辑，不连业务库、不调用模型。
边界：空结果不得生成正常记录；完成状态不能由执行成功推导出来。
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

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'configurations/szh_new'))
from actual_inspection_workflows import add_actual_inspections

def execute(rows):
    original={'nodes':[{'id':'start','kind':'start','title':'开始','config':{}},
        {'id':'results','kind':'merge','title':'汇总'}, {'id':'end','kind':'end','title':'结束'}],
        'edges':[{'id':'result_end','source':'results','target':'end','port':'out'}]}
    dataset={'id':'t'*26,'version':2,'parameters':{'columns':[]}}
    configured=add_actual_inspections(original,dataset,{'id':'s'*26,'version':2},'d'*26)
    assert add_actual_inspections(configured,dataset,{'id':'s'*26,'version':2},'d'*26)==configured
    graph=FlowGraph.model_validate(configured)
    check=inspect_graph(graph)
    assert check['valid'], check
    states={}
    budget=RuleBudget(time.monotonic()+10)
    for key in check['order']:
        node=next(n for n in graph.nodes if n.id==key)
        parents=active_parent_ids(graph,key,states)
        if node.kind!='start' and not parents:
            states[key]={'status':'skipped'}
            continue
        if node.kind=='read-values': output={'data':rows}
        else:
            inputs={} if node.kind=='start' else select_inputs(node,states,parents,budget,graph)
            output=execute_node(None,node,inputs,budget)
        states[key]={'status':'completed','output':output}
    return states['end']['output']['data']

@pytest.mark.parametrize('result,task,expected',[(1,1,'normal'),(1,2,'pending'),(1,None,'pending'),
    (2,1,'warning'),(2,2,'warning'),('2','1','warning'),('1','1','normal'),
    ('1','2','pending'),(None,1,'pending'),(0,1,'pending'),(3,1,'pending')])
def test_inspection_result_and_task_status(result,task,expected):
    row={'recordKey':'source-record','原巡检结果':result,'任务巡检状态':task,
         '记录时间':'2026-07-01T10:00:00','记录范围':'最近已填写结果的历史记录'}
    output=execute([row])
    assert len(output)==1
    assert output[0]['status']==expected
    assert output[0]['记录时间']==row['记录时间']
    assert output[0]['记录范围']==row['记录范围']
    assert '上次完成时间' not in output[0]

def test_empty_inspection_rows_do_not_create_a_normal_record():
    assert execute([])==[]
