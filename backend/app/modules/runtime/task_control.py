# 一次对话任务的协作式取消与可选总时限。
#
# ChatJob 默认不建立总截止时间，长时间的模型生成与多步工具执行不会因对话总时长中断。
# 管理员仍可显式配置正数 CHAT_TASK_TIMEOUT_SECONDS；底层数据库、沙箱与网络建连保护独立保留。
# 无 deadline 时，所有检查点仍响应用户取消，单项调用也保留调用方指定的预算。
"""Cooperative cancellation with an optional total chat deadline."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
import time


class ChatCancelled(Exception):
    """用户主动停止本次任务（POST /api/chat/runs/{id}/cancel 置标志后，在检查点抛出）。

    异常消息即用户可见文案；抛出后由 agent 循环写 Run 终态（cancelled）并保留已完成结果。
    """

    pass


# 总时限到期：与供应商侧超时无关，专门用于把"整轮超时"和"单次调用超时"区分开，
# 使错误文案能提示用户把任务拆小（见 agent._friendly_run_error）。
class ChatTaskTimeout(TimeoutError):
    """The total chat deadline expired, independently of provider timeouts."""


@dataclass
class TaskControl:
    """一次 Run 的执行控制块：总时限、取消标志、事件出口与重试额度。

    生命周期：由 `chat_jobs.ChatJob` 在启动执行线程前创建，随线程结束失效（不落库）。
    不变式：
    - `deadline=None` 表示无总时限；正数是单调时钟的绝对时间点，整轮共享；
    - `sink` 由 ChatJob 注入，签名 `sink(event: str, data: dict)`，用于发 task.progress；
    - `model_profile` / `model_workspace_id` 用于让嵌套的模型调用继承本次对话所用的档位；
    - `model_connection_retries` 受 `model_retry_lock` 保护：并发/多轮调用共享同一个额度。
    """

    deadline: float | None = None
    cancelled: Event = field(default_factory=Event)
    sink: object = None
    model_profile: str | None = None
    model_workspace_id: str | None = None
    query_receipts: dict = field(default_factory=dict)
    model_connection_retries: int = 0
    model_retry_lock: object = field(default_factory=Lock, repr=False)

    def claim_model_connection_retry(self) -> bool:
        """申请一次"模型建连失败"重试额度，返回是否获批（整轮最多一次）。

        约束（已附带的判断条件）：
        - 只有整轮还没重试过才发放，避免连接故障时反复重放同一轮；
        - 剩余预算不足 30 秒时拒绝：重试必然来不及完成，只会把任务拖到超时；
        - 只用于"请求尚未建立"的失败（`providers/model._create_chat_completion`），
          流式中断不重放，以免重复消费工具/写库副作用。
        副作用：获批时自增共享计数（写回 Run 快照用于排障）。
        """
        with self.model_retry_lock:
            self.check()
            if self.model_connection_retries or (self.deadline is not None and self.deadline - time.monotonic() < 30):
                return False
            self.model_connection_retries += 1
            return True

    def check(self):
        """统一检查点：取消优先于超时，两者都抛异常而不返回布尔值。

        供所有长流程（模型轮次、工具执行、SSE 推送间隙）调用；抛出的
        ChatCancelled / ChatTaskTimeout 由 agent 循环统一映射为对用户可见的中断文案。
        """
        if self.cancelled.is_set():
            raise ChatCancelled("已停止本次任务，完成的结果已保留。")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ChatTaskTimeout("本次任务已达到总时间上限，完成的结果已保留。")


# 当前执行线程/上下文的控制块。默认 None 表示"不在对话任务里"（例如后台脚本、
# 定时任务直接调用模型），此时各检查点退化为无限制行为。
current: ContextVar = ContextVar("chat_task_control", default=None)


def checkpoint():
    """在长流程的关键节点调用：无控制块则空操作，否则执行取消/超时检查（可能抛异常）。"""
    control = current.get()
    if control:
        control.check()


def remaining_timeout(requested: float | None = 120) -> float | None:
    """Keep per-operation limits while optionally clipping to a chat deadline.

    ``None`` means no timeout was requested. A chat with no total deadline leaves
    that value unchanged; a deliberately limited chat returns its remaining budget.
    Cancellation is checked in both modes. Database/sandbox callers retain their
    own finite budgets even when the chat itself can run without a time limit.
    """
    control = current.get()
    if control:
        control.check()
        if control.deadline is not None:
            remaining = control.deadline - time.monotonic()
            return max(0.1, remaining if requested is None else min(requested, remaining))
    return requested


def progress(message, percent=None):
    """向本次对话推送任务进度（sink 负责转成 task.progress 事件）。

    参数：`message` 为展示文案，`percent` 为可选的 0–100 进度值。
    副作用：先做取消/超时检查，再经 `TaskControl.sink` 写事件；无控制块或未接出时静默丢弃，
    因此调用方可以安全地在任意位置插入进度上报。
    """
    checkpoint()
    control = current.get()
    if control and control.sink:
        control.sink("task.progress", {"message": message, "progress": percent})
