"""Task deadlines/cancellation keep their identity through SQL analysis cleanup.

中文说明：验证任务超时/取消在 SQL 分析清理链路上保持同一语义（不会退化成普通模型错误）。
验证行为：取消先落终态记录再向上传播；其它模型异常仍走脱敏的模型错误；业务查询网关透传停止；
对话工具的任务停止不重复调用模型。
隔离方式：monkeypatch + Mock 模型与会话，client 夹具提供 HTTP 入口，不连业务库。
"""
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.errors import ModelError
from app.models import AuthIdentity, Run, RunStep
from app.modules.analytics import queries
from app.modules.analytics.models import DataSourceQueryLease, QueryHistory
from app.modules.analytics.schemas import QueryInput
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout
from app.providers import model
from app.providers.tool.gateway import ToolGateway, build_context
from test_analytics import source


def query_context(db, administrator):
    user_id = db.scalar(select(AuthIdentity).where(
        AuthIdentity.username_normalized == "workspace-admin",
    )).user_id
    return auth_context_from_tool({
        "db": db, "workspace_id": administrator["X-Workspace-Id"], "user_id": user_id,
    })


@pytest.mark.parametrize("phase", ["model", "read"])
@pytest.mark.parametrize("failure_type, expected_code", [
    (ChatTaskTimeout, "chat_task_timeout"),
    (ChatCancelled, "chat_cancelled"),
])
def test_analytics_task_stop_propagates_after_persisting_terminal_records(
    client, administrator, source, db, monkeypatch, phase, failure_type, expected_code,
):
    failure = failure_type("internal stop detail that should not enter query history")
    generate = Mock(return_value={"content": '{"sql":"SELECT amount FROM sales"}', "usage": {}})
    read = Mock(side_effect=failure)
    if phase == "model":
        generate.side_effect = failure
    monkeypatch.setattr(model, "chat_for_run", generate)
    monkeypatch.setattr(queries, "execute_read", read)
    ctx = query_context(db, administrator)

    with pytest.raises(failure_type) as raised:
        queries.execute_query(ctx, QueryInput(
            datasourceId=source["id"], query="查看 sales 的 amount 明细", mode="natural_language",
        ))

    assert raised.value is failure  # The caller must receive the original task signal.
    assert generate.call_count == 1  # Task stops are never SQL-generation repairs.
    assert read.call_count == (1 if phase == "read" else 0)
    db.expire_all()
    record = db.scalars(select(QueryHistory)).one()
    run = db.get(Run, record.run_id)
    step = db.scalars(select(RunStep).where(RunStep.run_id == run.id)).one()
    assert record.status == run.status == step.status == "failed"
    assert run.error_code == step.error_code == expected_code
    assert run.completed_at is not None and step.completed_at is not None
    assert record.error_message == run.error_summary == step.error_summary
    assert "internal stop detail" not in record.error_message
    assert record.result_json == {}
    assert db.scalars(select(DataSourceQueryLease)).all() == []


@pytest.mark.parametrize("failure_type", [RuntimeError, TimeoutError])
def test_other_model_exceptions_still_use_redacted_model_error(
    client, administrator, source, db, monkeypatch, failure_type,
):
    failure = failure_type("private transport detail")
    generate = Mock(side_effect=failure)
    read = Mock(side_effect=AssertionError("SQL must not execute after a model failure"))
    monkeypatch.setattr(model, "chat_for_run", generate)
    monkeypatch.setattr(queries, "execute_read", read)

    with pytest.raises(ModelError) as raised:
        queries.execute_query(query_context(db, administrator), QueryInput(
            datasourceId=source["id"], query="查看 sales 的 amount 明细", mode="natural_language",
        ))

    assert raised.value.__cause__ is failure
    assert "private transport detail" not in raised.value.message
    assert generate.call_count == 1 and read.call_count == 0
    db.expire_all()
    record = db.scalars(select(QueryHistory)).one()
    assert record.status == "failed"
    assert db.get(Run, record.run_id).error_code == "model_error"
    assert db.scalars(select(DataSourceQueryLease)).all() == []


@pytest.mark.parametrize("phase", ["model", "read"])
@pytest.mark.parametrize("failure_type, expected_code", [
    (ChatTaskTimeout, "chat_task_timeout"),
    (ChatCancelled, "chat_cancelled"),
])
def test_business_query_gateway_preserves_stop_through_real_analysis_chain(
    client, administrator, source, db, monkeypatch, phase, failure_type, expected_code,
):
    from app.modules.analytics import execution

    failure = failure_type("task stopped")
    generate = Mock(return_value={"content": '{"sql":"SELECT amount FROM sales"}', "usage": {}})
    connection = Mock(side_effect=failure)
    if phase == "model":
        generate.side_effect = failure
    monkeypatch.setattr(model, "chat_for_run", generate)
    monkeypatch.setattr(execution, "readonly_connection", connection)
    actor = query_context(db, administrator)
    ctx = build_context(db=db, workspace_id=actor.workspace_id, user_id=actor.user_id,
                        run_id="", selected_datasource_ids=[source["id"]])

    with pytest.raises(failure_type) as raised:
        ToolGateway(db).execute(actor.workspace_id, "query_business_data", {
            "datasource_id": source["id"], "query": "查看 sales 的 amount 明细",
            "mode": "natural_language",
        }, ctx)

    assert raised.value is failure
    assert generate.call_count == 1
    assert connection.call_count == (1 if phase == "read" else 0)
    db.expire_all()
    record = db.scalars(select(QueryHistory)).one()
    assert record.status == "failed" and record.result_json == {}
    assert db.get(Run, record.run_id).error_code == expected_code
    assert db.scalars(select(DataSourceQueryLease)).all() == []


@pytest.mark.parametrize("phase, expected_code", [
    ("read", "tool_execution_error"), ("provider", "tool_internal_error"),
])
def test_business_query_gateway_keeps_non_control_error_categories(
    client, administrator, source, db, monkeypatch, phase, expected_code,
):
    from app.modules.analytics import execution
    from app.providers.tool import builtin_provider

    failure = RuntimeError("adapter unavailable")
    monkeypatch.setattr(model, "chat_for_run", Mock(return_value={
        "content": '{"sql":"SELECT amount FROM sales"}', "usage": {},
    }))
    failing_call = Mock(side_effect=failure)
    if phase == "read":
        monkeypatch.setattr(execution, "readonly_connection", failing_call)
    else:
        monkeypatch.setattr(builtin_provider, "invoke", failing_call)
    actor = query_context(db, administrator)
    ctx = build_context(db=db, workspace_id=actor.workspace_id, user_id=actor.user_id,
                        run_id="", selected_datasource_ids=[source["id"]])

    result = ToolGateway(db).execute(actor.workspace_id, "query_business_data", {
        "datasource_id": source["id"], "query": "查看 sales 的 amount 明细",
        "mode": "natural_language",
    }, ctx)

    assert result.status == "failed" and result.error_code == expected_code
    assert failing_call.call_count == 1
    if phase == "read":
        assert "adapter unavailable" not in result.error_summary
    assert db.scalars(select(DataSourceQueryLease)).all() == []


@pytest.mark.parametrize("failure_type, expected_code", [
    (ChatTaskTimeout, "chat_task_timeout"),
    (ChatCancelled, "chat_cancelled"),
])
def test_chat_tool_task_stop_keeps_terminal_cause_without_another_call(
    client, administrator, db, monkeypatch, failure_type, expected_code,
):
    from app.models import ToolCall
    from app.modules.runtime import agent
    from app.modules.runtime import engine

    # 本夹具验证被 mock 的旧 agent 取消协议，不能随部署默认引擎转向真实模型服务。
    monkeypatch.setattr(engine, "run_chat", agent.agent_run)

    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    failure = failure_type("internal task stop")
    generate = Mock(return_value=iter([("final", {"content": "", "tool_calls": [
        {"id": "first", "type": "function", "function": {"name": "query_business_data",
         "arguments": '{"query":"查询库存数量","mode":"natural_language"}'}},
        {"id": "should-not-run", "type": "function", "function": {"name": "query_business_data",
         "arguments": '{"query":"查询其他库存","mode":"natural_language"}'}},
    ]})]))
    execute = Mock(side_effect=failure)
    followups = Mock(return_value=([], ""))
    monkeypatch.setattr(agent, "_llm_stream", generate)
    monkeypatch.setattr(agent.ToolGateway, "execute", execute)
    monkeypatch.setattr(agent, "_generate_followups", followups)
    response = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator,
                           json={"content": "查询库存数量"})

    assert response.status_code == 200, response.text
    assert "event: run.failed" in response.text and "event: done" in response.text
    assert "event: run.completed" not in response.text
    assert expected_code in response.text
    assert "internal task stop" not in response.text
    assert generate.call_count == execute.call_count == 1
    assert followups.call_count == 0
    db.expire_all()
    run = db.scalars(select(Run).where(Run.conversation_id == cid)).one()
    expected_status = "cancelled" if failure_type is ChatCancelled else "failed"
    assert run.status == expected_status and run.error_code == expected_code
    calls = db.scalars(select(ToolCall).where(ToolCall.run_id == run.id)).all()
    assert len(calls) == 1
    assert calls[0].status == "failed" and calls[0].error_code == expected_code
    step = db.get(RunStep, calls[0].step_id)
    assert step.status == "failed" and step.error_code == expected_code
    assert step.completed_at is not None and calls[0].completed_at is not None
    detail = client.get(f"/api/chat/conversations/{cid}", headers=administrator).json()
    assert detail["last_run"]["error_code"] == expected_code
    assert detail["messages"][-1]["role"] == "assistant"
    assert detail["messages"][-1]["content"]
