"""Execute visual rule nodes on bounded fixtures; no database, tools or model.

中文说明：在有限夹具上执行可视化规则节点（判断/取值分支），不使用数据库、工具或模型。
隔离方式：本地夹具 + 图执行，脚本自行把仓库根加入 sys.path。
边界：分支条件必须与配置一致，缺数据要显式呈现而不是猜测。
"""
import sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pytest
from app.modules.alerts.monitoring_rows import visual_rows
from app.modules.workflows.nodes import execute_node
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.schemas import FlowGraph
from app.modules.workflows.graph import inspect_graph
from configurations.szh_new.visual_device_workflow import build_graph
from app.modules.analytics.sql_safety import authorize_sql
from app.errors import ValidationError


def run(category,datasets):
    snapshot={'category':category,'asOf':'2026-09-17 12:00:00','windowStart':'2026-08-18',
              'scope':'fixture','devices':[{'deviceKey':'d'}],'datasets':datasets}
    rows=visual_rows(snapshot)
    graph=FlowGraph.model_validate(build_graph({'id':'s','version':None},'x',category=category))
    inspection=inspect_graph(graph)
    assert inspection['valid'],inspection
    assert not any(n.kind=='code' for n in graph.nodes)
    nodes={n.id:n for n in graph.nodes};outputs={};budget=RuleBudget(time.monotonic()+5)
    for key in inspection['order']:
        node=nodes[key]
        if key in {'start','devices','snapshot','report','end'}:continue
        if node.inputs:
            dataset=node.inputs['input'].path.split('.')[-1]
            inputs={'input':rows[dataset]}
        else:
            incoming=[]
            for edge in graph.edges:
                if edge.target!=key or edge.source not in outputs:continue
                value=outputs[edge.source]
                if nodes[edge.source].kind=='condition':
                    value=value['trueRows' if edge.port=='true' else 'falseRows']
                incoming.append(value)
            inputs=({str(i):v for i,v in enumerate(incoming)} if node.kind=='merge' else {'input':incoming[0]})
        outputs[key]=execute_node(None,node,inputs,budget)
    return outputs['results']['data']


def plan(cycle='5',kind=1):
    return {'deviceKey':'d','planId':'p','planName':'P','planType':kind,'cycleCode':cycle,'maintenanceCycle':cycle}


def test_inspection_cycle_and_manual_result_visible():
    rows=run('inspection',{'plans':[plan()], 'inspection':[{'deviceKey':'d','planName':'P','planType':1,
        'cycleCode':'5','completedAt':'2026-09-16 10:00:00','taskStatus':1,'result':2}]})
    assert len(rows)==2 and all(r['status']=='warning' for r in rows)


@pytest.mark.parametrize('category',['inspection'])
def test_missing_inspection_evidence_never_normal(category):
    rows=run(category,{})
    assert rows and all(r['status']=='pending' for r in rows)


@pytest.mark.parametrize('category',['maintenance','repair'])
def test_no_plan_or_repair_task_does_not_create_pending(category):
    assert run(category,{}) == []


def test_missing_maintenance_record_is_one_plan_issue_not_twenty_point_issues():
    datasets={'plans':[plan('13',2)],'maintenanceRequirements':[
        {'deviceKey':'d','planId':'p','pointCode':str(i),'days':7,'oil':'A','method':'B','amount':'1g'} for i in range(5)]}
    rows=run('maintenance',datasets)
    assert len(rows)==1 and rows[0]['status']=='pending'


def test_calendar_month_not_fixed_days():
    rows=run('inspection',{'plans':[plan('7')], 'inspection':[{'deviceKey':'d','planName':'P','planType':1,
        'cycleCode':'7','completedAt':'2026-08-17 12:00:00','taskStatus':1,'result':1}]})
    assert all(r['status']=='normal' for r in rows)


@pytest.mark.parametrize('acceptance,expected',[(1,'normal'),(2,'warning'),(None,'pending')])
def test_completed_repair_not_current_overdue(acceptance,expected):
    rows=run('repair',{'repair':[{'recordId':'r','deviceKey':'d','status':3,'completedAt':'2026-09-16 12:00:00',
        'deadline':'2026-09-15 12:00:00','acceptance':acceptance}]})
    assert len(rows)==1 and rows[0]['status']==expected


def test_maintenance_units_and_independent_evidence():
    datasets={'plans':[plan('13',2)],'maintenanceRequirements':[{'deviceKey':'d','planId':'p','pointCode':'x',
        'days':7,'oil':'A','method':'B','amount':'0.02-0.04kg'}],
        'maintenance':[{'deviceKey':'d','planName':'P','pointCode':'x','completedAt':'2026-09-16 12:00:00',
        'taskStatus':1,'pointStatus':1,'oil':'A','method':'B','amount':'30g'}]}
    rows=run('maintenance',datasets)
    assert len(rows)==5 and all(r['status']=='normal' for r in rows)
    datasets['maintenance'][0]['oil']='wrong'
    rows=run('maintenance',datasets)
    assert sum(r['status']=='warning' for r in rows)==1


def test_exists_authorizes_inner_tables_and_columns():
    tables=[{'tableName':'devices','columns':[{'columnName':'id'}]}]
    good='SELECT d.id FROM devices d WHERE EXISTS (SELECT 1 FROM devices x WHERE x.id=d.id)'
    assert authorize_sql(good,provider='mysql',tables=tables,max_rows=5).tables==('devices',)
    for bad in [good.replace('devices x','private x'),good.replace('x.id=d.id','x.secret=d.id'),
                good.replace('SELECT 1','SELECT SLEEP(2)')]:
        with pytest.raises(ValidationError):authorize_sql(bad,provider='mysql',tables=tables,max_rows=5)
