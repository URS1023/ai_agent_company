"""适配OWUI routers/files.py的原生上传/状态/删除，绝对预算内一次请求，无隐式轮询。"""

import asyncio
import json
from time import monotonic

import httpx

from agent_platform.adapters.native import segment
from agent_platform.core.errors import DomainError

MAX_RESPONSE = 2 * 1024 * 1024


class NativeFileTransport:
    def __init__(self, adapter, *, deadline):
        self.adapter, self.deadline = adapter, deadline

    def call(self, method, path, *, files=None, data=None, params=None, missing_ok=False):
        """异步超时覆盖请求体、响应体和关闭；仅从同步路由线程进入，不读外部错误正文。"""
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise DomainError(504, 'native_file_timeout', '附件操作已超时，请核对原记录')
        async def run():
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(base_url=self.adapter.base_url + '/', trust_env=False,
                        follow_redirects=False, transport=self.adapter.transport,
                        headers={'Authorization': 'Bearer ' + self.adapter.connection.token},
                        timeout=httpx.Timeout(remaining, connect=min(5, remaining))) as client:
                    async with client.stream(method, path.lstrip('/'), files=files, data=data, params=params) as response:
                        if missing_ok and response.status_code == 404:
                            return None
                        self.adapter._check(response)
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_RESPONSE:
                                raise DomainError(502, 'native_file_response_too_large', '附件响应超过读取预算')
                        return json.loads(content)
        try:
            return asyncio.run(run())
        except (TimeoutError, httpx.TimeoutException):
            raise DomainError(504, 'native_file_timeout', '附件操作已超时，请核对原记录') from None
        except httpx.RequestError:
            raise DomainError(503, 'native_file_unavailable', '附件服务暂不可用，请核对原记录') from None
        except (ValueError, UnicodeError, RecursionError):
            raise DomainError(502, 'native_file_response_invalid', '附件服务返回格式不正确') from None

    def upload(self, row, content):
        filename = 'aic-' + row.id + '-' + row.source_name
        return self.call('POST', '/api/v1/files/',
            params={'process': 'true', 'process_in_background': 'true'},
            data={'metadata': json.dumps({'platform_upload_id': row.id, 'file_hash': row.source_sha256}, ensure_ascii=False)},
            files={'file': (filename, content, row.source_content_type)})

    def status(self, native_id):
        return self.call('GET', '/api/v1/files/' + segment(native_id) + '/process/status', params={'stream': 'false'})

    def search(self, row):
        return self.call('GET', '/api/v1/files/search', params={
            'filename': 'aic-' + row.id + '-*', 'content': 'false', 'skip': 0, 'limit': 2}, missing_ok=True)

    def delete(self, native_id):
        return self.call('DELETE', '/api/v1/files/' + segment(native_id), missing_ok=True)
