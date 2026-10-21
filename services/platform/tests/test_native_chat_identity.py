"""临时SQLite、真实Vault加密和MockTransport验证身份/会话，不接原生服务。"""

from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import AuditEvent, ResourceGrant, ResourceRecord, ResourceReference, SecretEntry, SecretVersion, User
from agent_platform.core.secrets import SecretVault
from agent_platform.core.settings import Settings
from agent_platform.modules.chat.native_models import NativeChatIdentity, NativeConversation, NativeChatTurn, NativeTurnAction, utcnow
from agent_platform.modules.chat.native_service import NativeChatService

KEY = 'only-local-mock-signing-key-with-more-than-sixty-four-characters-for-tests'


def token(user_id):
    return jwt.encode({'id': user_id, 'exp': (utcnow() + timedelta(hours=1)).timestamp()}, KEY, algorithm='HS256')


class NativeFake:
    """按官方REST合同保存消息树的替身；此处没有模型推理或工具循环。"""
    def __init__(self):
        self.users, self.chats, self.calls = {}, {}, []
        self.fail_add = False
    def __call__(self, request):
        data = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, deepcopy(data), request.headers.get('authorization')))
        path = request.url.path
        if path == '/api/v1/auths/add':
            assert data['role'] == 'user' and request.headers['authorization'] == 'Bearer bootstrap-fixture'
            result = {'id': str(uuid4()), 'role': 'user', 'email': data['email']}
            self.users[data['email']] = {**result, 'password': data['password']}
            if self.fail_add:
                raise httpx.ReadTimeout('only-fixture', request=request)
            return httpx.Response(200, json={**result, 'token': token(result['id'])})
        if path == '/api/v1/auths/signin':
            user = self.users[data['email']]
            assert user['password'] == data['password']
            return httpx.Response(200, json={'id': user['id'], 'role': user['role'], 'token': token(user['id'])})
        user_id = jwt.decode(request.headers['authorization'].removeprefix('Bearer '), KEY, algorithms=['HS256'])['id']
        if path == '/api/v1/auths/':
            return httpx.Response(200, json={'id': user_id, 'role': 'user'})
        if path == '/api/v1/chats/new':
            chat_id = str(uuid4())
            result = {'id': chat_id, 'user_id': user_id, 'chat': deepcopy(data['chat'])}
            self.chats[chat_id] = result
            return httpx.Response(200, json=result)
        parts = path.strip('/').split('/')
        chat = self.chats[parts[3]]
        if chat['user_id'] != user_id:
            return httpx.Response(401, json={'detail': 'not-found'})
        if request.method == 'DELETE':
            del self.chats[chat['id']]
            return httpx.Response(200, json=True)
        if parts[-1] == 'fork':
            copied = deepcopy(chat)
            copied['id'] = str(uuid4())
            self.chats[copied['id']] = copied
            return httpx.Response(200, json=copied)
        if request.method == 'POST':
            if 'messages' in parts:
                chat['chat']['history']['messages'][parts[-1]]['content'] = data['content']
            else:
                chat['chat'].update(data['chat'])
        return httpx.Response(200, json=chat)


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.delenv('AGENT_MASTER_KEY', raising=False)
    engine = create_engine('sqlite:///' + (tmp_path / 'native-owned.sqlite3').as_posix())
    tables = [model.__table__ for model in (NativeChatIdentity, NativeConversation, NativeChatTurn, NativeTurnAction,
        ResourceRecord, ResourceGrant, ResourceReference, SecretEntry, SecretVersion, AuditEvent, User)]
    Base.metadata.create_all(engine, tables=tables)
    settings = Settings(data_dir=tmp_path / 'owned-platform')
    vault = SecretVault(SimpleNamespace(sessions=sessionmaker(engine)), settings)
    fake = NativeFake()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings, secret_vault=vault,
        native_http_transport=httpx.MockTransport(fake))))
    actor = Actor('platform-user-a', 'workspace-a', frozenset({'m02.*'}))
    with Session(engine, expire_on_commit=False) as session:
        ref = vault.put('bootstrap-fixture', workspace_id=actor.workspace_id, actor_id=actor.user_id, session=session)
        integration = ResourceRecord(id='instance-a', workspace_id=actor.workspace_id, kind='integration_instance',
            owner_id=actor.user_id, name='本机对话', visibility='workspace', data={
                'engine': 'openwebui', 'base_url': 'http://127.0.0.1:13000', 'credential_ref': ref['id'], 'enabled': True})
        session.add(integration)
        session.commit()
        yield session, actor, request, fake, engine
    engine.dispose()


def service(context, actor=None):
    return NativeChatService(context[0], actor or context[1], context[2])


def ready_chat(context, *, key='create-1'):
    current = service(context)
    identity = current.provision_identity('instance-a')
    return current.create(identity['id'], '点检', idempotency_key=key)


def test_identity_is_per_user_workspace_and_instance_without_exposing_native_credentials(context):
    session, actor, request, fake, _ = context
    first = service(context).provision_identity('instance-a')
    assert first['status'] == 'ready' and set(first) == {'id', 'integration_id', 'status', 'error_code', 'revision', 'created_at'}
    assert service(context).provision_identity('instance-a')['id'] == first['id']
    other = Actor('platform-user-b', actor.workspace_id, actor.permissions)
    second = service(context, other).provision_identity('instance-a')
    assert first['id'] != second['id'] and len(fake.users) == 2
    one, two = session.get(NativeChatIdentity, first['id']), session.get(NativeChatIdentity, second['id'])
    assert one.native_user_id != two.native_user_id and one.credential_ref != two.credential_ref
    secret = session.get(SecretEntry, one.credential_ref)
    assert 'password' not in secret.encrypted_value and token(one.native_user_id) not in secret.encrypted_value
    bundle = json.loads(request.app.state.secret_vault.get(one.credential_ref, actor.workspace_id, session=session))
    assert bundle['email'] == 'chat-' + one.id.replace('-', '') + '@localhost'
    assert session.scalar(select(ResourceReference).where(ResourceReference.source_id == one.id)).target_id == one.credential_ref
    with pytest.raises(DomainError) as denied:
        service(context, other).identity(first['id'])
    assert denied.value.status == 404


def test_identity_unknown_never_repeats_add_but_explicit_reconcile_only_signs_in(context):
    fake = context[3]
    fake.fail_add = True
    with pytest.raises(DomainError) as failed:
        service(context).provision_identity('instance-a')
    identity_id = failed.value.fields['identity_id']
    assert context[0].get(NativeChatIdentity, identity_id).status == 'unknown'
    with pytest.raises(DomainError):
        service(context).provision_identity('instance-a')
    assert sum(path == '/api/v1/auths/add' for _, path, _, _ in fake.calls) == 1
    assert service(context).reconcile_identity(identity_id)['status'] == 'ready'
    assert [path for _, path, _, _ in fake.calls] == ['/api/v1/auths/add', '/api/v1/auths/signin']


def test_conversation_creation_uses_same_intent_and_never_accepts_another_users_mapping(context):
    session, actor, _, fake, _ = context
    chat = ready_chat(context)
    identity_id = chat['identity_id']
    assert service(context).create(identity_id, '点检', idempotency_key='create-1')['id'] == chat['id']
    assert sum(path == '/api/v1/chats/new' for _, path, _, _ in fake.calls) == 1
    other = Actor('platform-user-b', actor.workspace_id, actor.permissions)
    for operation in [lambda: service(context, other).detail(chat['id']),
            lambda: service(context, other).create(identity_id),
            lambda: service(context, Actor(actor.user_id, 'workspace-b', actor.permissions)).detail(chat['id'])]:
        with pytest.raises(DomainError) as denied:
            operation()
        assert denied.value.status == 404
    assert service(context, other).list()['items'] == []


def test_history_tree_edit_fork_and_delete_use_exact_native_owner(context):
    session, _, _, fake, _ = context
    chat = ready_chat(context)
    row = session.get(NativeConversation, chat['id'])
    native = fake.chats[row.native_chat_id]
    native['chat']['history'] = {'currentId': 'assistant-a', 'messages': {
        'user-a': {'id': 'user-a', 'role': 'user', 'content': '点检', 'parentId': None, 'childrenIds': ['assistant-a']},
        'assistant-a': {'id': 'assistant-a', 'role': 'assistant', 'content': '记录', 'parentId': 'user-a', 'childrenIds': [], 'done': True}}}
    detail = service(context).detail(chat['id'])
    assert detail['history'] == native['chat']['history'] and 'native_user_id' not in detail
    edited = service(context).edit(chat['id'], chat['revision'], message_id='user-a', content='新问题')
    assert fake.chats[row.native_chat_id]['chat']['history']['messages']['user-a']['content'] == '新问题'
    forked = service(context).fork(chat['id'], edited['revision'], 'assistant-a', idempotency_key='fork-1')
    assert forked['id'] != chat['id'] and service(context).detail(forked['id'])['history']['currentId'] == 'assistant-a'
    assert service(context).delete(forked['id'], forked['revision'])['deleted'] is True
    with pytest.raises(DomainError):
        service(context).detail(forked['id'])


def test_active_turn_blocks_edit_and_stale_revision_cannot_overwrite(context):
    session = context[0]
    chat = ready_chat(context)
    service(context).edit(chat['id'], chat['revision'], title='其他标签已改名')
    with pytest.raises(DomainError) as conflict:
        service(context).edit(chat['id'], chat['revision'], title='过期覆盖')
    assert conflict.value.code == 'native_chat_edit_conflict'
    row = session.get(NativeConversation, chat['id'])
    row.active_turn_id = 'running-turn'
    session.commit()
    with pytest.raises(DomainError):
        service(context).edit(chat['id'], row.revision, title='运行时覆盖')


def test_swapped_native_chat_owner_is_rejected_instead_of_serving_shared_chat(context):
    session, _, _, fake, _ = context
    chat = ready_chat(context)
    row = session.get(NativeConversation, chat['id'])
    fake.chats[row.native_chat_id]['user_id'] = 'someone-else'
    with pytest.raises(DomainError):
        service(context).detail(chat['id'])
