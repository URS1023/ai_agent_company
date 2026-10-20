"""新临时SQLite验证任务证据授权，不连接业务源，不运行SQL取数。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Notification, Task, TaskEvent
from agent_platform.core.tasks import TaskService
from agent_platform.modules.data.service import DataService
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.energy.models import EnergyAnalysis
from agent_platform.modules.workspace.router import events, notifications, tasks, workbench


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'task-evidence.sqlite3').as_posix())
    Base.metadata.create_all(engine)
    actor = Actor('reviewer', 'workspace', frozenset({'m01.read', 'm10.read', 'm11.read'}))
    denied, reads = set(), []
    def read(service, query_id):
        reads.append(query_id)
        if query_id in denied:
            raise DomainError(403, 'query_scope_denied', '当前数据范围不匹配')
        return object()
    monkeypatch.setattr(DataService, 'get_query', read)
    monkeypatch.setattr(DataService, 'execute_query', lambda *a, **kw: pytest.fail('禁止重跑数据源查询'))
    with Session(engine) as session:
        yield session, actor, denied, reads
    engine.dispose()


def inspection_task(session, name, query_id, *, stamp='2026-10-21T00:00:00+00:00'):
    task = Task(id='task-' + name, workspace_id='workspace', user_id='reviewer',
                kind='inspection', title=name, engine='platform', status='waiting_review',
                inputs={'inspection_id': name, 'template_id': 'standard', 'template_revision': 1},
                outputs={'run_id': 'run-' + name, 'outcome': 'failed'},
                access_permission='m11.read', created_at=stamp)
    session.add(m.Inspection(id=name, workspace_id='workspace', name=name, target_type='device',
                            template_id='standard', template_revision=1,
                            standard_snapshot={'revision': 1}, result={}))
    session.add(task)
    session.add(m.InspectionRun(id='run-' + name, workspace_id='workspace',
        inspection_id=name, task_id=task.id,
        result={'run_id': 'run-' + name, 'standard_revision': 1, 'samples': [
            {'metrics': [{'provenance': {'query_id': query_id}}]}]}))
    session.add(TaskEvent(task_id=task.id, type='state_changed', data={'outcome': 'failed'}))
    session.add(Notification(workspace_id='workspace', user_id='reviewer', title=name,
                             resource_type='task', resource_id=task.id))
    session.flush()
    return task


def test_revoke_hides_detail_events_notifications_and_uses_frozen_run(context):
    session, actor, denied, reads = context
    task = inspection_task(session, '旧检验', 'old-query')
    # 最新检验结果换源也不能使旧运行重新可见。
    session.get(m.Inspection, '旧检验').result = {'samples': [{'metrics': [{'provenance': {'query_id': 'new-query'}}]}]}
    assert TaskService(session, actor).get(task.id)['outputs']['outcome'] == 'failed'
    denied.add('old-query')
    for operation in (lambda: TaskService(session, actor).get(task.id),
                      lambda: events(task.id, 0, actor, session)):
        with pytest.raises(DomainError) as result:
            operation()
        assert result.value.status == 403
    assert notifications(actor, session)['items'] == []
    assert set(reads) == {'old-query'}


def test_list_counts_and_home_filter_before_paging(context):
    session, actor, denied, _ = context
    hidden = inspection_task(session, '隐藏', 'restricted', stamp='2026-10-21T01:00:00+00:00')
    allowed = inspection_task(session, '可见', 'allowed')
    denied.add('restricted')
    result = tasks(limit=1, offset=0, actor=actor, session=session)
    assert result['total'] == 1 and [r['id'] for r in result['items']] == [allowed.id]
    result = workbench(actor, session)
    assert result['counts']['tasks'] == 1
    assert [r['id'] for r in result['tasks']] == [allowed.id]
    assert hidden.title not in str(result['tasks'])


def test_quality_task_checks_selected_run_without_recursive_task_lookup(context):
    session, actor, denied, reads = context
    original = inspection_task(session, '初检', 'original-query')
    service = TaskService(session, actor)
    task = service.create('quality_case', '处置单', inputs={'source_run_id': 'run-初检'},
                          access_permission='m11.read')
    denied.add('original-query')
    with pytest.raises(DomainError) as result:
        service.get(task['id'])
    assert result.value.status == 403 and reads[-1] == 'original-query'


def test_energy_task_uses_executed_snapshot_and_current_input_access(context):
    session, actor, denied, reads = context
    task = Task(id='energy-task', workspace_id='workspace', user_id='reviewer',
                kind='energy_analysis', title='能耗', engine='platform', status='succeeded',
                inputs={'query_id': 'template-query'}, outputs={}, access_permission='m10.read')
    session.add(task)
    session.add(EnergyAnalysis(id='energy', workspace_id='workspace', name='能耗',
        device_id='device', query_id='execution-query', task_id=task.id, created_by='reviewer',
        request_snapshot={}, result={}, provenance={'query_id': 'execution-query'}))
    session.flush()
    assert TaskService(session, actor).get(task.id)['id'] == task.id
    assert set(reads) == {'execution-query', 'template-query'}
    denied.add('execution-query')
    with pytest.raises(DomainError):
        TaskService(session, actor).get(task.id)


def test_admin_does_not_bypass_current_evidence_scope(context):
    session, _, denied, _ = context
    task = inspection_task(session, '管理员历史', 'restricted')
    denied.add('restricted')
    admin = Actor('admin', 'workspace', frozenset({'*'}), is_admin=True)
    with pytest.raises(DomainError) as result:
        TaskService(session, admin).get(task.id)
    assert result.value.status == 403


def test_task_preserves_business_values_but_keeps_secret_keys_hidden(context):
    from dataclasses import replace
    from agent_platform.core.records import sanitized
    session, actor, _, _ = context
    actor = replace(actor, permissions=actor.permissions | {'m04.read'})
    service = TaskService(session, actor)
    task = service.create('native_workflow', '业务输出', inputs={'value': '批次A', 'api_key': 'private'})
    service.transition(task['id'], 'running')
    result = service.transition(task['id'], 'succeeded', outputs={
        'value': 42, 'samples': [{'value': '正常', 'token': 'private'}], 'password': 'private',
    })
    assert result['inputs'] == {'value': '批次A', 'api_key': '[已隐藏]'}
    assert result['outputs'] == {
        'value': 42, 'samples': [{'value': '正常', 'token': '[已隐藏]'}], 'password': '[已隐藏]',
    }
    assert sanitized({'value': 'private', 'nested': {'value': 'private'}}) == {
        'value': '[已隐藏]', 'nested': {'value': '[已隐藏]'},
    }
