"""验证真实HTTP头编码边界，避免MockTransport掩盖空Bearer登录失败。"""

import h11
import httpx
import pytest

from agent_platform.adapters.native import NativeConnection
from agent_platform.modules.chat.native_transport import NativeChatAdapter


@pytest.mark.parametrize('credential', ['', 'local-fixture-token'])
def test_native_signin_headers_are_valid_on_the_wire(credential):
    calls = []

    def upstream(request):
        # 使用真实协议编码器校验MockTransport不会校验的头值。
        wire = h11.Connection(h11.CLIENT).send(h11.Request(
            method=request.method.encode(), target=request.url.raw_path, headers=request.headers.raw))
        assert wire.startswith(b'POST /api/v1/auths/signin HTTP/1.1\r\n')
        calls.append(request)
        return httpx.Response(200, json={'id': 'ordinary-user', 'role': 'user'})

    adapter = NativeChatAdapter(NativeConnection('http://127.0.0.1:13000', credential,
        auth_required=bool(credential)), transport=httpx.MockTransport(upstream))
    result = adapter.signin('fixture@localhost', 'fixture-password')
    assert result == {'id': 'ordinary-user', 'role': 'user'}
    assert len(calls) == 1
    if credential:
        assert calls[0].headers['authorization'] == 'Bearer ' + credential
    else:
        assert 'authorization' not in calls[0].headers
