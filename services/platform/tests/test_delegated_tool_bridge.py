"""复用桥的内存HTTP替身，验证新增路径及固定签名头；不启动WSL或请求实际服务。"""

import httpx
import pytest

from test_wsl_http_bridge import call, config, load_module


def test_all_and_only_nineteen_delegated_reads_and_one_schema_are_allowed():
    module = load_module('wsl_http_bridge')
    assert len(module.DELEGATED_TOOL_PATHS) == 19
    for path in module.DELEGATED_TOOL_PATHS | {module.DELEGATED_SCHEMA_PATH}:
        assert module.route_target('GET', '/platform' + path)[:2] == ('platform', path)
        for method in ('POST', 'PATCH', 'DELETE'):
            with pytest.raises(module.HTTPException):
                module.route_target(method, '/platform' + path)
    for path in ('unknown', 'get_device/extra', 'get_device.json'):
        with pytest.raises(module.HTTPException):
            module.route_target('GET', '/platform/api/v1/delegated-business-tools/' + path)


def test_only_three_fixed_headers_reach_delegated_reads(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    seen = []
    def upstream(request):
        seen.append(request)
        assert request.headers['x-openwebui-user-jwt'] == 'signed-test-jwt'
        assert request.headers['x-openwebui-chat-id'] == 'native-chat'
        assert request.headers['x-openwebui-message-id'] == 'assistant-message'
        assert 'x-openwebui-user-id' not in request.headers
        assert 'x-platform-user-id' not in request.headers
        assert 'cookie' not in request.headers
        return httpx.Response(403, json={'code': 'native_delegation_denied'})
    response = call(module, config(module, tmp_path, monkeypatch), upstream, 'GET',
        '/platform/api/v1/delegated-business-tools/get_device?id=device-one',
        headers={'Authorization': 'Bearer test-only', 'X-OpenWebUI-User-Jwt': 'signed-test-jwt',
            'X-OpenWebUI-Chat-Id': 'native-chat', 'X-OpenWebUI-Message-Id': 'assistant-message',
            'X-OpenWebUI-User-Id': 'cannot-trust', 'X-Platform-User-Id': 'cannot-trust', 'Cookie': 'never-forward'})
    assert response.status_code == 403 and len(seen) == 1


def test_legacy_path_does_not_gain_delegation_headers(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    def upstream(request):
        assert not any(header.lower() in request.headers for header in module.DELEGATION_HEADERS)
        return httpx.Response(200, json={'items': [], 'total': 0})
    response = call(module, config(module, tmp_path, monkeypatch), upstream, 'GET', '/platform/api/v1/devices',
        headers={'Authorization': 'Bearer test-only', 'X-OpenWebUI-User-Jwt': 'signed-test-jwt'})
    assert response.json() == {'items': [], 'total': 0}


def test_delegated_schema_rewrites_server_and_rejects_legacy_or_unknown_paths(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    path = '/api/v1/delegated-business-tools/get_device'
    value = config(module, tmp_path, monkeypatch)
    response = call(module, value, lambda _: httpx.Response(200, json={'paths': {path: {'get': {}}}}),
        'GET', '/platform' + module.DELEGATED_SCHEMA_PATH)
    assert response.status_code == 200
    assert response.json()['servers'] == [{'url': 'http://172.24.80.1:13002/platform'}]
    for invalid in ('/api/v1/devices', '/api/v1/delegated-business-tools/unknown'):
        response = call(module, value, lambda _: httpx.Response(200, json={'paths': {invalid: {'get': {}}}}),
            'GET', '/platform' + module.DELEGATED_SCHEMA_PATH)
        assert response.status_code == 502


def test_oversized_identity_header_does_not_call_upstream(tmp_path, monkeypatch):
    module = load_module('wsl_http_bridge')
    response = call(module, config(module, tmp_path, monkeypatch), lambda _: pytest.fail('不可派发非法身份头'),
        'GET', '/platform/api/v1/delegated-business-tools/get_device?id=device-one',
        headers={'Authorization': 'Bearer test-only', 'X-OpenWebUI-Chat-Id': 'a' * 161})
    assert response.status_code == 422
