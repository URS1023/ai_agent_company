"""Large row batches retain node history without quadratic network writes."""
from sqlalchemy import event, select

from app.db import get_engine
from app.models import RunStep
from app.modules.workflows.models import WorkflowExecution
from test_workflows import create_flow, edge, execute, node, result


def test_intermediate_json_updates_preserve_prior_nodes_and_final_outputs(client, administrator, db):
    flow = create_flow(client, administrator, {
        'nodes': [node('start', 'start'),
                  node('first', 'calculate-values', {'calculations': [{'name': 'value1', 'operation': 'copy', 'left': {'source': 'constant', 'value': 12}}]}),
                  node('second', 'calculate-values', {'calculations': [{'name': 'value2', 'operation': 'multiply', 'left': {'source': 'field', 'field': 'value1'}, 'right': {'source': 'constant', 'value': 2}}]}),
                  node('end', 'end')],
        'edges': [edge('start', 'first'), edge('first', 'second'), edge('second', 'end')]})
    writes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if 'json_set(' in statement.lower():
            writes.append(parameters)
    engine = get_engine()
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        run = result(execute(client, administrator, flow, {'data': [{'evidence': 'x'*100000}]}))
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert run['status'] == 'completed', run
    # Live progress now persists running checkpoints as well as completed
    # nodes. Verify exactly one of each, with no quadratic history rewrite.
    import json
    checkpoints = [json.loads(params[1]) for params in writes]
    assert [(item['nodeId'], item['status']) for item in checkpoints] == [
        ('start', 'completed'), ('first', 'running'), ('first', 'completed'),
        ('second', 'running'), ('second', 'completed'), ('end', 'running'), ('end', 'completed'),
    ]
    # Each bound JSON value is one node or end output, never the whole history.
    assert max(len(p) for params in writes for p in params if isinstance(p, str)) < 110000
    saved = db.scalar(select(WorkflowExecution).where(WorkflowExecution.id == run['id']))
    assert saved.result_json == run['result']
    states = saved.result_json['nodes']
    assert states['first']['output']['data'][0]['value1'] == 12
    assert 'value2' not in states['first']['output']['data'][0]
    assert states['second']['output']['data'][0]['value2'] == '24'
    assert saved.result_json['outputs']['end']['data'][0]['evidence'] == 'x'*100000


def test_local_transform_failure_retains_a_durable_failed_step(client, administrator, db):
    flow = create_flow(client, administrator, {
        'nodes': [node('start', 'start'), node('sum', 'aggregator', {
            'metrics': [{'name': 'total', 'operation': 'sum', 'field': 'amount'}]}), node('end', 'end')],
        'edges': [edge('start', 'sum'), edge('sum', 'end')]})
    run = result(execute(client, administrator, flow, {'data': [{'amount': 'invalid number'}]}), 502)
    assert run['status'] == 'failed'
    assert run['result']['nodes']['sum']['status'] == 'failed'
    assert run['result']['nodes']['end']['status'] == 'skipped'
    steps = db.scalars(select(RunStep).where(RunStep.run_id == run['runId'])).all()
    assert [step.status for step in steps if step.name == 'sum'] == ['failed']
