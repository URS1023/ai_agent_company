"""全新临时SQLite与模型替身验证运行预约，不访问任何在用数据库或外部服务。"""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.embeddings import EmbeddingClient
from agent_platform.core.errors import DomainError
from agent_platform.core.models import AuditEvent, Notification, Task, TaskEvent
from agent_platform.core.tasks import TaskService
from agent_platform.modules.chat.service import ChatService
from agent_platform.modules.flows.execution import WorkflowExecution
from agent_platform.modules.flows.models import Workflow, WorkflowRun, utcnow
from agent_platform.modules.flows.service import WorkflowService, render
from agent_platform.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from agent_platform.modules.knowledge.service import KnowledgeService

TABLES = [item.__table__ for item in (Workflow, WorkflowRun, KnowledgeSpace, KnowledgeDocument,
    KnowledgeChunk, Task, TaskEvent, AuditEvent, Notification)]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'workflow-owned.sqlite3').as_posix())
    Base.metadata.create_all(engine, tables=TABLES)
    actor = Actor('user-a', 'workspace-a', frozenset({'m04.*', 'm08.*'}))
    calls = []
    def complete(_self, messages, **kwargs):
        calls.append((messages, kwargs))
        return {'choices': [{'message': {'content': '模型结果'}}]}
    monkeypatch.setattr(ChatService, 'complete', complete)
    monkeypatch.setattr(EmbeddingClient, 'embed', lambda _self, texts, **kwargs: [[1.0, 0.0] for _ in texts])
    with Session(engine, expire_on_commit=False) as session:
        yield session, actor, engine, calls
    engine.dispose()


def create(environment, steps=None):
    session, actor, _, _ = environment
    result = WorkflowService(session, actor).create('流程', '', {'steps': steps or [
        {'name': '回答', 'type': 'ai', 'prompt': '{{input}}', 'max_tokens': 128}]})
    session.commit()
    return result['id']


def test_reservation_task_and_dispatch_are_committed_before_model_request(environment, monkeypatch):
    session, actor, engine, _ = environment
    workflow_id = create(environment)
    def inspect_before_request(_self, messages, **kwargs):
        assert not session.in_transaction()
        with Session(engine) as observer:
            run = observer.scalar(select(WorkflowRun))
            assert run.status == 'running' and run.steps[0]['status'] == 'dispatching'
            assert run.request_hash and run.workflow_revision == 1 and run.definition_snapshot['steps']
            task = observer.get(Task, run.task_id)
            assert task.status == 'running' and task.inputs['run_id'] == run.id
            assert 'knowledge_sources' not in task.inputs
        assert 0 < kwargs['timeout_seconds'] <= 90
        return {'choices': [{'message': {'content': '成功'}}]}
    monkeypatch.setattr(ChatService, 'complete', inspect_before_request)
    result = WorkflowService(session, actor).run(workflow_id, '提问', idempotency_key='intent-1')
    assert result['status'] == 'succeeded' and result['output'] == '成功'
    assert TaskService(session, actor).get(result['task_id'])['status'] == 'succeeded'
    assert session.scalar(select(Notification)).resource_id == result['task_id']


def test_same_intent_reads_original_but_changed_input_or_revision_conflicts(environment):
    session, actor, _, calls = environment
    workflow_id = create(environment)
    service = WorkflowService(session, actor)
    first = service.run(workflow_id, '原提问', idempotency_key='fixed')
    assert service.run(workflow_id, '原提问', idempotency_key='fixed')['id'] == first['id']
    assert len(calls) == 1
    for changed_input, change_revision in [('另一问题', False), ('原提问', True)]:
        if change_revision:
            session.get(Workflow, workflow_id).revision += 1
            session.commit()
        with pytest.raises(DomainError) as conflict:
            service.run(workflow_id, changed_input, idempotency_key='fixed')
        assert conflict.value.code == 'idempotency_conflict'
        assert conflict.value.fields['run_id'] == first['id']
    assert len(calls) == 1


@pytest.mark.parametrize('failure', [DomainError(504, 'chat_timeout', '超时'), RuntimeError('上游含私密内容')])
def test_uncertain_request_keeps_completed_steps_and_never_redispatches(environment, monkeypatch, failure):
    session, actor, engine, _ = environment
    workflow_id = create(environment, [{'type': 'text', 'name': '准备', 'template': '已准备{{input}}'},
        {'type': 'ai', 'name': '回答'}])
    count = []
    def fail(_self, messages, **kwargs):
        count.append(1)
        with Session(engine) as observer:
            run = observer.scalar(select(WorkflowRun))
            assert run.steps[0]['output'] == '已准备原问题' and run.steps[1]['status'] == 'dispatching'
        raise failure
    monkeypatch.setattr(ChatService, 'complete', fail)
    service = WorkflowService(session, actor)
    first = service.run(workflow_id, '原问题', idempotency_key='unknown-intent')
    session.rollback()
    saved = service.run_detail(first['id'])
    assert saved['status'] == 'unknown' and saved['steps'][0]['status'] == 'ok'
    assert '私密' not in str(saved)
    assert TaskService(session, actor).get(saved['task_id'])['status'] == 'unknown'
    assert service.run(workflow_id, '原问题', idempotency_key='unknown-intent')['id'] == first['id']
    assert count == [1]


def test_explicit_rejection_is_failed_and_same_key_stays_failed(environment, monkeypatch):
    session, actor, _, _ = environment
    workflow_id = create(environment)
    def limited(*args, **kwargs):
        raise DomainError(429, 'chat_rate_limited', '限流')
    monkeypatch.setattr(ChatService, 'complete', limited)
    service = WorkflowService(session, actor)
    result = service.run(workflow_id, '问题', idempotency_key='rate-limited')
    assert result['status'] == 'failed' and result['error'] == '限流'
    assert service.run(workflow_id, '问题', idempotency_key='rate-limited')['id'] == result['id']


def test_expired_reservation_is_only_marked_unknown_without_recovery_execution(environment):
    session, actor, _, calls = environment
    workflow_id = create(environment)
    service = WorkflowService(session, actor)
    reserved, fresh = WorkflowExecution(service).reserve(workflow_id, '问题', 'crashed')
    assert fresh
    reserved.deadline_at = utcnow() - timedelta(seconds=1)
    session.commit()
    result = service.run_detail(reserved.id)
    assert result['status'] == 'unknown' and calls == []
    assert service.run(workflow_id, '问题', idempotency_key='crashed')['status'] == 'unknown'
    assert calls == []


def test_request_key_scope_and_lookup_are_user_and_workspace_bound(environment):
    session, actor, _, calls = environment
    workflow_id = create(environment)
    first = WorkflowService(session, actor).run(workflow_id, 'a', idempotency_key='same')
    second_user = Actor('user-b', actor.workspace_id, actor.permissions)
    second = WorkflowService(session, second_user).run(workflow_id, 'b', idempotency_key='same')
    assert first['id'] != second['id'] and len(calls) == 2
    assert WorkflowService(session, actor).runs(workflow_id, idempotency_key='same')['items'][0]['id'] == first['id']
    assert WorkflowService(session, second_user).runs(workflow_id, idempotency_key='same')['total'] == 1
    stranger = Actor(actor.user_id, 'workspace-b', actor.permissions)
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, stranger).run(workflow_id, 'a', idempotency_key='same')
    assert denied.value.status == 404 and len(calls) == 2


def test_old_without_key_remains_new_run_and_frozen_definition_does_not_change(environment):
    session, actor, _, calls = environment
    workflow_id = create(environment)
    service = WorkflowService(session, actor)
    first, second = service.run(workflow_id, 'x'), service.run(workflow_id, 'x')
    assert first['id'] != second['id'] and len(calls) == 2
    service.update(workflow_id, '新名', '', {'steps': [{'name': '新步骤', 'type': 'text', 'template': '修改'}]})
    session.commit()
    saved = service.run_detail(first['id'])
    assert saved['workflow_revision'] == 1 and saved['definition_snapshot']['steps'][0]['type'] == 'ai'


def test_all_three_steps_share_remaining_budget_and_history_task_recheck_sources(environment, monkeypatch):
    session, actor, engine, calls = environment
    knowledge = KnowledgeService(session, actor)
    space = knowledge.create_space('点检')
    knowledge.import_text(space['id'], '标准', '温度75℃')
    session.commit()
    timeline, timeouts = [0.0], []
    def embed(_self, texts, **kwargs):
        assert not session.in_transaction()
        timeouts.append(kwargs['timeout_seconds'])
        timeline[0] += 3
        return [[1.0, 0.0]]
    monkeypatch.setattr(EmbeddingClient, 'embed', embed)
    workflow_id = create(environment, [{'name': '检索', 'type': 'kb', 'space_ids': [space['id']]},
        {'name': '回答', 'type': 'ai', 'prompt': '{{检索.output}}'},
        {'name': '结果', 'type': 'text', 'template': '{{回答.output}}'}])
    result = WorkflowService(session, actor, budget_seconds=10, clock=lambda: timeline[0]).run(workflow_id, '温度', idempotency_key='kb')
    assert result['status'] == 'succeeded' and len(result['steps']) == 3
    assert timeouts == [10] and calls[0][1]['timeout_seconds'] == 7
    restricted = Actor(actor.user_id, actor.workspace_id, frozenset({'m04.*'}))
    with pytest.raises(DomainError):
        TaskService(session, restricted).get(result['task_id'])
    assert WorkflowService(session, restricted).runs(workflow_id)['total'] == 0
    with pytest.raises(DomainError):
        WorkflowService(session, restricted).run(workflow_id, '温度', idempotency_key='kb')
    assert len(calls) == 1


def test_whole_run_deadline_prevents_following_model_calls(environment, monkeypatch):
    session, actor, _, _ = environment
    workflow_id = create(environment, [{'name': '一', 'type': 'ai'}, {'name': '二', 'type': 'ai'}])
    timeline, calls = [0.0], []
    def complete(*args, **kwargs):
        calls.append(kwargs['timeout_seconds'])
        timeline[0] += 11
        return {'choices': [{'message': {'content': '迟到结果'}}]}
    monkeypatch.setattr(ChatService, 'complete', complete)
    result = WorkflowService(session, actor, budget_seconds=10, clock=lambda: timeline[0]).run(workflow_id, '')
    assert result['status'] == 'unknown' and calls == [10]
    assert result['steps'][0]['error_code'] == 'workflow_deadline_exceeded'


@pytest.mark.parametrize('key', ['', '中文', 'space key', 'a' * 161, '\n'])
def test_invalid_request_number_has_no_run_or_model(environment, key):
    session, actor, _, calls = environment
    workflow_id = create(environment)
    with pytest.raises(DomainError) as invalid:
        WorkflowService(session, actor).run(workflow_id, 'x', idempotency_key=key)
    assert invalid.value.code == 'invalid_idempotency_key'
    assert session.scalar(select(WorkflowRun)) is None and calls == []


def test_template_expansion_is_bounded_before_joining_repeated_large_outputs():
    with pytest.raises(DomainError) as invalid:
        render('{{a.output}}' * 20, {'a.output': 'x' * 10_000})
    assert invalid.value.code == 'workflow_value_too_large'
    assert render('{{unknown}} {{input}}', {'input': '值'}) == '{{unknown}} 值'


def test_source_deleted_during_model_wait_still_closes_run_and_task_without_further_dispatch(environment, monkeypatch):
    session, actor, _, _ = environment
    knowledge = KnowledgeService(session, actor)
    space = knowledge.create_space('临时标准')
    document = knowledge.import_text(space['id'], '说明', '上限75℃')
    session.commit()
    workflow_id = create(environment, [{'type': 'kb', 'name': '检索', 'space_ids': [space['id']]},
        {'type': 'ai', 'name': '回答', 'prompt': '{{检索.output}}'}, {'type': 'ai', 'name': '后续'}])
    calls = []
    def source_removed(*args, **kwargs):
        calls.append(1)
        knowledge.delete_document(document['id'])
        session.commit()
        return {'choices': [{'message': {'content': '已获得结果'}}]}
    monkeypatch.setattr(ChatService, 'complete', source_removed)
    with pytest.raises(DomainError) as denied:
        WorkflowService(session, actor).run(workflow_id, '上限', idempotency_key='delete-during-wait')
    assert denied.value.status == 403 and calls == [1]
    run = session.scalar(select(WorkflowRun))
    task = session.get(Task, run.task_id)
    assert run.status == task.status == 'failed'
    assert run.steps[0]['status'] == 'ok' and task.outputs['completed_steps'] == 2
    with pytest.raises(DomainError):
        TaskService(session, actor).get(task.id)
    assert WorkflowService(session, actor).runs(workflow_id)['total'] == 0


def test_internal_task_projection_refuses_foreign_or_mismatched_task_binding(environment):
    session, actor, _, _ = environment
    workflow_id = create(environment)
    run, _ = WorkflowExecution(WorkflowService(session, actor)).reserve(workflow_id, 'x', 'binding')
    task = session.get(Task, run.task_id)
    original = task.inputs
    task.inputs = {**original, 'run_id': 'unrelated-run'}
    session.commit()
    with pytest.raises(DomainError) as conflict:
        TaskService(session, actor).project_workflow(run.id)
    assert conflict.value.code == 'workflow_task_binding_invalid'
    task.inputs = original
    session.commit()
    stranger = Actor(actor.user_id, 'workspace-b', actor.permissions)
    with pytest.raises(DomainError):
        TaskService(session, stranger).project_workflow(run.id)
    with pytest.raises(DomainError) as incomplete:
        TaskService(session, actor).project_workflow(run.id, completed_step=1)
    assert incomplete.value.code == 'workflow_step_not_completed'


def test_storage_error_after_dispatch_preserves_reservation_and_never_reissues(environment, monkeypatch):
    session, actor, engine, _ = environment
    workflow_id = create(environment)
    real_commit, issued = session.commit, []
    def model_once(*args, **kwargs):
        issued.append(1)
        monkeypatch.setattr(session, 'commit', lambda: (_ for _ in ()).throw(OperationalError('mock', {}, Exception())))
        return {'choices': [{'message': {'content': '已生成'}}]}
    monkeypatch.setattr(ChatService, 'complete', model_once)
    with pytest.raises(DomainError) as failure:
        WorkflowService(session, actor).run(workflow_id, 'x', idempotency_key='store-failed')
    assert failure.value.code == 'workflow_state_unavailable'
    assert failure.value.fields['run_id'] and failure.value.fields['task_id']
    monkeypatch.setattr(session, 'commit', real_commit)
    with Session(engine) as observer:
        saved = observer.get(WorkflowRun, failure.value.fields['run_id'])
        assert saved.status == 'running' and saved.steps[0]['status'] == 'dispatching'
    same = WorkflowService(session, actor).run(workflow_id, 'x', idempotency_key='store-failed')
    assert same['id'] == failure.value.fields['run_id'] and issued == [1]


@pytest.mark.parametrize('entry', ['run', 'task', 'list'])
def test_expired_reservation_closes_task_even_if_knowledge_was_revoked(environment, entry):
    session, actor, _, calls = environment
    knowledge = KnowledgeService(session, actor)
    space = knowledge.create_space('权限范围')
    session.commit()
    workflow_id = create(environment, [{'type': 'kb', 'name': '检索', 'space_ids': [space['id']]}])
    service = WorkflowService(session, actor)
    run, _ = WorkflowExecution(service).reserve(workflow_id, '问题', 'revoked-orphan')
    run.deadline_at = utcnow() - timedelta(seconds=1)
    session.commit()
    restricted = Actor(actor.user_id, actor.workspace_id, frozenset({'m04.*'}))
    if entry == 'list':
        assert WorkflowService(session, restricted).runs(workflow_id)['total'] == 0
    else:
        with pytest.raises(DomainError):
            if entry == 'task':
                TaskService(session, restricted).get(run.task_id)
            else:
                WorkflowService(session, restricted).run_detail(run.id)
    assert run.status == 'unknown' and session.get(Task, run.task_id).status == 'unknown' and calls == []
