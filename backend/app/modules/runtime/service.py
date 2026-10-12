# -*- coding: utf-8 -*-
# Run / Message / Step / ToolCall / Artifact / Citation 的落库原语（模块 docstring 见下）。
#
# 位置：`runtime/engine.run_chat`（harness / openwebui 两个原生运行时）与
# `chat/router.py`、`chat_jobs.py` 使用 create_run / create_message / finalize_run 等原语；
# （历史）`agent.agent_run` 已休眠，不再作为执行路径（见 runtime/agent.py 头注释）。
#
# 共同约定：
# - 所有写入函数只 `flush()`（拿到 id）不提交，事务边界由调用方掌握——
#   这是"用户消息 + Run + 执行身份"能原子提交的前提；
# - 错误摘要统一截断到 4000 字符，避免超长异常文本写满列或撑爆响应；
# - 状态取值与前端约定一致：created/running/completed/failed/cancelled 与 step/tool 的
#   running/completed/failed。
"""Run lifecycle + result finalization helpers."""
from __future__ import annotations

from app.core.observability import log_event, observe

import hashlib
import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.parts import artifact_part, text_part, tool_call_part, tool_result_part
from ...ids import new_id
from ...models import (Conversation, Message, Run, RunStep, ToolCall,
                       Artifact, Citation)


# ``runs.conversation_id`` is a NOT NULL foreign key in the provisioned MySQL
# schema.  Non-chat entry points (analytics, reports, workflows, scheduled
# jobs...) still need a valid parent without appearing in the user's chat
# history, so they share one archived conversation per user/workspace.
INTERNAL_RUN_CONVERSATION_TITLE = "__test5_internal_runs__"


def _standalone_conversation(
    db: Session, *, workspace_id: str, user_id: str,
) -> Conversation:
    """为该用户的非对话 Run 取得（或创建）那条唯一的内置归档会话。

    查找条件是"标题为该常量 + 已归档"，复用同一条记录，避免每次非对话任务都新建会话；
    首次调用时创建并直接置 archived_at，因此它不会出现在用户的会话列表中。
    只 flush 不 commit，仍由调用方掌握事务。
    """
    conversation = db.execute(
        select(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.owner_user_id == user_id,
            Conversation.title == INTERNAL_RUN_CONVERSATION_TITLE,
            Conversation.archived_at.is_not(None),
        )
        .order_by(Conversation.created_at)
        .limit(1)
    ).scalar_one_or_none()
    if conversation is not None:
        return conversation

    timestamp = datetime.utcnow()
    conversation = Conversation(
        id=new_id(),
        workspace_id=workspace_id,
        owner_user_id=user_id,
        title=INTERNAL_RUN_CONVERSATION_TITLE,
        created_at=timestamp,
        updated_at=timestamp,
        archived_at=timestamp,
    )
    db.add(conversation)
    db.flush()
    return conversation


def create_run(db: Session, *, workspace_id: str, conversation_id: str, user_id: str,
               plugin_version_id: Optional[str] = None, context_snapshot: dict = None,
               model_profile: str = "reasoning") -> Run:
    """创建一条 Run（状态 created，未提交），是执行链路的起点。

    参数：`conversation_id` 为空表示非对话入口（analytics/reports/workflows/定时任务），
    此时会自动挂到内置归档会话上并在快照打 `execution_scope=standalone`——
    该标记同时被 chat_liveness 用来把非对话 Run 排除在"孤儿对话"判定之外。
    返回：已 flush（有 id）的 Run；调用方负责把 context_snapshot 补全后提交。
    """
    snapshot = dict(context_snapshot or {})
    if not conversation_id:
        conversation_id = _standalone_conversation(
            db, workspace_id=workspace_id, user_id=user_id,
        ).id
        snapshot.setdefault("execution_scope", "standalone")
    run = Run(
        id=new_id(), workspace_id=workspace_id, conversation_id=conversation_id,
        started_by_user_id=user_id, plugin_version_id=plugin_version_id,
        status="created", created_at=datetime.utcnow(),
        context_snapshot=snapshot, model_profile=model_profile,
    )
    db.add(run)
    db.flush()
    log_event("run.created", "运行记录已建立，等待事务提交", workspace_id=workspace_id,
              conversation_id=conversation_id, run_id=run.id, profile=model_profile)
    return run


def touch_conversation(db: Session, conversation_id: str) -> None:
    """刷新会话的 updated_at，使最新活动排到列表前面。不提交事务。"""
    conv = db.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.utcnow()


def next_sequence(db: Session, conversation_id: str) -> int:
    """取会话内下一个消息序号（按现有最大 sequence + 1，空会话从 1 开始）。

    注意：并发发送同一会话时该值可能重复（前端按 sequence 展示顺序），
    业务上以"同一用户顺序发送"为前提，不在此处加锁。
    """
    last = db.execute(select(Message).where(Message.conversation_id == conversation_id)
                      .order_by(Message.sequence.desc()).limit(1)).scalar_one_or_none()
    return (last.sequence + 1) if last else 1


def create_message(db: Session, *, workspace_id: str, conversation_id: str, role: str,
                   content: str, parts: Optional[list] = None) -> Message:
    """写入一条消息（默认 status=completed），返回已 flush 的对象。

    `parts` 是结构化内容（文本/工具调用/工件/追问等），前端按它渲染卡片；
    调用方如需表达失败消息，创建后改 `status`（见 chat_liveness / agent 的错误分支）。
    副作用：写 messages 并 flush，不提交。
    """
    seq = next_sequence(db, conversation_id)
    msg = Message(id=new_id(), workspace_id=workspace_id, conversation_id=conversation_id,
                  role=role, sequence=seq, content=content, status="completed",
                  created_at=datetime.utcnow(), parts_json=parts)
    db.add(msg)
    db.flush()
    return msg


def start_step(db: Session, *, workspace_id: str, run_id: str, sequence: int,
               step_type: str, name: str, input_data: dict = None) -> RunStep:
    """开始一个执行步骤（状态 running），用于轨迹可视化与排查。不提交事务。

    `sequence` 由调用方给出（agent 里用 seq() 计数并与工具调用共享编号空间）。
    """
    step = RunStep(id=new_id(), workspace_id=workspace_id, run_id=run_id, sequence=sequence,
                   step_type=step_type, name=name, status="running",
                   input_json=input_data, started_at=datetime.utcnow(), metadata_json={})
    db.add(step)
    db.flush()
    log_event("step.started", "执行步骤开始", workspace_id=workspace_id, run_id=run_id,
              step_id=step.id, step_type=step_type, step_name=name, sequence=sequence)
    return step


def complete_step(db: Session, step: RunStep, output_data: dict = None) -> None:
    """把步骤置为 completed 并记录输出（含"等待用户输入"这类非失败的成功态）。不提交事务。"""
    step.status = "completed"
    step.output_json = output_data
    step.completed_at = datetime.utcnow()
    log_event("step.completed", "执行步骤完成", run_id=step.run_id, step_id=step.id,
              step_type=step.step_type, step_name=step.name,
              duration_ms=int((step.completed_at - step.started_at).total_seconds() * 1000))


def fail_step(db: Session, step: RunStep, error_code: str, error_summary: str) -> None:
    """把步骤置为 failed；错误摘要截断到 4000 字符防止超长文本入库。不提交事务。"""
    step.status = "failed"
    step.error_code = error_code
    step.error_summary = error_summary[:4000]
    step.completed_at = datetime.utcnow()
    log_event("step.failed", "执行步骤失败", level=40, run_id=step.run_id, step_id=step.id,
              step_type=step.step_type, step_name=step.name, error_code=error_code,
              duration_ms=int((step.completed_at - step.started_at).total_seconds() * 1000))


def record_tool_call(db: Session, *, workspace_id: str, run_id: str, step_id: Optional[str],
                     tool_name: str, tool_version_id: Optional[str], arguments: dict) -> ToolCall:
    """登记一次工具调用（状态 running），返回已 flush 的行。不提交事务。

    调用方随后用 `finish_tool_call` 写结果；即使工具抛异常也应先登记再失败，
    这样轨迹里能看到"调用过但失败"，而不是整段缺失。
    """
    tc = ToolCall(id=new_id(), workspace_id=workspace_id, run_id=run_id, step_id=step_id,
                  tool_version_id=tool_version_id, tool_name=tool_name,
                  arguments_json=arguments, status="running", started_at=datetime.utcnow())
    db.add(tc)
    db.flush()
    return tc


def finish_tool_call(db: Session, tc: ToolCall, result: dict, error_code=None, error_summary=None) -> None:
    """收尾一次工具调用：有 error_code 记为 failed，否则 completed；错误摘要截断到 4000。不提交事务。"""
    tc.result_json = result
    tc.status = "failed" if error_code else "completed"
    if error_code:
        tc.error_code = error_code
    if error_summary:
        tc.error_summary = error_summary[:4000]
    tc.completed_at = datetime.utcnow()


def finalize_run(db: Session, run: Run, *, status: str, error_code=None, error_summary=None,
                 model_usage: dict = None, cost: float = None) -> None:
    """写 Run 终态：状态、completed_at 与（可选的）错误、用量、费用。

    `model_usage`/`cost` 只在本轮真正产生 token 时才传，避免把 None 覆盖掉已有统计；
    是否 cancelled 由调用方决定（如用户取消）。不提交事务。
    """
    run.status = status
    run.completed_at = datetime.utcnow()
    if error_code:
        run.error_code = error_code
    if error_summary:
        run.error_summary = error_summary[:4000]
    if model_usage:
        run.model_usage = model_usage
    if cost is not None:
        run.cost = cost
    log_event("run.finalized", "运行进入终态，等待事务提交", run_id=run.id,
              conversation_id=run.conversation_id, status=status, error_code=error_code,
              duration_ms=int((run.completed_at - (run.started_at or run.created_at)).total_seconds() * 1000))


def attach_artifacts(db: Session, run_id: str, artifacts: List[Artifact]) -> List[dict]:
    """把工件行转成消息用的 artifact part 列表（供 assistant 消息内嵌下载入口）。只读。"""
    from ...modules.artifact.service import artifact_reference
    return [artifact_part(artifact_reference(a)) for a in artifacts]


def record_citations(db: Session, *, workspace_id: str, run_id: str, message_id: Optional[str],
                     citations: List[dict]) -> None:
    """写入知识库引用（按传入顺序编号 ordinal），供回答下方的来源列表使用。

    `ordinal` 与列表顺序一致，前端据此还原引用角标；缺失的 chunk/file 允许为空（历史数据兼容）。
    不提交事务。
    """
    for i, c in enumerate(citations):
        db.add(Citation(
            id=new_id(), workspace_id=workspace_id, run_id=run_id, message_id=message_id,
            chunk_id=c.get("chunk_id"), file_id=c.get("file_id"), ordinal=i,
            label=c.get("file_name") or c.get("label") or "来源", location=c.get("location") or {},
            created_at=datetime.utcnow(),
        ))
