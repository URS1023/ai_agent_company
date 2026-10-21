"""隔离SQLite与Mock验证普通模型组接入；不访问原生服务或模型。"""

from copy import deepcopy
import json
from datetime import timedelta

import httpx
import jwt
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from test_native_chat_identity import context, NativeFake, KEY
from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import ResourceRecord
from agent_platform.core.models import Task, TaskEvent, Notification
from agent_platform.core.database import Base
from agent_platform.modules.chat.native_models import NativeChatIdentity, NativeConversation, NativeChatTurn, utcnow
from agent_platform.modules.chat.native_service import NativeChatService
from agent_platform.modules.chat.native_turns import NativeTurnService
from agent_platform.modules.chat.native_access import validate_owned_group, NativeModelAccess
from agent_platform.modules.integrations.chat_model_config import validate_chat_model_config, chat_model_policy
from agent_platform.modules.integrations.schemas import IntegrationCreate, IntegrationPatch

GROUP = 'dedicated-group-a'
MODEL = 'ds-fixture'


class AccessFake(NativeFake):
    """模拟官方增量成员API，故意不提供模型授权写入接口。"""
    def __init__(self):
        super().__init__()
        self.members = set()
        self.fail_after_add, self.models_available = False, True
        self.before_membership_read = None
        self.group = {'id': GROUP, 'parent_group_id': None, 'permissions': {}, 'data': {
            'platform': {'purpose': 'chat-model-access', 'workspace_id': 'workspace-a',
                'integration_id': 'instance-a', 'version': 1}, 'config': {'share': False}}}
    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path in {'/api/v1/groups/id/' + GROUP, '/api/v1/groups/id/' + GROUP + '/users/add'}:
            self.calls.append((request.method, path, body, None))
            assert request.headers['authorization'] == 'Bearer bootstrap-fixture'
            if request.method == 'POST':
                assert len(body['user_ids']) == 1 and body['user_ids'][0] in {user['id'] for user in self.users.values()}
                self.members.update(body['user_ids'])
                if self.fail_after_add:
                    raise httpx.ReadTimeout('only-fixture membership accepted but response lost', request=request)
            return httpx.Response(200, json=deepcopy(self.group))
        if path in {'/api/v1/groups/', '/api/models'}:
            self.calls.append((request.method, path, body, None))
            user_id = jwt.decode(request.headers['authorization'].removeprefix('Bearer '), KEY, algorithms=['HS256'])['id']
            if path == '/api/v1/groups/':
                if self.before_membership_read:
                    action, self.before_membership_read = self.before_membership_read, None
                    action()
                return httpx.Response(200, json=[deepcopy(self.group)] if user_id in self.members else [])
            return httpx.Response(200, json={'data': [{'id': MODEL}, {'id': 'another-native-model'}]
                if self.models_available and user_id in self.members else []})
        assert not path.startswith('/api/v1/models/')
        return super().__call__(request)


@pytest.fixture
def access(context):
    session, actor, request, _, engine = context
    fake = AccessFake()
    request.app.state.native_http_transport = httpx.MockTransport(fake)
    record = session.get(ResourceRecord, 'instance-a')
    record.data = {**record.data, 'chat_model_group_id': GROUP, 'chat_model_ids': [MODEL]}
    session.commit()
    return session, actor, request, fake, engine


def service(access):
    return NativeChatService(access[0], access[1], access[2])


def test_new_ordinary_identity_joins_exact_owned_group_before_ready(access):
    identity = service(access).provision_identity('instance-a')
    assert identity['status'] == 'ready'
    row = access[0].get(NativeChatIdentity, identity['id'])
    assert row.native_user_id in access[3].members
    adds = [body for method, path, body, _ in access[3].calls if path.endswith('/users/add')]
    assert adds == [{'user_ids': [row.native_user_id]}]
    assert all(body['role'] == 'user' for _, path, body, _ in access[3].calls if path == '/api/v1/auths/add')
    assert NativeTurnService(access[0], access[1], access[2]).models(row.id) == {'items': [{'id': MODEL, 'name': MODEL}]}
    before = len(adds)
    service(access).provision_identity('instance-a')
    assert len([path for _, path, _, _ in access[3].calls if path.endswith('/users/add')]) == before


def test_existing_ready_identity_explicitly_repairs_membership_but_get_models_is_readonly(access):
    identity = service(access).provision_identity('instance-a')
    access[3].members.clear()
    calls = len(access[3].calls)
    assert NativeTurnService(access[0], access[1], access[2]).models(identity['id']) == {'items': []}
    assert all(method == 'GET' for method, _, _, _ in access[3].calls[calls:])
    assert service(access).reconcile_identity(identity['id'])['status'] == 'ready'
    assert sum(path == '/api/v1/auths/add' for _, path, _, _ in access[3].calls) == 1
    assert sum(path.endswith('/users/add') for _, path, _, _ in access[3].calls) == 2


def test_unknown_membership_is_retained_and_explicit_read_adopts_without_second_write(access):
    access[3].fail_after_add = True
    with pytest.raises(DomainError) as failure:
        service(access).provision_identity('instance-a')
    identity_id = failure.value.fields['identity_id']
    row = access[0].get(NativeChatIdentity, identity_id)
    assert row.status == 'access_pending' and row.error_code == 'native_chat_timeout'
    with pytest.raises(DomainError):
        service(access).provision_identity('instance-a')
    assert service(access).reconcile_identity(identity_id)['status'] == 'ready'
    assert sum(path.endswith('/users/add') for _, path, _, _ in access[3].calls) == 1
    assert sum(path == '/api/v1/auths/add' for _, path, _, _ in access[3].calls) == 1


@pytest.mark.parametrize('change', ['workspace', 'integration', 'purpose', 'version_bool', 'parent', 'permissions', 'share', 'malformed_data'])
def test_wrong_group_marker_or_privilege_is_rejected_before_membership_write(access, change):
    group = access[3].group
    if change in {'workspace', 'integration', 'purpose'}:
        group['data']['platform'][{'workspace': 'workspace_id', 'integration': 'integration_id', 'purpose': 'purpose'}[change]] = 'other'
    elif change == 'version_bool': group['data']['platform']['version'] = True
    elif change == 'parent': group['parent_group_id'] = 'inherited-privileged-group'
    elif change == 'permissions': group['permissions'] = {'workspace': {'tools': True}}
    elif change == 'share': group['data']['config']['share'] = True
    else: group['data'] = ['invalid-shape']
    with pytest.raises(DomainError) as denied:
        service(access).provision_identity('instance-a')
    assert denied.value.code == 'native_chat_model_group_invalid'
    assert access[0].get(NativeChatIdentity, denied.value.fields['identity_id']).status == 'access_pending'
    assert not any(path.endswith('/users/add') for _, path, _, _ in access[3].calls)


def test_model_not_granted_is_not_reported_ready_and_never_rewrites_model_acl(access):
    access[3].models_available = False
    with pytest.raises(DomainError) as denied:
        service(access).provision_identity('instance-a')
    assert denied.value.code == 'native_chat_models_unavailable'
    assert access[0].get(NativeChatIdentity, denied.value.fields['identity_id']).status == 'access_pending'
    assert not any(path.startswith('/api/v1/models/') for _, path, _, _ in access[3].calls)


def test_concurrent_or_active_chat_access_reconciliation_is_not_dispatched_twice(access):
    identity = service(access).provision_identity('instance-a')
    row = access[0].get(NativeChatIdentity, identity['id'])
    integration = service(access).integration('instance-a')
    owner = NativeModelAccess(service(access), row, integration)
    owner.claim(reconcile=True)
    with pytest.raises(DomainError) as conflict:
        NativeModelAccess(service(access), row, integration).claim(reconcile=True)
    assert conflict.value.code == 'native_chat_model_access_requires_review'
    row.status, row.error_code = 'ready', None
    access[0].add(NativeConversation(id='active-chat', workspace_id=row.workspace_id, user_id=row.user_id,
        integration_id=row.integration_id, identity_id=row.id, native_chat_id='native-active',
        request_hash='fixture', status='ready', active_turn_id='running-turn'))
    access[0].commit()
    with pytest.raises(DomainError) as active:
        service(access).provision_identity('instance-a')
    assert active.value.code == 'native_chat_model_access_busy'


def test_admin_policy_dto_and_merge_validation_preserve_unrelated_configuration(access):
    admin = Actor(access[1].user_id, access[1].workspace_id, frozenset({'m15.admin'}))
    data = {'engine': 'openwebui', 'chat_model_group_id': GROUP, 'chat_model_ids': [MODEL]}
    assert validate_chat_model_config(admin, data)['model_ids'] == [MODEL]
    with pytest.raises(DomainError) as denied:
        validate_chat_model_config(access[1], data)
    assert denied.value.status == 403
    assert validate_chat_model_config(access[1], data | {'name': '普通改名'}, existing=data)
    with pytest.raises(DomainError):
        validate_chat_model_config(access[1], data | {'chat_model_group_id': None, 'chat_model_ids': []}, existing=data)
    patch = IntegrationPatch(revision=1, chat_model_group_id=GROUP, chat_model_ids=[MODEL]).model_dump(exclude_unset=True)
    assert patch == {'revision': 1, 'chat_model_group_id': GROUP, 'chat_model_ids': [MODEL]}
    assert IntegrationCreate(name='本机', engine='openwebui', base_url='http://127.0.0.1:13000',
        console_url='http://127.0.0.1:13000').chat_model_ids is None


@pytest.mark.parametrize('group,models,engine', [(None, [MODEL], 'openwebui'), (GROUP, [], 'openwebui'),
    (GROUP, [MODEL, MODEL], 'openwebui'), (GROUP, ['*'], 'openwebui'), (GROUP, ['has space'], 'openwebui'),
    ('http://bad/group', [MODEL], 'openwebui'), (GROUP, [MODEL], 'dify')])
def test_invalid_model_policy_is_rejected_without_network(group, models, engine):
    with pytest.raises(DomainError):
        chat_model_policy({'engine': engine, 'chat_model_group_id': group, 'chat_model_ids': models})


def test_integration_service_calls_config_guard_on_create_and_merged_patch(access):
    from agent_platform.modules.integrations.service import IntegrationService
    configuration = {'engine': 'openwebui', 'chat_model_group_id': GROUP, 'chat_model_ids': [MODEL]}
    with pytest.raises(DomainError) as denied:
        service(access).integrations.validate_config(configuration)
    assert denied.value.status == 403
    admin = Actor(access[1].user_id, access[1].workspace_id, frozenset({'m15.admin'}))
    IntegrationService(access[0], admin, access[2]).validate_config(configuration)
    service(access).integrations.validate_config({'name': '改名'}, existing=configuration)
    with pytest.raises(DomainError):
        service(access).integrations.validate_config({'chat_model_ids': ['other']}, existing=configuration)


def test_token_refresh_during_access_keeps_pending_claim_and_ordinary_role(access):
    current = service(access)
    identity = current.provision_identity('instance-a')
    row = access[0].get(NativeChatIdentity, identity['id'])
    bundle = current._bundle(row)
    bundle['token'] = jwt.encode({'id': row.native_user_id, 'exp': (utcnow() + timedelta(seconds=30)).timestamp()},
        KEY, algorithm='HS256')
    current._save_bundle(row, bundle)
    access[0].commit()
    assert service(access).reconcile_identity(row.id)['status'] == 'ready'
    assert sum(path == '/api/v1/auths/signin' for _, path, _, _ in access[3].calls) == 1
    assert sum(path == '/api/v1/auths/add' for _, path, _, _ in access[3].calls) == 1


def test_policy_change_during_read_blocks_membership_write(access):
    def change():
        with Session(access[4]) as other:
            record = other.get(ResourceRecord, 'instance-a')
            record.data = {**record.data, 'chat_model_ids': ['changed-model']}
            other.commit()
    access[3].before_membership_read = change
    with pytest.raises(DomainError) as conflict:
        service(access).provision_identity('instance-a')
    assert conflict.value.code == 'native_chat_model_policy_changed'
    assert not any(path.endswith('/users/add') for _, path, _, _ in access[3].calls)


def test_database_failure_preserves_pending_attempt_and_does_not_repeat_native_write(access, monkeypatch):
    calls = []
    def failure(self, *args):
        calls.append(True)
        raise SQLAlchemyError('only-fixture')
    monkeypatch.setattr(NativeModelAccess, 'finish', failure)
    with pytest.raises(DomainError) as failed:
        service(access).provision_identity('instance-a')
    assert failed.value.code == 'native_chat_state_unavailable'
    row = access[0].get(NativeChatIdentity, failed.value.fields['identity_id'])
    assert row.status == 'access_pending' and calls == [True]
    assert sum(path.endswith('/users/add') for _, path, _, _ in access[3].calls) == 1
    assert sum(path == '/api/v1/auths/add' for _, path, _, _ in access[3].calls) == 1


def test_turn_reservation_cas_rejects_access_claim_committed_after_its_model_read(access, monkeypatch):
    Base.metadata.create_all(access[4], tables=[model.__table__ for model in (Task, TaskEvent, Notification)])
    current = service(access)
    identity = current.provision_identity('instance-a')
    chat = current.create(identity['id'], idempotency_key='create-race')
    turns = NativeTurnService(access[0], access[1], access[2])
    original = turns.read_native_chat
    def claim_after_read(chat_id):
        result = original(chat_id)
        with Session(access[4]) as other:
            other.execute(update(NativeChatIdentity).where(NativeChatIdentity.id == identity['id']).values(
                status='access_pending', error_code='native_chat_model_access_in_progress'))
            other.commit()
        return result
    monkeypatch.setattr(turns, 'read_native_chat', claim_after_read)
    body = {'revision': chat['revision'], 'model_id': MODEL, 'content': 'Mock only',
        'parent_message_id': None, 'max_tokens': 64, 'temperature': 0, 'attachment_ids': []}
    with pytest.raises(DomainError) as rejected:
        turns.reserve(chat['id'], body, 'model-access-race')
    assert rejected.value.code == 'native_chat_edit_conflict'
    assert list(access[0].scalars(select(NativeChatTurn))) == []
    assert list(access[0].scalars(select(Task))) == []


def test_access_claim_cas_rechecks_active_chat_after_preflight_read(access, monkeypatch):
    current = service(access)
    identity = current.provision_identity('instance-a')
    chat = current.create(identity['id'], idempotency_key='create-race')
    row = access[0].get(NativeChatIdentity, identity['id'])
    claimant = NativeModelAccess(current, row, current.integration('instance-a'))
    original, armed = access[0].execute, [True]
    def execute(statement, *args, **kwargs):
        if armed[0] and getattr(statement, 'is_update', False) and statement.table.name == 'native_chat_identities':
            armed[0] = False
            with Session(access[4]) as other:
                other.execute(update(NativeConversation).where(NativeConversation.id == chat['id']).values(
                    active_turn_id='just-reserved'))
                other.commit()
        return original(statement, *args, **kwargs)
    monkeypatch.setattr(access[0], 'execute', execute)
    before = len(access[3].calls)
    with pytest.raises(DomainError) as rejected:
        claimant.claim(reconcile=True)
    assert rejected.value.code == 'native_chat_model_access_conflict'
    access[0].rollback()
    assert access[0].get(NativeChatIdentity, identity['id']).status == 'ready'
    assert len(access[3].calls) == before


def test_claim_storage_failure_stops_before_any_authorization_http(access, monkeypatch):
    current = service(access)
    identity = current.provision_identity('instance-a')
    row = access[0].get(NativeChatIdentity, identity['id'])
    attempt = NativeModelAccess(current, row, current.integration('instance-a'))
    before = len(access[3].calls)
    def fail(**kwargs):
        raise SQLAlchemyError('only-fixture reservation storage failed')
    monkeypatch.setattr(attempt, 'claim', fail)
    with pytest.raises(DomainError) as failed:
        attempt.prepare(reconcile=True)
    assert failed.value.code == 'native_chat_state_unavailable'
    assert len(access[3].calls) == before
