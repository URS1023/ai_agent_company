"""工具凭据只用临时密文和HTTPX替身验证，不连接平台、原生服务或数据库。"""

import asyncio
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs

from cryptography import x509
from cryptography.fernet import Fernet
import httpx
import jwt
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / 'deploy/native') not in sys.path:
    sys.path.insert(0, str(ROOT / 'deploy/native'))


def tool():
    spec = importlib.util.spec_from_file_location('wsl_tool_credentials', ROOT / 'deploy/native/wsl_tool_credentials.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module = tool()
    (tmp_path / 'runtime.key').write_bytes(Fernet.generate_key())
    monkeypatch.setattr(module.windows, 'load', lambda _: {'id': 'a' * 24})
    monkeypatch.setattr(module.windows, 'runtime_lock', lambda _: nullcontext())
    clock = [time.time()]
    store = module.ToolCredentialStore(tmp_path, clock=lambda: clock[0])
    return module, store, clock


def binding(module):
    return {'workspace_id': 'workspace-one', 'service_identity_id': 'identity-one', 'certificate_id': 'certificate-one',
        'scope': ['m02.read', 'm10.read'], 'token_endpoint': module.TOKEN_ENDPOINT, 'audience': module.TOKEN_ENDPOINT}


def configured(fixture, *, engine='dify'):
    module, store, clock = fixture
    row = store.prepare(engine, '独立只读工具')
    row = store.import_binding(row['id'], binding(module), revision=row['revision'])
    return row, store.get_native_key(row['id'])


def network():
    return {'source_ip': '172.24.81.83', 'bind_ip': '172.24.80.1'}


def native_handler(module, row, events, *, fault=None):
    cert = x509.load_pem_x509_certificate(row['certificate_pem'].encode())
    def handler(request):
        events.append(request)
        assert request.url.host == '127.0.0.1' and request.url.port == 8080
        if request.url.path == '/api/v1/client-auth/token':
            assert request.method == 'POST' and 'authorization' not in request.headers
            fields = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
            assert fields['client_id'] == 'identity-one' and fields['grant_type'] == 'client_credentials'
            assert fields['client_assertion_type'] == module.ASSERTION_TYPE
            claims = jwt.decode(fields['client_assertion'], cert.public_key(), algorithms=['ES256'],
                audience=module.TOKEN_ENDPOINT, issuer='identity-one', subject='identity-one',
                options={'verify_iat': False, 'verify_nbf': False, 'verify_exp': False})
            assert claims['exp'] - claims['iat'] == 120 and claims['jti']
            assert jwt.get_unverified_header(fields['client_assertion'])['kid'] == 'certificate-one'
            if fault == 'timeout': raise httpx.ReadTimeout('sensitive-secret-must-not-escape', request=request)
            if fault == 'denied': return httpx.Response(401, json={'error': 'invalid_client'})
            if fault == 'redirect': return httpx.Response(307, headers={'Location': 'http://other.invalid'})
            return httpx.Response(200, json={'access_token': 'service_mock-only', 'token_type': 'Bearer',
                'expires_in': 601 if fault == 'duration' else 600, 'scope': fields['scope']})
        assert request.method == 'GET' and request.url.path == '/api/v1/me'
        assert request.headers['authorization'] == 'Bearer service_mock-only'
        return httpx.Response(200, json={'workspace': {'id': 'wrong-workspace' if fault == 'workspace' else 'workspace-one'},
            'user': {'is_admin': fault == 'admin'},
            'permissions': ['*'] if fault == 'permissions' else ['m02.read', 'm10.read']})
    return handler


def test_prepare_keeps_private_key_and_opaque_key_only_in_one_fernet_payload(fixture):
    module, store, _ = fixture
    row = store.prepare('dify', '质检工具')
    key = store.get_native_key(row['id'])
    index = store.index.read_text('utf-8')
    assert key not in index and 'PRIVATE KEY' not in index and 'bridge_key' not in index
    assert 'key_hash' in index and row['phase'] == 'prepared'
    files = list(store.folder.glob('*.enc'))
    assert len(files) == 1 and key.encode() not in files[0].read_bytes()
    decrypted = json.loads(Fernet((store.root / 'runtime.key').read_bytes()).decrypt(files[0].read_bytes()))
    assert decrypted['bridge_key'] == key and 'PRIVATE KEY' in decrypted['private_key_pem']
    assert 'bridge_key' not in row and 'private_key_pem' not in row and 'secret_file' not in row
    assert not store.cache


@pytest.mark.parametrize('fault', ['endpoint', 'audience', 'admin_scope', 'missing_schema_scope', 'unknown_field'])
def test_import_cannot_select_external_endpoint_or_privilege_scope(fixture, fault):
    module, store, _ = fixture
    row = store.prepare('dify', '工具')
    value = binding(module)
    if fault == 'endpoint': value['token_endpoint'] = 'http://192.168.1.10/api/v1/client-auth/token'
    if fault == 'audience': value['audience'] = 'http://127.0.0.1:18080/api/v1/client-auth/token'
    if fault == 'admin_scope': value['scope'] = ['m02.read', '*']
    if fault == 'missing_schema_scope': value['scope'] = ['m10.read']
    if fault == 'unknown_field': value['admin_password'] = 'never-accepted'
    with pytest.raises(module.CredentialError, match='binding_invalid'):
        store.import_binding(row['id'], value, revision=1)
    assert store._load()['records'][row['id']]['phase'] == 'prepared' and len(list(store.folder.glob('*.enc'))) == 1


def test_import_cas_and_existing_binding_cannot_silently_change_workspace(fixture):
    module, store, _ = fixture
    row, _ = configured(fixture)
    with pytest.raises(module.CredentialError, match='revision_conflict'):
        store.revoke(row['id'], revision=1)
    value = binding(module) | {'workspace_id': 'another-workspace'}
    with pytest.raises(module.CredentialError, match='requires_rotation'):
        store.import_binding(row['id'], value, revision=2)
    assert len(list(store.folder.glob('*.enc'))) == 1


def test_authentication_is_single_flight_cached_and_renews_before_expiry_with_new_jti(fixture):
    module, store, clock = fixture
    row, key = configured(fixture)
    events = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(native_handler(module, row, events)),
                trust_env=False, follow_redirects=False) as client:
            values = await asyncio.gather(*(store.authorization(key, network()['source_ip'], network(), client) for _ in range(4)))
            assert values == ['Bearer service_mock-only'] * 4 and len(events) == 2
            clock[0] += 571
            assert await store.authorization(key, network()['source_ip'], network(), client) == 'Bearer service_mock-only'
            assert len(events) == 4
    asyncio.run(run())
    assertions = [parse_qs(request.content.decode())['client_assertion'][0] for request in events if request.method == 'POST']
    assert jwt.decode(assertions[0], options={'verify_signature': False})['jti'] != jwt.decode(assertions[1], options={'verify_signature': False})['jti']
    assert 'service_mock-only' not in store.index.read_text('utf-8')


@pytest.mark.parametrize('engine,source', [('dify', '172.24.80.1'), ('openwebui', '172.24.81.83')])
def test_engine_key_cannot_be_reused_from_other_native_source(fixture, engine, source):
    module, store, _ = fixture
    row, key = configured(fixture, engine=engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('来源拒绝不可换token'))) as client:
            with pytest.raises(module.CredentialError, match='source_denied'):
                await store.authorization(key, source, network(), client)
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['timeout', 'denied', 'redirect', 'duration', 'workspace', 'admin', 'permissions'])
def test_failed_exchange_is_latched_without_retry_or_sensitive_exception_text(fixture, fault):
    module, store, _ = fixture
    row, key = configured(fixture)
    events = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(native_handler(module, row, events, fault=fault)),
                follow_redirects=False) as client:
            with pytest.raises(module.CredentialError, match='authentication_failed') as error:
                await store.authorization(key, network()['source_ip'], network(), client)
            assert 'sensitive-secret' not in str(error.value)
            count = len(events)
            with pytest.raises(module.CredentialError, match='requires_review'):
                await store.authorization(key, network()['source_ip'], network(), client)
            restarted = module.ToolCredentialStore(store.root, clock=store.clock)
            with pytest.raises(module.CredentialError, match='requires_review'):
                await restarted.authorization(key, network()['source_ip'], network(), client)
            assert len(events) == count
    asyncio.run(run())
    assert len(events) == (2 if fault in {'workspace', 'admin', 'permissions'} else 1) and not store.cache


def test_cached_token_does_not_survive_local_revoke_and_rotation_invalidates_old_key(fixture):
    module, store, _ = fixture
    row, key = configured(fixture)
    events = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(native_handler(module, row, events))) as client:
            await store.authorization(key, network()['source_ip'], network(), client)
            store.revoke(row['id'], revision=2)
            with pytest.raises(module.CredentialError, match='unknown_or_revoked'):
                await store.authorization(key, network()['source_ip'], network(), client)
            rotated = store.rotate(row['id'], revision=3)
            assert rotated['phase'] == 'prepared' and rotated['revision'] == 4
            assert store.get_native_key(row['id']) != key and len(list(store.folder.glob('*.enc'))) == 2
            with pytest.raises(module.CredentialError, match='unknown_or_revoked'):
                await store.authorization(key, network()['source_ip'], network(), client)
            assert len(events) == 2
    asyncio.run(run())


def test_upstream_401_blocks_cached_token_until_explicit_cas_confirmation(fixture):
    module, store, _ = fixture
    row, key = configured(fixture)
    events = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(native_handler(module, row, events))) as client:
            await store.authorization(key, network()['source_ip'], network(), client)
            store.reject_token(key)
            with pytest.raises(module.CredentialError, match='requires_review'):
                await store.authorization(key, network()['source_ip'], network(), client)
            store.import_binding(row['id'], binding(module), revision=2)
            await store.authorization(key, network()['source_ip'], network(), client)
            assert len(events) == 4
    asyncio.run(run())


def test_revocation_during_exchange_discards_new_token_and_does_not_forward_it(fixture):
    module, store, _ = fixture
    row, key = configured(fixture)
    events = []
    handler = native_handler(module, row, events)
    def revoke_after_me(request):
        response = handler(request)
        if request.url.path == '/api/v1/me': store.revoke(row['id'], revision=2)
        return response
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(revoke_after_me)) as client:
            with pytest.raises(module.CredentialError, match='authentication_failed'):
                await store.authorization(key, network()['source_ip'], network(), client)
    asyncio.run(run())
    assert not store.cache and len(events) == 2


def test_cancelled_exchange_is_not_automatically_replayed(fixture):
    module, store, _ = fixture
    row, key = configured(fixture)
    events = []
    async def handler(request):
        events.append(request)
        raise asyncio.CancelledError()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(asyncio.CancelledError):
                await store.authorization(key, network()['source_ip'], network(), client)
            with pytest.raises(module.CredentialError, match='requires_review'):
                await store.authorization(key, network()['source_ip'], network(), client)
    asyncio.run(run())
    assert len(events) == 1 and not store.cache


def test_legacy_wsl_bearer_is_preserved_but_host_requires_managed_key(fixture):
    module, store, _ = fixture
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('旧Bearer不做认证交换'))) as client:
            assert await store.authorization('service_legacy', network()['source_ip'], network(), client) == 'Bearer service_legacy'
            with pytest.raises(module.CredentialError, match='host_key_required'):
                await store.authorization('service_legacy', network()['bind_ip'], network(), client)
            with pytest.raises(module.CredentialError, match='unknown_or_revoked'):
                await store.authorization(module.PREFIX + 'unknown', network()['source_ip'], network(), client)
    asyncio.run(run())


def test_cli_prepare_never_prints_key_and_existing_runtime_key_is_required(fixture, monkeypatch, capsys):
    module, store, _ = fixture
    monkeypatch.setattr(module, 'ToolCredentialStore', lambda: store)
    assert module.main(['prepare', '--engine', 'dify', '--name', '客户端']) == 0
    value = json.loads(capsys.readouterr().out)
    key = store.get_native_key(value['id'])
    assert value['certificate_pem'].startswith('-----BEGIN CERTIFICATE-----')
    assert key not in json.dumps(value) and 'PRIVATE KEY' not in json.dumps(value)
    (store.root / 'runtime.key').unlink()
    with pytest.raises(module.CredentialError, match='master_key_missing'):
        store.get_native_key(value['id'])


def test_crash_reservation_remains_blocked_after_restart_and_cas_ack_keeps_failure_history(fixture):
    module, store, _ = fixture
    row, key = configured(fixture)
    store._authentication_state(row, 'attempted')
    restarted = module.ToolCredentialStore(store.root)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('不确定认证不得重放'))) as client:
            with pytest.raises(module.CredentialError, match='requires_review'):
                await restarted.authorization(key, network()['source_ip'], network(), client)
    asyncio.run(run())
    restarted.import_binding(row['id'], binding(module), revision=2)
    actual = restarted._load()['records'][row['id']]
    assert actual['revision'] == 3 and 'authentication' not in actual
    assert actual['authentication_history'][-1]['status'] == 'attempted'


def test_cached_token_never_outlives_certificate_and_expiry_does_not_trigger_exchange(fixture):
    from datetime import datetime
    module, store, clock = fixture
    row, key = configured(fixture)
    certificate_expires = datetime.fromisoformat(row['certificate_expires_at']).timestamp()
    clock[0] = certificate_expires - 60
    events = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(native_handler(module, row, events))) as client:
            await store.authorization(key, network()['source_ip'], network(), client)
            assert next(iter(store.cache.values()))[1] == certificate_expires
            clock[0] += 31
            with pytest.raises(module.CredentialError, match='certificate_expiring'):
                await store.authorization(key, network()['source_ip'], network(), client)
            assert len(events) == 2
    asyncio.run(run())
