"""库存概况结算使用本轮真实回执，目录和历史记录不能替代库存事实。"""
from types import SimpleNamespace

import pytest

from app.models import Run
from app.modules.business_ai.inventory_evidence import _simple_inventory_request, compact_inventory_message, verified_inventory_reply
from business_worker_fakes import isolated_dsh_worker
from test_inventory_query import inventory_source


REQUEST = "查看当前仓库的备品备件库存，简短说明有哪些计量单位、单位是否一致，以及库存预警结论。无需生成文件。"
UNSUPPORTED = "所有记录单位一致，物料档案 20 条，这些物料当前无库存。"


def prepare_inventory(client, headers, source, worker, content=REQUEST, cid=None):
    """使用正式 Worker 创建当前轮与已选择来源，不连接模型。"""
    cid = cid or client.post("/api/chat/conversations", headers=headers, json={}).json()["id"]
    return cid, worker.prepare(headers, cid, content, datasource_ids=[source["id"]])


def read_inventory(worker, run, source, *, name="inventory", sequence=1, **extra):
    """执行正式网关与库存查询，数据来自临时 SQLite。"""
    return worker.execute(run["run_id"], name, sequence, "query_inventory_data",
                          {"datasource_id": source["id"], "kind": "inventory", **extra})


def test_inventory_final_reply_replaces_stream_and_ignores_master_data_counts(
    client, administrator, db, inventory_source, isolated_dsh_worker,
):
    worker = isolated_dsh_worker
    cid, run = prepare_inventory(client, administrator, inventory_source, worker)
    result = read_inventory(worker, run, inventory_source)
    assert result["status"] == "success", result
    for index, kind in enumerate(("warehouses", "materials"), start=2):
        extra = worker.execute(run["run_id"], kind, index, "query_inventory_data",
                               {"datasource_id": inventory_source["id"], "kind": kind})
        assert extra["status"] == "success", extra
    worker.append_event(run["run_id"], "message.delta", {"delta": UNSUPPORTED})
    settled = worker.settle(run["run_id"], "completed", UNSUPPORTED)
    event = worker.append_event(run["run_id"], "run.completed", {"status": settled["status"], "content": settled["content"]})
    detail = worker.history(administrator, cid)
    answer = settled["content"]
    assert settled["status"] == "completed"
    assert answer == detail["messages"][-1]["content"] == event["data"]["content"]
    assert "本页快照" in answer and "6 条库存记录" in answer and "不作为全库总量" in answer
    assert "升 2 条" in answer and "千克 2 条" in answer and "单位未知 2 条" in answer
    assert "同一记录的库存单位与物料单位" in answer and "一致 3 条" in answer and "未知 3 条" in answer
    assert "4 条缺少安全库存阈值" in answer and "4 条为未知" in answer
    assert "这些物料当前无库存" not in answer and "所有记录单位一致" not in answer
    assert "物料档案" not in answer and "仓库目录" not in answer


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_failed_current_inventory_read_cannot_reuse_earlier_success(
    client, administrator, inventory_source, isolated_dsh_worker, state,
):
    worker = isolated_dsh_worker
    cid, run = prepare_inventory(client, administrator, inventory_source, worker)
    assert read_inventory(worker, run, inventory_source)["status"] == "success"
    invalid = read_inventory(worker, run, inventory_source, name="invalid", sequence=2, page_size=0)
    assert invalid["status"] == "failed"
    error = {"code": "model_unavailable", "message": "模型暂不可用"} if state == "failed" else None
    settled = worker.settle(run["run_id"], state, UNSUPPORTED, error)
    assert settled["status"] == ("partial" if state == "completed" else state)
    assert "本轮库存读取尚未完成" in settled["content"] and UNSUPPORTED not in settled["content"]
    if error:
        assert settled["error"] == error
    assert worker.history(administrator, cid)["last_run"]["status"] == settled["status"]


def test_inventory_history_receipt_is_not_current_evidence(client, administrator, inventory_source, isolated_dsh_worker):
    worker = isolated_dsh_worker
    cid, first = prepare_inventory(client, administrator, inventory_source, worker)
    assert read_inventory(worker, first, inventory_source)["status"] == "success"
    worker.settle(first["run_id"], "completed", UNSUPPORTED)
    _, second = prepare_inventory(client, administrator, inventory_source, worker, cid=cid)
    assert read_inventory(worker, second, inventory_source, name="second-invalid", page_size=0)["status"] == "failed"
    settled = worker.settle(second["run_id"], "completed", UNSUPPORTED)
    assert settled["status"] == "partial" and "本轮库存读取尚未完成" in settled["content"]
    assert "6 条库存记录" not in settled["content"]


def test_multiple_inventory_pages_are_not_collapsed_into_one_page(
    client, administrator, inventory_source, isolated_dsh_worker,
):
    worker = isolated_dsh_worker
    _, run = prepare_inventory(client, administrator, inventory_source, worker)
    assert read_inventory(worker, run, inventory_source, page_size=2)["status"] == "success"
    assert read_inventory(worker, run, inventory_source, name="next-page", sequence=2,
                          page_size=2, offset=2)["status"] == "success"
    scoped = "分别读取了两页库存，本次各页均不是全库总量。"
    settled = worker.settle(run["run_id"], "completed", scoped)
    assert settled["content"] == scoped


@pytest.mark.parametrize("with_inventory", [False, True])
def test_custom_query_success_is_not_overridden_by_inventory_template(
    client, administrator, inventory_source, isolated_dsh_worker, with_inventory,
):
    worker = isolated_dsh_worker
    _, run = prepare_inventory(client, administrator, inventory_source, worker)
    if with_inventory:
        assert read_inventory(worker, run, inventory_source)["status"] == "success"
    result = worker.execute(run["run_id"], "custom-query", 2, "query_business_data", {
        "datasource_id": inventory_source["id"], "mode": "sql",
        "query": "SELECT spInventoryId FROM t_spinventory WHERE isDeleted=0 ORDER BY spInventoryId",
        "task": "查询当前库存记录标识，保留本次自定义查询结果",
        "max_rows": 10,
    })
    assert result["status"] == "success", result
    answer = "已按用户指定的自定义条件查询，当前查询返回 6 条库存记录。"
    settled = worker.settle(run["run_id"], "completed", answer)
    assert settled["status"] == "completed" and settled["content"] == answer


def test_no_inventory_attempt_preserves_source_clarification(client, administrator, inventory_source, isolated_dsh_worker):
    worker = isolated_dsh_worker
    _, run = prepare_inventory(client, administrator, inventory_source, worker)
    answer = "请明确目标仓库后继续查询。"
    settled = worker.settle(run["run_id"], "completed", answer)
    assert settled["content"] == answer and settled["status"] == "completed"


def test_file_request_keeps_delivery_answer_after_inventory_success(
    client, administrator, inventory_source, isolated_dsh_worker,
):
    worker = isolated_dsh_worker
    _, run = prepare_inventory(client, administrator, inventory_source, worker, "查看库存单位，生成 Excel")
    assert read_inventory(worker, run, inventory_source)["status"] == "success"
    answer = "库存已经查询，Excel 尚未生成完成。"
    settled = worker.settle(run["run_id"], "completed", answer)
    assert settled["status"] == "partial" and settled["content"] == answer


def test_non_object_parts_do_not_crash_inventory_evidence():
    class Database:
        def scalars(self, statement):
            return []

        def get(self, model, identity):
            return SimpleNamespace(parts_json=[None, "incomplete", {"type": "tool_result", "id": "lost",
                "tool": "query_inventory_data", "result": None}])

    run = SimpleNamespace(id="current", workspace_id="workspace", context_snapshot={
        "user_request": REQUEST, "assistant_message_id": "assistant"})
    answer = verified_inventory_reply(Database(), run, UNSUPPORTED, "completed", None)
    assert answer["status"] == "partial" and "本轮库存读取尚未完成" in answer["content"]


@pytest.mark.parametrize("user_request", [
    "查看库存预警，并生成 Excel 和 PPT", "查看库存单位历史变化", "统计上月库存预警趋势",
    "查看库存预警，列出采购补货建议", "查看库存单位和设备预警",
    "逐条列出库存明细及计量单位", "查看库存概况并找出无库存物料", "对比 2026-09 库存预警",
    "解释库存预警单位的含义", "库存预警的单位应该如何设计", "查看库存预警单位的定义和实现代码",
])
def test_complex_inventory_request_keeps_normal_delivery_chain(user_request):
    class NoDatabase:
        def scalars(self, statement):
            raise AssertionError("范围不适用时不应读取或接管回执")

    run = SimpleNamespace(context_snapshot={"user_request": user_request})
    assert verified_inventory_reply(NoDatabase(), run, "原有多任务回答", "completed", None) is None


def test_explicit_deliveries_prevent_inventory_override():
    assert not _simple_inventory_request({"user_request": REQUEST,
        "business_task": {"required_deliveries": [{"type": "spreadsheet", "count": 1}]}})


def test_inventory_compact_facts_keep_mixed_units_and_unknown_thresholds():
    rows = [{"物料标识": str(index), "库存计量单位": unit, "物料计量单位": unit,
             "实际可用库存": 1, "安全库存": None} for index, unit in enumerate(("桶", "根", "桶", "根"))]
    answer = compact_inventory_message({"data": rows, "statistics": {"offset": 2}, "truncated": True})
    assert "桶 2 条" in answer and "根 2 条" in answer and "一致 4 条" in answer
    assert "不表示不同记录的单位相同" in answer and "4 条缺少安全库存阈值" in answer
    assert "从第 3 条开始" in answer and "还有后续记录" in answer
    empty = compact_inventory_message({"data": [], "statistics": {"offset": 999}})
    assert "当前页未返回记录" in empty and "不能证明全库没有库存" in empty
