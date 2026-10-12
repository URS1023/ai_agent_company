# 对话的"分离执行 + 可认证的持久化 SSE 重放"（模块 docstring 见下）。
#
# 为什么这样设计：请求线程只负责建单并返回事件流，真正的模型/工具循环跑在后台守护线程里，
# 因此用户关掉页面、网络抖动或换设备后仍能靠 `?after=` 续传，不会丢事件、也不会中断任务。
#
# 事件流的双通道（两者都要看，顺序以事件 id 为准）：
# - 内存缓冲 `ChatJob.packets`：当前进程正在执行的 Run，毫秒级唤醒（Condition）；
# - 数据库 `run_events`（event_type=chat.stream）：所有事件在 `_save` 里先落库再入缓冲，
#   因此进程重启后 `replay` 仍能把历史帧重新推给客户端。
#
# 事件名（data 里一律带 run_id；`id:` 行为事件自增 id，供断点续传）：
# run.created / run.started / message.delta / message.question / message.followups /
# tool.started / tool.completed / context.compacted / task.progress / run.failed /
# run.completed / done。`done` 是客户端停止监听的正规终止帧；`: keepalive` 注释帧用于保活。
"""Detached chat execution with authenticated, durable SSE replay."""
from datetime import datetime
import json
import logging
import socket
import threading
import time
from app.core.observability import current_context, log_context, log_event, observe

from sqlalchemy import select
from app.config import settings
from app.db import new_session
from app.models import Run, RunEvent, User, Workspace
from app.modules.runtime.chat_liveness import chat_lease, mark_interrupted, recover_abandoned_chat
from app.modules.runtime.task_control import TaskControl, current

logger = logging.getLogger(__name__)
# 进程内活跃任务表：run_id -> ChatJob。跨进程/重启后的状态只能靠 run_events 与租约判断。
_jobs = {}
_lock = threading.Lock()


class ChatJob:
    """一条对话 Run 在内存中的执行句柄：事件缓冲、增量聚合、进度条件变量与执行控制。

    生命周期：`start_job` 校验通过后创建并登记进 `_jobs`，执行线程结束时从 `_jobs` 移除；
    同一 run_id 同时最多一个实例（由文件租约保证）。
    职责边界：
    - 只做"事件写出 + 唤醒等待者"，不做业务判断；业务通过完整 Open WebUI 后端及工具网关执行；
    - `packets` 是**追加型**内存缓冲（不做淘汰，供 replay 按 id 过滤），因此长任务只保留
      元组引用而不再常驻整段文本；
    - `finished` 与 `changed` 配合使用：置位 finished 后必须 notify_all，
      否则 replay 的等待者会一直停在 Condition 上。
    不变式：`sequence` 单调递增且与数据库 `run_events.sequence` 对应，由 `_save` 在持锁下自增。
    """

    def __init__(self, run_id, workspace_id, conversation_id=None):
        """建立内存缓冲与控制块；默认只检查取消，不按整轮耗时终止。"""
        self.run_id, self.workspace_id = run_id, workspace_id
        self.conversation_id = conversation_id
        self.output_characters = 0
        self.output_started = False
        # 唤醒用条件变量（replay 等待新事件）与写串行化锁（emit 可能被多个线程调用）。
        self.changed = threading.Condition()
        self.write_lock = threading.RLock()
        self.finished = False
        self.sequence = 0
        self.terminal_event = None
        # message.delta 的待聚合文本：模型逐 token 输出，按 0.25s 合并成一帧，避免刷爆事件表。
        self.pending = ""
        self.pending_attempt = None
        self.packets = []
        self.last_flush = time.monotonic()
        # RUN_TIMEOUT_SECONDS belongs to other execution paths, not detached chat.
        timeout = settings.CHAT_TASK_TIMEOUT_SECONDS
        deadline = time.monotonic() + timeout if timeout > 0 else None
        self.control = TaskControl(deadline, sink=self.emit)

    def emit(self, event, data):
        """写出一条事件（同时也作为 TaskControl 的 sink 接收 task.progress）。

        增量聚合语义：`message.delta` 只追加到 `pending`，不足 0.25 秒不落库；
        任何**非 delta** 事件都会先 `flush()`，保证顺序语义（例如 tool.started 之前
        的正文一定先发出），因此 delta 的合并不会改变客户端看到的内容顺序。
        副作用：经 `_save` 写 run_events 并唤醒 replay 等待者。
        """
        with self.write_lock:
            if event == "message.delta":
                delta = str(data.get("delta") or "")
                attempt = data.get("attempt_id")
                if self.pending and attempt != self.pending_attempt:
                    self.flush()
                self.pending_attempt = attempt
                self.output_characters += len(delta)
                if delta and not self.output_started:
                    self.output_started = True
                    log_event("chat.output.started", "对话开始输出正文", run_id=self.run_id,
                              conversation_id=self.conversation_id)
                self.pending += delta
                if time.monotonic() - self.last_flush < .25:
                    return
                self.flush()
                return
            self.flush()
            labels = {"run.created": "后台对话已接管", "run.started": "开始处理对话",
                      "tool.started": "对话发起工具调用", "tool.completed": "对话收到工具结果",
                      "context.compacted": "对话上下文已压缩", "task.progress": "对话处理进度",
                      "message.question": "对话需要补充信息", "message.followups": "后续建议已生成",
                      "run.completed": "对话处理完成", "run.failed": "对话处理失败", "done": "对话事件流收尾"}
            details = {key: data[key] for key in ("status", "error_code", "tool", "tool_name", "name", "progress", "iteration")
                       if key in data and isinstance(data[key], (str, int, float, bool))}
            if event == "task.progress":
                details["progress_message"] = str(data.get("message") or "")[:400]
            # 人读日志只写用户能理解的阶段、工具名称和摘要，不写 SQL、参数或追踪 ID。
            human_label, human_content = None, None
            if event == "tool.started":
                human_label, human_content = "工具", f"开始：{data.get('tool') or data.get('tool_name') or data.get('name') or '业务工具'}"
            elif event == "tool.completed":
                human_label, human_content = "工具", (
                    f"{data.get('tool') or data.get('tool_name') or data.get('name') or '业务工具'}："
                    f"{data.get('status') or '已完成'}；{data.get('summary') or data.get('error_code') or '已完成'}")
            elif event == "task.progress":
                human_label, human_content = "进度", data.get("message") or f"已完成 {data.get('progress', 0)}%"
            elif event == "message.question":
                human_label, human_content = "AI", data.get("question") or data.get("message") or "需要补充信息"
            elif event == "message.followups":
                human_label, human_content = "AI", data.get("message") or "已生成后续建议"
            elif event == "run.failed":
                human_label, human_content = "失败", data.get("error") or data.get("error_code") or "对话处理失败"
            elif event == "run.completed":
                human_label, human_content = "状态", "对话处理完成"
            elif event in {"run.created", "run.started", "context.compacted", "done"}:
                human_label, human_content = "状态", labels.get(event, "对话节点事件")
            log_event("chat." + event, labels.get(event, "对话节点事件"),
                      level=logging.ERROR if event == "run.failed" else logging.INFO,
                      human_label=human_label, human_content=human_content,
                      run_id=self.run_id, conversation_id=self.conversation_id,
                      sequence=self.sequence + 1, output_characters=self.output_characters, **details)
            self._save(event, data)
            if event in {"run.completed", "run.failed"}:
                self.terminal_event = event

    def flush(self):
        """把累积的 delta 立即写成一条 message.delta（无内容则只刷新时间戳）。"""
        if self.pending:
            body = {"delta": self.pending}
            if self.pending_attempt is not None:
                body["attempt_id"] = self.pending_attempt
            self._save("message.delta", body)
            self.pending = ""
            self.pending_attempt = None
        self.last_flush = time.monotonic()

    def _save(self, event, data):
        """持久化一条事件后再入内存缓冲：先落库保证重启可重放，`id` 即 SSE 的 `id:` 值。

        每次写库使用独立 session（`new_session`），不占用请求事务，避免长任务把连接池占满；
        写库成功后才 notify，确保客户端被唤醒时数据一定可查。
        """
        with new_session() as db:
            self.sequence += 1
            row = RunEvent(workspace_id=self.workspace_id, run_id=self.run_id,
                event_type="chat.stream", payload={"event": event, "data": {**data, "run_id": self.run_id}},
                created_at=datetime.utcnow(), sequence=self.sequence)
            db.add(row)
            db.commit()
            packet = (row.id, row.payload)
        with self.changed:
            self.packets.append(packet)
            self.changed.notify_all()


def start_job(run_id, workspace_id, user_id, payload, *, execution_lease=None):
    """校验并启动一个对话执行线程，立即返回（调用方随后用 `replay` 推流）。

    参数：`execution_lease` 允许调用方（`chat/router.send`）把已持有的租约传进来，
    保证"抢锁 → 落库身份 → 起线程"是一段连续临界区；未传时自行 `chat_lease(run_id)` 获取。
    异常：抢不到租约抛 RuntimeError（同一 Run 已有执行者）；Run 不存在/不属于该工作区与用户/
    状态不是 created 时抛 RuntimeError（幂等保护：重复启动不会产生第二个执行者）。
    副作用：写 runs.context_snapshot 的 chatLease（host + runId）、启动守护线程，
    线程内会驱动 ``runtime.engine.run_chat`` 并把事件写进 run_events（``agent_run`` 已休眠，见其头注释）。
    失败清理：`failed_start` 负责把已占用的资源全部归还——标记 Run 中断、置 finished 并唤醒
    replay 等待者、从 `_jobs` 移除、释放租约，因此任何异常都不会留下"有 job 无线程"的假活状态。
    """
    lease = execution_lease or chat_lease(run_id)
    if not lease.acquire():
        raise RuntimeError("Chat run already has an executor")
    job = None
    owns_run = False

    def failed_start():
        """启动阶段失败的统一清理：标记 Run 中断、唤醒 replay 等待者、移除 job、释放租约。

        必须在任何异常路径上调用一次，否则会留下"有 job 记录但没有执行线程"的假活状态，
        使 replay 永远等不到事件、租约也永远不释放（后续同 run_id 无法再执行）。
        """
        try:
            if owns_run:
                with new_session() as db:
                    run = db.get(Run, run_id)
                    if run:
                        mark_interrupted(db, run)
        finally:
            if job:
                job.finished = True
                with job.changed:
                    job.changed.notify_all()
                with _lock:
                    if _jobs.get(run_id) is job:
                        _jobs.pop(run_id, None)
            lease.release()

    try:
        with new_session() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if (not run or run.workspace_id != workspace_id or run.started_by_user_id != user_id
                    or run.status != "created"):
                raise RuntimeError("Chat run is not available for execution")
            owns_run = True
            run.context_snapshot = {**(run.context_snapshot or {}),
                "chatLease": {"host": socket.gethostname(), "runId": run_id}}
            db.commit()
        job = ChatJob(run_id, workspace_id, run.conversation_id)
        with _lock:
            _jobs[run_id] = job
    except Exception:
        failed_start()
        raise

    inherited_log_context = current_context()

    @observe("chat.worker", "后台对话线程")
    def work():
        """执行线程主体：设置上下文控制块 → 驱动本次固定引擎并逐事件写出 → 收尾释放资源。

        异常一律在内部消化成 run.failed 事件（用户看到的是"已中断"而非堆栈），
        并在 finally 中把 Run 置为终态、补 done 事件、释放租约——保证事件流必然收尾。
        """
        # ContextVar 必须在执行线程内设置：TaskControl 靠它被模型/工具层的检查点读到。
        token = current.set(job.control)
        try:
            from app.modules.runtime.engine import run_chat
            with new_session() as db:
                run = db.get(Run, run_id)
                job.control.model_profile = run.model_profile
                job.control.model_workspace_id = workspace_id
                job.emit("run.created", {"conversation_id": run.conversation_id})
                for ev in run_chat(db, run, db.get(Workspace, workspace_id), db.get(User, user_id),
                    content=payload.content, file_ids=payload.file_ids, kb_ids=payload.knowledge_base_ids,
                    plugin_version_id=payload.plugin_version_id, events=[]):
                    job.emit(ev.get("type", "event"), ev.get("payload") or {})
                db.commit()
        except Exception as exc:
            # Read durable state before emitting a terminal event. An SSE failure
            # after a successful commit must not misreport completed work as failed.
            logger.exception("Detached chat failed (%s)", type(exc).__name__)
        finally:
            current.reset(token)
            try:
                with new_session() as db:
                    run = db.get(Run, run_id)
                    if run:
                        mark_interrupted(db, run, error_code=(
                            "chat_cancelled" if job.control.cancelled.is_set() else "chat_interrupted"))
                        if not job.terminal_event:
                            if run.status == "completed":
                                job.emit("run.completed", {"status": "completed"})
                            elif run.status in {"failed", "cancelled"}:
                                job.emit("run.failed", {"status": run.status,
                                    "error_code": run.error_code,
                                    "error": ("已停止本次任务，完成的结果已保留。" if run.status == "cancelled"
                                              else "本次处理已中断，已完成的结果仍然保留。")})
                job.emit("done", {})
            except Exception:
                # Even storage failure must release the lease. Reconnect can recover
                # from the persisted run once storage is available again.
                logger.exception("Detached chat finalization failed")
            finally:
                # 顺序不可调换：先释放租约再置 finished，否则别的进程可能抢到锁却发现 job 仍在。
                lease.release()
                job.finished = True
                with job.changed:
                    job.changed.notify_all()
                with _lock:
                    _jobs.pop(run_id, None)

    try:
        def traced_work():
            """在线程入口显式继承请求标识；后续模型线程继续使用 copy_context 传播。"""
            with log_context(**{**inherited_log_context, "workspace_id": workspace_id,
                               "conversation_id": job.conversation_id, "run_id": run_id, "chat_run_id": run_id}):
                work()
        threading.Thread(target=traced_work, name="chat-" + run_id, daemon=True).start()
        log_event("chat.queued", "对话已交给后台执行", run_id=run_id,
                  conversation_id=job.conversation_id, workspace_id=workspace_id)
    except Exception:
        failed_start()
        raise


def cancel_job(run_id):
    """请求取消本进程内正在执行的 Run：置取消标志，返回是否有执行者（False=已不在执行）。

    只置标志不等待：真正的中断发生在执行线程的下一个 `TaskControl.check()`，
    因此调用方拿到 True 只代表"请求已投递"，客户端应以事件流中的终态为准。
    边界：本进程没有该 Run（例如服务重启后）时返回 False，由调用方转做孤儿判定。
    """
    with _lock:
        job = _jobs.get(run_id)
    if job:
        log_event("chat.cancel.requested", "收到停止对话请求", run_id=run_id,
                  conversation_id=job.conversation_id, workspace_id=job.workspace_id)
        job.control.cancelled.set()
        return True
    return False


def replay(run_id, workspace_id, after=0):
    """生成器：按事件 id 顺序产出 SSE 帧，支持断点续传与存活探测。

    参数：`after` 是客户端已收到的最大事件 id，只补推 `id > after` 的帧。
    事件来源二选一：内存中的 `ChatJob`（活跃 Run，带 Condition 唤醒）或 `run_events` 表
    （进程重启后的历史帧）；两者都按 300 条/批分页，超过一页就继续循环，避免一次拉全表。
    终止语义：收到 `done` 立即返回；否则当"无新帧 + 已终态 + 无活动 job"时，补发 run.failed
    （若失败帧没在历史里出现过，防止客户端停在中间态）与 done 后返回。
    保活：无新事件时最多等 1 秒并发送 `: keepalive` 注释帧，防止反向代理/浏览器掐掉长连接。
    权限：调用方（chat/router 的 /events、/send）已校验 Run 归属，本函数只按 workspace_id 过滤。
    """
    cursor = max(0, after)
    terminal_sent = False
    while True:
        terminal_packet = None
        with _lock:
            job = _jobs.get(run_id)
        if job:
            with job.changed:
                packets = [(i,p) for i,p in job.packets if i > cursor][:300]
                terminal = job.finished
        else:
            with new_session() as db:
                rows = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id,
                    RunEvent.workspace_id == workspace_id, RunEvent.event_type == "chat.stream",
                    RunEvent.id > cursor).order_by(RunEvent.id).limit(300)).all()
                packets = [(r.id, r.payload) for r in rows]
                run = db.scalar(select(Run).where(Run.id == run_id, Run.workspace_id == workspace_id))
                if run:
                    recover_abandoned_chat(db, run)
                terminal = not run or run.status not in {"created", "running"}
                if run and run.status == "completed":
                    terminal_packet = ("run.completed", {"run_id": run_id, "status": "completed"})
                elif run and run.status in {"failed", "cancelled"}:
                    terminal_packet = ("run.failed", {"run_id": run_id, "status": run.status,
                        "error_code": run.error_code,
                        "error": ("已停止本次任务，完成的结果已保留。" if run.status == "cancelled"
                                  else "本次处理已中断，已完成的结果仍然保留。")})
        for event_id, packet in packets:
            cursor = event_id
            # Older workers could persist done without a terminal event. Restore
            # its meaning from the durable Run before the client stops listening.
            if packet["event"] == "done" and terminal_packet and not terminal_sent:
                event, data = terminal_packet
                yield f'event: {event}\ndata: ' + json.dumps(data, ensure_ascii=False) + '\n\n'
                terminal_sent = True
            yield f"id: {event_id}\nevent: {packet['event']}\ndata: {json.dumps(packet['data'], ensure_ascii=False)}\n\n"
            if packet["event"] in {"run.failed", "run.completed"}:
                terminal_sent = True
            if packet["event"] == "done":
                return
        with _lock:
            job = _jobs.get(run_id)
        if not packets and terminal and not job:
            # 没有活动执行者且已到终态：把失败/成功语义补齐，客户端只认 done 作为收尾。
            if terminal_packet and not terminal_sent:
                event, data = terminal_packet
                yield f'event: {event}\ndata: ' + json.dumps(data, ensure_ascii=False) + '\n\n'
            yield 'event: done\ndata: ' + json.dumps({"run_id": run_id}) + '\n\n'
            return
        if len(packets) >= 300:
            # 满一页说明还有积压，立刻继续取下一页，不进入等待与保活分支。
            continue
        if job:
            with job.changed:
                job.changed.wait(timeout=1)
        else:
            time.sleep(1)
        yield ": keepalive\n\n"
