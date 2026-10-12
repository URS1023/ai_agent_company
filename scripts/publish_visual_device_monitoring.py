"""Migrate only the reviewed 18 local device flows; preserve sources and job identities."""
import json,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend'),str(ROOT/'scripts')]
from sqlalchemy import select,text
from sqlalchemy.engine import make_url
from app.config import settings
from benchmark_local_szh import local_listener,require_local
u=make_url(settings.DATABASE_URL)
require_local(u.host,u.port or 3306,'szh_new');assert u.database=='company_agent';local_listener()
from app.db import new_session
from app.models import User,Workspace,WorkspaceMember,DataSource
from app.common.deps import AuthContext
from app.modules.workflows.models import Workflow
from app.modules.workflows.schemas import WorkflowInput,FlowGraph
from app.modules.workflows.graph import executable_graph
from app.modules.workflows.service import save_workflow,change_status
from app.modules.system_tools.models import ScheduledJob
from app.modules.system_tools.schemas import JobInput
from app.modules.system_tools.schedules import save_job,job_view,change_job
from configurations.szh_new.visual_device_workflow import build_graph
folder=ROOT/'.implementation/visual_device_release_20260917';folder.mkdir(exist_ok=True)
manifest=json.loads((ROOT/'.implementation/device_split_20260917/published.json').read_text(encoding='utf-8'))
phase=sys.argv[1] if len(sys.argv)>1 else 'prepare'
def write(name,value): (folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
with new_session() as db:
 db.execute(text('SET SESSION MAX_EXECUTION_TIME=3000'))
 assert db.scalar(text('SELECT @@hostname')).lower()==os.environ['COMPUTERNAME'].lower()
 def context(w):
  m=db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id==w.workspace_id,WorkspaceMember.user_id==w.owner_user_id))
  assert m
  return AuthContext(db,db.get(User,m.user_id),db.get(Workspace,m.workspace_id),m.role,'')
 if phase=='prepare':
  before=[];prepared=[]
  for item in manifest:
   w=db.get(Workflow,item['id']);j=db.get(ScheduledJob,item['scheduleId']);assert w and j and j.target_id==w.id
   root=next(n for n in w.graph['nodes'] if n['id']=='devices')
   source=db.get(DataSource,root['config']['datasourceId']);cfg=source.connection_config
   require_local(cfg.get('host'),int(cfg.get('port') or 3306),cfg.get('databaseName') or cfg.get('database'))
   keywords=[f['value'] for f in root['config']['filters'] if f['field']=='c_deviceName']
   profile=next(n for n in w.graph['nodes'] if n['kind']=='llm')['config']['modelProfile']
   graph=build_graph({'id':source.id,'version':None},keywords,profile,item['category'])
   executable_graph(context(w),FlowGraph.model_validate(graph))
   before.append({'id':w.id,'name':w.name,'version':w.version,'publishedRevision':w.published_revision,'graph':w.graph,'job':job_view(j)})
   prepared.append({**item,'version':w.version,'graph':graph})
  scope=db.get(Workflow,manifest[0]['id'])
  ids=db.scalars(select(Workflow.id).where(Workflow.workspace_id==scope.workspace_id,Workflow.owner_user_id==scope.owner_user_id,Workflow.status!='deleted')).all()
  quality=[]
  for wid in ids:
   w=db.get(Workflow,wid)
   if 'quality' not in w.domains:continue
   def code_nodes(graph):
    result=[]
    for n in graph.get('nodes',[]):
     if n['kind']=='code':result.append(n['id'])
     if isinstance(n.get('config',{}).get('subgraph'),dict):result+=code_nodes(n['config']['subgraph'])
    return result
   quality.append({'id':w.id,'name':w.name,'status':w.status,'publishedRevision':w.published_revision,'codeNodes':code_nodes(w.graph)})
  write('before.json',before);write('prepared.json',prepared);write('quality_audit.json',quality)
  print(json.dumps({'devices':len(prepared),'schedules':[{'name':r['name'],'status':r['job']['status'],'expression':r['job']['scheduleExpression'],'payload':r['job']['payload'],'next':r['job']['nextRunAt']} for r in before],'quality':quality},ensure_ascii=False))
 elif phase=='publish':
  assert not (folder/'published.json').exists(),'Review previous publication first'
  prepared=json.loads((folder/'prepared.json').read_text(encoding='utf-8'))
  for item in prepared:
   w=db.get(Workflow,item['id']);assert w.version==item['version']
  for item in prepared:
   w=db.get(Workflow,item['id']);j=db.get(ScheduledJob,item['scheduleId'])
   if j.status=='active':change_job(context(w),j.id,j.version,'pause')
  result=[]
  for item in prepared:
   w=db.get(Workflow,item['id']);ctx=context(w);j=db.get(ScheduledJob,item['scheduleId'])
   saved=save_workflow(ctx,WorkflowInput(name=w.name,description='可视化条件节点独立判断；定时采集、保存快照、逐项结果与AI报告。',domains=w.domains,graph=FlowGraph.model_validate(item['graph']),version=w.version),w.id)
   published=change_status(ctx,w.id,saved.version,publish=True)
   j=save_job(ctx,JobInput(code=j.code,name=j.name,description=j.description,taskType='workflow',targetId=w.id,
      scheduleKind=j.schedule_kind,scheduleExpression=j.schedule_expression,timezone=j.timezone,
      payload={**j.payload,'mode':'published','version':published.version},status='paused',version=j.version),j.id)
   result.append({'id':w.id,'name':w.name,'version':published.version,'scheduleId':j['id'],'nodeCount':len(item['graph']['nodes'])})
   write('published.json',result)
  print('Published',len(result),'workflows; schedules paused for reload')
 elif phase=='activate':
  result=[]
  published=json.loads((folder/'published.json').read_text(encoding='utf-8'));assert len(published)==18
  for item in published:
   w=db.get(Workflow,item['id']);j=db.get(ScheduledJob,item['scheduleId']);assert w.version==item['version'] and j.status=='paused'
   result.append(change_job(context(w),j.id,j.version,'activate'))
  write('schedules_after.json',result)
  print('Activated',len(result),'schedules')
 elif phase=='verify':
  from app.modules.workflows.models import WorkflowRevision
  result=[]
  for item in json.loads((folder/'published.json').read_text(encoding='utf-8')):
   w=db.get(Workflow,item['id']);j=db.get(ScheduledJob,item['scheduleId'])
   revision=db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_id==w.id,WorkflowRevision.revision==w.published_revision))
   assert w.status=='published' and w.version==item['version'] and revision
   assert not any(n['kind']=='code' for n in revision.snapshot['graph']['nodes'])
   assert j.status=='active' and j.payload['version']==w.version and j.next_run_at
   result.append({'name':w.name,'revision':w.published_revision,'nodes':len(revision.snapshot['graph']['nodes']),
                  'schedule':j.schedule_expression,'timezone':j.timezone,'nextRunUtc':j.next_run_at})
  for prior in json.loads((folder/'quality_audit.json').read_text(encoding='utf-8')):
   w=db.get(Workflow,prior['id']);assert w.published_revision==prior['publishedRevision']
  write('verified.json',result)
  print(json.dumps({'verified':len(result),'jobsActive':len(result),'qualityUnchanged':True,'entries':result},ensure_ascii=False,default=str))
 elif phase=='recent':
  from app.modules.system_tools.models import ScheduledJobRun
  result=[]
  for item in manifest:
   rows=db.execute(select(ScheduledJobRun.id,ScheduledJobRun.status,ScheduledJobRun.started_at,ScheduledJobRun.error_summary).where(
       ScheduledJobRun.job_id==item['scheduleId']).order_by(ScheduledJobRun.created_at.desc()).limit(1)).mappings().all()
   result.append({'name':item['name'],'runs':[dict(r) for r in rows]})
  write('recent_runs.json',result)
  print(json.dumps(result,ensure_ascii=False,default=str))
 elif phase=='latest_visual_run':
  from app.modules.workflows.models import WorkflowExecution
  item=next(item for item in manifest if 'DCS' in item['name'] and item['category']=='inspection')
  rows=db.execute(select(WorkflowExecution.id,WorkflowExecution.status,WorkflowExecution.revision,WorkflowExecution.error_message,WorkflowExecution.created_at).where(
     WorkflowExecution.workflow_id==item['id']).order_by(WorkflowExecution.created_at.desc()).limit(1)).mappings().all()
  write('latest_visual_run.json',[dict(row) for row in rows]);print(json.dumps([dict(row) for row in rows],ensure_ascii=False,default=str))
 else:raise ValueError(phase)
