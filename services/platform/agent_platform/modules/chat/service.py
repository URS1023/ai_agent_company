"""平台原生对话：直接调用本机已配置的模型服务，不经过任何外部对话系统。"""

from __future__ import annotations

import json
from typing import Iterator
from uuid import uuid4

import httpx

from agent_platform.core.errors import DomainError
from agent_platform.core.provider_settings import CHAT_MODEL, LocalProviderStore


def _upstream_error(status: int) -> DomainError:
    """把上游状态映射成用户能读懂的领域错误；429 单独提示限流。"""
    if status == 429:
        return DomainError(429, 'chat_rate_limited', '模型服务正在限流，请稍后重试')
    return DomainError(502, 'chat_upstream_failed', f'模型服务返回 {status}，请检查模型配置与额度')


class ChatStream:
    """已建立的上游事件流；迭代结束或取消时都会释放连接。"""

    def __init__(self, client: httpx.Client, response: httpx.Response) -> None:
        self._client = client
        self._response = response

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._response.iter_bytes()
        finally:
            self.close()

    def close(self) -> None:
        self._response.close()
        self._client.close()


class ChatService:
    """只读本机加密模型配置，按请求流式转发；不在服务端保存对话内容。"""

    def __init__(self, store: LocalProviderStore | None = None) -> None:
        self.store = store or LocalProviderStore()

    def profile(self) -> dict:
        """当前对话模型信息；不返回密钥和完整地址，避免泄露凭据。"""
        try:
            chat = self.store.read().get('chat', {})
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            raise DomainError(503, 'chat_model_unconfigured', '尚未配置对话模型，请先完成模型安装') from exc
        if not chat.get('api_key'):
            raise DomainError(503, 'chat_model_unconfigured', '尚未配置对话模型，请先完成模型安装')
        return {'model': chat.get('model', CHAT_MODEL), 'provider': chat.get('provider', ''), 'configured': True}

    def _request(self, messages: list[dict], *, max_tokens: int, temperature: float, stream: bool,
                 session: str | None = None) -> tuple[str, dict, dict]:
        try:
            chat = self.store.read().get('chat', {})
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            raise DomainError(503, 'chat_model_unconfigured', '尚未配置对话模型，请先完成模型安装') from exc
        base_url, api_key, model = chat.get('base_url', ''), chat.get('api_key', ''), chat.get('model', CHAT_MODEL)
        if not base_url or not api_key:
            raise DomainError(503, 'chat_model_unconfigured', '尚未配置对话模型，请先完成模型安装')
        payload = {'model': model, 'messages': messages, 'stream': stream,
                   'max_tokens': max_tokens, 'temperature': temperature}
        # 上游网关按会话标识路由与复用连接，缺少该头会直接拒绝。
        headers = {'Authorization': f'Bearer {api_key}',
                   'x-opencode-session': session or uuid4().hex}
        return f'{base_url.rstrip("/")}/chat/completions', payload, headers

    def open_stream(self, messages: list[dict], *, max_tokens: int = 1600, temperature: float = 0.3,
                    session: str | None = None) -> ChatStream:
        """先建立上游连接并校验状态，再交给 StreamingResponse。

        这样 429 与 5xx 能以正常 JSON 错误返回，而不是先发 200 再断开连接。
        """
        url, payload, headers = self._request(messages, max_tokens=max_tokens, temperature=temperature,
                                              stream=True, session=session)
        client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        try:
            request = client.build_request('POST', url, json=payload, headers=headers)
            response = client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            client.close()
            raise DomainError(504, 'chat_timeout', '模型响应超时，请稍后重试') from exc
        except httpx.RequestError as exc:
            client.close()
            raise DomainError(503, 'chat_unavailable', '无法连接模型服务，请检查网络与模型配置') from exc
        if response.status_code >= 400:
            response.read()
            response.close()
            client.close()
            raise _upstream_error(response.status_code)
        if 'text/event-stream' not in response.headers.get('content-type', ''):
            response.close()
            client.close()
            raise DomainError(502, 'chat_invalid_stream', '模型服务未返回流式响应')
        return ChatStream(client, response)

    def complete(self, messages: list[dict], *, max_tokens: int = 1600, temperature: float = 0.3,
                 session: str | None = None, timeout_seconds: float | None = None) -> dict:
        """非流式补全，供需要一次性结果的业务使用。"""
        url, payload, headers = self._request(messages, max_tokens=max_tokens, temperature=temperature,
                                              stream=False, session=session)
        try:
            if timeout_seconds is not None:
                from agent_platform.core.bounded_http import post_json
                response = post_json(url, headers=headers, payload=payload, timeout_seconds=timeout_seconds)
            else:
                with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                    response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise DomainError(504, 'chat_timeout', '模型响应超时，请稍后重试') from exc
        except httpx.RequestError as exc:
            raise DomainError(503, 'chat_unavailable', '无法连接模型服务，请检查网络与模型配置') from exc
        if response.status_code >= 400:
            raise _upstream_error(response.status_code)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise DomainError(502, 'chat_invalid_response', '模型服务返回了无法识别的响应') from exc
        choices = data.get('choices') if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise DomainError(502, 'chat_invalid_response', '模型服务没有返回生成结果')
        return data
