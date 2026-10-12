"""Harness 适配隔离测试：真实权限网关、内存平台库和可控协议事件。"""
from __future__ import annotations

import queue
import io
import subprocess
import itertools
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Message, ModelCall, ToolCall, User, Workspace
from app.errors import NotFoundError
from app.modules.harness import context, runtime
from app.modules.harness import client as harness_client
from app.modules.harness.client import HarnessUnavailable
from app.modules.harness.projection import NativeProjection, native_usage
from app.modules.runtime.engine import selected_engine
from app.modules.runtime.service import create_run
from app.modules.runtime.task_control import TaskControl
from app.providers.tool.base import ToolResult
from app.providers.tool.base import ToolSpec
from app.providers.tool.gateway import ToolGateway
from test_operations import user_id
from test_system_foundation import activate_user, create_role, create_user
from app.modules.system.models import ResourceGrant, Role


def frame(kind, **values):
    """构造原生实时尝试帧。"""
    return {"type": "assistant-stream", "frame": {"type": kind, "attemptId": "attempt-1", **values}}


def test_attempt_settlement_replaces_text_without_duplicate_prefix():
    projection = NativeProjection()
    projection.feed({"type": "snapshot", "cursor": 3, "records": []})
    assert projection.feed(frame("start"))[0][1]["content"] == ""
    packet = frame("chunk", index=0, chunk={"type": "text-delta", "text": "已收到"})
    assert projection.feed(packet)[0][1]["delta"] == "已收到"
    assert projection.feed(packet) == []
    event = {"type": "event", "event": {"type": "assistant/message", "seq": 4,
        "data": {"message": {"content": [{"type": "text", "text": "已收到原文。"}]}}}}
    assert projection.feed(event)[0][1]["content"] == "已收到原文。"
    assert projection.feed(event) == []
    projection.feed(frame("start"))
    projection.feed(frame("chunk", index=0, chunk={"type": "text-delta", "text": "失败片段"}))
    failed = {"type": "event", "event": {"type": "assistant/attempt", "seq": 5, "data": {}}}
    assert projection.feed(failed)[0][1]["content"] == "已收到原文。"


def test_missing_live_chunk_cannot_silently_corrupt_text():
    projection = NativeProjection()
    projection.feed(frame("start"))
    with pytest.raises(HarnessUnavailable):
        projection.feed(frame("chunk", index=2, chunk={"type": "text-delta", "text": "缺失"}))


def test_model_protocol_and_cache_accounting():
    official = context.model_configuration({"base_url": "https://api.deepseek.com/v1", "model": "example", "api_key": "secret"})
    assert official["protocol"] == "deepseek-messages" and official["thinking"] == "enabled"
    assert official["baseURL"] == "https://api.deepseek.com/anthropic"
    gateway = context.model_configuration({"base_url": "http://127.0.0.1:12345/v1", "model": "fixture"})
    assert gateway["protocol"] == "openai-completions" and "reasoningEffort" not in gateway
    assert native_usage({"data": {"usage": {"inputTokens": 10, "cacheReadTokens": 20,
        "cacheWriteTokens": 5, "outputTokens": 4}}})["total_tokens"] == 39
    with pytest.raises(ValueError):
        context.model_configuration({"base_url": "http://127.0.0.1/v1", "model": "fixture", "capabilities": {"protocol": "invalid"}})


def test_business_tool_whitelist_excludes_generic_execution(monkeypatch):
    monkeypatch.setattr(settings, "HARNESS_EXTRA_TOOLS", ("approved_connector", "python_execute"))
    names = ["read_file", "python_execute", "write_file", "delete_file", "add_tool", "approved_connector", "new_unreviewed"]
    specs = [ToolSpec(name=name, description=name, provider="builtin") for name in names]
    assert set(runtime.approved_tools(specs)) == {"read_file", "approved_connector"}


def test_enterprise_context_retains_user_intent_without_forced_delivery(monkeypatch):
    run = SimpleNamespace(context_snapshot={"datasource_ids": ["source-1"]}, workspace_id="workspace", plugin_version_id=None)
    value = context.system_prompt(None, run, "先分析，确认后再做Word", ["file-1"], [])
    assert "等待确认" in value and "source-1" in value and "file-1" in value
    assert "create_tasks" not in value and "15页" not in value and "必须交付" not in value
    assert context.FACT_POLICY_VERSION in value
    assert value.index(context.BUSINESS_FACT_POLICY) < value.index("当前业务资源")


def test_engine_is_pinned_to_run_without_mid_run_fallback(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ENGINE", "harness")
    assert selected_engine(SimpleNamespace(context_snapshot={"chat_engine": "openwebui"})) == "openwebui"
    assert selected_engine() == "harness"
    monkeypatch.setattr(settings, "CHAT_ENGINE", "unknown")
    with pytest.raises(ValueError):
        selected_engine()


def test_plugin_cannot_inject_a_foreign_workspace_skill(monkeypatch):
    values = {
        (context.PluginVersion, "version"): SimpleNamespace(plugin_id="plugin", spec={"skill_refs": ["foreign-version"]}),
        (context.Plugin, "plugin"): SimpleNamespace(workspace_id="current", name="当前插件"),
        (context.SkillVersion, "foreign-version"): SimpleNamespace(skill_id="skill", spec={"instructions": "secret"}),
        (context.Skill, "skill"): SimpleNamespace(workspace_id="foreign", name="外部技能"),
    }
    db = SimpleNamespace(get=lambda model, key: values.get((model, key)))
    run = SimpleNamespace(workspace_id="current", started_by_user_id="owner")
    monkeypatch.setattr(context, "auth_context_from_tool", lambda args: object())
    monkeypatch.setattr(context, "require_resource", lambda *args: None)
    with pytest.raises(NotFoundError):
        context._plugin_instructions(db, run, "version")


def test_cold_resume_refuses_unconfirmed_tool_receipts():
    previous = SimpleNamespace(context_snapshot={"chat_engine": "harness"})
    db = SimpleNamespace(execute=lambda query: SimpleNamespace(all=lambda: [(object(), previous)]))
    run = SimpleNamespace(workspace_id="workspace", started_by_user_id="user", conversation_id="conversation", id="next-run")
    with pytest.raises(runtime.HarnessFailure) as failed:
        runtime._require_reconciled_tools(db, run)
    assert failed.value.code == "tool_result_unconfirmed"


def test_failed_process_termination_keeps_slot_until_observed_exit(monkeypatch):
    client = harness_client.HarnessClient("fixture", TaskControl())
    released, waiters = [], []
    process = SimpleNamespace(pid=12345, returncode=None, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    process.poll = lambda: process.returncode
    process.kill = lambda: None
    def wait(timeout=None):
        if process.returncode is None:
            raise subprocess.TimeoutExpired("fixture-node", timeout)
        return process.returncode
    process.wait = wait
    client.process = process
    lease = SimpleNamespace(release=lambda: released.append(True))
    client._lease = lease
    def unavailable(*args, **kwargs):
        raise harness_client.HarnessUnavailable("合成终止失败")
    monkeypatch.setattr(client, "request", unavailable)
    monkeypatch.setattr(harness_client.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(harness_client.threading, "Thread", lambda **kwargs:
                        SimpleNamespace(start=lambda: waiters.append(kwargs["target"])))
    with pytest.raises(subprocess.TimeoutExpired):
        client.close()
    assert client._lease is lease and not released
    assert not process.stdout.closed and not process.stderr.closed
    process.returncode = 0
    waiters[0]()
    assert released == [True] and client._lease is None and process.stdout.closed


@pytest.mark.parametrize("scenario", ["success", "dedupe", "revoked", "revoked_duplicate", "timeout", "transport", "cancelled", "false_complete", "quota", "ordinary_dedupe", "ordinary_revoked_duplicate", "ordinary_grant_revoke", "reused_provider_id", "compaction_failed", "compaction_unconfirmed", "running", "failed_no_code", "model_retries", "interrupted_model"])
def test_runtime_uses_real_gateway_and_only_commits_native_terminal(client, administrator, db, monkeypatch, tmp_path, scenario):
    workspace_id = administrator["X-Workspace-Id"]
    owner_id = user_id(client, administrator)
    ordinary = scenario.startswith("ordinary_")
    member_role = None
    resource_grant = None
    if ordinary:
        scenario = scenario.removeprefix("ordinary_")
        role = create_role(client, administrator, code="harness_user", scope="self",
            permissions=["home.view", "chat.view", "chat.create", "chat.execute", "files.view"])
        create_user(client, administrator, [role["id"]], username="harness-user")
        member = activate_user(client, username="harness-user")
        owner_id = user_id(client, member)
        member_role = db.get(Role, role["id"])
        if scenario == "grant_revoke":
            from app.modules.files.service import save_upload
            resource = save_upload(db, workspace_id=workspace_id, owner_user_id=user_id(client, administrator),
                filename="授权资料.txt", mime_type="text/plain", data=b"synthetic confidential")
            resource_grant = ResourceGrant(workspace_id=workspace_id, resource_type="files", resource_id=resource.id,
                                          subject_type="role", subject_id=role["id"], actions=["view"])
            db.add(resource_grant)
    actor = db.get(User, owner_id)
    run = create_run(db, workspace_id=workspace_id, conversation_id="", user_id=owner_id,
                     context_snapshot={"chat_engine": "harness"})
    db.commit()
    invocations = []
    responses = []
    ledger = []
    model_clock = itertools.count(10, .125)
    monkeypatch.setattr(runtime, "time", SimpleNamespace(perf_counter=lambda: next(model_clock), monotonic=time.monotonic))
    def record_usage(**values):
        """断言传给生产账本的状态、起点和重复调用口径；真实落库另由 API 验收覆盖。"""
        assert values["started"] < runtime.time.perf_counter()
        ledger.append(values)
    monkeypatch.setattr(runtime, "_persist_usage", record_usage)
    original_list = ToolGateway.list_available
    original_dispatch = ToolGateway._dispatch
    monkeypatch.setattr(ToolGateway, "list_available", lambda self, *args, **kwargs:
        [item for item in original_list(self, *args, **kwargs) if item.name == "list_files"])

    def dispatch(self, spec, args, name, execution_context):
        """仅替换业务最终执行器，查找与实时身份鉴权仍走生产网关。"""
        invocations.append(name)
        if scenario == "timeout":
            return ToolResult(status="failed", error_code="database_timeout", error_summary="数据库查询超时")
        if scenario == "running":
            return ToolResult(status="running", data={"job_id": "fixture-pending"})
        if scenario == "failed_no_code":
            return ToolResult(status="failed", error_summary="参数需要修正")
        if scenario == "grant_revoke":
            return original_dispatch(self, spec, args, name, execution_context)
        return ToolResult(status="success", data={"files": []})

    monkeypatch.setattr(ToolGateway, "_dispatch", dispatch)
    monkeypatch.setattr(runtime, "_profile_config", lambda *args: {
        "model": "fixture", "base_url": "http://127.0.0.1:12345/v1", "api_key": "fixture-key"})
    monkeypatch.setattr(runtime, "enforce_budget", lambda workspace_id: None)

    class FakeClient:
        """协议夹具只提供原生事实，不运行模型或外部数据源。"""

        def __init__(self, identity, control):
            self.identity, self.control = identity, control
            self.events = queue.Queue()
            self.cwd = Path(tmp_path)

        def start(self):
            pass

        def notify(self, method, params, request_id=None):
            packet = {"jsonrpc": "2.0", "method": method, "params": params}
            if request_id:
                packet["id"] = request_id
            self.events.put(packet)

        def native(self, native_frame):
            self.notify("session.frame", {"sessionId": self.identity, "frame": native_frame})

        def request(self, method, params):
            if method == "initialize":
                assert params["tools"][0]["name"] == "list_files"
                self.native({"type": "snapshot", "cursor": 0, "records": []})
            elif method == "session.prompt":
                self.notify("model.authorize", {"sessionId": self.identity}, "model-1")
                if scenario == "model_retries":
                    for index in (1, 2):
                        self.native({"type": "event", "event": {"type": "assistant/attempt", "seq": index,
                            "data": {"usage": {"inputTokens": 3, "outputTokens": 1} if index == 1 else None}}})
                        self.notify("model.authorize", {"sessionId": self.identity}, f"retry-{index}")
                if scenario in {"compaction_failed", "compaction_unconfirmed"}:
                    self.notify("model.authorize", {"sessionId": self.identity, "purpose": "compaction"}, "compaction-1")
                    if scenario == "compaction_failed":
                        self.native({"type": "event", "event": {"type": "compaction/end", "seq": 1,
                            "data": {"error": {"code": "INVALID_REQUEST", "message": "fixture-secret-must-not-leak"}}}})
                for index in (1, 2):
                    native_call = 1 if scenario in {"dedupe", "revoked_duplicate", "grant_revoke"} else index
                    provider_call = 1 if scenario == "reused_provider_id" else native_call
                    self.notify("tool.invoke", {"sessionId": self.identity, "callId": f"call-{provider_call}", "invocationSeq": native_call,
                        "name": "list_files", "arguments": {}}, f"tool-{index}")
                self.native(frame("start"))
                self.native(frame("chunk", index=0, chunk={"type": "text-delta", "text": "回答"}))
                if scenario == "transport":
                    self.notify("transport.closed", {})
                else:
                    committed_seq = 3 if scenario == "model_retries" else 2 if scenario == "compaction_failed" else 1
                    self.native({"type": "event", "event": {"type": "assistant/message", "seq": committed_seq,
                        "data": {"interrupted": scenario == "interrupted_model", "usage": {"inputTokens": 7, "outputTokens": 2},
                                 "message": {"content": [{"type": "text", "text": "已说明真实结果。"}]}}}})
                    self.notify("run.settled", {"sessionId": self.identity, "requestId": params["requestId"],
                        "status": "cancelled" if scenario in {"cancelled", "interrupted_model"} else "failed" if scenario == "quota" else "completed",
                        "error": {"code": "QUOTA", "message": "fixture-secret-must-not-leak"} if scenario == "quota" else None,
                        "cursor": 2 if scenario == "false_complete" else committed_seq})
            return {"protocolVersion": 1}

        def respond(self, request_id, result):
            responses.append((request_id, result))
            if scenario == "grant_revoke" and request_id == "tool-1":
                assert "授权资料" in str(result)
                db.delete(resource_grant)
                db.commit()
            if scenario in {"revoked", "revoked_duplicate"} and request_id == "tool-1":
                if ordinary:
                    member_role.permissions = [permission for permission in member_role.permissions if permission != "files.view"]
                else:
                    actor.status = "disabled"
                db.commit()

        def cancel(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(runtime, "HarnessClient", FakeClient)
    result = list(runtime.run_chat(db, run, db.get(Workspace, workspace_id), actor, content="先查看文件清单"))
    expected = "cancelled" if scenario in {"cancelled", "interrupted_model"} else "failed" if scenario in {"transport", "false_complete", "timeout", "quota", "running"} else "completed"
    assert run.status == expected
    assert result[-1]["type"] == ("run.completed" if expected == "completed" else "run.failed")
    assert len(invocations) == (1 if scenario in {"revoked", "revoked_duplicate", "timeout", "dedupe", "grant_revoke", "running"} else 2)
    calls = db.scalars(select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.started_at)).all()
    assert len(calls) == (1 if scenario in {"dedupe", "grant_revoke"} else 2)
    if scenario in {"revoked", "revoked_duplicate"}:
        assert calls[1].status == "failed" and calls[1].error_code == ("forbidden" if ordinary else "unauthorized")
    if scenario == "timeout":
        assert calls[1].error_code == "database_access_halted"
    if scenario == "running":
        assert calls[0].status == "failed" and calls[0].error_code == "tool_result_unconfirmed"
        assert calls[1].error_code == "database_access_halted"
    if scenario == "failed_no_code":
        assert all(call.status == "failed" and call.error_code == "tool_failed" for call in calls)
    message = db.get(Message, run.context_snapshot["harness"]["message_id"])
    assert message.status == expected and message.content
    if scenario == "quota":
        assert run.error_code == "model_balance_insufficient"
        assert "fixture-secret-must-not-leak" not in repr(result) + repr(message.parts_json)
    if scenario in {"compaction_failed", "compaction_unconfirmed"}:
        compacted = db.scalar(select(ModelCall).where(ModelCall.run_id == run.id, ModelCall.status == "failed"))
        assert compacted and compacted.completed_at
        assert compacted.error_code == ("harness_compaction_failed" if scenario == "compaction_failed" else "harness_model_result_unconfirmed")
        assert not any(event["type"] == "context.compacted" for event in result)
        assert "fixture-secret-must-not-leak" not in repr(result)
    assert not any(kind == "model-1" and "apiKey" in repr(payload) for kind, payload in responses)
    model_calls = db.scalars(select(ModelCall).where(ModelCall.run_id == run.id)).all()
    assert len(ledger) == len(model_calls)
    assert len({entry["usage_context"]["model_call_id"] for entry in ledger}) == len(ledger)
    assert all(entry["status"] == next(row.status for row in model_calls if row.id == entry["usage_context"]["model_call_id"]) for entry in ledger)
    if scenario == "model_retries":
        # 按真实事实记账（R9）：带用量的 attempt 记为完成、无用量回执的尝试记为失败、
        # 最终 message 记为完成——成功轮不再被提前误记失败，重试仍有独立失败记录。
        assert [entry["status"] for entry in ledger] == ["completed", "failed", "completed"]
        assert ledger[0]["usage"]["total_tokens"] == 4 and ledger[0].get("error_code") is None
        assert ledger[1]["status"] == "failed" and ledger[1]["usage"] is None
        assert ledger[2]["usage"]["total_tokens"] == 9 and ledger[2].get("error_code") is None
    if scenario in {"transport", "interrupted_model"}:
        assert ledger[0]["status"] == "failed"
    if ordinary:
        assert "resource_access_criteria" not in db.info
        second = next(payload for kind, payload in responses if kind == "tool-2")
        assert second["status"] == ("failed" if scenario == "revoked_duplicate" else "success")
        if scenario in {"dedupe", "grant_revoke"}:
            assert second["data"]["already_completed"] and second["metadata"]["sensitive_payload_omitted"]
            assert "授权资料" not in str(second) and "files" not in second["data"]
