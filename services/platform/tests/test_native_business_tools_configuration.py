"""初始化器仅用临时密文/HTTPX/WSL替身，不连接任何服务或数据库。"""

from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]


def module():
    spec = importlib.util.spec_from_file_location('configure_native_business_tools', ROOT / 'tools/configure_native_business_tools.py')
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value


class FakeNative:
    def __init__(self, module, store, ledger):
        self.module, self.store, self.ledger = module, store, ledger
        self.events, self.identities, self.certificates = [], {}, {}
        self.failure = None
        self.saved = None
        self.original = [{'url': 'https://disabled.example.invalid', 'type': 'openapi', 'auth_type': 'bearer',
            'key': 'prior-secret-must-stay-encrypted', 'config': {'enable': False}, 'info': {'id': 'prior'}}]
        self.members = [{'email': 'dify@local.test', 'role': 'owner'}]

    def schema(self):
        return {'openapi': '3.1.0', 'info': {'title': '制造业务查询', 'version': '1'},
            'servers': [{'url': 'http://172.24.80.1:13002/platform'}], 'paths': {path: {'get': {
                'operationId': values[1]}} for path, values in self.module.bridge.READ_TOOLS.items()}}

    def mark_authentication(self, engine):
        id = self.ledger.read()['engines'][engine]['credential_id']
        value = self.store._load()
        if not value['records'][id].get('authentication'):
            value['records'][id]['authentication'] = {'id': 'mock-auth-' + engine, 'status': 'succeeded'}
            self.store._save(value)

    def wsl_get(self, network, path, key, seconds):
        self.events.append(('wsl', 'GET', path))
        assert network['bind_ip'] == '172.24.80.1' and key.startswith('aic_tool_') and seconds <= 180
        self.mark_authentication('dify')
        return 200, json.dumps(self.schema() if path.endswith('openapi.json') else {'items': []}).encode()

    def handler(self, request):
        self.events.append((request.url.port, request.method, request.url.path))
        # 真实HTTP替身被调用前，密文中必须已有该次请求的attempted预约。
        phase = next(record for record in self.ledger.read()['phases'].values() if record['status'] == 'running')
        assert any(operation['status'] == 'attempted' for operation in phase['operations'].values())
        if self.failure and self.failure == request.url.path:
            return httpx.Response(500, json={'detail': 'sensitive-server-response-never-recorded'})
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if request.url.port == 13002:
            assert request.url.host == '172.24.80.1' and request.headers['authorization'].startswith('Bearer aic_tool_')
            self.mark_authentication('openwebui')
            return httpx.Response(200, json=self.schema() if path.endswith('openapi.json') else {'items': []})
        if path == '/api/v1/auth/login':
            return httpx.Response(200, json={'user': {'is_admin': True}, 'workspace': {'id': 'workspace-one'},
                'csrf_token': 'test-csrf'}, headers={'Set-Cookie': 'agent_session=test-browser-session; Path=/'})
        if request.url.port == 8080:
            assert request.headers['x-csrf-token'] == 'test-csrf' and 'agent_session=' in request.headers['cookie']
            if path == '/api/v1/service-identities':
                assert request.method == 'POST' and body['permissions'] == self.module.SCOPES
                id = 'identity-' + str(len(self.identities))
                result = {'id': id, 'workspace_id': 'workspace-one', 'permissions': body['permissions']}
                self.identities[id] = result
                return httpx.Response(201, json=result)
            identity = path.split('/')[4]
            if path.endswith('/certificates'):
                cert = x509.load_pem_x509_certificate(body['certificate_pem'].encode())
                result = {'id': 'certificate-' + str(len(self.certificates)), 'identity_id': identity,
                    'workspace_id': 'workspace-one', 'status': 'active', 'fingerprint_sha256': cert.fingerprint(hashes.SHA256()).hex()}
                self.certificates[result['id']] = result
                return httpx.Response(201, json=result)
            assert path.endswith('/client-auth')
            return httpx.Response(200, json={'client_id': identity, 'token_endpoint': self.module.TOKEN_ENDPOINT,
                'audience': self.module.TOKEN_ENDPOINT, 'token_auth_method': 'private_key_jwt'})
        if path == '/console/api/login':
            assert body['email'] == 'dify@local.test'
            return httpx.Response(200, json={'result': 'success'}, headers=httpx.Headers([
                ('Set-Cookie', 'csrf_token=dify-csrf; Path=/'), ('Set-Cookie', 'access_token=dify-access; Path=/')]))
        if request.url.port == 13001:
            assert request.headers['x-csrf-token'] == 'dify-csrf'
            if path.endswith('/members'): return httpx.Response(200, json={'accounts': self.members})
            if path.endswith('/api/add'):
                assert body['credentials']['api_key_value'].startswith('aic_tool_')
                assert set(json.loads(body['schema'])['paths']) == set(self.module.bridge.READ_TOOLS)
                return httpx.Response(200, json={'result': 'success'})
            assert path.endswith('/api/tools')
            return httpx.Response(200, json=[{'name': values[1]} for values in self.module.bridge.READ_TOOLS.values()])
        if path == '/api/v1/auths/signin':
            return httpx.Response(200, json={'role': 'admin', 'token': 'owui-admin-session'})
        assert request.headers['authorization'] == 'Bearer owui-admin-session'
        if path == '/api/v1/groups/id/group-read-only':
            return httpx.Response(200, json={'id': 'group-read-only'})
        assert path == '/api/v1/configs/tool_servers'
        if request.method == 'POST':
            self.saved = body['TOOL_SERVER_CONNECTIONS']
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={'TOOL_SERVER_CONNECTIONS': self.saved if self.saved is not None else self.original})


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    value = module()
    local = tmp_path / '.local'
    local.mkdir()
    (local / 'desktop-access.key').write_bytes(Fernet.generate_key())
    native = tmp_path / 'native'
    native.mkdir()
    (native / 'runtime.key').write_bytes(Fernet.generate_key())
    monkeypatch.setattr(value.windows, 'runtime_lock', lambda _: nullcontext())
    monkeypatch.setattr(value.windows, 'load', lambda _: {'id': 'a' * 24})
    ledger = value.Ledger('native-tools-test-001', repo=tmp_path)
    store = value.ToolCredentialStore(native)
    driver = FakeNative(value, store, ledger)
    accounts = {'platform': {'username': 'admin', 'password': 'only-temporary-test-password'},
        'dify': {'username': 'dify@local.test', 'password': 'only-temporary-test-password'},
        'openwebui': {'username': 'owui@local.test', 'password': 'only-temporary-test-password'}, 'group_id': 'group-read-only'}
    proof = {'native_runtime_id': 'a' * 24, 'wsl_runtime_id': 'b' * 32, 'network': {'bind_ip': '172.24.80.1'},
        'bridge_launch_id': 'c' * 24, 'upstreams': {}}
    def runner():
        return value.Runner(ledger, store=store, accounts=deepcopy(accounts), ownership=lambda: deepcopy(proof),
            transport=httpx.MockTransport(driver.handler), wsl_get=driver.wsl_get)
    return value, ledger, store, driver, runner, proof


def test_all_four_phases_preserve_credentials_scope_and_native_configuration(fixture):
    module, ledger, store, driver, runner, proof = fixture
    for phase in module.PHASES:
        result = runner().execute(phase)
        assert result['phases'][phase]['status'] == 'succeeded'
    phases = ledger.read()['phases']
    assert [phases[phase]['primary_http'] for phase in module.PHASES] == [0, 7, 11, 4]
    assert phases['native-config']['authentication_http_observed'] == 4 and phases['probe']['authentication_http_observed'] == 0
    assert all(value['primary_http'] + value['authentication_http_reserved'] <= 25 for value in phases.values())
    assert len(driver.identities) == len(driver.certificates) == 2
    assert driver.saved[0] == driver.original[0]
    connection = driver.saved[-1]
    assert connection['spec_type'] == 'json' and connection['forward_cookies'] is False
    assert connection['config']['access_grants'] == [{'principal_type': 'group', 'principal_id': 'group-read-only', 'permission': 'read'}]
    assert set(json.loads(connection['spec'])['paths']) == set(module.bridge.READ_TOOLS)
    plain = ledger.read()
    serialized = json.dumps(plain)
    assert 'only-temporary-test-password' not in serialized and 'owui-admin-session' not in serialized
    assert 'test-browser-session' not in serialized and 'dify-access' not in serialized
    assert 'prior-secret-must-stay-encrypted' in serialized and b'prior-secret-must-stay-encrypted' not in ledger.path.read_bytes()
    assert 'aic_tool_' not in json.dumps(ledger.public())
    before = len(driver.events)
    runner().execute('register')
    runner().execute('native-config')
    runner().execute('probe')
    assert len(driver.events) == before


def test_failed_write_preserves_identity_and_never_registers_it_again(fixture):
    module, ledger, store, driver, runner, proof = fixture
    runner().execute('prepare')
    driver.failure = '/api/v1/service-identities/identity-0/certificates'
    with pytest.raises(module.ConfigurationError, match='http_status_500'):
        runner().execute('register')
    assert ledger.read()['engines']['dify']['identity_id'] == 'identity-0'
    assert ledger.read()['phases']['register']['operations']['dify-certificate']['status'] == 'attempted'
    before = len(driver.events)
    with pytest.raises(module.ConfigurationError, match='already_attempted'):
        runner().execute('register')
    assert len(driver.events) == before and len(driver.identities) == 1
    assert 'sensitive-server-response' not in json.dumps(ledger.read())


def test_native_config_does_not_write_when_dify_workspace_has_other_members(fixture):
    module, ledger, store, driver, runner, proof = fixture
    runner().execute('prepare')
    runner().execute('register')
    driver.members.append({'email': 'another-user@local.test', 'role': 'normal'})
    with pytest.raises(module.ConfigurationError, match='workspace_not_dedicated'):
        runner().execute('native-config')
    assert driver.saved is None and not any(path.endswith('/api/add') for _, _, path in driver.events)


def test_existing_active_connection_is_preserved_and_blocks_all_native_writes(fixture):
    module, ledger, store, driver, runner, proof = fixture
    runner().execute('prepare')
    runner().execute('register')
    driver.original[0]['config']['enable'] = True
    with pytest.raises(module.ConfigurationError, match='active_connections_require_review'):
        runner().execute('native-config')
    assert driver.saved is None and driver.original[0]['config']['enable']
    assert not any(path.endswith('/api/add') for _, _, path in driver.events)


def test_phase_budget_and_unknown_operation_appointment_cannot_be_replayed(fixture):
    module, ledger, store, driver, runner, proof = fixture
    runner().execute('prepare')
    ledger.begin('register')
    ledger.reserve('register', 'same', 'POST', 'platform', '/api/v1/service-identities', {'permissions': []})
    with pytest.raises(module.ConfigurationError, match='already_attempted'):
        ledger.reserve('register', 'same', 'POST', 'platform', '/api/v1/service-identities')
    ledger.update(lambda value: value['phases']['register'].update(primary_http=24))
    with pytest.raises(module.ConfigurationError, match='http_budget'):
        ledger.reserve('register', 'extra', 'GET', 'bridge:dify', '/platform/api/v1/devices?limit=1', extra_auth=2)
    assert 'extra' not in ledger.read()['phases']['register']['operations']


def test_runtime_change_between_phases_stops_before_any_login(fixture):
    module, ledger, store, driver, runner, proof = fixture
    runner().execute('prepare')
    proof['wsl_runtime_id'] = 'd' * 32
    with pytest.raises(module.ConfigurationError, match='runtime_changed_between_phases'):
        runner().execute('register')
    assert not driver.events


@pytest.mark.parametrize('fault', ['write', 'unknown_path', 'foreign_server', 'external_ref', 'operation_server'])
def test_schema_contract_cannot_gain_write_or_foreign_proxy_entry(fixture, fault):
    module, ledger, store, driver, runner, proof = fixture
    schema = driver.schema()
    if fault == 'write': schema['paths']['/api/v1/devices']['post'] = {}
    if fault == 'unknown_path': schema['paths']['/api/v1/secrets'] = {'get': {}}
    if fault == 'foreign_server': schema['servers'] = [{'url': 'http://192.168.1.2'}]
    if fault == 'external_ref': schema['components'] = {'schemas': {'External': {'$ref': 'https://remote.invalid/schema'}}}
    if fault == 'operation_server': schema['paths']['/api/v1/devices']['get']['servers'] = [{'url': 'https://remote.invalid'}]
    with pytest.raises(module.ConfigurationError, match='schema_changed|schema_reference_denied|server_override_denied'):
        module.validate_schema(schema, proof['network'])


def test_wsl_key_only_uses_anonymous_stdin_and_linux_deadline(fixture, monkeypatch):
    module, ledger, store, driver, runner, proof = fixture
    observed = {}
    def subprocess_run(command, **kwargs):
        observed.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b'{"status":200,"body":"e30="}')
    monkeypatch.setattr(module.subprocess, 'run', subprocess_run)
    assert module.wsl_bridge_get(proof['network'], '/platform/api/v1/devices?limit=1', 'never-in-argv', 20) == (200, b'{}')
    assert 'never-in-argv' not in repr(observed['command']) and 'never-in-argv' not in repr(observed['env'])
    assert json.loads(observed['input'])['key'] == 'never-in-argv'
    assert '/usr/bin/timeout' in observed['command'] and '--kill-after=2' in observed['command']
    assert observed['timeout'] <= 20


def test_whole_http_response_deadline_closes_stream_without_retry(fixture):
    import asyncio
    import time
    module, ledger, store, driver, runner, proof = fixture
    application = runner()
    application.deadline = time.monotonic() + 0.05
    class Slow(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'{'
            await asyncio.sleep(5)
        async def aclose(self): self.closed = True
    stream, requests = Slow(), []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)
    application.transport = httpx.MockTransport(handler)
    with pytest.raises(TimeoutError):
        application.bounded_http('GET', 'http://127.0.0.1:8080/api/v1/devices', None, {})
    assert stream.closed and len(requests) == 1


def test_same_preparation_id_cannot_create_another_pair_of_credentials(fixture):
    module, ledger, store, driver, runner, proof = fixture
    runner().execute('prepare')
    assert len(store._load()['records']) == 2
    with pytest.raises(module.ConfigurationError, match='already_exists'):
        runner().execute('prepare')
    assert len(store._load()['records']) == 2 and not driver.events


def test_disabled_native_connection_with_optional_none_fields_is_preserved(fixture):
    module, ledger, store, driver, runner, proof = fixture
    old = {'url': 'https://disabled.invalid', 'type': 'openapi', 'config': None, 'info': None, 'key': 'existing-secret'}
    new = {'info': {'id': 'dedicated-new'}}
    result = module.merge_owui_connections([old], new)
    assert result == [old, new] and result[0] is not old
