"""通过原生 HTTP、Socket.IO 协议连接 Open WebUI，执行决策由上游负责。"""
from __future__ import annotations

from concurrent.futures import Future
import hashlib
import os
from pathlib import Path
import queue
import threading
from typing import Callable

import httpx
import socketio

from ...errors import AppError

ROOT = Path(__file__).resolve().parents[4]


class BackendUnavailable(AppError):
    """内部后端不可达或协议执行失败。"""

    status_code = 503
    code = "openwebui_unavailable"


class OpenWebUIClient:
    """每次运行独占连接，身份由服务端认证信息生成，禁止接受前端身份头。"""

    HEARTBEAT_INTERVAL_SECONDS = 20

    def __init__(self, workspace_id: str, user_id: str, *, base_url: str | None = None,
                 key_path: Path | None = None):
        self.base_url = base_url or f"http://127.0.0.1:{os.environ.get('OPENWEBUI_PORT', '8012')}"
        key_path = key_path or Path(os.environ.get("OPENWEBUI_DATA_DIR", ROOT / "storage/openwebui")) / "bridge.key"
        key = key_path.read_text(encoding="utf-8").strip()
        self.identity = hashlib.sha256(f"{workspace_id}:{user_id}".encode()).hexdigest()
        self.headers = {"X-Enterprise-Bridge-Key": key}
        self.http = httpx.Client(base_url=self.base_url, headers=self.headers,
                                 timeout=httpx.Timeout(30, connect=10), trust_env=False)
        self.socket = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        self.events: queue.Queue = queue.Queue()
        self.pending: list[Future] = []
        self.closed = threading.Event()
        self.emit_lock = threading.Lock()
        self.heartbeat_thread: threading.Thread | None = None
        self.chat_id: str | None = None
        self.message_id: str | None = None
        self.session_token: str | None = None
        self._events_registered = False

    def request(self, method: str, path: str, **kwargs):
        """HTTP 错误仅暴露状态码，避免泄露响应中的连接信息。"""
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendUnavailable("Open WebUI 内部连接不可用") from exc
        if response.is_error:
            raise BackendUnavailable(f"Open WebUI 接口 {path.split('?')[0]} 返回 {response.status_code}")
        return response.json()

    def connect(self):
        """按工作区和用户映射为独立的原生用户，并建立经过认证的事件连接。"""
        session = self.request("POST", "/api/v1/auths/signin",
            headers={"X-Enterprise-Email": self.identity + "@enterprise.invalid", "X-Enterprise-Role": "user"},
            json={"email": self.identity + "@enterprise.invalid", "password": "trusted-header"})
        self.session_token = session["token"]
        self.http.headers["Authorization"] = "Bearer " + self.session_token

        if not self._events_registered:
            @self.socket.on("events")
            def on_event(event):
                if self.closed.is_set():
                    return {"error": "运行已结束"}
                if event.get("chat_id") != self.chat_id or event.get("message_id") != self.message_id:
                    return {"error": "事件不属于本次运行"}
                # 入队后仍保留消息归属，防止切换补交任务时消费迟到的上一轮事件。
                data = {**(event.get("data") or {}), "_native_message_id": event.get("message_id")}
                kind = data.get("type")
                if kind in {"execute:tool", "request:chat:completion"}:
                    future = Future()
                    self.pending.append(future)
                    self.events.put(("callback", data, future))
                    return future.result()
                self.events.put(("event", data, None))

            @self.socket.on("disconnect")
            def on_disconnect(*args):
                if not self.closed.is_set():
                    self.events.put(("disconnect", {}, None))
            self._events_registered = True

        self.socket.connect(self.base_url, headers=self.headers, auth={"token": session["token"]},
                            socketio_path="ws/socket.io", transports=["websocket"], wait_timeout=10)
        self._start_heartbeat()

    def _start_heartbeat(self):
        """只保留一个心跳线程；重连时不重复注册定时发送器。"""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            return
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True,
                                                 name="openwebui-heartbeat-" + self.identity[:12])
        self.heartbeat_thread.start()

    def reconnect(self):
        """恢复同一原生会话连接，不新建聊天、不重放已经完成的工具。"""
        if self.closed.is_set():
            return
        if self.socket.connected:
            self._start_heartbeat()
            return
        # 复用尚未过期的内部身份；过期时重新登录但仍绑定同一工作区用户。
        if not self.session_token:
            self.connect()
            return
        try:
            self.socket.connect(self.base_url, headers=self.headers,
                                auth={"token": self.session_token},
                                socketio_path="ws/socket.io", transports=["websocket"], wait_timeout=10)
            self._start_heartbeat()
        except Exception:
            self.session_token = None
            self.connect()

    def _emit(self, channel: str, data: dict):
        """心跳与模型流共用发送锁，防止多线程交错发送 Socket.IO 包。"""
        with self.emit_lock:
            if self.closed.is_set():
                return
            self.socket.emit(channel, data)

    def _heartbeat_loop(self):
        """独立维持原生应用会话；底层 ping 不会更新 Open WebUI 会话池。"""
        while not self.closed.is_set():
            try:
                self._emit("heartbeat", {})
            except Exception:
                if not self.closed.is_set():
                    self.events.put(("disconnect", {"reason": "heartbeat_failed"}, None))
                return
            if self.closed.wait(self.HEARTBEAT_INTERVAL_SECONDS):
                return

    def create_chat(self, title: str, history: list[dict]) -> str:
        """首次迁移旧消息，后续上下文始终由上游读取自己的结构化历史。"""
        messages = {}
        parent = None
        for message in history:
            item = {**message, "parentId": parent, "childrenIds": [], "done": True}
            messages[item["id"]] = item
            if parent:
                messages[parent]["childrenIds"].append(item["id"])
            parent = item["id"]
        result = self.request("POST", "/api/v1/chats/new", json={"chat": {
            "title": title, "history": {"messages": messages, "currentId": parent}}})
        return result["id"]

    def submit(self, *, chat_id: str, message_id: str, user_message: dict, model: str,
               specs: list[dict], system: str, skill_ids: list[str] | None = None):
        """提交原生后台任务；显式传递工具服务器，启用上游的工具循环。"""
        self.chat_id, self.message_id = chat_id, message_id
        return self.request("POST", "/api/chat/completions", json={
            "model": model, "model_item": {"id": model, "name": model, "direct": True,
                "info": {"meta": {"capabilities": {"builtin_tools": True}, "builtinTools": {
                    "tasks": True, "chats": True, "knowledge": False, "memory": False,
                    "subagents": False, "notes": False, "channels": False, "automations": False,
                    "calendar": False, "web_search": False, "image_generation": False,
                    "code_interpreter": False}}}},
            "stream": True, "chat_id": chat_id, "id": message_id,
            "session_id": self.socket.get_sid(), "parent_id": user_message.get("parentId"),
            "user_message": user_message,
            "messages": [{"role": "system", "content": system + "\n" + " ".join(
                "<$" + skill_id + "|业务技能>" for skill_id in (skill_ids or []))},
                         {"role": "user", "content": user_message["content"]}],
            "params": {"function_calling": "native"}, "skill_ids": skill_ids or [],
            "tool_servers": [{"id": "enterprise-business", "specs": specs}],
            "background_tasks": {"title_generation": False, "tags_generation": False,
                                 "follow_up_generation": False},
        })

    def stream_model(self, data: dict, generate: Callable, *, incremental: bool = False):
        """单次推理传回上游；后续轮次和工具选择始终由 Open WebUI 发起。"""
        form = data["form_data"]
        channel = data.get("channel")
        streamed = False

        def on_delta(text):
            """将模型增量送入原生事件队列，不直接绕过上游写入页面。"""
            nonlocal streamed
            if form.get("stream") and text:
                streamed = True
                self._emit(channel, {"id": self.message_id, "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})

        result = generate(form, on_delta) if incremental else generate(form)
        message = {"role": "assistant", "content": result.get("content") or ""}
        if result.get("tool_calls"):
            message["tool_calls"] = result["tool_calls"]
        if not form.get("stream"):
            return {"choices": [{"message": message, "finish_reason": result.get("finish_reason", "stop")}],
                    "usage": result.get("usage", {})}
        delta = dict(message)
        if streamed:
            delta.pop("content", None)
        if delta.get("tool_calls"):
            delta["tool_calls"] = [{"index": i, **tool} for i, tool in enumerate(delta["tool_calls"])]
        self._emit(channel, {"id": self.message_id, "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        self._emit(channel, {"choices": [{"index": 0, "delta": {},
            "finish_reason": result.get("finish_reason", "stop")}], "usage": result.get("usage", {})})
        self._emit(channel, {"done": True})
        return {"status": True}

    def stop(self):
        """按原生聊天归属取消任务，不使用需要管理员权限的全局任务接口。"""
        if self.chat_id:
            self.request("POST", f"/api/tasks/chat/{self.chat_id}/stop")

    def close(self):
        """释放待回调和网络连接，避免断线线程永远等待。"""
        self.closed.set()
        if self.heartbeat_thread and self.heartbeat_thread is not threading.current_thread():
            self.heartbeat_thread.join(timeout=1)
        for future in self.pending:
            if not future.done():
                future.set_result({"error": "本次运行已结束"})
        if self.socket.connected:
            self.socket.disconnect()
        self.http.close()
