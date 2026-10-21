"""新建临时SQLite和ASGI替身验证工具额度，实际读取、模型和MySQL调用均为零。"""

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import flag_modified

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task, TaskEvent
from agent_platform.modules.chat.native_models import NativeChatTurn, NativeConversation, NativeChatIdentity, utcnow
from agent_platform.modules.integrations import delegated_router
from agent_platform.modules.integrations.delegated_budget import reserve_tool_call

from test_delegated_business_tools import site as base_site, call

POLICY = {'version': 1, 'alias_model_id': 'mock-model', 'server_id': 'business-server',
          'max_tool_calls': 4, 'max_tool_iterations': 2, 'max_model_requests': 3, 'max_seconds': 90,
          'integration_revision': 1, 'budget_secret_ref': 'test-budget-secret'}


@pytest.fixture
def site(base_site):
    with base_site.db.sessions.begin() as session:
        turn = session.get(NativeChatTurn, 'turn')
        turn.task_id = 'task'
        turn.request_metadata = {'tool_policy': deepcopy(POLICY), 'tool_budget': {'version': 1, 'calls_used': 0},
                                 'content_sha256': 'a' * 64}
        session.add(Task(id='task', workspace_id='ws', user_id='human', kind='platform_native_chat',
            title='预算测试', status='running', inputs={'turn_id': 'turn', 'chat_id': 'conversation'}))
    return base_site


def used(site):
    with site.db.sessions() as session:
        return session.get(NativeChatTurn, 'turn').request_metadata['tool_budget']['calls_used']


def test_fifth_call_is_rejected_before_tool_execution(site, monkeypatch):
    executed = []
    original = delegated_router.execute_tool

    def execute(*args, **kwargs):
        executed.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(delegated_router, 'execute_tool', execute)
    for _ in range(4):
        assert call(site).status_code == 200
    response = call(site)
    assert response.status_code == 429 and response.json()['code'] == 'native_tool_budget_exhausted'
    assert executed == ['get_device'] * 4 and used(site) == 4
    with site.db.sessions() as session:
        turn = session.get(NativeChatTurn, 'turn')
        assert turn.sources[0]['resource']['id'] == 'device-one'
        assert turn.request_metadata['content_sha256'] == 'a' * 64
        events = list(session.scalars(select(TaskEvent).where(TaskEvent.type == 'native_tool_call_reserved')))
        assert [item.data['calls_used'] for item in events] == [1, 2, 3, 4]
        assert all(set(item.data) == {'turn_id', 'operation_id', 'calls_used', 'max_tool_calls'} for item in events)


@pytest.mark.parametrize('operation,params,status', [
    ('get_device', {'id': 'missing'}, 404), ('get_device', {'id': 'device-one', 'sql': 'invalid'}, 422),
    ('unknown-tool', {}, 404),
])
def test_failed_tool_does_not_refund_committed_budget(site, operation, params, status):
    assert call(site, operation=operation, params=params).status_code == status
    assert used(site) == 1


@pytest.mark.parametrize('budget', [None, {}, {'version': True, 'calls_used': 0},
    {'version': 1, 'calls_used': -1}, {'version': 1, 'calls_used': True},
    {'version': 1, 'calls_used': 5}, {'version': 1, 'calls_used': 0, 'extra': 1}])
def test_corrupt_budget_never_falls_back_to_unbounded(site, budget):
    with site.db.sessions.begin() as session:
        turn = session.get(NativeChatTurn, 'turn')
        turn.request_metadata = {**turn.request_metadata, 'tool_budget': budget}
        # Python把True与1判相等；强制写入这份故障JSON，避免ORM将测试污染误判为未变。
        flag_modified(turn, 'request_metadata')
    response = call(site)
    assert response.status_code == 503 and response.json()['code'] == 'native_tool_budget_invalid'


def test_cancel_between_reservation_and_execution_prevents_business_read(site, monkeypatch):
    original = delegated_router.resolve_delegated_actor
    resolves = []
    def resolve(session, engine, request):
        resolves.append(1)
        if len(resolves) == 2:
            with site.db.sessions.begin() as other:
                other.get(NativeChatTurn, 'turn').status = 'cancel_requested'
        return original(session, engine, request)
    monkeypatch.setattr(delegated_router, 'resolve_delegated_actor', resolve)
    monkeypatch.setattr(delegated_router, 'execute_tool', lambda *args, **kwargs: pytest.fail('取消后不应读取业务'))
    assert call(site).status_code == 403
    assert used(site) == 1


@pytest.mark.parametrize('changed', ['revision', 'cancel', 'deadline', 'conversation', 'identity', 'task'])
def test_atomic_claim_guards_changes_after_authenticated_snapshot(site, changed):
    actor = Actor('human', 'ws', frozenset({'m02.read'}))
    context = {'turn_id': 'turn', 'actor_user_id': 'human', 'workspace_id': 'ws',
               'chat_id': 'conversation', 'identity_id': 'native-identity', 'instance_id': 'instance'}
    with site.db.sessions() as session:
        # 保留已认证时的ORM快照，再用同事务SQL模拟CAS前已发生的并发状态变化。
        turn = session.get(NativeChatTurn, 'turn')
        session.get(Task, 'task')
        if changed == 'revision':
            statement = update(NativeChatTurn).where(NativeChatTurn.id == 'turn').values(revision=turn.revision + 1)
        elif changed == 'cancel':
            statement = update(NativeChatTurn).where(NativeChatTurn.id == 'turn').values(status='cancel_requested')
        elif changed == 'deadline':
            statement = update(NativeChatTurn).where(NativeChatTurn.id == 'turn').values(deadline_at=utcnow() - timedelta(seconds=1))
        elif changed == 'conversation':
            statement = update(NativeConversation).where(NativeConversation.id == 'conversation').values(active_turn_id=None)
        elif changed == 'identity':
            statement = update(NativeChatIdentity).where(NativeChatIdentity.id == 'native-identity').values(status='failed')
        else:
            statement = update(Task).where(Task.id == 'task').values(status='cancel_requested')
        session.execute(statement, execution_options={'synchronize_session': False})
        with pytest.raises(DomainError) as error:
            reserve_tool_call(session, actor, context, 'get_device')
        assert error.value.code == 'native_tool_budget_conflict'
    assert used(site) == 0


def test_legacy_turn_does_not_synthesize_policy_from_current_config(base_site):
    assert call(base_site).status_code == 200
    with base_site.db.sessions() as session:
        assert 'tool_policy' not in session.get(NativeChatTurn, 'turn').request_metadata
