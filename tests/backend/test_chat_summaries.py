"""验证会话摘要与标题的生成规则（首句摘要、旧占位标题被首条用户消息替换）。

验证行为：摘要取首个有意义行且长度受限；会话列表用首条用户提问替换「新对话」等占位标题。
隔离方式：直接调用摘要纯函数并构造 ORM 对象，走 conftest 的内存 SQLite，不调用模型、不连业务库。
边界：空内容/仅符号行不产生摘要；摘要不得超过长度上限并带省略号。
"""
from __future__ import annotations

from datetime import datetime

from app.ids import new_id
from app.models import Conversation, Message
from app.modules.chat.service import first_sentence_summary


def test_first_sentence_summary_is_compact_and_uses_first_meaningful_line():
    assert first_sentence_summary("\n  帮我分析华北销售数据。然后生成大屏") == "帮我分析华北销售数据"
    assert first_sentence_summary("# 设备预警分析\n第二行") == "设备预警分析"
    assert first_sentence_summary("一" * 60) == "一" * 42 + "…"


def test_conversation_list_replaces_old_placeholder_with_first_user_sentence(client, administrator, db):
    created = client.post("/api/chat/conversations", headers=administrator, json={"title": "新对话"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    conversation = db.get(Conversation, conversation_id)
    db.add(Message(
        id=new_id(), workspace_id=conversation.workspace_id, conversation_id=conversation_id,
        role="user", sequence=1, content="生成财务与设备数字大屏。使用真实数据",
        status="completed", created_at=datetime.utcnow(), parts_json=[],
    ))
    db.commit()

    listing = client.get("/api/chat/conversations", headers=administrator)
    assert listing.status_code == 200
    row = next(item for item in listing.json()["items"] if item["id"] == conversation_id)
    assert row["title"] == "生成财务与设备数字大屏"
    assert row["summary"] == row["title"]

    detail = client.get(f"/api/chat/conversations/{conversation_id}", headers=administrator)
    assert detail.status_code == 200
    assert detail.json()["title"] == "生成财务与设备数字大屏"
