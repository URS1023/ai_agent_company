# -*- coding: utf-8 -*-
# Run 管理入口（前缀 /api/runs）：任务列表、Run 详情与卡死任务恢复。
#
# 与 `chat/router.py` 的分工：对话路径（发送/事件流/取消/轨迹）在 chat 侧，
# 这里提供跨会话的"任务中心"视图与运维恢复能力。
#
# 权限与约束：
# - 列表与详情只返回当前用户发起的 Run；详情额外允许工作区管理员查看，
#   非所有者/非管理员统一抛 NotFoundError（不区分"不存在"与"无权"）；
# - 详情与列表会调用 `chat_liveness.recover_abandoned_chat`，把已确认死亡的执行者
#   就地标记中断，避免界面长期停留在"运行中"；
# - `recover-stale` 需要管理员，且默认 `dry_run=true`（只列出不写库）。
"""Run 管理入口（前缀 `/api/runs`）：任务列表、Run 详情与卡死任务恢复。

与 `chat/router.py` 的分工：对话路径（发送/事件流/取消/轨迹）在 chat 侧，
这里提供跨会话的"任务中心"视图与运维恢复能力。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from ...common.deps import AuthContext, get_current_context
from ...errors import NotFoundError
from ...models import Artifact, Conversation, Run, RunStep, ToolCall
from ..artifact.service import artifact_reference

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/runs —— 当前用户最近 100 条 Run 的汇总列表。需登录。

    返回每条的会话标题（会话已归档则把 conversation_id 置空，标题回退"未命名任务"）、
    步骤/工具/工件计数与耗时；计数用一次 GROUP BY 批量取，避免逐 Run 查询。
    只读。
    """
    rows = ctx.db.execute(select(Run).where(Run.workspace_id == ctx.workspace_id,
                                            Run.started_by_user_id == ctx.user_id)
                          .order_by(Run.created_at.desc()).limit(100)).scalars().all()
    run_ids = [row.id for row in rows]
    conversation_ids = list({row.conversation_id for row in rows if row.conversation_id})
    conversations = {
        row.id: row.title
        for row in ctx.db.execute(
            select(Conversation).where(
                Conversation.id.in_(conversation_ids),
                Conversation.archived_at.is_(None),
            )
        ).scalars().all()
    } if conversation_ids else {}

    def counts(model):
        """按 run_id 批量统计某张从属表（RunStep/ToolCall/Artifact）的行数，避免逐 Run 查询。"""
        if not run_ids:
            return {}
        return {
            run_id: int(count)
            for run_id, count in ctx.db.execute(
                select(model.run_id, func.count())
                .where(model.run_id.in_(run_ids))
                .group_by(model.run_id)
            ).all()
        }

    step_counts = counts(RunStep)
    tool_counts = counts(ToolCall)
    artifact_counts = counts(Artifact)
    return {"items": [{
        "id": r.id,
        "conversation_id": r.conversation_id if r.conversation_id in conversations else "",
        "status": r.status,
        "model_profile": r.model_profile, "cost": r.cost,
        "error_code": r.error_code, "error_summary": r.error_summary,
        "title": conversations.get(r.conversation_id) or "未命名任务",
        "step_count": step_counts.get(r.id, 0),
        "tool_count": tool_counts.get(r.id, 0),
        "artifact_count": artifact_counts.get(r.id, 0),
        "duration_seconds": (
            round((r.completed_at - r.started_at).total_seconds(), 1)
            if r.started_at and r.completed_at else None
        ),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    } for r in rows], "count": len(rows)}


@router.post("/recover-stale")
def recover_stale(run_id: str = None, ctx: AuthContext = Depends(get_current_context),
                  dry_run: bool = True):
    """列出/清理卡在非终态太久的 Run（默认 dry_run 不写库）。"""
    from .recovery import recover_stale_runs
    # 需要管理员：该操作会改写其他用户的 Run 状态，属于运维动作。
    if ctx.role != "admin":
        from ...errors import AuthError
        raise AuthError("需要管理员权限")
    result = recover_stale_runs(
        ctx.db, workspace_id=ctx.workspace_id, threshold_seconds=900,
        apply=not dry_run, actor=ctx.user_id,
        target_run_id=run_id,
    )
    if not dry_run:
        ctx.db.commit()
    return result


@router.get("/{run_id}")
def run_detail(run_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/runs/{id} —— 单个 Run 的完整详情（含步骤、工具调用、工件引用）。需登录。

    权限：发起者本人或工作区管理员；其余按 NotFoundError 处理。
    读取时会先做孤儿判定（`recover_abandoned_chat`），使卡死的 Run 能当场落到失败态。
    返回的 `context_snapshot` 含执行输入（用户请求、数据源、模板等），用于重放与排障。
    只读（孤儿判定例外，可能写一次中断状态）。
    """
    run = ctx.db.get(Run, run_id)
    if not run or run.workspace_id != ctx.workspace_id:
        raise NotFoundError("Run 不存在")
    if run.started_by_user_id != ctx.user_id and ctx.role != "admin":
        raise NotFoundError("Run 不存在")
    from .chat_liveness import recover_abandoned_chat
    recover_abandoned_chat(ctx.db, run)
    steps = ctx.db.execute(select(RunStep).where(RunStep.run_id == run_id)
                           .order_by(RunStep.sequence)).scalars().all()
    tool_calls = ctx.db.execute(select(ToolCall).where(ToolCall.run_id == run_id)
                                .order_by(ToolCall.started_at)).scalars().all()
    artifacts = ctx.db.execute(select(Artifact).where(Artifact.run_id == run_id)
                               .order_by(Artifact.created_at)).scalars().all()
    conversation = ctx.db.get(Conversation, run.conversation_id) if run.conversation_id else None
    public_conversation_id = (
        run.conversation_id
        if conversation is not None and conversation.archived_at is None
        else ""
    )
    return {
        "run": {"id": run.id, "conversation_id": public_conversation_id,
                "status": run.status, "model_usage": run.model_usage,
                "cost": run.cost, "error_summary": run.error_summary,
                "context_snapshot": run.context_snapshot,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None},
        "steps": [{"id": s.id, "sequence": s.sequence, "step_type": s.step_type, "name": s.name,
                   "status": s.status, "input": s.input_json, "output": s.output_json,
                   "error_summary": s.error_summary} for s in steps],
        "tool_calls": [{"id": t.id, "tool_name": t.tool_name, "status": t.status,
                        "arguments": t.arguments_json, "result": t.result_json,
                        "error_summary": t.error_summary} for t in tool_calls],
        "artifacts": [artifact_reference(a) for a in artifacts],
    }
