"""分阶段登记本机制造只读工具；不启动服务、不运行模型，未知写入不得重复派发。"""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from cryptography import x509
from cryptography.hazmat.primitives import hashes
import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'deploy/native'), str(REPO / 'tools')]
import windows
import wsl
import wsl_http_bridge as bridge
from wsl_tool_credentials import ToolCredentialStore, TOKEN_ENDPOINT, atomic_bytes

ORIGINS = {'platform': 'http://127.0.0.1:8080', 'openwebui': 'http://127.0.0.1:13000',
    'dify': 'http://127.0.0.1:13001'}
PHASES = ('prepare', 'register', 'native-config', 'probe')
SCOPES = ['m02.read', 'm07.read', 'm10.read', 'm11.read']
MAX_HTTP = 25
MAX_SECONDS = 180
MAX_RESPONSE = 2 * 1024 * 1024


class ConfigurationError(Exception):
    """只向收据输出固定错误码，原生响应和身份凭据保留在内存。"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Ledger:
    """逐操作先预约后执行；密文账本不保管管理员密码、登录Cookie或Token。"""
    def __init__(self, run_id: str, *, repo: Path = REPO):
        if not re.fullmatch('[A-Za-z0-9._-]{8,64}', run_id):
            raise ConfigurationError('invalid_run_id')
        self.run_id, self.repo = run_id, repo.resolve()
        self.root = self.repo / '.local/native-business-tools' / run_id
        self.path = self.root / 'ledger.enc'
        self.key = self.repo / '.local/desktop-access.key'

    def cipher(self):
        if self.key.resolve() != self.key or not self.key.is_file():
            raise ConfigurationError('desktop_delivery_key_missing')
        return Fernet(self.key.read_bytes().strip())

    def read(self):
        if self.path.resolve() != self.path or self.path.stat().st_size > 4 * 1024 * 1024:
            raise ConfigurationError('operation_ledger_path_invalid')
        value = json.loads(self.cipher().decrypt(self.path.read_bytes()))
        if value.get('kind') != 'native-business-tools-v1' or value.get('run_id') != self.run_id:
            raise ConfigurationError('operation_ledger_identity_changed')
        return value

    def create(self):
        if self.root.exists():
            raise ConfigurationError('run_id_already_exists')
        parent = self.root.parent
        parent.mkdir(parents=True, exist_ok=True)
        if parent.resolve() != parent:
            raise ConfigurationError('operation_ledger_parent_redirected')
        self.root.mkdir()
        self.root.chmod(0o700)
        self.cipher()
        value = {'kind': 'native-business-tools-v1', 'run_id': self.run_id, 'created_at': now(),
            'phases': {}, 'engines': {}}
        atomic_bytes(self.path, self.cipher().encrypt(json.dumps(value).encode()))

    def update(self, change):
        with windows.runtime_lock(self.root):
            value = self.read()
            change(value)
            atomic_bytes(self.path, self.cipher().encrypt(json.dumps(value, ensure_ascii=False).encode()))
            return value

    def begin(self, phase):
        def change(value):
            previous = value['phases'].get(phase)
            if previous is not None:
                raise ConfigurationError('phase_already_attempted_requires_review')
            index = PHASES.index(phase)
            if index and value['phases'].get(PHASES[index - 1], {}).get('status') != 'succeeded':
                raise ConfigurationError('previous_phase_not_succeeded')
            value['phases'][phase] = {'status': 'running', 'started_at': now(), 'primary_http': 0,
                'authentication_http_reserved': 0, 'authentication_http_observed': 0, 'operations': {}}
        self.update(change)

    def reserve(self, phase, name, method, target, path, body=None, *, extra_auth=0):
        def change(value):
            record = value['phases'][phase]
            if record['status'] != 'running' or name in record['operations']:
                raise ConfigurationError('operation_already_attempted')
            increment = 0 if method == 'LOCAL' else 1
            if record['primary_http'] + record['authentication_http_reserved'] + increment + extra_auth > MAX_HTTP:
                raise ConfigurationError('phase_http_budget_exceeded')
            record['primary_http'] += increment
            record['authentication_http_reserved'] += extra_auth
            record['operations'][name] = {'status': 'attempted', 'method': method, 'target': target,
                'path': path, 'request_sha256': digest(body), 'at': now(), 'authentication_http_reserved': extra_auth}
        self.update(change)

    def completed(self, phase, name, *, http_status=None, auth_observed=0):
        def change(value):
            record = value['phases'][phase]
            record['operations'][name].update(status='succeeded', finished_at=now(), http_status=http_status)
            record['authentication_http_observed'] += auth_observed
        self.update(change)

    def public(self):
        value = self.read()
        phases = {name: {key: record[key] for key in ('status', 'primary_http', 'authentication_http_reserved',
            'authentication_http_observed', 'error_code') if key in record} for name, record in value['phases'].items()}
        return {'run_id': self.run_id, 'phases': phases, 'ledger': str(self.path), 'model_calls': 0,
            'enterprise_source_queries': 0, 'implicit_native_http_count_verified': True}


def access_accounts(repo=REPO) -> dict:
    """只在root实际执行阶段解密既有交付账号；不把管理员身份写入新账本。"""
    cipher = Fernet((repo / '.local/desktop-access.key').read_bytes().strip())
    accounts = json.loads(cipher.decrypt((repo / '.local/desktop-access.enc').read_bytes()))
    result = {}
    for engine, origin in ORIGINS.items():
        matches = [value for value in accounts.values() if value.get('url', '').rstrip('/') == origin]
        if len(matches) != 1 or not matches[0].get('username') or not matches[0].get('password'):
            raise ConfigurationError('delivery_account_missing:' + engine)
        result[engine] = {key: matches[0][key] for key in ('username', 'password')}
    service = json.loads(cipher.decrypt((repo / '.local/dify-owui-service.enc').read_bytes()))
    if not re.fullmatch('[A-Za-z0-9_-]{8,100}', service.get('group_id', '')):
        raise ConfigurationError('dedicated_owui_group_missing')
    result['group_id'] = service['group_id']
    return result


def owned_context() -> dict:
    """执行前只读核当前三入口、专属WSL新PG及固定桥，不连接数据库。"""
    from verify_dify_native import check_ownership
    native = windows.load(windows.DEFAULT_ROOT)
    network = wsl.verified_nat_network()
    upstreams = bridge.capture_upstreams(windows.DEFAULT_ROOT)
    process = bridge.owned_bridge(windows.DEFAULT_ROOT, native)
    info = native['processes'].get(bridge.COMPONENT, {})
    if not process or not info.get('ready') or info.get('network') != network or info.get('upstreams') != upstreams:
        raise ConfigurationError('owned_bridge_not_current')
    listener = bridge.listener_signature(process, Path(info['command'][0]), info['command'], bridge.PROJECT,
        bridge.PORT, network['bind_ip'])
    if listener != info.get('listener'):
        raise ConfigurationError('owned_bridge_listener_changed')
    bridge.require_host_interface(network)
    proof = check_ownership(network['runtime_id'], 20)
    return {'native_runtime_id': native['id'], 'wsl_runtime_id': proof['runtime_id'], 'network': network,
        'bridge_launch_id': info['launch_id'], 'upstreams': upstreams}


WSL_GET = '''import base64,http.client,json,sys
try:
    payload=json.loads(sys.stdin.buffer.read(32769))
    if payload['path'] not in ['/platform/api/v1/business-tools/openapi.json','/platform/api/v1/devices?limit=1']:
        raise ValueError()
    connection=http.client.HTTPConnection(payload['host'],13002,timeout=15)
    connection.request('GET',payload['path'],headers={'Authorization':'Bearer '+payload['key'],'User-Agent':'manufacturing-agent-platform/0.1 ToolConfiguration'})
    response=connection.getresponse()
    content=response.read(2097153)
    if len(content)>2097152:raise ValueError()
    print(json.dumps({'status':response.status,'body':base64.b64encode(content).decode()}))
    connection.close()
except Exception:
    print(json.dumps({'error':'wsl_tool_probe_failed'}))
    sys.exit(1)
'''


def wsl_bridge_get(network: dict, path: str, key: str, seconds: float) -> tuple[int, bytes]:
    """密钥仅经匿名stdin进入固定本机发行版；不使用命令参数、环境变量或代理配置。"""
    executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'
    if seconds <= 3:
        raise ConfigurationError('wsl_bridge_request_deadline')
    linux_seconds = min(15, seconds - 3)
    result = subprocess.run([str(executable), '--distribution', wsl.DISTRO, '--user', 'root', '--exec',
        '/usr/bin/timeout', '--signal=TERM', '--kill-after=2', str(linux_seconds),
        wsl.LINUX_ROOT + '/tooling/bin/python', '-c', WSL_GET], input=json.dumps({
            'host': network['bind_ip'], 'path': path, 'key': key}).encode(), capture_output=True,
        env=wsl.clean_environment(), timeout=linux_seconds + 2.5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or len(result.stdout) > 3 * 1024 * 1024:
        raise ConfigurationError('wsl_bridge_request_failed')
    value = json.loads(result.stdout)
    if set(value) != {'status', 'body'}:
        raise ConfigurationError('wsl_bridge_response_invalid')
    return value['status'], base64.b64decode(value['body'], validate=True)


def validate_schema(document: dict, network: dict) -> dict:
    if (not isinstance(document, dict) or set(document.get('paths', {})) != set(bridge.READ_TOOLS)
            or any(set(operations) != {'get'} for operations in document['paths'].values())
            or document.get('servers') != [{'url': f"http://{network['bind_ip']}:13002/platform"}]):
        raise ConfigurationError('fifteen_read_tool_schema_changed')
    def refs(value):
        if isinstance(value, dict):
            reference = value.get('$ref')
            if reference is not None and (not isinstance(reference, str) or not reference.startswith('#/components/')):
                raise ConfigurationError('external_tool_schema_reference_denied')
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(document)
    if any('servers' in item or 'servers' in item['get'] for item in document['paths'].values()):
        raise ConfigurationError('operation_tool_server_override_denied')
    return document


def merge_owui_connections(existing: list, connection: dict) -> list:
    """保留既有配置；会引起未知Schema/OAuth外联的旧连接必须先由root审阅。"""
    if not isinstance(existing, list) or len(existing) > 100:
        raise ConfigurationError('owui_existing_connections_invalid')
    for item in existing:
        if not isinstance(item, dict) or not isinstance(item.get('config') or {}, dict) or not isinstance(item.get('info') or {}, dict):
            raise ConfigurationError('owui_existing_connections_invalid')
        if (item.get('config') or {}).get('enable') or (
                item.get('type') == 'mcp' and item.get('auth_type') in {'oauth_2.1', 'oauth_2.1_static'}):
            raise ConfigurationError('owui_existing_active_connections_require_review')
        if (item.get('info') or {}).get('id') == connection['info']['id']:
            raise ConfigurationError('owui_tool_connection_already_exists')
    return [*deepcopy(existing), deepcopy(connection)]


class Runner:
    """每阶段最多25本机HTTP/180秒，任何失败结束该阶段，不重新创建或自动恢复。"""
    def __init__(self, ledger: Ledger, *, store=None, accounts=None, ownership=None, transport=None, wsl_get=None):
        self.ledger = ledger
        self.store = store or ToolCredentialStore()
        self.accounts = accounts
        self.ownership = ownership or owned_context
        self.transport = transport
        self.wsl_get = wsl_get or wsl_bridge_get
        self.clients = {}
        self.headers = {}
        self.deadline = 0

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise ConfigurationError('phase_total_deadline')
        return seconds

    def recheck(self):
        """阶段内钉住真实进程；账号/Key不能在已核上游被替换后继续发送。"""
        self.remaining()
        if isinstance(self.transport, httpx.MockTransport):
            return
        native = windows.load(windows.DEFAULT_ROOT)
        for engine in ('platform', 'openwebui'):
            if bridge.signature(windows.DEFAULT_ROOT, engine, native) != self.proof['upstreams'][engine]:
                raise ConfigurationError('upstream_changed_during_phase')
        info = native['processes'].get(bridge.COMPONENT, {})
        if not bridge.owned_bridge(windows.DEFAULT_ROOT, native) or info.get('launch_id') != self.proof['bridge_launch_id']:
            raise ConfigurationError('bridge_changed_during_phase')

    def request(self, name, engine, method, path, body=None, *, expected=(200,)):
        self.recheck()
        self.ledger.reserve(self.phase, name, method, engine, path, body)
        headers = dict(self.headers.get(engine, {}))
        headers['Origin'] = ORIGINS[engine]
        if engine == 'dify':
            csrf = self.clients[engine].cookies.get('csrf_token')
            if csrf:
                headers['X-CSRF-Token'] = csrf
            elif path != '/console/api/login':
                raise ConfigurationError('dify_console_csrf_missing')
        status, content = self.bounded_http(method, ORIGINS[engine] + path, body, headers, self.clients[engine].cookies)
        if status not in expected:
            raise ConfigurationError('http_status_' + str(status) + ':' + name)
        value = json.loads(content)
        self.ledger.completed(self.phase, name, http_status=status)
        return value

    def bounded_http(self, method, url, body, headers, cookies=None):
        """总时限包住连接、响应头与所有分块读取，不能用逐块read timeout冒充阶段预算。"""
        seconds = min(20, self.remaining())
        async def read():
            async with asyncio.timeout(seconds):
                async with httpx.AsyncClient(transport=self.transport or httpx.AsyncHTTPTransport(retries=0),
                        trust_env=False, follow_redirects=False, cookies=cookies, timeout=seconds) as client:
                    async with client.stream(method, url, json=body, headers=headers) as response:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_RESPONSE:
                                raise ConfigurationError('response_size_budget_exceeded')
                        if cookies is not None:
                            cookies.update(client.cookies)
                        return response.status_code, bytes(content)
        return asyncio.run(read())

    def local(self, name, call):
        self.ledger.reserve(self.phase, name, 'LOCAL', 'credential_store', name)
        value = call()
        self.ledger.completed(self.phase, name)
        return value

    def bridge_get(self, engine, path, name):
        self.recheck()
        record = self.ledger.read()['engines'][engine]
        raw = self.store.get_native_key(record['credential_id'])
        previous = self.store._load()['records'][record['credential_id']].get('authentication', {}).get('id')
        self.ledger.reserve(self.phase, name, 'GET', 'bridge:' + engine, path, extra_auth=2)
        if engine == 'dify':
            status, content = self.wsl_get(self.proof['network'], path, raw, self.remaining())
        else:
            status, content = self.bounded_http('GET', f"http://{self.proof['network']['bind_ip']}:13002" + path,
                None, {'Authorization': 'Bearer ' + raw})
        if status != 200:
            raise ConfigurationError('bridge_http_status_' + str(status) + ':' + engine)
        value = json.loads(content)
        current = self.store._load()['records'][record['credential_id']].get('authentication', {})
        observed = 2 if current.get('id') != previous and current.get('status') == 'succeeded' else 0
        self.ledger.completed(self.phase, name, http_status=status, auth_observed=observed)
        return value

    def login(self, engine):
        account = self.accounts[engine]
        if engine == 'platform':
            value = self.request('platform-login', engine, 'POST', '/api/v1/auth/login', account)
            if value.get('user', {}).get('is_admin') is not True or not value.get('csrf_token'):
                raise ConfigurationError('platform_admin_login_required')
            self.headers[engine] = {'X-CSRF-Token': value['csrf_token']}
        elif engine == 'openwebui':
            value = self.request('owui-login', engine, 'POST', '/api/v1/auths/signin',
                {'email': account['username'], 'password': account['password']})
            if value.get('role') != 'admin' or not value.get('token'):
                raise ConfigurationError('owui_admin_login_required')
            self.headers[engine] = {'Authorization': 'Bearer ' + value['token']}
        else:
            value = self.request('dify-login', engine, 'POST', '/console/api/login',
                {'email': account['username'], 'password': base64.b64encode(account['password'].encode()).decode(), 'remember_me': False})
            if value.get('result') != 'success' or not self.clients[engine].cookies.get('csrf_token') or not self.clients[engine].cookies.get('access_token'):
                raise ConfigurationError('dify_cookie_login_required')
        return value

    def prepare(self):
        for engine in ('dify', 'openwebui'):
            row = self.local('prepare-' + engine, lambda: self.store.prepare(engine, '制造只读工具 · ' + engine))
            self.ledger.update(lambda value: value['engines'].update({engine: {'credential_id': row['id'], 'revision': row['revision']}}))

    def register(self):
        account = self.login('platform')
        workspace_id = account['workspace']['id']
        for engine in ('dify', 'openwebui'):
            local = self.ledger.read()['engines'][engine]
            row = self.store._load()['records'][local['credential_id']]
            identity = self.request(engine + '-identity', 'platform', 'POST', '/api/v1/service-identities',
                {'name': '制造只读工具 · ' + engine + ' · ' + self.ledger.run_id[-12:],
                    'description': '', 'caller_kind': 'integration', 'permissions': SCOPES}, expected=(201,))
            if identity.get('workspace_id') != workspace_id or set(identity.get('permissions', [])) != set(SCOPES):
                raise ConfigurationError('created_service_identity_scope_changed')
            self.ledger.update(lambda value: value['engines'][engine].update(identity_id=identity['id'], workspace_id=workspace_id))
            certificate = self.request(engine + '-certificate', 'platform', 'POST',
                '/api/v1/service-identities/' + identity['id'] + '/certificates',
                {'name': '本机工具调用', 'certificate_pem': row['certificate_pem']}, expected=(201,))
            fingerprint = x509.load_pem_x509_certificate(row['certificate_pem'].encode()).fingerprint(hashes.SHA256()).hex()
            if (certificate.get('workspace_id') != workspace_id or certificate.get('identity_id') != identity['id']
                    or certificate.get('status') != 'active' or certificate.get('fingerprint_sha256') != fingerprint):
                raise ConfigurationError('registered_certificate_identity_changed')
            self.ledger.update(lambda value: value['engines'][engine].update(certificate_id=certificate['id']))
            info = self.request(engine + '-client-auth', 'platform', 'GET', '/api/v1/service-identities/' + identity['id'] + '/client-auth')
            if (info.get('client_id') != identity['id'] or info.get('token_endpoint') != TOKEN_ENDPOINT
                    or info.get('audience') != TOKEN_ENDPOINT or info.get('token_auth_method') != 'private_key_jwt'):
                raise ConfigurationError('registered_client_auth_contract_changed')
            configured = self.local(engine + '-import-binding', lambda: self.store.import_binding(local['credential_id'], {
                'workspace_id': workspace_id, 'service_identity_id': identity['id'], 'certificate_id': certificate['id'],
                'scope': SCOPES, 'token_endpoint': info['token_endpoint'], 'audience': info['audience']}, revision=local['revision']))
            self.ledger.update(lambda value: value['engines'][engine].update(revision=configured['revision']))

    def native_config(self):
        self.login('dify')
        members = self.request('dify-members', 'dify', 'GET', '/console/api/workspaces/current/members').get('accounts')
        if (not isinstance(members, list) or len(members) != 1 or members[0].get('email') != self.accounts['dify']['username']
                or members[0].get('role') not in {'owner', 'admin'}):
            raise ConfigurationError('dify_workspace_not_dedicated_requires_review')
        self.login('openwebui')
        group_id = self.accounts['group_id']
        group = self.request('owui-group', 'openwebui', 'GET', '/api/v1/groups/id/' + group_id)
        if group.get('id') != group_id:
            raise ConfigurationError('dedicated_owui_group_changed')
        original = self.request('owui-connections', 'openwebui', 'GET', '/api/v1/configs/tool_servers')['TOOL_SERVER_CONNECTIONS']
        connection_id = 'manufacturing-read-' + self.ledger.run_id
        merge_owui_connections(original, {'info': {'id': connection_id}})
        self.ledger.update(lambda value: value.update(original_owui_connections=original))
        schema = validate_schema(self.bridge_get('dify', '/platform' + bridge.SCHEMA_PATH, 'dify-schema'), self.proof['network'])
        provider = 'manufacturing-read-' + self.ledger.run_id[-20:]
        result = self.request('dify-add-provider', 'dify', 'POST', '/console/api/workspaces/current/tool-provider/api/add',
            {'provider': provider, 'icon': {'content': '🔧', 'background': '#EFF4FF'}, 'schema_type': 'openapi',
                'schema': json.dumps(schema), 'credentials': {'auth_type': 'api_key_header', 'api_key_header': 'Authorization',
                    'api_key_header_prefix': 'bearer', 'api_key_value': self.store.get_native_key(self.ledger.read()['engines']['dify']['credential_id'])}})
        if result.get('result') != 'success':
            raise ConfigurationError('dify_tool_provider_not_created')
        self.ledger.update(lambda value: value['engines']['dify'].update(provider=provider))
        tools = self.request('dify-tools', 'dify', 'GET', '/console/api/workspaces/current/tool-provider/api/tools?provider=' + quote(provider, safe=''))
        if not isinstance(tools, list) or {item.get('name') for item in tools} != {item[1] for item in bridge.READ_TOOLS.values()}:
            raise ConfigurationError('dify_fifteen_tools_not_registered')
        schema = validate_schema(self.bridge_get('openwebui', '/platform' + bridge.SCHEMA_PATH, 'owui-schema'), self.proof['network'])
        connection = {'url': f"http://{self.proof['network']['bind_ip']}:13002/platform", 'path': '/api/v1/business-tools/openapi.json',
            'type': 'openapi', 'auth_type': 'bearer', 'forward_cookies': False, 'headers': None,
            'key': self.store.get_native_key(self.ledger.read()['engines']['openwebui']['credential_id']),
            'spec_type': 'json', 'spec': json.dumps(schema), 'info': {'id': connection_id},
            'config': {'enable': True, 'access_grants': [{'principal_type': 'group', 'principal_id': group_id, 'permission': 'read'}]}}
        merged = merge_owui_connections(original, connection)
        saved = self.request('owui-save-connections', 'openwebui', 'POST', '/api/v1/configs/tool_servers', {'TOOL_SERVER_CONNECTIONS': merged})
        if saved.get('TOOL_SERVER_CONNECTIONS') != merged:
            raise ConfigurationError('owui_connection_write_projection_changed')
        readback = self.request('owui-readback', 'openwebui', 'GET', '/api/v1/configs/tool_servers')
        if readback.get('TOOL_SERVER_CONNECTIONS') != merged:
            raise ConfigurationError('owui_connection_readback_changed')
        self.ledger.update(lambda value: value['engines']['openwebui'].update(connection_id=connection['info']['id'], group_id=group_id))

    def probe(self):
        for engine in ('dify', 'openwebui'):
            validate_schema(self.bridge_get(engine, '/platform' + bridge.SCHEMA_PATH, engine + '-probe-schema'), self.proof['network'])
            result = self.bridge_get(engine, '/platform/api/v1/devices?limit=1', engine + '-probe-directory')
            if not isinstance(result, dict) or not isinstance(result.get('items'), list) or len(result['items']) > 1:
                raise ConfigurationError('bounded_device_directory_contract_changed')

    def execute(self, phase):
        self.phase, self.deadline = phase, time.monotonic() + MAX_SECONDS
        if phase == 'prepare':
            self.ledger.create()
        else:
            previous = self.ledger.read()['phases'].get(phase, {})
            if previous.get('status') == 'succeeded':
                return self.ledger.public()
        self.ledger.begin(phase)
        try:
            self.proof = self.ownership()
            existing = self.ledger.read().get('runtime_ids')
            actual = {key: self.proof[key] for key in ('native_runtime_id', 'wsl_runtime_id')}
            if existing is not None and existing != actual:
                raise ConfigurationError('runtime_changed_between_phases')
            self.ledger.update(lambda value: value.update(runtime_ids=actual))
            self.ledger.update(lambda value: value['phases'][phase].update(ownership=self.proof))
            if phase != 'prepare':
                self.accounts = self.accounts or access_accounts(self.ledger.repo)
            with ExitStack() as stack:
                for engine, origin in ORIGINS.items():
                    self.clients[engine] = stack.enter_context(httpx.Client(base_url=origin, trust_env=False,
                        follow_redirects=False, transport=self.transport or httpx.HTTPTransport(retries=0)))
                getattr(self, phase.replace('-', '_'))()
            self.remaining()
            self.ledger.update(lambda value: value['phases'][phase].update(status='succeeded', finished_at=now()))
        except BaseException as error:
            code = str(error) if isinstance(error, ConfigurationError) else type(error).__name__
            self.ledger.update(lambda value: value['phases'][phase].update(status='failed', error_code=code, finished_at=now()))
            raise ConfigurationError(code) from None
        finally:
            self.headers.clear()
            self.clients.clear()
            self.accounts = None
        return self.ledger.public()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PHASES)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        result = Runner(Ledger(args.run_id)).execute(args.phase)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'passed': False, 'error_code': str(error) if isinstance(error, ConfigurationError)
            else type(error).__name__, 'model_calls': 0, 'requires_review': True}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
