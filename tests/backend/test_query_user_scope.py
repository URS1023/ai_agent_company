"""The planner cannot replace the user's scope or choose another chat's history."""
import json

import pytest
from sqlalchemy import select

from app.models import AuthIdentity, Run
from app.errors import PermissionErrorEx
from app.modules.analytics.query_intent import query_context
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.analytics.models import QueryHistory
from app.modules.runtime import service as runtime
from app.providers import model
from app.providers.tool.gateway import ToolGateway, build_context
from test_analytics import source


def invoke(client, administrator, source, db, monkeypatch, current, previous=(), *, mode='natural_language'):
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    user = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == 'workspace-admin')).user_id
    for question in previous:
        message = runtime.create_message(db, workspace_id=administrator['X-Workspace-Id'],
            conversation_id=cid, role='user', content=question)
        old = runtime.create_run(db, workspace_id=administrator['X-Workspace-Id'], conversation_id=cid,
            user_id=user, context_snapshot={'user_request': question, 'datasource_ids': [source['id']],
                'user_message_id': message.id, 'user_message_sequence': message.sequence})
        old.status = 'failed'  # No successful QueryHistory exists for these user requests.
    message = runtime.create_message(db, workspace_id=administrator['X-Workspace-Id'],
        conversation_id=cid, role='user', content=current)
    run = runtime.create_run(db, workspace_id=administrator['X-Workspace-Id'], conversation_id=cid,
        user_id=user, context_snapshot={'user_request': current, 'datasource_ids': [source['id']],
            'user_message_id': message.id, 'user_message_sequence': message.sequence})
    db.commit()
    observed = []
    def respond(profile, messages, **kwargs):
        context = json.loads(messages[1]['content'])
        observed.append(context)
        intent = context.get('userIntent') or {}
        questions = [t['request'] for t in intent.get('previousUserTurns', [])]
        questions.append(context['question'])
        scoped = any('设备部门' in q for q in questions)
        sql = 'SELECT id, department, amount FROM sales'
        if scoped:
            sql += " WHERE department='设备'"
        sql += ' ORDER BY id'
        return {'content': json.dumps({'sql': sql, 'explanation': '沿用用户指定范围'}, ensure_ascii=False), 'usage': {}}
    monkeypatch.setattr(model, 'chat_for_run', respond)
    context = build_context(db=db, workspace_id=administrator['X-Workspace-Id'], user_id=user,
        run_id=run.id, selected_datasource_ids=[source['id']])
    context['conversation_id'] = cid
    result = ToolGateway(db).execute(administrator['X-Workspace-Id'], 'query_business_data', {
        'datasource_id': source['id'], 'query': '查询各部门金额',
        'session_id': 'model-chosen-unrelated-session', 'mode': mode,
    }, context)
    assert result.status == 'success', result.error_summary
    return result, observed[0], db.get(QueryHistory, result.data['queryId']), run


def test_planner_sql_mode_cannot_bypass_original_department_scope(client, administrator, source, db, monkeypatch):
    result, prompt, record, run = invoke(client, administrator, source, db, monkeypatch,
        '查询设备部门的金额明细', mode='sql')
    assert [r['id'] for r in result.data['data']] == [1, 3, 5]
    assert record.query_mode == 'natural_language'
    assert record.query_text == prompt['question'] == '查询设备部门的金额明细'
    assert record.session_id == run.conversation_id


def test_user_supplied_sql_is_preserved_and_cannot_use_planner_session(client, administrator, source, db, monkeypatch):
    from app.modules.runtime import service as runtime
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    user = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == 'workspace-admin')).user_id
    original = "SELECT id, department FROM sales WHERE department='设备' ORDER BY id"
    run = runtime.create_run(db, workspace_id=administrator['X-Workspace-Id'], conversation_id=cid,
        user_id=user, context_snapshot={'user_request': original, 'datasource_ids': [source['id']]})
    db.commit()
    monkeypatch.setattr(model, 'chat_for_run', lambda *a, **kw: pytest.fail('User SQL should execute without SQL generation'))
    context = build_context(db=db, workspace_id=run.workspace_id, user_id=user, run_id=run.id,
                            selected_datasource_ids=[source['id']])
    context['conversation_id'] = cid
    result = ToolGateway(db).execute(run.workspace_id, 'query_business_data', {
        'datasource_id': source['id'], 'query': 'SELECT * FROM sales', 'mode': 'sql',
        'session_id': 'unrelated-session',
    }, context)
    assert result.status == 'success', result.error_summary
    assert [r['id'] for r in result.data['data']] == [1, 3, 5]
    record = db.get(QueryHistory, result.data['queryId'])
    assert record.query_text == original and record.query_mode == 'sql' and record.session_id == cid


@pytest.mark.parametrize('first_mode, next_mode', [('sql', 'sql'), ('sql', 'natural_language'), ('natural_language', 'sql')])
def test_same_subtask_reuses_complete_same_turn_result_but_not_new_source_version(
    client, administrator, source, db, monkeypatch, first_mode, next_mode,
):
    import time
    from app.models import DataSource
    from app.modules.runtime.task_control import TaskControl, current
    control = TaskControl(deadline=time.monotonic()+60)
    token = current.set(control)
    try:
        first, prompt, record, run = invoke(client, administrator, source, db, monkeypatch,
            '查询设备部门的金额明细', mode=first_mode)
        assert first.metadata['user_scope_applied']
        context = build_context(db=db, workspace_id=run.workspace_id, user_id=run.started_by_user_id,
            run_id=run.id, selected_datasource_ids=[source['id']])
        context['conversation_id'] = run.conversation_id
        monkeypatch.setattr(model, 'chat_for_run', lambda *a, **kw: pytest.fail('Same turn result must not regenerate SQL'))
        second = ToolGateway(db).execute(run.workspace_id, 'query_business_data', {
            'datasource_id': source['id'], 'query': '查询各部门金额', 'mode': next_mode, 'max_rows': 10,
        }, context)
        assert second.status == 'success' and second.metadata['same_turn_result_reused']
        assert second.data['queryId'] == first.data['queryId']
        assert db.scalars(select(QueryHistory)).all() == [record]
        second.data['data'].clear()
        assert next(iter(control.query_receipts.values()))['data']  # Returned copy cannot mutate cached evidence.
        calls = []
        def fresh(*a, **kw):
            calls.append(True)
            return {'content': '{"sql":"SELECT id,department,amount FROM sales WHERE department=\'设备\' ORDER BY id"}', 'usage': {}}
        monkeypatch.setattr(model, 'chat_for_run', fresh)
        row = db.get(DataSource, source['id'])
        row.connection_config = {**row.connection_config, 'version': 2}
        db.commit()
        third = ToolGateway(db).execute(run.workspace_id, 'query_business_data', {
            'datasource_id': source['id'], 'query': '查询各部门金额', 'mode': 'sql',
        }, context)
        assert third.status == 'success' and calls and third.data['queryId'] != first.data['queryId']
        assert not third.metadata.get('same_turn_result_reused')
    finally:
        current.reset(token)


@pytest.mark.parametrize('current, previous', [
    ('查询设备部门的金额明细', ()),
    ('这些金额对吗', ('查询设备部门的金额明细',)),
    ('你还是没查', ('查询设备部门的金额明细', '这些金额对吗', '你还没有查')),
])
def test_real_query_preserves_original_and_unexecuted_followup_scope(
    client, administrator, source, db, monkeypatch, current, previous,
):
    result, prompt, record, run = invoke(client, administrator, source, db, monkeypatch, current, previous)
    assert {r['department'] for r in result.data['data']} == {'设备'}
    assert [r['id'] for r in result.data['data']] == [1, 3, 5]
    assert prompt['question'] == current
    assert record.query_text == current
    assert record.session_id == run.conversation_id
    assert [t['request'] for t in prompt['userIntent']['previousUserTurns']] == list(previous)
    assert result.data['modelUsage']['userScope']['runId'] == run.id


def test_explicit_new_question_does_not_inherit_old_department(
    client, administrator, source, db, monkeypatch,
):
    result, prompt, record, run = invoke(client, administrator, source, db, monkeypatch,
        '查询全部部门的金额明细', ('查询设备部门的金额明细',))
    assert len(result.data['data']) == 5
    assert prompt['question'] == '查询全部部门的金额明细'
    assert prompt['userIntent']['previousUserTurns'] == []


def test_scope_stops_at_source_change_and_rejects_foreign_conversation(
    client, administrator, source, db, monkeypatch,
):
    result, prompt, record, run = invoke(client, administrator, source, db, monkeypatch,
        '这些金额对吗', ('查询设备部门的金额明细',))
    ctx = auth_context_from_tool({'db': db, 'workspace_id': run.workspace_id,
                                 'user_id': run.started_by_user_id})
    execution = {'run_id': run.id, 'conversation_id': run.conversation_id}
    old = db.get(Run, prompt['userIntent']['previousUserTurns'][0]['runId'])
    old.context_snapshot = {**old.context_snapshot, 'datasource_ids': ['other-source']}
    db.commit()
    assert query_context(ctx, execution, source['id'], 'task')['previousUserTurns'] == []
    with pytest.raises(PermissionErrorEx):
        query_context(ctx, {**execution, 'conversation_id': 'other-conversation'}, source['id'], 'task')
    run.started_by_user_id = 'another-user'
    db.flush()
    with pytest.raises(PermissionErrorEx):
        query_context(ctx, execution, source['id'], 'task')


def test_future_turn_with_same_timestamp_is_not_used(
    client, administrator, source, db, monkeypatch,
):
    result, prompt, record, run = invoke(client, administrator, source, db, monkeypatch,
        '这些金额对吗', ('查询设备部门的金额明细',))
    future = runtime.create_run(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
        user_id=run.started_by_user_id, context_snapshot={'user_request': '查询财务部门',
            'datasource_ids': [source['id']], 'user_message_sequence': 999})
    future.created_at = run.created_at
    old = db.get(Run, prompt['userIntent']['previousUserTurns'][0]['runId'])
    old.created_at = run.created_at  # Persisted sequence also disambiguates same-second earlier turns.
    db.commit()
    ctx = auth_context_from_tool({'db': db, 'workspace_id': run.workspace_id,
                                 'user_id': run.started_by_user_id})
    intent = query_context(ctx, {'run_id': run.id, 'conversation_id': run.conversation_id}, source['id'], 'task')
    assert [t['request'] for t in intent['previousUserTurns']] == ['查询设备部门的金额明细']


def test_different_natural_language_subtasks_do_not_share_an_aggregate_receipt(client, administrator, source, db, monkeypatch):
    import time
    from app.modules.runtime.task_control import TaskControl, current
    control=TaskControl(deadline=time.monotonic()+60);token=current.set(control)
    try:
        first, prompt, record, run=invoke(client,administrator,source,db,monkeypatch,'查询设备部门金额并列出明细')
        context=build_context(db=db,workspace_id=run.workspace_id,user_id=run.started_by_user_id,run_id=run.id,selected_datasource_ids=[source['id']])
        context['conversation_id']=run.conversation_id
        calls=[]
        def respond(profile,messages,**kwargs):
            calls.append(json.loads(messages[1]['content']))
            return {'content':json.dumps({'sql':"SELECT COUNT(*) AS count FROM sales WHERE department='设备'"})}
        monkeypatch.setattr(model,'chat_for_run',respond)
        second=ToolGateway(db).execute(run.workspace_id,'query_business_data',{'datasource_id':source['id'],'query':'统计设备部门记录数','mode':'natural_language'},context)
        assert second.status=='success'
        assert second.data['queryId']!=first.data['queryId'] and calls
        assert calls[0]['userIntent']['plannerTask']=='统计设备部门记录数'
        assert calls[0]['executionTask']=='统计设备部门记录数'
        assert calls[0]['question']=='查询设备部门金额并列出明细'
        assert not second.metadata.get('same_turn_result_reused')
    finally:current.reset(token)


def test_redirected_sql_for_new_subtask_does_not_return_previous_rows(client, administrator, source, db, monkeypatch):
    """同一轮查明细后再查计数，不能因 SQL 模式重定向而返回上一份明细。"""
    import time
    from app.modules.runtime.task_control import TaskControl, current
    control = TaskControl(deadline=time.monotonic() + 60)
    token = current.set(control)
    try:
        first, _, _, run = invoke(client, administrator, source, db, monkeypatch,
                                  '查询设备部门金额明细和记录数')
        context = build_context(db=db, workspace_id=run.workspace_id, user_id=run.started_by_user_id,
                                run_id=run.id, selected_datasource_ids=[source['id']])
        context['conversation_id'] = run.conversation_id
        observed = []
        def respond(profile, messages, **kwargs):
            observed.append(json.loads(messages[1]['content']))
            return {'content': json.dumps({'sql': "SELECT COUNT(*) AS count FROM sales WHERE department='设备'"})}
        monkeypatch.setattr(model, 'chat_for_run', respond)
        second = ToolGateway(db).execute(run.workspace_id, 'query_business_data', {
            'datasource_id': source['id'], 'query': 'SELECT COUNT(*) AS count FROM sales', 'mode': 'sql',
        }, context)
        assert second.status == 'success', second.error_summary
        assert second.data['queryId'] != first.data['queryId']
        assert second.data['data'] == [{'count': 3}]
        assert not second.metadata.get('same_turn_result_reused')
        assert observed[0]['question'] == '查询设备部门金额明细和记录数'
        assert observed[0]['executionTask'] == 'SELECT COUNT(*) AS count FROM sales'
    finally:
        current.reset(token)


def test_subtask_recall_keeps_user_named_tables_without_delivery_instructions(client, administrator, source, db, monkeypatch):
    """子任务控制召回主题，同时保留用户明确点名的授权表。"""
    from app.modules.analytics import queries
    original = queries.recall_tables
    recalled_questions = []
    def recall(ctx, datasource, tables, question, **kwargs):
        recalled_questions.append(question)
        return original(ctx, datasource, tables, question, **kwargs)
    monkeypatch.setattr(queries, 'recall_tables', recall)
    _, prompt, _, _ = invoke(client, administrator, source, db, monkeypatch,
                             '请查询 sales 表设备部门金额，另做一份 ERP 介绍 PPT')
    assert 'sales' in recalled_questions[0]
    assert '查询各部门金额' in recalled_questions[0]
    assert 'ERP' not in recalled_questions[0]
    assert '设备部门' in prompt['userIntent']['currentRequest']


def test_followup_context_sorts_only_run_ids_not_large_payloads(client, administrator, source, db, monkeypatch):
    """追问读取先排序轻量主键，避免把 Run 快照 JSON 放进 MySQL filesort。"""
    from sqlalchemy import event
    _, _, _, run = invoke(client, administrator, source, db, monkeypatch,
                          '这些金额对吗', ('查询设备部门的金额明细',))
    statements = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        ctx = auth_context_from_tool({'db': db, 'workspace_id': run.workspace_id, 'user_id': run.started_by_user_id})
        intent = query_context(ctx, {'run_id': run.id, 'conversation_id': run.conversation_id}, source['id'], '核对明细')
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert intent['previousUserTurns']
    sorted_runs = [statement for statement in statements if 'from runs' in statement and 'order by' in statement]
    assert sorted_runs and all('context_snapshot' not in statement and statement.lstrip().startswith('select runs.id') for statement in sorted_runs)
