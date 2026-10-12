# -*- coding: utf-8 -*-
# 会话与消息的领域服务层（模块 docstring 见下）：`chat/router.py` 只做鉴权、
# 参数校验与 HTTP 语义，真正的读写规则集中在这里，便于其他入口（runtime、测试脚本）复用。
#
# 位置与依赖：
# - 依赖 `...models`（Conversation/Message）、`chat/business_modules`（业务模块校验）、
#   `chat/models.ConversationBusinessContext`（业务模块归属）；
# - 被 `chat/router.py`、`chat/model_selection.py` 及 runtime 侧间接调用；
# - `message_out` 反向依赖 `runtime/clarification.public_question_parts` 做读取期投影。
#
# 重要约束：
# - 本模块不提交事务，由调用方（路由或创建 Run 的代码）负责 `commit`，
#   因此 `/conversations/{id}/send` 能把会话改名、用户消息与 Run 的创建放在同一事务里；
# - 会话归属校验按 `workspace_id` 过滤并排除已归档行，跨工作区 id 一律当作不存在（抛 NotFoundError）；
# - 标题/摘要仅用于展示，历史行里的占位标题在读取时按首条用户消息重算，不写回数据库。
"""Conversation / message services."""
from __future__ import annotations

from datetime import datetime
import re
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError
from ...ids import new_id
from ...models import Conversation, Message
from ...common.ordered_rows import ordered_payload_rows
from .business_modules import module_instructions
from .models import ConversationBusinessContext


# 这些标题是历史版本写入的占位值，读取时视为"未命名"，可用首条用户消息恢复出可读标题。
PLACEHOLDER_TITLES = {"", "新对话", "新聊天", "AI 对话"}


# 取正文首个有效行、去掉 Markdown 列表/标题前缀后截取第一句，用于会话标题与摘要。
# 参数：`content` 可为任意用户输入（含多行、Markdown），`max_length` 为截断长度。
# 返回：非空字符串；无有效内容时回退为"新对话"，超长时以省略号结尾。纯函数，不写库。
def first_sentence_summary(content: str, max_length: int = 42) -> str:
    """Return the first meaningful sentence for a compact conversation label."""
    lines = [line.strip() for line in str(content or "").replace("\r", "").split("\n") if line.strip()]
    if not lines:
        return "新对话"
    text = re.sub(r"^\s*(?:#{1,6}|[-*>]|\d+[.)、])\s*", "", lines[0])
    text = re.sub(r"\s+", " ", text).strip()
    sentence = re.split(r"[。！？!?；;]", text, maxsplit=1)[0].strip()
    if not sentence:
        return "新对话"
    return sentence if len(sentence) <= max_length else sentence[:max_length] + "…"


def is_placeholder_title(title: str | None) -> bool:
    """判断标题是否为历史占位值（空、"新对话"等），仅比较去空白后的字面量。"""
    return str(title or "").strip() in PLACEHOLDER_TITLES


# 仅对占位标题的会话补查首条用户消息（批量排序主键后读取内容，避免逐会话查询）。
# 返回：{conversation_id: 展示标题}；非占位标题保持原值。
# 只读，不写回 `Conversation.title`——历史数据保持原样，避免读接口产生写放大。
def conversation_display_titles(db: Session, conversations: list[Conversation]) -> dict[str, str]:
    """用每个会话的首条用户消息恢复旧占位标题，排序时不加载正文或附件 JSON。"""
    result = {row.id: str(row.title or "新对话") for row in conversations}
    unresolved = [row.id for row in conversations if is_placeholder_title(row.title)]
    if not unresolved:
        return result
    messages = ordered_payload_rows(db, Message, (
            Message.conversation_id.in_(unresolved),
            Message.role == "user",
        ), (Message.conversation_id, Message.sequence, Message.id))
    first_messages: dict[str, str] = {}
    for message in messages:
        first_messages.setdefault(message.conversation_id, message.content)
    for conversation_id in unresolved:
        result[conversation_id] = first_sentence_summary(first_messages.get(conversation_id, ""))
    return result


def create_conversation(db: Session, *, workspace_id: str, owner_user_id: str,
                        title: str = "新对话", business_module: str | None = None) -> Conversation:
    """新建会话，可选绑定业务模块。

    参数：`workspace_id`/`owner_user_id` 决定归属（后续所有读写都要匹配）；
    `business_module` 必须是 `BUSINESS_MODULES` 中的键，否则抛 ValidationError。
    返回：已 flush（拿到 id）但未提交的 Conversation。
    副作用：写 `conversations`，并在指定业务模块时写 `conversation_business_contexts`；
    先校验再插入两条记录，避免出现"有会话无模块"的半成品。
    """
    module_instructions(business_module)  # Validate before inserting either record.
    conv = Conversation(id=new_id(), workspace_id=workspace_id, owner_user_id=owner_user_id,
                        title=title, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(conv)
    db.flush()
    if business_module:
        db.add(ConversationBusinessContext(conversation_id=conv.id, module_id=business_module))
        db.flush()
    return conv


def conversation_modules(db: Session, conversation_ids: list[str]) -> dict[str, str]:
    """批量取会话的业务模块归属，返回 {conversation_id: module_id}（缺失即不在字典中）。

    供列表/详情/发送接口一次性注入模块上下文；空入参直接返回空字典以避免 IN () 空查询。
    只读。
    """
    if not conversation_ids:
        return {}
    rows = db.scalars(select(ConversationBusinessContext).where(
        ConversationBusinessContext.conversation_id.in_(conversation_ids),
    )).all()
    return {row.conversation_id: row.module_id for row in rows}


def list_conversations(db: Session, workspace_id: str, owner_user_id: str):
    """列出当前用户在本工作区未归档的会话，按最近更新倒序。

    只读；归档（`archived_at` 非空）的会话一律不返回，删除语义即"归档"。
    """
    return db.execute(select(Conversation).where(
        Conversation.workspace_id == workspace_id,
        Conversation.owner_user_id == owner_user_id,
        Conversation.archived_at.is_(None)).order_by(Conversation.updated_at.desc())).scalars().all()


def get_conversation(db: Session, workspace_id: str, conversation_id: str) -> Conversation:
    """按 id 取会话，跨越工作区或已归档时抛 NotFoundError（不泄漏存在性）。

    注意：本函数只校验工作区，不校验用户归属；调用方需自行比较 `owner_user_id`。
    """
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.workspace_id != workspace_id or conv.archived_at is not None:
        raise NotFoundError("会话不存在")
    return conv


def list_messages(db: Session, conversation_id: str, *, limit: int | None = None):
    """按消息序号返回历史；需要限额时取最新消息，避免会话无限增长拖垮读取接口。"""
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ValueError("消息历史限额必须为正整数")
    ordering = (Message.sequence.desc(), Message.id.desc()) if limit is not None else (Message.sequence, Message.id)
    rows = ordered_payload_rows(db, Message, (Message.conversation_id == conversation_id,), ordering, limit=limit)
    return list(reversed(rows)) if limit is not None else rows


def rename_conversation(db: Session, conv: Conversation, title: str) -> None:
    """改名并顺带更新 `updated_at`，使会话在列表里前置；不提交事务。"""
    conv.title = title
    conv.updated_at = datetime.utcnow()


def message_out(m: Message) -> dict:
    """把消息 ORM 行投影为对外 JSON（含 parts）。

    助手消息的 parts 先经 `runtime.clarification.public_question_parts` 过滤：
    历史里只存了"等待你的选择"文本的旧消息，会在读取时升级为可点击的选择卡片，
    因此该函数只做读取期加工，绝不回写 `parts_json`（历史记录保持原样）。
    用户消息直接透传，仅保证 parts 非空列表。
    """
    from app.modules.runtime.clarification import public_question_parts
    return {"id": m.id, "role": m.role, "sequence": m.sequence,
            "content": m.content, "status": m.status,
            "parts": public_question_parts(m.parts_json) if m.role == 'assistant' else m.parts_json or [],
            "created_at": m.created_at.isoformat() if m.created_at else None}
