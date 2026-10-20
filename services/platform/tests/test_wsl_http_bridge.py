"""只用ASGI/HTTPX替身、进程替身与临时文件，不启动WSL或连接实际上游。"""

import asyncio
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / 'deploy/native') not in sys.path:
    sys.path.insert(0, str(ROOT / 'deploy/native'))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy/native' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def network():
    return {'source_ip': '172.24.81.83', 'bind_ip': '172.24.80.1', 'prefix_length': 20,
        'windows_interface_index': 46, 'windows_interface_alias': 'vEthernet (WSL (Hyper-V firewall))',
        'distro': 'ai-agent-company', 'root': '/opt/ai-agent-company', 'runtime_id': 'a' * 32,
        'boot_id': 'b' * 64, 'keeper_id': 'c' * 32}


def config(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module.windows, 'DEFAULT_ROOT', tmp_path)
    return {'kind': module.KIND, 'port': module.PORT, 'native_root': str(tmp_path),
        'native_runtime_id': 'd' * 32, 'network': network(), 'upstreams': {'openwebui': {}, 'platform': {}}}


def call(module, value, handler, method, path, *, source=None, headers=None, content=None, check=None,
        credentials=None, host_check=None):
    async def run():
        app = module.create_app(value, transport=httpx.MockTransport(handler), ownership_check=check or (lambda _: None),
            credential_store=credentials, host_interface_check=host_check)
        try:
            transport = httpx.ASGITransport(app=app, client=(source or value['network']['source_ip'], 12345))
            async with httpx.AsyncClient(transport=transport, base_url='http://bridge.invalid') as client:
                return await client.request(method, path, headers=headers or {'Authorization': 'Bearer test-only'}, content=content)
        finally:
            await app.state.upstream_client.aclose()
    return asyncio.run(run())


@pytest.mark.parametrize('fault', ['none', 'foreign_gateway', 'physical_nic', 'ambiguous', 'down'])
def test_nat_requires_distro_eth0_default_route_and_matching_windows_virtual_nic(fault):
    module = load_module('wsl')
    addresses = [{'ifname': 'eth0', 'flags': ['UP'], 'addr_info': [
        {'family': 'inet', 'scope': 'global', 'local': '172.24.81.83', 'prefixlen': 20}]}]
    routes = [{'dst': 'default', 'dev': 'eth0', 'gateway': '172.24.80.1'}]
    interfaces = [{'IPAddress': '172.24.80.1', 'InterfaceAlias': 'vEthernet (WSL)',
        'InterfaceIndex': 46, 'PrefixLength': 20, 'AddressState': 4}]
    if fault == 'foreign_gateway': routes[0]['gateway'] = '192.168.1.1'
    if fault == 'physical_nic': interfaces[0]['InterfaceAlias'] = 'Ethernet'
    if fault == 'ambiguous': interfaces.append(dict(interfaces[0]))
    if fault == 'down': addresses[0]['flags'] = []
    if fault == 'none':
        assert module.parse_nat_network(addresses, routes, interfaces)['bind_ip'] == '172.24.80.1'
    else:
        with pytest.raises(RuntimeError, match='wsl_nat_'):
            module.parse_nat_network(addresses, routes, interfaces)


def test_two_model_routes_and_all_fifteen_read_tools_are_the_only_forwarding_contract():
    module = load_module('wsl_http_bridge')
    assert module.route_target('GET', '/owui/api/v1/models')[:2] == ('openwebui', '/api/v1/models')
    assert module.route_target('POST', '/owui/api/v1/chat/completions')[:2] == ('openwebui', '/api/v1/chat/completions')
    assert len(module.READ_TOOLS) == 15
    for template in module.READ_TOOLS:
        path = template.replace('{id}', '123e4567-e89b-12d3-a456-426614174000')
        assert module.route_target('GET', '/platform' + path)[:2] == ('platform', path)
        with pytest.raises(module.HTTPException):
            module.route_target('POST', '/platform' + path)
    for method, path in [('GET', '/owui/api/v1/chats'), ('POST', '/platform/api/v1/queries'),
        ('GET', '/platform/api/v1/connections'), ('GET', '/owui/api/v1/models/other')]:
        with pytest.raises(module.HTTPException):
            module.route_target(method, path)


@pytest.mark.parametrize('path', ['/owui/api/v1/models', '/platform/api/v1/devices'])
def test_forwarding_preserves_bearer_and_native_permission_denial_without_cookies(tmp_path, monkeypatch, path):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.host == '127.0.0.1'
        assert request.headers['authorization'] == 'Bearer test-only'
        assert 'cookie' not in request.headers
        return httpx.Response(403, json={'detail': 'native-permission-revoked'}, headers={'Set-Cookie': 'never-forward'})
    response = call(module, value, handler, 'GET', path,
        headers={'Authorization': 'Bearer test-only', 'Cookie': 'token=never-forward'})
    assert response.status_code == 403 and response.json()['detail'] == 'native-permission-revoked'
    assert 'set-cookie' not in response.headers and len(requests) == 1


@pytest.mark.parametrize('fault', ['source', 'missing_bearer', 'unknown_path', 'encoded_path', 'revoked_owner'])
def test_rejected_requests_never_reach_any_upstream(tmp_path, monkeypatch, fault):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    source = '172.24.81.84' if fault == 'source' else None
    headers = {'Accept': 'application/json'} if fault == 'missing_bearer' else None
    path = '/owui/api/v1/models'
    if fault == 'unknown_path': path = '/platform/api/v1/secret-references'
    if fault == 'encoded_path': path = '/platform/api/v1/devices/%2fsecret'
    def check(_):
        if fault == 'revoked_owner': raise RuntimeError('PID changed')
    response = call(module, value, lambda _: pytest.fail('拒绝请求不可转发'), 'GET', path,
        source=source, headers=headers, check=check)
    assert response.status_code in (401, 403, 404, 503)


def test_openapi_rewrites_only_servers_and_keeps_permission_filtered_operations(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    document = {'openapi': '3.1.0', 'servers': [{'url': '/'}],
        'paths': {'/api/v1/devices': {'get': {'operationId': 'list_devices'}}}, 'components': {'schemas': {}}}
    response = call(module, value, lambda _: httpx.Response(200, json=document), 'GET', '/platform' + module.SCHEMA_PATH)
    actual = response.json()
    assert actual['servers'] == [{'url': 'http://172.24.80.1:13002/platform'}]
    assert actual['paths'] == document['paths'] and actual['components'] == document['components']
    assert response.headers['cache-control'] == 'no-store'


def test_bridge_rejects_schema_outside_the_existing_read_contract(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    response = call(module, value, lambda _: httpx.Response(200, json={'paths': {
        '/api/v1/devices': {'get': {}, 'delete': {}}}}), 'GET', '/platform' + module.SCHEMA_PATH)
    assert response.status_code == 502 and 'outside_read_contract' in response.json()['detail']


def test_upstream_timeout_is_not_retried_and_returns_no_native_exception_text(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout('sensitive-native-diagnostic', request=request)
    response = call(module, value, handler, 'GET', '/owui/api/v1/models')
    assert response.status_code == 502 and len(requests) == 1
    assert 'sensitive-native' not in response.text


def test_oversized_native_response_is_closed_before_error_return(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'MAX_RESPONSE', 4)
    response = call(module, value, lambda _: httpx.Response(200, content=b'oversized'), 'GET', '/owui/api/v1/models')
    assert response.status_code == 502 and 'response_too_large' in response.json()['detail']


@pytest.mark.parametrize('fault', ['none', 'saved_chat', 'direct_model', 'other_model', 'oversize'])
def test_completion_only_accepts_bounded_standard_model_protocol(tmp_path, monkeypatch, fault):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    payload = {'model': module.CHAT_MODEL, 'messages': [{'role': 'user', 'content': 'test'}], 'stream': False, 'max_tokens': 16}
    if fault == 'saved_chat': payload['chat_id'] = 'do-not-create'
    if fault == 'direct_model': payload['model_item'] = {'direct': True}
    if fault == 'other_model': payload['model'] = 'unconfigured-model'
    if fault == 'oversize': payload['messages'][0]['content'] = 'x' * module.MAX_REQUEST
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == '/api/v1/chat/completions' and request.url.port == 13000
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={'choices': [{'message': {'content': 'test-result'}}]})
    response = call(module, value, handler, 'POST', '/owui/api/v1/chat/completions',
        headers={'Authorization': 'Bearer test-only', 'Content-Type': 'application/json'}, content=json.dumps(payload))
    assert response.status_code == (200 if fault == 'none' else 413 if fault == 'oversize' else 422)
    assert len(calls) == (1 if fault == 'none' else 0)


def test_model_stream_cancellation_closes_original_upstream_without_retries(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"choices":[]}\n\n'
            await asyncio.sleep(3600)
        async def aclose(self): self.closed = True
    stream, calls = Stream(), []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=stream)
    async def run():
        app = module.create_app(value, transport=httpx.MockTransport(handler), ownership_check=lambda _: None)
        payload = json.dumps({'model': module.CHAT_MODEL, 'messages': [{'role': 'user', 'content': 'test'}], 'stream': True}).encode()
        async def receive(): return {'type': 'http.request', 'body': payload, 'more_body': False}
        scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
            'path': '/owui/api/v1/chat/completions', 'raw_path': b'/owui/api/v1/chat/completions', 'query_string': b'',
            'client': (network()['source_ip'], 1234), 'server': ('bridge', 13002),
            'headers': [(b'authorization', b'Bearer test-only'), (b'content-type', b'application/json')]}
        endpoint = app.routes[0].endpoint
        response = await endpoint(Request(scope, receive), path='owui/api/v1/chat/completions')
        assert await anext(response.body_iterator) == b'data: {"choices":[]}\n\n'
        await response.body_iterator.aclose()
        await app.state.upstream_client.aclose()
    asyncio.run(run())
    assert stream.closed and len(calls) == 1


@pytest.mark.parametrize('wrong_listener', [False, True])
def test_bridge_start_tracks_only_its_pid_and_cleans_failed_listener(tmp_path, monkeypatch, wrong_listener):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    state = {'id': 'd' * 32, 'processes': {}}
    process = SimpleNamespace(net_connections=lambda **_: [SimpleNamespace(status='LISTEN',
        laddr=SimpleNamespace(ip='0.0.0.0' if wrong_listener else network()['bind_ip'], port=13002))])
    process.pid, process.create_time = 123, lambda: 1.
    process.children, process.cwd = lambda **_: [], lambda: str(module.PROJECT)
    process.cmdline = lambda: state['processes'][module.COMPONENT]['command']
    process.exe = lambda: process.cmdline()[0]
    monkeypatch.setattr(module.wsl, 'verified_nat_network', network)
    monkeypatch.setattr(module, 'capture_upstreams', lambda _: value['upstreams'])
    monkeypatch.setattr(module.windows, 'load', lambda _: state)
    monkeypatch.setattr(module.windows, 'update_runtime', lambda _, change: change(state) or state)
    monkeypatch.setattr(module.windows, 'ensure_inside', lambda _, path: path)
    monkeypatch.setattr(module.windows_services, 'exclusive_operation', lambda *a: nullcontext())
    monkeypatch.setattr(module, 'owned_bridge', lambda *_: process if module.COMPONENT in state['processes'] else None)
    stops = []
    def spawn(root, name, command, **kwargs):
        record = {'launch_id': 'e' * 24, 'pid': 123, 'create_time': 1., 'executable': 'owned-python', 'port': None,
            **kwargs['metadata']}
        state['processes'][name] = record
        return record
    def stop(_, name):
        stops.append(name)
        state['processes'].pop(name)
        return {'stopped': True}
    monkeypatch.setattr(module.windows, 'spawn', spawn)
    monkeypatch.setattr(module.windows, 'stop', stop)
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def setsockopt(self, *a): pass
        def bind(self, address): assert address == ('172.24.80.1', 13002)
    monkeypatch.setattr(module.socket, 'socket', Socket)
    if wrong_listener:
        with pytest.raises(RuntimeError, match='listener_changed'):
            module.start_bridge(tmp_path)
        assert stops == [module.COMPONENT] and state['bridge_failures'][-1]['cleanup']['stopped']
    else:
        assert module.start_bridge(tmp_path)['ready']
        monkeypatch.setattr(module.wsl, 'verified_nat_network', lambda: {**network(), 'source_ip': '172.24.81.84'})
        with pytest.raises(RuntimeError, match='existing_network_or_upstreams_require_stop'):
            module.start_bridge(tmp_path)
        assert stops == []
    documents = list((tmp_path / module.COMPONENT).glob('*.json'))
    assert len(documents) == 1 and 'Bearer' not in documents[0].read_text()


def test_stop_old_owned_bridge_does_not_require_current_nat_or_touch_other_components(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    monkeypatch.setattr(module.windows_services, 'exclusive_operation', lambda *a: nullcontext())
    monkeypatch.setattr(module.windows, 'load', lambda _: {})
    monkeypatch.setattr(module, 'owned_bridge', lambda *_: object())
    monkeypatch.setattr(module.wsl, 'verified_nat_network', lambda: pytest.fail('停止旧进程不能重新探测网络'))
    names = []
    monkeypatch.setattr(module.windows, 'stop', lambda _, name: names.append(name) or {'stopped': True})
    assert module.stop_bridge(tmp_path)['stopped'] and names == [module.COMPONENT]


@pytest.mark.parametrize('cwd_kind', ['backend', 'temporary', 'foreign'])
def test_openwebui_signature_pins_observed_cwd_without_weakening_binary_or_data_binding(tmp_path, monkeypatch, cwd_kind):
    module = load_module('wsl_http_bridge')
    paths = {'backend': tmp_path / 'sources/openwebui/backend', 'python': tmp_path / 'venvs/openwebui/Scripts/python.exe',
        'sqlite': tmp_path / 'openwebui/data/webui.db', 'data': tmp_path / 'openwebui/data'}
    temporary = tmp_path / 'actual-windows-temp'
    cwd = paths['backend'] if cwd_kind == 'backend' else temporary if cwd_kind == 'temporary' else tmp_path / 'foreign'
    command = [str(paths['python']), '-m', 'uvicorn', 'open_webui.main:app', '--host', '127.0.0.1',
        '--port', '13000', '--workers', '1']
    listeners = [SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(ip='127.0.0.1', port=13000))]
    process = SimpleNamespace(pid=123, create_time=lambda: 1., children=lambda **_: [],
        cwd=lambda: str(cwd), exe=lambda: str(paths['python']), cmdline=lambda: command,
        net_connections=lambda **_: listeners)
    data = {'processes': {'openwebui': {'ready': True, 'pid': 123, 'create_time': 1., 'executable': str(paths['python']),
        'launch_id': 'owned', 'sqlite_file': str(paths['sqlite']), 'data_directory': str(paths['data'])}}}
    monkeypatch.setattr(module.windows, 'owned_process', lambda *a: process)
    monkeypatch.setattr(module.windows_services, 'openwebui_paths', lambda *a, **k: paths)
    monkeypatch.setattr(module.tempfile, 'gettempdir', lambda: str(temporary))
    if cwd_kind == 'foreign':
        with pytest.raises(RuntimeError, match='process_identity_changed'):
            module.signature(tmp_path, 'openwebui', data)
        command.pop()
        data['processes']['openwebui']['sqlite_file'] = str(tmp_path / 'foreign.db')
        with pytest.raises(RuntimeError, match='database_binding_changed'):
            module.signature(tmp_path, 'openwebui', data)
    else:
        monkeypatch.setattr(module, 'listener_signature', lambda *a: {'verified': True})
        assert module.signature(tmp_path, 'openwebui', data)['cwd'] == str(cwd.resolve())
        command.append('--reload')
        with pytest.raises(RuntimeError, match='process_identity_changed'):
            module.signature(tmp_path, 'openwebui', data)


@pytest.mark.parametrize('fault', ['none', 'foreign_interpreter', 'foreign_parent', 'foreign_port_bind', 'ambiguous'])
def test_venv_launcher_only_accepts_pinned_direct_python_runner(tmp_path, fault):
    module = load_module('wsl_http_bridge')
    python = tmp_path / 'venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    base = tmp_path / 'base/python.exe'
    (python.parent.parent / 'pyvenv.cfg').write_text('home = ' + str(base.parent), encoding='utf-8')
    command = [str(python), '-m', 'uvicorn', 'native:app', '--port', '13000']
    connection = SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(ip='127.0.0.1', port=13000))
    child = SimpleNamespace(pid=2, ppid=lambda: 99 if fault == 'foreign_parent' else 1, create_time=lambda: 2.,
        exe=lambda: str(tmp_path / 'foreign.exe' if fault == 'foreign_interpreter' else base),
        cmdline=lambda: [str(base), *command[1:]], cwd=lambda: str(tmp_path), net_connections=lambda **_: [connection])
    parent = SimpleNamespace(pid=1, children=lambda **_: [child, child] if fault == 'ambiguous' else [child],
        net_connections=lambda **_: [])
    if fault == 'foreign_port_bind': connection.laddr.ip = '0.0.0.0'
    if fault == 'none':
        assert module.listener_signature(parent, python, command, tmp_path, 13000)['pid'] == 2
    else:
        with pytest.raises(RuntimeError, match='bridge_upstream_'):
            module.listener_signature(parent, python, command, tmp_path, 13000)


@pytest.mark.parametrize('fault', ['none', 'down', 'index', 'ip', 'mask'])
def test_local_source_exception_requires_current_exact_windows_virtual_interface(monkeypatch, fault):
    module = load_module('wsl_http_bridge')
    value = network()
    alias = value['windows_interface_alias']
    monkeypatch.setattr(module.psutil, 'net_if_stats', lambda: {alias: SimpleNamespace(isup=fault != 'down')})
    monkeypatch.setattr(module, 'windows_interface_indices', lambda _: [47 if fault == 'index' else 46])
    monkeypatch.setattr(module.psutil, 'net_if_addrs', lambda: {alias: [SimpleNamespace(family=module.socket.AF_INET,
        address='192.168.1.2' if fault == 'ip' else value['bind_ip'], netmask='255.255.255.0' if fault == 'mask' else '255.255.240.0')]})
    if fault == 'none':
        module.require_host_interface(value)
    else:
        with pytest.raises(RuntimeError, match='virtual_interface_changed'):
            module.require_host_interface(value)


@pytest.mark.parametrize('fault', ['none', 'model_route', 'raw_bearer', 'nic_changed', 'unknown_source'])
def test_windows_source_is_only_accepted_for_managed_platform_key_and_pinned_nic(tmp_path, monkeypatch, fault):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    calls, checks, authorizations = [], [], []
    class Credentials:
        async def authorization(self, raw, source, network, client):
            authorizations.append((raw, source))
            assert raw.startswith('aic_tool_') and source == network['bind_ip']
            return 'Bearer service_scoped-only'
        def reject_token(self, raw): pytest.fail('正常业务返回不应废弃token')
    def check(network):
        checks.append(network['bind_ip'])
        if fault == 'nic_changed': raise RuntimeError('NIC changed')
    def handler(request):
        calls.append(request)
        assert request.url.port == 8080 and request.headers['authorization'] == 'Bearer service_scoped-only'
        return httpx.Response(200, json={'items': []})
    path = '/owui/api/v1/models' if fault == 'model_route' else '/platform/api/v1/devices'
    source = '172.24.80.2' if fault == 'unknown_source' else network()['bind_ip']
    raw = 'service_legacy' if fault == 'raw_bearer' else 'aic_tool_managed'
    response = call(module, value, handler, 'GET', path, source=source,
        headers={'Authorization': 'Bearer ' + raw}, host_check=check,
        credentials=None if fault == 'raw_bearer' else Credentials())
    assert response.status_code == (200 if fault == 'none' else 503 if fault == 'nic_changed' else 403)
    assert len(calls) == (1 if fault == 'none' else 0)
    assert len(authorizations) == (1 if fault == 'none' else 0)


def test_platform_401_latches_managed_key_without_replaying_business_request(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    value = config(module, tmp_path, monkeypatch)
    rejected, calls = [], []
    class Credentials:
        async def authorization(self, *a): return 'Bearer service_short'
        def reject_token(self, raw): rejected.append(raw)
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={'detail': 'revoked'})
    response = call(module, value, handler, 'GET', '/platform/api/v1/devices',
        headers={'Authorization': 'Bearer aic_tool_managed'}, credentials=Credentials())
    assert response.status_code == 401 and rejected == ['aic_tool_managed'] and len(calls) == 1
