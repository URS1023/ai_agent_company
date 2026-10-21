"""隔离配置验证：只新建临时SQLite和内存Vault，不触现有服务或实际数据库。"""

from copy import deepcopy
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, Organization, ResourceRecord, Role, User, Workspace
from agent_platform.core.secrets import SecretVault
from agent_platform.core.settings import Settings
from agent_platform.modules.identity.models import ServiceIdentity
from agent_platform.modules.integrations.delegation_config import validate_delegation_config
from agent_platform.modules.integrations.schemas import IntegrationCreate, IntegrationPatch
from agent_platform.modules.integrations.service import INTEGRATION, IntegrationService


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_MASTER_KEY', Fernet.generate_key().decode())
    engine = create_engine('sqlite:///' + (tmp_path / 'delegation-config.sqlite3').as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Organization(id='org', name='隔离组织'))
        session.add_all([Workspace(id=value, organization_id='org', name=value) for value in ('ws', 'other')])
        session.add_all([User(id=value, organization_id='org', username=value, display_name=value,
            password_hash='unused-placeholder') for value in ('human', 'engine')])
        session.add(Role(id='role', workspace_id='ws', name='引擎', permissions=['m02.read', 'm10.read']))
        session.add(Membership(user_id='engine', workspace_id='ws', role_ids=['role']))
        session.add(ServiceIdentity(id='service-id', user_id='engine', workspace_id='ws', role_id='role',
            name='本地引擎身份', permissions=['m02.read', 'm10.read'], enabled=True, created_by='human'))
        session.flush()
        actor = Actor('human', 'ws', frozenset({'m15.admin'}))
        vault = SecretVault(None, Settings(data_dir=tmp_path, environment='test'))
        secret = vault.put('a' * 64, workspace_id='ws', actor_id='human', session=session)['id']
        data = {'name': '原生实例', 'engine': 'openwebui', 'base_url': 'http://127.0.0.1:13000',
                'console_url': 'http://127.0.0.1:13000', 'delegation_service_identity_id': 'engine',
                'delegation_jwt_secret_ref': secret}
        yield SimpleNamespace(session=session, engine=engine, actor=actor, vault=vault, data=data)
    engine.dispose()


def check(context, data=None, actor=None):
    return validate_delegation_config(context.session, actor or context.actor,
        context.data if data is None else data, vault=context.vault)


def test_create_and_merged_patch_accept_refs_without_exposing_secret(context):
    data = IntegrationCreate.model_validate(context.data).model_dump(exclude_none=True)
    assert check(context, data) is None
    patch = IntegrationPatch(revision=1, name='更名').model_dump(exclude_unset=True, exclude={'revision'})
    assert check(context, {**data, **patch}) is None
    assert 'a' * 64 not in str(data)
    clear = IntegrationPatch(revision=1, delegation_service_identity_id=None,
        delegation_jwt_secret_ref=None).model_dump(exclude_unset=True, exclude={'revision'})
    assert check(context, {**data, **clear}) is None


def test_existing_integration_can_keep_its_own_identity_on_update(context):
    row = ResourceRecord(id='instance', workspace_id='ws', kind='integration_instance',
        owner_id='human', name='原实例', data=context.data)
    context.session.add(row)
    context.session.flush()
    assert check(context, {**context.data, 'id': row.id}) is None
    with pytest.raises(DomainError) as error:
        check(context)
    assert error.value.code == 'delegation_identity_already_bound'


@pytest.mark.parametrize('fault', ['one_ref', 'wrong_engine', 'human_id', 'service_pk', 'foreign', 'disabled_identity',
                                  'disabled_user', 'disabled_membership', 'missing_secret', 'foreign_secret', 'short_secret'])
def test_invalid_binding_fails_closed_with_field_feedback(context, fault):
    data = deepcopy(context.data)
    session = context.session
    if fault == 'one_ref': data.pop('delegation_jwt_secret_ref')
    if fault == 'wrong_engine': data['engine'] = 'dify'
    if fault == 'human_id': data['delegation_service_identity_id'] = 'human'
    if fault == 'service_pk': data['delegation_service_identity_id'] = 'service-id'
    if fault == 'foreign': session.get(ServiceIdentity, 'service-id').workspace_id = 'other'
    if fault == 'disabled_identity': session.get(ServiceIdentity, 'service-id').enabled = False
    if fault == 'disabled_user': session.get(User, 'engine').disabled = True
    if fault == 'disabled_membership': session.scalar(select(Membership)).disabled = True
    if fault == 'missing_secret': data['delegation_jwt_secret_ref'] = 'missing'
    if fault == 'foreign_secret':
        data['delegation_jwt_secret_ref'] = context.vault.put('s' * 64, workspace_id='other', actor_id='human', session=session)['id']
    if fault == 'short_secret':
        data['delegation_jwt_secret_ref'] = context.vault.put('short-value', workspace_id='ws', actor_id='human', session=session)['id']
    session.flush()
    with pytest.raises(DomainError) as error:
        check(context, data)
    assert error.value.status == 422 and error.value.fields
    assert 'a' * 64 not in str(error.value.fields) + error.value.message


def test_configuration_and_clear_require_management_permission_but_unrelated_data_does_not(context):
    ordinary = Actor('human', 'ws', frozenset({'m15.write'}))
    for data in (context.data, {'delegation_service_identity_id': None, 'delegation_jwt_secret_ref': None}):
        with pytest.raises(DomainError) as error:
            check(context, data, ordinary)
        assert error.value.status == 403
    assert check(context, {'engine': 'openwebui', 'name': '没有委托的普通集成'}, ordinary) is None


def configuration_service(context, actor=None):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=Settings(data_dir=context.engine.url.database + '-settings', environment='test'),
        secret_vault=context.vault)))
    return IntegrationService(context.session, actor or context.actor, request)


def test_production_service_create_partial_patch_and_clear_use_the_complete_pair(context):
    service = configuration_service(context)
    created = service.create(INTEGRATION, IntegrationCreate.model_validate(context.data).model_dump(exclude_none=True))
    renamed = service.update(INTEGRATION, created['id'], {'name': '已改名'}, created['revision'])
    assert renamed['delegation_service_identity_id'] == 'engine' and renamed['revision'] == 2
    replacement = context.vault.put('b' * 64, workspace_id='ws', actor_id='human', session=context.session)['id']
    changed = service.update(INTEGRATION, created['id'], {'delegation_jwt_secret_ref': replacement}, renamed['revision'])
    assert changed['delegation_service_identity_id'] == 'engine' and changed['delegation_jwt_secret_ref'] == replacement
    cleared = service.update(INTEGRATION, created['id'], {
        'delegation_service_identity_id': None, 'delegation_jwt_secret_ref': None}, changed['revision'])
    assert cleared['delegation_service_identity_id'] is None and cleared['delegation_jwt_secret_ref'] is None
    assert 'a' * 64 not in str(cleared) and 'b' * 64 not in str(changed)


def test_production_service_rejects_m15_write_for_delegation_configuration(context):
    ordinary = configuration_service(context, Actor('human', 'ws', frozenset({'m15.write'})))
    with pytest.raises(DomainError) as error:
        ordinary.create(INTEGRATION, context.data)
    assert error.value.status == 403
    created = configuration_service(context).create(INTEGRATION, context.data)
    with pytest.raises(DomainError) as error:
        ordinary.update(INTEGRATION, created['id'], {
            'delegation_service_identity_id': None, 'delegation_jwt_secret_ref': None}, created['revision'])
    assert error.value.status == 403
    current = context.session.get(ResourceRecord, created['id'])
    assert current.revision == created['revision'] and current.data['delegation_service_identity_id'] == 'engine'
