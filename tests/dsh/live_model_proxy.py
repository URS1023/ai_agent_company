"""有限真实模型验收代理；只在内存记录指标，不保存提示词、正文或凭据。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4


class LiveModelProxy:
    """固定上游的本机代理，限制请求总数及绝对时间；不做重试或重定向。"""

    def __init__(self, base_url, api_key, *, model="", max_requests=40,
                 total_timeout=480, request_timeout=90, opencode=False):
        target = urlsplit(base_url)
        if (target.scheme not in {"http", "https"} or not target.hostname or target.username
                or target.password or target.query or target.fragment):
            raise ValueError("模型服务地址必须是没有凭据或查询参数的 HTTP(S) 地址")
        if target.scheme == "http" and target.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("真实模型上游必须使用 HTTPS；HTTP 仅允许本机桩测试")
        if not api_key or not 1 <= max_requests <= 40 or not 0 < total_timeout <= 480 or not 0 < request_timeout <= 90:
            raise ValueError("代理凭据或请求预算无效")
        self._target = target
        prefix = target.path.rstrip("/")
        self._path = prefix if prefix.endswith("/chat/completions") else prefix + "/chat/completions"
        self._api_key = api_key
        self.model = model
        self._opencode = opencode
        self.max_requests = max_requests
        self.total_timeout = total_timeout
        self.request_timeout = request_timeout
        self._lock = threading.RLock()
        self._records = []
        self._connections = set()
        self._stopped = threading.Event()
        self._server = None
        self._started = None
        self._ended = None

    @classmethod
    def from_env(cls, env_file=None, **limits):
        """仅读取配置到内存；显式进程环境优先，避免输出配置值。"""
        values = {}
        if env_file is not None:
            from dotenv import dotenv_values
            values = dotenv_values(Path(env_file), interpolate=False)
        base_url = os.environ.get("AGNES_BASE_URL") or values.get("AGNES_BASE_URL")
        api_key = os.environ.get("AGNES_API_KEY") or os.environ.get("AGNES_KEY") or values.get("AGNES_API_KEY") or values.get("AGNES_KEY")
        model = os.environ.get("AGNES_CHAT_MODEL") or values.get("AGNES_CHAT_MODEL") or ""
        if not base_url or not api_key:
            raise ValueError("缺少 AGNES_BASE_URL 或 AGNES_API_KEY")
        return cls(base_url, api_key, model=model, **limits)

    @classmethod
    def from_opencode(cls, config_file, model="deepseek-v4.1-flash", **limits):
        """读取 OpenCode Go 凭据，模型使用本轮明确指定的 ID，不沿用旧模型清单。"""
        try:
            value = json.loads(Path(config_file).read_text(encoding="utf-8-sig"))
            options = value["provider"]["opencode-go"]["options"]
            base_url, api_key = options["baseURL"], options["apiKey"]
            if not isinstance(base_url, str) or not isinstance(api_key, str):
                raise ValueError("invalid_provider_options")
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("OpenCode 配置缺少有效的 opencode-go baseURL/apiKey") from None
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", model):
            raise ValueError("OpenCode 验收模型 ID 无效")
        return cls(base_url, api_key, model=model, opencode=True, **limits)

    @property
    def base_url(self):
        """仅暴露本机入口，调用方不需要拿到真实凭据。"""
        if self._server is None:
            raise RuntimeError("代理尚未启动")
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    @property
    def records(self):
        """返回脱敏快照，调用方不能改变运行中的统计对象。"""
        with self._lock:
            return deepcopy(self._records)

    def summary(self):
        """真实模型网络时间独立于 DSH 启动、工具和数据库用时。"""
        records = self.records
        return {"request_count": len(records), "max_requests": self.max_requests,
                "provider": "opencode-go" if self._opencode else "configured", "model": self.model,
                "elapsed_seconds": round((self._ended or time.monotonic()) - self._started, 3) if self._started else 0,
                "total_timeout_seconds": self.total_timeout, "request_timeout_seconds": self.request_timeout,
                "model_request_seconds": round(sum(item.get("duration_ms") or 0 for item in records) / 1000, 3),
                "errors": sum(bool(item.get("error")) for item in records),
                "active_requests": sum(item.get("ended_at") is None for item in records)}

    def __enter__(self):
        if self._server is not None:
            raise RuntimeError("代理上下文不能重复使用")
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            """只接受固定补全路径，不开放通用转发能力。"""
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                """不输出 HTTP 请求及响应正文。"""

            def do_POST(self):
                proxy._handle(self)

            def do_GET(self):
                proxy._reject(self, 405, "post_required")

            def do_CONNECT(self):
                proxy._reject(self, 405, "unsupported_method")

        class Server(ThreadingHTTPServer):
            """请求线程随拥有本轮代理的进程退出。"""
            daemon_threads = True

            def handle_error(self, request, client_address):
                """异常由请求指标记录，避免标准库打印含调用参数的堆栈。"""

        self._server = Server(("127.0.0.1", 0), Handler)
        self._started = time.monotonic()
        self._timer = threading.Timer(self.total_timeout, self._stop_requests)
        self._timer.daemon = True
        self._timer.start()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._timer.cancel()
        self._stop_requests()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        self._ended = time.monotonic()

    def _stop_requests(self):
        self._stopped.set()
        with self._lock:
            connections = tuple(self._connections)
        for connection, client in connections:
            self._abort(connection, client)

    @staticmethod
    def _abort(connection, client):
        """绝对期限到达时中断套接字，不能靠持续小块响应延长预算。"""
        for current in (connection.sock or getattr(connection, "_proxy_socket", None), client):
            if current is not None:
                try:
                    current.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        connection.close()

    @staticmethod
    def _reject(handler, status, code):
        body = json.dumps({"error": {"code": code, "message": code}}).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        handler.close_connection = True

    @staticmethod
    def _utc():
        return datetime.now(timezone.utc).isoformat()

    def _handle(self, handler):
        if handler.path != "/v1/chat/completions":
            return self._reject(handler, 404, "unsupported_path")
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            return self._reject(handler, 400, "invalid_body_length")
        if handler.headers.get("Transfer-Encoding") or not 0 < length <= 16 * 1024 * 1024:
            return self._reject(handler, 413, "invalid_body_length")
        with self._lock:
            if self._stopped.is_set() or time.monotonic() - self._started >= self.total_timeout:
                return self._reject(handler, 429, "total_time_budget_exhausted")
            if len(self._records) >= self.max_requests:
                return self._reject(handler, 429, "request_budget_exhausted")
            started = time.monotonic()
            record = {"request_index": len(self._records) + 1, "started_at": self._utc(), "ended_at": None,
                      "start_offset_ms": round((started - self._started) * 1000, 3),
                      "ttft_ms": None, "first_byte_ms": None, "duration_ms": None,
                      "http_status": None, "usage": {}, "tool_names": [], "finish_reasons": [],
                      "error": None, "response_bytes": 0, "stream_done": False, "attempts": 1}
            self._records.append(record)
        deadline = min(started + self.request_timeout, self._started + self.total_timeout)
        connection_class = http.client.HTTPSConnection if self._target.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self._target.hostname, self._target.port, timeout=max(.01, deadline - started))
        timer = threading.Timer(max(.01, deadline - time.monotonic()), self._abort, args=(connection, handler.connection))
        timer.daemon = True
        pair = (connection, handler.connection)
        with self._lock:
            self._connections.add(pair)
        response = None
        headers_sent = False
        try:
            timer.start()
            handler.connection.settimeout(max(.01, deadline - time.monotonic()))
            body = handler.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete_body")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("invalid_json_body")
            if self._stopped.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("request_deadline_exceeded")
            if self._opencode:
                # 固定本轮验收模型，并与现有 OpenCode 代理保持缺省思考参数一致。
                payload["model"] = self.model
                payload.setdefault("thinking", {"type": "disabled"})
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            actual_model = payload.get("model")
            with self._lock:
                record["model"] = actual_model if isinstance(actual_model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", actual_model) else None
                record["provider"] = "opencode-go" if self._opencode else "configured"
            # 原始请求只用于当前转发；统计不保存消息、工具参数或模型正文。
            parser = _ResponseMetrics(record, started, self._lock)
            connection.request("POST", self._path, body=body, headers={
                "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json",
                "Accept": "text/event-stream" if payload.get("stream") else "application/json",
                "Accept-Encoding": "identity", "Connection": "close",
                **({"x-opencode-session": str(uuid4())} if self._opencode else {})})
            connection._proxy_socket = connection.sock
            del body, payload
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "application/octet-stream")
            with self._lock:
                record["http_status"] = response.status
                if not 200 <= response.status < 300:
                    record["error"] = "upstream_http_" + str(response.status)
            handler.send_response(response.status)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()
            headers_sent = True
            while not self._stopped.is_set() and time.monotonic() < deadline:
                chunk = response.read1(8192)
                if not chunk:
                    break
                with self._lock:
                    record["response_bytes"] += len(chunk)
                    if record["first_byte_ms"] is None:
                        record["first_byte_ms"] = round((time.monotonic() - started) * 1000, 3)
                parser.feed(chunk, "text/event-stream" in content_type)
                handler.wfile.write(chunk)
                handler.wfile.flush()
            parser.finish("text/event-stream" in content_type)
            with self._lock:
                if self._stopped.is_set() or time.monotonic() >= deadline:
                    record["error"] = "total_time_budget_exhausted" if self._stopped.is_set() else "request_deadline_exceeded"
                elif not record["error"] and "text/event-stream" in content_type and not record["stream_done"]:
                    record["error"] = "incomplete_stream"
        except Exception as exc:
            with self._lock:
                record["error"] = ("total_time_budget_exhausted" if self._stopped.is_set() else
                    "request_deadline_exceeded" if time.monotonic() >= deadline else
                    "invalid_request" if isinstance(exc, (ValueError, json.JSONDecodeError)) else type(exc).__name__)
            if not headers_sent:
                try:
                    self._reject(handler, 502, record["error"])
                except OSError:
                    pass
        finally:
            timer.cancel()
            if response:
                response.close()
            connection.close()
            handler.close_connection = True
            with self._lock:
                self._connections.discard(pair)
                record["ended_at"] = self._utc()
                record["duration_ms"] = round((time.monotonic() - started) * 1000, 3)


class _ResponseMetrics:
    """有界解析流式事件，提取时间和计数后立即丢弃原始模型内容。"""
    LIMIT = 2 * 1024 * 1024

    def __init__(self, record, started, lock):
        self.record = record
        self.started = started
        self.lock = lock
        self.buffer = b""
        self.discard_line = False
        self.tool_names = {}

    def feed(self, chunk, streaming):
        if not streaming:
            self.buffer = (self.buffer + chunk)[:self.LIMIT]
            return
        for segment in chunk.splitlines(keepends=True):
            if not self.discard_line:
                self.buffer += segment
                if len(self.buffer) > self.LIMIT:
                    self.buffer = b""
                    self.discard_line = True
            if segment.endswith(b"\n"):
                if not self.discard_line:
                    self._line(self.buffer)
                self.buffer = b""
                self.discard_line = False

    def _line(self, line):
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data == b"[DONE]":
            with self.lock:
                self.record["stream_done"] = True
            return
        self._value(data)

    def _value(self, raw):
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(value, dict):
            return
        with self.lock:
            response_model = value.get("model")
            if isinstance(response_model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", response_model):
                self.record["response_model"] = response_model
            usage = value.get("usage")
            if isinstance(usage, dict):
                self.record["usage"] = _numeric_usage(usage)
            for choice_number, choice in enumerate(value.get("choices") or []):
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or choice.get("message") or {}
                if not isinstance(delta, dict):
                    continue
                meaningful = bool(delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"))
                for tool_number, tool in enumerate(delta.get("tool_calls") or []):
                    function = tool.get("function", {}) if isinstance(tool, dict) else {}
                    name = function.get("name")
                    meaningful |= bool(name or function.get("arguments"))
                    if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name):
                        key = (choice.get("index", choice_number), tool.get("index", tool_number))
                        if key in self.tool_names or len(self.tool_names) < 40:
                            self.tool_names[key] = (self.tool_names.get(key, "") + name)[:128]
                            self.record["tool_names"] = list(dict.fromkeys(self.tool_names.values()))
                if meaningful and self.record["ttft_ms"] is None:
                    self.record["ttft_ms"] = round((time.monotonic() - self.started) * 1000, 3)
                reason = choice.get("finish_reason")
                if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,40}", reason):
                    if reason not in self.record["finish_reasons"]:
                        self.record["finish_reasons"].append(reason)

    def finish(self, streaming):
        if self.buffer:
            self._line(self.buffer) if streaming else self._value(self.buffer)
        self.buffer = b""


def _numeric_usage(value):
    """只保留已知用量字段内的非负数字，禁止错误正文混入报告。"""
    names = {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
             "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cached_tokens", "reasoning_tokens",
             "audio_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
             "prompt_tokens_details", "completion_tokens_details", "input_tokens_details", "output_tokens_details"}
    result = {}
    for key, item in value.items():
        if key not in names:
            continue
        if isinstance(item, dict):
            result[key] = _numeric_usage(item)
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and item >= 0:
            result[key] = item
    return result
