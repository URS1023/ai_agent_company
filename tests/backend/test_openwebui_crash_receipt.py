"""执行者中断时同步收尾已有原生助手消息，保留部分结果且不覆盖别人的回复。"""
import socket
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Conversation, Message, Run, RunStep, ToolCall, RunEvent
from app.modules.runtime import service
from app.modules.runtime.chat_liveness import mark_interrupted


@pytest.mark.parametrize("cancelled", [False, True])
def test_interruption_closes_existing_assistant_and_preserves_parts(cancelled):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[model.__table__ for model in
        (Conversation, Message, Run, RunStep, ToolCall, RunEvent)])
    with Session(engine) as db:
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        conversation = Conversation(id="conversation", workspace_id="workspace", owner_user_id="user", title="合成任务",
                                    created_at=timestamp, updated_at=timestamp)
        db.add(conversation)
        db.flush()
        run = service.create_run(db, workspace_id="workspace", conversation_id=conversation.id, user_id="user")
        run.status = "running"
        reply = service.create_message(db, workspace_id="workspace", conversation_id=conversation.id,
            role="assistant", content="已完成部分分析", parts=[{"type": "artifact", "artifact_id": "saved-artifact"}])
        reply.status = "running"
        run.context_snapshot = {"chatLease": {"host": socket.gethostname(), "runId": run.id},
                                "openwebui": {"message_id": reply.id}}
        db.commit()
        code = "chat_cancelled" if cancelled else "chat_interrupted"
        assert mark_interrupted(db, run, error_code=code)
        assert reply.status == ("cancelled" if cancelled else "failed")
        assert reply.content == "已完成部分分析"
        assert reply.parts_json[0]["artifact_id"] == "saved-artifact"
        assert len(reply.parts_json) == 2
        assert not mark_interrupted(db, run, error_code=code)
        assert len(reply.parts_json) == 2
