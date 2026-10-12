"""使用内存 SQLite 验证通用续办的原始请求、成功回执和资源隔离。"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.common.storage import LocalStorage
from app.models import Artifact, Run, ToolCall
from app.modules.chat.router import SendRequest
from app.modules.openwebui.resume import (
    is_continue_request, original_request, resume_snapshot,
    unfinished_task_for_continue, validated_resume_calls,
)


@pytest.fixture
def resume_case(tmp_path, monkeypatch):
    """创建一次性平台表；不导入或连接真实数据库，不启动后台执行线程。"""
    from app.modules.presentation import draft
    monkeypatch.setattr(draft, "get_storage", lambda: LocalStorage(tmp_path))
    engine = create_engine("sqlite:///:memory:")
    for model in (Run, ToolCall, Artifact):
        model.__table__.create(engine)
    with Session(engine) as db:
        previous = Run(id="previous", workspace_id="workspace", conversation_id="chat",
                       started_by_user_id="user", status="failed", created_at=datetime(2026, 9, 22),
                       context_snapshot={
                           "user_request": "分析设备未巡检情况并导出Word和Excel", "user_message_id": "message",
                           "openwebui": {"chat_id": "native-chat"}, "datasource_ids": ["selected"],
                           "file_ids": ["file"], "knowledge_base_ids": ["knowledge"],
                           "plugin_inputs": {}, "dashboard_template_mode": "auto",
                           "openwebui_tasks": [{"id": "one", "content": "查询巡检", "status": "completed"},
                                               {"id": "two", "content": "导出Excel", "status": "pending"}],
                           "delivery": {"required": {"docx": True, "xlsx": True}, "missing": ["Excel 文件"],
                                        "files": [{"id": "word", "name": "巡检报告.docx"}]},
                       })
        db.add(previous)
        db.add_all([
            ToolCall(id="query-success", workspace_id="workspace", run_id=previous.id, tool_name="query_business_data",
                     status="completed", arguments_json={"datasource_id": "selected"},
                     result_json={"status": "success", "data": {"rows": [{"未巡检": 12}]}}, completed_at=previous.created_at),
            ToolCall(id="word-success", workspace_id="workspace", run_id=previous.id, tool_name="render_document",
                     status="completed", arguments_json={}, result_json={"status": "success", "artifacts": [{"id": "word"}]},
                     completed_at=previous.created_at + timedelta(seconds=1)),
            ToolCall(id="excel-failed", workspace_id="workspace", run_id=previous.id, tool_name="export_inspection_report",
                     status="failed", arguments_json={}, result_json={"status": "error"}),
            ToolCall(id="inconsistent", workspace_id="workspace", run_id=previous.id, tool_name="execute_python",
                     status="completed", arguments_json={}, result_json={"status": "error"}),
        ])
        db.commit()
        ctx = SimpleNamespace(db=db, workspace_id="workspace", user_id="user")
        payload = SendRequest(content="继续", datasource_ids=["selected"], file_ids=["file"], knowledge_base_ids=["knowledge"])
        yield ctx, payload, previous
    engine.dispose()


def next_run(ctx, previous, run_id="resumed"):
    """模拟发送入口建立独立 Run，始终用本次资源建立快照，再加入恢复合同。"""
    snapshot = deepcopy(previous.context_snapshot or {})
    snapshot.pop("task_resume", None)
    snapshot.pop("resumed_from_run_id", None)
    snapshot.update(resume_snapshot(ctx.db, previous))
    snapshot["user_message_id"] = run_id + "-message"
    row = Run(id=run_id, workspace_id=ctx.workspace_id, conversation_id="chat", started_by_user_id=ctx.user_id,
              status="failed", created_at=previous.created_at + timedelta(minutes=1), context_snapshot=snapshot)
    ctx.db.add(row)
    ctx.db.commit()
    return row


@pytest.mark.parametrize("text", ["继续", "请继续。", "帮我继续完成", "继续生成", "接着处理吧"])
def test_continue_resumes_non_presentation_business_task(resume_case, text):
    ctx, payload, previous = resume_case
    payload.content = text
    assert unfinished_task_for_continue(ctx, "chat", payload).id == previous.id


@pytest.mark.parametrize("text", ["停止", "取消", "重试", "继续，但改成质量报告", "不要继续", "谢谢"])
def test_non_continuation_does_not_revive_stopped_or_failed_task(resume_case, text):
    ctx, payload, previous = resume_case
    previous.status = "cancelled"
    ctx.db.commit()
    payload.content = text
    assert unfinished_task_for_continue(ctx, "chat", payload) is None


def test_cancelled_task_only_resumes_after_explicit_user_continue(resume_case):
    ctx, payload, previous = resume_case
    previous.status = "cancelled"
    ctx.db.commit()
    assert unfinished_task_for_continue(ctx, "chat", payload).id == previous.id


@pytest.mark.parametrize("change", [
    {"datasource_ids": []}, {"datasource_ids": ["other-source"]}, {"file_ids": []}, {"knowledge_base_ids": []},
    {"template_id": "other"}, {"plugin_inputs": {"page_count": 20}}, {"plugin_version_id": "other"},
    {"dashboard_template_mode": "none"}, {"dashboard_template_id": "other"}, {"mode": "deep"},
])
def test_changed_resource_scope_never_inherits_progress(resume_case, change):
    ctx, payload, _ = resume_case
    assert unfinished_task_for_continue(ctx, "chat", payload.model_copy(update=change)) is None


@pytest.mark.parametrize("key", ["workspace_id", "user_id"])
def test_other_identity_cannot_resume_task(resume_case, key):
    ctx, payload, _ = resume_case
    foreign = SimpleNamespace(**{**vars(ctx), key: "other"})
    assert unfinished_task_for_continue(foreign, "chat", payload) is None


def test_other_conversation_and_latest_new_topic_block_old_task(resume_case):
    ctx, payload, previous = resume_case
    assert unfinished_task_for_continue(ctx, "other-chat", payload) is None
    row = next_run(ctx, previous, "new-topic")
    row.status = "completed"
    row.context_snapshot = {"user_message_id": "new-topic-message", "user_request": "解释设备保养流程"}
    ctx.db.commit()
    assert unfinished_task_for_continue(ctx, "chat", payload) is None


@pytest.mark.parametrize("status", ["created", "queued", "pending", "running", "stopping"])
def test_active_task_is_not_started_twice(resume_case, status):
    ctx, payload, previous = resume_case
    previous.status = status
    ctx.db.commit()
    assert unfinished_task_for_continue(ctx, "chat", payload) is None


def test_completed_task_requires_actual_unfinished_evidence(resume_case):
    ctx, payload, previous = resume_case
    previous.status = "completed"
    previous.context_snapshot = {**previous.context_snapshot, "openwebui_tasks": [], "delivery": {"missing": []},
                                 "completion_check": {"complete": False, "execution_complete": True}}
    ctx.db.commit()
    assert unfinished_task_for_continue(ctx, "chat", payload) is None
    previous.context_snapshot = {**previous.context_snapshot, "delivery": {"missing": ["Excel 文件"]}}
    ctx.db.commit()
    assert unfinished_task_for_continue(ctx, "chat", payload).id == previous.id


def test_snapshot_keeps_original_request_tasks_and_success_only_references(resume_case):
    ctx, _, previous = resume_case
    restored = resume_snapshot(ctx.db, previous)
    assert restored["user_request"] == "分析设备未巡检情况并导出Word和Excel"
    assert not is_continue_request(restored["user_request"])
    assert restored["task_resume"]["successful_tool_call_ids"] == ["query-success", "word-success"]
    assert restored["task_resume"]["delivery"]["missing"] == ["Excel 文件"]
    assert restored["openwebui_tasks"][1]["status"] == "pending"
    assert not {"chatLease", "openwebui", "status"}.intersection(restored)
    # 不复制整个工具参数或结果到快照，运行期通过授权引用读取真实回执。
    assert "rows" not in str(restored["task_resume"])
    restored["openwebui_tasks"][0]["status"] = "pending"
    assert previous.context_snapshot["openwebui_tasks"][0]["status"] == "completed"


def test_repeated_continue_retains_older_evidence_without_replaying_tools(resume_case):
    ctx, _, previous = resume_case
    resumed = next_run(ctx, previous)
    ctx.db.add(ToolCall(id="excel-success", workspace_id="workspace", run_id=resumed.id, tool_name="export_inspection_report",
                        status="completed", arguments_json={}, result_json={"status": "success", "artifacts": [{"id": "excel"}]},
                        completed_at=resumed.created_at))
    ctx.db.commit()
    twice = next_run(ctx, resumed, "resumed-again")
    assert original_request(twice.context_snapshot) == original_request(previous.context_snapshot)
    assert {call.id for call in validated_resume_calls(ctx.db, twice)} == {"query-success", "word-success", "excel-success"}
    assert set(twice.context_snapshot["task_resume"]["source_run_ids"]) == {"previous", "resumed"}
    assert len(ctx.db.scalars(select(ToolCall)).all()) == 5


@pytest.mark.parametrize("change", [
    {"started_by_user_id": "other"}, {"workspace_id": "other"}, {"conversation_id": "other"},
    {"datasource_ids": ["other"]}, {"user_request": "查询工资"},
])
def test_runtime_receipt_read_rechecks_authority_and_task_scope(resume_case, change):
    ctx, _, previous = resume_case
    resumed = next_run(ctx, previous)
    for key, value in change.items():
        if hasattr(previous, key):
            setattr(previous, key, value)
        else:
            previous.context_snapshot = {**previous.context_snapshot, key: value}
    ctx.db.commit()
    assert validated_resume_calls(ctx.db, resumed) == []


def test_forged_foreign_receipt_reference_does_not_escape_scope(resume_case):
    ctx, _, previous = resume_case
    resumed = next_run(ctx, previous)
    foreign = Run(id="foreign", workspace_id="workspace", conversation_id="chat", started_by_user_id="someone",
                  status="completed", created_at=previous.created_at, context_snapshot=deepcopy(previous.context_snapshot))
    ctx.db.add(foreign)
    ctx.db.add(ToolCall(id="foreign-call", workspace_id="workspace", run_id="foreign", tool_name="render_document",
                        status="completed", arguments_json={}, result_json={"status": "success"}))
    modified = deepcopy(resumed.context_snapshot)
    modified["task_resume"]["successful_tool_call_ids"].append("foreign-call")
    modified["task_resume"]["source_run_ids"].append("foreign")
    resumed.context_snapshot = modified
    ctx.db.commit()
    assert {call.id for call in validated_resume_calls(ctx.db, resumed)} == {"query-success", "word-success"}


@pytest.mark.parametrize("changed_scope", [False, True])
def test_send_route_preserves_visible_continue_and_original_task_snapshot(resume_case, monkeypatch, changed_scope):
    from app.modules.chat import model_selection, router as chat_router
    from app.modules.analytics import sources
    from app.modules.runtime import chat_jobs, chat_liveness

    ctx, payload, previous = resume_case
    if changed_scope:
        payload.file_ids = []
    conv = SimpleNamespace(id="chat", owner_user_id="user", workspace_id="workspace", title="设备巡检")
    monkeypatch.setattr(chat_router.chat_service, "get_conversation", lambda *_: conv)
    monkeypatch.setattr(chat_router.chat_service, "conversation_modules", lambda *_: {})
    monkeypatch.setattr(model_selection, "selected_model", lambda *_: "reasoning")
    monkeypatch.setattr(sources, "get_source", lambda *_args, **_kwargs: None)
    visible = []
    monkeypatch.setattr(chat_router, "chat_create_msg", lambda _db, _conv, _role, content, _parts:
                        (visible.append(content) or SimpleNamespace(id="new-message", sequence=2)))
    monkeypatch.setattr(chat_router, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_liveness, "chat_lease", lambda *_: SimpleNamespace(acquire=lambda: True, release=lambda: None))
    started = []
    monkeypatch.setattr(chat_jobs, "start_job", lambda run_id, *_args, **_kwargs: started.append(run_id))
    monkeypatch.setattr(chat_jobs, "replay", lambda *_: iter(()))

    response = chat_router._send_admitted("chat", payload, ctx)
    assert response.status_code == 200
    assert visible == ["继续"]
    assert len(started) == 1
    created = ctx.db.get(Run, started[0])
    if changed_scope:
        assert created.context_snapshot["user_request"] == "继续"
        assert "resumed_from_run_id" not in created.context_snapshot
        assert "task_resume" not in created.context_snapshot
    else:
        assert created.context_snapshot["user_request"] == previous.context_snapshot["user_request"]
        assert created.context_snapshot["resumed_from_run_id"] == previous.id
        assert created.context_snapshot["task_resume"]["successful_tool_call_ids"] == ["query-success", "word-success"]
    assert created.context_snapshot["datasource_ids"] == payload.datasource_ids
