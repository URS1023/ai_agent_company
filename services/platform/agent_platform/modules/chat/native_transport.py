"""锁定原生聊天REST合同；只在准确端点接受bool/null，不放宽通用NativeHTTP协议。"""

import asyncio
import json

import httpx

from agent_platform.adapters.native import OpenWebUIAdapter, segment
from agent_platform.core.errors import DomainError


class NativeChatAdapter(OpenWebUIAdapter):
    """访问已配置实例，不接受用户提供URL、任意路径或原生数据库连接。"""
    max_response_bytes = 2 * 1024 * 1024

    async def async_call(self, method, path, *, payload=None, timeout_seconds=10, allow_null=False, allow_bool=False):
        """Socket监控线程中的绝对HTTP预算；连接、逐块读取和释放共用同一次剩余时限。"""
        # 登录尚无令牌；空Bearer的尾空格会被真实HTTP协议层拒绝。
        headers = {'Authorization': 'Bearer ' + self.connection.token} if self.connection.token else {}
        try:
            async with asyncio.timeout(timeout_seconds):
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=self.transport,
                        timeout=httpx.Timeout(timeout_seconds, connect=min(5, timeout_seconds)),
                        headers=headers) as client:
                    async with client.stream(method, self.base_url + '/' + path.lstrip('/'), json=payload) as response:
                        try:
                            self._check(response)
                        except DomainError as exc:
                            exc.fields['native_http_status'] = response.status_code
                            raise
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > self.max_response_bytes:
                                raise DomainError(502, 'native_chat_response_too_large', '对话内容超过本次读取预算')
                        data = json.loads(content)
                        if (isinstance(data, (dict, list)) or (type(data) is bool and allow_bool)
                                or (data is None and allow_null and response.status_code == 200
                                and bytes(content).strip() == b'null')):
                            return data
                        raise ValueError('unexpected response')
        except (TimeoutError, httpx.TimeoutException):
            raise DomainError(504, 'native_chat_timeout', '原生对话响应超时，请核对原操作') from None
        except httpx.RequestError:
            raise DomainError(503, 'native_chat_unavailable', '原生对话服务暂不可用') from None
        except (ValueError, UnicodeError, RecursionError):
            raise DomainError(502, 'native_chat_invalid_response', '原生对话返回格式不正确') from None

    def json_call(self, method, path, *, payload=None, allow_null=False, allow_bool=False):
        """同步路由复用同一绝对读取边界，不让逐块慢响应无限刷新inactivity timeout。"""
        return asyncio.run(self.async_call(method, path, payload=payload, allow_null=allow_null, allow_bool=allow_bool,
            timeout_seconds=self.connection.timeout))

    def add_user(self, email, password, name):
        return self.json_call('POST', '/api/v1/auths/add', payload={
            'email': email, 'password': password, 'name': name, 'role': 'user'})

    def signin(self, email, password):
        return self.json_call('POST', '/api/v1/auths/signin', payload={'email': email, 'password': password})

    def profile(self):
        return self.json_call('GET', '/api/v1/auths/')

    def models(self):
        return self.json_call('GET', '/api/models')

    def create_chat(self, title):
        return self.json_call('POST', '/api/v1/chats/new', payload={'chat': {
            'title': title, 'history': {'messages': {}, 'currentId': None}, 'messages': []}})

    def chat(self, chat_id):
        return self.json_call('GET', '/api/v1/chats/' + segment(chat_id))

    def rename_chat(self, chat_id, title):
        return self.json_call('POST', '/api/v1/chats/' + segment(chat_id), payload={'chat': {'title': title}})

    def edit_message(self, chat_id, message_id, content):
        return self.json_call('POST', f'/api/v1/chats/{segment(chat_id)}/messages/{segment(message_id)}',
            payload={'content': content})

    def fork_chat(self, chat_id, message_id):
        return self.json_call('POST', f'/api/v1/chats/{segment(chat_id)}/fork', payload={'message_id': message_id})

    def remove_chat(self, chat_id):
        return self.json_call('DELETE', '/api/v1/chats/' + segment(chat_id), allow_bool=True)

    def saved_completion(self, payload):
        """Socket事件模式可合法200/null或任务对象，终态另由准确消息读取确认。"""
        return self.json_call('POST', '/api/chat/completions', payload=payload, allow_null=True)

    def stop_chat(self, chat_id):
        return self.json_call('POST', f'/api/tasks/chat/{segment(chat_id)}/stop')

    def resolve(self, chat_id, message_id, payload):
        return self.json_call('POST', f'/api/v1/chats/{segment(chat_id)}/messages/{segment(message_id)}/resolve', payload=payload)
