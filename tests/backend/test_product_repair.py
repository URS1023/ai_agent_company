"""HTTP and persisted SQLite checks, no business database or external model."""
import json
from uuid import uuid4
from sqlalchemy import select
from app.modules.system_tools.models import InboxMessage
from app.modules.workflows.models import WorkflowBusinessState
from test_workflows import API, create_flow, graph, node, result, execute
from test_analytics import source


def test_report_notification_deduplicates_across_batches_and_only_observed_recovery(client, administrator, db):
    flow = create_flow(client,administrator,graph(
        node('report','business-report',{'kind':'quality','title':'质检报告','aiAnalysis':False}),
        node('notice','business-notify',{'audience':'executor'})))
    def run(doc, verdict):
        data = [{'质检单号':doc,'检验项目':'二氧化硅','标准原文':'≤1.0%',
                 '测量值':'2' if verdict=='不合格' else '.4','结果':verdict,'说明':'已判定'}] if doc else []
        completed = result(execute(client,administrator,flow,{'data':data}))
        assert completed['status'] == 'completed', completed
        return completed['result']['outputs']['end']
    assert run('A','不合格')['notification']['sent']
    assert run('B','不合格')['notification']['sent']
    assert not run('A','不合格')['notification']['sent']
    assert not run(None,'')['notification']['recovery']
    assert run('A','合格')['notification']['recovery']
    assert len(db.scalars(select(InboxMessage)).all()) == 4


def test_child_wait_resumes_parent_automatically(client, administrator):
    child = create_flow(client,administrator,graph(node('ask','question',{'prompt':'确认继续','answerKey':'answer'})))
    child = result(client.post(API+'/'+child['id']+'/publish',headers=administrator,json={'version':child['version']}))
    parent = create_flow(client,administrator,graph(node('child','subflow',{'workflowId':child['id'],'workflowVersion':child['version']})))
    first = result(execute(client,administrator,parent,{}),202)
    assert first['status'] == 'waiting', first
    child_id = first['result']['waiting']['childExecutionId']
    child_run = result(client.get(API+'/'+child['id']+'/runs/'+child_id,headers=administrator))
    continued = result(client.post(API+'/'+child['id']+'/runs/'+child_id+'/resume',headers=administrator,
        json={'version':child_run['version'],'input':'确认','idempotencyKey':uuid4().hex}))
    assert continued['status']=='completed',continued
    final = result(client.get(API+'/'+parent['id']+'/runs/'+first['id'],headers=administrator))
    assert final['status']=='completed',final


def test_chat_journal_replays_without_running_model_again(client, administrator, monkeypatch):
    from app.modules.runtime import agent
    from app.modules.runtime.service import finalize_run, create_message
    calls=[]
    def fake(db, run, workspace, user, **kw):
        calls.append(run.id)
        yield {'type':'message.delta','payload':{'delta':'**已保存的回答**'}}
        create_message(db,workspace_id=run.workspace_id,conversation_id=run.conversation_id,
            role='assistant',content='**已保存的回答**',parts=[{'type':'text','text':'**已保存的回答**'}])
        finalize_run(db,run,status='completed'); db.commit()
        yield {'type':'run.completed','payload':{}}
    monkeypatch.setattr(agent,'agent_run',fake)
    conversation=client.post('/api/chat/conversations',headers=administrator,json={'title':'断线恢复测试'}).json()
    sent=client.post('/api/chat/conversations/'+conversation['id']+'/send',headers=administrator,json={'content':'测试'})
    assert sent.status_code==200 and '**已保存的回答**' in sent.text, sent.text
    created=next(json.loads(line[6:]) for line in sent.text.splitlines() if line.startswith('data: ') and 'conversation_id' in line)
    replay=client.get('/api/chat/runs/'+created['run_id']+'/events',headers=administrator)
    assert '**已保存的回答**' in replay.text and len(calls)==1


def test_scan_position_survives_failed_downstream_and_advances_after_success(client, administrator, source, db, monkeypatch):
    from app.modules.workflows import engine
    conf={'datasourceId':source['id'],'query':'SELECT id FROM sales WHERE id > :afterKey ORDER BY id LIMIT 2','maxRows':2,'scanKey':'id'}
    flow=create_flow(client,administrator,graph(node('scan','database-query',conf)))
    real=engine.execute_node
    def fail_end(ctx,n,*args,**kwargs):
        if n.kind=='end': raise ValueError('test downstream failure')
        return real(ctx,n,*args,**kwargs)
    monkeypatch.setattr(engine,'execute_node',fail_end)
    first=result(execute(client,administrator,flow,{}),502)
    assert first['status']=='failed'
    assert db.scalars(select(WorkflowBusinessState)).all()==[]
    monkeypatch.setattr(engine,'execute_node',real)
    second=result(execute(client,administrator,flow,{}))
    assert [r['id'] for r in second['result']['outputs']['end']['data']]==[1,2]
    third=result(execute(client,administrator,flow,{}))
    assert [r['id'] for r in third['result']['outputs']['end']['data']]==[3,4]
    db.expire_all()
    state=db.scalar(select(WorkflowBusinessState).where(WorkflowBusinessState.state_key=='scan:scan'))
    assert state.value['afterKey']==4


def test_stop_button_cancels_server_job_before_next_operation(client, administrator, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.modules.runtime import agent
    from app.modules.runtime.task_control import checkpoint, ChatCancelled
    from app.modules.runtime.service import finalize_run
    ready=Event()
    run_ids=[]
    operations=[]
    def fake(db,run,workspace,user,**kwargs):
        run_ids.append(run.id)
        ready.set()
        try:
            for _ in range(300):
                Event().wait(.01)
                checkpoint()
            operations.append('unexpected next operation')
        except ChatCancelled:
            finalize_run(db,run,status='cancelled'); db.commit()
            yield {'type':'run.cancelled','payload':{'message':'已停止'}}
    monkeypatch.setattr(agent,'agent_run',fake)
    conv=client.post('/api/chat/conversations',headers=administrator,json={'title':'停止验证'}).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(client.post,'/api/chat/conversations/'+conv['id']+'/send',headers=administrator,json={'content':'测试停止'})
        assert ready.wait(5)
        stopped=client.post('/api/chat/runs/'+run_ids[0]+'/cancel',headers=administrator)
        assert stopped.status_code==200 and stopped.json()['status']=='stopping'
        sent=future.result(timeout=5)
    assert 'run.cancelled' in sent.text and not operations
