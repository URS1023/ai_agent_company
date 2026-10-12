"""终态契约回归：真实 Worker/SQLite 和内存 provider，不调用模型或业务接口。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools
from app.models import Message, Run, ToolCall
from app.providers.tool.base import ToolResult, ToolSpec
from app.providers.tool.gateway import ToolGateway
from app.providers.tool.side_effects import uncertain_result
from test_business_worker import prepare, worker_context  # noqa: F401
from test_dsh_steering import submit
from test_tool_replay_authorization import replay_actor  # noqa: F401


@pytest.fixture
def completion_provider(worker_context, monkeypatch):
    """保留网关和派发日志，只以可计数内存桩代替最后的 HTTP/MCP 执行。"""
    from app.providers.tool import gateway
    state = SimpleNamespace(calls=[], mode="unknown")
    available, resolve = ToolGateway.list_available, ToolGateway.resolve
    specs = {}
    for provider in ("http", "mcp"):
        spec = ToolSpec("completion." + provider, "隔离终态验收", provider, input_schema={
            "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]})
        spec._tool_version_id = "completion-" + provider
        spec._connector_spec = {"method": "POST"}
        specs[spec.name] = spec
        def invoke(selected, args, context):
            context["_before_tool_dispatch"](provider=selected.provider, external_side_effect=True)
            state.calls.append((context["run_id"], context["idempotency_key"], deepcopy(args)))
            if state.mode == "success":
                return ToolResult(status="success", data={"confirmed": True})
            return ToolResult(**uncertain_result(key=context["idempotency_key"], provider=selected.provider,
                error_summary="隔离夹具：可能已执行，最终结果待核实"))
        monkeypatch.setitem(gateway._PROVIDERS, provider, SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(ToolGateway, "list_available", lambda self, *args, **kwargs: available(self, *args, **kwargs) + list(specs.values()))
    monkeypatch.setattr(ToolGateway, "resolve", lambda self, workspace, name, **kwargs:
        specs[name] if name in specs else resolve(self, workspace, name, **kwargs))
    return state


@pytest.mark.parametrize("provider", ["http", "mcp"])
@pytest.mark.parametrize("with_document", [False, True])
def test_saved_unknown_receipt_prevents_completed_without_redispatch(worker_context, completion_provider, db, provider, with_document):
    first = prepare(worker_context, "生成 Word 文档" if with_document else "执行隔离操作")
    run_id = first["run_id"]
    result = tools.execute(run_id, "unknown-original", 1, "completion." + provider, {"value": "original"})
    assert result["error_code"] == "tool_result_unconfirmed"
    db.expire_all()
    call = db.scalars(select(ToolCall).where(ToolCall.run_id == run_id)).one()
    assert call.status == "failed"
    original = deepcopy(call.result_json)
    blocked = tools.execute(run_id, "changed-id", 2, "completion." + provider, {"value": "changed"})
    assert blocked["metadata"]["replayBlocked"] is True
    assert tools.execute(run_id, "independent-read", 3, "current_datetime", {})["status"] == "success"
    if with_document:
        document = tools.execute(run_id, "document", 4, "render_document", {"name": "本轮已完成文档",
            "document_spec": {"title": "隔离文档", "sections": [{"title": "结果", "blocks": [{"type": "p", "text": "真实临时工件"}]}]}})
        assert document["status"] == "success"
    receipt = chat.settle(run_id, "completed", "模型声称已全部完成")
    assert receipt["status"] == "interrupted"
    assert receipt["native_status"] == "completed"
    assert receipt["error"]["code"] == "tool_result_unconfirmed"
    db.expire_all()
    run = db.get(Run, run_id)
    assert run.status == db.get(Message, first["message_id"]).status == "interrupted"
    assert run.context_snapshot["business_task"]["status"] == "interrupted"
    assert db.get(ToolCall, call.id).result_json == original
    assert len(completion_provider.calls) == 1
    assert tools.execute(run_id, "after-end", 5, "completion." + provider, {"value": "new"})["error_code"] == "run_already_settled"
    repeated = chat.settle(run_id, "completed", "迟到正文")
    assert repeated["duplicate"] and repeated["status"] == "interrupted"
    assert repeated["content"] == receipt["content"]
    assert len(completion_provider.calls) == 1


def test_corrected_validation_error_can_finish_successfully(worker_context, completion_provider):
    first = prepare(worker_context)
    failed = tools.execute(first["run_id"], "invalid", 1, "completion.http", {"value": 7})
    assert failed["status"] == "failed" and not completion_provider.calls
    completion_provider.mode = "success"
    success = tools.execute(first["run_id"], "corrected", 2, "completion.http", {"value": "valid"})
    assert success["status"] == "success" and len(completion_provider.calls) == 1
    assert chat.settle(first["run_id"], "completed", "正确完成")["status"] == "completed"


def test_recovery_identity_uses_persisted_steer_and_distinct_root_runs(worker_context):
    first = prepare(worker_context, request_id="client-A")
    chat.append_event(first["run_id"], "run.started")
    submit(worker_context, first["run_id"], request_id="steer-A-001")
    submit(worker_context, first["run_id"], request_id="steer-A-001")
    second = prepare(worker_context, "下一项任务", request_id="client-B")
    chat.settle(second["run_id"], "completed", "B结果")
    recovered = next(row for row in chat.recover()["runs"] if row["run_id"] == first["run_id"])
    identity = recovered["recovery_identity"]
    assert identity["root_request_id"] == first["run_id"]
    assert identity["steering_request_ids"] == ["steer-A-001"]
    assert second["run_id"] in identity["other_request_ids"]
    assert "client-A" not in identity["other_request_ids"]
    assert not identity["ambiguous_request_ids"]


def test_recovery_detects_steer_identity_collision_with_another_run(worker_context):
    first = prepare(worker_context)
    second = prepare(worker_context, "另一轮")
    chat.append_event(first["run_id"], "run.started")
    submit(worker_context, first["run_id"], request_id=second["run_id"])
    recovered = next(row for row in chat.recover()["runs"] if row["run_id"] == first["run_id"])
    assert second["run_id"] in recovered["recovery_identity"]["ambiguous_request_ids"]


def test_unknown_receipt_survives_loss_of_runs_view_and_tool_availability(replay_actor):
    actor = replay_actor
    actor.cache("retired.http", {}, state="failed")
    call = actor.db.get(ToolCall, "replay-call")
    call.result_json = uncertain_result(key=call.idempotency_key, provider="http", error_summary="原操作未知")
    original = deepcopy(call.result_json)
    actor.role.permissions = [value for value in actor.role.permissions if value != "runs.view"]
    actor.db.commit()
    receipt = chat.settle("replay-run", "completed", "模型声称完成")
    assert receipt["status"] == "interrupted" and receipt["error"]["code"] == "tool_result_unconfirmed"
    actor.db.expire_all()
    assert actor.db.get(ToolCall, call.id).result_json == original


@pytest.mark.parametrize("change", ["provider", "binding", "replay_block", "arguments_only", "plain_error"])
def test_untrusted_unknown_markers_do_not_become_operation_evidence(worker_context, db, change):
    first = prepare(worker_context)
    result = uncertain_result(key="fixture-call-key", provider="http", error_summary="夹具候选回执")
    arguments = {}
    if change == "provider":
        result["metadata"]["provider"] = "builtin"
    elif change == "binding":
        result["metadata"]["idempotencyKey"] = "another-key"
    elif change == "replay_block":
        result["metadata"]["replayBlocked"] = True
    elif change == "arguments_only":
        arguments = deepcopy(result)
        result = {"status": "failed", "error_code": "validation_error"}
    else:
        result["error_code"] = "validation_error"
    db.add(ToolCall(id="fixture-call", workspace_id=first["workspace_id"], run_id=first["run_id"], tool_name="fixture",
        arguments_json=arguments, result_json=result, status="failed", idempotency_key="fixture-call-key"))
    db.commit()
    assert chat.settle(first["run_id"], "completed", "独立要求已完成")["status"] == "completed"


@pytest.mark.parametrize("status", ["cancelled", "failed", "interrupted"])
def test_unknown_does_not_overwrite_existing_noncompleted_terminal(worker_context, completion_provider, status):
    first = prepare(worker_context)
    tools.execute(first["run_id"], "original", 1, "completion.http", {"value": "first"})
    receipt = chat.settle(first["run_id"], status, "保留原始终态", {"code": "original_reason", "message": "原始原因"})
    assert receipt["status"] == status and receipt["error"]["code"] == "original_reason"
    assert len(completion_provider.calls) == 1


def test_unknown_in_another_run_does_not_block_independent_completion(worker_context, completion_provider):
    first = prepare(worker_context)
    tools.execute(first["run_id"], "original", 1, "completion.http", {"value": "first"})
    second = prepare(worker_context, "另一项独立要求")
    assert chat.settle(second["run_id"], "completed", "完成独立要求")["status"] == "completed"
    assert len(completion_provider.calls) == 1
