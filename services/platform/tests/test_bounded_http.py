"""仅使用HTTPX内存传输验证总时限、流关闭和响应大小，不进行网络连接。"""

import asyncio
import gzip

import httpx
import pytest

from agent_platform.core import bounded_http

ASYNC_CLIENT = httpx.AsyncClient

class Body(httpx.AsyncByteStream):
    """可被取消的内存响应，记录资源是否已释放。"""

    def __init__(self, chunks, delay=0):
        self.chunks, self.delay, self.closed = chunks, delay, False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def install(monkeypatch, body, *, status=200, headers=None):
    requests, settings = [], []
    async def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=headers or {}, stream=body)
    def client(**kwargs):
        settings.append(kwargs)
        return ASYNC_CLIENT(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(bounded_http.httpx, 'AsyncClient', client)
    return requests, settings


def test_json_transport_is_bounded_and_does_not_inherit_proxy_or_follow_redirects(monkeypatch):
    body = Body([b'{"value":42}'])
    requests, settings = install(monkeypatch, body)
    response = bounded_http.post_json('https://mock.invalid/api', headers={'Authorization': 'Bearer mock'},
        payload={'question': 'test'}, timeout_seconds=1)
    assert response.json() == {'value': 42} and body.closed
    assert len(requests) == 1 and settings[0]['trust_env'] is False and settings[0]['follow_redirects'] is False


def test_absolute_deadline_cancels_dribbling_body_and_closes_transport(monkeypatch):
    body = Body([b'a', b'b', b'c'], delay=0.03)
    requests, _ = install(monkeypatch, body)
    with pytest.raises(httpx.ReadTimeout):
        bounded_http.post_json('https://mock.invalid/api', headers={}, payload={}, timeout_seconds=0.04)
    assert len(requests) == 1 and body.closed


def test_large_success_and_any_error_body_are_not_unbounded_reads(monkeypatch):
    body = Body([b'x' * bounded_http.MAX_RESPONSE_BYTES, b'x'])
    install(monkeypatch, body)
    with pytest.raises(httpx.RemoteProtocolError):
        bounded_http.post_json('https://mock.invalid/api', headers={}, payload={}, timeout_seconds=1)
    assert body.closed
    error = Body([b'never expose upstream secret'], delay=100)
    install(monkeypatch, error, status=502)
    response = bounded_http.post_json('https://mock.invalid/api', headers={}, payload={}, timeout_seconds=1)
    assert response.content == b'' and error.closed


def test_gzip_body_is_decoded_exactly_once(monkeypatch):
    body = Body([gzip.compress(b'{"value":42}')])
    install(monkeypatch, body, headers={'content-encoding': 'gzip', 'content-type': 'application/json'})
    assert bounded_http.post_json('https://mock.invalid/api', headers={}, payload={}, timeout_seconds=1).json() == {'value': 42}
    assert body.closed


@pytest.mark.parametrize('value', [0, -1, float('inf'), float('nan')])
def test_invalid_budget_does_not_create_client(monkeypatch, value):
    monkeypatch.setattr(bounded_http.httpx, 'AsyncClient', lambda **kwargs: pytest.fail('不应创建客户端'))
    with pytest.raises(httpx.ReadTimeout):
        bounded_http.post_json('https://mock.invalid/api', headers={}, payload={}, timeout_seconds=value)


def test_sync_entry_rejects_running_event_loop_before_client_creation(monkeypatch):
    monkeypatch.setattr(bounded_http.httpx, 'AsyncClient', lambda **kwargs: pytest.fail('不应创建客户端'))
    async def call():
        with pytest.raises(RuntimeError, match='requires_sync_route_thread'):
            bounded_http.post_json('https://mock.invalid/api', headers={}, payload={}, timeout_seconds=1)
    asyncio.run(call())
