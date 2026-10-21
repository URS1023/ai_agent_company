"""本机工具集成凭据：复用证书认证换短令牌，不保存管理员或重写业务授权。"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
from tempfile import NamedTemporaryFile
import time
from uuid import uuid4

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import httpx
import jwt

import windows

KIND = 'ai-agent-company-native-tool-credentials-v1'
PREFIX = 'aic_tool_'
TOKEN_ENDPOINT = 'http://127.0.0.1:8080/api/v1/client-auth/token'
ASSERTION_TYPE = 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer'
READ_SCOPES = frozenset({'m02.read', 'm06.read', 'm07.read', 'm08.read', 'm10.read', 'm11.read'})


class CredentialError(Exception):
    """仅携带可公开的错误码，原生错误、断言和密钥不进入异常文本。"""
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_bytes(path: Path, value: bytes) -> None:
    """同目录原子替换；索引永远不会指向半写的私钥密文。"""
    pending = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False, suffix='.pending') as handle:
            pending = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        pending.chmod(0o600)
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def material(name: str) -> dict:
    """生成独立P-256客户端叶证书；有效期90天，私钥只进入调用方密文。"""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name[:64])])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True).sign(key, hashes.SHA256()))
    return {'private_key_pem': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()).decode(),
        'bridge_key': PREFIX + secrets.token_urlsafe(40),
        'certificate_pem': cert.public_bytes(serialization.Encoding.PEM).decode(),
        'certificate_expires_at': cert.not_valid_after_utc.isoformat()}


class ToolCredentialStore:
    """磁盘仅持久化hash索引和独立密文，短token仅内存缓存，逐请求重读撤销与revision。"""
    def __init__(self, root: Path | None = None, *, clock=None):
        self.root = (root or windows.DEFAULT_ROOT).resolve()
        self.index = self.root / 'tool-credentials.json'
        self.folder = self.root / 'tool-credentials'
        self.clock = clock or time.time
        self.cache: dict[tuple, tuple[str, float]] = {}
        self.faults: set[tuple] = set()
        self.locks: dict[str, asyncio.Lock] = {}

    def _runtime(self) -> dict:
        return windows.load(self.root)

    def _load(self) -> dict:
        runtime = self._runtime()
        if not self.index.exists():
            return {'kind': KIND, 'runtime_id': runtime['id'], 'records': {}}
        if self.index.resolve() != self.index or self.index.stat().st_size > 1024 * 1024:
            raise CredentialError('tool_credential_index_invalid')
        value = json.loads(self.index.read_text('utf-8'))
        if value.get('kind') != KIND or value.get('runtime_id') != runtime['id'] or not isinstance(value.get('records'), dict):
            raise CredentialError('tool_credential_runtime_changed')
        return value

    def _save(self, value: dict) -> None:
        atomic_bytes(self.index, json.dumps(value, ensure_ascii=False, indent=2).encode())

    def _cipher(self) -> Fernet:
        key = self.root / 'runtime.key'
        if key.resolve() != key or not key.is_file():
            raise CredentialError('tool_credential_master_key_missing')
        return Fernet(key.read_bytes().strip())

    def _secret(self, row: dict) -> dict:
        name = row.get('secret_file', '')
        if not re.fullmatch(r'[a-f0-9]{32}\.enc', name):
            raise CredentialError('tool_credential_secret_path_invalid')
        path = self.folder / name
        if path.resolve() != path or not path.is_relative_to(self.root) or path.stat().st_size > 32768:
            raise CredentialError('tool_credential_secret_path_invalid')
        value = json.loads(self._cipher().decrypt(path.read_bytes()))
        if value.get('id') != row['id'] or sha256(value['bridge_key'].encode()).hexdigest() != row['key_hash']:
            raise CredentialError('tool_credential_secret_identity_changed')
        cert = x509.load_pem_x509_certificate(row['certificate_pem'].encode())
        private = serialization.load_pem_private_key(value['private_key_pem'].encode(), password=None)
        if (cert.public_key().public_numbers() != private.public_key().public_numbers()
                or cert.not_valid_after_utc.isoformat() != row['certificate_expires_at']):
            raise CredentialError('tool_credential_certificate_key_mismatch')
        return value

    def _new(self, engine: str, name: str, *, id: str | None = None, revision=1) -> dict:
        if engine not in {'dify', 'openwebui'} or not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise CredentialError('tool_credential_prepare_invalid', 422)
        values, id = material(name.strip()), id or uuid4().hex
        self.folder.mkdir(exist_ok=True)
        if self.folder.resolve() != self.folder:
            raise CredentialError('tool_credential_folder_redirected')
        self.folder.chmod(0o700)
        filename = uuid4().hex + '.enc'
        secret = {key: values[key] for key in ('private_key_pem', 'bridge_key')} | {'id': id}
        # 先落独立密文，再原子写索引；中断最多留下不可调用的密文孤件，不接管或自动恢复。
        atomic_bytes(self.folder / filename, self._cipher().encrypt(json.dumps(secret).encode()))
        return {'id': id, 'engine': engine, 'name': name.strip(), 'revision': revision, 'enabled': True,
            'phase': 'prepared', 'key_hash': sha256(values['bridge_key'].encode()).hexdigest(),
            'secret_file': filename, 'certificate_pem': values['certificate_pem'],
            'certificate_expires_at': values['certificate_expires_at'], 'created_at': utc_now(), 'history': []}

    @staticmethod
    def public(row: dict) -> dict:
        return {key: value for key, value in row.items() if key not in {'secret_file', 'key_hash', 'history'}}

    def prepare(self, engine: str, name: str) -> dict:
        with windows.runtime_lock(self.root):
            value = self._load()
            if len(value['records']) >= 100:
                raise CredentialError('tool_credential_record_limit', 409)
            row = self._new(engine, name)
            value['records'][row['id']] = row
            self._save(value)
            return self.public(row)

    @staticmethod
    def _row(value: dict, id: str, revision: int | None = None) -> dict:
        row = value['records'].get(id)
        if row is None:
            raise CredentialError('tool_credential_missing', 404)
        if revision is not None and (type(revision) is not int or row['revision'] != revision):
            raise CredentialError('tool_credential_revision_conflict', 409)
        return row

    @staticmethod
    def _binding(binding: dict) -> dict:
        fields = {'workspace_id', 'service_identity_id', 'certificate_id', 'scope', 'token_endpoint', 'audience'}
        if not isinstance(binding, dict) or set(binding) != fields:
            raise CredentialError('tool_credential_binding_invalid', 422)
        for key in ('workspace_id', 'service_identity_id', 'certificate_id'):
            if not isinstance(binding[key], str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', binding[key]):
                raise CredentialError('tool_credential_binding_invalid', 422)
        scopes = binding['scope']
        if (not isinstance(scopes, list) or not scopes or any(not isinstance(scope, str) for scope in scopes)
                or len(scopes) != len(set(scopes))
                or any(scope not in READ_SCOPES for scope in scopes) or 'm02.read' not in scopes
                or binding['token_endpoint'] != TOKEN_ENDPOINT or binding['audience'] != TOKEN_ENDPOINT):
            raise CredentialError('tool_credential_binding_invalid', 422)
        return {**binding, 'scope': sorted(scopes)}

    def import_binding(self, id: str, binding: dict, *, revision: int) -> dict:
        binding = self._binding(binding)
        with windows.runtime_lock(self.root):
            value = self._load()
            row = self._row(value, id, revision)
            if not row['enabled']:
                raise CredentialError('tool_credential_revoked', 409)
            self._secret(row)
            previous = row.get('binding')
            # 对既有记录只能确认同一身份；改工作区或证书须显式轮换，不能把旧key转授他人。
            if previous is not None and previous != binding:
                raise CredentialError('tool_credential_binding_change_requires_rotation', 409)
            previous_auth = row.pop('authentication', None)
            if previous_auth:
                row['authentication_history'] = [*row.get('authentication_history', []), previous_auth][-20:]
            row.update(binding=binding, phase='configured', revision=revision + 1, configured_at=utc_now())
            self._save(value)
            return self.public(row)

    def revoke(self, id: str, *, revision: int) -> dict:
        with windows.runtime_lock(self.root):
            value = self._load()
            row = self._row(value, id, revision)
            row.update(enabled=False, phase='revoked', revision=revision + 1, revoked_at=utc_now())
            self._save(value)
            return self.public(row)

    def rotate(self, id: str, *, revision: int) -> dict:
        with windows.runtime_lock(self.root):
            value = self._load()
            old = self._row(value, id, revision)
            row = self._new(old['engine'], old['name'], id=id, revision=revision + 1)
            row['history'] = [*old.get('history', []), {key: old[key] for key in
                ('revision', 'key_hash', 'certificate_expires_at', 'phase')}]
            row['authentication_history'] = [*old.get('authentication_history', []),
                *([old['authentication']] if old.get('authentication') else [])][-20:]
            value['records'][id] = row
            self._save(value)
            return self.public(row)

    def get_native_key(self, id: str) -> str:
        """只供本机初始化器在内存填原生凭据，CLI不打印或导出此值。"""
        row = self._row(self._load(), id)
        if not row['enabled']:
            raise CredentialError('tool_credential_revoked', 409)
        return self._secret(row)['bridge_key']

    def match(self, raw: str) -> dict:
        digest = sha256(raw.encode()).hexdigest()
        matches = [row for row in self._load()['records'].values() if hmac.compare_digest(row.get('key_hash', ''), digest)]
        if len(matches) != 1 or not matches[0].get('enabled') or matches[0].get('phase') != 'configured':
            raise CredentialError('tool_credential_unknown_or_revoked', 401)
        return matches[0]

    @staticmethod
    def _cache_key(row: dict) -> tuple:
        binding = row['binding']
        return (row['id'], row['revision'], row['engine'], binding['workspace_id'], binding['service_identity_id'],
            binding['certificate_id'], tuple(binding['scope']))

    def reject_token(self, raw: str) -> None:
        """上游401后不自动重换令牌；管理员审阅后CAS确认/轮换才恢复认证。"""
        try:
            row = self.match(raw)
            key = self._cache_key(row)
            self.cache.pop(key, None)
            self.faults.add(key)
            self._authentication_state(row, 'failed', reason='upstream_401')
        except CredentialError:
            pass

    def _authentication_state(self, row: dict, status: str, *, reason: str | None = None) -> None:
        """认证预约先落盘；进程中断保留attempted，重启不会自动重发不确定请求。"""
        with windows.runtime_lock(self.root):
            value = self._load()
            current = self._row(value, row['id'], row['revision'])
            if not current.get('enabled'):
                raise CredentialError('tool_credential_unknown_or_revoked', 401)
            previous = current.get('authentication')
            if status == 'attempted':
                if previous and previous.get('status') in {'attempted', 'failed'}:
                    raise CredentialError('tool_credential_authentication_requires_review')
                # 完整签发审计保留在平台；本机索引仅保留有限状态摘要，不堆积令牌或断言。
                current['authentication_history'] = [*current.get('authentication_history', []), *([previous] if previous else [])][-20:]
                current['authentication'] = {'id': uuid4().hex, 'status': status, 'at': utc_now()}
            else:
                current['authentication'] = {**(previous or {'id': uuid4().hex, 'at': utc_now()}),
                    'status': status, 'finished_at': utc_now()}
                if reason:
                    current['authentication']['reason'] = reason
            self._save(value)

    async def authorization(self, raw: str, source: str, network: dict, client: httpx.AsyncClient) -> str:
        if not raw.startswith(PREFIX):
            if source == network['source_ip']:
                return 'Bearer ' + raw
            raise CredentialError('tool_credential_host_key_required', 403)
        row = self.match(raw)
        expected_source = network['source_ip'] if row['engine'] == 'dify' else network['bind_ip']
        if source != expected_source:
            raise CredentialError('tool_credential_engine_source_denied', 403)
        async with self.locks.setdefault(row['id'], asyncio.Lock()):
            row = self.match(raw)
            key = self._cache_key(row)
            if key in self.faults or row.get('authentication', {}).get('status') in {'attempted', 'failed'}:
                raise CredentialError('tool_credential_authentication_requires_review')
            expires = datetime.fromisoformat(row['certificate_expires_at']).timestamp()
            if expires <= self.clock() + 30:
                raise CredentialError('tool_credential_certificate_expiring')
            cached = self.cache.get(key)
            if cached and cached[1] > self.clock() + 30:
                return 'Bearer ' + cached[0]
            self._authentication_state(row, 'attempted')
            try:
                token, token_expires = await self._exchange(row, client)
                # 认证期间发生本机撤销/轮换时丢弃返回值，不把旧预约的token交给业务接口。
                if self._cache_key(self.match(raw)) != key:
                    raise CredentialError('tool_credential_changed_during_authentication')
                self._authentication_state(row, 'succeeded')
                self.cache[key] = (token, min(expires, token_expires))
                return 'Bearer ' + token
            except BaseException as error:
                self.faults.add(key)
                self.cache.pop(key, None)
                try:
                    self._authentication_state(row, 'failed', reason='exchange_failed')
                except Exception:
                    # 若已撤销/轮换，不覆盖新revision；原attempted或新配置均保留给操作者核查。
                    pass
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise CredentialError('tool_credential_authentication_failed') from None

    async def _exchange(self, row: dict, client: httpx.AsyncClient) -> tuple[str, float]:
        binding = self._binding(row['binding'])
        secret = self._secret(row)
        now = int(self.clock())
        assertion = jwt.encode({'iss': binding['service_identity_id'], 'sub': binding['service_identity_id'],
            'aud': binding['audience'], 'iat': now, 'nbf': now, 'exp': now + 120, 'jti': secrets.token_hex(24)},
            secret['private_key_pem'], algorithm='ES256', headers={'kid': binding['certificate_id']})
        async def read_json(method: str, url: str, **kwargs) -> dict:
            response = None
            try:
                response = await client.send(client.build_request(method, url, **kwargs), stream=True, follow_redirects=False)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 32768:
                        raise CredentialError('tool_credential_token_response_invalid')
                if response.status_code != 200:
                    raise CredentialError('tool_credential_token_exchange_rejected')
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise CredentialError('tool_credential_token_response_invalid')
                return value
            finally:
                if response is not None:
                    await asyncio.shield(response.aclose())

        async with asyncio.timeout(10):
            value = await read_json('POST', TOKEN_ENDPOINT, data={
                'grant_type': 'client_credentials', 'client_id': binding['service_identity_id'],
                'client_assertion_type': ASSERTION_TYPE, 'client_assertion': assertion,
                'scope': ' '.join(binding['scope'])}, headers={'User-Agent': 'manufacturing-agent-platform/0.1 ToolCredentials'})
            duration = value.get('expires_in')
            token = value.get('access_token')
            if (value.get('token_type') != 'Bearer' or type(duration) is not int or not 30 < duration <= 600
                    or not isinstance(token, str) or not token.startswith('service_') or len(token) > 4096
                    or set(value.get('scope', '').split()) != set(binding['scope'])):
                raise CredentialError('tool_credential_token_response_invalid')
            # opaque token本身不携带可验证的工作区；用同一固定本机平台的/me核一次实际身份。
            identity = await read_json('GET', 'http://127.0.0.1:8080/api/v1/me',
                headers={'Authorization': 'Bearer ' + token})
            if (identity.get('workspace', {}).get('id') != binding['workspace_id']
                    or identity.get('user', {}).get('is_admin') is not False
                    or not isinstance(identity.get('permissions'), list)
                    or set(identity['permissions']) - set(binding['scope'])):
                raise CredentialError('tool_credential_workspace_or_permissions_changed')
            return token, min(self.clock(), now) + duration


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'import', 'revoke', 'rotate'])
    parser.add_argument('--engine', choices=['dify', 'openwebui'])
    parser.add_argument('--name')
    parser.add_argument('--id')
    parser.add_argument('--revision', type=int)
    args = parser.parse_args(argv)
    store = ToolCredentialStore()
    try:
        if args.action == 'prepare':
            if not args.engine or not args.name or args.id or args.revision:
                parser.error('prepare必须指定engine/name，不接受已有id/revision')
            result = store.prepare(args.engine, args.name)
        else:
            if not args.id or args.revision is None or args.engine or args.name:
                parser.error('操作必须指定id和当前revision')
            if args.action == 'import':
                payload = sys.stdin.buffer.read(32769)
                if len(payload) > 32768:
                    raise CredentialError('tool_credential_binding_invalid', 422)
                result = store.import_binding(args.id, json.loads(payload), revision=args.revision)
            else:
                result = getattr(store, args.action)(args.id, revision=args.revision)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_code': error.code if isinstance(error, CredentialError)
            else 'tool_credential_operation_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
