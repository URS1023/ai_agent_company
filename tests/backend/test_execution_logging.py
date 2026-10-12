"""验证日志落盘、轮转、脱敏及真实执行入口的追踪关联；仅用内存和隔离夹具。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core import observability as logs


@pytest.fixture
def log_files(tmp_path):
    """为本用例配置独立日志目录，结束后恢复服务的隔离测试日志。"""
    from app.config import settings
    options = NS(LOG_DIR=str(tmp_path), STORAGE_ROOT=str(tmp_path), LOG_LEVEL="INFO",
                 LOG_MAX_BYTES=1024 * 1024, LOG_BACKUP_COUNT=2)
    paths = logs.configure_logging(options)
    yield options, paths
    logs.configure_logging(settings)


def read_records(path):
    """逐行解析日志，验证中文与 JSON 行边界均有效。"""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def test_console_and_files_are_utf8_redacted_and_idempotent(log_files):
    options, paths = log_files
    assert logs.configure_logging(options) == paths
    stream = io.StringIO()
    handler = next(h for h in logging.getLogger().handlers
                   if getattr(h, "_execution_owned", False) and not hasattr(h, "baseFilename"))
    handler.setStream(stream)
    with logs.log_context(conversation_id="会话甲", run_id="run-1"):
        logs.log_event("chat.check", "中文日志正常", api_key="secret-key-value",
                       messages=["私密问题"], details={"password": "db-credential"})
        logging.getLogger("third_party").warning(
            'Authorization: Bearer accesssecret password="pw-123" '
            'api_key=sk-testing12345 mysql+pymysql://user:password123@127.0.0.1/db')
    text = Path(paths["backend"]).read_text(encoding="utf-8") + stream.getvalue()
    assert "中文日志正常" in text
    for secret in ("secret-key-value", "私密问题", "db-credential", "accesssecret", "pw-123", "sk-testing12345", "password123"):
        assert secret not in text
    records = read_records(paths["ai_chat"])
    assert sum(r["event"] == "chat.check" for r in records) == 1
    assert all(r["conversation_id"] == "会话甲" for r in records)


def test_rotation_preserves_valid_json_and_limits_backups(log_files):
    options, _ = log_files
    options.LOG_MAX_BYTES = 1024
    paths = logs.configure_logging(options)
    for i in range(35):
        logs.log_event("chat.rotate", "中文轮转" * 20, conversation_id="rotate", sequence=i)
    # 人读日志是 UTF-8 文本；机器日志才要求每行均可解析为 JSON。
    for kind in ("backend", "ai_chat"):
        path = paths[kind]
        files = list(Path(path).parent.glob(Path(path).name + "*"))
        assert 2 <= len(files) <= 3
        for file in files:
            assert read_records(file)
    assert read_records(paths["ai_chat"])[-1]["sequence"] == 34


def test_human_chat_log_contains_content_without_trace_ids(log_files):
    """人读日志直接显示问答正文，机器追踪字段只保留在 JSONL。"""
    _, paths = log_files
    with logs.log_context(workspace_id="workspace-secret", conversation_id="会话甲", run_id="run-1",
                          operation_id="operation-secret"):
        logs.log_event("chat.message.user", "用户发送消息", human_label="用户", human_content="查设备状态")
        logs.log_event("chat.message.assistant", "AI 回复", human_label="AI", human_content="已找到 6 台设备")
    text = Path(paths["ai_chat_readable"]).read_text(encoding="utf-8")
    assert "用户：查设备状态" in text
    assert "AI：已找到 6 台设备" in text
    assert "workspace-secret" not in text
    assert "operation-secret" not in text
    rows = read_records(paths["ai_chat"])
    assert all("查设备状态" not in json.dumps(row, ensure_ascii=False) for row in rows)


def test_parallel_context_isolated_and_restored(log_files):
    _, paths = log_files

    @logs.observe("test.node", "节点测试")
    def operation(index):
        logs.log_event("test.inside", "节点内部", item=index)
        return index

    def worker(index):
        with logs.log_context(conversation_id=f"conversation-{index}", run_id=f"run-{index}"):
            return operation(index)

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(worker, range(10))) == list(range(10))
    records = [r for r in read_records(paths["ai_chat"]) if r["event"] == "test.inside"]
    assert len(records) == 10
    for record in records:
        assert record["conversation_id"] == f"conversation-{record['item']}"
        assert record["run_id"] == f"run-{record['item']}"
    assert logs.current_context() == {}


def test_failure_and_cancellation_keep_original_exception(log_files):
    from app.modules.runtime.task_control import ChatCancelled
    _, paths = log_files
    failure = ValueError("password=do-not-log-this")

    @logs.observe("test.failure", "失败测试")
    def fail():
        raise failure

    @logs.observe("test.stop", "取消测试")
    def stop():
        raise ChatCancelled("已停止")

    with pytest.raises(ValueError) as raised:
        fail()
    assert raised.value is failure
    with pytest.raises(ChatCancelled):
        stop()
    records = read_records(paths["backend"])
    failed = next(r for r in records if r["event"] == "test.failure.failed")
    assert failed["error_type"] == "ValueError" and failed["duration_ms"] >= 0
    assert failed["stack"]
    assert "do-not-log-this" not in Path(paths["backend"]).read_text(encoding="utf-8")
    assert any(r["event"] == "test.stop.cancelled" for r in records)


def test_real_tool_gateway_records_failed_results(log_files, monkeypatch):
    from app.providers.tool.base import ToolResult, ToolSpec
    from app.providers.tool.gateway import ToolGateway
    _, paths = log_files
    gateway = ToolGateway(None)
    spec = ToolSpec("logging_fixture", "隔离工具", "builtin", input_schema={"type": "object"})
    monkeypatch.setattr(gateway, "resolve", lambda *a, **k: spec)
    monkeypatch.setattr(gateway, "_authorize_spec", lambda *a: None)
    monkeypatch.setattr(gateway, "_prepare_secrets", lambda *a: None)
    monkeypatch.setattr(gateway, "_dispatch", lambda *a: ToolResult("failed", error_code="fixture_error"))
    with logs.log_context(conversation_id="tool-conversation"):
        result = gateway.execute("w", "logging_fixture", {}, {"run_id": "tool-run"})
    assert result.status == "failed"
    rows = read_records(paths["ai_chat"])
    assert [r["event"] for r in rows] == ["tool.execute.started", "tool.execute.failed"]
    assert rows[-1]["error_code"] == "fixture_error"
    assert rows[-1]["tool_name"] == "logging_fixture"
    assert rows[0]["operation_id"] == rows[-1]["operation_id"]


def test_real_workflow_node_records_each_attempt_and_wait(log_files):
    from app.modules.workflows.nodes import execute_node, WorkflowPause
    from app.modules.workflows.expressions import RuleBudget
    import time
    _, paths = log_files
    ctx = NS(workspace_id="w")
    budget = RuleBudget(time.monotonic() + 5)
    assert execute_node(ctx, NS(id="start", kind="start", title="开始", config={}),
                        {"value": "私密内容"}, budget, run_id="wf-run") == {"value": "私密内容"}
    rows = read_records(paths["backend"])
    assert [r["event"] for r in rows if r.get("node_id") == "start"] == ["workflow.node.started", "workflow.node.completed"]
    assert "私密内容" not in Path(paths["backend"]).read_text(encoding="utf-8")


def test_model_stream_logs_first_chunk_and_worker_keeps_context(log_files, monkeypatch):
    from app.providers import model
    from app.modules.runtime.agent import _llm_stream
    _, paths = log_files
    monkeypatch.setattr(model, "_profile_config", lambda *a, **k: {
        "model": "fixture-model", "provider": "fixture", "base_url": "https://unused.invalid", "api_key": "private-fixture-key"})
    monkeypatch.setattr(model, "_governed_call", lambda *a: None)
    monkeypatch.setattr(model, "_persist_usage", lambda **k: None)
    client = MagicMock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = iter([
        NS(usage=None, choices=[NS(finish_reason=None, delta=NS(content="中文回复", tool_calls=[]))]),
        NS(usage=None, choices=[NS(finish_reason="stop", delta=NS(content=None, tool_calls=[]))]),
    ])
    monkeypatch.setattr(model, "_client", lambda *a: client)
    with logs.log_context(conversation_id="stream-c", run_id="stream-r", request_id="stream-request"):
        events = list(_llm_stream(None, NS(model_profile="reasoning", workspace_id="w"), [], None))
    assert events[-1][1]["content"] == "中文回复"
    rows = read_records(paths["ai_chat"])
    assert {r["event"] for r in rows} >= {"model.stream.started", "model.stream.first_chunk", "model.stream.completed"}
    assert all(r["conversation_id"] == "stream-c" and r["run_id"] == "stream-r" for r in rows)
    assert "private-fixture-key" not in Path(paths["backend"]).read_text(encoding="utf-8")
    assert "中文回复" not in Path(paths["backend"]).read_text(encoding="utf-8")


def test_chat_delta_logged_once_and_all_states_logged(log_files, monkeypatch):
    from app.modules.runtime.chat_jobs import ChatJob
    _, paths = log_files
    job = ChatJob("chat-run", "w", "chat-conversation")
    monkeypatch.setattr(job, "_save", lambda *a: None)
    job.emit("run.started", {})
    for _ in range(200):
        job.emit("message.delta", {"delta": "正文"})
    job.emit("run.completed", {"status": "completed"})
    job.emit("done", {})
    rows = read_records(paths["ai_chat"])
    assert len(rows) == 4
    assert rows[-1]["output_characters"] == 400
    assert [r["event"] for r in rows].count("chat.output.started") == 1


def test_asgi_stream_not_buffered_and_disconnect_is_distinct(log_files):
    _, paths = log_files
    packets, snapshots = [], []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        snapshots.append(len(packets))
        await receive()
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    async def receive():
        return {"type": "http.disconnect"}

    async def send(packet):
        packets.append(packet)

    asyncio.run(logs.RequestLoggingMiddleware(app)(
        {"type": "http", "method": "POST", "path": "/api/chat/conversations/middleware-c/send"}, receive, send))
    assert snapshots == [2]
    assert any(key == b"x-request-id" for key, _ in packets[0]["headers"])
    rows = read_records(paths["ai_chat"])
    assert rows[-1]["disconnected"] is True and rows[-1]["response_bytes"] == 9
    assert len({r["request_id"] for r in rows}) == 1


def test_logging_failure_cannot_fail_business_operation(log_files, monkeypatch):
    monkeypatch.setattr(logs._logger, "log", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    @logs.observe("test.disk", "磁盘异常测试")
    def operation():
        return {"ok": True}

    assert operation() == {"ok": True}


def test_background_chat_retains_http_request_and_conversation(log_files, client, administrator, monkeypatch):
    """真实发送接口与分离线程共用追踪标识，执行体用本地夹具避免模型调用。"""
    from app.modules.openwebui import runtime as native_runtime
    from app.modules.runtime import service
    _, paths = log_files

    def fake_run(db, run, workspace, user, **kwargs):
        """openwebui 引擎分派入口的本地夹具；不连接 Open WebUI 后端。"""
        logs.log_event("test.background.seen", "已进入后台线程")
        yield {"type": "run.started", "payload": {"status": "running", "engine": "openwebui"}}
        with logs.log_context(run_id="nested-query-run"):
            logs.log_event("test.nested.seen", "嵌套内部会话", conversation_id="internal-conversation")
        service.finalize_run(db, run, status="completed")
        yield {"type": "run.completed", "payload": {"status": "completed"}}

    monkeypatch.setattr(native_runtime, "run_chat", fake_run)
    created = client.post("/api/chat/conversations", headers=administrator, json={"title": "日志测试"}).json()
    response = client.post(f"/api/chat/conversations/{created['id']}/send", headers=administrator,
                           json={"content": "仅执行本地日志夹具"})
    assert response.status_code == 200, response.text
    assert "event: done" in response.text
    rows = read_records(paths["ai_chat"])
    background = next(r for r in rows if r["event"] == "test.background.seen")
    assert background["conversation_id"] == created["id"]
    assert background["request_id"] == response.headers["x-request-id"]
    nested = next(r for r in rows if r["event"] == "test.nested.seen")
    assert nested["conversation_id"] == created["id"]
    assert nested["record_conversation_id"] == "internal-conversation"
    assert nested["chat_run_id"] == background["run_id"]


def test_access_query_token_is_redacted(log_files):
    """访问日志即使带令牌查询参数，也不能把令牌原文落盘。"""
    _, paths = log_files
    logging.getLogger("uvicorn.access").info("GET /api/example?token=private-access-token&limit=10")
    text = Path(paths["backend"]).read_text(encoding="utf-8")
    assert "private-access-token" not in text
    assert "limit=10" in text
