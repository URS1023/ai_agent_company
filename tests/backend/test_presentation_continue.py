"""验证纯继续仅恢复本会话同身份同资源的未交付 PPT，不访问业务数据库。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.common.storage import LocalStorage
from app.models import Artifact, Run
from app.modules.chat.router import SendRequest, _unfinished_presentation_for_continue
from app.modules.presentation import draft


@pytest.fixture
def resume_context(tmp_path, monkeypatch):
    """仅在内存 SQLite 创建所需两张平台表，草稿放入临时目录。"""
    engine = create_engine("sqlite:///:memory:")
    Run.__table__.create(engine)
    Artifact.__table__.create(engine)
    storage = LocalStorage(tmp_path)
    monkeypatch.setattr(draft, "get_storage", lambda: storage)
    with Session(engine) as session:
        previous = Run(id="previous", workspace_id="workspace", conversation_id="chat",
                       started_by_user_id="user", created_at=datetime(2026, 9, 22), status="completed",
                       context_snapshot={"user_request": "请制作15页PPT", "user_message_id": "message",
                                         "datasource_ids": ["selected"], "file_ids": ["file"],
                                         "knowledge_base_ids": ["knowledge"], "template_id": None,
                                         "plugin_inputs": {}, "dashboard_template_mode": "auto"})
        session.add(previous)
        session.commit()
        draft.create_draft("workspace", "previous", {"title": "合成验收", "slides": [{"title": "待写正文"}]},
                           template_id="executive-ivory", aspect_ratio="16:9")
        ctx = SimpleNamespace(db=session, workspace_id="workspace", user_id="user")
        payload = SendRequest(content="继续", datasource_ids=["selected"], file_ids=["file"],
                              knowledge_base_ids=["knowledge"])
        yield ctx, payload, previous
    engine.dispose()


def test_continue_recovers_actual_unfinished_draft(resume_context):
    ctx, payload, previous = resume_context
    assert _unfinished_presentation_for_continue(ctx, "chat", payload).id == previous.id
    draft.delete_draft("workspace", "previous")
    assert _unfinished_presentation_for_continue(ctx, "chat", payload) is None


@pytest.mark.parametrize("change", [
    {"datasource_ids": []}, {"file_ids": []}, {"knowledge_base_ids": []},
    {"template_id": "other-template"}, {"plugin_inputs": {"page_count": 20}},
    {"plugin_version_id": "other-plugin"}, {"dashboard_template_mode": "none"},
    {"mode": "deep"}, {"content": "继续，但是要改成销售报告"},
])
def test_continue_rejects_changed_resources_or_new_requirement(resume_context, change):
    ctx, payload, _ = resume_context
    changed = payload.model_copy(update=change)
    assert _unfinished_presentation_for_continue(ctx, "chat", changed) is None


def test_continue_keeps_user_workspace_and_conversation_boundaries(resume_context):
    ctx, payload, _ = resume_context
    assert _unfinished_presentation_for_continue(ctx, "other-chat", payload) is None
    for attr in ("workspace_id", "user_id"):
        foreign = SimpleNamespace(**{**vars(ctx), attr: "other"})
        assert _unfinished_presentation_for_continue(foreign, "chat", payload) is None


def test_continue_does_not_repeat_already_delivered_presentation(resume_context):
    ctx, payload, previous = resume_context
    ctx.db.add(Artifact(id="artifact", workspace_id=ctx.workspace_id, run_id=previous.id,
                        owner_user_id=ctx.user_id, type="presentation", name="合成验收.pptx",
                        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        size=1, storage_key="synthetic.pptx", metadata_json={}, status="ready",
                        created_at=datetime(2026, 9, 22)))
    ctx.db.commit()
    assert _unfinished_presentation_for_continue(ctx, "chat", payload) is None


def test_continue_does_not_revive_older_topic_or_running_task(resume_context):
    ctx, payload, previous = resume_context
    previous.status = "running"
    ctx.db.commit()
    assert _unfinished_presentation_for_continue(ctx, "chat", payload) is None
    previous.status = "completed"
    ctx.db.add(Run(id="new-topic", workspace_id=ctx.workspace_id, conversation_id="chat",
                   started_by_user_id=ctx.user_id, created_at=previous.created_at + timedelta(minutes=1),
                   status="completed", context_snapshot={"user_message_id": "new-message", "user_request": "解释采购流程"}))
    ctx.db.commit()
    assert _unfinished_presentation_for_continue(ctx, "chat", payload) is None
