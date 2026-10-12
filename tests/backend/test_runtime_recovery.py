"""历史 Agent 的正文收尾与参数修复合同，不冒充当前 DSH 的浏览器验收。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import Message, Run, ToolCall
from app.modules.runtime import agent
from app.providers.tool.base import ToolResult, ToolSpec
from app.providers.tool.validator import validate_arguments


@pytest.fixture(autouse=True)
def legacy_runtime_fixture(monkeypatch):
    """显式运行被替换模型流的历史实现，禁止转到真实 Open WebUI 服务。"""
    from app.modules.runtime import engine
    monkeypatch.setattr(engine, "run_chat", agent.agent_run)
    monkeypatch.setattr(agent, "_generate_followups", lambda *_args, **_kwargs: ([], ""))


def _send(client, headers, content="请查看当前资料"):
    conversation = client.post(
        "/api/chat/conversations", headers=headers, json={"title": "用户随意起的标题"},
    ).json()
    sent = client.post(
        f"/api/chat/conversations/{conversation['id']}/send",
        headers=headers, json={"content": content},
    )
    assert sent.status_code == 200, sent.text
    return conversation["id"]


def test_final_payload_without_deltas_is_persisted_and_streamed(client, administrator, db, monkeypatch):
    def stream(*_args, **_kwargs):
        yield "final", {"content": "这里是完整的回答。", "tool_calls": []}

    monkeypatch.setattr(agent, "_llm_stream", stream)
    conversation_id = _send(client, administrator)
    message = db.scalar(select(Message).where(
        Message.conversation_id == conversation_id, Message.role == "assistant",
    ))
    assert message.content == "这里是完整的回答。"
    assert message.status == "completed"


@pytest.mark.parametrize("recovers", [True, False])
def test_empty_terminal_response_retries_once_then_reports_failure(
    client, administrator, db, monkeypatch, recovers,
):
    calls = []

    def stream(_db, _run, messages, tools, *_args):
        calls.append({"messages": messages, "tools": tools})
        yield "final", {
            "content": "根据已读取资料，设备温度正常。" if recovers and len(calls) == 2 else "",
            "tool_calls": [],
        }

    monkeypatch.setattr(agent, "_llm_stream", stream)
    conversation_id = _send(client, administrator)
    message = db.scalar(select(Message).where(
        Message.conversation_id == conversation_id, Message.role == "assistant",
    ))
    run = db.scalar(select(Run).where(Run.conversation_id == conversation_id))
    assert len(calls) == 2
    assert calls[1]["tools"] is None
    assert message.content.strip()
    assert run.status == ("completed" if recovers else "failed")
    if not recovers:
        assert run.error_code == "empty_model_response"


@pytest.mark.parametrize("raw_args", ['{"type":"text"}', '{"broken":', '[]'])
def test_invalid_tool_arguments_return_schema_guidance_and_allow_corrected_call(
    client, administrator, db, monkeypatch, raw_args,
):
    spec = ToolSpec(
        name="inspect_example", description="Inspect", provider="builtin",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    dispatched = []
    responses = []

    def execute(self, _workspace_id, _name, raw, _context):
        validated = validate_arguments(raw, spec.input_schema)
        dispatched.append(validated)
        return ToolResult(status="success", data={"answer": "检查完成"})

    monkeypatch.setattr(agent.ToolGateway, "list_available", lambda *args, **kwargs: [spec])
    monkeypatch.setattr(agent.ToolGateway, "execute", execute)

    def stream(_db, _run, messages, _tools, *_args):
        responses.append(messages)
        attempt = len(responses)
        if attempt <= 2:
            yield "final", {"content": "", "tool_calls": [{
                "id": f"call_{attempt}", "type": "function",
                "function": {"name": spec.name, "arguments": raw_args if attempt == 1 else "{}"},
            }]}
        else:
            yield "final", {"content": "检查完成。", "tool_calls": []}

    monkeypatch.setattr(agent, "_llm_stream", stream)
    conversation_id = _send(client, administrator)
    first_feedback = json.loads(next(
        message["content"] for message in responses[1] if message["role"] == "tool"
    ))
    assert first_feedback["error_code"] == "schema_validation_error"
    assert first_feedback["retryable"] is True
    assert first_feedback["input_schema"] == spec.input_schema
    assert dispatched == [{}], "Only the corrected arguments may reach the provider"
    failed_call = db.scalar(select(ToolCall).join(Run, Run.id == ToolCall.run_id).where(
        Run.conversation_id == conversation_id, ToolCall.status == "failed",
    ))
    assert failed_call.error_code == "schema_validation_error"


def test_system_identity_is_product_and_workspace_not_conversation_title(client, administrator, monkeypatch):
    observed = []

    def stream(_db, _run, messages, _tools, *_args):
        observed.append(messages[0]["content"])
        yield "delta", "智慧云脑帮助你管理知识、分析数据和完成业务协作。"
        yield "final", {"content": "智慧云脑帮助你管理知识、分析数据和完成业务协作。", "tool_calls": []}

    monkeypatch.setattr(agent, "_llm_stream", stream)
    _send(client, administrator, "你好，请用一句话介绍智慧云脑")
    assert "智慧云脑" in observed[0]
    assert "用户随意起的标题" not in observed[0]
