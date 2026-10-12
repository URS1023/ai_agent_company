"""用原生清单语义验证结束条件，不调用数据库或模型。"""
import pytest

from app.modules.openwebui.delivery import DeliveryIncomplete
from app.modules.openwebui.task_completion import TaskCompletionGuard


def task(content, status="pending", id="1"):
    """构造原生待办回执。"""
    return {"id": id, "content": content, "status": status}


def test_unrelated_old_tasks_do_not_block_new_answer():
    guard = TaskCompletionGuard()
    guard.set_baseline([task("旧任务")])
    guard.reconcile([task("旧任务")])
    assert guard.continuation() is None


def test_persisted_pending_task_catches_lost_event():
    guard = TaskCompletionGuard()
    guard.set_baseline([])
    guard.reconcile([task("核对巡检口径")])
    assert "核对巡检口径" in guard.continuation()
    guard.reconcile([task("核对巡检口径", "completed")])
    assert guard.continuation() is None


def test_replacing_list_does_not_remove_original_pending_work():
    guard = TaskCompletionGuard()
    guard.observe([task("生成 Word"), task("生成 Excel", id="2")])
    guard.observe([task("生成 Word", "completed")])
    assert guard.status()["missing"] == ["生成 Excel"]


def test_refining_task_content_keeps_stable_identity():
    guard = TaskCompletionGuard([task("查询数据")])
    guard.observe([task("查询设备巡检数据", "completed")])
    assert guard.status()["missing"] == []
    assert len(guard.status()["tasks"]) == 1


def test_cancelled_task_is_not_user_authorization_to_skip():
    guard = TaskCompletionGuard([task("生成 Excel", "cancelled")])
    assert guard.status()["missing"] == ["生成 Excel"]
    assert "update_task" in guard.continuation()


def test_unproductive_continuations_stop_without_success():
    guard = TaskCompletionGuard([task("生成 Excel")])
    guard.continuation()
    guard.continuation()
    with pytest.raises(DeliveryIncomplete, match="连续核验未见进展"):
        guard.continuation()


def test_long_task_with_progress_has_no_four_round_limit():
    guard = TaskCompletionGuard([task(f"步骤 {i}", id=str(i)) for i in range(9)])
    for i in range(9):
        assert guard.continuation()
        guard.observe([task(f"步骤 {i}", "completed", id=str(i))])
    assert guard.continuation() is None


def test_progress_only_answer_is_resubmitted_even_without_file_request():
    guard = TaskCompletionGuard()
    assert "最终答复" in guard.continuation(reply_errors=["progress_only_answer"])


def test_database_blocker_prevents_further_continuations():
    guard = TaskCompletionGuard([task("生成 Excel")])
    with pytest.raises(DeliveryIncomplete, match="查询超时"):
        guard.continuation(blocker="查询超时")


def test_real_clarification_without_error_summary_remains_a_blocker():
    from types import SimpleNamespace
    from app.modules.openwebui.delivery import DeliveryTracker
    from app.providers.tool.base import ToolResult
    tracker = DeliveryTracker("做一份 Excel")
    tracker.observe(None, SimpleNamespace(id="run"), "query_business_data", {},
                    ToolResult(status="failed", data={"clarification": "统计收入还是回款？"},
                               metadata={"requires_user_input": True}))
    with pytest.raises(DeliveryIncomplete, match="统计收入还是回款"):
        tracker.continuation()


def test_malformed_event_cannot_crash_guard_or_complete_pending_task():
    guard = TaskCompletionGuard([task("核对数据")])
    guard.observe([None, "wrong", {}, task("核对数据", "unexpected")])
    assert guard.status()["missing"] == ["核对数据"]
