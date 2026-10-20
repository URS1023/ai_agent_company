"""真实API身份与凭据回归；所有数据库均为本轮tmp_path新建SQLite。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sys

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.core.auth import LoginBudget
from agent_platform.core.errors import DomainError
from agent_platform.core.context import Actor
from agent_platform.core.models import LoginSession, Membership, Role, SecretEntry, SecretRotationTicket, SecretVersion, SystemState, Workspace
from agent_platform.core.settings import Settings
from agent_platform.main import create_app
from agent_platform.modules.identity.models import ServiceAccess

PASSWORD = 'Local-test-password-2026!'


@pytest.fixture
def site(tmp_path, monkeypatch):
    # 测试主密钥只用于该新平台文件，不读取现有系统.env或业务连接。
    monkeypatch.setenv('AGENT_MASTER_KEY', Fernet.generate_key().decode())
    settings = Settings(data_dir=tmp_path, environment='test', enabled_modules=('identity', 'catalog', 'integrations', 'files', 'workspace'))
    app = create_app(settings)
    with TestClient(app) as admin:
        result = admin.post('/api/v1/auth/setup', json={'username': 'admin', 'password': PASSWORD,
            'display_name': '测试管理员', 'workspace_name': '甲车间', 'organization_name': '独立测试企业'})
        assert result.status_code == 201, result.text
        yield app, admin, result.json()


def headers(client):
    result = client.get('/api/v1/me')
    assert result.status_code == 200, result.text
    return {'X-CSRF-Token': result.json()['csrf_token']}


def post(client, path, data):
    return client.post('/api/v1' + path, json=data, headers=headers(client))


def patch(client, path, data):
    return client.patch('/api/v1' + path, json=data, headers=headers(client))


def role(admin, name, permissions):
    result = post(admin, '/roles', {'name': name, 'permissions': permissions})
    assert result.status_code == 201, result.text
    return result.json()


def member(admin, username, roles, password=PASSWORD):
    result = post(admin, '/members', {'username': username, 'password': password,
        'display_name': username, 'role_ids': roles})
    assert result.status_code == 201, result.text
    return result.json()


def login(app, username, password=PASSWORD):
    client = TestClient(app)
    result = client.post('/api/v1/auth/login', json={'username': username, 'password': password})
    assert result.status_code == 200, result.text
    return client


def test_service_internal_roles_and_members_are_only_managed_through_service_identity(site):
    app, admin, _ = site
    created = post(admin, "/service-identities", {
        "name": "设备查询集成", "permissions": ["m02.read", "m10.read"], "caller_kind": "integration",
    })
    assert created.status_code == 201
    identity = created.json()
    with app.state.database.sessions() as session:
        service_member = session.scalar(select(Membership).where(Membership.user_id == identity["user_id"]))
        service_role_id = service_member.role_ids[0]
        member_id, member_revision = service_member.id, service_member.revision
    assert service_role_id not in {row["id"] for row in admin.get("/api/v1/roles").json()["items"]}
    assert identity["user_id"] not in {row["user_id"] for row in admin.get("/api/v1/members").json()["items"]}
    assert patch(admin, "/roles/" + service_role_id, {"revision": 1, "permissions": ["*"]}).json()["error"]["code"] == "service_role_managed"
    assert patch(admin, "/members/" + member_id, {"revision": member_revision, "disabled": True}).json()["error"]["code"] == "service_member_managed"
    assigned = post(admin, "/members", {
        "username": "person", "display_name": "正常成员", "password": PASSWORD, "role_ids": [service_role_id],
    })
    assert assigned.status_code == 422
    assert assigned.json()["error"]["code"] == "service_role_not_assignable"
    changed = patch(admin, "/service-identities/" + identity["id"], {
        "revision": identity["revision"], "permissions": ["m02.read"],
    })
    assert changed.status_code == 200
    with app.state.database.sessions() as session:
        assert session.get(Role, service_role_id).permissions == ["m02.read"]


def test_setup_claim_is_persistent_and_repeat_initialization_cannot_overwrite(site):
    app, admin, original = site
    repeated = admin.post('/api/v1/auth/setup', json={'username': 'otheradmin', 'password': PASSWORD,
        'display_name': '新管理员', 'workspace_name': '覆盖工作区'})
    assert repeated.status_code == 409
    assert admin.get('/api/v1/me').json()['user']['id'] == original['user']['id']
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SystemState)) == 1


def test_cookie_csrf_and_browser_token_bearer_bypass_are_blocked(site):
    app, admin, _ = site
    denied = admin.post('/api/v1/roles', json={'name': '无CSRF', 'permissions': []})
    assert denied.status_code == 403 and denied.json()['error']['code'] == 'csrf_failed'
    raw = admin.cookies.get('agent_session')
    bypass = admin.post('/api/v1/roles', json={'name': '绕过尝试', 'permissions': []}, headers={'Authorization': f'Bearer {raw}'})
    assert bypass.status_code == 401 and bypass.json()['error']['code'] == 'browser_token_bearer_denied'
    assert post(admin, '/roles', {'name': '正常请求', 'permissions': []}).status_code == 201


def test_limited_admin_cannot_grant_wildcards_or_assign_existing_broad_role(site):
    app, admin, _ = site
    limited_role = role(admin, '有限管理员', ['m14.admin', 'm14.read', 'm01.read'])
    member(admin, 'limited', [limited_role['id']])
    limited = login(app, 'limited')
    assert post(limited, '/roles', {'name': '越权角色', 'permissions': ['*']}).status_code == 403
    assert post(limited, '/roles', {'name': '扩大未来权限', 'permissions': ['m14.*']}).status_code == 403
    admin_role = next(row for row in admin.get('/api/v1/roles').json()['items'] if row['permissions'] == ['*'])
    assert post(limited, '/members', {'username': 'elevated', 'password': PASSWORD, 'role_ids': [admin_role['id']]}).status_code == 403
    assert post(limited, '/roles', {'name': '合法角色', 'permissions': ['m01.read']}).status_code == 201
    assert patch(limited, '/roles/' + limited_role['id'], {'revision': limited_role['revision'], 'permissions': ['m15.admin']}).status_code == 403


def test_role_revocation_applies_to_existing_session_and_disabled_members_are_revoked(site):
    app, admin, _ = site
    reader = role(admin, '组织读取', ['m14.read'])
    account = member(admin, 'reader', [reader['id']])
    client = login(app, 'reader')
    assert client.get('/api/v1/roles').status_code == 200
    assert patch(admin, '/roles/' + reader['id'], {'revision': reader['revision'], 'permissions': []}).status_code == 200
    assert client.get('/api/v1/roles').status_code == 403
    assert patch(admin, '/members/' + account['id'], {'revision': account['revision'], 'disabled': True}).status_code == 200
    assert client.get('/api/v1/me').status_code == 401


def test_revision_cas_rejects_stale_roles_members_and_workspaces(site):
    app, admin, me = site
    row = role(admin, '更新角色', ['m01.read'])
    assert patch(admin, '/roles/' + row['id'], {'revision': row['revision'], 'name': '新名称'}).status_code == 200
    assert patch(admin, '/roles/' + row['id'], {'revision': row['revision'], 'name': '过期覆盖'}).status_code == 409
    account = member(admin, 'casuser', [row['id']])
    assert patch(admin, '/members/' + account['id'], {'revision': account['revision'], 'role_ids': []}).status_code == 200
    assert patch(admin, '/members/' + account['id'], {'revision': account['revision'], 'role_ids': [row['id']]}).status_code == 409
    workspace = me['workspace']
    assert patch(admin, '/workspaces/' + workspace['id'], {'revision': workspace['revision'], 'name': '新车间名'}).status_code == 200
    assert patch(admin, '/workspaces/' + workspace['id'], {'revision': workspace['revision'], 'name': '过期覆盖'}).status_code == 409


def test_second_workspace_requires_membership_and_switch_rotates_csrf(site):
    app, admin, original = site
    second = post(admin, '/workspaces', {'name': '乙车间'}).json()
    old_csrf = headers(admin)['X-CSRF-Token']
    switched = admin.post('/api/v1/workspace-selections', json={'workspace_id': second['id']}, headers={'X-CSRF-Token': old_csrf})
    assert switched.status_code == 200
    assert switched.json()['csrf_token'] != old_csrf
    assert admin.post('/api/v1/roles', json={'name': '旧CSRF', 'permissions': []}, headers={'X-CSRF-Token': old_csrf}).status_code == 403
    second_role = role(admin, '乙角色', ['m01.read'])
    assert post(admin, '/workspace-selections', {'workspace_id': original['workspace']['id']}).status_code == 200
    first_role = role(admin, '甲角色', ['m01.read'])
    member(admin, 'firstonly', [first_role['id']])
    client = login(app, 'firstonly')
    denied = post(client, '/workspace-selections', {'workspace_id': second['id']})
    assert denied.status_code == 403
    assert client.get('/api/v1/me').json()['workspace']['id'] == original['workspace']['id']
    assert post(admin, '/members', {'username': 'wrongscope', 'password': PASSWORD, 'role_ids': [second_role['id']]}).status_code == 422


def test_permission_preview_matches_private_resource_read_write_and_grant_revocation(site):
    app, admin, _ = site
    reader = role(admin, '应用成员', ['m13.read', 'm13.write'])
    account = member(admin, 'appuser', [reader['id']])
    client = login(app, 'appuser')
    resource = post(admin, '/applications', {'name': '私有应用', 'entry': '/overview', 'engine': 'platform'}).json()
    private = patch(admin, '/applications/' + resource['id'], {'revision': resource['revision'], 'visibility': 'private'})
    assert private.status_code == 200
    assert client.get('/api/v1/applications/' + resource['id']).status_code == 404
    body = {'principal_id': account['user_id'], 'resource_id': resource['id'], 'resource_type': 'application'}
    assert post(admin, '/permission-previews', body).json()['allowed'] is False
    grant = post(admin, '/resource-grants', {'user_id': account['user_id'], 'resource_id': resource['id'], 'resource_type': 'application', 'actions': ['read']})
    assert grant.status_code == 201, grant.text
    assert client.get('/api/v1/applications/' + resource['id']).status_code == 200
    assert post(admin, '/permission-previews', body).json()['allowed'] is True
    assert post(admin, '/permission-previews', {**body, 'action': 'write'}).json()['allowed'] is False
    assert patch(client, '/applications/' + resource['id'], {'revision': private.json()['revision'], 'name': '无权编辑'}).status_code == 403
    assert admin.delete('/api/v1/resource-grants/' + grant.json()['id'], headers=headers(admin)).status_code == 200
    assert client.get('/api/v1/applications/' + resource['id']).status_code == 404


def test_positions_check_department_members_and_revision(site):
    app, admin, _ = site
    account = member(admin, 'positionuser', [])
    department = post(admin, '/departments', {'name': '质检部'}).json()
    created = post(admin, '/positions', {'name': '质检员', 'department_id': department['id'], 'member_ids': [account['id']]})
    assert created.status_code == 201, created.text
    row = created.json()
    assert admin.get('/api/v1/positions').json()['items'][0]['member_ids'] == [account['id']]
    assert patch(admin, '/positions/' + row['id'], {'revision': row['revision'], 'name': '高级质检员'}).status_code == 200
    assert patch(admin, '/positions/' + row['id'], {'revision': row['revision'], 'name': '过期编辑'}).status_code == 409
    assert post(admin, '/positions', {'name': '错成员', 'department_id': department['id'], 'member_ids': ['not-a-member']}).status_code == 422


def test_navigation_uses_enabled_backend_modules_and_current_role(site):
    app, admin, _ = site
    only_home = role(admin, '首页', ['m01.read'])
    member(admin, 'homeonly', [only_home['id']])
    client = login(app, 'homeonly')
    data = client.get('/api/v1/permissions/navigation').json()
    assert [row['key'] for row in data['items']] == ['workspace']
    assert data['permissions'] == ['m01.read']
    admin_data = admin.get('/api/v1/permissions/navigation').json()
    assert 'manufacturing' not in {row['key'] for row in admin_data['items']}
    assert {row['key'] for row in admin_data['items']} == {row.key for row in app.state.modules}


def test_app_launch_is_real_deep_link_without_fake_sso_or_open_redirect(site):
    app, admin, _ = site
    assert post(admin, '/app-launches', {'application': 'dify', 'return_path': '/apps'}).status_code == 503
    integration = post(admin, '/integration-instances', {'name': 'Dify配置', 'engine': 'dify', 'base_url': 'http://127.0.0.1:12080/v1', 'console_url': 'http://127.0.0.1:12080'}).json()
    result = post(admin, '/app-launches', {'application': 'dify', 'integration_id': integration['id'], 'return_path': '/app/app-one/workflow'})
    assert result.status_code == 200, result.text
    assert result.json()['url'] == 'http://127.0.0.1:12080/app/app-one/workflow'
    assert result.json()['requires_native_login'] is True and result.json()['mode'] == 'native_deep_link'
    assert 'token' not in result.json()
    assert post(admin, '/app-launches', {'application': 'dify', 'return_path': '//evil.invalid/apps'}).status_code == 422
    assert post(admin, '/app-launches', {'application': 'analytics', 'return_path': '/analysis'}).json()['url'] == '/analysis'


def test_secret_api_encrypts_history_and_single_use_rotation_ticket(site):
    app, admin, original = site
    created = post(admin, '/secret-references', {'name': '测试凭据', 'value': 'fake-initial-api-value'}).json()
    ticket = post(admin, '/secret-references/' + created['id'] + '/rotation-tickets', {'revision': 1, 'value': 'fake-next-api-value'})
    assert ticket.status_code == 201, ticket.text
    raw_ticket = ticket.json()['vault_import_ticket']
    with app.state.database.sessions() as session:
        entry = session.get(SecretEntry, created['id'])
        imported = session.get(SecretRotationTicket, sha256(raw_ticket.encode()).hexdigest())
        assert 'fake-initial-api-value' not in entry.encrypted_value
        assert 'fake-next-api-value' not in imported.encrypted_value
        assert imported.token_hash != raw_ticket
    consumed = post(admin, '/secret-references/' + created['id'] + '/rotations', {'vault_import_ticket': raw_ticket})
    assert consumed.status_code == 200 and consumed.json()['revision'] == 2
    assert post(admin, '/secret-references/' + created['id'] + '/rotations', {'vault_import_ticket': raw_ticket}).status_code == 409
    metadata = admin.get('/api/v1/secret-references').text
    assert 'fake-initial-api-value' not in metadata and 'fake-next-api-value' not in metadata
    assert 'encrypted_value' not in metadata
    assert app.state.secret_vault.get(created['id'], original['workspace']['id'], revision=1) == 'fake-initial-api-value'
    assert app.state.secret_vault.get(created['id'], original['workspace']['id']) == 'fake-next-api-value'
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SecretVersion).where(SecretVersion.secret_id == created['id'])) == 2
    legacy = patch(admin, '/secret-references/' + created['id'], {'revision': 2, 'value': 'fake-compatible-third-value'})
    assert legacy.status_code == 200 and legacy.json()['revision'] == 3
    assert app.state.secret_vault.get(created['id'], original['workspace']['id'], revision=2) == 'fake-next-api-value'
    assert patch(admin, '/secret-references/' + created['id'], {'revision': 2, 'value': 'fake-stale-value'}).status_code == 409


def test_rotation_ticket_expiration_actor_scope_and_references_prevent_delete(site):
    app, admin, _ = site
    secret = post(admin, '/secret-references', {'name': '被引用凭据', 'value': 'fake-referenced-key'}).json()
    ticket = post(admin, '/secret-references/' + secret['id'] + '/rotation-tickets', {'revision': 1, 'value': 'fake-expired-key'}).json()
    with app.state.database.sessions.begin() as session:
        row = session.get(SecretRotationTicket, sha256(ticket['vault_import_ticket'].encode()).hexdigest())
        row.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert post(admin, '/secret-references/' + secret['id'] + '/rotations', {'vault_import_ticket': ticket['vault_import_ticket']}).status_code == 410
    post(admin, '/integration-instances', {'name': '引用连接', 'engine': 'openwebui', 'base_url': 'http://127.0.0.1:13000', 'console_url': 'http://127.0.0.1:13000', 'credential_ref': secret['id']})
    assert admin.delete('/api/v1/secret-references/' + secret['id'], headers=headers(admin)).status_code == 409


def test_rotation_ticket_cannot_be_used_by_another_admin_or_workspace(site):
    app, admin, original = site
    secret = post(admin, '/secret-references', {'name': '轮换范围', 'value': 'fake-scoped-secret'}).json()
    ticket = post(admin, '/secret-references/' + secret['id'] + '/rotation-tickets', {'revision': 1, 'value': 'fake-rotated-secret'}).json()
    body = {'vault_import_ticket': ticket['vault_import_ticket']}
    manager = role(admin, '凭据管理员', ['m15.admin', 'm15.read'])
    member(admin, 'secretmanager', [manager['id']])
    client = login(app, 'secretmanager')
    denied = post(client, '/secret-references/' + secret['id'] + '/rotations', body)
    assert denied.status_code == 403
    second = post(admin, '/workspaces', {'name': '轮换隔离区'}).json()
    assert post(admin, '/workspace-selections', {'workspace_id': second['id']}).status_code == 200
    assert post(admin, '/secret-references/' + secret['id'] + '/rotations', body).status_code == 403
    assert post(admin, '/workspace-selections', {'workspace_id': original['workspace']['id']}).status_code == 200
    assert post(admin, '/secret-references/' + secret['id'] + '/rotations', body).status_code == 200


@pytest.mark.parametrize('entity', ['role', 'member', 'workspace'])
def test_sql_cas_blocks_a_stale_identity_map_overwriting_a_concurrent_commit(site, entity):
    from agent_platform.modules.identity.router import patch_member, patch_role, update_workspace
    from agent_platform.modules.identity.schemas import MemberPatch, RolePatch, WorkspacePatch
    app, admin, original = site
    selected_role = role(admin, '并发角色', ['m01.read'])
    selected_member = member(admin, 'concurrentuser', [selected_role['id']])
    specs = {
        'role': (Role, selected_role['id'], '/roles/', {'name': '先提交名称'}, RolePatch, patch_role),
        'member': (Membership, selected_member['id'], '/members/', {'role_ids': []}, MemberPatch, patch_member),
        'workspace': (Workspace, original['workspace']['id'], '/workspaces/', {'name': '先提交车间'}, WorkspacePatch, update_workspace),
    }
    model, id, path, changes, body_type, endpoint = specs[entity]
    with app.state.database.sessions() as stale:
        loaded = stale.get(model, id)
        original_revision = loaded.revision
        workspace = stale.get(Workspace, original['workspace']['id'])
        actor = Actor(original['user']['id'], workspace.id, frozenset({'*'}), organization_id=workspace.organization_id, is_admin=True)
        assert patch(admin, path + id, {'revision': original_revision, **changes}).status_code == 200
        assert loaded.revision == original_revision
        with pytest.raises(DomainError) as conflict:
            endpoint(id=id, body=body_type(revision=original_revision, **changes), actor=actor, session=stale)
        assert conflict.value.code == 'revision_conflict'
        stale.rollback()


def test_service_tokens_are_scoped_one_time_visible_and_immediately_revocable(site):
    app, admin, _ = site
    identity = post(admin, '/service-identities', {'name': '外部报表调用方', 'permissions': ['m01.read', 'm14.read'], 'caller_kind': 'external'})
    assert identity.status_code == 201, identity.text
    row = identity.json()
    issued = post(admin, '/service-identities/' + row['id'] + '/tokens', {'name': '短期调用', 'scopes': ['m01.read'], 'ttl_minutes': 5})
    assert issued.status_code == 201, issued.text
    token = issued.json()['token']
    service = TestClient(app)
    service.headers['Authorization'] = 'Bearer ' + token
    assert service.get('/api/v1/tasks').status_code == 200
    assert service.get('/api/v1/roles').status_code == 403
    assert service.post('/api/v1/workspace-selections', json={'workspace_id': row['workspace_id']}).status_code == 403
    listing = admin.get('/api/v1/service-identities/' + row['id'] + '/tokens').text
    assert token not in listing
    cookie_attempt = TestClient(app)
    cookie_attempt.cookies.set('agent_session', token)
    assert cookie_attempt.get('/api/v1/me').status_code == 401
    narrowed = patch(admin, '/service-identities/' + row['id'], {'revision': row['revision'], 'permissions': []})
    assert narrowed.status_code == 200, narrowed.text
    assert service.get('/api/v1/tasks').status_code == 403
    disabled = patch(admin, '/service-identities/' + row['id'], {'revision': narrowed.json()['revision'], 'enabled': False})
    assert disabled.status_code == 200
    assert service.get('/api/v1/me').status_code == 401


def test_password_spaces_preserved_and_self_session_revocation_applies(site):
    app, admin, _ = site
    spaced = '  Local-password-spaces!  '
    member(admin, 'spaces', [], password=spaced)
    client = login(app, 'spaces', spaced)
    assert post(client, '/session-revocations', {}).status_code == 200
    assert client.get('/api/v1/me').status_code == 401


def test_login_budget_caps_keys_and_secret_production_requires_explicit_master(tmp_path, monkeypatch):
    budget = LoginBudget(max_keys=4)
    for index in range(4):
        budget.check(str(index))
        budget.failure(str(index))
    with pytest.raises(DomainError) as error:
        budget.check('overflow')
    assert error.value.status == 429 and len(budget.attempts) == 4
    from agent_platform.core.secrets import SecretVault
    monkeypatch.delenv('AGENT_MASTER_KEY', raising=False)
    with pytest.raises(DomainError) as missing:
        SecretVault(None, Settings(data_dir=tmp_path, environment='production'))
    assert missing.value.code == 'master_key_missing'
