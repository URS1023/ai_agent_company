"""Text mentioning creation must never synthesize a tool invocation.

中文说明：只是"提到创建"的文本不得被合成成真实工具调用：讨论大屏不创建、创建请求在模型不可用时
不能绕过（要如实失败）、总超时要在 SSE 与持久化运行记录中一致体现。
隔离方式：monkeypatch + client 夹具（内存 SQLite），不连业务库与外部模型。
"""
import pytest
from sqlalchemy import select

from app.errors import ModelError
from app.models import ToolCall
from app.modules.runtime import agent


@pytest.mark.parametrize("question", [
    "怎么生成大屏？", "不要生成大屏", "大屏生成失败的原因", "大屏生成要多久",
    "数字看板的制作流程", "我想生成大屏，但先看看有哪些数据", "先了解大屏生成步骤",
])
def test_dashboard_discussion_reaches_model_without_creation(client, administrator, db, monkeypatch, question):
    calls = []
    def respond(*args, **kwargs):
        calls.append({tool['function']['name'] for tool in (args[3] or [])})
        yield "final", {"content": "已了解，这里仅说明使用步骤。", "tool_calls": [], "usage": {}}
    monkeypatch.setattr(agent, "_llm_stream", respond)
    monkeypatch.setattr(agent, "_generate_followups", lambda *args, **kwargs: ([], ""))
    conversation = client.post("/api/chat/conversations", headers=administrator, json={"title": "意图校验"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator,
                           json={"content": question})
    assert response.status_code == 200 and "仅说明使用步骤" in response.text, response.text
    assert "run.completed" in response.text and "run.failed" not in response.text
    assert len(calls) == 1 and calls[0] != {'create_business_dashboard'}
    assert not db.scalars(select(ToolCall)).all()
    detail = client.get(f"/api/chat/conversations/{conversation['id']}", headers=administrator).json()
    assert detail['last_run']['status'] == 'completed'
    assert any(message['role'] == 'assistant' and '仅说明使用步骤' in message['content']
               for message in detail['messages'])


def test_creation_request_cannot_bypass_unavailable_model(client, administrator, db, monkeypatch):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise ModelError("isolated model unavailable")
    monkeypatch.setattr(agent, "_llm_stream", unavailable)
    monkeypatch.setattr(agent, "_generate_followups", lambda *args, **kwargs: ([], ""))
    conversation = client.post("/api/chat/conversations", headers=administrator, json={"title": "意图校验"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator,
                           json={"content": "帮我生成大屏"})
    assert response.status_code == 200 and "run.failed" in response.text, response.text
    assert calls and not db.scalars(select(ToolCall)).all()


def test_total_chat_timeout_reaches_sse_and_persisted_run(client, administrator, db, monkeypatch):
    import time
    from app.models import Run
    from app.modules.runtime.task_control import current
    def expire(*args, **kwargs):
        control = current.get()
        assert control is not None
        control.deadline = time.monotonic() - 1
        control.check()
    monkeypatch.setattr(agent, "_llm_stream", expire)
    conversation = client.post("/api/chat/conversations", headers=administrator,
                               json={"title": "总时限错误可追溯"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator,
                           json={"content": "请说明系统功能"})
    assert response.status_code == 200, response.text
    assert "run.failed" in response.text and "chat_task_timeout" in response.text
    assert "总时间上限" in response.text and "保留" in response.text
    assert "event: done" in response.text and "run.completed" not in response.text
    run = db.scalar(select(Run).where(Run.conversation_id == conversation["id"]))
    assert run.status == "failed" and run.error_code == "chat_task_timeout"
    assert run.completed_at is not None
    detail = client.get(f"/api/chat/conversations/{conversation['id']}", headers=administrator).json()
    assert detail["last_run"]["error_code"] == "chat_task_timeout"
    assert any(message["role"] == "assistant" and "总时间上限" in message["content"] for message in detail["messages"])
