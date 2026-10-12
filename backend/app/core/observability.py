"""后端节点日志：统一上下文、中文事件、脱敏输出和按进程滚动落盘。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import inspect
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading
import time
import traceback
from uuid import uuid4

_context: ContextVar[dict] = ContextVar("execution_log_context", default={})
_setup_lock = threading.RLock()
_logger = logging.getLogger("app.execution")
_SECRET_KEY = re.compile(r"^(?:authorization|cookie|set.cookie|password|passwd|pwd|api.?key|token|access.?token|refresh.?token|secret|secret_values|credentials|.*_password|.*_secret|.*_api_key)$", re.I)
_PRIVATE_KEY = re.compile(r"^(?:content|messages|prompt|arguments|arguments_json|sql|generated_sql|parameters|rows|data|inputs|output|result_json)$", re.I)
_SECRET_ASSIGNMENT = re.compile(r'''((?:["']?)(?:password|passwd|pwd|api[_-]?key|token|access[_-]?token|refresh[_-]?token|secret|authorization|cookie)(?:["']?)\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&}\]]+)''', re.I)


def redact(text: object) -> str:
    """清除常见凭据形态，同时转义换行，防止日志被输入伪造成多行。"""
    value = str(text)
    value = re.sub(r"\b(?:sk-|sk_)[A-Za-z0-9_-]{6,}", "[已脱敏]", value)
    value = re.sub(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", "[已脱敏认证]", value, flags=re.I)
    value = re.sub(r"(\w+(?:\+\w+)?://)[^/\s:@]+:[^\s/@]+@", r"\1[已脱敏]@", value)
    value = _SECRET_ASSIGNMENT.sub(r"\1[已脱敏]", value)
    return value.replace("\r", "\\r").replace("\n", "\\n")


def safe_fields(values: dict, depth: int = 0) -> dict:
    """只序列化有界诊断元数据，不把业务正文或数据库行复制到文件日志。"""
    clean = {}
    for key, value in list(values.items())[:60]:
        key = str(key)[:80]
        if _SECRET_KEY.match(key) or _PRIVATE_KEY.match(key):
            clean[key] = "[已省略]"
        elif value is None or isinstance(value, (bool, int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = redact(value)[:800]
        elif isinstance(value, dict) and depth < 3:
            clean[key] = safe_fields(value, depth + 1)
        elif isinstance(value, (list, tuple)):
            clean[key] = [redact(item)[:160] for item in value[:20] if isinstance(item, (str, int, float, bool))]
    return clean


def current_context() -> dict:
    """取得当前追踪标识的副本，供显式创建的后台线程继承。"""
    return dict(_context.get())


@contextmanager
def log_context(**fields):
    """在当前线程或异步任务中绑定标识，嵌套返回后恢复父级上下文。"""
    token = _context.set({**_context.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def log_event(event: str, message: str, *, level: int = logging.INFO,
              human_label: str | None = None, human_content: object | None = None,
              **fields) -> None:
    """输出一条节点事件；可选的人读正文只进入人读日志，不进入机器审计字段。"""
    try:
        context = _context.get()
        if context.get("conversation_id") and fields.get("conversation_id") not in (None, context["conversation_id"]):
            # SQL、工作流可能另建内部会话；对话日志仍以用户原会话检索，另存记录归属。
            fields = {**fields, "record_conversation_id": fields["conversation_id"],
                      "conversation_id": context["conversation_id"]}
        extra = {"execution_event": event,
                 "execution_fields": safe_fields({**context, **fields})}
        if human_label is not None:
            extra["human_label"] = str(human_label)[:40]
        if human_content is not None:
            # 正文单独挂在 LogRecord 上，避免破坏现有 JSONL 的脱敏审计契约。
            extra["human_content"] = redact(human_content)[:12000]
        _logger.log(level, message, extra=extra)
    except Exception:
        # 日志是诊断旁路；文件写入错误由日志处理器报告，不能重试业务操作。
        pass


def error_fields(exc: BaseException) -> dict:
    """保留错误类型、错误码、HTTP 状态与调用位置，避免异常原文泄露请求内容。"""
    causes = []
    cause, seen = exc, set()
    while cause is not None and id(cause) not in seen and len(causes) < 5:
        seen.add(id(cause))
        causes.append(type(cause).__name__)
        cause = cause.__cause__ or cause.__context__
    return {"error_type": type(exc).__name__, "error_code": getattr(exc, "code", type(exc).__name__),
            "provider_status": getattr(exc, "status_code", None), "causes": causes,
            "stack": [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                      for frame in traceback.extract_tb(exc.__traceback__)[-12:]]}


class ExecutionFormatter(logging.Formatter):
    """机器文件使用单行 JSON，人读输出使用中文标签和正文。"""

    def __init__(self, json_lines: bool = False, human_chat: bool = False):
        super().__init__()
        self.json_lines = json_lines
        self.human_chat = human_chat

    @staticmethod
    def _human_event(record: logging.LogRecord) -> bool:
        """判断是否为对话节点；HTTP/Uvicorn 访问日志不进入人读对话流。"""
        event = str(getattr(record, "execution_event", ""))
        return bool(getattr(record, "human_content", None) is not None
                    or event.startswith(("chat.", "openwebui.", "model.", "octop.")))

    def _human_format(self, record: logging.LogRecord, timestamp: str) -> str:
        """生成不带追踪 ID 的人读行，正文经过凭据脱敏并限制长度。"""
        event = str(getattr(record, "execution_event", ""))
        labels = {"chat.message.user": "用户", "chat.message.assistant": "AI",
                  "chat.message.error": "失败", "chat.tool": "工具",
                  "chat.progress": "进度", "chat.status": "状态",
                  "chat.message.question": "AI", "chat.message.followups": "AI",
                  "chat.run.failed": "失败", "chat.run.completed": "状态",
                  "openwebui.tool": "工具", "openwebui.progress": "进度",
                  "openwebui.failed": "失败", "openwebui.completed": "状态"}
        label = str(getattr(record, "human_label", None) or labels.get(event, "节点"))
        content = getattr(record, "human_content", None)
        if content is None:
            content = record.getMessage()
        # 日志保持一事件一行，换行用可读箭头表示，避免正文伪造日志头。
        content = redact(content)[:12000].replace("\\r", " ").replace("\\n", " ↵ ")
        return f"{timestamp[:19].replace('T', ' ')} [AI对话] {label}：{content}".rstrip()

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        if self.human_chat and self._human_event(record):
            return self._human_format(record, timestamp)
        fields = safe_fields({**_context.get(), **getattr(record, "execution_fields", {})})
        if record.exc_info and record.exc_info[1]:
            fields.update(error_fields(record.exc_info[1]))
        item = {"time": timestamp, "level": record.levelname, "logger": record.name,
                "pid": record.process, "thread": record.threadName,
                "event": getattr(record, "execution_event", "log"),
                "message": redact(record.getMessage())[:3000], **fields}
        if self.json_lines:
            return json.dumps(item, ensure_ascii=False, default=str)
        detail = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in fields.items())
        return f"{timestamp} {record.levelname} [{item['event']}] {item['message']} {detail}".rstrip()


class ChatLogFilter(logging.Filter):
    """将对话及其模型、工具、SQL、文件子调用集中到 AI 专用日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        fields = {**_context.get(), **getattr(record, "execution_fields", {})}
        event = str(getattr(record, "execution_event", ""))
        return bool(fields.get("conversation_id") or fields.get("chat_run_id")
                    or event.startswith(("chat.", "model.")))


class HumanChatLogFilter(logging.Filter):
    """只保留人读对话正文与节点，排除 HTTP、Uvicorn 和第三方访问噪声。"""

    def filter(self, record: logging.LogRecord) -> bool:
        event = str(getattr(record, "execution_event", ""))
        if event.startswith(("http.", "uvicorn.", "log")):
            return False
        return ExecutionFormatter._human_event(record)


def configure_logging(settings) -> dict:
    """配置控制台、机器审计日志和人读 AI 日志；不同进程分文件避免 Windows 轮转冲突。"""
    with _setup_lock:
        folder = Path(settings.LOG_DIR or (Path(settings.STORAGE_ROOT) / "logs")).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        identity = (str(folder), os.getpid(), settings.LOG_LEVEL, settings.LOG_MAX_BYTES, settings.LOG_BACKUP_COUNT)
        root = logging.getLogger()
        if getattr(root, "_execution_log_identity", None) == identity:
            return root._execution_log_paths
        for handler in list(root.handlers):
            if getattr(handler, "_execution_owned", False):
                root.removeHandler(handler)
                handler.close()
        level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        root.setLevel(level)
        paths = {"backend": str(folder / f"backend-{os.getpid()}.jsonl"),
                 "ai_chat": str(folder / f"ai-chat-{os.getpid()}.jsonl"),
                 "ai_chat_readable": str(folder / f"ai-chat-readable-{os.getpid()}.log")}
        console = logging.StreamHandler()
        console.setFormatter(ExecutionFormatter(human_chat=True))
        handlers = [console]
        for kind, path in paths.items():
            handler = RotatingFileHandler(path, maxBytes=max(1024, settings.LOG_MAX_BYTES),
                                          backupCount=max(1, settings.LOG_BACKUP_COUNT),
                                          encoding="utf-8", errors="backslashreplace")
            if kind == "ai_chat_readable":
                handler.setFormatter(ExecutionFormatter(human_chat=True))
                handler.addFilter(HumanChatLogFilter())
            else:
                handler.setFormatter(ExecutionFormatter(json_lines=True))
            if kind == "ai_chat":
                handler.addFilter(ChatLogFilter())
            handlers.append(handler)
        for handler in handlers:
            handler._execution_owned = True
            handler.setLevel(level)
            root.addHandler(handler)
        # Uvicorn 默认自带控制台处理器；改为向根日志传播，确保只打印一次且访问日志也落盘。
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.propagate = True
        # SDK 的逐请求输出可能包含 URL 参数；节点日志已提供耗时与状态，库日志只保留告警。
        for name in ("httpx", "httpcore", "openai"):
            logging.getLogger(name).setLevel(logging.WARNING)
        root._execution_log_identity = identity
        root._execution_log_paths = paths
        log_event("logging.ready", "运行日志已启用", paths=paths,
                  max_bytes=settings.LOG_MAX_BYTES, backups=settings.LOG_BACKUP_COUNT)
        return paths


def _selected_fields(arguments: dict, selectors: dict) -> dict:
    """仅按声明读取参数标识，不枚举上下文对象、数据库对象或凭据配置。"""
    fields = {}
    for name, path in selectors.items():
        value = arguments
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
            if value is None:
                break
        if isinstance(value, (str, int, float, bool)):
            fields[name] = value
    return fields


def result_fields(result) -> dict:
    """记录结果类型、数量和可追踪标识，不展开结果正文。"""
    fields = {"result_type": type(result).__name__}
    for key in ("ok", "status", "error_code", "queryId", "runId", "truncated", "model", "provider", "finish_reason"):
        value = result.get(key) if isinstance(result, dict) else getattr(result, key, None)
        if isinstance(value, (str, int, float, bool)):
            fields[key] = value
    for key in ("data", "columns", "artifacts", "tool_calls"):
        value = result.get(key) if isinstance(result, dict) else getattr(result, key, None)
        if isinstance(value, (list, tuple)):
            fields[key + "_count"] = len(value)
    if isinstance(result, (list, tuple, bytes, str)):
        fields["result_size"] = len(result)
    return fields


def observe(event: str, label: str, *, fields: dict | None = None, status_from: str | None = None):
    """为同步操作成对记录开始与终态，并保留每次重试、嵌套操作的独立标识。"""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            selected = _selected_fields(arguments, fields or {})
            parent = _context.get().get("operation_id")
            with log_context(**selected, operation_id=uuid4().hex, parent_operation_id=parent):
                start = time.perf_counter()
                log_event(event + ".started", label + "：开始")
                try:
                    result = function(*args, **kwargs)
                except BaseException as exc:
                    status = "waiting" if type(exc).__name__ == "WorkflowPause" else (
                        "cancelled" if type(exc).__name__ in {"ChatCancelled", "CancelledError", "GeneratorExit"} else "failed")
                    log_event(event + "." + status, label + {"waiting": "：等待恢复", "cancelled": "：已取消", "failed": "：失败"}[status],
                              level=logging.ERROR if status == "failed" else logging.INFO,
                              duration_ms=round((time.perf_counter() - start) * 1000, 2), **error_fields(exc))
                    raise
                details = result_fields(result)
                if status_from:
                    details.update(_selected_fields(arguments, {"status": status_from}))
                status = details.get("status")
                outcome = "failed" if details.get("ok") is False or status == "failed" else (
                    status if status in {"waiting", "running", "cancelled"} else "completed")
                ending = {"failed": "：返回失败结果", "waiting": "：等待恢复", "running": "：已受理，仍在执行",
                          "cancelled": "：已取消", "completed": "：完成"}[outcome]
                log_event(event + "." + outcome, label + ending,
                          level=logging.WARNING if outcome == "failed" else logging.INFO,
                          duration_ms=round((time.perf_counter() - start) * 1000, 2), **details)
                return result
        return wrapped
    return decorate


class RequestLoggingMiddleware:
    """原生 ASGI 请求日志，不读取请求体、不缓存 SSE，并记录真正的响应结束或断线。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        match = re.match(r"/api/chat/conversations/([^/]+)", path)
        fields = {"request_id": uuid4().hex, "method": scope.get("method"), "path": path}
        if match:
            fields["conversation_id"] = match.group(1)
        started, status, sent, disconnected = time.perf_counter(), None, 0, False
        with log_context(**fields):
            log_event("http.request.started", "收到接口请求")

            async def logged_receive():
                nonlocal disconnected
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                    log_event("http.client.disconnected", "客户端连接已断开，后台任务状态单独记录")
                return message

            async def logged_send(message):
                nonlocal status, sent
                if message["type"] == "http.response.start":
                    status = message["status"]
                    message = {**message, "headers": [*message.get("headers", []),
                               (b"x-request-id", fields["request_id"].encode("ascii"))]}
                    log_event("http.response.started", "开始发送接口响应", status=status)
                elif message["type"] == "http.response.body":
                    sent += len(message.get("body", b""))
                await send(message)

            try:
                await self.app(scope, logged_receive, logged_send)
            except BaseException as exc:
                log_event("http.request.failed", "接口执行异常", level=logging.ERROR, **error_fields(exc))
                raise
            finally:
                log_event("http.request.finished", "接口连接结束", status=status, response_bytes=sent,
                          disconnected=disconnected, duration_ms=round((time.perf_counter() - started) * 1000, 2))
