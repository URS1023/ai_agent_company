"""聊天单测使用的原生协议桩；保留真实运行时、路由和任务持久化，禁止访问 8012。"""
from __future__ import annotations

from concurrent.futures import Future
import json
from queue import Queue
from types import SimpleNamespace

import pytest

from app.modules.openwebui import runtime as native_runtime
from app.modules.openwebui.client import OpenWebUIClient
from app.providers.tool.base import ToolSpec


@pytest.fixture(autouse=True)
def block_live_native(monkeypatch):
    """错误地沿用旧 agent mock 时立即失败，不能碰到用户正在运行的服务或密钥文件。"""
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("隔离聊天测试必须替换 OpenWebUIClient，禁止访问真实 8012")

    monkeypatch.setattr(OpenWebUIClient, "__init__", blocked)
    monkeypatch.setattr(OpenWebUIClient, "connect", blocked)
    yield
    assert not attempts, "测试错误进入了真实 Open WebUI 客户端"


@pytest.fixture
def scripted_native(monkeypatch, block_live_native):
    """用事件队列和 Future 模拟原生调用协议；所有终态判定仍执行正式 run_chat。"""
    def configure(steps, final_reply="本次工具结果已保存。"):
        controller = SimpleNamespace(steps=steps, final_reply=final_reply, submissions=[], calls=[], clients=[])

        class Client:
            def __init__(self, workspace_id, user_id):
                self.events = Queue()
                self.message_id = None
                self.history = {"messages": {}, "currentId": None}
                self.count = 0
                controller.clients.append(self)

            def connect(self):
                """协议桩不创建 HTTP 或 Socket.IO 连接。"""

            def create_chat(self, title, history):
                return "isolated-native-chat"

            def request(self, method, path, **kwargs):
                assert method == "GET" and path.startswith("/api/v1/chats/"), (method, path)
                return {"chat": {"history": self.history}, "tasks": []}

            def submit(self, **kwargs):
                self.count += 1
                assert self.count <= 8, "运行时不应无限提交无进展补交"
                self.message_id = kwargs["message_id"]
                self.history["currentId"] = self.message_id
                controller.submissions.append(kwargs)
                output = []
                pending = list(controller.steps) if self.count == 1 else []

                def next_step():
                    if not pending:
                        text = controller.final_reply
                        output.append({"type": "message", "role": "assistant", "status": "completed",
                                       "content": [{"type": "output_text", "text": text}]})
                        saved = {"done": True, "content": text, "output": list(output)}
                        self.history["messages"][self.message_id] = saved
                        self.events.put(("event", {"type": "chat:completion", "data": saved,
                                                  "_native_message_id": self.message_id}, None))
                        return
                    step = pending.pop(0)
                    identity = f"{self.message_id}-call-{len(output)}"
                    output.append({"type": "function_call", "call_id": identity, "name": step["name"],
                                   "arguments": json.dumps(step.get("args") or {}), "status": "completed"})
                    future = Future()

                    def completed(done):
                        output.append({"type": "function_call_output", "call_id": identity, "status": "completed",
                                       "output": [{"type": "input_text", "text": json.dumps(done.result(), ensure_ascii=False)}]})
                        next_step()

                    future.add_done_callback(completed)
                    self.events.put(("callback", {"type": "execute:tool", "_native_message_id": self.message_id,
                        "data": {"id": identity, "name": step["name"], "params": step.get("args") or {}}}, future))

                next_step()

            def stop(self):
                """协议桩记录结束时无需停止真实后台任务。"""

            def close(self):
                """协议桩没有网络或后台线程需要关闭。"""

        def execute(gateway, workspace_id, name, args, context):
            controller.calls.append(name)
            step = next(item for item in controller.steps if item["name"] == name)
            outcome = step["result"]
            return outcome(context) if callable(outcome) else outcome

        monkeypatch.setattr(native_runtime, "OpenWebUIClient", Client)
        monkeypatch.setattr(native_runtime, "sync_skills", lambda *args, **kwargs: [])
        monkeypatch.setattr(native_runtime.ToolGateway, "list_available", lambda *args, **kwargs: [
            ToolSpec(name=item["name"], description="隔离工具", provider="builtin", input_schema={"type": "object"})
            for item in controller.steps])
        monkeypatch.setattr(native_runtime.ToolGateway, "execute", execute)
        return controller

    return configure
