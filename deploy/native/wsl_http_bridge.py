"""专属WSL到本机原生服务的窄HTTP入口；保留上游身份与权限，不代理任意地址。"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from uuid import uuid4

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

import windows
import windows_services
import wsl

PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT / 'services/platform') not in sys.path:
    sys.path.insert(0, str(PROJECT / 'services/platform'))
from agent_platform.core.provider_settings import CHAT_MODEL
from agent_platform.modules.integrations.business_tools import READ_TOOLS
from wsl_tool_credentials import CredentialError, PREFIX as TOOL_KEY_PREFIX, ToolCredentialStore

KIND = 'ai-agent-company-wsl-http-bridge-v1'
COMPONENT = 'wsl-http-bridge'
PORT = 13002
MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
TOTAL_SECONDS = 180
SCHEMA_PATH = '/api/v1/business-tools/openapi.json'
STANDARD_FIELDS = frozenset({'model', 'messages', 'stream', 'temperature', 'top_p', 'max_tokens',
    'max_completion_tokens', 'frequency_penalty', 'presence_penalty', 'stop', 'user', 'seed', 'n',
    'logprobs', 'top_logprobs', 'response_format', 'tools', 'tool_choice', 'parallel_tool_calls',
    'stream_options', 'reasoning_effort'})


def listener_signature(process, python: Path, command: list[str], cwd: Path, port: int,
        bind_ip: str = '127.0.0.1', *, required: bool = True) -> dict | None:
    """Windows venv可能是launcher；只接受其直系、同venv解释器的唯一真实监听者。"""
    listeners = []
    for candidate in [process, *process.children(recursive=True)]:
        for connection in candidate.net_connections(kind='tcp'):
            if connection.status == 'LISTEN' and connection.laddr.port == port:
                listeners.append((candidate, connection))
    if not listeners and not required:
        return None
    if len(listeners) != 1 or listeners[0][1].laddr.ip != bind_ip:
        raise RuntimeError('bridge_upstream_listener_changed')
    candidate = listeners[0][0]
    expected_python = python.resolve()
    allowed_first = {expected_python}
    if candidate.pid != process.pid:
        cfg = python.parent.parent / 'pyvenv.cfg'
        values = dict(line.split('=', 1) for line in cfg.read_text('utf-8').splitlines() if '=' in line)
        values = {key.strip(): value.strip() for key, value in values.items()}
        base = Path(values.get('executable') or str(Path(values['home']) / 'python.exe')).resolve()
        if candidate.ppid() != process.pid:
            raise RuntimeError('bridge_upstream_runner_parent_changed')
        expected_python = base
        allowed_first.add(base)
    observed = candidate.cmdline()
    if (Path(candidate.exe()).resolve() != expected_python or not observed
            or Path(observed[0]).resolve() not in allowed_first or observed[1:] != command[1:]
            or Path(candidate.cwd()).resolve() != cwd.resolve()):
        raise RuntimeError('bridge_upstream_runner_identity_changed')
    return {'pid': candidate.pid, 'create_time': candidate.create_time(), 'exe': str(expected_python),
        'cwd': str(cwd.resolve()), 'command': observed}


def signature(root: Path, component: str, data: dict) -> dict:
    """只读核真实上游PID、出生时间、解释器、命令、cwd和本项目SQLite绑定。"""
    process = windows.owned_process(root, component, data)
    info = data.get('processes', {}).get(component, {})
    if not process or not info.get('ready'):
        raise RuntimeError('bridge_upstream_not_ready:' + component)
    if component == 'openwebui':
        paths = windows_services.openwebui_paths(windows, root, data, api_only=info.get('api_only', False))
        expected_cwd, expected_python = paths['backend'], paths['python']
        if info.get('sqlite_file') != str(paths['sqlite']) or info.get('data_directory') != str(paths['data']):
            raise RuntimeError('bridge_openwebui_database_binding_changed')
        target, port = 'open_webui.main:app', 13000
        expected_command = [str(expected_python), '-m', 'uvicorn', target,
            '--host', '127.0.0.1', '--port', str(port), '--workers', '1']
    elif component == 'platform':
        expected_cwd, expected_python = PROJECT, PROJECT / '.venv/Scripts/python.exe'
        folder = PROJECT / '.local/platform'
        expected = {'data_directory': str(folder), 'sqlite_file': str(folder / 'platform.sqlite3')}
        if data.get('databases', {}).get('platform') != expected or any(info.get(k) != v for k, v in expected.items()):
            raise RuntimeError('bridge_platform_database_binding_changed')
        target, port = 'agent_platform.main:create_app', 8080
        expected_command = [str(expected_python), '-m', 'uvicorn', target, '--factory',
            '--host', '127.0.0.1', '--port', str(port)]
    else:
        raise RuntimeError('bridge_upstream_not_allowed')
    command = process.cmdline()
    observed_cwd = Path(process.cwd()).resolve()
    allowed_cwds = {expected_cwd.resolve()}
    if component == 'openwebui':
        # 当前原生依赖已把进程cwd切到Windows实际TEMP；仍钉住所见值，后续变化立即拒绝。
        # 源码、解释器、完整argv及SQLite归属继续单独核验，不从临时cwd推断任何配置。
        allowed_cwds.add(Path(tempfile.gettempdir()).resolve())
    if (observed_cwd not in allowed_cwds
            or Path(process.exe()).resolve() != expected_python.resolve()
            or command != expected_command):
        raise RuntimeError('bridge_upstream_process_identity_changed:' + component)
    listener = listener_signature(process, expected_python, expected_command, expected_cwd, port)
    return {key: info[key] for key in ('pid', 'create_time', 'executable', 'launch_id')} | {
        'cwd': str(observed_cwd), 'command': command, 'listener': listener}


def capture_upstreams(root: Path) -> dict:
    data = windows.load(root)
    return {name: signature(root, name, data) for name in ('openwebui', 'platform')}


def check_upstream(config: dict, component: str) -> None:
    """每请求重新核上游与keeper，重启或撤销后的旧归属不得继续转发。"""
    import wsl_lifetime
    root = Path(config['native_root'])
    data = windows.load(root)
    if data['id'] != config['native_runtime_id'] or signature(root, component, data) != config['upstreams'][component]:
        raise RuntimeError('bridge_upstream_reservation_changed')
    keeper = data.get('processes', {}).get(wsl_lifetime.COMPONENT, {})
    if (not windows.owned_process(root, wsl_lifetime.COMPONENT, data)
            or keeper.get('runtime_id') != config['network']['runtime_id']
            or keeper.get('keeper_id') != config['network']['keeper_id']):
        raise RuntimeError('bridge_wsl_lifetime_changed')


def validate_config(config: dict) -> dict:
    if config.get('kind') != KIND or config.get('port') != PORT or Path(config.get('native_root', '')).resolve() != windows.DEFAULT_ROOT.resolve():
        raise RuntimeError('bridge_configuration_identity_changed')
    network = config['network']
    source = ipaddress.IPv4Address(network['source_ip'])
    host = ipaddress.IPv4Address(network['bind_ip'])
    subnet = ipaddress.IPv4Network(f"{source}/{network['prefix_length']}", strict=False)
    if (source == host or host not in subnet or network.get('distro') != wsl.DISTRO
            or network.get('root') != wsl.LINUX_ROOT or not network.get('keeper_id')
            or any(not address.is_private or address.is_loopback or address.is_link_local
                or address.is_unspecified or address.is_multicast for address in (source, host))):
        raise RuntimeError('bridge_network_configuration_invalid')
    if set(config.get('upstreams', {})) != {'openwebui', 'platform'}:
        raise RuntimeError('bridge_upstreams_configuration_invalid')
    return config


def load_config() -> dict:
    path = Path(os.environ.get('AIC_WSL_BRIDGE_CONFIG', ''))
    folder = windows.DEFAULT_ROOT / 'wsl-http-bridge'
    if not path.is_absolute() or path.resolve() != path.absolute() or path.parent != folder or not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
        raise RuntimeError('bridge_configuration_path_invalid')
    if path.stat().st_size > 64 * 1024:
        raise RuntimeError('bridge_configuration_size_invalid')
    config = validate_config(json.loads(path.read_text('utf-8')))
    if wsl.verified_nat_network() != config['network']:
        raise RuntimeError('bridge_nat_changed_before_start')
    for name in config['upstreams']:
        check_upstream(config, name)
    return config


def route_target(method: str, path: str) -> tuple[str, str, bool]:
    """只接受两个标准模型接口及现有15项只读工具；工具清单直接复用业务模块。"""
    if (method, path) == ('GET', '/owui/api/v1/models'):
        return 'openwebui', '/api/v1/models', False
    if (method, path) == ('POST', '/owui/api/v1/chat/completions'):
        return 'openwebui', '/api/v1/chat/completions', False
    if method == 'GET' and path.startswith('/platform/'):
        native = path.removeprefix('/platform')
        if native == SCHEMA_PATH:
            return 'platform', native, True
        for template in READ_TOOLS:
            pattern = re.escape(template).replace(r'\{id\}', '[A-Za-z0-9_-]{1,80}')
            if re.fullmatch(pattern, native):
                return 'platform', native, False
    raise HTTPException(404, 'bridge_route_not_allowed')


async def request_bytes(request: Request) -> bytes:
    length = request.headers.get('content-length')
    if length and (not length.isdigit() or int(length) > MAX_REQUEST):
        raise HTTPException(413, 'bridge_request_too_large')
    content = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > MAX_REQUEST:
                    raise HTTPException(413, 'bridge_request_too_large')
    except TimeoutError:
        raise HTTPException(408, 'bridge_request_body_deadline') from None
    return bytes(content)


def windows_interface_indices(alias: str) -> list[int]:
    """GetAdaptersAddresses同时提供FriendlyName/IfIndex，避免把友好名当socket内部设备名。"""
    class Adapter(ctypes.Structure):
        pass
    Adapter._fields_ = [('Length', ctypes.c_ulong), ('IfIndex', ctypes.c_ulong),
        ('Next', ctypes.POINTER(Adapter)), ('AdapterName', ctypes.c_char_p),
        ('FirstUnicastAddress', ctypes.c_void_p), ('FirstAnycastAddress', ctypes.c_void_p),
        ('FirstMulticastAddress', ctypes.c_void_p), ('FirstDnsServerAddress', ctypes.c_void_p),
        ('DnsSuffix', ctypes.c_wchar_p), ('Description', ctypes.c_wchar_p), ('FriendlyName', ctypes.c_wchar_p)]
    query = ctypes.windll.iphlpapi.GetAdaptersAddresses
    query.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    query.restype = ctypes.c_ulong
    size = ctypes.c_ulong(32768)
    buffer = ctypes.create_string_buffer(size.value)
    result = query(socket.AF_INET, 14, None, buffer, ctypes.byref(size))
    if result == 111 and 0 < size.value <= 1024 * 1024:
        # Windows规定的缓冲区容量协商，不执行网络连接或失败重试。
        buffer = ctypes.create_string_buffer(size.value)
        result = query(socket.AF_INET, 14, None, buffer, ctypes.byref(size))
    if result != 0:
        raise RuntimeError('bridge_host_interface_metadata_unavailable')
    current = ctypes.cast(buffer, ctypes.POINTER(Adapter))
    indices, visited = [], set()
    while current:
        address = ctypes.addressof(current.contents)
        if address in visited or len(visited) >= 256:
            raise RuntimeError('bridge_host_interface_metadata_invalid')
        visited.add(address)
        if current.contents.FriendlyName == alias:
            indices.append(current.contents.IfIndex)
        current = current.contents.Next
    return indices


def require_host_interface(network: dict) -> None:
    """本机源例外必须仍属于已核的Windows WSL虚拟NIC，不信请求自报或代理头。"""
    alias = network['windows_interface_alias']
    statistics = psutil.net_if_stats().get(alias)
    addresses = psutil.net_if_addrs().get(alias, [])
    matches = [item for item in addresses if item.family == socket.AF_INET and item.address == network['bind_ip']
        and ipaddress.IPv4Network(f"{item.address}/{item.netmask}", strict=False).prefixlen == network['prefix_length']]
    if (not statistics or not statistics.isup or len(matches) != 1
            or 'WSL' not in alias or windows_interface_indices(alias) != [network['windows_interface_index']]):
        raise RuntimeError('bridge_host_virtual_interface_changed')


def create_app(config: dict | None = None, *, transport=None, ownership_check=None,
        credential_store=None, host_interface_check=None) -> FastAPI:
    """只装配HTTP转发；配置无密钥，Bearer仅在本次请求内转交原生鉴权。"""
    config = validate_config(config) if config is not None else load_config()
    checker = ownership_check or (lambda component: check_upstream(config, component))
    credentials = credential_store or ToolCredentialStore(Path(config['native_root']))
    host_checker = host_interface_check or require_host_interface
    client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(connect=3, read=30, write=10, pool=3),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2))

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.upstream_client = client

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE', 'CONNECT'])
    async def forward(request: Request, path: str):
        raw = request.scope.get('raw_path', b'')
        if b'%' in raw or b'//' in raw or '..' in request.url.path.split('/'):
            raise HTTPException(404, 'bridge_route_not_allowed')
        component, native, schema = route_target(request.method, request.url.path)
        source = request.client.host if request.client else None
        network = config['network']
        if source != network['source_ip']:
            if component != 'platform' or source != network['bind_ip']:
                raise HTTPException(403, 'bridge_source_not_allowed')
            try:
                host_checker(network)
            except Exception:
                raise HTTPException(503, 'bridge_host_virtual_interface_changed') from None
        authorization = request.headers.get('authorization', '')
        if not authorization.startswith('Bearer ') or not authorization[7:].strip() or len(authorization) > 16384 or any(c in authorization for c in '\r\n'):
            raise HTTPException(401, 'bridge_bearer_required')
        if len(request.url.query.encode()) > 4096 or (component == 'openwebui' and request.url.query):
            raise HTTPException(422, 'bridge_query_not_allowed')
        body = await request_bytes(request)
        if request.method == 'GET' and body:
            raise HTTPException(422, 'bridge_get_body_not_allowed')
        streaming = False
        if request.method == 'POST':
            if request.headers.get('content-type', '').split(';', 1)[0] != 'application/json':
                raise HTTPException(415, 'bridge_json_required')
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(422, 'bridge_json_invalid') from None
            if (not isinstance(payload, dict) or set(payload) - STANDARD_FIELDS or payload.get('model') != CHAT_MODEL
                    or not isinstance(payload.get('messages'), list) or not payload['messages']
                    or type(payload.get('stream', False)) is not bool):
                raise HTTPException(422, 'bridge_completion_invalid')
            streaming = payload.get('stream', False)
        try:
            checker(component)
        except Exception:
            raise HTTPException(503, 'bridge_upstream_ownership_changed') from None
        native_key = authorization[7:]
        if component == 'platform':
            try:
                authorization = await credentials.authorization(native_key, source, network, client)
            except CredentialError as error:
                raise HTTPException(error.status, error.code) from None
            except Exception:
                raise HTTPException(503, 'bridge_tool_credential_unavailable') from None
        port = 13000 if component == 'openwebui' else 8080
        url = httpx.URL(f'http://127.0.0.1:{port}{native}', query=request.url.query.encode())
        headers = {'Authorization': authorization, 'Accept': request.headers.get('accept', '*/*'),
            'Content-Type': 'application/json', 'User-Agent': 'manufacturing-agent-platform/0.1 WSLBridge'}
        upstream = None
        stream_returned = False
        deadline = time.monotonic() + TOTAL_SECONDS
        try:
            async with asyncio.timeout(TOTAL_SECONDS):
                upstream = await client.send(client.build_request(request.method, url, headers=headers, content=body), stream=True)
            if component == 'platform' and native_key.startswith(TOOL_KEY_PREFIX) and upstream.status_code == 401:
                credentials.reject_token(native_key)
            response_headers = {name: value for name, value in upstream.headers.items()
                if name.lower() in {'content-type', 'cache-control', 'retry-after'}}
            if streaming and upstream.is_success and upstream.headers.get('content-type', '').startswith('text/event-stream'):
                async def chunks():
                    size = 0
                    try:
                        async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                            async for chunk in upstream.aiter_bytes():
                                size += len(chunk)
                                if size > MAX_RESPONSE:
                                    raise RuntimeError('bridge_stream_budget_exceeded')
                                yield chunk
                    finally:
                        await asyncio.shield(upstream.aclose())
                response = StreamingResponse(chunks(), status_code=upstream.status_code, headers=response_headers,
                    background=BackgroundTask(upstream.aclose))
                stream_returned = True
                return response
            content = bytearray()
            async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                async for chunk in upstream.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE:
                        raise HTTPException(502, 'bridge_response_too_large')
            if schema and upstream.status_code == 200:
                document = json.loads(content)
                if not isinstance(document, dict) or not isinstance(document.get('paths'), dict):
                    raise HTTPException(502, 'bridge_tool_schema_invalid')
                if any(path not in READ_TOOLS or not isinstance(operations, dict) or set(operations) != {'get'}
                        for path, operations in document['paths'].items()):
                    raise HTTPException(502, 'bridge_tool_schema_outside_read_contract')
                # 只改此Schema的入口；路径/参数/权限投影由平台现有实现决定。
                document['servers'] = [{'url': f"http://{config['network']['bind_ip']}:{PORT}/platform"}]
                return JSONResponse(document, headers={'Cache-Control': 'no-store'})
            return Response(bytes(content), status_code=upstream.status_code, headers=response_headers)
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise HTTPException(502, 'bridge_upstream_failed') from None
        finally:
            # SSE的连接由迭代器最终释放；其他分支和取消/异常立即关闭，无重发。
            if upstream is not None and not stream_returned:
                await asyncio.shield(upstream.aclose())

    return app


def owned_bridge(root: Path, data: dict):
    """停止也只认当前进程的完整命令/cwd，不以旧IP或进程名称扩大回收范围。"""
    process = windows.owned_process(root, COMPONENT, data)
    if process:
        record = data['processes'][COMPONENT]
        if process.cmdline() != record.get('command') or Path(process.cwd()).resolve() != PROJECT:
            raise RuntimeError('bridge_process_identity_changed')
    return process


def start_bridge(root: Path | None = None) -> dict:
    """每次启动先重新核NAT；只登记一次启动，不自动接管旧来源或占用端口。"""
    root = root or windows.DEFAULT_ROOT
    with windows_services.exclusive_operation(root, 'openwebui-start'):
        network = wsl.verified_nat_network()
        upstreams = capture_upstreams(root)
        data = windows.load(root)
        process = owned_bridge(root, data)
        previous = data.get('processes', {}).get(COMPONENT, {})
        if process:
            if previous.get('network') != network or previous.get('upstreams') != upstreams or not previous.get('ready'):
                raise RuntimeError('bridge_existing_network_or_upstreams_require_stop')
            listener = listener_signature(process, Path(previous['command'][0]), previous['command'], PROJECT,
                PORT, network['bind_ip'])
            if listener != previous.get('listener'):
                raise RuntimeError('bridge_existing_listener_changed')
            return {'ready': True, 'reused': True, 'url': f"http://{network['bind_ip']}:{PORT}", 'source_ip': network['source_ip']}
        with socket.socket() as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((network['bind_ip'], PORT))
        folder = windows.ensure_inside(root, root / COMPONENT)
        folder.mkdir(exist_ok=True)
        config = {'kind': KIND, 'native_root': str(root), 'native_runtime_id': data['id'],
            'network': network, 'upstreams': upstreams, 'port': PORT}
        path = folder / (uuid4().hex + '.json')
        with path.open('x', encoding='utf-8') as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        command = [str(PROJECT / '.venv/Scripts/python.exe'), '-m', 'uvicorn', 'wsl_http_bridge:create_app',
            '--factory', '--host', network['bind_ip'], '--port', str(PORT), '--workers', '1',
            '--no-access-log', '--no-proxy-headers']
        launched = windows.spawn(root, COMPONENT, command, cwd=PROJECT, env=windows.base_environment({
            'PYTHONPATH': os.pathsep.join([str(PROJECT / 'deploy/native'), str(PROJECT / 'services/platform')]),
            'AIC_WSL_BRIDGE_CONFIG': str(path), 'PYTHON_DOTENV_DISABLED': '1'}), metadata={
                'command': command, 'network': network, 'upstreams': upstreams, 'config_file': str(path),
                'config_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bridge_port': PORT, 'ready': False})
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                current = windows.load(root)
                candidate = owned_bridge(root, current)
                if not candidate:
                    raise RuntimeError('bridge_process_exited')
                listener = listener_signature(candidate, Path(command[0]), command, PROJECT, PORT,
                    network['bind_ip'], required=False)
                if listener:
                    windows.update_runtime(root, lambda value: value['processes'][COMPONENT].update(ready=True, listener=listener))
                    return {'ready': True, 'url': f"http://{network['bind_ip']}:{PORT}", 'source_ip': network['source_ip'],
                        'business_verified': False}
                time.sleep(.2)
            raise TimeoutError('bridge_listening_deadline')
        except BaseException as error:
            cleanup = {'stopped': False, 'reason': 'launch_changed'}
            if windows.same_launch(windows.load(root).get('processes', {}).get(COMPONENT), launched):
                try:
                    owned_bridge(root, windows.load(root))
                    cleanup = windows.stop(root, COMPONENT)
                except Exception as failure:
                    cleanup = {'stopped': False, 'error_type': type(failure).__name__}
            windows.update_runtime(root, lambda value: value.setdefault('bridge_failures', []).append({
                'error_type': type(error).__name__, 'launch_id': launched['launch_id'], 'cleanup': cleanup}))
            raise


def stop_bridge(root: Path | None = None) -> dict:
    """无网络探测即可停止旧绑定；IP变化不阻止对本次所属进程的正常回收。"""
    root = root or windows.DEFAULT_ROOT
    with windows_services.exclusive_operation(root, 'openwebui-start'):
        if not owned_bridge(root, windows.load(root)):
            return {'component': COMPONENT, 'stopped': True, 'already_stopped': True}
        return windows.stop(root, COMPONENT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop'])
    args = parser.parse_args()
    print(json.dumps(start_bridge() if args.action == 'start' else stop_bridge(), ensure_ascii=False))
