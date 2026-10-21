"""服务端持有普通原生JWT的Socket.IO连接；不把整个user room转给浏览器。"""

import asyncio
import json

from agent_platform.core.errors import DomainError


class NativeSocket:
    """回调只过滤及排队，不访问SQLAlchemy Session，也不处理模型/工具循环。"""
    def __init__(self, adapter, chat_id, message_id):
        self.adapter, self.chat_id, self.message_id = adapter, chat_id, message_id
        self.queue = asyncio.Queue(maxsize=128)
        self.client, self.http = None, None
        self.closed, self.fault = False, None

    async def connect(self, timeout_seconds):
        import aiohttp
        import socketio
        self.http = aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout_seconds))
        self.client = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False,
            http_session=self.http)
        self.client.on('events', self.receive)
        self.client.on('disconnect', self.disconnected)
        try:
            async with asyncio.timeout(timeout_seconds):
                await self.client.connect(self.adapter.base_url, auth={'token': self.adapter.connection.token},
                    transports=['websocket'], socketio_path='ws/socket.io', wait_timeout=timeout_seconds, retry=False)
            # SESSION_POOL按Socket.IO namespace sid保存，Engine.IO client.sid不能替代。
            sid = self.client.get_sid('/')
            if not isinstance(sid, str) or not sid:
                raise DomainError(502, 'native_chat_socket_invalid', '原生会话连接未返回有效标识')
            return sid
        except BaseException:
            await self.close()
            raise

    async def receive(self, value):
        if (not isinstance(value, dict) or value.get('chat_id') != self.chat_id
                or value.get('message_id') != self.message_id or value.get('internal') is True):
            return
        event = value.get('data')
        if (not isinstance(event, dict) or not isinstance(event.get('type'), str)
                or not 1 <= len(event['type']) <= 160 or any(ord(char) < 32 for char in event['type'])):
            self.fault = 'native_chat_socket_invalid'
            return
        try:
            encoded = json.dumps(event, ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > 2 * 1024 * 1024:
                raise ValueError()
            self.queue.put_nowait(event)
        except (ValueError, TypeError, RecursionError, asyncio.QueueFull):
            self.fault = 'native_chat_event_budget_exceeded'

    async def disconnected(self, *args):
        self.closed = True

    async def next_event(self, timeout_seconds):
        if self.fault:
            raise DomainError(502, self.fault, '原生对话事件需要重新核对')
        if self.closed and self.queue.empty():
            raise DomainError(503, 'native_chat_socket_closed', '原生对话连接已中断，请核对原运行')
        try:
            return await asyncio.wait_for(self.queue.get(), timeout_seconds)
        except TimeoutError:
            return None

    async def close(self):
        if self.client is not None:
            try:
                async with asyncio.timeout(2):
                    await self.client.disconnect()
            except (Exception, asyncio.CancelledError):
                pass
        if self.http is not None:
            await self.http.close()
        self.closed = True
