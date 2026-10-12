"""自然续办沿用真实 Worker 任务身份；仅临时平台 SQLite，不请求模型或业务源。"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools
from app.business_worker.dispatch_state import task_datasource_halted
from app.errors import NotFoundError
from app.models import File, Run, ToolCall
from app.modules.chat_artifacts.resume import validated_resume_calls
from test_business_worker import prepare, worker_context  # noqa: F401
from test_dsh_completion_contract import completion_provider  # noqa: F401
from test_dsh_task_datasource_halt import failing_source  # noqa: F401


ORIGINAL = "制作3页PPT和Word，再制作大屏和Excel"
CONTINUE_SAVED = "继续，使用已有结果制作大屏和Excel"


def unfinished_task(worker_context):
    """通过真实收口保留未完成合同，不手工注入 task_id 或运行状态。"""
    first = prepare(worker_context, ORIGINAL)
    assert chat.settle(first["run_id"], "completed", "尚未制作交付文件")["status"] == "partial"
    return first


def test_original_saved_results_phrase_keeps_task_and_contract(worker_context):
    first = unfinished_task(worker_context)
    continued = prepare(worker_context, CONTINUE_SAVED)
    assert continued["task"]["task_id"] == first["task"]["task_id"], CONTINUE_SAVED
    assert continued["task"]["draft_id"] == first["task"]["draft_id"]
    assert continued["task"]["required_deliveries"] == first["task"]["required_deliveries"]
    assert continued["task"]["delivery_constraints"] == first["task"]["delivery_constraints"]
    assert continued["task"]["continuation_of_run_id"] == first["run_id"]


def test_new_task_after_comma_does_not_inherit(worker_context):
    first = unfinished_task(worker_context)
    content = "继续，另做一个新任务，使用刚才的结果"
    following = prepare(worker_context, content)
    assert following["task"]["task_id"] != first["task"]["task_id"], content
    assert following["task"]["draft_id"] != first["task"]["draft_id"]
    assert not following["task"]["delivery_constraints"]


def test_new_task_with_modification_does_not_inherit(worker_context):
    first = unfinished_task(worker_context)
    content = "继续，另开一个新任务，把刚才的PPT改成Word"
    following = prepare(worker_context, content)
    assert following["task"]["task_id"] != first["task"]["task_id"], content
    assert following["task"]["draft_id"] != first["task"]["draft_id"]
    assert not following["task"]["delivery_constraints"]


def test_actual_modification_of_quoted_previous_artifact_keeps_task(worker_context):
    first = unfinished_task(worker_context)
    content = "把“刚才的PPT”改成Word"
    continued = prepare(worker_context, content)
    assert continued["task"]["task_id"] == first["task"]["task_id"], content
    assert continued["task"]["draft_id"] == first["task"]["draft_id"]
    assert continued["task"]["requires_new_delivery"] is True


@pytest.mark.parametrize("content", [
    "修改《刚才的PPT》", '将"上一份Word"改成PPT', "修改《刚才的新任务指南PPT》",
])
def test_quoted_artifact_names_remain_modification_objects(worker_context, content):
    first = unfinished_task(worker_context)
    continued = prepare(worker_context, content)
    assert continued["task"]["task_id"] == first["task"]["task_id"], content
    assert continued["task"]["draft_id"] == first["task"]["draft_id"]
    assert continued["task"]["requires_new_delivery"] is True


def test_continued_explanation_question_keeps_consultation_boundary(worker_context):
    first = unfinished_task(worker_context)
    content = "继续解释刚才的问题该如何处理"
    following = prepare(worker_context, content)
    assert following["task"]["task_id"] != first["task"]["task_id"], content


@pytest.mark.parametrize("content", [
    "请继续，沿用已保存的查询结果导出Excel",
    "接着，基于现有结果生成大屏",
    "继续，不要重新取数，只用已有结果生成Excel",
    "继续，不要继续查询数据，只使用已有结果制作大屏和Excel",
    "继续原任务，不要新建任务，使用已有结果",
    "继续原任务不用新建任务，使用已有结果",
    "请继续完成上一条请求里尚未完成的产物，不要继续查询数据",
    "继续原任务，制作题为《新任务指南》的Word",
    '继续原任务，补充说明：“另开一个新任务，把刚才的PPT改成Word”',
    "继续，把剩下的做完",
    "重试刚才失败的任务",
    "继续生成刚才的Word，内容说明如何使用已有结果",
])
def test_natural_continuation_and_execution_limits_keep_original_task(worker_context, db, content):
    first = unfinished_task(worker_context)
    continued = prepare(worker_context, content)
    assert continued["task"]["task_id"] == first["task"]["task_id"], content
    assert continued["task"]["draft_id"] == first["task"]["draft_id"]
    assert continued["task"]["required_deliveries"] == first["task"]["required_deliveries"]
    assert continued["task"]["delivery_constraints"] == first["task"]["delivery_constraints"]
    db.expire_all()
    assert db.get(Run, continued["run_id"]).context_snapshot["submitted_content"] == content


@pytest.mark.parametrize("content", [
    "使用已有结果制作一份独立的新报表",
    "如何继续使用已有结果制作大屏",
    "能否继续使用已有结果",
    "请问把刚才的PPT改成Word应该怎么做",
    "不要把刚才的PPT改成Word",
    "不要继续原任务，使用已有结果另做报表",
    "继续，如何修改刚才的PPT",
    "继续，取消原任务，使用刚才的结果",
    "继续，这次是独立任务，使用刚才的结果",
    "继续，使用新任务的已有结果，生成Excel",
    "继续阅读“刚才的结果”",
    '说明这段文字：“把刚才的PPT改成Word”',
    "说明《把刚才的PPT改成Word》",
    "说明这段文字：`把刚才的PPT改成Word`",
    "继续，另开一个新任务，把“刚才的PPT”改成Word",
    "接着说明刚才的需求该如何实现",
])
def test_new_consultation_negation_and_quoted_reference_do_not_inherit(worker_context, content):
    first = unfinished_task(worker_context)
    following = prepare(worker_context, content)
    assert following["task"]["task_id"] != first["task"]["task_id"], content
    assert following["task"]["draft_id"] != first["task"]["draft_id"]


def test_quote_masking_does_not_change_contract_source(worker_context, db):
    first = unfinished_task(worker_context)
    content = "继续原任务，制作《新任务指南》5页PPT"
    continued = prepare(worker_context, content)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    assert any(row["minimum"] == 5 and "《新任务指南》" in row["source_text"]
               for row in continued["task"]["delivery_constraints"] if row["kind"] == "page_count")
    db.expire_all()
    assert db.get(Run, continued["run_id"]).context_snapshot["submitted_content"] == content


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_natural_continuation_keeps_failed_or_interrupted_task(worker_context, status):
    first = prepare(worker_context, ORIGINAL)
    assert chat.settle(first["run_id"], status, "前轮未完成")["status"] == status
    continued = prepare(worker_context, CONTINUE_SAVED)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    assert continued["task"]["delivery_constraints"] == first["task"]["delivery_constraints"]


@pytest.mark.parametrize("content", ["继续", CONTINUE_SAVED])
def test_completed_latest_task_does_not_resume_or_reach_older_task(worker_context, content):
    older = unfinished_task(worker_context)
    latest = prepare(worker_context, "解释一项独立概念")
    assert chat.settle(latest["run_id"], "completed", "说明完成")["status"] == "completed"
    following = prepare(worker_context, content)
    assert following["task"]["task_id"] not in {older["task"]["task_id"], latest["task"]["task_id"]}


@pytest.mark.parametrize("content", [
    CONTINUE_SAVED, "继续，另做一个新任务，使用刚才的结果", "继续，另开一个新任务，把刚才的PPT改成Word",
])
def test_explicit_task_identity_keeps_existing_precedence(worker_context, content):
    first = unfinished_task(worker_context)
    following = prepare(worker_context, content, task_id=first["task"]["task_id"])
    assert following["task"]["task_id"] == first["task"]["task_id"]
    assert following["task"]["draft_id"] == first["task"]["draft_id"]


def uploaded_file(client, worker_context, name):
    """通过正式平台文件入口建立选中资源，不接触业务数据源。"""
    response = client.post("/api/files", headers=worker_context[0], files={"file": (name, b"fixture", "text/plain")})
    assert response.status_code in {200, 201}, response.text
    return response.json()["id"]


def test_resource_selection_is_inherited_but_replacement_starts_new_task(worker_context, client, db):
    first_file = uploaded_file(client, worker_context, "first.txt")
    other_file = uploaded_file(client, worker_context, "other.txt")
    first = prepare(worker_context, ORIGINAL, file_ids=[first_file])
    chat.settle(first["run_id"], "failed", "尚未交付")
    continued = prepare(worker_context, CONTINUE_SAVED)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    db.expire_all()
    assert db.get(Run, continued["run_id"]).context_snapshot["file_ids"] == [first_file]
    chat.settle(continued["run_id"], "failed", "尚未交付")
    replaced = prepare(worker_context, CONTINUE_SAVED, file_ids=[other_file])
    assert replaced["task"]["task_id"] != first["task"]["task_id"]


def test_inherited_resource_is_revalidated_before_continuation(worker_context, client, db):
    identity = uploaded_file(client, worker_context, "scoped.txt")
    first = prepare(worker_context, ORIGINAL, file_ids=[identity])
    chat.settle(first["run_id"], "failed", "尚未交付")
    db.expire_all()
    # 仅改变隔离夹具的资源归属；真实 prepare 必须再次检查继承资源的当前工作区。
    db.get(File, identity).workspace_id = "foreign-scope-fixture"
    db.commit()
    with pytest.raises(NotFoundError):
        prepare(worker_context, CONTINUE_SAVED)


@pytest.mark.parametrize("provider", ["http", "mcp"])
@pytest.mark.parametrize("replay", [False, True])
def test_saved_results_phrase_preserves_unknown_receipt_and_final_state(worker_context, completion_provider, db, provider, replay):
    first = prepare(worker_context, "执行隔离操作")
    assert tools.execute(first["run_id"], "known", 1, "current_datetime", {})["status"] == "success"
    original = tools.execute(first["run_id"], "original", 2, "completion." + provider, {"value": "original"})
    assert original["error_code"] == "tool_result_unconfirmed"
    assert chat.settle(first["run_id"], "completed", "模型声称完成")["status"] == "interrupted"
    db.expire_all()
    call = db.scalars(select(ToolCall).where(ToolCall.run_id == first["run_id"], ToolCall.tool_name == "completion." + provider)).one()
    saved = deepcopy(call.result_json)
    continued = prepare(worker_context, CONTINUE_SAVED)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    db.expire_all()
    previous = validated_resume_calls(db, db.get(Run, continued["run_id"]))
    assert [row.tool_name for row in previous] == ["current_datetime"]
    completion_provider.mode = "success"
    if replay:
        result = tools.execute(continued["run_id"], "different-call", 3, "completion." + provider, {"value": "changed"})
        assert result["error_code"] == "tool_result_unconfirmed"
        assert result["metadata"]["replayBlocked"] is True
        assert result["metadata"]["idempotencyKey"] == call.idempotency_key
        assert result["metadata"]["originalToolCallId"] == call.id
        assert result["metadata"]["originalRunId"] == first["run_id"]
        assert not result.get("data") and not result.get("artifacts")
    settled = chat.settle(continued["run_id"], "completed", "本轮没有待处理操作")
    assert settled["status"] == "interrupted"
    assert settled["error"]["code"] == "tool_result_unconfirmed"
    assert len(completion_provider.calls) == 1
    db.expire_all()
    assert db.get(ToolCall, call.id).result_json == saved


@pytest.mark.parametrize("queued_before_failure", [False, True])
def test_saved_results_phrase_keeps_task_halt_without_copying_flag(worker_context, failing_source, db, queued_before_failure):
    first = prepare(worker_context, "查询设备状态")
    continued = prepare(worker_context, CONTINUE_SAVED) if queued_before_failure else None
    failed = tools.execute(first["run_id"], "failure", 1, "database.query", {})
    assert failed["metadata"]["recovery"] == "stop_datasource"
    chat.settle(first["run_id"], "failed", "来源已停止")
    continued = continued or prepare(worker_context, CONTINUE_SAVED)
    assert continued["task"]["task_id"] == first["task"]["task_id"]
    db.expire_all()
    run = db.get(Run, continued["run_id"])
    assert not run.context_snapshot.get("database_access_halted")
    assert task_datasource_halted(db, run)
    failing_source.fail = False
    for sequence, name in enumerate(("database.query", "query_inventory_data"), 2):
        assert tools.execute(run.id, "blocked-" + name, sequence, name, {})["error_code"] == "database_access_halted"
    assert tools.execute(run.id, "independent", 4, "current_datetime", {})["status"] == "success"
    assert failing_source.calls == [("database.query", first["run_id"]), ("current_datetime", run.id)]
    db.expire_all()
    assert db.get(Run, first["run_id"]).context_snapshot["database_access_halted"] is True


def test_natural_continuation_reuses_real_presentation_draft(worker_context):
    from test_presentation_request_pages import page

    first = prepare(worker_context, ORIGINAL)
    arguments = {"title": "隔离主题", "include_cover": False, "include_closing": False,
                 "slides": [page(number) for number in range(1, 4)]}
    planned = tools.execute(first["run_id"], "plan", 1, "create_presentation_plan", arguments)
    assert planned["status"] == "success", planned
    draft_id = planned["data"]["draft_id"]
    written = tools.execute(first["run_id"], "write", 2, "write_presentation_slides", {
        "draft_id": draft_id, "slides": [{**page(1), "slide_number": 1}]})
    assert written["status"] == "success", written
    assert chat.settle(first["run_id"], "completed", "仅写好第一页")["status"] == "partial"
    continued = prepare(worker_context, CONTINUE_SAVED)
    restored = tools.execute(continued["run_id"], "restore", 1, "create_presentation_plan", arguments)
    assert restored["status"] == "success", restored
    assert restored["data"]["draft_id"] == draft_id
    assert restored["data"]["written_count"] == 1
