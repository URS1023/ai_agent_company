"""Review and apply local device-workflow applicability without changing source data."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,select,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.config import settings
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.workflows.models import Workflow,WorkflowRevision
from app.modules.workflows.schemas import WorkflowInput,FlowGraph
from app.modules.workflows.service import save_workflow,change_status
from app.modules.workflows.graph import inspect_graph,graph_sources
from app.modules.system_tools.models import ScheduledJob
from app.modules.system_tools.schemas import JobInput
from app.modules.system_tools.schedules import save_job,change_job
from configurations.yinhao.graph_builder import compare,expr,field,GraphBuilder
from scripts.workflow_testdata.database import local_listener,check_destination,check_identity,load_connection_and_flows

FOLDER=ROOT/'.implementation/device_applicability_20260917'


def revise(graph,kind):
    """为保养/维修类设备流程插入适用性过滤节点，返回重新布局后的图（dict）。

    kind='maintenance' 时在数值节点前加「仅有保养计划」「已有保养记录」两个 filter；
    kind='repair' 时加「仅检查实际存在的维修任务」，并把维修未完成的提示语改成明确跟踪口径。
    已有同名节点则跳过（幂等）。函数会校验图可执行（inspect_graph 必须 valid），不通过直接断言失败。
    """
    graph=deepcopy(graph)
    def gate(target,key,title,predicate):
        if any(n['id']==key for n in graph['nodes']):return
        node=next(n for n in graph['nodes'] if n['id']==target)
        for edge in graph['edges']:
            if edge['target']==target:edge['target']=key
        graph['nodes'].append({'id':key,'kind':'filter','title':title,'config':{'expression':predicate},
            'inputs':node.get('inputs',{}),'position':{'x':0,'y':0}})
        node['inputs']={}
        graph['edges'].append({'id':key+'_out_'+target,'source':key,'target':target,'port':'out'})
    if kind=='maintenance':
        gate('cycle_values','applicable_plans','仅检查已配置保养计划的设备',compare('gt','planMatches',0))
        gate('maintenance_values','recorded_maintenance','已有保养记录才展开点位检查',compare('gt','recordMatches',0))
    elif kind=='repair':
        gate('repair_values','actual_repairs','仅检查实际存在的维修任务',expr('present',field('recordId')))
        next(n for n in graph['nodes'] if n['id']=='repair_pending')['config']['message']='已有维修任务尚未完成，或状态时间不完整，需要继续跟踪。'
    builder=GraphBuilder({});builder.nodes=graph['nodes'];builder.edges=graph['edges'];builder.layout()
    parsed=FlowGraph.model_validate(graph);assert inspect_graph(parsed)['valid']
    return parsed.model_dump(mode='json',by_alias=True)


def main(phase):
    """两阶段应用设备流程适用性修订：phase='prepare' 只生成 prepared_changes.json，其它值执行变更。

    参数：phase 由命令行第 1 个参数传入（脚本不做 argparse 校验）；'prepare' 阶段只读库并落盘，
    其余阶段会：暂停相关调度、保存并发布新图（或停用无可检查项的流程）、重写调度版本。
    前置条件：仅本机（校验 @@hostname/端口/datadir 与库名 company_agent），
    .implementation/device_applicability_20260917/maintenance_audit.json 必须已存在；
    执行阶段若 applied_changes.json 已存在会直接断言失败，防止重复应用；
    并发修改（工作流或调度 version 变化）同样会断言失败。
    """
    local_listener();url=make_url(settings.DATABASE_URL)
    check_destination(url.host,url.port,url.database,'company_agent')
    engine=create_engine(url,connect_args={'connect_timeout':3,'read_timeout':5,'write_timeout':5,
        'init_command':'SET SESSION MAX_EXECUTION_TIME=3000'},hide_parameters=True)
    try:
        with Session(engine) as db:
            check_identity(db.execute(text('SELECT @@hostname host,@@port port,@@datadir datadir,DATABASE() db')).mappings().one(),'company_agent')
            if phase=='prepare':
                _,flows=load_connection_and_flows()
                audit=json.loads((FOLDER/'maintenance_audit.json').read_text('utf-8'))
                disabled={f['id'] for f in audit['flows'] if not f['plans'] and not f['taskSummary']}
                changes=[]
                for flow in flows:
                    if flow['kind'] not in {'maintenance','repair'}:continue
                    w=db.get(Workflow,flow['id']);assert w.graph==db.scalar(select(WorkflowRevision.snapshot).where(
                        WorkflowRevision.workflow_id==w.id,WorkflowRevision.revision==w.published_revision))['graph'], 'Unpublished edits exist'
                    jobs=db.scalars(select(ScheduledJob).where(ScheduledJob.target_id==w.id,ScheduledJob.workspace_id==w.workspace_id)).all()
                    graph=revise(w.graph,flow['kind']) if w.id not in disabled else deepcopy(w.graph)
                    assert graph_sources(FlowGraph.model_validate(graph))==graph_sources(FlowGraph.model_validate(w.graph))
                    changes.append({'id':w.id,'name':w.name,'version':w.version,'domains':w.domains,'description':w.description,
                        'disable':w.id in disabled,'graph':graph,'originalGraph':w.graph,
                        'jobs':[{c.name:getattr(j,c.name) for c in j.__table__.columns} for j in jobs]})
                (FOLDER/'prepared_changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
                print(json.dumps({'disable':[f['name'] for f in changes if f['disable']],'publish':sum(not f['disable'] for f in changes)},ensure_ascii=False))
                return
            changes=json.loads((FOLDER/'prepared_changes.json').read_text('utf-8'))
            assert not (FOLDER/'applied_changes.json').exists(),'Already applied; inspect saved result'
            for item in changes:
                assert db.get(Workflow,item['id']).version==item['version'],'Concurrent workflow edit'
                for job in item['jobs']:assert db.get(ScheduledJob,job['id']).version==job['version'],'Concurrent schedule edit'
            applied=[]
            for item in changes:
                w=db.get(Workflow,item['id'])
                ctx=auth_context_from_tool({'db':db,'workspace_id':w.workspace_id,'user_id':w.owner_user_id})
                for job in item['jobs']:
                    j=db.get(ScheduledJob,job['id'])
                    if j.status=='active':change_job(ctx,j.id,j.version,'pause')
                if item['disable']:
                    updated=change_status(ctx,w.id,w.version,publish=False)
                else:
                    saved=save_workflow(ctx,WorkflowInput(name=w.name,description=w.description,domains=w.domains,
                        graph=item['graph'],version=w.version),w.id)
                    updated=change_status(ctx,w.id,saved.version,publish=True)
                    for job in item['jobs']:
                        j=db.get(ScheduledJob,job['id'])
                        save_job(ctx,JobInput(code=j.code,name=j.name,description=j.description,taskType=j.task_type,
                            targetId=j.target_id,scheduleKind=j.schedule_kind,scheduleExpression=j.schedule_expression,
                            timezone=j.timezone,payload={**j.payload,'version':updated.version},status='paused',version=j.version),j.id)
                applied.append({'id':w.id,'name':w.name,'status':updated.status,'version':updated.version,
                    'resumeJobs':[j['id'] for j in item['jobs'] if j['status']=='active'] if not item['disable'] else []})
                (FOLDER/'applied_changes.json').write_text(json.dumps(applied,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(applied,ensure_ascii=False))
    finally:engine.dispose()


if __name__=='__main__':main(sys.argv[1])
