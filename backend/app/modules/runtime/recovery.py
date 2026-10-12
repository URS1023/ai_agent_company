# -*- coding: utf-8 -*-
# 恢复"卡在非终态"的 Run：服务重启或 SSE 客户端断开可能让执行线程消失，
# 使 Run 永远停在 running（模块 docstring 见下）。
#
# 设计原则（默认非破坏性）：
# - `list_stale_runs` 只读，用于预演与审计；`recover_stale_runs` 默认 `apply=False` 也只列出；
# - 只有能证明"本机执行者已死"的 Run 才会被改写（判定见 chat_liveness.abandoned_chat_lease），
#   其余一律进 skipped 并给出原因，绝不猜测；
# - 状态迁移与租约持有在同一临界区内完成（ExitStack 保持所有已获取的租约，直到提交成功），
#   防止判定与提交之间被新执行者插入。
"""Recovery for runs stuck in a non-terminal state (service restarts / SSE
client disconnect can strand an in-flight generator thread, leaving the run
'running' forever). Run management is intentionally non-destructive by default.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...models import Run
from .chat_liveness import abandoned_chat_lease, mark_interrupted

def list_stale_runs(
    db: Session,
    *,
    workspace_id: str,
    threshold_seconds: int = 900,
    owner_ids: set[str] | None = None,
    run_ids: set[str] | None = None,
    target_run_id: str | None = None,
) -> List[Run]:
    """筛出"停留在 created/running/waiting 且已超过阈值"的 Run（只读，不判定存活）。

    参数：`threshold_seconds` 默认 900s；`owner_ids` 用于把范围限制到一组用户
    （配合 `run_ids` 兜底，使租约判定仅覆盖真正相关的 Run）；`target_run_id` 用于单条排障。
    返回：Run 列表，年龄按 `started_at`（缺失则 `created_at`）计算。
    `waiting` 也在候选内：它表示等待用户输入，超过阈值说明前端已放弃这一轮。
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=threshold_seconds)
    statement = select(Run).where(
        Run.workspace_id == workspace_id,
        Run.status.in_(["created", "running", "waiting"]),
    )
    if owner_ids is not None:
        statement = statement.where(or_(
            Run.started_by_user_id.in_(owner_ids),
            Run.id.in_(run_ids or set()),
        ))
    if target_run_id is not None:
        statement = statement.where(Run.id == target_run_id)
    rows = db.scalars(statement).all()
    stale = []
    for r in rows:
        ref = r.started_at or r.created_at
        if ref and ref < cutoff:
            stale.append(r)
    return stale


def recover_stale_runs(
    db: Session,
    *,
    workspace_id: str,
    threshold_seconds: int = 900,
    apply: bool = False,
    actor: str = "system",
    owner_ids: set[str] | None = None,
    run_ids: set[str] | None = None,
    target_run_id: str | None = None,
) -> dict:
    """批量恢复卡死的 Run：默认只列出，`apply=True` 才真正改写状态。

    参数：`actor` 仅用于审计语义标记（由调用方/路由约定，本函数不写审计表）；
    `apply=False` 时对能证明已死的 Run 也只记入 items，不落库。
    返回：{'count', 'dry_run', 'items', 'skipped_count', 'skipped'}，其中 skipped 带
    `reason`（等待中/执行者仍活跃/无法确认已中断），供管理员判断是否需要人工介入。
    副作用：apply 时写 runs/run_steps/tool_calls（经 mark_interrupted，error_code=stale_run_recovered）
    并补一条用户可见的中断消息；整个批次在同一个事务里提交。
    """
    now = datetime.utcnow()
    stale = list_stale_runs(
        db, workspace_id=workspace_id, threshold_seconds=threshold_seconds,
        owner_ids=owner_ids, run_ids=run_ids,
        target_run_id=target_run_id,
    )
    items, skipped = [], []
    with ExitStack() as leases:
        for row in stale:
            item = {"run_id": row.id, "conversation_id": row.conversation_id, "status": row.status,
                    "age_s": int((now - (row.started_at or row.created_at)).total_seconds())}
            lease = abandoned_chat_lease(row)
            if lease is None:
                skipped.append({**item, "reason": "任务正在等待、执行者仍活跃或无法确认已中断"})
                continue
            leases.callback(lease.release)
            if not apply or mark_interrupted(db, row, commit=False, error_code='stale_run_recovered'):
                items.append(item)
        if apply:
            # Keep every acquired executor lease until the state transition is
            # durable. An executor cannot start between our check and commit.
            db.commit()
    return {"count": len(items), "dry_run": not apply, "items": items,
            "skipped_count": len(skipped), "skipped": skipped}
