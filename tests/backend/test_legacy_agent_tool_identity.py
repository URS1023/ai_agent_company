"""旧 Agent 工具调用身份回归：真实隔离 SQLite 唯一约束，模型与 HTTP 传输使用内存桩。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import AuthIdentity, ToolCall, Workspace
from app.modules.runtime import agent, service as runtime
from app.providers.tool import http_provider
from app.providers.tool.base import ToolSpec
from app.providers.tool.side_effects import idempotency_key


@pytest.mark.parametrize("values", [(1, 2), (1, 1)], ids=["changed-arguments", "same-arguments"])
def test_same_native_call_id_on_later_turn_is_a_distinct_persisted_call(
    client, administrator, db, monkeypatch, values,
):
    """跨轮复用原生 ID 不应撞唯一键；每次实际调用的持久键完整传到 HTTP provider。"""
    spec = ToolSpec(name="fixture.inspect", description="隔离测试检查工具", provider="http",
                    input_schema={"type": "object", "properties": {"value": {"type": "integer"}},
                                  "required": ["value"], "additionalProperties": False})
    workspace_id = administrator["X-Workspace-Id"]
    user_id = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin")).user_id
    conversation_id = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    run = runtime.create_run(db, workspace_id=workspace_id, conversation_id=conversation_id,
                             user_id=user_id, context_snapshot={})
    db.commit()
    model_turns = []
    calls_at_dispatch = []
    transport_headers = []

    def stream(_db, _run, messages, _tools, *_args):
        model_turns.append(messages)
        turn = len(model_turns) - 1
        if turn < len(values):
            yield "final", {"content": "", "tool_calls": [{
                "id": "native-call-reused", "type": "function", "function": {
                    "name": spec.name, "arguments": json.dumps({"value": values[turn]}),
                },
            }]}
        else:
            yield "final", {"content": "两次检查均已完成。", "tool_calls": []}

    def dispatch(_gateway, _workspace, _name, arguments, context):
        persisted = db.scalar(select(ToolCall).where(ToolCall.idempotency_key == context["idempotency_key"]))
        assert persisted is not None, "发出请求前必须已经保存调用键"
        calls_at_dispatch.append((persisted.id, persisted.idempotency_key, dict(arguments)))
        # 同一持久调用进入 provider 时只能沿用既有键，不能根据参数另造一个键。
        assert idempotency_key(context, spec, arguments) == persisted.idempotency_key
        return http_provider.invoke(spec, arguments, {**context, "connector_spec": {
            "base_url": "https://fixture.invalid", "path": "/inspect", "method": "POST",
            "body_mapping": "json", "headers": {}, "auth": {"type": "none"},
        }})

    def request_stub(config, _body, _credentials, **_kwargs):
        transport_headers.append(config.headers["Idempotency-Key"])
        return {"checked": True}, {"httpStatus": 200}

    monkeypatch.setattr(agent.ToolGateway, "list_available", lambda *_args, **_kwargs: [spec])
    monkeypatch.setattr(agent.ToolGateway, "execute", dispatch)
    monkeypatch.setattr(agent, "_llm_stream", stream)
    monkeypatch.setattr(agent, "_generate_followups", lambda *_args, **_kwargs: ([], ""))
    monkeypatch.setattr(http_provider, "request_json", request_stub)

    events = list(agent.agent_run(db, run, db.get(Workspace, workspace_id), None,
                                 content="依次执行两次检查并给出结果", events=[]))
    db.expire_all()
    assert run.status == "completed", run.error_summary
    persisted_calls = db.scalars(select(ToolCall).where(ToolCall.run_id == run.id)).all()
    assert len(persisted_calls) == 2
    assert all(call.status == "completed" for call in persisted_calls)
    assert len({call.idempotency_key for call in persisted_calls}) == 2
    assert len(model_turns) == 3
    assert [entry[2]["value"] for entry in calls_at_dispatch] == list(values)
    assert transport_headers == [entry[1] for entry in calls_at_dispatch]
    assert {entry[0] for entry in calls_at_dispatch} == {call.id for call in persisted_calls}
    assert [event["payload"]["iteration"] for event in events if event["type"] == "tool.started"] == [0, 1]
    assert [event["payload"]["tool_call_id"] for event in events if event["type"] == "tool.started"] == [
        "native-call-reused", "native-call-reused",
    ]
