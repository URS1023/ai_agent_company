"""DSH 业务 Worker 隔离验收：只使用测试夹具 SQLite、临时文件和模型伪对象。"""
from __future__ import annotations

import base64
import json
import queue
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.business_worker import application, chat, context, tasks, tools, transport
from app.business_worker.models import model_configuration
from app.business_worker.protocol import JsonRpc
from app.models import Artifact, Message, Run
from app.providers.tool.base import ToolResult


def request(headers, path, data=None, method="GET"):
    return {"method": method, "path": path, "query_string": "",
            "headers": [[key, value] for key, value in {**headers, "content-type": "application/json"}.items()],
            "body_base64": base64.b64encode(json.dumps(data).encode()).decode() if data is not None else ""}


@pytest.fixture
def worker_context(client, administrator, monkeypatch):
    """只替换模型配置，不替换业务网关、身份与工件持久化。"""
    from app.providers import model
    monkeypatch.setattr(transport, "_rpc", None)
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "model": "fixture", "base_url": "http://127.0.0.1:12349/v1", "api_key": "fixture",
        "capabilities": {"runtime_options": {"thinking": "disabled"}}})
    conversation = client.post("/api/chat/conversations", headers=administrator, json={"title": "Worker测试"}).json()
    assert conversation.get("id"), conversation
    return administrator, conversation["id"]


def prepare(worker_context, content="查看文件", **extra):
    headers, cid = worker_context
    return chat.prepare(request(headers, f"/api/chat/conversations/{cid}/send", {"content": content, **extra}, "POST"))


def test_worker_preserves_static_auth_and_api_contract(worker_context):
    headers, _ = worker_context
    response = application.dispatch(request(headers, "/api/auth/me"))
    assert response["status"] == 200, base64.b64decode(response["body_base64"])
    page = application.dispatch(request({}, "/ai"))
    assert page["status"] == 200
    assert b"<!doctype html>" in base64.b64decode(page["body_base64"]).lower()
    declared = {(method, row["path"]) for row in application.routes() for method in row["methods"]}
    assert ("POST", "/api/chat/conversations/{conversation_id}/send") not in declared
    assert ("GET", "/api/v1/workflows") in declared
    assert application.application().openapi()["paths"]


def test_prepare_idempotency_persistent_events_and_recovery(worker_context, db):
    first = prepare(worker_context, request_id="fixture-prepare-001")
    duplicate = prepare(worker_context, request_id="fixture-prepare-001")
    assert first["run_id"] == duplicate["run_id"] and duplicate["duplicate"]
    assert first["client_request_id"] == "fixture-prepare-001"
    assert first["status"] == "queued"
    from app.modules.runtime.chat_liveness import recover_abandoned_chat
    run = db.get(Run, first["run_id"])
    assert not recover_abandoned_chat(db, run)
    assert chat.recover()["runs"][0]["run_id"] == first["run_id"]
    start = chat.append_event(first["run_id"], "run.started", {}, 1)
    repeated = chat.append_event(first["run_id"], "run.started", {}, 1)
    assert start == repeated
    chat.append_event(first["run_id"], "message.replace", {"content": "实际回答"}, 2)
    settled = chat.settle(first["run_id"], "completed", "实际回答")
    assert settled["status"] == "completed"
    headers, cid = worker_context
    history = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)
    assert history["messages"][-1]["content"] == "实际回答"
    assert history["messages"][-1]["run_id"] == first["run_id"]
    assert history["messages"][0]["request_id"] == "fixture-prepare-001"
    assert history["cursor"] >= start["id"]
    assert not history["projections"]["queue"]


def test_run_timing_is_server_owned_and_consistent_after_refresh(worker_context):
    """实时事件与刷新后的消息使用同一平台时间，不接受客户端伪造耗时。"""
    from datetime import datetime
    first = prepare(worker_context)
    started = chat.append_event(first["run_id"], "run.started", {"started_at": "1999-01-01T00:00:00Z"})
    assert started["data"]["started_at"] != "1999-01-01T00:00:00Z"
    assert started["data"]["completed_at"] is None
    chat.settle(first["run_id"], "completed", "已完成")
    finished = chat.append_event(first["run_id"], "run.completed", {"duration_ms": 99999999})
    headers, cid = worker_context
    history = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)
    timing = history["messages"][-1]["timing"]
    assert timing["duration_ms"] != 99999999
    assert timing["started_at"].endswith("Z") and timing["completed_at"].endswith("Z")
    expected = (datetime.fromisoformat(timing["completed_at"]) - datetime.fromisoformat(timing["started_at"])).total_seconds() * 1000
    assert abs(timing["duration_ms"] - expected) <= 1
    for key in timing:
        assert finished["data"][key] == timing[key] == history["last_run"][key]


def test_history_keeps_each_run_terminal_status_for_stream_resume(worker_context):
    """旧运行恢复订阅必须使用自己的终态，不能被新一轮的取消或运行中状态覆盖。"""
    first = prepare(worker_context, "查询当前时间")
    chat.append_event(first["run_id"], "run.started")
    chat.settle(first["run_id"], "completed", "已完成")
    second = prepare(worker_context, "下一项任务")
    chat.settle(second["run_id"], "cancelled")
    headers, cid = worker_context
    history = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)
    runs = {row["id"]: row for row in history["runs"]}
    assert runs[first["run_id"]]["status"] == "completed"
    assert runs[first["run_id"]]["duration_ms"] is not None
    assert runs[second["run_id"]]["status"] == "cancelled"
    assert history["last_run"]["id"] == second["run_id"]


def test_delivery_requires_actual_artifact_and_preserves_download(worker_context):
    initial = prepare(worker_context, "生成一份 Word 文档")
    missing = chat.settle(initial["run_id"], "completed", "文档已制作完成，请下载附件。")
    assert missing["status"] == "partial" and missing["error"]["code"] == "delivery_incomplete"
    continued = prepare(worker_context, "继续")
    assert continued["task"]["task_id"] == initial["task"]["task_id"]
    result = tools.execute(continued["run_id"], "doc-1", 10, "render_document", {
        "name": "真实交付", "document_spec": {"title": "真实交付", "sections": [{"title": "结果", "blocks": [{"type": "p", "text": "隔离测试文件"}]}]}})
    assert result["status"] == "success", result
    settled = chat.settle(continued["run_id"], "completed", "文档已生成。")
    assert settled["status"] == "completed"
    artifact = settled["deliveries"][0]
    assert artifact["task_id"] == initial["task"]["task_id"] and artifact["status"] == "ready"
    downloaded = application.dispatch(request(worker_context[0], artifact["download_url"]))
    assert downloaded["status"] == 200 and base64.b64decode(downloaded["body_base64"]).startswith(b"PK")


def test_generic_tool_failure_does_not_poison_next_turn(worker_context, monkeypatch):
    first = prepare(worker_context)
    from app.providers.tool.gateway import ToolGateway
    monkeypatch.setattr(ToolGateway, "execute", lambda *args, **kwargs: ToolResult(status="failed", error_code="tool_internal_error", error_summary="颜色处理失败"))
    failed = tools.execute(first["run_id"], "failure", 1, "list_files", {})
    assert failed["metadata"]["recovery"] == "correct_input"
    chat.settle(first["run_id"], "failed", "工具失败")
    second = prepare(worker_context, "告诉我现在时间")
    assert second["status"] == "queued"
    monkeypatch.setattr(ToolGateway, "execute", lambda *args, **kwargs: ToolResult(status="success", data={"time": "fixture"}))
    result = tools.execute(second["run_id"], "success", 2, "current_datetime", {})
    assert result["status"] == "success"


def test_presentation_draft_stable_across_continue_and_specific_edit(worker_context, db):
    from app.modules.presentation.draft import _save, load_draft, _key
    first = prepare(worker_context, "制作一份 PPT")
    with context.run_context(db.get(Run, first["run_id"])):
        original_key = _key(first["workspace_id"], first["run_id"])
        _save(first["workspace_id"], first["run_id"], {"title": "原草稿", "slides": [{"written": True}]})
    chat.settle(first["run_id"], "completed", "已写部分页面")
    second = prepare(worker_context, "把第二页改成销售分析")
    assert second["task"]["task_id"] == first["task"]["task_id"]
    with context.run_context(db.get(Run, second["run_id"])):
        assert _key(second["workspace_id"], second["run_id"]) == original_key
        assert load_draft(second["workspace_id"], second["run_id"])["title"] == "原草稿"
    unrelated = prepare(worker_context, "查一下设备数量")
    assert unrelated["task"]["task_id"] != second["task"]["task_id"]


def test_reasoning_and_plan_only_delivery_contract():
    disabled = model_configuration({"model": "fixture", "base_url": "https://api.deepseek.com",
        "capabilities": {"runtime_options": {"thinking": "disabled"}}})
    assert disabled["thinking"] == "disabled" and disabled["reasoningEffort"] == "off"
    assert tasks.requirements("先给 Word 文档方案，确认后生成") == []
    assert tasks.requirements("制作 PPT 和 Excel") == [{"type": "presentation", "count": 1}, {"type": "spreadsheet", "count": 1}]


def test_reasoning_projection_survives_refresh_and_discards_failed_attempt(worker_context):
    first = prepare(worker_context)
    chat.append_event(first["run_id"], "reasoning.delta", {"delta": "未提交推理", "attempt_id": "failed"})
    chat.append_event(first["run_id"], "message.replace", {"content": "", "committed": False})
    chat.append_event(first["run_id"], "reasoning.delta", {"delta": "真实推理", "attempt_id": "success"})
    chat.settle(first["run_id"], "completed", "答案")
    headers, cid = worker_context
    message = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)["messages"][-1]
    reasoning = [part for part in message["parts"] if part["type"] == "reasoning"]
    assert len(reasoning) == 1 and reasoning[0]["text"] == "真实推理"


def test_native_tool_projection_is_persistent_and_idempotent(worker_context):
    first = prepare(worker_context)
    started = {"call_id": "native-read", "tool_name": "read_file", "arguments": {"path": "fixture"}, "native": True}
    chat.append_event(first["run_id"], "tool.started", started, 10)
    chat.append_event(first["run_id"], "tool.started", started, 10)
    chat.append_event(first["run_id"], "tool.failed", {**started, "result": {"content": []}, "status": "failed"}, 11)
    headers, cid = worker_context
    parts = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)["messages"][-1]["parts"]
    assert len([item for item in parts if item["type"] == "tool_call"]) == 1
    assert next(item for item in parts if item["type"] == "tool_call")["status"] == "failed"
    outcome = next(item for item in parts if item["type"] == "tool_result")
    assert outcome["native"] is True and outcome["result"]["status"] == "failed"


def test_cancel_returns_persisted_partial_content_and_settles_once(worker_context):
    first = prepare(worker_context)
    chat.append_event(first["run_id"], "message.delta", {"delta": "已完成的部分分析"})
    cancelled = chat.settle(first["run_id"], "cancelled", "")
    repeated = chat.settle(first["run_id"], "completed", "不应覆盖")
    assert cancelled["content"] == repeated["content"] == "已完成的部分分析"
    assert repeated["duplicate"] is True and repeated["status"] == "cancelled"
    headers, cid = worker_context
    message = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)["messages"][-1]
    assert message["content"] == cancelled["content"] and message["status"] == "cancelled"


def test_queue_edit_rebuilds_delivery_task_prompt_and_user_message(worker_context):
    first = prepare(worker_context, "制作 PPT")
    edited = chat.queue_update(first["run_id"], "edit", "改成生成 Word 文档")
    assert edited["run_id"] == first["run_id"] and edited["task"]["task_id"] == first["task"]["task_id"]
    assert edited["task"]["required_deliveries"] == [{"type": "document", "count": 1}]
    assert edited["content"][0]["text"] == "改成生成 Word 文档"
    assert '"required_deliveries": [{"type": "document"' in edited["system_prompt"]
    assert edited["model"] and edited["tools"] and edited["user_message_id"]
    headers, cid = worker_context
    user_message = chat.history(request(headers, f"/api/chat/conversations/{cid}"), cid)["messages"][0]
    assert user_message["id"] == edited["user_message_id"] and user_message["content"] == "改成生成 Word 文档"


def test_history_seed_contains_legacy_messages_without_polluting_system_prompt(worker_context, db):
    legacy = prepare(worker_context, "上次选择绿色")
    row = db.get(Run, legacy["run_id"])
    row.context_snapshot = {**row.context_snapshot, "chat_engine": "harness"}
    db.commit()
    first = prepare(worker_context, "继续分析")
    assert [item["content"] for item in first["history_seed"]["messages"] if item["role"] == "user"] == ["上次选择绿色"]
    assert "上次选择绿色" not in first["system_prompt"]
    second = prepare(worker_context, "回答第二个问题")
    assert [item["content"] for item in second["history_seed"]["messages"] if item["role"] == "user"] == ["上次选择绿色"]


def test_legacy_presentation_draft_migrates_to_stable_task(worker_context, db):
    from app.modules.presentation.draft import _save, load_draft
    first = prepare(worker_context, "制作 PPT")
    row = db.get(Run, first["run_id"])
    row.context_snapshot = {key: value for key, value in row.context_snapshot.items()
                            if key not in {"task_id", "business_task"}}
    row.context_snapshot = {**row.context_snapshot, "chat_engine": "harness"}
    row.status = "failed"
    db.commit()
    _save(row.workspace_id, row.id, {"title": "迁移前草稿", "slides": []})
    continued = prepare(worker_context, "继续")
    assert continued["task"]["resume_from_run_id"] == row.id
    with context.run_context(db.get(Run, continued["run_id"])):
        assert load_draft(row.workspace_id, continued["run_id"])["title"] == "迁移前草稿"


def test_invalid_chat_payload_returns_validation_error(worker_context):
    from app.errors import ValidationError
    with pytest.raises(ValidationError):
        prepare(worker_context, "", request_id="bad")


def test_business_model_calls_reverse_rpc_only(worker_context, monkeypatch):
    from app.providers import model
    calls = []
    def reverse(method, params, **kwargs):
        calls.append((method, params))
        return {"content": "宿主模型结果", "tool_calls": [], "usage": {}, "finish_reason": "stop"}
    monkeypatch.setattr(transport, "_rpc", SimpleNamespace(request=reverse))
    monkeypatch.setattr(model, "_client", lambda *args: pytest.fail("Worker 不得创建 Python 对话模型客户端"))
    result = model.chat_for_run("reasoning", [{"role": "user", "content": "测试"}], workspace_id=worker_context[0]["X-Workspace-Id"])
    assert result["content"] == "宿主模型结果"
    assert calls[0][0] == "model.generate" and calls[0][1]["model"]["thinking"] == "disabled"


def test_vision_reverse_contract_preserves_image_and_model_metadata(worker_context, monkeypatch):
    from app.providers import model
    monkeypatch.setattr(model.settings, "VISION_ENABLED", True)
    monkeypatch.setattr(model, "_governed_call", lambda workspace_id: None)
    captured = []
    def reverse(method, params, **kwargs):
        captured.append(params)
        return {"content": "  识别文本  ", "tool_calls": [], "usage": {"total_tokens": 12}, "finish_reason": "stop"}
    monkeypatch.setattr(transport, "_rpc", SimpleNamespace(request=reverse))
    monkeypatch.setattr(model, "_client", lambda *args: pytest.fail("视觉业务不能打开 Python 模型客户端"))
    result = model.analyze_image(b"fixture-image", "image/png", workspace_id=worker_context[0]["X-Workspace-Id"])
    assert result == {"content": "识别文本", "model": "fixture", "provider": "openai-compatible", "usage": {"total_tokens": 12}}
    params = captured[0]
    assert params["capability"] == "vision" and params["model"]["inputModalities"] == ["text", "image"]
    assert "reasoningEffort" not in params["model"] and params["model"]["reasoningEfforts"] is False
    assert params["model"]["maxTokens"] == model.settings.VISION_MAX_OUTPUT_TOKENS
    encoded = params["messages"][0]["content"][0]["image_url"]["url"]
    assert base64.b64decode(encoded.split(",", 1)[1]) == b"fixture-image"
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "model": "fixture", "base_url": "http://127.0.0.1:12349/v1", "api_key": "fixture",
        "capabilities": {"runtime_options": {"input_modalities": ["text"]}}})
    model.analyze_image(b"fixture-image", "image/png", workspace_id=worker_context[0]["X-Workspace-Id"])
    assert captured[-1]["model"]["inputModalities"] == ["text"]


def test_chat_image_attachment_uses_declared_model_modality(worker_context, client, monkeypatch):
    from app.providers import model
    uploaded = client.post("/api/files", headers=worker_context[0], files={"file": ("fixture.png", b"fixture-image", "image/png")})
    assert uploaded.status_code in {200, 201}, uploaded.text
    file_id = uploaded.json()["id"]
    plain = prepare(worker_context, "分析这张图", file_ids=[file_id])
    assert [part["type"] for part in plain["content"]] == ["text"]
    assert "通过视觉业务服务读取图片" in plain["system_prompt"]
    monkeypatch.setattr(model, "_profile_config", lambda *args, **kwargs: {
        "model": "fixture", "base_url": "http://127.0.0.1:12349/v1", "api_key": "fixture",
        "capabilities": {"runtime_options": {"input_modalities": ["text", "image"]}}})
    visual = prepare(worker_context, "分析这张图", file_ids=[file_id])
    image = next(part for part in visual["content"] if part["type"] == "image")
    assert image["mediaType"] == "image/png" and base64.b64decode(image["data"]) == b"fixture-image"


def test_failed_model_with_usage_preserves_ledger_and_business_run(worker_context, db, monkeypatch):
    from app.business_worker import ledger
    from app.models import ModelCall
    from app.providers import model
    from app.modules.model import service
    monkeypatch.setattr(service, "enforce_budget", lambda workspace_id: None)
    recorded = []
    monkeypatch.setattr(model, "_persist_usage", lambda **kwargs: recorded.append(kwargs))
    prepared = prepare(worker_context)
    permission = ledger.authorize(run_id=prepared["run_id"], provider="fixture-native", model="fixture", profile="vision",
                                  capability="vision", usage_context={"source": "model_probe", "source_id": "vision"})
    finished = ledger.settle(prepared["run_id"], permission["model_call_id"], "failed", {"total_tokens": 10}, {"code": "transport_failure"})
    assert finished["status"] == "failed"
    db.expire_all()
    assert db.get(ModelCall, permission["model_call_id"]).status == "failed"
    assert db.get(Run, prepared["run_id"]).status == "queued"
    assert recorded[0]["capability"] == recorded[0]["profile_name"] == "vision"
    assert recorded[0]["usage_context"]["source"] == "model_probe" and recorded[0]["usage"]["total_tokens"] == 10


def test_api_model_probe_retains_authenticated_actor(worker_context, db, monkeypatch):
    from app.providers import model
    captured = []
    def reverse(method, params, **kwargs):
        captured.append(params)
        return {"content": "ok", "tool_calls": [], "finish_reason": "stop", "usage": {}}
    monkeypatch.setattr(transport, "_rpc", SimpleNamespace(request=reverse))
    response = application.dispatch(request(worker_context[0], "/api/models/probe/reasoning", {}, "POST"))
    assert response["status"] == 200, base64.b64decode(response["body_base64"])
    assert captured
    actor = chat.authorize(request(worker_context[0], "/api/chat/conversations/" + worker_context[1]), conversation_id=worker_context[1])
    assert captured[0]["usage_context"]["actor_user_id"] == actor["user_id"]


def test_bidirectional_protocol_keeps_reader_free():
    incoming, outgoing = queue.Queue(), queue.Queue()
    class Source:
        def readline(self):
            return incoming.get(timeout=5) or ""
    class Sink:
        def write(self, line):
            outgoing.put(json.loads(line))
        def flush(self):
            pass
    rpc = JsonRpc(Source(), Sink(), workers=2)
    def handle(method, params):
        return rpc.request("model.generate", {"messages": []}, timeout=3) if method == "tool.execute" else {"stopped": True}
    reader = threading.Thread(target=rpc.serve, args=(handle,))
    reader.start()
    incoming.put(json.dumps({"jsonrpc": "2.0", "id": "host-1", "method": "tool.execute", "params": {}}))
    reverse = outgoing.get(timeout=3)
    assert reverse["method"] == "model.generate"
    incoming.put(json.dumps({"jsonrpc": "2.0", "id": reverse["id"], "result": {"content": "返回"}}))
    response = outgoing.get(timeout=3)
    assert response["id"] == "host-1" and response["result"]["content"] == "返回"
    incoming.put(None)
    reader.join(timeout=3)
    assert not reader.is_alive()


def test_reverse_callback_survives_saturated_api_and_tool_pools():
    incoming, outgoing = queue.Queue(), queue.Queue()
    class Source:
        def readline(self):
            return incoming.get(timeout=5) or ""
    class Sink:
        def write(self, line):
            outgoing.put(json.loads(line))
        def flush(self):
            pass
    rpc = JsonRpc(Source(), Sink(), workers=1)
    def handle(method, params):
        if method in {"api.dispatch", "tool.execute"}:
            return rpc.request("model.generate", {"origin": method}, timeout=3)
        return {"authorized": True}
    reader = threading.Thread(target=rpc.serve, args=(handle,))
    reader.start()
    try:
        for index, method in enumerate(("api.dispatch", "tool.execute")):
            incoming.put(json.dumps({"jsonrpc": "2.0", "id": str(index), "method": method, "params": {}}))
        requests = [outgoing.get(timeout=2), outgoing.get(timeout=2)]
        incoming.put(json.dumps({"jsonrpc": "2.0", "id": "ledger", "method": "model.authorize", "params": {}}))
        assert outgoing.get(timeout=2) == {"jsonrpc": "2.0", "id": "ledger", "result": {"authorized": True}}
        for packet in requests:
            incoming.put(json.dumps({"jsonrpc": "2.0", "id": packet["id"], "result": {"content": "已完成"}}))
        assert {outgoing.get(timeout=2)["id"], outgoing.get(timeout=2)["id"]} == {"0", "1"}
    finally:
        incoming.put(None)
        reader.join(timeout=5)
    assert not reader.is_alive()


def test_manifest_override_disables_plugins_and_validates_dependencies(monkeypatch, tmp_path):
    from app.business_worker import registry
    path = tmp_path / "manifest.json"
    enabled = {"id": "enabled", "router_modules": ["modules.auth.router"], "tools": ["current_datetime"], "dependencies": []}
    disabled = {"id": "disabled", "enabled": False, "router_modules": ["modules.workflows.router"], "tools": ["query_business_data"]}
    path.write_text(json.dumps({"schema_version": 1, "plugins": [enabled, disabled]}), encoding="utf-8")
    monkeypatch.setenv("BUSINESS_APPS_MANIFEST", str(path))
    assert registry.router_modules() == ["modules.auth.router"]
    assert registry.tool_names() == {"current_datetime"}
    enabled["dependencies"] = ["disabled"]
    path.write_text(json.dumps({"schema_version": 1, "plugins": [enabled, disabled]}), encoding="utf-8")
    with pytest.raises(ValueError, match="依赖缺失或已停用"):
        registry.catalog()


def test_database_failure_halts_dynamic_and_builtin_reads_only(worker_context, monkeypatch):
    """停止新取数，但平台内已保存的预警读取和本地工具仍可用。"""
    from app.providers.tool.base import ToolSpec
    from app.providers.tool.gateway import ToolGateway
    initial = prepare(worker_context)
    specs = [ToolSpec(name="database.query", description="隔离伪数据库", provider="database"),
             ToolSpec(name="query_inventory_data", description="需要新取数的库存", provider="builtin"),
             ToolSpec(name="view_device_alerts", description="预警", provider="builtin"),
             ToolSpec(name="current_datetime", description="时间", provider="builtin")]
    monkeypatch.setattr(ToolGateway, "list_available", lambda *args, **kwargs: specs)
    calls = []
    def invoke(self, workspace, name, arguments, ctx):
        calls.append(name)
        from app.modules.analytics.connections import connection_failure
        failure = ToolGateway._error_result(name, connection_failure(SimpleNamespace(provider="mysql"), TimeoutError("fixture timeout")))
        return (failure
                if name == "database.query" else ToolResult(status="success", data={"value": "本机伪结果"}))
    monkeypatch.setattr(ToolGateway, "execute", invoke)
    failed = tools.execute(initial["run_id"], "db-fail", 1, "database.query", {})
    assert failed["metadata"]["recovery"] == "stop_datasource"
    assert tools.execute(initial["run_id"], "builtin-blocked", 2, "query_inventory_data", {})["error_code"] == "database_access_halted"
    assert tools.execute(initial["run_id"], "dynamic-blocked", 3, "database.query", {})["error_code"] == "database_access_halted"
    assert tools.execute(initial["run_id"], "saved-alerts-allowed", 4, "view_device_alerts", {})["status"] == "success"
    assert tools.execute(initial["run_id"], "time-allowed", 5, "current_datetime", {})["status"] == "success"
    assert calls == ["database.query", "view_device_alerts", "current_datetime"]
    render_error = ToolResult(status="failed", error_code="tool_internal_error", error_summary="渲染颜色失败")
    assert tools.classification("render_document", render_error)["recovery"] == "correct_input"


def test_worker_subprocess_replies_before_stdin_eof(tmp_path):
    """真实 Windows/Linux 管道必须在 stdin 保持打开时返回首个控制响应。"""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DATABASE_URL": "sqlite:///:memory:", "APP_ENV": "test",
           "LOCAL_STORAGE_ROOT": str(tmp_path), "STORAGE_ROOT": str(tmp_path), "LOG_DIR": str(tmp_path / "logs"),
           "SCHEDULER_ENABLED": "0", "BOOTSTRAP_LYNX_REPORTS": "0", "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen([sys.executable, "-m", "app.business_worker"], cwd=root / "backend", env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    replies = queue.Queue()
    reader = threading.Thread(target=lambda: replies.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": "probe", "method": "system.describe", "params": {}}) + "\n")
        process.stdin.flush()
        response = json.loads(replies.get(timeout=15))
        assert response["id"] == "probe" and response["result"]["protocol_version"] == 2
        assert process.poll() is None
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": "stop", "method": "shutdown", "params": {}}) + "\n")
        process.stdin.flush()
        process.wait(timeout=10)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
