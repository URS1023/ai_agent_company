"""Persisted local SQLite contracts for incremental windows and shared report reads."""
from sqlalchemy import select
from app.modules.workflows.models import WorkflowBusinessState
from test_workflows import API, create_flow, graph, node, result, execute
from test_analytics import source


def window_graph(source):
    value = graph(node('read', 'database-query', {'datasourceId': source['id'],
        'query': 'SELECT id, :windowStart AS periodStart, :windowEnd AS periodEnd FROM sales WHERE id > :afterKey ORDER BY id LIMIT 2',
        'windowKey': 'id', 'maxRows': 2}))
    value['nodes'][0]['config'] = {'incrementalWindow': True, 'lookbackMinutes': 30}
    return value


def test_window_failure_keeps_position_and_success_pages_without_gaps(client, administrator, source, db, monkeypatch):
    from app.modules.workflows import engine
    flow = create_flow(client, administrator, window_graph(source))
    real = engine.execute_node
    def fail_end(ctx, n, *args, **kwargs):
        if n.kind == 'end':
            raise ValueError('test downstream failure')
        return real(ctx, n, *args, **kwargs)
    monkeypatch.setattr(engine, 'execute_node', fail_end)
    assert result(execute(client, administrator, flow), 502)['status'] == 'failed'
    assert not db.scalars(select(WorkflowBusinessState)).all()
    monkeypatch.setattr(engine, 'execute_node', real)
    first = result(execute(client, administrator, flow))
    second = result(execute(client, administrator, flow))
    third = result(execute(client, administrator, flow))
    assert [[r['id'] for r in run['result']['outputs']['end']['data']] for run in (first, second, third)] == [[1, 2], [3, 4], [5]]
    windows = [r['result']['collectionWindow'] for r in (first, second, third)]
    assert windows[0]['start'] == windows[1]['start'] == windows[2]['start']
    assert windows[0]['end'] == windows[1]['end'] == windows[2]['end']
    assert windows[0]['hasMore'] and not windows[2]['hasMore']
    db.expire_all()
    state = db.scalar(select(WorkflowBusinessState).where(WorkflowBusinessState.state_key == 'collection-window'))
    assert state.value['watermark'] == windows[2]['end'] and not state.value['pending']
    fourth = result(execute(client, administrator, flow))
    assert fourth['result']['collectionWindow']['start'] == windows[2]['end']


def test_manual_period_never_advances_automatic_checkpoint(client, administrator, source, db):
    flow = create_flow(client, administrator, window_graph(source))
    inputs = {'windowStart': '2025-01-01T00:00', 'windowEnd': '2025-02-01T00:00', 'windowAfterKey': 2}
    run = result(execute(client, administrator, flow, inputs))
    assert run['result']['collectionWindow']['mode'] == 'manual'
    assert [r['id'] for r in run['result']['outputs']['end']['data']] == [3, 4]
    assert not db.scalars(select(WorkflowBusinessState)).all()


def test_invalid_period_is_a_failed_run_not_a_stuck_running_task(client, administrator, source, db):
    flow = create_flow(client, administrator, window_graph(source))
    run = result(execute(client, administrator, flow, {'windowStart': '2025-02-01', 'windowEnd': '2025-01-01'}), 502)
    assert run['status'] == 'failed' and run['result']['nodes']['start']['status'] == 'failed'
    assert not db.scalars(select(WorkflowBusinessState)).all()


def test_business_pages_read_same_saved_report_with_time_and_reference_filters(client, administrator):
    flow = create_flow(client, administrator, graph(node('report', 'business-report', {'kind':'quality','title':'石灰石质检','aiAnalysis':False})))
    row = {'质检单号':'SHS-055','检验项目':'二氧化硅','标准原文':'≤1%','测量值':2,'结果':'不合格','说明':'超出上限'}
    run = result(execute(client, administrator, flow, {'data':[row]}))
    listed = result(client.get(API+'/business-results', headers=administrator, params={'kind':'quality'}))
    assert listed['items'][0]['id'] == run['id'] and listed['items'][0]['summary']['issues'] == 1
    assert not listed['hasMore'] and 'data' not in listed['items'][0]
    detail = result(client.get(API+'/business-results/'+run['id'], headers=administrator, params={'kind':'quality'}))
    assert detail['data'][0]['实测值'] == '2' and '# 石灰石质检' in detail['content']
    by_id = result(client.get(API+'/business-results', headers=administrator, params={'reference':run['runId']}))
    assert by_id['items'][0]['id'] == run['id']
    empty = result(client.get(API+'/business-results', headers=administrator, params={'end':'2020-01-01T00:00:00Z'}))
    assert not empty['items']
    assert client.get(API+'/business-results/'+run['id']).status_code == 401


def test_period_without_updates_is_not_missing_data_or_a_false_pass(client, administrator):
    value = graph(node('report', 'business-report', {'kind':'quality','title':'质检','aiAnalysis':False}))
    value['nodes'][0]['config'] = {'incrementalWindow':True}
    run = result(execute(client, administrator, create_flow(client, administrator, value), {'data':[]}))
    report = run['result']['outputs']['end']
    assert report['outcome'] == 'no_changes' and '没有新增或更新' in report['content']


def test_chat_internal_identifier_reads_saved_quality_result_without_external_query(client, administrator, db, monkeypatch):
    import json
    from app.modules.runtime import agent
    from app.models import ToolCall
    from app.modules.analytics import queries
    flow = create_flow(client, administrator, graph(node('report', 'business-report', {'kind':'quality','title':'质检报告','aiAnalysis':False})))
    run = result(execute(client, administrator, flow, {'data':[{'质检单号':'SHS-055','检验项目':'二氧化硅','标准原文':'≤1%','测量值':2,'结果':'不合格','说明':'超出上限'}]}))
    def forbidden(*args, **kwargs):
        raise AssertionError('Saved report lookup must not execute an external business query')
    monkeypatch.setattr(queries, 'execute_query', forbidden)
    turns = []
    def completion(db, chat_run, messages, *args, **kwargs):
        turns.append(messages)
        if len(turns) == 1:
            yield 'final', {'content': '', 'tool_calls': [{'id': 'saved-report', 'type': 'function',
                'function': {'name': 'query_workflow_results', 'arguments': json.dumps({
                    'kind': 'quality', 'reference': run['id'], 'limit': 5})}}]}
        else:
            evidence = next(message['content'] for message in reversed(messages) if message['role'] == 'tool')
            assert 'SHS-055' in evidence and '不合格' in evidence
            yield 'final', {'content': '已读取保存的质检报告：SHS-055 不合格，二氧化硅超出上限。', 'tool_calls': []}
    monkeypatch.setattr(agent, '_llm_stream', completion)
    monkeypatch.setattr(agent, '_generate_followups', lambda *args, **kwargs: (['查看其他质检报告','查看待确认项目','查看不合格项目'], ''))
    conversation = client.post('/api/chat/conversations', headers=administrator, json={'title':'质检记录联通'}).json()
    sent = client.post('/api/chat/conversations/'+conversation['id']+'/send', headers=administrator,
        json={'content':run['id']+' 帮我查看质检的数据'})
    assert sent.status_code == 200 and 'SHS-055' in sent.text, sent.text
    assert 'run.completed' in sent.text and 'run.failed' not in sent.text
    assert len(turns) == 2
    assert [call.tool_name for call in db.scalars(select(ToolCall)).all()] == ['query_workflow_results']
