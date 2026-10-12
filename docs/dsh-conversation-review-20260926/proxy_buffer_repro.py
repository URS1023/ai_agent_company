"""用内存 HTTP 分块响应复现代理聚合 SSE 帧，不打开网络连接。"""
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
import json


class FixtureSocket:
    """提供两帧已分块的 SSE 字节流。"""

    def makefile(self, *args):
        chunks = [b'data: first\n\n', b'data: second\n\n']
        body = b''.join(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n' for chunk in chunks)
        return BytesIO(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n' + body + b'0\r\n\r\n')


def read(method):
    """比较当前定长读取与可用片段读取。"""
    response = HTTPResponse(FixtureSocket())
    response.begin()
    return getattr(response, method)(65536).decode()


if __name__ == '__main__':
    result = {'scope': 'in-memory HTTPResponse; no network/database',
              'current_read_65536': read('read'), 'read1_65536': read('read1')}
    assert result['current_read_65536'] == 'data: first\n\ndata: second\n\n'
    assert result['read1_65536'] == 'data: first\n\n'
    Path(__file__).with_name('proxy-buffer-evidence.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
