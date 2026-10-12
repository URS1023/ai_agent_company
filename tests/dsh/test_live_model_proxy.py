"""代理的纯本机测试，不读取部署配置，不连接真实模型或数据库。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit
from uuid import UUID

from live_model_proxy import LiveModelProxy


@contextmanager
def upstream(callback):
    """提供本轮拥有的固定 HTTP 桩，结束后关闭监听。"""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            """测试不输出请求正文。"""

        def do_POST(self):
            self.request_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                callback(self)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/provider/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def request(proxy, path="/v1/chat/completions", extra=None):
    """仅访问上下文创建的随机本机端口。"""
    target = urlsplit(proxy.base_url)
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=2)
    connection.request("POST", path, body=json.dumps({"model": "fixture", "stream": True,
        "messages": [{"role": "user", "content": "PRIVATE_PROMPT_MUST_NOT_BE_REPORTED"}], **(extra or {})}),
        headers={"Content-Type": "application/json", "Authorization": "Bearer local-only"})
    return connection, connection.getresponse()


def wait_settled(proxy):
    """等有限时间收集请求线程的最终统计。"""
    until = time.monotonic() + 1
    while time.monotonic() < until and any(item["ended_at"] is None for item in proxy.records):
        time.sleep(.01)


class LiveModelProxyTest(unittest.TestCase):
    def test_opencode_fixed_model_session_header_and_thinking(self):
        """新模型 ID 优先于过时清单，逐请求注入会话头且保留显式思考设置。"""
        seen = []

        def handle(handler):
            seen.append({"path": handler.path, "authorization": handler.headers["Authorization"],
                         "session": handler.headers.get("x-opencode-session"), "body": json.loads(handler.request_body)})
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b'data: {"model":"deepseek-v4.1-flash","choices":[{"delta":{"content":"fixture"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

        with upstream(handle) as base, tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "opencode.json"
            config.write_text(json.dumps({"provider": {"opencode-go": {
                "options": {"baseURL": base, "apiKey": "opencode-fixture-private-key"},
                "models": {"deepseek-v4-flash": {"name": "old"}}}}}), encoding="utf-8")
            with LiveModelProxy.from_opencode(config) as proxy:
                self.assertEqual(proxy.model, "deepseek-v4.1-flash")
                for extra in ({}, {"thinking": {"type": "enabled"}}):
                    connection, response = request(proxy, extra=extra)
                    self.assertEqual(response.status, 200)
                    response.read()
                    connection.close()
                wait_settled(proxy)
                self.assertEqual(len(seen), 2)
                self.assertNotEqual(seen[0]["session"], seen[1]["session"])
                for item in seen:
                    self.assertEqual(UUID(item["session"]).version, 4)
                    self.assertEqual(item["path"], "/provider/v1/chat/completions")
                    self.assertEqual(item["authorization"], "Bearer opencode-fixture-private-key")
                    self.assertEqual(item["body"]["model"], "deepseek-v4.1-flash")
                self.assertEqual(seen[0]["body"]["thinking"], {"type": "disabled"})
                self.assertEqual(seen[1]["body"]["thinking"], {"type": "enabled"})
                self.assertTrue(all(item["model"] == item["response_model"] == "deepseek-v4.1-flash" for item in proxy.records))
                self.assertEqual(proxy.summary()["provider"], "opencode-go")
                self.assertNotIn("opencode-fixture-private-key", json.dumps(proxy.records))

    def test_stream_is_incremental_and_records_are_content_free(self):
        """首块确实先于尾块到达，统计只保留允许的计量字段。"""
        first = b'data: {"choices":[{"delta":{"content":"PRIVATE_RESPONSE"}}]}\n\n'
        last = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read_file","arguments":"PRIVATE_ARGS"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10,"leak":"SECRET"}}\n\ndata: [DONE]\n\n'
        seen = []
        release = threading.Event()

        def handle(handler):
            seen.append((handler.path, handler.headers["Authorization"]))
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(first)
            handler.wfile.flush()
            release.wait(1)
            handler.wfile.write(last)
            handler.wfile.flush()

        with upstream(handle) as base, LiveModelProxy(base, "private-provider-key", max_requests=1) as proxy:
            connection, response = request(proxy)
            self.assertEqual(response.read1(len(first)), first)
            self.assertIsNone(proxy.records[0]["ended_at"])
            release.set()
            self.assertEqual(response.read(), last)
            connection.close()
            wait_settled(proxy)
            record = proxy.records[0]
            self.assertTrue(record["stream_done"])
            self.assertIsNone(record["error"])
            self.assertIsNotNone(record["ttft_ms"])
            self.assertEqual(record["tool_names"], ["read_file"])
            self.assertEqual(record["usage"], {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
            encoded = json.dumps(proxy.records)
            self.assertNotIn("PRIVATE", encoded)
            self.assertNotIn("private-provider-key", encoded)
            self.assertNotIn("SECRET", encoded)
            self.assertEqual(seen, [("/provider/v1/chat/completions", "Bearer private-provider-key")])
            connection, response = request(proxy)
            self.assertEqual(response.status, 429)
            response.read()
            connection.close()
            self.assertEqual(len(seen), 1)
            self.assertEqual(proxy.summary()["request_count"], 1)

    def test_fixed_path_and_redirect_are_never_followed(self):
        """任意路径拒绝，原站重定向也不产生第二次请求。"""
        calls = []

        def handle(handler):
            calls.append(handler.path)
            handler.send_response(302)
            handler.send_header("Location", "http://127.0.0.1:1/must-not-call")
            handler.end_headers()

        with upstream(handle) as base, LiveModelProxy(base, "fixture") as proxy:
            connection, response = request(proxy, "/v1/chat/completions?target=other")
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()
            self.assertEqual(proxy.records, [])
            connection, response = request(proxy)
            self.assertEqual(response.status, 302)
            response.read()
            connection.close()
            wait_settled(proxy)
            self.assertEqual(calls, ["/provider/v1/chat/completions"])
            self.assertEqual(proxy.records[0]["error"], "upstream_http_302")

    def test_absolute_deadline_stops_trickling_upstream(self):
        """持续心跳不能延长单请求绝对期限，也不会重试。"""
        calls = []
        stopped = threading.Event()

        def handle(handler):
            calls.append(1)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            try:
                for _ in range(30):
                    handler.wfile.write(b": heartbeat\n\n")
                    handler.wfile.flush()
                    time.sleep(.05)
            finally:
                stopped.set()

        with upstream(handle) as base, LiveModelProxy(base, "fixture", request_timeout=.2) as proxy:
            connection, response = request(proxy)
            response.read()
            connection.close()
            wait_settled(proxy)
            self.assertEqual(proxy.records[0]["error"], "request_deadline_exceeded")
            self.assertLess(proxy.records[0]["duration_ms"], 1000)
            self.assertEqual(calls, [1])
            self.assertTrue(stopped.wait(1))

    def test_global_deadline_rejects_later_requests(self):
        """总时间预算耗尽后不再访问原站。"""
        calls = []
        with upstream(lambda handler: calls.append(1)) as base, LiveModelProxy(base, "fixture", total_timeout=.05) as proxy:
            time.sleep(.08)
            connection, response = request(proxy)
            self.assertEqual(response.status, 429)
            response.read()
            connection.close()
            self.assertEqual(calls, [])

    def test_premature_eof_is_not_success(self):
        """上游未发结束标记就断流，报告必须标记不完整。"""
        def handle(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

        with upstream(handle) as base, LiveModelProxy(base, "fixture") as proxy:
            connection, response = request(proxy)
            response.read()
            connection.close()
            wait_settled(proxy)
            self.assertEqual(proxy.records[0]["error"], "incomplete_stream")


if __name__ == "__main__":
    unittest.main()
