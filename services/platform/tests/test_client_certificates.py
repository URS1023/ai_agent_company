"""新临时SQLite与本地生成密钥验证证书接入；不访问旧库、外网或真实服务。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import func, select

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, User, Workspace
from agent_platform.core.settings import Settings
from agent_platform.main import create_app
from agent_platform.modules.identity.client_certificate_crypto import (
    ASSERTION_TYPE, configured_audience, parse_public_certificate,
)
from agent_platform.modules.identity.client_certificate_models import (
    ServiceCertificateTokenBinding, ServiceClientAssertion, ServiceClientCertificate,
)
from agent_platform.modules.identity.models import ServiceAccess, ServiceIdentity

PASSWORD = 'Local-Certificate-Test-2026!'
AUDIENCE = 'https://client-auth.example.invalid/api/v1/client-auth/token'
TOKEN_PATH = '/api/v1/client-auth/token'


def material(*, rsa_key=False, ca=False, start=-1, end=30, signing=True):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if rsa_key else ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '本机制造测试调用方')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now + timedelta(days=start))
        .not_valid_after(now + timedelta(days=end))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=signing, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca,
            encipher_only=False, decipher_only=False), critical=True).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_MASTER_KEY', Fernet.generate_key().decode())
    app = create_app(Settings(data_dir=tmp_path, environment='test', enabled_modules=('identity', 'catalog'),
                              client_assertion_audience=AUDIENCE))
    with TestClient(app) as admin:
        setup = admin.post('/api/v1/auth/setup', json={'username': 'admin', 'password': PASSWORD,
            'display_name': '测试管理员', 'workspace_name': '证书测试车间', 'organization_name': '隔离企业'})
        assert setup.status_code == 201, setup.text
        csrf = {'X-CSRF-Token': admin.get('/api/v1/me').json()['csrf_token']}
        identity = admin.post('/api/v1/service-identities', json={
            'name': '质检集成', 'permissions': ['m01.read', 'm14.read']}, headers=csrf).json()
        yield app, admin, csrf, identity


def register(site, pem, name='质检公钥'):
    _, admin, csrf, identity = site
    return admin.post(f"/api/v1/service-identities/{identity['id']}/certificates",
        json={'name': name, 'certificate_pem': pem}, headers=csrf)


def assertion(identity, cert, key, *, claims=None, headers=None):
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {'iss': identity['id'], 'sub': identity['id'], 'aud': AUDIENCE,
               'iat': now, 'nbf': now, 'exp': now + 120, 'jti': secrets.token_hex(16)}
    payload.update(claims or {})
    return jwt.encode(payload, key, algorithm=cert['algorithm'], headers={'kid': cert['id'], **(headers or {})})


def exchange(site, signed, *, scope=None, **changes):
    _, admin, _, identity = site
    fields = {'grant_type': 'client_credentials', 'client_id': identity['id'],
              'client_assertion_type': ASSERTION_TYPE, 'client_assertion': signed}
    if scope is not None:
        fields['scope'] = scope
    fields.update(changes)
    return admin.post(TOKEN_PATH, data=fields)


def bearer_get(site, token, path='/api/v1/service-identities'):
    return site[1].get(path, headers={'Authorization': 'Bearer ' + token})


@pytest.mark.parametrize('rsa_key', [False, True])
def test_real_signature_exchange_scopes_and_replay_are_atomic(site, rsa_key):
    key, pem = material(rsa_key=rsa_key)
    cert_response = register(site, pem)
    assert cert_response.status_code == 201, cert_response.text
    cert = cert_response.json()
    signed = assertion(site[3], cert, key)
    accepted = exchange(site, signed, scope='m14.read')
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body['token_type'] == 'Bearer' and 0 < body['expires_in'] <= 600
    assert body['scope'] == 'm14.read'
    assert accepted.headers['pragma'] == 'no-cache'
    assert bearer_get(site, body['access_token']).status_code == 200
    replay = exchange(site, signed)
    assert replay.status_code == 401 and replay.json()['error'] == 'invalid_client'
    with site[0].state.database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ServiceAccess)) == 1
        assert db.scalar(select(func.count()).select_from(ServiceClientAssertion)) == 1
        assert db.scalar(select(func.count()).select_from(ServiceCertificateTokenBinding)) == 1
        row = db.scalar(select(ServiceClientAssertion))
        assert row.jti_sha256 == sha256(jwt.decode(signed, options={'verify_signature': False})['jti'].encode()).hexdigest()
    assert pem not in str(cert) and 'PRIVATE KEY' not in accepted.text


@pytest.mark.parametrize('claim_change', [
    {'aud': 'https://attacker.invalid'}, {'iss': 'another-client'}, {'sub': 'another-client'},
    {'exp': 0}, {'iat': 0}, {'nbf': 9999999999}, {'jti': ''}, {'iat': True},
    {'aud': [AUDIENCE]}, {'exp': int(datetime.now(timezone.utc).timestamp()) + 900},
])
def test_invalid_claims_never_issue_or_consume_jti(site, claim_change):
    key, pem = material()
    cert = register(site, pem).json()
    result = exchange(site, assertion(site[3], cert, key, claims=claim_change))
    assert result.status_code == 401 and result.json()['error'] == 'invalid_client'
    with site[0].state.database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ServiceAccess)) == 0
        assert db.scalar(select(func.count()).select_from(ServiceClientAssertion)) == 0


def test_wrong_key_headers_scope_and_host_do_not_authenticate(site):
    key, pem = material()
    wrong, _ = material()
    cert = register(site, pem).json()
    assert exchange(site, assertion(site[3], cert, wrong)).status_code == 401
    assert exchange(site, assertion(site[3], cert, key, headers={'jku': 'http://127.0.0.1/jwks'})).status_code == 401
    assert exchange(site, assertion(site[3], cert, key), scope='m15.admin').json()['error'] == 'invalid_scope'
    # 伪造Host不会成为audience；签名必须仍匹配固定服务器配置。
    signed = assertion(site[3], cert, key, claims={'aud': 'https://spoof.invalid/api/v1/client-auth/token'})
    denied = site[1].post(TOKEN_PATH, data={'grant_type': 'client_credentials', 'client_id': site[3]['id'],
        'client_assertion_type': ASSERTION_TYPE, 'client_assertion': signed}, headers={'Host': 'spoof.invalid'})
    assert denied.status_code == 401


@pytest.mark.parametrize('change', ['revocation', 'disable', 'expiry', 'identity_disable', 'permission_change'])
def test_existing_bearer_rechecks_certificate_and_current_identity(site, change):
    key, pem = material()
    cert = register(site, pem).json()
    token = exchange(site, assertion(site[3], cert, key)).json()['access_token']
    assert bearer_get(site, token).status_code == 200
    app, admin, csrf, identity = site
    base = f"/api/v1/service-identities/{identity['id']}"
    if change == 'revocation':
        response = admin.post(base + f"/certificates/{cert['id']}/revocations", json={'revision': 1, 'reason': '调用方退役'}, headers=csrf)
        assert response.status_code == 200
    elif change == 'disable':
        assert admin.patch(base + f"/certificates/{cert['id']}", json={'revision': 1, 'enabled': False}, headers=csrf).status_code == 200
    elif change == 'expiry':
        with app.state.database.sessions.begin() as db:
            db.get(ServiceClientCertificate, cert['id']).not_after = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    else:
        update = {'enabled': False} if change == 'identity_disable' else {'permissions': ['m01.read']}
        assert admin.patch(base, json={'revision': 1, **update}, headers=csrf).status_code == 200
    assert bearer_get(site, token).status_code == (403 if change == 'permission_change' else 401)


def test_rotation_revokes_old_tokens_and_preserves_historical_public_key(site):
    key, pem = material()
    cert = register(site, pem).json()
    raw = exchange(site, assertion(site[3], cert, key)).json()['access_token']
    new_key, new_pem = material()
    base = f"/api/v1/service-identities/{site[3]['id']}/certificates"
    rotated = site[1].post(base + f"/{cert['id']}/rotations", json={
        'revision': 1, 'name': '轮换公钥', 'certificate_pem': new_pem}, headers=site[2])
    assert rotated.status_code == 201, rotated.text
    replacement = rotated.json()
    assert bearer_get(site, raw).status_code == 401
    assert exchange(site, assertion(site[3], cert, key)).status_code == 401
    assert exchange(site, assertion(site[3], replacement, new_key)).status_code == 200
    old = next(row for row in site[1].get(base).json()['items'] if row['id'] == cert['id'])
    assert old['replaced_by_id'] == replacement['id'] and old['status'] == 'revoked'
    assert site[1].get(base + f"/{cert['id']}/public-certificate").text == pem
    denied = site[1].patch(base + f"/{cert['id']}", json={'revision': old['revision'], 'enabled': True}, headers=site[2])
    assert denied.status_code == 409


def test_certificate_cas_duplicate_future_and_private_key_rejection(site):
    key, pem = material()
    cert = register(site, pem).json()
    assert register(site, pem).status_code == 409
    path = f"/api/v1/service-identities/{site[3]['id']}/certificates/{cert['id']}"
    assert site[1].patch(path, json={'revision': 1, 'name': '新名称'}, headers=site[2]).status_code == 200
    assert site[1].patch(path, json={'revision': 1, 'enabled': False}, headers=site[2]).status_code == 409
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    denied = register(site, pem + private)
    assert denied.status_code == 422 and private not in denied.text
    _, future = material(start=1)
    future_cert = register(site, future).json()
    assert future_cert['status'] == 'pending'
    _, future_pem = material(start=1)
    prior = site[1].post(path + '/rotations', json={'revision': 2, 'name': '未到期', 'certificate_pem': future_pem}, headers=site[2])
    assert prior.status_code == 422
    with site[0].state.database.sessions() as db:
        assert db.get(ServiceClientCertificate, cert['id']).revoked_at is None
        assert db.scalar(select(func.count()).select_from(ServiceClientCertificate)) == 2


@pytest.mark.parametrize('kwargs', [{'ca': True}, {'signing': False}, {'start': -2, 'end': -1}])
def test_ca_no_signing_and_expired_certificates_are_rejected(kwargs):
    _, pem = material(**kwargs)
    with pytest.raises(DomainError) as error:
        parse_public_certificate(pem)
    assert error.value.code == 'client_certificate_invalid'


@pytest.mark.parametrize('audience', ['', 'http://external.example/api/v1/client-auth/token',
    'https://user:secret@example.com/api/v1/client-auth/token', 'https://example.com/api/v1/client-auth/token?x=1',
    'https://example.com/other'])
def test_invalid_server_audience_fails_closed(audience):
    with pytest.raises(DomainError) as error:
        configured_audience(Settings(client_assertion_audience=audience))
    assert error.value.status == 503


def test_form_errors_no_secret_echo_and_disabled_membership_cannot_exchange(site):
    key, pem = material()
    cert = register(site, pem).json()
    signed = assertion(site[3], cert, key)
    wrong = site[1].post(TOKEN_PATH, json={'client_assertion': signed})
    assert wrong.status_code == 400 and signed not in wrong.text
    duplicate = site[1].post(TOKEN_PATH, content='client_id=a&client_id=b',
                            headers={'Content-Type': 'application/x-www-form-urlencoded'})
    assert duplicate.status_code == 400
    assert exchange(site, signed, grant_type='password').json()['error'] == 'unsupported_grant_type'
    assert exchange(site, signed, client_assertion_type='unknown').status_code == 400
    with site[0].state.database.sessions.begin() as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == site[3]['user_id']))
        membership.disabled = True
    assert exchange(site, signed).status_code == 401


def test_workspace_isolation_and_limited_admin_cannot_register_broader_client(site):
    app, admin, csrf, identity = site
    key, pem = material()
    cert = register(site, pem).json()
    # 新工作区没有同ID身份，管理入口不得跨工作区读写证书。
    with app.state.database.sessions.begin() as db:
        user = db.get(User, identity['user_id'])
        other = Workspace(organization_id=user.organization_id, name='另一车间')
        db.add(other)
        db.flush()
        other_id = other.id
    from agent_platform.core.context import Actor
    from agent_platform.modules.identity.client_certificate_service import ClientCertificateService
    with app.state.database.sessions() as db:
        other_actor = Actor(identity['user_id'], other_id, frozenset({'*'}), is_admin=True)
        with pytest.raises(DomainError) as error:
            ClientCertificateService(db, other_actor).get(identity['id'], cert['id'])
        assert error.value.status == 404
        limited = Actor(identity['user_id'], identity['workspace_id'], frozenset({'m14.admin', 'm14.read'}))
        with pytest.raises(DomainError) as error:
            ClientCertificateService(db, limited).register(identity['id'], '越权证书', pem)
        assert error.value.status == 403


def test_stopped_certificate_does_not_revive_old_bearer_on_reenable(site):
    key, pem = material()
    cert = register(site, pem).json()
    token = exchange(site, assertion(site[3], cert, key)).json()['access_token']
    path = f"/api/v1/service-identities/{site[3]['id']}/certificates/{cert['id']}"
    assert site[1].patch(path, json={'revision': 1, 'enabled': False}, headers=site[2]).status_code == 200
    assert site[1].patch(path, json={'revision': 2, 'enabled': True}, headers=site[2]).status_code == 200
    assert bearer_get(site, token).status_code == 401
    assert exchange(site, assertion(site[3], cert, key)).status_code == 200


def test_jti_cannot_be_replayed_with_rotated_certificate(site):
    key, pem = material()
    cert = register(site, pem).json()
    jti = secrets.token_hex(16)
    assert exchange(site, assertion(site[3], cert, key, claims={'jti': jti})).status_code == 200
    next_key, next_pem = material()
    replacement = register(site, next_pem, '并行轮换公钥').json()
    assert exchange(site, assertion(site[3], replacement, next_key, claims={'jti': jti})).status_code == 401


@pytest.mark.parametrize('algorithm', ['HS256', 'none'])
def test_algorithm_confusion_is_rejected_before_signature_verification(site, algorithm):
    _, pem = material()
    cert = register(site, pem).json()
    forged = jwt.encode({'iss': site[3]['id']}, 'untrusted-hmac-key-32-bytes-long!!' if algorithm == 'HS256' else None,
                        algorithm=algorithm, headers={'kid': cert['id']})
    assert exchange(site, forged).status_code == 401


def test_token_exchange_failure_rolls_back_replay_and_token_together(site, monkeypatch):
    from agent_platform.modules.identity import client_certificate_service as service
    key, pem = material()
    cert = register(site, pem).json()
    signed = assertion(site[3], cert, key)
    def unavailable(*args, **kwargs):
        raise DomainError(503, 'test_unavailable', '本机故障回归')
    monkeypatch.setattr(service, 'mint_service_token', unavailable)
    result = exchange(site, signed)
    assert result.status_code == 503
    with site[0].state.database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ServiceClientAssertion)) == 0
        assert db.scalar(select(func.count()).select_from(ServiceAccess)) == 0


def test_certificate_expiry_caps_token_lifetime_and_info_never_uses_host(site):
    key, pem = material(end=0.002)
    cert = register(site, pem).json()
    result = exchange(site, assertion(site[3], cert, key))
    assert result.status_code == 200 and 0 < result.json()['expires_in'] <= 173
    info = site[1].get(f"/api/v1/service-identities/{site[3]['id']}/client-auth", headers={'Host': 'spoof.invalid'})
    assert info.status_code == 200
    assert info.json()['audience'] == info.json()['token_endpoint'] == AUDIENCE
    assert 'spoof' not in info.text


def test_foreign_client_kid_and_invalid_unicode_cannot_escape_authentication_boundary(site):
    key, pem = material()
    cert = register(site, pem).json()
    invalid = exchange(site, assertion(site[3], cert, key, claims={'jti': '\ud800'}))
    assert invalid.status_code == 401 and invalid.json()['error'] == 'invalid_client'
    other = site[1].post('/api/v1/service-identities', json={
        'name': '另一集成方', 'permissions': ['m14.read']}, headers=site[2]).json()
    forged = assertion(other, cert, key)
    assert exchange(site, forged, client_id=other['id']).status_code == 401
    with site[0].state.database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ServiceClientAssertion)) == 0
