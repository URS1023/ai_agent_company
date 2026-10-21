"""只用新临时SQLite、内存签名和ASGI替身，验证工具委托、来源撤权与固定桥契约。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import select
from starlette.datastructures import Headers

from agent_platform.core.context import Actor, resolve_actor
from agent_platform.core.database import Database
from agent_platform.core.errors import DomainError
from agent_platform.core.models import (
    FileEntry, LoginSession, Membership, Organization, ResourceGrant, ResourceRecord,
    Role, Task, User, Workspace,
)
from agent_platform.core.secrets import SecretVault
from agent_platform.core.settings import Settings
from agent_platform.modules.chat.native_models import NativeChatIdentity, NativeChatTurn, NativeConversation
from agent_platform.modules.chat.native_sources import append_turn_sources, authorize_conversation_sources
from agent_platform.modules.data.row_policies import RowPolicyService
from agent_platform.modules.data.schemas import RowPolicyInput, RowPolicyPatch
from agent_platform.modules.data.service import DataService
from agent_platform.modules.identity.client_certificate_models import ServiceCertificateTokenBinding, ServiceClientCertificate
from agent_platform.modules.identity.models import ServiceAccess, ServiceIdentity
from agent_platform.modules.identity.service_accounts import resolve_service_actor
from agent_platform.modules.integrations.delegated_contract import HEADERS, OPERATIONS, PREFIX, delegated_tool_spec
from agent_platform.modules.integrations.business_tools import READ_TOOLS
from agent_platform.modules.integrations.delegated_router import router
from agent_platform.modules.integrations.delegated_sources import capture_source
from agent_platform.modules.integrations.delegated_tools import authorize_delegated_source, execute_tool, parameters
from agent_platform.modules.integrations.delegation_auth import _signed_subject, resolve_delegated_actor
from agent_platform.modules.manufacturing import models as manufacturing
from agent_platform.modules.reports.models import Report
from agent_platform.modules.reports.inspection_source import InspectionSource

SCOPES = ['m02.read', 'm10.read', 'm11.read', 'm07.read', 'm06.read']
SIGNING_KEY = 'isolated-unit-signing-key-' + 'a' * 40
ENGINE_TOKEN = 'isolated-certificate-service-access'


@pytest.fixture
def site(tmp_path, monkeypatch):
    """新建工作区、人类角色、证书服务身份和精确原生消息预约，不继承在用配置。"""
    monkeypatch.setenv('AGENT_MASTER_KEY', Fernet.generate_key().decode())
    settings = Settings(data_dir=tmp_path / 'new-platform', environment='test')
    database = Database(settings)
    database.initialize()
    app = FastAPI()
    app.state.database = database
    app.state.secret_vault = SecretVault(database, settings)
    app.state.service_actor_resolver = resolve_service_actor
    app.include_router(router, prefix='/api/v1')

    @app.exception_handler(DomainError)
    async def error_handler(request, error):
        return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status)

    expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with database.sessions.begin() as session:
        session.add(Organization(id='org', name='隔离企业'))
        session.flush()
        session.add_all([Workspace(id=key, organization_id='org', name=key) for key in ('ws', 'other-ws')])
        session.add_all([User(id=key, organization_id='org', username=key, display_name=key,
            password_hash='unused-test-placeholder') for key in ('human', 'engine', 'other-human')])
        session.flush()
        session.add_all([Role(id=key, workspace_id='ws', name=key, permissions=SCOPES) for key in ('human-role', 'engine-role')])
        session.flush()
        session.add_all([Membership(user_id=key, workspace_id='ws', role_ids=[key + '-role']) for key in ('human', 'engine')])
        session.add(LoginSession(id='engine-login', token_hash=sha256(ENGINE_TOKEN.encode()).hexdigest(),
            user_id='engine', workspace_id='ws', csrf_token='unused', expires_at=expiry))
        session.add(ServiceIdentity(id='engine-service', workspace_id='ws', user_id='engine', role_id='engine-role',
            name='本机引擎', permissions=SCOPES, enabled=True, created_by='human'))
        session.flush()
        session.add(ServiceAccess(id='engine-access', workspace_id='ws', identity_id='engine-service',
            session_id='engine-login', name='证书令牌', scopes=SCOPES, expires_at=expiry, created_by='human'))
        session.add(ServiceClientCertificate(id='engine-cert', workspace_id='ws', identity_id='engine-service',
            name='单元测试证书', certificate_pem='not-used-for-jwt-validation', fingerprint_sha256='a' * 64,
            subject_text='测试', issuer_text='测试', serial_number='1', algorithm='ES256',
            not_before=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), not_after=expiry, created_by='human'))
        session.flush()
        session.add(ServiceCertificateTokenBinding(workspace_id='ws', certificate_id='engine-cert', service_access_id='engine-access'))
        ref = app.state.secret_vault.put(SIGNING_KEY, workspace_id='ws', actor_id='human', session=session)['id']
        session.add(ResourceRecord(id='instance', workspace_id='ws', kind='integration_instance', name='原生内核', owner_id='human',
            data={'engine': 'openwebui', 'enabled': True, 'base_url': 'http://127.0.0.1:13000',
                  'delegation_service_identity_id': 'engine', 'delegation_jwt_secret_ref': ref}))
        session.add(NativeChatIdentity(id='native-identity', workspace_id='ws', user_id='human', integration_id='instance',
            integration_revision=1, integration_url='http://127.0.0.1:13000', native_user_id='native-human', status='ready'))
        session.add(NativeConversation(id='conversation', workspace_id='ws', user_id='human', integration_id='instance',
            identity_id='native-identity', native_chat_id='native-chat', request_hash='a' * 64,
            status='ready', active_turn_id='turn'))
        session.add(NativeChatTurn(id='turn', workspace_id='ws', user_id='human', integration_id='instance',
            identity_id='native-identity', conversation_id='conversation', native_chat_id='native-chat',
            user_message_id='user-message', assistant_message_id='assistant-message', model_id='mock-model',
            idempotency_key='turn-intent', request_hash='a' * 64, status='running',
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=90)))
        session.add_all([manufacturing.Device(id='device-one', workspace_id='ws', name='循环泵01'),
                         manufacturing.Device(id='device-hidden', workspace_id='other-ws', name='别的工作区设备')])
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, db=database, settings=settings)
    database.dispose()


def signed(**changes):
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode({'sub': 'native-human', 'iss': 'open-webui', 'iat': now, 'exp': now + 120, **changes}, SIGNING_KEY, algorithm='HS256')


def headers(**changes):
    return {'Authorization': 'Bearer ' + ENGINE_TOKEN, HEADERS[0]: signed(),
            HEADERS[1]: 'native-chat', HEADERS[2]: 'assistant-message', **changes}


def call(site, operation='get_device', params=None, supplied=None):
    return site.client.get(PREFIX + '/' + operation, params=params or {'id': 'device-one'},
                           headers=headers() if supplied is None else supplied)


def human(session):
    return resolve_actor(session, LoginSession(user_id='human', workspace_id='ws'))


def test_full_proof_reads_real_domain_service_and_appends_no_body_source(site):
    response = call(site)
    assert response.status_code == 200, response.text
    assert response.json()['name'] == '循环泵01'
    assert response.headers['cache-control'] == 'no-store'
    with site.db.sessions() as session:
        sources = session.get(NativeChatTurn, 'turn').sources
        assert sources == [{'version': 1, 'workspace_id': 'ws', 'resource': {'kind': 'device', 'id': 'device-one'}, 'queries': [], 'files': []}]
        assert '循环泵01' not in str(sources) and SIGNING_KEY not in str(sources)


@pytest.mark.parametrize('fault', ['jwt', 'chat', 'message', 'plain_user'])
def test_missing_or_plain_identity_never_falls_back_to_engine_actor(site, fault):
    supplied = headers()
    if fault == 'jwt': supplied.pop(HEADERS[0])
    if fault == 'chat': supplied[HEADERS[1]] = 'other-chat'
    if fault == 'message': supplied[HEADERS[2]] = 'other-message'
    if fault == 'plain_user':
        supplied.pop(HEADERS[0])
        supplied['X-OpenWebUI-User-Id'] = 'native-human'
    response = call(site, supplied=supplied)
    assert response.status_code in {401, 403}
    with site.db.sessions() as session:
        assert session.get(NativeChatTurn, 'turn').sources == []


@pytest.mark.parametrize('fault', ['token_binding', 'certificate', 'identity', 'human', 'membership', 'role', 'engine_role', 'turn', 'active_turn', 'instance'])
def test_current_revocation_and_binding_changes_stop_tools(site, fault):
    with site.db.sessions.begin() as session:
        if fault == 'token_binding': session.delete(session.scalar(select(ServiceCertificateTokenBinding)))
        if fault == 'certificate': session.get(ServiceClientCertificate, 'engine-cert').enabled = False
        if fault == 'identity': session.get(ServiceIdentity, 'engine-service').enabled = False
        if fault == 'human': session.get(User, 'human').disabled = True
        if fault == 'membership': session.scalar(select(Membership).where(Membership.user_id == 'human')).disabled = True
        if fault == 'role': session.get(Role, 'human-role').permissions = ['m02.read']
        if fault == 'engine_role': session.get(Role, 'engine-role').permissions = ['m02.read']
        if fault == 'turn': session.get(NativeChatTurn, 'turn').status = 'succeeded'
        if fault == 'active_turn': session.get(NativeConversation, 'conversation').active_turn_id = None
        if fault == 'instance':
            row = session.get(ResourceRecord, 'instance')
            row.data = {**row.data, 'delegation_service_identity_id': 'someone-else'}
    response = call(site)
    assert response.status_code in {401, 403, 503}, response.text
    assert '循环泵01' not in response.text


def test_human_permissions_are_intersected_and_identity_is_not_a_model_parameter(site):
    with site.db.sessions() as session:
        engine = Actor('engine', 'ws', frozenset(SCOPES + ['m14.admin']), session_id='engine-login')
        request = SimpleNamespace(headers=Headers(headers()), app=site.app)
        actor, context = resolve_delegated_actor(session, engine, request)
        assert actor.user_id == 'human' and not actor.is_admin and not actor.can('m14.admin')
        assert context['turn_id'] == 'turn'
    assert call(site, params={'id': 'device-one', 'user_id': 'engine'}).status_code == 422
    assert call(site, params={'id': 'device-hidden'}).status_code == 404


@pytest.mark.parametrize('claims', [
    {'exp': 1}, {'iss': 'other'}, {'sub': 'other-native-user'}, {'sub': 'native-human', 'iat': 0, 'exp': 400},
])
def test_wrong_signed_subject_or_invalid_lifetime_is_rejected(site, claims):
    assert call(site, supplied=headers(**{HEADERS[0]: signed(**claims)})).status_code in {401, 403}


def test_only_current_page_items_are_returned_or_frozen_without_hidden_counts(site):
    with site.db.sessions.begin() as session:
        session.add(manufacturing.Device(id='device-two', workspace_id='ws', name='循环泵02'))
    result = call(site, 'list_devices', {'limit': '1', 'page': '1'})
    assert result.status_code == 200, result.text
    assert set(result.json()) == {'items', 'returned_count'} and result.json()['returned_count'] == 1
    assert 'total' not in result.json() and 'has_more' not in result.json()
    with site.db.sessions() as session:
        source = session.get(NativeChatTurn, 'turn').sources
        assert len(source) == 1 and source[0]['resource']['id'] == result.json()['items'][0]['id']
    empty = call(site, 'list_devices', {'limit': '1', 'page': '99'})
    assert empty.json() == {'items': [], 'returned_count': 0}


def test_all_fifteen_operations_use_saved_domain_records(site):
    """逐个执行固定工具，覆盖检验历史、预警事件及报表，不借助模拟返回值凑接口数。"""
    with site.db.sessions.begin() as session:
        session.add(manufacturing.Product(id='product', workspace_id='ws', name='样品食品'))
        session.add(manufacturing.InspectionTemplate(id='template', workspace_id='ws', name='固定检验标准',
            target_type='product', metrics=[]))
        session.add(manufacturing.AlertRule(id='rule', workspace_id='ws', device_id='device-one', name='温度预警',
            operator='gt', threshold='80', unit='C'))
        session.flush()
        session.add(manufacturing.ProductBatch(id='batch', workspace_id='ws', product_id='product', name='样品批次'))
        session.add(manufacturing.Inspection(id='inspection', workspace_id='ws', name='样品质检',
            target_type='product', template_id='template', template_revision=1, standard_snapshot={'revision': 1}, result={}))
        session.add(manufacturing.MaintenanceAnalysis(id='analysis', workspace_id='ws', device_id='device-one',
            name='温度分析', status='insufficient_data', request_snapshot={}, result={}, provenance={}))
        session.add(manufacturing.Incident(id='incident', workspace_id='ws', rule_id='rule', device_id='device-one', name='温度异常',
            first_observed_at=datetime.now(timezone.utc), last_observed_at=datetime.now(timezone.utc), evidence={}))
        session.add(Report(id='report', workspace_id='ws', name='静态报告', layout={'widgets': []}))
        session.add(Task(id='inspection-task', workspace_id='ws', user_id='human', kind='inspection', title='检验运行',
            access_permission='m11.read', status='waiting_review', inputs={'inspection_id': 'inspection',
            'template_id': 'template', 'template_revision': 1}, outputs={'run_id': 'inspection-run'}))
        session.flush()
        session.add(manufacturing.InspectionRun(id='inspection-run', workspace_id='ws', inspection_id='inspection',
            task_id='inspection-task', result={'run_id': 'inspection-run', 'standard_revision': 1, 'samples': []}))
        session.add(manufacturing.IncidentEvent(id='event', incident_id='incident', action='acknowledge',
            note='已安排核查', actor_id='human'))
    detail_ids = {'get_device': 'device-one', 'get_maintenance_analysis': 'analysis', 'get_incident': 'incident',
                  'get_product_batch': 'batch', 'get_inspection': 'inspection', 'get_inspection_result': 'inspection',
                  'list_inspection_runs': 'inspection', 'get_report': 'report'}
    for operation in (entry[1] for entry in READ_TOOLS.values()):
        values = {'id': detail_ids[operation]} if operation in detail_ids else {'limit': '1'}
        response = call(site, operation, values)
        assert response.status_code == 200, (operation, response.text)
        if operation.startswith('list_'):
            assert set(response.json()) == {'items', 'returned_count'} and response.json()['returned_count'] == 1
        if operation == 'get_incident':
            assert response.json()['events'][0]['note'] == '已安排核查'
    with site.db.sessions() as session:
        sources = session.get(NativeChatTurn, 'turn').sources
        assert {source['resource']['kind'] for source in sources} == {
            'device', 'maintenance_analysis', 'incident', 'product', 'product_batch', 'inspection', 'inspection_run', 'report'}
    with site.db.sessions.begin() as session:
        actor = human(session)
        run = session.get(manufacturing.InspectionRun, 'inspection-run')
        run.result = {**run.result, 'execution_task_id': 'inspection-task'}
        session.flush()
        report = session.get(Report, 'report')
        report.layout = {'kind': 'inspection_report', 'widgets': [],
                         'inspection_source': InspectionSource(session, actor).capture('inspection', run.id)}
        session.flush()
        _, formal_sources = execute_tool(session, actor, 'get_report', {'id': 'report'})
        assert [source['resource']['kind'] for source in formal_sources] == ['report', 'inspection_run']
        # 后来的普通报表布局不能移除旧聊天对检验读权限的依赖。
        report.layout = {'widgets': []}
        session.get(Role, 'human-role').permissions = ['m02.read', 'm07.read']
        session.flush()
        fresh = human(session)
        authorize_delegated_source(session, fresh, formal_sources[0])
        with pytest.raises(DomainError) as error:
            authorize_delegated_source(session, fresh, formal_sources[1])
        assert error.value.status == 403


def test_duplicate_identity_header_and_non_hs256_signature_are_rejected(site):
    supplied = list(headers().items()) + [(HEADERS[1], 'second-chat')]
    response = site.client.get(PREFIX + '/get_device', params={'id': 'device-one'}, headers=supplied)
    assert response.status_code == 401
    now = int(datetime.now(timezone.utc).timestamp())
    wrong_algorithm = jwt.encode({'sub': 'native-human', 'iss': 'open-webui', 'iat': now, 'exp': now + 120},
                                 SIGNING_KEY, algorithm='HS384')
    assert call(site, supplied=headers(**{HEADERS[0]: wrong_algorithm})).status_code == 401


def test_history_uses_current_permissions_and_expired_turn_cannot_append(site):
    assert call(site).status_code == 200
    with site.db.sessions.begin() as session:
        session.get(Role, 'human-role').permissions = ['m02.read']
        session.flush()
        with pytest.raises(DomainError) as error:
            authorize_conversation_sources(session, human(session), session.get(NativeConversation, 'conversation'))
        assert error.value.status == 403
        session.get(Role, 'human-role').permissions = SCOPES
        session.flush()
        engine = Actor('engine', 'ws', frozenset(SCOPES), session_id='engine-login')
        actor, context = resolve_delegated_actor(session, engine, SimpleNamespace(headers=Headers(headers()), app=site.app))
        _, receipts = execute_tool(session, actor, 'get_device', {'id': 'device-one'})
        turn = session.get(NativeChatTurn, 'turn')
        original = deepcopy(turn.sources)
        turn.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.flush()
        with pytest.raises(DomainError) as error:
            append_turn_sources(session, context, receipts)
        assert error.value.status == 403 and turn.sources == original


def test_fixed_schema_does_not_need_a_turn_but_requires_certificate_and_filters_modules(site):
    result = site.client.get(PREFIX + '/openapi.json', headers={'Authorization': 'Bearer ' + ENGINE_TOKEN})
    assert result.status_code == 200 and len(result.json()['paths']) == 17
    assert all(set(methods) == {'get'} for methods in result.json()['paths'].values())
    actor = Actor('engine', 'ws', frozenset({'m02.read', 'm10.read'}))
    spec = delegated_tool_spec(actor)
    assert len(spec['paths']) == 6 and 'get_report' not in str(spec)
    schema = spec['paths'][PREFIX + '/list_devices']['get']['responses']['200']['content']['application/json']['schema']
    assert set(schema['properties']) == {'items', 'returned_count'}


@pytest.mark.parametrize('operation,params', [
    ('unknown', {}), ('get_device', {'id': 'device-one', 'sql': 'SELECT 1'}),
    ('list_devices', {'limit': '21'}), ('list_devices', {'page': '0'}),
    ('get_device', {}), ('get_device', {'id': '../secret'}),
])
def test_operation_and_parameter_allowlist(operation, params):
    with pytest.raises(DomainError):
        parameters(operation, params)


def test_immutable_query_scope_is_user_bound_and_history_does_not_open_source(site, monkeypatch):
    with site.db.sessions.begin() as session:
        actor = human(session)
        data = DataService(session, actor, site.settings)
        imported = data.import_csv({'name': '隔离产线', 'csv_text': 'sample_id,line,value\nA1,A,10\nB1,B,99\n'})
        source_id, table = imported['datasource_id'], imported['table_name']
        rules = [{'subject_type': 'user', 'subject_ids': ['human'],
                  'conditions': [{'field': 'line', 'operator': 'eq', 'value': 'A'}]}]
        admin = Actor('human', 'ws', frozenset({'*'}), ('admin',), is_admin=True)
        policy = RowPolicyService(session, admin).create(source_id, RowPolicyInput(table_name=table, mode='rules', rules=rules).model_dump())
        saved = data.execute_query({'datasource_id': source_id, 'sql': f'SELECT sample_id,value FROM "{table}"', 'parameters': {}, 'max_rows': 10})
        device = session.get(manufacturing.Device, 'device-one')
        analysis = manufacturing.MaintenanceAnalysis(id='analysis', workspace_id='ws', device_id=device.id,
            name='只读分析', status='succeeded', request_snapshot={}, result={}, provenance={'query_id': saved['id']})
        session.add(analysis)
        session.flush()
        payload, sources = execute_tool(session, actor, 'get_maintenance_analysis', {'id': 'analysis'})
        receipt = sources[0]
        assert receipt['queries'][0]['scope_hash'] and payload['provenance']['query_id'] == saved['id']
        monkeypatch.setattr(DataService, '_path', lambda *_: pytest.fail('历史来源重授权不能打开业务源'))
        authorize_delegated_source(session, actor, receipt)
        # 当前对象换成无查询的新结果，旧对话仍必须核被冻结的A行范围。
        analysis.provenance = {}
        changed = deepcopy(rules)
        changed[0]['conditions'][0]['value'] = 'B'
        RowPolicyService(session, admin).patch(policy.id, RowPolicyPatch(revision=policy.revision, mode='rules', rules=changed).model_dump())
        with pytest.raises(DomainError) as denied:
            authorize_delegated_source(session, actor, receipt)
        assert denied.value.status == 403


def test_file_grant_revocation_cannot_be_laundered_by_later_report_layout(site):
    with site.db.sessions.begin() as session:
        actor = human(session)
        session.add(FileEntry(id='image', workspace_id='ws', owner_id='other-human', visibility='private',
            name='包装示意.png', storage_key='not-opened', content_type='image/png', size=10, sha256='b' * 64))
        grant = ResourceGrant(workspace_id='ws', user_id='human', resource_type='file', resource_id='image', actions=['read'])
        session.add(grant)
        report = Report(id='image-report', workspace_id='ws', name='原示意', layout={'widgets': [
            {'id': 'widget', 'component': 'JImg', 'config': {'file_id': 'image'}}]})
        session.add(report)
        session.flush()
        _, sources = execute_tool(session, actor, 'get_report', {'id': report.id})
        assert sources[0]['files'] == [{'id': 'image', 'revision': 1, 'sha256': 'b' * 64}]
        report.layout = {'widgets': []}
        session.delete(grant)
        session.flush()
        with pytest.raises(DomainError):
            authorize_delegated_source(session, actor, sources[0])


@pytest.mark.parametrize('change', [{'workspace_id': 'other-ws'}, {'version': 2}, {'body': '不能接受正文'}])
def test_malformed_or_foreign_source_fails_closed(site, change):
    with site.db.sessions() as session:
        actor = human(session)
        receipt = capture_source(session, actor, 'device', 'device-one', {})
        with pytest.raises(DomainError):
            authorize_delegated_source(session, actor, {**receipt, **change})
