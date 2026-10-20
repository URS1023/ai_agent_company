"""新临时SQLite与纯图替身验证平台接线；不连接在用服务、业务源或模型。"""

from copy import deepcopy
import hashlib
import io
import json
import os
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import AuditEvent, Notification, Task, TaskEvent
from agent_platform.core.tasks import TaskService
from agent_platform.modules.flows.graph_runtime import GRAPH_EVENT, normalize_definition, worker_module
from agent_platform.modules.flows.models import Workflow, WorkflowRun
from agent_platform.modules.flows.service import WorkflowService
from agent_platform.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace


TABLES = [item.__table__ for item in (Workflow, WorkflowRun, KnowledgeSpace, KnowledgeDocument,
    KnowledgeChunk, Task, TaskEvent, AuditEvent, Notification)]


def definition(fields=None):
    """真实Graphon Start/End格式；不模拟自写节点执行循环。"""
    return {'format': 'graph', 'engine': 'graphon', 'engine_version': '0.8.0', 'root_node_id': 'start',
        'graph': {'nodes': [{'id': 'start', 'data': {'type': 'start', 'title': '开始', 'variables': fields or []}},
            {'id': 'end', 'data': {'type': 'end', 'title': '结束',
                'outputs': [{'variable': 'result', 'value_selector': ['start', 'message']}]}}],
            'edges': [{'id': 'start-end', 'source': 'start', 'target': 'end', 'sourceHandle': 'source'}]},
        'variables': [], 'limits': {'max_steps': 100}}


def form_field(variable='message', kind='text-input', **extra):
    return {'variable': variable, 'label': variable, 'type': kind, **extra}


@pytest.fixture
def context(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'owned-graph.sqlite3').as_posix())
    Base.metadata.create_all(engine, tables=TABLES)
    actor = Actor('user-a', 'workspace-a', frozenset({'m04.*'}))
    with Session(engine, expire_on_commit=False) as session:
        yield session, actor, engine
    engine.dispose()


def create(context, value=None):
    session, actor, _ = context
    result = WorkflowService(session, actor).create('图流程', '', value or definition())
    session.commit()
    return result['id']


def frame(run_id, sequence, event_type='NodeRunSucceededEvent', **data):
    return {'protocol_version': 1, 'run_id': run_id, 'sequence': sequence, 'kind': 'event',
        'event_type': event_type, 'data': {'node_id': 'start', 'node_execution_id': 'execution-1', **data}}


def terminal(run_id, sequence=4, outputs=None, status='succeeded'):
    return {'protocol_version': 1, 'run_id': run_id, 'sequence': sequence, 'kind': 'result',
        'engine': 'graphon', 'engine_version': '0.8.0', 'status': status,
        'outputs': outputs if outputs is not None else {'result': 42}}


def fake_worker(monkeypatch, callback=None, outputs=None, failure=None):
    calls = []
    worker = worker_module()
    def execute(request, *, timeout_seconds, on_event):
        calls.append(deepcopy(request))
        assert 0 < timeout_seconds <= 90
        event = frame(request['run_id'], 2, value=42)
        on_event(event)
        if callback:
            callback(request)
        if failure:
            raise failure
        return {'result': terminal(request['run_id'], outputs=outputs), 'events': [event],
            'process': {'client_pid': 123, 'stopped': True, 'exit_code': 0},
            'runtime': {'kind': 'owned-wsl', 'runtime_id': 'a' * 32, 'boot_id': 'b' * 64}}
    monkeypatch.setattr(worker, 'run_wsl', execute)
    return calls


def test_required_form_can_be_saved_then_inputs_are_validated_before_reservation(context, monkeypatch):
    workflow_id = create(context, definition([form_field(required=True)]))
    calls = fake_worker(monkeypatch)
    session, actor, _ = context
    with pytest.raises(DomainError) as missing:
        WorkflowService(session, actor).run(workflow_id, inputs={})
    assert missing.value.fields == {'inputs.message': '请填写message'}
    assert session.scalar(select(WorkflowRun)) is None and not calls
    result = WorkflowService(session, actor).run(workflow_id, inputs={'message': '点检'})
    assert result['status'] == 'succeeded' and result['format'] == 'graph'


def test_graph_reserves_and_commits_each_event_before_worker_returns(context, monkeypatch):
    session, actor, engine = context
    workflow_id = create(context)
    def observe(request):
        assert not session.in_transaction()
        with Session(engine) as observer:
            run = observer.scalar(select(WorkflowRun))
            assert run.status == 'running' and run.definition_snapshot['format'] == 'graph'
            assert observer.get(Task, run.task_id).status == 'running'
            event = observer.scalar(select(TaskEvent).where(TaskEvent.type == GRAPH_EVENT))
            assert event.data['run_id'] == run.id and event.data['data']['value'] == 42
            assert event.data['data']['node_execution_id'] == 'execution-1'
            assert run.steps[0]['event_count'] == 1 and run.steps[0]['completed_nodes'] == 1
    outputs = {'reading': 42, 'passed': False, 'values': [0, True, {'name': '甲'}]}
    calls = fake_worker(monkeypatch, callback=observe, outputs=outputs)
    service = WorkflowService(session, actor)
    result = service.run(workflow_id, inputs={}, idempotency_key='graph-intent')
    assert result['outputs'] == outputs and json.loads(result['output']) == outputs
    assert result['graph_progress']['completed_nodes'] == 1
    assert result['steps'][0]['process']['stopped'] is True
    assert result['started_at'].endswith('+00:00')
    assert TaskService(session, actor).get(result['task_id'])['status'] == 'succeeded'
    assert service.run(workflow_id, inputs={}, idempotency_key='graph-intent')['id'] == result['id']
    assert len(calls) == 1


def test_graph_hash_freezes_definition_and_rejects_reused_key_after_edit(context, monkeypatch):
    session, actor, _ = context
    workflow_id = create(context)
    calls = fake_worker(monkeypatch)
    service = WorkflowService(session, actor)
    first = service.run(workflow_id, inputs={}, idempotency_key='fixed')
    changed = definition()
    changed['variables'] = [{'selector': ['conversation', 'counter'], 'value': 0}]
    service.update(workflow_id, '改图', '', changed, expected_revision=1)
    session.commit()
    with pytest.raises(DomainError) as conflict:
        service.run(workflow_id, inputs={}, idempotency_key='fixed')
    assert conflict.value.code == 'idempotency_conflict' and conflict.value.fields['run_id'] == first['id']
    assert service.run_detail(first['id'])['definition_snapshot']['variables'] == [] and len(calls) == 1


def test_graph_edit_requires_revision_and_sql_compare_and_swap_preserves_other_save(context):
    session, actor, engine = context
    workflow_id = create(context)
    service = WorkflowService(session, actor)
    with pytest.raises(DomainError) as missing:
        service.update(workflow_id, '未带版本', '', definition())
    assert missing.value.code == 'workflow_revision_required'
    service.get(workflow_id)
    with Session(engine) as other:
        WorkflowService(other, actor).update(workflow_id, '其他人已保存', '', definition(), expected_revision=1)
        other.commit()
    with pytest.raises(DomainError) as conflict:
        service.update(workflow_id, '过期覆盖', '', definition(), expected_revision=1)
    assert conflict.value.code == 'revision_conflict'
    session.rollback()
    assert service.detail(workflow_id)['name'] == '其他人已保存'


@pytest.mark.parametrize('kind', ['llm', 'tool', 'code', 'http-request', 'knowledge-retrieval', 'template-transform'])
def test_unadapted_nodes_are_rejected_when_saving(context, kind):
    value = definition()
    value['graph']['nodes'][1]['data']['type'] = kind
    session, actor, _ = context
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, actor).create('不支持', '', value)
    assert denied.value.code == 'workflow_node_not_supported'
    assert session.scalar(select(Workflow)) is None


def test_form_preserves_zero_false_and_nested_arrays_applies_only_missing_defaults(context, monkeypatch):
    session, actor, _ = context
    fields = [form_field('count', 'number', default=10), form_field('enabled', 'checkbox', default=True),
        form_field('data', 'json_object', json_schema={'type': 'object', 'properties': {'items': {'type': 'array'}}}),
        form_field('title', default='默认')]
    workflow_id = create(context, definition(fields))
    calls = fake_worker(monkeypatch)
    values = {'count': 0, 'enabled': False, 'data': {'items': []}}
    result = WorkflowService(session, actor).run(workflow_id, inputs=values)
    assert result['inputs'] == {**values, 'title': '默认'}
    assert calls[0]['inputs'] == result['inputs']


@pytest.mark.parametrize('values', [{'count': True}, {'mode': '不存在'}, {'extra': '隐藏字段'}, {'data': []}])
def test_form_rejects_wrong_types_options_and_unknown_fields_before_dispatch(context, values, monkeypatch):
    workflow_id = create(context, definition([form_field('count', 'number'),
        form_field('mode', 'select', options=['a', 'b']), form_field('data', 'json_object')]))
    calls = fake_worker(monkeypatch)
    session, actor, _ = context
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, actor).run(workflow_id, inputs=values)
    assert denied.value.code == 'workflow_inputs_invalid' and not calls


def test_schema_local_reference_is_checked_without_external_resource_lookup(context, monkeypatch):
    session, actor, _ = context
    value = definition([form_field('data', 'json_object', json_schema={'type': 'object',
        '$defs': {'count': {'type': 'number'}}, 'properties': {'count': {'$ref': '#/$defs/count'}}})])
    workflow_id = create(context, value)
    fake_worker(monkeypatch)
    service = WorkflowService(session, actor)
    assert service.run(workflow_id, inputs={'data': {'count': 0}})['inputs']['data']['count'] == 0
    with pytest.raises(DomainError) as wrong:
        service.run(workflow_id, inputs={'data': {'count': '文本'}})
    assert wrong.value.fields == {'inputs.data': '请检查data'}
    value['graph']['nodes'][0]['data']['variables'][0]['json_schema']['properties']['count']['$ref'] = '#/missing'
    service.update(workflow_id, '不存在的引用', '', value, expected_revision=1)
    session.commit()
    with pytest.raises(DomainError) as unresolved:
        service.run(workflow_id, inputs={'data': {'count': 42}})
    assert unresolved.value.code == 'workflow_inputs_invalid'


def test_initial_variables_cannot_override_validated_start_inputs(context):
    value = definition([form_field('message', 'number')])
    value['variables'] = [{'selector': ['start', 'message'], 'value': '绕过数值控件'}]
    session, actor, _ = context
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, actor).create('覆盖输入', '', value)
    assert denied.value.code == 'workflow_variable_invalid'


@pytest.mark.parametrize('bad', ['format', 'field-type', 'schema-bool'])
def test_malformed_definition_metadata_returns_validation_error_instead_of_server_failure(context, bad):
    value = definition([form_field()])
    if bad == 'format':
        value['format'] = []
    elif bad == 'field-type':
        value['graph']['nodes'][0]['data']['variables'][0]['type'] = []
    else:
        value['graph']['nodes'][0]['data']['variables'][0]['json_schema'] = False
    with pytest.raises(DomainError) as denied:
        normalize_definition(value)
    assert denied.value.status == 422


def test_text_and_structured_input_are_mutually_exclusive_and_legacy_is_unchanged(context, monkeypatch):
    session, actor, _ = context
    workflow_id = create(context, definition([form_field('input')]))
    fake_worker(monkeypatch)
    service = WorkflowService(session, actor)
    with pytest.raises(DomainError):
        service.run(workflow_id, '文本', inputs={'input': '结构'})
    assert service.run(workflow_id, '文本')['inputs'] == {'input': '文本'}
    legacy = service.create('旧流程', '', {'steps': [{'type': 'text', 'template': '{{input}}'}]})
    session.commit()
    assert legacy['definition'] == {'steps': [{'name': '步骤1', 'type': 'text', 'template': '{{input}}'}]}
    with pytest.raises(DomainError):
        service.run(legacy['id'], inputs={})
    assert service.run(legacy['id'], '原输入')['output'] == '原输入'


def test_uncertain_worker_keeps_committed_event_and_does_not_replay(context, monkeypatch):
    session, actor, _ = context
    workflow_id = create(context)
    worker = worker_module()
    calls = fake_worker(monkeypatch, failure=worker.WorkerError('owned_process_cleanup_unconfirmed', '未确认', {'stopped': False}))
    service = WorkflowService(session, actor)
    result = service.run(workflow_id, inputs={}, idempotency_key='unknown')
    assert result['status'] == 'unknown' and result['steps'][0]['runtime_error']['stopped'] is False
    assert result['graph_progress']['event_count'] == 1 and len(service.run_events(result['id'])['items']) == 1
    assert TaskService(session, actor).get(result['task_id'])['status'] == 'unknown'
    assert service.run(workflow_id, inputs={}, idempotency_key='unknown')['id'] == result['id'] and len(calls) == 1


@pytest.mark.parametrize('status', ['failed', 'partial-succeeded'])
def test_native_failure_preserves_partial_typed_outputs_and_error_evidence(context, monkeypatch, status):
    session, actor, _ = context
    workflow_id = create(context)
    worker = worker_module()
    def execute(request, *, on_event, **kwargs):
        on_event(frame(request['run_id'], 2, 'NodeRunFailedEvent', error='字段值未满足条件'))
        result = terminal(request['run_id'], outputs={'completed': [1, False]}, status=status)
        result['error'] = {'code': 'graph_failed', 'message': '节点运行失败'}
        return {'result': result, 'process': {'stopped': True}}
    monkeypatch.setattr(worker, 'run_wsl', execute)
    result = WorkflowService(session, actor).run(workflow_id, inputs={})
    assert result['status'] == 'failed' and result['outputs'] == {'completed': [1, False]}
    assert result['steps'][0]['runtime_error']['code'] == 'graph_failed'
    assert result['graph_progress']['event_count'] == 1
    assert TaskService(session, actor).get(result['task_id'])['status'] == 'failed'


def test_event_read_is_paginated_and_workspace_permission_and_binding_are_rechecked(context, monkeypatch):
    session, actor, _ = context
    workflow_id = create(context)
    fake_worker(monkeypatch)
    service = WorkflowService(session, actor)
    result = service.run(workflow_id, inputs={})
    page = service.run_events(result['id'], limit=1)
    assert len(page['items']) == 1 and page['items'][0]['event_sequence'] == 2
    assert service.run_events(result['id'], after=page['next_cursor'])['items'] == []
    for outsider in [Actor('user-b', 'workspace-b', frozenset({'m04.read'})), Actor('user-a', 'workspace-a', frozenset())]:
        with pytest.raises(DomainError):
            WorkflowService(session, outsider).run_events(result['id'])
    row = session.get(WorkflowRun, result['id'])
    row.knowledge_sources = {'schema_version': 1, 'workspace_id': actor.workspace_id, 'space_ids': ['removed'], 'chunks': []}
    session.commit()
    with pytest.raises(DomainError):
        service.run_events(result['id'])


def test_internal_events_and_terminal_survive_source_revocation_but_public_body_is_denied(context, monkeypatch):
    session, actor, engine = context
    workflow_id = create(context)
    worker = worker_module()
    def execute(request, *, on_event, **kwargs):
        on_event(frame(request['run_id'], 2))
        run = session.get(WorkflowRun, request['run_id'])
        run.knowledge_sources = {'schema_version': 1, 'workspace_id': actor.workspace_id,
            'space_ids': ['removed-during-run'], 'chunks': []}
        session.commit()
        on_event(frame(request['run_id'], 3, 'GraphRunSucceededEvent', outputs={'result': 42}))
        return {'result': terminal(request['run_id']), 'process': {'stopped': True}}
    monkeypatch.setattr(worker, 'run_wsl', execute)
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, actor).run(workflow_id, inputs={})
    assert denied.value.status == 403
    with Session(engine) as observer:
        run = observer.scalar(select(WorkflowRun))
        assert run.status == 'succeeded' and observer.get(Task, run.task_id).status == 'succeeded'
        events = observer.scalars(select(TaskEvent).where(TaskEvent.type == GRAPH_EVENT)).all()
        assert [event.data['event_sequence'] for event in events] == [2, 3]


def test_event_read_refuses_task_rebound_to_another_run(context, monkeypatch):
    session, actor, _ = context
    workflow_id = create(context)
    fake_worker(monkeypatch)
    service = WorkflowService(session, actor)
    first, second = service.run(workflow_id, inputs={}), service.run(workflow_id, inputs={})
    session.get(WorkflowRun, first['id']).task_id = second['task_id']
    session.commit()
    with pytest.raises(DomainError) as denied:
        service.run_events(first['id'])
    assert denied.value.code == 'workflow_task_binding_invalid'


def test_database_failure_stops_pipeline_without_compensation_sql(context, monkeypatch):
    session, actor, engine = context
    workflow_id = create(context)
    worker = worker_module()
    cleanup = {'stopped': True}
    def failure(request, *, on_event, **kwargs):
        # 已提交预约后模拟事件flush失败；上层不能继续发SQL补偿“failed”。
        monkeypatch.setattr(session, 'commit', lambda: (_ for _ in ()).throw(OperationalError('event', {}, Exception('fixture'))))
        try:
            on_event(frame(request['run_id'], 2))
        except OperationalError as exc:
            exc.workflow_cleanup = cleanup
            raise
    monkeypatch.setattr(worker, 'run_wsl', failure)
    with pytest.raises(DomainError) as unavailable:
        WorkflowService(session, actor).run(workflow_id, inputs={}, idempotency_key='db-failure')
    assert unavailable.value.code == 'workflow_state_unavailable'
    assert unavailable.value.fields['process_cleanup'] == cleanup
    with Session(engine) as observer:
        run = observer.scalar(select(WorkflowRun))
        assert run.status == 'running' and observer.get(Task, run.task_id).status == 'running'
        assert observer.scalar(select(TaskEvent).where(TaskEvent.type == GRAPH_EVENT)) is None


class MemoryProcess:
    """不派发操作系统进程的管道替身；事件回调发生时尚未wait完成。"""
    def __init__(self, frames, stderr=b''):
        self.pid, self.returncode = 321, None
        self.stdout = io.BytesIO(b''.join(json.dumps(item).encode() + b'\n' for item in frames))
        self.stderr, self.stdin = io.BytesIO(stderr), io.BytesIO()
        self.killed = False
    def wait(self, timeout=None):
        self.returncode = -1 if self.killed else 0
        return self.returncode
    def poll(self):
        return self.returncode
    def kill(self):
        self.killed = True


def test_live_collector_calls_events_on_main_thread_before_process_wait():
    worker = worker_module()
    process = MemoryProcess([frame('live', 1), terminal('live', 2)])
    observed = []
    main_thread = threading.get_ident()
    def on_event(event):
        assert process.returncode is None and threading.get_ident() == main_thread
        observed.append(event)
    result = worker._collect_live(process, b'{}', 1, 'live', on_event)
    assert len(observed) == 1 and result['result']['outputs'] == {'result': 42}


def test_live_callback_storage_error_keeps_original_exception_and_exact_cleanup():
    worker = worker_module()
    process = MemoryProcess([frame('live', 1), terminal('live', 2)])
    stopped = []
    def on_event(_):
        raise OperationalError('fixture', {}, Exception('no-secret'))
    with pytest.raises(OperationalError) as failed:
        worker._collect_live(process, b'{}', 1, 'live', on_event,
            stop_owned=lambda identity: stopped.append(identity) or {'stopped': False})
    assert stopped == [None] and failed.value.workflow_cleanup == {'stopped': False} and process.killed


def test_cleanup_exception_cannot_mask_storage_failure_or_claim_success():
    worker = worker_module()
    process = MemoryProcess([frame('live', 1)])
    def fail(_):
        raise OperationalError('fixture', {}, Exception('fixture'))
    def stop(_):
        raise OSError('only-fixture')
    with pytest.raises(OperationalError) as failure:
        worker._collect_live(process, b'{}', 1, 'live', fail, stop_owned=stop)
    assert failure.value.workflow_cleanup['stopped'] is False


@pytest.mark.parametrize('mismatch', [None, 'birth', 'uid', 'command', 'nonce', 'executable'])
def test_linux_stop_rechecks_identity_before_signalling(monkeypatch, mismatch):
    import psutil
    from types import SimpleNamespace
    worker = worker_module()
    runtime_id, boot_id, nonce = 'a' * 32, 'b' * 64, 'c' * 32
    command = ['/opt/ai-agent-company/venvs/api/bin/python', '-I', '-u',
        str(worker.WORKER), '--owned-wsl', runtime_id, boot_id]
    expected = {'pid': 7654, 'uid': 1001, 'start_ticks': '123', 'executable': '/opt/ai-agent-company/python',
        'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest(), 'worker_path': str(worker.WORKER),
        'run_id': 'stop-fixture', 'runtime_id': runtime_id, 'boot_id': boot_id, 'launch_nonce': nonce}
    calls, alive = [], [True]
    class Candidate:
        pid = 7654
        def uids(self):
            return SimpleNamespace(real=1002 if mismatch == 'uid' else 1001)
        def exe(self):
            return '/other/python' if mismatch == 'executable' else expected['executable']
        def cmdline(self):
            return command + ['other'] if mismatch == 'command' else command
        def environ(self):
            return {'WORKFLOW_RUNTIME_NONCE': 'wrong' if mismatch == 'nonce' else nonce,
                'WORKFLOW_RUNTIME_RUN_ID': 'stop-fixture'}
        def status(self):
            return 'running'
        def terminate(self):
            calls.append(('terminate', self.pid))
        def wait(self, timeout):
            alive[0] = False
    candidate = Candidate()
    def process(pid):
        assert pid == 7654
        if not alive[0]:
            raise psutil.NoSuchProcess(pid)
        return candidate
    monkeypatch.setattr(worker, 'verify_linux_context', lambda *args: None)
    monkeypatch.setattr(worker.os, 'getuid', lambda: 1001, raising=False)
    monkeypatch.setattr(psutil, 'Process', process)
    monkeypatch.setattr(worker, 'Path', lambda path: SimpleNamespace(
        read_text=lambda: '7654 (python) ' + ' '.join(['S'] + ['0'] * 18 + ['124' if mismatch == 'birth' else '123'])))
    value = {'protocol_version': 1, 'process': expected}
    if mismatch:
        with pytest.raises(worker.WorkerError) as denied:
            worker.stop_linux_owned(value, runtime_id, boot_id)
        assert denied.value.code == 'owned_process_identity_changed' and calls == []
    else:
        assert worker.stop_linux_owned(value, runtime_id, boot_id)['stopped'] is True
        assert calls == [('terminate', 7654)]


def test_invalid_process_identity_is_never_used_to_stop_other_linux_pid():
    worker = worker_module()
    process = MemoryProcess([{'protocol_version': 1, 'run_id': 'live', 'sequence': 1, 'kind': 'process',
        'data': {'pid': 999, 'launch_nonce': 'wrong'}}, terminal('live', 2)])
    stopped, observed = [], []
    with pytest.raises(worker.WorkerError) as denied:
        worker._collect_live(process, b'{}', 1, 'live', observed.append, expected_identity={'launch_nonce': 'expected'},
            stop_owned=lambda identity: stopped.append(identity) or {'stopped': False})
    assert denied.value.code == 'process_identity_invalid' and stopped == [None] and observed == []


def test_live_collector_rejects_stderr_budget_even_when_stdout_has_valid_success():
    worker = worker_module()
    process = MemoryProcess([terminal('live', 1)], stderr=b'x' * (64 * 1024 + 1))
    with pytest.raises(worker.WorkerError) as denied:
        worker._collect_live(process, b'{}', 1, 'live', lambda _: None)
    assert denied.value.code == 'output_budget_exceeded'


@pytest.mark.skipif(os.environ.get('WORKFLOW_PURE_TEST_WSL') != '1', reason='只在显式授权本机纯图环境时执行')
def test_real_owned_wsl_live_handshake_and_typed_outputs():
    worker = worker_module()
    value = definition([form_field('message', 'number')])
    request = {'protocol_version': 1, 'run_id': 't37-live-pure-graph', 'root_node_id': value['root_node_id'],
        'graph': value['graph'], 'inputs': {'message': 42}, 'variables': [],
        'limits': {'max_steps': 10, 'max_seconds': 10}}
    observed, main_thread = [], threading.get_ident()
    def on_event(event):
        assert threading.get_ident() == main_thread
        observed.append(event)
    result = worker.run_wsl(request, timeout_seconds=15, on_event=on_event)
    assert result['result']['status'] == 'succeeded' and result['result']['outputs'] == {'result': 42}
    assert result['process']['stopped'] and result['process']['linux']['uid'] > 0
    assert result['process']['linux']['run_id'] == request['run_id']
    assert observed and observed[0]['sequence'] == 2
    assert any(event['event_type'] == 'GraphRunSucceededEvent' for event in observed)


@pytest.mark.skipif(os.environ.get('WORKFLOW_PURE_TEST_WSL') != '1', reason='只在显式授权本机纯图环境时执行')
def test_real_owned_wsl_callback_failure_verifies_only_its_linux_process_stopped():
    worker = worker_module()
    value = definition()
    request = {'protocol_version': 1, 'run_id': 't37-live-callback-stop', 'root_node_id': value['root_node_id'],
        'graph': value['graph'], 'inputs': {}, 'variables': [], 'limits': {'max_steps': 10, 'max_seconds': 10}}
    observed = []
    def on_event(event):
        observed.append(event)
        raise OperationalError('only-fixture-callback', {}, Exception('fixture'))
    with pytest.raises(OperationalError) as stopped:
        worker.run_wsl(request, timeout_seconds=15, on_event=on_event)
    assert len(observed) == 1 and stopped.value.workflow_cleanup['stopped'] is True
