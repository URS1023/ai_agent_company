"""受管工具签名补丁：只改临时副本、执行实际插入块，不导入OWUI或访问服务。"""

import ast
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / 'deploy/native'
if str(NATIVE) not in sys.path:
    sys.path.insert(0, str(NATIVE))

import owui_tool_forward_patch as patch
import windows_services
from test_wsl_tool_credentials import fixture as credential_fixture, binding


@pytest.fixture
def original():
    source = ROOT / '.local/native_windows/sources/openwebui' / patch.SOURCE_FILE
    if not source.is_file():
        pytest.skip('本机未安装锁定OWUI副本；其余插入块及配置纯检查仍独立运行')
    value = source.read_bytes()
    if patch.digest(value) == patch.PATCHED_SHA256:
        value = value.replace(patch.BLOCK, b'', 1)
    assert patch.digest(value) == patch.ORIGINAL_SHA256
    return value


@pytest.fixture
def managed(tmp_path, original, monkeypatch):
    root = (tmp_path / 'new-runtime').resolve()
    source = root / 'sources/openwebui'
    target = source / patch.SOURCE_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(original)
    (source / 'package.json').write_text(json.dumps({'version': '0.12.3'}), encoding='utf-8')
    network = {'bind_ip': '172.24.80.1', 'windows_interface_alias': 'vEthernet (WSL)',
               'windows_interface_index': 3, 'prefix_length': 20}
    manifest = {'kind': 'manufacturing-native-windows', 'root': str(root), 'id': 'new-owned-runtime',
        'sources': {'openwebui': str(source)}, 'processes': {'wsl-http-bridge': {'network': network}}}
    (root / 'runtime.json').write_text(json.dumps(manifest), encoding='utf-8')
    paths = {'data': root / 'openwebui/data', 'source': source, 'backend': source / 'backend',
             'sqlite': root / 'openwebui/data/webui.db', 'static': root / 'openwebui/static',
             'model_cache': root / 'openwebui/model-cache', 'frontend': source / 'build'}
    config = {'kind': patch.CONFIG_KIND, 'runtime_id': manifest['id'], 'enabled': True,
              'server_id': 'aic-business', 'bridge_url': 'http://172.24.80.1:13002/platform'}
    checks = []
    monkeypatch.setitem(sys.modules, 'wsl_http_bridge', SimpleNamespace(require_host_interface=lambda value: checks.append(value)))
    return SimpleNamespace(root=root, source=source, target=target, manifest=manifest, paths=paths,
        config=config, checks=checks, secret={'webui_secret': 'different-login-secret', patch.SECRET_KEY: 'private-unit-forward-' + 'x' * 40})


def enable(site):
    (site.root / patch.CONFIG_FILE).write_text(json.dumps(site.config), encoding='utf-8')


def test_exact_patch_compiles_is_idempotent_and_leaves_global_header_code_untouched(managed):
    before = managed.target.read_bytes()
    first = patch.apply_patch(managed.root)
    assert first['applied'] is True and first['before_sha256'] == patch.ORIGINAL_SHA256
    changed = managed.target.read_bytes()
    assert patch.digest(changed) == patch.PATCHED_SHA256
    assert changed.replace(patch.BLOCK, b'', 1) == before
    assert first['global_user_forwarding'] is False
    assert patch.apply_patch(managed.root)['applied'] is False
    compile(changed, 'only-temporary-tools.py', 'exec')
    assert not list(managed.target.parent.glob('.aic-tool-*.pending'))


def test_unknown_source_or_unmanaged_root_never_changes_file(managed):
    managed.target.write_bytes(managed.target.read_bytes() + b'\n# unknown source\n')
    before = managed.target.read_bytes()
    with pytest.raises(RuntimeError, match='source_unknown'):
        patch.apply_patch(managed.root)
    assert managed.target.read_bytes() == before
    managed.manifest['root'] = str(managed.root.parent)
    (managed.root / 'runtime.json').write_text(json.dumps(managed.manifest), encoding='utf-8')
    with pytest.raises(RuntimeError, match='source_invalid'):
        patch.apply_patch(managed.root)


def test_absent_or_disabled_configuration_preserves_environment_without_secret_reads(managed):
    assert patch.forwarding_environment(None, managed.paths, {}) == {}
    managed.config['enabled'] = False
    enable(managed)
    assert patch.forwarding_environment(None, managed.paths, {}) == {}
    runtime = SimpleNamespace(base_environment=lambda value: value, openwebui_provider_environment=lambda: {})
    env = windows_services.openwebui_environment(runtime, managed.paths, {'webui_secret': 'old-login-only'})
    assert not any(key.startswith('AIC_PLATFORM_TOOL_') for key in env)
    assert 'FORWARD_USER_INFO_HEADER_JWT_SECRET' not in env


def test_enabled_environment_requires_patched_source_and_only_exports_tool_pins(managed):
    enable(managed)
    with pytest.raises(RuntimeError, match='not_ready'):
        patch.forwarding_environment(None, managed.paths, managed.secret)
    patch.apply_patch(managed.root)
    runtime = SimpleNamespace(base_environment=lambda value: value, openwebui_provider_environment=lambda: {})
    env = windows_services.openwebui_environment(runtime, managed.paths, managed.secret)
    assert env['ENABLE_FORWARD_USER_INFO_HEADERS'] == 'False'
    assert env['AIC_PLATFORM_TOOL_SERVER_ID'] == 'aic-business'
    assert env['AIC_PLATFORM_TOOL_BRIDGE_URL'] == managed.config['bridge_url']
    assert env['FORWARD_USER_INFO_HEADER_JWT_SECRET'] == managed.secret[patch.SECRET_KEY]
    assert env['FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS'] == '300'
    assert 'CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS' not in env
    assert managed.checks and managed.secret[patch.SECRET_KEY] not in json.dumps(managed.config)


@pytest.mark.parametrize('fault', ['runtime', 'url', 'credentials', 'same_secret', 'unconfirmed_nic', 'extra'])
def test_invalid_configuration_or_unknown_network_fails_without_fallback(managed, monkeypatch, fault):
    patch.apply_patch(managed.root)
    if fault == 'runtime': managed.config['runtime_id'] = 'other-runtime'
    if fault == 'url': managed.config['bridge_url'] = 'http://192.168.1.10:13002/platform'
    if fault == 'credentials': managed.secret.pop(patch.SECRET_KEY)
    if fault == 'same_secret': managed.secret['webui_secret'] = managed.secret[patch.SECRET_KEY]
    if fault == 'extra': managed.config['key'] = 'must-not-be-stored-in-config'
    if fault == 'unconfirmed_nic':
        def reject(_network):
            raise RuntimeError('local-interface-unknown')
        monkeypatch.setitem(sys.modules, 'wsl_http_bridge', SimpleNamespace(require_host_interface=reject))
    enable(managed)
    with pytest.raises(RuntimeError):
        patch.forwarding_environment(None, managed.paths, managed.secret)


def executable_block(monkeypatch, *, pins=True, global_forward=False, secret='unit-key-' + 'x' * 40):
    """执行真实BLOCK，仅提供上游函数局部上下文与原签名函数替身。"""
    minted = []
    env = ModuleType('open_webui.env')
    env.FORWARD_USER_INFO_HEADER_JWT_SECRET = secret
    env.FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS = 300
    headers_module = ModuleType('open_webui.utils.headers')
    def mint(user):
        minted.append(user.id)
        return 'signed-by-original-mint:' + user.id
    headers_module._mint_forward_user_jwt = mint
    monkeypatch.setitem(sys.modules, 'open_webui.env', env)
    monkeypatch.setitem(sys.modules, 'open_webui.utils.headers', headers_module)
    source = ("async def run(connection, request, user, server_id='', metadata=None):\n"
              "    metadata = metadata or {}\n"
              "    auth_type = connection.get('auth_type', 'bearer')\n"
              "    headers = dict(connection.get('headers') or {})\n"
              "    cookies = {'private-cookie': 'not-to-forward'} if connection.get('forward_cookies', False) else {}\n"
              + patch.BLOCK.decode() + "    return headers, cookies\n")
    tree = ast.parse(source)
    values = {'os': SimpleNamespace(environ={'AIC_PLATFORM_TOOL_SERVER_ID': 'aic-business',
              'AIC_PLATFORM_TOOL_BRIDGE_URL': 'http://172.24.80.1:13002/platform'} if pins else {}),
              're': re, 'ENABLE_FORWARD_USER_INFO_HEADERS': global_forward}
    exec(compile(tree, 'actual-patch-block.py', 'exec'), values)
    return values['run'], minted


def connection():
    return {'url': 'http://172.24.80.1:13002/platform', 'type': 'openapi', 'auth_type': 'bearer',
        'key': 'aic_tool_unit-only', 'forward_cookies': False, 'config': {'platform_delegation': deepcopy(patch.MARKER)},
        'headers': {'X-OpenWebUI-User-Jwt': 'cannot-use-literal-user'}}


def invoke(function, connection_value=None, *, server_id='aic-business', metadata=None):
    return asyncio.run(function(connection_value or connection(), SimpleNamespace(), SimpleNamespace(id='ordinary-user'),
        server_id, {'chat_id': 'native-chat', 'message_id': 'assistant-message'} if metadata is None else metadata))


def test_marked_connection_calls_original_mint_with_actual_user_and_exact_message(monkeypatch):
    function, minted = executable_block(monkeypatch)
    headers, cookies = invoke(function)
    assert minted == ['ordinary-user'] and cookies == {}
    assert headers == {'X-OpenWebUI-User-Jwt': 'signed-by-original-mint:ordinary-user',
        'X-OpenWebUI-Chat-Id': 'native-chat', 'X-OpenWebUI-Message-Id': 'assistant-message'}


def test_actual_upstream_header_function_preserves_engine_bearer(original, monkeypatch):
    """只执行锁定原函数的AST，原工具加载器、数据库和网络模块均不导入。"""
    changed, _ = patch.prepare_patch(original)
    node = next(item for item in ast.parse(changed).body
                if isinstance(item, ast.AsyncFunctionDef) and item.name == 'build_tool_server_headers')
    harness, minted = executable_block(monkeypatch)
    values = harness.__globals__
    values['bearer_auth_header'] = lambda key: {'Authorization': 'Bearer ' + key}
    async def custom(headers, user, metadata):
        return headers
    values['get_custom_headers'] = custom
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'locked-upstream-header-function.py', 'exec'), values)
    headers, cookies = invoke(values['build_tool_server_headers'])
    assert headers['Authorization'] == 'Bearer aic_tool_unit-only'
    assert headers['X-OpenWebUI-User-Jwt'] == 'signed-by-original-mint:ordinary-user'
    assert minted == ['ordinary-user'] and cookies == {}


@pytest.mark.parametrize('fault', ['marker', 'missing_marker', 'bool_version', 'server', 'url', 'auth', 'cookie',
                                  'key', 'chat', 'message', 'pin', 'secret', 'global'])
def test_any_marked_connection_mismatch_cannot_fall_back_to_unsigned(monkeypatch, fault):
    function, minted = executable_block(monkeypatch, pins=fault != 'pin', global_forward=fault == 'global',
                                         secret=None if fault == 'secret' else 'unit-key-' + 'x' * 40)
    value, server_id = connection(), 'aic-business'
    metadata = {'chat_id': 'native-chat', 'message_id': 'assistant-message'}
    if fault == 'marker': value['config']['platform_delegation']['kind'] = 'foreign'
    if fault == 'missing_marker': value['config'].clear()
    if fault == 'bool_version': value['config']['platform_delegation']['version'] = True
    if fault == 'server': server_id = 'foreign-server'
    if fault == 'url': value['url'] = 'https://outside.invalid'
    if fault == 'auth': value['auth_type'] = 'session'
    if fault == 'cookie': value['forward_cookies'] = True
    if fault == 'key': value['key'] = 'service_wrong-type'
    if fault == 'chat': metadata.pop('chat_id')
    if fault == 'message': metadata['message_id'] = 'wrong\r\nheader'
    with pytest.raises(RuntimeError, match='forwarding_invalid'):
        invoke(function, value, server_id=server_id, metadata=metadata)
    assert minted == []


def test_unmarked_other_server_never_gets_user_assertion(monkeypatch):
    function, minted = executable_block(monkeypatch)
    value = connection()
    value.update(url='https://other-tool.invalid', config={}, headers={'ordinary-header': 'unchanged'})
    headers, _ = invoke(function, value, server_id='unrelated-server')
    assert headers == {'ordinary-header': 'unchanged'} and minted == []


def test_new_read_scopes_do_not_admit_writes_or_change_existing_bindings(credential_fixture):
    module, store, _ = credential_fixture
    row = store.prepare('openwebui', '知识与数据只读')
    scope = binding(module)
    scope['scope'] += ['m06.read', 'm08.read']
    configured = store.import_binding(row['id'], scope, revision=row['revision'])
    assert {'m06.read', 'm08.read'}.issubset(configured['binding']['scope'])
    assert all(permission.endswith('.read') for permission in module.READ_SCOPES)
    scope['scope'].append('m08.write')
    with pytest.raises(module.CredentialError):
        store.import_binding(row['id'], scope, revision=configured['revision'])
    different = deepcopy(configured['binding'])
    different['scope'].remove('m06.read')
    with pytest.raises(module.CredentialError, match='requires_rotation'):
        store.import_binding(row['id'], different, revision=configured['revision'])
