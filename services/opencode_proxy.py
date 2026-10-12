"""OpenCode /zen/go 头注入代理（修复 harness 原生请求缺 x-opencode-session）。

背景：opencode.ai/zen/go/v1 要求每个请求携带 ``x-opencode-session`` 头
（缺失返回 400 MissingSessionID）。平台自身 OpenAI 网关（providers/model.py）
已按 host 自动添加，但 DSH 原生进程直接发往模型端点，没有该头。

本代理：本地监听（默认 127.0.0.1:8901），把 /v1/* 转发到
https://opencode.ai/zen/go/v1/*，保留宿主的稳定 ``x-opencode-session``，旧客户端缺失时补 UUID，
并保持 SSE/chunked 流式透传；错误状态与响应体原样回传。

用法：python services/opencode_proxy.py [--port 8901] [--target https://opencode.ai/zen/go/v1]
模型 profile 的 endpoint 改为 http://127.0.0.1:<port>/v1 即可（key/model 不变）。
生产化建议：后续在上游 llm 适配器加官方 opencode 支持后，可停用本代理。
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import json
import socket
import ssl
import time
import uuid
from urllib.parse import urlsplit

_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host"}


class OpenCodeProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    _target: str = "https://opencode.ai/zen/go/v1"
    _timeout: int = 300

    def _relay(self, method: str) -> None:
        """逐块转发上游响应，保持首个 SSE 事件即时可见。"""
        conn = None
        response_started = False
        try:
            body = None
            length = self.headers.get("Content-Length")
            raw_body = self.rfile.read(int(length)) if length and length.isdigit() else None
            if method == "POST" and raw_body:
                # opencode 的 OpenAI 兼容路由在大载荷下默认开启 thinking，deepseek-v4-flash
                # 会输出超长推理流导致超时/上下文耗尽（修复背景见 docs/implementation/
                # 20260924-opencode-ds4f-integration.md）。请求体未显式声明 thinking 时注入关闭。
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if isinstance(payload, dict) and "thinking" not in payload:
                        payload["thinking"] = {"type": "disabled"}
                    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    pass  # 非 JSON 或不可解析：原样转发
            body = raw_body
            path = self.path
            target = self._target.rstrip("/")
            if path.startswith("/v1"):
                path = path[len("/v1"):]
            url = urlsplit(target)
            scheme_port = 443 if url.scheme == "https" else 80
            conn = http.client.HTTPSConnection(url.hostname, url.port or scheme_port,
                                               timeout=self._timeout, context=ssl.create_default_context()) \
                if url.scheme == "https" else http.client.HTTPConnection(url.hostname, url.port or scheme_port, timeout=self._timeout)
            headers = {}
            for key, value in self.headers.items():
                if key.lower() in _HOP_BY_HOP:
                    continue
                headers[key] = value
            headers["Host"] = url.netloc
            # 宿主已按工作区和会话生成稳定标识，不能每次覆盖而破坏多轮缓存。
            for key in list(headers):
                if key.lower() == "x-opencode-session":
                    del headers[key]
            headers["x-opencode-session"] = self.headers.get("x-opencode-session") or str(uuid.uuid4())
            headers["Connection"] = "close"
            conn.request(method, url.path + path, body=body, headers=headers)
            started = time.perf_counter()
            response = conn.getresponse()
            elapsed = time.perf_counter() - started
            # 请求级计时（路由/状态/耗时），便于定位慢调用；不记录请求内容。
            print(f"[opencode-proxy] {method} {path} -> {response.status} {elapsed:.1f}s body={len(body) if body else 0}B", flush=True)
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() in _HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            self.close_connection = True
            while True:
                # read(n) 会等待填满 n 或 EOF；read1 只读取当前可用的数据。
                chunk = response.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # 浏览器取消时立即关闭上游；不继续生成无人消费的响应。
            self.close_connection = True
        except Exception as exc:  # 代理失败必须显式回传错误，不能让客户端挂起
            self.close_connection = True
            if response_started:
                # 响应体已经开始时不能再拼入第二个 HTTP 状态行。
                return
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(f"opencode proxy error: {type(exc).__name__}: {exc}".encode("utf-8"))
            except OSError:
                pass
        finally:
            if conn is not None:
                conn.close()

    def do_GET(self):
        self._relay("GET")

    def do_POST(self):
        self._relay("POST")

    def do_PUT(self):
        self._relay("PUT")

    def do_DELETE(self):
        self._relay("DELETE")

    def log_message(self, fmt, *args):
        pass  # 静默：交由平台日志体系承载


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8901)
    parser.add_argument("--target", default="https://opencode.ai/zen/go/v1")
    args = parser.parse_args(argv)
    OpenCodeProxyHandler._target = args.target
    server = http.server.ThreadingHTTPServer((args.host, args.port), OpenCodeProxyHandler)
    print(f"opencode proxy listening http://{args.host}:{args.port} -> {args.target}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
