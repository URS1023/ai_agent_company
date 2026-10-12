# 用"文件租约"判断一条对话 Run 到底是"还在跑"还是"执行者已经死了"（模块 docstring 见下）。
#
# 为什么需要它：对话执行在分离线程里，若进程被重启/崩溃，Run 记录会永远停在 running，
# 前端就会一直转圈。仅凭数据库状态无法区分"正常执行中"与"孤儿"，因此引入本地文件锁：
# - 执行者启动时持有 `<PROJECT_ROOT>/.implementation/chat_leases/<run_id>.lock` 并在快照写
#   `chatLease.host/runId`（见 chat/router.py 的 send 与 chat_jobs.start_job）；
# - 只有当"租约能立即抢到 且 快照里的 host 是本机 且 runId 与 Run 一致"时才判定为孤儿。
#
# 重要边界（宁可漏判也不能误判）：
# - 其他服务器写入的 chatLease 一律不处理——本机锁证明不了远端执行者已死；
# - 升级前的历史 Run 没有 chatLease 字段，同样不处理，交给运维显式维护，
#   以免把正在别处执行的 Run 误标为中断；
# - `execution_scope == "standalone"`（非对话入口的 Run）不在此模块管辖范围内。
"""Per-chat kernel leases distinguish active work from abandoned run records."""
import logging
import re
import socket
from datetime import datetime

from sqlalchemy import select, update

from app.config import PROJECT_ROOT
from app.models import Message, Run, RunStep, ToolCall
from app.modules.system_tools.scheduler_lease import SchedulerLease

logger = logging.getLogger(__name__)


def chat_lease(run_id):
    """取某条 Run 的执行租约（进程间互斥的文件锁）。

    参数：`run_id` 必须是 `[A-Za-z0-9_-]{1,100}`，否则抛 ValueError——
    该 id 会被拼进文件路径，先做白名单校验以避免路径穿越。
    返回：SchedulerLease（未获取；调用方需自己 `acquire()`/`release()`）。
    """
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id):
        raise ValueError('Invalid run identity')
    return SchedulerLease(PROJECT_ROOT / '.implementation' / 'chat_leases' / (run_id + '.lock'))


def conversation_send_lease(conversation_id):
    """Serialize local send admission without holding a SQL transaction open."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', conversation_id):
        raise ValueError('Invalid conversation identity')
    return SchedulerLease(PROJECT_ROOT / '.implementation' / 'chat_leases' /
                          ('conversation-' + conversation_id + '.lock'))


# 在会话里补一条"本次处理已中断"的助手消息，让用户下次打开会话能看到失败凭据。
# 跳过条件（都是为了避免污染正常对话）：
# - 非对话入口（`execution_scope == standalone`）或从未建立过 chatLease 的 Run；
# - 该 Run 已不是会话里最新的 Run（用户已经在下一轮继续，旧中断不应再插话）；
# - 会话最后一条不是用户消息（说明已有回复落库，无需重复提示）。
# 副作用：写 messages（status=failed，parts 带 run_error 便于前端展示错误卡片）、更新会话时间。
def _persist_interrupted_reply(db, run, message):
    """Keep a visible receipt after a worker crash, without rewriting newer turns."""
    conversation_id = getattr(run, 'conversation_id', None)
    snapshot = run.context_snapshot or {}
    if not conversation_id or snapshot.get('execution_scope') == 'standalone' or not snapshot.get('chatLease'):
        return
    latest = db.scalar(select(Run.id).where(
        Run.conversation_id == conversation_id, Run.workspace_id == run.workspace_id,
    ).order_by(Run.created_at.desc(), Run.id.desc()).limit(1))
    if latest != run.id:
        return
    last_message = db.scalar(select(Message).where(
        Message.conversation_id == conversation_id, Message.workspace_id == run.workspace_id,
    ).order_by(Message.sequence.desc()).limit(1))
    if last_message is None or last_message.role != 'user':
        return
    from app.modules.runtime.service import create_message, touch_conversation
    reply = create_message(db, workspace_id=run.workspace_id, conversation_id=conversation_id,
                           role='assistant', content=message, parts=[{'type': 'text', 'text': message},
                           {'type': 'run_error', 'run_id': run.id, 'error_code': run.error_code}])
    reply.status = 'failed'
    touch_conversation(db, conversation_id)


def mark_interrupted(db, run, *, commit=True, error_code='chat_interrupted'):
    """把一条非终态 Run 标记为失败，并连带清理其未完成的步骤/工具调用。

    参数：`commit=False` 时只 flush，交由调用方在同一事务里提交（批量恢复场景）；
    `error_code` 用于区分普通中断与运维恢复（`stale_run_recovered`）。
    返回：True 表示确实完成了状态迁移；False 表示 Run 已不在 created/running（已被别处终结）。
    并发处理：用带 `status in (created, running)` 条件的 UPDATE 抢占，`rowcount != 1` 说明
    另一个执行者已经先提交，此时刷新 ORM 对象并放弃——避免把已完成的 Run 改写成失败。
    副作用：写 runs（status/error_code/error_summary/completed_at）、run_steps、tool_calls，
    并补一条用户可见的中断消息（见 `_persist_interrupted_reply`）。
    """
    if run.status not in {'created', 'running'}:
        return False
    completed_at = datetime.utcnow()
    cancelled = error_code == 'chat_cancelled'
    message = ('已停止本次任务，完成的结果已保留。' if cancelled else
               '本次处理已中断，已完成的结果仍然保留。')
    terminal_status = 'cancelled' if cancelled else 'failed'
    # A loaded ORM object may predate another executor's final commit.
    changed = db.execute(update(Run).where(
        Run.id == run.id, Run.workspace_id == run.workspace_id,
        Run.status.in_(['created', 'running']),
    ).values(status=terminal_status, error_code=error_code, error_summary=message,
             completed_at=completed_at).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.refresh(run)
        return False
    run.status = terminal_status
    run.error_code = error_code
    run.error_summary = message
    run.completed_at = completed_at
    if (run.context_snapshot or {}).get("chat_engine") == "dsh":
        # Worker 已停止的恢复边界保留发送阶段，不用通用 failed 覆盖未知副作用证据。
        from app.business_worker.dispatch_state import reconcile_dispatches
        reconcile_dispatches(db, run)
    for model in (RunStep, ToolCall):
        db.execute(update(model).where(model.run_id == run.id, model.workspace_id == run.workspace_id,
            model.status.in_(['pending', 'running'])).values(
            status='failed', error_code=run.error_code, error_summary=run.error_summary,
            completed_at=run.completed_at))
    # 原生助手消息在生成时就已保存；进程退出后应收尾这条消息并保留附件，不能永远显示生成中。
    binding = ((run.context_snapshot or {}).get('harness') or
               (run.context_snapshot or {}).get('openwebui') or {})
    assistant = db.get(Message, binding.get('message_id')) if binding.get('message_id') else None
    if (assistant is not None and assistant.workspace_id == run.workspace_id
            and assistant.conversation_id == run.conversation_id and assistant.role == 'assistant'
            and assistant.status in {'created', 'running', 'pending'}):
        from app.core.parts import error_part
        assistant.status = terminal_status
        assistant.content = assistant.content or message
        assistant.parts_json = list(assistant.parts_json or []) + [error_part(message, error_code)]
    _persist_interrupted_reply(db, run, message)
    if commit:
        db.commit()
    else:
        db.flush()
    # 进程被强杀或失联时 started 的模型调用没有 Python 收尾，也不会再 settle：
    # 把本地状态补为 failed，并按真实事实写入用量账本（评审 R10，失败与零用量也记账）。
    _settle_dangling_model_calls(db, run, error_code)
    return True


def _settle_dangling_model_calls(db, run, error_code):
    """把孤儿 Run 下仍为 started 的模型调用收尾为 failed，并补齐用量账本。"""
    try:
        from ...models import ModelCall
        from ...modules.model.service import record_usage
        rows = db.execute(select(ModelCall).where(
            ModelCall.workspace_id == run.workspace_id, ModelCall.run_id == run.id,
            ModelCall.status.in_(["started", "pending"]),
        )).scalars().all()
        if not rows:
            return
        completed_at = datetime.utcnow()
        for call in rows:
            call.status, call.error_code, call.completed_at = "failed", error_code, completed_at
            db.add(call)
        profile_name = run.model_profile or "reasoning"
        for call in rows:
            metadata = dict(call.metadata_json or {})
            metadata["model_call_id"] = call.id
            record_usage(
                workspace_id=run.workspace_id, profile_name=profile_name,
                provider=call.provider or "harness", model=call.model or "unknown",
                capability="chat", usage=None, pricing=None, source="harness", source_id=run.id,
                status="failed", error_code=error_code, latency_ms=None, metadata=metadata,
            )
    except Exception:
        logger.exception("harness dangling model-call ledger settle failed")


# 判定"这条 Run 的执行者确实已经不在了"，判定成立时返回**已持有**的租约。
# 返回 None 的所有情形都表示"无法证明已死"，此时绝不能标记中断：
# - Run 已经是终态，或不是对话执行（standalone），或快照缺失；
# - chatLease 的 host 不是本机 / runId 与 Run 不匹配（可能是别的服务器或旧版本记录）；
# - 租约当前被别人持有（执行者还活着）。
# 为什么必须持锁返回：仅"能抢到锁"与"标记中断"之间若释放锁，会有新执行者插入的竞态，
# 因此把互斥窗口交给调用方一起闭合（迁移完成后必须 release）。
def abandoned_chat_lease(run):
    """Hold a proven local abandoned executor; unknown owners are not dead."""
    if not run or run.status not in {'created', 'running'}:
        return None
    snapshot = run.context_snapshot or {}
    if not isinstance(snapshot, dict) or snapshot.get('execution_scope') == 'standalone':
        return None
    owner = snapshot.get('chatLease') or {}
    # Other servers and pre-upgrade records require explicit maintenance.
    if not isinstance(owner, dict) or owner.get('host') != socket.gethostname() or owner.get('runId') != run.id:
        return None
    lease = chat_lease(run.id)
    if not lease.acquire():
        return None
    return lease


def recover_abandoned_chat(db, run):
    """读取接口的兜底：确认执行者已死后就地标记中断，返回是否发生了迁移（False=无需处理）。

    调用点：会话详情、Run 详情、事件重放、取消接口——都是"用户正在观察这条 Run"的时机，
    在这里做一次判定，能让界面立刻从"运行中"收敛到失败态，而不是等运维清理。
    副作用：可能写库（状态迁移 + 一条中断消息），因此调用方应保证在请求事务内并在末端提交。
    """
    # DSH 宿主按原生日志恢复队列与终态；旧进程租约不代表它的存活状态。
    if (run.context_snapshot or {}).get("execution_owner") == "dsh-host":
        return False
    lease = abandoned_chat_lease(run)
    if lease is None:
        return False
    try:
        return mark_interrupted(db, run)
    finally:
        lease.release()
