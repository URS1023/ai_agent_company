"""同业务任务跨轮未知回执与自然续办；只使用隔离SQLite及内存provider。"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools
from app.models import Run, ToolCall
from test_business_worker import prepare, worker_context  # noqa: F401
from test_dsh_completion_contract import completion_provider  # noqa: F401


@pytest.mark.parametrize("provider", ["http", "mcp"])
@pytest.mark.parametrize("continuation", ["继续", "重试刚才失败的任务", "继续，把剩下的做完"])
def test_continuation_cannot_resend_unknown_external_operation(worker_context, completion_provider, db, provider, continuation):
    first = prepare(worker_context, "执行隔离操作")
    original = tools.execute(first["run_id"], "original", 1, "completion." + provider, {"value": "original"})
    assert original["error_code"] == "tool_result_unconfirmed"
    assert chat.settle(first["run_id"], "completed", "模型完成声明")["status"] == "interrupted"
    db.expire_all()
    call = db.scalars(select(ToolCall).where(ToolCall.run_id == first["run_id"])).one()
    saved = deepcopy(call.result_json)
    continued = prepare(worker_context, continuation)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    assert continued["run_id"] != first["run_id"]
    completion_provider.mode = "success"
    result = tools.execute(continued["run_id"], "new-call-id", 8, "completion." + provider, {"value": "changed"})
    assert result["error_code"] == "tool_result_unconfirmed", result
    assert result["metadata"]["replayBlocked"] is True
    assert result["metadata"]["originalToolCallId"] == call.id
    assert result["metadata"]["idempotencyKey"] == call.idempotency_key
    assert result["metadata"]["originalRunId"] == first["run_id"]
    assert "originalErrorSummary" not in result["metadata"]
    assert not result.get("data") and not result.get("artifacts")
    assert len(completion_provider.calls) == 1
    assert tools.execute(continued["run_id"], "independent", 9, "current_datetime", {})["status"] == "success"
    assert chat.settle(continued["run_id"], "completed", "独立工具完成")["status"] == "interrupted"
    db.expire_all()
    assert db.get(ToolCall, call.id).result_json == saved


def test_continuation_cannot_hide_prior_unknown_by_not_calling_tool(worker_context, completion_provider):
    first = prepare(worker_context, "执行隔离操作")
    tools.execute(first["run_id"], "original", 1, "completion.http", {"value": "original"})
    chat.settle(first["run_id"], "interrupted", "结果待核实")
    continued = prepare(worker_context, "继续")
    result = chat.settle(continued["run_id"], "completed", "没有新回执也声称已完成")
    assert result["status"] == "interrupted", result
    assert result["error"]["code"] == "tool_result_unconfirmed"
    assert len(completion_provider.calls) == 1


@pytest.mark.parametrize("content", ["重试刚才失败的任务", "继续，把剩下的做完"])
def test_explicit_back_reference_keeps_unfinished_delivery_contract(worker_context, content):
    first = prepare(worker_context, "生成3页PPT和Word")
    assert chat.settle(first["run_id"], "completed", "尚未制作")["status"] == "partial"
    continued = prepare(worker_context, content)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    assert continued["task"]["draft_id"] == first["task"]["draft_id"]
    assert continued["task"]["required_deliveries"] == first["task"]["required_deliveries"]
    assert continued["task"]["delivery_constraints"] == first["task"]["delivery_constraints"]
    assert chat.settle(continued["run_id"], "completed", "没有文件")["status"] == "partial"


@pytest.mark.parametrize("content", ["如何重试刚才失败的任务", "不要重试刚才失败的任务", "另做一个新任务"])
def test_consultation_negation_and_new_task_do_not_inherit(worker_context, content):
    first = prepare(worker_context, "生成3页PPT和Word")
    chat.settle(first["run_id"], "completed", "未生成")
    second = prepare(worker_context, content)
    assert second["task"]["task_id"] != first["task"]["task_id"]


def test_independent_task_keeps_existing_external_execution_permission(worker_context, completion_provider):
    first = prepare(worker_context, "执行隔离操作")
    tools.execute(first["run_id"], "original", 1, "completion.http", {"value": "original"})
    chat.settle(first["run_id"], "interrupted", "待核实")
    second = prepare(worker_context, "另一项独立操作")
    assert second["task"]["task_id"] != first["task"]["task_id"]
    completion_provider.mode = "success"
    assert tools.execute(second["run_id"], "independent", 2, "completion.http", {"value": "new"})["status"] == "success"
    assert chat.settle(second["run_id"], "completed", "完成独立要求")["status"] == "completed"
    assert len(completion_provider.calls) == 2


def test_confirmed_validation_failure_can_retry_in_same_task(worker_context, completion_provider):
    first = prepare(worker_context, "执行隔离操作")
    assert tools.execute(first["run_id"], "invalid", 1, "completion.http", {"value": 5})["status"] == "failed"
    assert not completion_provider.calls
    chat.settle(first["run_id"], "failed", "参数错误")
    second = prepare(worker_context, "重试刚才失败的任务")
    assert second["task"]["task_id"] == first["task"]["task_id"]
    completion_provider.mode = "success"
    assert tools.execute(second["run_id"], "corrected", 2, "completion.http", {"value": "valid"})["status"] == "success"
    assert chat.settle(second["run_id"], "completed", "成功")["status"] == "completed"
    assert len(completion_provider.calls) == 1


@pytest.mark.parametrize("field", ["workspace_id", "started_by_user_id", "conversation_id"])
def test_task_identity_collision_cannot_include_foreign_run(worker_context, completion_provider, db, field):
    """受控篡改夹具制造同task标识的异域记录，内部读取仍必须保留三重归属条件。"""
    first = prepare(worker_context, "执行隔离操作")
    tools.execute(first["run_id"], "original", 1, "completion.http", {"value": "original"})
    chat.settle(first["run_id"], "interrupted", "待核实")
    continued = prepare(worker_context, "继续")
    db.expire_all()
    setattr(db.get(Run, first["run_id"]), field, "foreign-scope-fixture")
    db.commit()
    completion_provider.mode = "success"
    result = tools.execute(continued["run_id"], "independent", 2, "completion.http", {"value": "valid"})
    assert result["status"] == "success", result
    assert chat.settle(continued["run_id"], "completed", "本任务完成")["status"] == "completed"
    assert len(completion_provider.calls) == 2


def test_absent_task_identity_does_not_scan_other_runs(worker_context, completion_provider, db):
    first = prepare(worker_context, "执行隔离操作")
    tools.execute(first["run_id"], "original", 1, "completion.http", {"value": "original"})
    chat.settle(first["run_id"], "interrupted", "待核实")
    current = prepare(worker_context, "继续")
    db.expire_all()
    run = db.get(Run, current["run_id"])
    run.context_snapshot = {key: value for key, value in run.context_snapshot.items() if key != "task_id"}
    db.commit()
    completion_provider.mode = "success"
    assert tools.execute(current["run_id"], "new", 2, "completion.http", {"value": "valid"})["status"] == "success"
    assert len(completion_provider.calls) == 2


def test_legacy_unstaged_unknown_stays_unconfirmed_when_tool_is_no_longer_available(worker_context, db):
    """旧版没有provider与发送日志；已保存的未核实终态不能因换轮或工具下线被抹掉。"""
    first = prepare(worker_context, "执行隔离旧操作")
    db.add(ToolCall(id="legacy-unknown-fixture", workspace_id=first["workspace_id"], run_id=first["run_id"],
        tool_name="retired.fixture", arguments_json={}, status="running", idempotency_key="legacy-task-key"))
    db.commit()
    assert chat.settle(first["run_id"], "completed", "旧运行结束")["status"] == "interrupted"
    db.expire_all()
    saved = deepcopy(db.get(ToolCall, "legacy-unknown-fixture").result_json)
    assert saved["error_code"] == "tool_interrupted_unknown"
    continued = prepare(worker_context, "继续")
    result = chat.settle(continued["run_id"], "completed", "未核实原操作")
    assert result["status"] == "interrupted", result
    assert result["error"]["code"] == "tool_result_unconfirmed"
    db.expire_all()
    assert db.get(ToolCall, "legacy-unknown-fixture").result_json == saved
