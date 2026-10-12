"""No database/network: exercise the actual agent with fake model/tool/session.

中文说明：用假的模型/工具/会话跑真实 agent 逻辑，验证停止（取消）语义不被吞掉。
隔离方式：SimpleNamespace + MagicMock，全程无数据库、无网络。
边界：取消要作为可识别状态向上传播，而不是变成普通错误或成功回答。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.errors import ValidationError
from app.modules.runtime import agent
from app.providers.tool.base import ToolResult, ToolSpec
from app.providers.tool.gateway import ToolGateway


def test_clarification_survives_gateway():
    result = ToolGateway._error_result("query_business_data", ValidationError(
        "需要补充业务口径", clarification="你想看收入还是回款？",
    ))
    assert result.metadata["retryable"] is False
    assert result.data["clarification"] in agent._business_query_stop_message(result)


@pytest.mark.parametrize("detail", [
    "分析 SQL 不允许调用函数 JSON_ARRAYAGG",
    "name 'select' is not defined",
    "模型没有返回合法 JSON",
    "同一幂等键已用于不同的请求参数",
])
def test_customer_messages_hide_internal_details(detail):
    result = ToolResult(status="failed", error_summary=detail, data={"debug": detail})
    for text in (agent._tool_summary(result), agent._business_query_stop_message(result),
                 agent._dashboard_creation_stop_message(result)):
        assert detail not in text
        assert "补充" not in text
    payload = agent._tool_payload_for_message(result)
    assert "data" not in payload
    assert result.error_summary == detail  # Full trace remains unchanged.


def test_customer_question_is_direct_and_business_only():
    question = "你想看全部设备，还是只看异常设备？"
    result = ToolGateway._error_result("query_business_data", ValidationError(
        "需要补充业务口径", clarification=question,
    ))
    assert agent._business_query_stop_message(result) == question
    result.data["clarification"] = "请提供 information_schema 中的表名"
    assert "information_schema" not in agent._business_query_stop_message(result)


def test_business_question_survives_technical_preface_without_repeating_in_status():
    question = "你是想查看销售订单的金额和客户信息，还是需要查看其他财务相关数据？"
    clarification = "当前元数据中没有找到收款、回款相关的表。" + question
    result = ToolGateway._error_result("query_business_data", ValidationError(
        "需要补充业务口径", clarification=clarification,
    ))
    assert agent._business_query_stop_message(result) == question
    assert agent._tool_summary(result) == "等待你的选择"
    assert agent._tool_payload_for_message(result) == {
        "status": "waiting_input", "summary": "等待你的选择",
    }
    assert result.data["clarification"] == clarification
    assert clarification in result.error_summary


@pytest.mark.parametrize("question", [
    "请提供收款的表名和字段，可以吗？",
    "你想查询 information_schema 还是 t_saleorder？",
    "需要补充业务口径，可以吗？",
    "你能联系管理员补充相关配置吗？",
])
def test_technical_or_configuration_questions_remain_private(question):
    result = ToolGateway._error_result("query_business_data", ValidationError(
        "需要补充业务口径", clarification=question,
    ))
    assert question not in agent._business_query_stop_message(result)
    assert agent._tool_summary(result) != "等待你的选择"


@pytest.mark.parametrize("error_code,metadata", [
    ("validation_error", {}),
    ("forbidden", {"requires_user_input": True}),
    ("tool_internal_error", {"requires_user_input": True}),
])
def test_question_requires_explicit_business_clarification_signal(error_code, metadata):
    question = "你想看收入还是回款？"
    result = ToolResult(status="failed", error_code=error_code, metadata=metadata,
                        data={"clarification": question})
    assert question not in agent._business_query_stop_message(result)


@pytest.mark.parametrize("failure", [
    ToolGateway._error_result("query_business_data", ValidationError(
        "需要补充业务口径", clarification="需要收入还是回款？",
    )),
    ToolResult(status="failed", error_summary="不允许跨数据库或访问其他 schema"),
    ToolResult(status="failed", error_summary="分析 SQL 不允许调用函数 CURRENT_SCHEMA"),
    ToolResult(status="failed", error_summary="查询超时"),
    RuntimeError("连接中断"),
])
@pytest.mark.parametrize("tool_name", ["query_business_data", "create_business_dashboard"])
def test_failed_query_stops_batch_and_next_model_turn(monkeypatch, failure, tool_name):
    monkeypatch.setattr(agent, "_generate_followups", lambda *args: (["请解释本次查询缺少哪些业务口径"], ""))
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []
    gateway = MagicMock()
    gateway.list_available.return_value = [ToolSpec(name=tool_name, description="query", provider="builtin")]
    gateway.execute.side_effect = [failure, ToolResult(status="success")]
    monkeypatch.setattr(agent, "ToolGateway", lambda *_: gateway)
    for name in ("_load_plugin_context", "_kb_descriptions", "_attached_file_summaries", "_conversation_messages", "_persist_result_artifacts"):
        monkeypatch.setattr(agent, name, lambda *args, **kwargs: [])
    for name in ("_record_model_call", "finish_tool_call", "fail_step", "complete_step", "record_citations", "finalize_run", "touch_conversation"):
        monkeypatch.setattr(agent, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "build_context", lambda **kwargs: {})
    monkeypatch.setattr(agent, "validate_arguments", lambda args, *_: args)
    monkeypatch.setattr(agent, "_prepare_messages_for_model", lambda messages: (messages, {"compacted": False}))
    for name in ("start_step", "record_tool_call", "create_message"):
        monkeypatch.setattr(agent, name, lambda *args, **kwargs: SimpleNamespace(id="fake"))
    calls = [{"id": str(i), "type": "function", "function": {
        "name": tool_name, "arguments": '{}',
    }} for i in range(3)]
    model = MagicMock(return_value=iter([("final", {"tool_calls": calls})]))
    monkeypatch.setattr(agent, "_llm_stream", model)
    run = SimpleNamespace(id="run", workspace_id="workspace", started_by_user_id="user", conversation_id="conversation", context_snapshot={}, plugin_version_id=None)
    events = list(agent.agent_run(db, run, SimpleNamespace(name="test"), None, content="查看财务数据", events=[]))
    assert gateway.execute.call_count == 1
    assert model.call_count == 1
    assert sum(event["type"] == "tool.started" for event in events) == 1
    conclusion = "".join(event.get("payload", {}).get("delta", "") for event in events)
    questions = [event['payload'] for event in events if event['type'] == 'message.question']
    assert conclusion.strip() or questions
    assert "CURRENT_SCHEMA" not in conclusion and "schema" not in conclusion
    if isinstance(failure, ToolResult) and agent._customer_business_question(failure):
        assert questions[0]['question'] == agent._customer_business_question(failure)
        assert questions[0]['options']
        completed = [event for event in events if event["type"] == "tool.completed"]
        assert completed[0]["payload"]["summary"] == "等待你的选择"
        assert completed[0]['payload']['status'] == 'waiting_input'
        assert not any(e['type'] == 'message.followups' and e['payload']['questions'] for e in events)
    needs_input = isinstance(failure, ToolResult) and bool(agent._customer_business_question(failure))
    assert events[-1]["payload"]["status"] == ('completed' if needs_input else 'failed')
    assert any(e['type'] == 'run.failed' for e in events) is not bool(needs_input)
