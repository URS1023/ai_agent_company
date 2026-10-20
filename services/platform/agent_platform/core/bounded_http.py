"""供同步业务路由使用的有界JSON请求；绝对时限覆盖连接、响应体及资源释放。"""

from __future__ import annotations

import asyncio
import math

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def post_json(url: str, *, headers: dict, payload: dict, timeout_seconds: float) -> httpx.Response:
    """只在同步路由线程调用；不重试、不跟重定向、不继承系统代理，密钥不进入异常文本。"""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise httpx.ReadTimeout('request budget exhausted')
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('bounded_http_requires_sync_route_thread')

    async def request():
        try:
            async with asyncio.timeout(timeout_seconds):
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                        timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))) as client:
                    async with client.stream('POST', url, headers=headers, json=payload) as response:
                        content = bytearray()
                        if response.status_code < 400:
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > MAX_RESPONSE_BYTES:
                                    raise httpx.RemoteProtocolError('response exceeds byte budget')
                        # 不读取外部错误正文，避免超量、未知编码或凭据被错误回传。
                        # aiter_bytes已完成解压，不能把上游Content-Encoding再传给新响应导致二次解码。
                        return httpx.Response(response.status_code,
                            headers={'content-type': response.headers.get('content-type', 'application/json')},
                            content=bytes(content))
        except TimeoutError as exc:
            raise httpx.ReadTimeout('request deadline exceeded') from exc
    return asyncio.run(request())
