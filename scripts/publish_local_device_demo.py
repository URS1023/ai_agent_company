"""Publish and execute one isolated local demo workflow; preserve existing bindings."""
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
OUT=ROOT/'.implementation/device_operating_20260922'
STOP=ROOT/'.implementation/local_szh_acceptance/STOP_AFTER_DATABASE_ERROR.json'
assert not STOP.exists()
assert json.loads((OUT/'demo_seed.json').read_text(encoding='utf-8'))['status']=='passed'
phase=sys.argv[1] if len(sys.argv)>1 else 'publish'
assert phase in {'publish','run'}
assert not (OUT/('demo_'+phase+'.json')).exists(), 'An attempt is already recorded; inspect before continuing'
target=json.loads(subprocess.run(['pwsh','-NoProfile','-File',str(ROOT/'scripts/verify_local_mysql.ps1')],capture_output=True,text=True,check=True,timeout=10).stdout)
from app.config import settings
from sqlalchemy.engine import make_url,Engine
from sqlalchemy import select,text,event
url=make_url(settings.DATABASE_URL)
assert url.host in {'127.0.0.1','localhost','::1'} and (url.port or 3306)==3306 and url.database=='company_agent'
from app.db import new_session
from app.models import Conversation,User,Workspace,WorkspaceMember,DataSource
from app.common.deps import AuthContext
from app.modules.analytics.sources import create_source
from app.modules.analytics.schemas import SourceInput
from app.modules.analytics.models import SourceMetadata,DataSourcePolicy
from app.modules.workflows.models import Workflow
from app.modules.workflows.schemas import WorkflowInput,FlowGraph,RunInput
from app.modules.workflows.service import save_workflow,change_status
from configurations.device_operations.workflow import build_graph,TABLES
started=time.monotonic();ledger={'phase':phase,'target':target,'status':'started','commands':0,'selects':0,'businessSelects':0,'plans':[],
 'budget':{'commands':500,'querySeconds':3,'concurrency':1,'totalSeconds':60,'businessSelects':1},'existingBindingsUnchanged':False}
@event.listens_for(Engine,'before_cursor_execute')
def guard(conn,cursor,statement,parameters,context,executemany):
    ledger['commands']+=1
    assert ledger['commands']<=500 and time.monotonic()-started<60,'Installation budget exhausted'
    assert conn.engine.url.host in {'127.0.0.1','localhost','::1'} and conn.engine.url.database in {'company_agent','szh_new'}
    if statement.lstrip().upper().startswith('SELECT'):
        ledger['selects']+=1
        if ' FROM ' in statement.upper() or '\nFROM ' in statement.upper():
            cursor.execute('SET SESSION MAX_EXECUTION_TIME=3000')
            cursor.execute('EXPLAIN '+statement,parameters)
            columns=[d[0] for d in cursor.description];plan=[dict(zip(columns,r)) if not isinstance(r,dict) else r for r in cursor.fetchall()]
            assert all((p.get('rows') or 0)<=10000 for p in plan),'Query plan exceeds row budget'
            if conn.engine.url.database=='szh_new':
                ledger['businessSelects']+=1
                assert ledger['businessSelects']<=1,'Only the reviewed bounded sample read may execute'
                ledger['plans'].append(plan)
try:
    with new_session() as db:
        db.execute(text('SET SESSION MAX_EXECUTION_TIME=3000'))
        assert str(db.scalar(text('SELECT @@hostname'))).casefold()==target['machine'].casefold()
        conversation=db.get(Conversation,'01M33HY4Y5N7X34QDBXXH33HAA');assert conversation
        member=db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id==conversation.workspace_id,WorkspaceMember.user_id==conversation.owner_user_id));assert member
        ctx=AuthContext(db,db.get(User,member.user_id),db.get(Workspace,member.workspace_id),member.role,'')
        rows=db.scalars(select(Workflow).where(Workflow.workspace_id==ctx.workspace_id)).all()
        before={w.id:json.dumps(w.graph,sort_keys=True,ensure_ascii=False) for w in rows}
        if phase=='publish':
            assert not db.scalar(select(DataSource.id).where(DataSource.workspace_id==ctx.workspace_id,DataSource.name=='设备运行演示数据（本机 szh_new）'))
            source=create_source(ctx,SourceInput(name='设备运行演示数据（本机 szh_new）',type='mysql',host='127.0.0.1',port=3306,databaseName='szh_new',username=settings.MYSQL_USER,password=settings.MYSQL_PASSWORD,enabled=False,
                allowedTables=TABLES,timeoutSeconds=3,maxRows=200,description='独立演示：6 台样例设备、144 条小时采样。支持运行/停机/故障/离线、开机时间、累计运行秒数、转速 rpm、温度 ℃。全部为标记演示数据，固定历史样例，不代表生产采集。'))
            row=db.get(DataSource,source.id)
            metadata=json.loads((OUT/'demo_metadata.json').read_text(encoding='utf-8'))
            row.status='active';row.connection_config={**row.connection_config,'metadataStatus':'ready','tableCount':2,'fieldCount':sum(len(t['columns']) for t in metadata),'semanticIndexStatus':'keyword_only','isDemo':True}
            db.add(SourceMetadata(workspace_id=ctx.workspace_id,owner_user_id=ctx.user_id,datasource_id=row.id,source_version=1,tables_json=metadata,status='ready',error_message=''))
            policy=db.scalar(select(DataSourcePolicy).where(DataSourcePolicy.datasource_id==row.id));assert policy
            policy.max_concurrency=1;policy.daily_query_limit=200;policy.health_check_enabled=False
            db.commit()
            graph=build_graph(row.id,1)
            saved=save_workflow(ctx,WorkflowInput(name='设备运行状态分析（演示数据）',description='读取独立演示设备 24 小时采样，分析状态、开机时长、转速、温度与缺失值；输出 24 项规则检查及可追溯报告。示例阈值不是生产标准，无外部模型依赖，不创建定时任务。',domains=['analysis','alert'],graph=FlowGraph.model_validate(graph)))
            saved=change_status(ctx,saved.id,saved.version,publish=True)
            ledger.update(datasourceId=row.id,workflowId=saved.id,version=saved.version,workspaceId=ctx.workspace_id,nodeCount=len(graph['nodes']))
            (OUT/'device_operating_workflow.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
        else:
            published=json.loads((OUT/'demo_publish.json').read_text(encoding='utf-8'));assert published['status']=='passed'
            source=db.get(DataSource,published['datasourceId']);assert source.connection_config['host']=='127.0.0.1' and source.connection_config['databaseName']=='szh_new' and set(source.connection_config['allowedTables'])==set(TABLES)
            from app.modules.workflows.execution import start
            result=start(ctx,published['workflowId'],RunInput(version=published['version'],mode='published',inputs={},idempotencyKey='device-operating-demo-20260922-v1',timeoutSeconds=30))
            ledger.update(workflowId=published['workflowId'],executionId=result.id,runId=result.runId if hasattr(result,'runId') else result.run_id,result=result.model_dump(mode='json'))
            assert result.status=='completed',result.status
        db.expire_all()
        after={w.id:json.dumps(w.graph,sort_keys=True,ensure_ascii=False) for w in db.scalars(select(Workflow).where(Workflow.id.in_(list(before)))).all()}
        assert before==after,'Existing workflow graph changed during installation'
        ledger.update(status='passed',existingBindingsUnchanged=True)
except Exception as exc:
    ledger.update(status='failed',errorType=type(exc).__name__,error=str(exc)[:200])
    # On connection/query anomalies no follow-up workload is permitted.
    if any(word in str(exc).lower() for word in ('timeout','timed out','超时','lost connection','server has gone')):
        STOP.write_text(json.dumps({'phase':phase,'errorType':type(exc).__name__}),encoding='utf-8')
    raise
finally:
    ledger['elapsedSeconds']=round(time.monotonic()-started,3)
    (OUT/('demo_'+phase+'.json')).write_text(json.dumps(ledger,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:v for k,v in ledger.items() if k not in {'target','plans','result','error'}},ensure_ascii=False))
