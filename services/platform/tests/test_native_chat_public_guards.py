"""只用临时SQLite验证公共任务与凭据删除入口，不连接原生服务或企业数据库。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import (
    Notification, Organization, ResourceRecord, ResourceReference, Task, TaskEvent, User, Workspace,
)
from agent_platform.core.settings import Settings
from agent_platform.core.tasks import TaskService
from agent_platform.modules.chat import native_sources
from agent_platform.modules.chat.native_models import NativeChatIdentity, NativeChatTurn, NativeConversation
from agent_platform.modules.identity.credentials import secret_delete
from agent_platform.modules.integrations.service import IntegrationService
from agent_platform.modules.workspace.router import events, notifications, tasks


@pytest.fixture
def context(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'new-public-guards.sqlite3').as_posix())
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Organization(id='org', name='隔离验证'))
        session.flush()
        session.add_all([Workspace(id=name, organization_id='org', name=name) for name in ('ws', 'other-ws')])
        session.add_all([User(id=name, organization_id='org', username=name, display_name=name,
            password_hash='fixture-only') for name in ('owner', 'other')])
        session.flush()
        session.add(NativeChatIdentity(id='identity', workspace_id='ws', user_id='owner', integration_id='engine',
            integration_revision=1, integration_url='http://127.0.0.1:13000', native_user_id='native-owner', status='ready'))
        session.add(NativeConversation(id='chat', workspace_id='ws', user_id='owner', integration_id='engine',
            identity_id='identity', native_chat_id='native-chat', request_hash='hash', status='ready'))
        session.add(Task(id='task', workspace_id='ws', user_id='owner', kind='platform_native_chat',
            title='用户私有对话', engine='openwebui', status='succeeded', access_permission='m02.read',
            inputs={'turn_id': 'turn', 'chat_id': 'chat'}))
        session.flush()
        session.add(NativeChatTurn(id='turn', workspace_id='ws', user_id='owner', integration_id='engine',
            identity_id='identity', conversation_id='chat', native_chat_id='native-chat', user_message_id='user-message',
            assistant_message_id='assistant-message', model_id='fixture-model', idempotency_key='one-turn',
            request_hash='hash', status='succeeded', task_id='task',
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1)))
        session.add(TaskEvent(task_id='task', type='state_changed', data={'status': 'succeeded'}))
        session.add(Notification(workspace_id='ws', user_id='owner', title='用户私有对话',
            resource_type='task', resource_id='task'))
        session.commit()
        actor = Actor('owner', 'ws', frozenset({'m01.read', 'm02.read', 'm15.admin'}))
        deleted = []
        vault = SimpleNamespace(delete=lambda *args, **kwargs: deleted.append(args))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            settings=Settings(data_dir=tmp_path / 'configuration'), secret_vault=vault)))
        yield session, actor, request, deleted
    engine.dispose()


def test_owner_can_read_complete_task_and_events(context):
    session, actor, _, _ = context
    assert TaskService(session, actor).get('task')['status'] == 'succeeded'
    assert events('task', 0, actor, session)['items'][0]['data'] == {'status': 'succeeded'}


@pytest.mark.parametrize('admin', [False, True])
def test_same_workspace_permission_and_admin_do_not_read_another_chat(context, admin):
    session, _, _, _ = context
    actor = Actor('other', 'ws', frozenset({'*'} if admin else {'m01.read', 'm02.read'}), is_admin=admin)
    for operation in (lambda: TaskService(session, actor).get('task'), lambda: events('task', 0, actor, session)):
        with pytest.raises(DomainError) as failure:
            operation()
        assert failure.value.status == 403
    assert tasks(actor=actor, session=session)['total'] == 0


@pytest.mark.parametrize('change', ['task_turn', 'turn_task', 'chat_identity', 'identity_owner', 'native_chat'])
def test_broken_bidirectional_binding_is_hidden(context, change):
    session, actor, _, _ = context
    if change == 'task_turn':
        session.get(Task, 'task').inputs = {'turn_id': 'missing', 'chat_id': 'chat'}
    elif change == 'turn_task':
        session.get(NativeChatTurn, 'turn').task_id = 'different-task'
    elif change == 'chat_identity':
        session.get(NativeConversation, 'chat').identity_id = 'different-identity'
    elif change == 'identity_owner':
        session.get(NativeChatIdentity, 'identity').user_id = 'other'
    else:
        session.get(NativeConversation, 'chat').native_chat_id = 'different-native-chat'
    session.flush()
    with pytest.raises(DomainError) as failure:
        TaskService(session, actor).get('task')
    assert failure.value.code == 'native_chat_task_access_denied'


def test_revoked_inherited_source_hides_task_events_list_and_notification(context, monkeypatch):
    session, actor, _, _ = context
    session.get(NativeConversation, 'chat').inherited_sources = [{'fixture': 'revoked'}]
    session.flush()
    def deny(_session, _actor, sources):
        if sources:
            raise DomainError(403, 'fixture_source_revoked', '来源已撤权')
    monkeypatch.setattr(native_sources, 'authorize_sources', deny)
    with pytest.raises(DomainError, match='来源已撤权'):
        events('task', 0, actor, session)
    assert tasks(actor=actor, session=session)['total'] == 0
    assert notifications(actor, session)['items'] == []


def test_explicit_identity_reference_blocks_secret_delete(context):
    session, actor, request, deleted = context
    session.add_all([ResourceReference(workspace_id='ws', source_type='native_chat_identity', source_id='identity',
        target_type='secret', target_id='secret') for _ in range(2)])
    session.flush()
    with pytest.raises(DomainError) as failure:
        secret_delete('secret', request, actor, session)
    assert failure.value.code == 'secret_in_use' and deleted == []
    assert failure.value.fields['references'] == [{'kind': 'native_chat_identity', 'id': 'identity', 'name': ''}]


def test_signature_key_reference_blocks_secret_delete(context):
    session, actor, request, deleted = context
    session.add(ResourceRecord(id='engine', workspace_id='ws', owner_id='owner', kind='integration_instance',
        name='原生引擎', data={'delegation_jwt_secret_ref': 'signing-secret'}))
    session.flush()
    with pytest.raises(DomainError) as failure:
        secret_delete('signing-secret', request, actor, session)
    assert failure.value.code == 'secret_in_use' and deleted == []


def test_unrelated_reference_does_not_cross_workspace_or_resource_type(context):
    session, actor, request, _ = context
    session.add_all([
        ResourceReference(workspace_id='other-ws', source_type='native_chat_identity', source_id='other',
            target_type='secret', target_id='secret'),
        ResourceReference(workspace_id='ws', source_type='report', source_id='report',
            target_type='file', target_id='secret'),
    ])
    session.flush()
    assert IntegrationService(session, actor, request).credential_references('secret') == []
