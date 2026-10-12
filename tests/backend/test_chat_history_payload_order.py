"""隔离 SQLite 与 MySQL SQL 编译检查：历史排序不得携带大段 JSON 或正文。"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import mysql

from app.business_worker import chat, tasks
from app.common.ordered_rows import ordered_payload_rows
from app.models import Conversation, Message, Run, RunEvent, ToolCall
from app.modules.chat import service


@pytest.fixture
def history_rows(db):
    """按乱序插入真实 ORM 数据，保留大工具回执、同时间运行与跨工作区记录。"""
    stamp = datetime(2026, 9, 28, 1, 0, 0)
    conversation = Conversation(id="history-c", workspace_id="history-w", owner_user_id="history-u",
                                title="新对话", created_at=stamp, updated_at=stamp)
    db.add(conversation)
    for identity, sequence, role in [("message-c", 2, "assistant"), ("message-b", 1, "user"), ("message-a", 1, "user")]:
        db.add(Message(id=identity, workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                       role=role, sequence=sequence, content=identity + "。" + "正文" * 16000,
                       created_at=stamp, status="completed", parts_json=[{"type": "text", "text": identity},
                           {"type": "tool_result", "tool": "fixture", "result": {"data": {"large": "回执" * 32000}}}]))
    for identity, owner in [("run-b", "history-u"), ("run-a", "history-u"), ("other-owner", "other")]:
        db.add(Run(id=identity, workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                   started_by_user_id=owner, status="completed", created_at=stamp, started_at=stamp,
                   completed_at=stamp + timedelta(seconds=2), context_snapshot={"task_id": "task-history",
                       "chat_engine": "dsh", "business_task": {"id": "task-history", "title": identity, "status": "completed"},
                       "user_message_id": "message-a" if identity == "run-a" else "message-b",
                       "assistant_message_id": "message-c" if identity == "run-b" else None,
                       "large": "快照" * 32000}))
    db.add_all([
        RunEvent(id=41, workspace_id=conversation.workspace_id, run_id="run-b", event_type="chat.stream", sequence=2, created_at=stamp,
                 payload={"event": "question.resolved", "data": {"question_id": "question-a"}, "large": "事件" * 32000}),
        RunEvent(id=12, workspace_id=conversation.workspace_id, run_id="run-a", event_type="chat.stream", sequence=1, created_at=stamp,
                 payload={"event": "question.requested", "data": {"id": "question-a", "question": "已解决"}}),
        RunEvent(id=48, workspace_id=conversation.workspace_id, run_id="run-b", event_type="chat.stream", sequence=3, created_at=stamp,
                 payload={"event": "question.requested", "data": {"id": "question-b", "question": "待补充"}}),
        RunEvent(id=99, workspace_id=conversation.workspace_id, run_id="other-owner", event_type="chat.stream", sequence=1, created_at=stamp,
                 payload={"event": "message.delta", "data": {"delta": "不可读取"}}),
    ])
    db.commit()
    return SimpleNamespace(db=db, workspace_id=conversation.workspace_id, user_id=conversation.owner_user_id), conversation


@pytest.fixture
def ordered_sql_guard(db):
    """记录实际 SQLite 执行语句，并用 MySQL 方言确认排序选择列表不含大对象。"""
    statements = []

    def inspect_statement(connection, statement, multiparams, params, execution_options):
        if not getattr(statement, "is_select", False):
            return
        compiled = str(statement.compile(dialect=mysql.dialect()))
        statements.append(compiled)
        if "ORDER BY" in compiled:
            selected = compiled.split("\nFROM", 1)[0]
            assert not any(column in selected for column in ("parts_json", "messages.content", "context_snapshot", "run_events.payload", "model_usage", "result_json", "arguments_json")), compiled

    engine = db.get_bind()
    event.listen(engine, "before_execute", inspect_statement)
    try:
        yield statements
    finally:
        event.remove(engine, "before_execute", inspect_statement)


def test_message_history_keeps_sequence_ties_and_complete_payload(history_rows, ordered_sql_guard):
    ctx, conversation = history_rows
    rows = service.list_messages(ctx.db, conversation.id)
    assert [row.id for row in rows] == ["message-a", "message-b", "message-c"]
    assert rows[-1].parts_json[-1]["result"]["data"]["large"] == "回执" * 32000
    assert len(ordered_sql_guard) == 2
    assert "ORDER BY" not in ordered_sql_guard[-1]


def test_placeholder_title_uses_first_user_without_sorting_payload(history_rows, ordered_sql_guard):
    ctx, conversation = history_rows
    assert service.conversation_display_titles(ctx.db, [conversation]) == {conversation.id: "message-a"}


def test_context_history_preserves_runs_events_cursor_and_pending_questions(history_rows, ordered_sql_guard):
    ctx, conversation = history_rows
    result = chat.history_for_context(ctx, conversation.id, after=12)
    assert [row["id"] for row in result["runs"]] == ["run-a", "run-b"]
    assert result["last_run"]["id"] == "run-b"
    assert [row["id"] for row in result["events"]] == [41, 48]
    assert result["cursor"] == 48
    # 历史终态运行的旧追问不能再次显示成等待回答；原事件仍完整保留。
    assert result["projections"]["pending_questions"] == []
    assert result["messages"][-1]["run_id"] == "run-b"
    assert result["messages"][-1]["timing"]["duration_ms"] == 2000
    assert result["projections"]["tasks"][0]["run_ids"] == ["run-a", "run-b"]
    assert ordered_sql_guard


def test_payload_batches_preserve_descending_limit_and_filters(history_rows, ordered_sql_guard):
    ctx, conversation = history_rows
    rows = ordered_payload_rows(ctx.db, Message, (Message.conversation_id == conversation.id,),
                                (Message.sequence.desc(), Message.id.desc()), limit=2, batch_size=1)
    assert [row.id for row in rows] == ["message-c", "message-b"]
    assert len(ordered_sql_guard) == 3
    assert "LIMIT" in ordered_sql_guard[0]
    assert all("ORDER BY" not in statement for statement in ordered_sql_guard[1:])


def test_active_history_keeps_unanswered_question_without_reviving_resolved_one(history_rows, ordered_sql_guard):
    ctx, conversation = history_rows
    ctx.db.get(Run, "run-b").status = "running"
    ctx.db.commit()
    result = chat.history_for_context(ctx, conversation.id)
    assert [row["id"] for row in result["projections"]["pending_questions"]] == ["question-b"]


def test_overflow_history_keeps_latest_runs_messages_events_and_projections(history_rows, ordered_sql_guard, monkeypatch):
    """让真实查询超过各自窗口，验证探测用的额外一条不会挤掉最新运行。"""
    ctx, conversation = history_rows
    monkeypatch.setattr(chat, "MAX_HISTORY_RUNS", 2)
    monkeypatch.setattr(chat, "MAX_HISTORY_MESSAGES", 2)
    monkeypatch.setattr(chat, "MAX_HISTORY_EVENTS", 2)
    for index, suffix in enumerate(("c", "d"), start=3):
        identity, message_id = "run-" + suffix, "new-message-" + suffix
        ctx.db.add(Run(id=identity, workspace_id=ctx.workspace_id, conversation_id=conversation.id,
            started_by_user_id=ctx.user_id, status="running" if suffix == "d" else "completed",
            created_at=conversation.created_at, model_profile="model-" + suffix,
            context_snapshot={"chat_engine": "dsh", "assistant_message_id": message_id, "mode": "standard"}))
        ctx.db.add(Message(id=message_id, workspace_id=ctx.workspace_id, conversation_id=conversation.id,
            role="assistant", sequence=index, content=identity, created_at=conversation.created_at,
            status="running" if suffix == "d" else "completed"))
    ctx.db.add_all([
        RunEvent(id=100, workspace_id=ctx.workspace_id, run_id="run-c", event_type="chat.stream", sequence=1, created_at=conversation.created_at,
            payload={"event": "todo.updated", "data": {"todos": [{"content": "旧任务"}]}}),
        RunEvent(id=101, workspace_id=ctx.workspace_id, run_id="run-d", event_type="chat.stream", sequence=1, created_at=conversation.created_at,
            payload={"event": "todo.updated", "data": {"todos": [{"content": "最新任务"}]}}),
        RunEvent(id=102, workspace_id=ctx.workspace_id, run_id="run-d", event_type="chat.stream", sequence=2, created_at=conversation.created_at,
            payload={"event": "question.requested", "data": {"id": "latest-question", "question": "请选择范围"}}),
    ])
    ctx.db.commit()

    result = chat.history_for_context(ctx, conversation.id, after=100)

    assert [row["id"] for row in result["runs"]] == ["run-c", "run-d"]
    assert result["last_run"]["id"] == "run-d"
    assert result["last_run"]["status"] == "running"
    assert result["last_run"]["model_profile"] == "model-d"
    assert [(row["id"], row["run_id"]) for row in result["messages"]] == [
        ("new-message-c", "run-c"), ("new-message-d", "run-d")]
    assert [row["id"] for row in result["events"]] == [101, 102]
    assert result["cursor"] == 102
    assert result["projections"]["model"] == "model-d"
    assert result["projections"]["todos"] == [{"content": "最新任务"}]
    assert [row["id"] for row in result["projections"]["pending_questions"]] == ["latest-question"]
    assert result["history_window"] == {"runs_limit": 2, "messages_limit": 2, "events_limit": 2,
        "runs_truncated": True, "messages_truncated": True, "events_truncated": True}


def test_run_authorization_returns_owned_status_outside_history_window(history_rows, monkeypatch):
    """目标运行状态由独立鉴权读取，不依赖当前展示窗口，也不能读取其他人的运行。"""
    from contextlib import nullcontext
    from app.errors import NotFoundError

    ctx, conversation = history_rows
    monkeypatch.setattr(chat, "session", lambda: nullcontext(ctx.db))
    monkeypatch.setattr(chat, "authenticate", lambda db, request: ctx)
    monkeypatch.setattr(chat, "MAX_HISTORY_RUNS", 1)
    assert [row["id"] for row in chat.history_for_context(ctx, conversation.id)["runs"]] == ["run-b"]
    result = chat.authorize({}, run_id="run-a")
    assert result["run_id"] == "run-a"
    assert result["run_status"] == "completed"
    assert result["conversation_id"] == conversation.id
    with pytest.raises(NotFoundError):
        chat.authorize({}, run_id="other-owner")


def test_empty_history_does_not_read_payload(history_rows, ordered_sql_guard):
    ctx, _ = history_rows
    assert service.list_messages(ctx.db, "missing") == []
    assert len(ordered_sql_guard) == 1


def test_explicit_task_selection_keeps_latest_matching_run(history_rows, ordered_sql_guard):
    ctx, conversation = history_rows
    payload = chat.SendInput(content="修改这份报告", task_id="task-history")
    result = tasks.choose_task(ctx.db, ctx, conversation.id, payload)
    assert result["id"] == "task-history"
    assert result["title"] == "run-b"
    assert result["requires_new_delivery"] is True


def test_resume_reuses_large_receipts_without_sorting_json(history_rows, ordered_sql_guard):
    """补交既有大屏会读取旧工具回执，按时间恢复顺序但不把完整回执送入排序缓冲。"""
    from app.modules.chat_artifacts.resume import validated_resume_calls
    ctx, _ = history_rows
    earlier, current = ctx.db.get(Run, "run-a"), ctx.db.get(Run, "run-b")
    for run in (earlier, current):
        run.context_snapshot = {**run.context_snapshot, "business_task": {"task_id": "task-history"}}
    for identity, seconds in (("call-b", 2), ("call-a", 1)):
        ctx.db.add(ToolCall(id=identity, workspace_id=ctx.workspace_id, run_id=earlier.id,
            tool_name="query_business_data", status="completed", arguments_json={"query": "真实查询"},
            result_json={"status": "success", "data": {"payload": "回执" * 32000}},
            started_at=earlier.created_at, completed_at=earlier.created_at + timedelta(seconds=seconds)))
    ctx.db.commit()
    assert [call.id for call in validated_resume_calls(ctx.db, current)] == ["call-a", "call-b"]
