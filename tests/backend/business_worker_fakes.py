"""只替换模型配置的 DSH Worker 测试入口，业务工具、事件与结算使用正式实现。"""
import base64
import json
from types import SimpleNamespace

import pytest

from app.business_worker import chat, tools, transport


def request(headers, path, data=None, method="GET"):
    """构建宿主发给私有 Worker 的请求信封，不发网络请求。"""
    return {"method": method, "path": path, "query_string": "",
            "headers": [[key, value] for key, value in {**headers, "content-type": "application/json"}.items()],
            "body_base64": base64.b64encode(json.dumps(data).encode()).decode() if data is not None else ""}


@pytest.fixture
def isolated_dsh_worker(monkeypatch):
    """模型配置使用本机不可连接桩；这些测试不运行模型或旧 OpenWebUI 客户端。"""
    from app.providers import model
    monkeypatch.setattr(transport, "_rpc", None)
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "model": "isolated-business", "base_url": "http://127.0.0.1:12349/v1", "api_key": "fixture",
        "capabilities": {"runtime_options": {"thinking": "disabled"}}})

    def prepare(headers, conversation_id, content, **extra):
        prepared = chat.prepare(request(headers, f"/api/chat/conversations/{conversation_id}/send",
                                        {"content": content, **extra}, "POST"))
        chat.append_event(prepared["run_id"], "run.started", {})
        return prepared

    def history(headers, conversation_id):
        return chat.history(request(headers, f"/api/chat/conversations/{conversation_id}"), conversation_id)

    return SimpleNamespace(prepare=prepare, execute=tools.execute, settle=chat.settle,
                           append_event=chat.append_event, history=history)
