"""外部未知副作用重放回归，仅用纯回执和隔离 SQLite，不发真实请求。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.business_worker.replay_guard import unconfirmed_replay_result
from app.providers.tool.base import ToolResult, ToolSpec
from app.providers.tool.side_effects import uncertain_result


def saved_call(provider="http", **changes):
    """构造 provider 回执落库后的最小记录，参数不是可信回执来源。"""
    key = "dsh:fixture-first-call"
    row = SimpleNamespace(id="persisted-first-call", run_id="run-one", tool_name="send_order",
                          status="failed", idempotency_key=key, arguments_json={"order": "1"},
                          result_json=uncertain_result(key=key, provider=provider, error_summary="远端连接中断，原结果待核实"))
    row.__dict__.update(changes)
    return row


@pytest.mark.parametrize("provider", ["http", "mcp"])
def test_unknown_provider_receipt_is_preserved_without_mutating_storage(provider):
    record = saved_call(provider)
    record.result_json.update(data={"private": "旧结果"}, artifacts=[{"id": "old-private-artifact"}])
    record.result_json["metadata"]["privateResult"] = {"value": "不可沿防重分支回放"}
    original = deepcopy(record.result_json)
    blocked = unconfirmed_replay_result(run_id="run-one", tool_name="send_order", provider=provider, previous_calls=[record])
    assert blocked.error_code == "tool_result_unconfirmed"
    assert blocked.metadata["operationOutcome"] == "uncertain"
    assert blocked.metadata["retryable"] is False
    assert blocked.metadata["replayBlocked"] is True
    assert blocked.metadata["originalToolCallId"] == record.id
    assert blocked.metadata["idempotencyKey"] == record.idempotency_key
    assert blocked.metadata["originalErrorSummary"] == original["error_summary"]
    assert blocked.data is None and blocked.artifacts == []
    assert "privateResult" not in blocked.metadata
    blocked.metadata["originalErrorSummary"] = "不得改写原始回执"
    assert record.result_json == original


@pytest.mark.parametrize("change", [
    {"run_id": "another-run"}, {"tool_name": "lookup_order"}, {"provider": "builtin"},
    {"provider": "database"}, {"provider": "sandbox"},
])
def test_other_runs_and_independent_read_tools_are_not_frozen(change):
    args = {"run_id": "run-one", "tool_name": "send_order", "provider": "http"}
    args.update(change)
    assert unconfirmed_replay_result(**args, previous_calls=[saved_call()]) is None


@pytest.mark.parametrize("metadata_change", [
    {"provider": "builtin"}, {"operationOutcome": "rejected"}, {"retryable": True},
    {"requiresVerification": False}, {"idempotencyKey": "dsh:another-call"}, {"replayBlocked": True},
])
def test_non_provider_or_unbound_metadata_cannot_claim_unknown_side_effect(metadata_change):
    record = saved_call()
    record.result_json["metadata"].update(metadata_change)
    assert unconfirmed_replay_result(run_id="run-one", tool_name="send_order", provider="http", previous_calls=[record]) is None


def test_model_arguments_and_generic_errors_do_not_become_provider_evidence():
    record = saved_call()
    record.arguments_json = {"result_json": deepcopy(record.result_json)}
    record.result_json = {"status": "failed", "error_code": "tool_internal_error", "metadata": {}}
    assert unconfirmed_replay_result(run_id="run-one", tool_name="send_order", provider="http", previous_calls=[record]) is None
    record.result_json = {"status": "failed", "error_code": "tool_result_unconfirmed", "metadata": {"outcome": "unknown"}}
    assert unconfirmed_replay_result(run_id="run-one", tool_name="send_order", provider="http", previous_calls=[record]) is None


@pytest.mark.parametrize("provider", ["http", "mcp"])
def test_worker_does_not_redispatch_unknown_operation_under_new_native_call(worker_context, monkeypatch, provider):
    """真实 Worker 经隔离 SQLite 落库；伪 provider 的执行次数必须保持一次。"""
    from app.business_worker import chat, tools
    from app.providers.tool.gateway import ToolGateway
    from test_business_worker import prepare

    first = prepare(worker_context)
    spec = ToolSpec("send_order", "隔离的外部下单工具", provider,
                    input_schema={"type": "object", "properties": {"order": {"type": "string"}}})
    spec._tool_version_id = "fixture-tool-version"
    available, resolve = ToolGateway.list_available, ToolGateway.resolve
    monkeypatch.setattr(ToolGateway, "list_available", lambda self, *args, **kwargs: available(self, *args, **kwargs) + [spec])
    monkeypatch.setattr(ToolGateway, "resolve", lambda self, workspace, name, **kwargs: spec if name == spec.name else resolve(self, workspace, name, **kwargs))
    from app.providers.tool import gateway
    provider_calls = []

    def fake_invoke(tool_spec, args, ctx):
        provider_calls.append((ctx["run_id"], ctx["idempotency_key"], deepcopy(args)))
        return ToolResult(**uncertain_result(key=ctx["idempotency_key"], provider=provider, error_summary="隔离的未知回执"))

    monkeypatch.setitem(gateway._PROVIDERS, provider, SimpleNamespace(invoke=fake_invoke))
    original = tools.execute(first["run_id"], "native-first", 100, spec.name, {"order": "1"})
    assert original["error_code"] == "tool_result_unconfirmed"
    for call_id, sequence, arguments in [
        ("native-second", 101, {"order": "1"}),
        ("native-third", 102, {"order": "2"}),
    ]:
        blocked = tools.execute(first["run_id"], call_id, sequence, spec.name, arguments)
        assert blocked["error_code"] == "tool_result_unconfirmed"
        assert blocked["metadata"]["replayBlocked"] is True
        assert blocked["metadata"]["idempotencyKey"] == original["metadata"]["idempotencyKey"]
    assert len(provider_calls) == 1
    independent = tools.execute(first["run_id"], "read-time", 103, "current_datetime", {})
    assert independent["status"] == "success"
    chat.settle(first["run_id"], "failed", "原操作结果待核实")
    following = prepare(worker_context, "下一项独立任务")
    tools.execute(following["run_id"], "new-run-first", 104, spec.name, {"order": "3"})
    assert len(provider_calls) == 2


# 复用已有隔离身份与模型夹具，不建立真实模型或业务库连接。
from test_business_worker import worker_context  # noqa: E402, F401
