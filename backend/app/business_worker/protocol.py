"""双向 JSON-RPC：读线程持续接收，业务线程可等待宿主模型响应。"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError

from app.common.json_numbers import preserve_json_integers


class RemoteError(Exception):
    """宿主返回的结构化业务错误。"""
    def __init__(self, error):
        super().__init__(error.get("message") or "宿主请求失败")
        self.code = (error.get("data") or {}).get("code") or str(error.get("code", "host_error"))
        self.status_code = (error.get("data") or {}).get("status", 502)


class JsonRpc:
    """主线程只读协议；取消直接受理，其余方法交给独立执行器。"""
    def __init__(self, source, sink, *, workers=8):
        self.source, self.sink = source, sink
        self.lock = threading.RLock()
        self.pending = {}
        self.counter = 0
        self.closed = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="business-api")
        self.tool_executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="business-tool")
        self.control_executor = ThreadPoolExecutor(max_workers=max(2, workers), thread_name_prefix="business-control")
        self.handler = None

    def write(self, packet):
        """整包输出，防止并发响应混入同一行。"""
        with self.lock:
            # 宿主使用 JSON.parse；输出前保护大整数，避免工具回执被舍入后再次落库。
            self.sink.write(json.dumps(preserve_json_integers(packet), ensure_ascii=False, allow_nan=False, default=str) + "\n")
            self.sink.flush()

    def notify(self, method, params):
        self.write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params, *, timeout=None, cancel=None):
        """反向请求等待期间主读线程仍能处理回复与取消。"""
        with self.lock:
            self.counter += 1
            identity = f"worker-{self.counter}"
            future = self.pending[identity] = Future()
        self.write({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})
        import time
        deadline = time.monotonic() + timeout if timeout else None
        try:
            while True:
                if cancel and cancel.is_set():
                    self.notify("model.cancel", {"request_id": identity, "run_id": params.get("run_id")})
                    from app.modules.runtime.task_control import ChatCancelled
                    raise ChatCancelled("业务模型调用已取消")
                if self.closed.is_set():
                    raise RemoteError({"message": "宿主连接已关闭"})
                remaining = deadline - time.monotonic() if deadline else None
                if remaining is not None and remaining <= 0:
                    self.notify("model.cancel", {"request_id": identity, "run_id": params.get("run_id")})
                    raise TimeoutError("宿主模型调用超时")
                try:
                    return future.result(timeout=min(.1, remaining) if remaining else .1)
                except TimeoutError:
                    if future.done():
                        raise
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def dispatch(self, packet):
        """业务异常保留稳定代码；未知异常不把堆栈放入协议。"""
        result = None
        try:
            result = self.handler(packet["method"], packet.get("params") or {})
            if "id" in packet:
                self.write({"jsonrpc": "2.0", "id": packet["id"], "result": result})
        except Exception as exc:
            logging.exception("业务操作失败：%s", packet.get("method"))
            if "id" in packet:
                self.write({"jsonrpc": "2.0", "id": packet["id"], "error": {
                    "code": -32000, "message": getattr(exc, "message", str(exc)),
                    "data": {"code": getattr(exc, "code", "worker_error"),
                             "status": getattr(exc, "status_code", 500), **getattr(exc, "extra", {})}}})
        finally:
            if (packet.get("method") == "tool.execute" and isinstance(result, dict)
                    and result.get("receipt_protocol") == 1 and result.get("state") == "committed"):
                # 成功回复先发送；通知仅带身份。任一信号丢失可查询回执，两者都丢失仍需显式恢复。
                try:
                    self.notify("tool.receipt.committed", {"identity": result["receipt"]["identity"]})
                except Exception:
                    logging.exception("工具回执已提交，但完成通知发送失败")

    def serve(self, handler):
        """持续读取，直到宿主关闭输入；响应包不进入业务执行队列。"""
        self.handler = handler
        try:
            while not self.closed.is_set():
                # 显式按行读取，不能依赖 Windows 管道文本迭代器的预读行为。
                line = self.source.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    packet = json.loads(line)
                    if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0":
                        raise ValueError("无效 JSON-RPC 包")
                    if "method" not in packet:
                        with self.lock:
                            future = self.pending.get(str(packet.get("id")))
                        if future and not future.done():
                            if "error" in packet:
                                future.set_exception(RemoteError(packet["error"]))
                            else:
                                future.set_result(packet.get("result"))
                    elif packet["method"] in {"run.cancel", "shutdown"}:
                        self.dispatch(packet)
                        if packet["method"] == "shutdown":
                            break
                    else:
                        # API 可等待原生会话，工具可等待原生模型；短控制/台账回调
                        # 必须有独立线程，避免长操作占满后相互等待形成死锁。
                        executor = (self.executor if packet["method"] == "api.dispatch" else
                                    self.tool_executor if packet["method"] == "tool.execute" else self.control_executor)
                        executor.submit(self.dispatch, packet)
                except (ValueError, TypeError):
                    self.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "无效协议消息"}})
        finally:
            self.closed.set()
            with self.lock:
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(RemoteError({"message": "宿主连接已关闭"}))
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.tool_executor.shutdown(wait=True, cancel_futures=True)
            self.control_executor.shutdown(wait=True, cancel_futures=True)
