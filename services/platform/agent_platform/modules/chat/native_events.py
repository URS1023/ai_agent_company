"""有界原生事件缓冲与本进程监控归属；浏览器断开只退订，不停止已派发生成。"""

from collections import deque
from contextlib import asynccontextmanager
import asyncio
import json
from threading import BoundedSemaphore, Event, Lock, Thread
import time

from agent_platform.core.errors import DomainError
from .native_execution import NativeTurnRunner, current_actor
from .native_sources import authorize_turn_sources, authorize_conversation_sources
from .native_models import NativeChatTurn, NativeConversation

_manager_lock = Lock()


class TurnBuffer:
    """正文只进入短期内存窗口；历史正文仍从原生chat读取，缓冲缺口要求显式回查。"""
    def __init__(self, user_id, workspace_id):
        self.user_id, self.workspace_id = user_id, workspace_id
        self.frames, self.bytes, self.sequence = deque(), 0, 0
        self.lock, self.shutdown_requested = Lock(), Event()
        self.finished, self.finished_at, self.thread = False, None, None
    def publish(self, event_type, data):
        with self.lock:
            self.sequence += 1
            frame = {'sequence': self.sequence, 'event_type': event_type, 'data': data}
            encoded = json.dumps(frame, ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > 2 * 1024 * 1024:
                raise DomainError(502, 'native_chat_event_budget_exceeded', '对话事件超过本次读取预算')
            self.frames.append((frame, len(encoded)))
            self.bytes += len(encoded)
            while self.bytes > 4 * 1024 * 1024 or len(self.frames) > 4000:
                _, size = self.frames.popleft()
                self.bytes -= size
    def read(self, after):
        with self.lock:
            if self.frames and after < self.frames[0][0]['sequence'] - 1:
                return [], True, True
            return [frame for frame, _ in self.frames if frame['sequence'] > after][:100], self.finished, False
    def finish(self):
        with self.lock:
            self.finished, self.finished_at = True, time.monotonic()


class NativeChatRuntime:
    """最多四个监控线程，每个线程自建Session；不把请求Session交给Socket回调。"""
    def __init__(self, request):
        self.request, self.controls, self.lock = request, {}, Lock()
        self.slots, self.closing = BoundedSemaphore(4), False
    def launch(self, turn_id, actor, payload, *, action_id=None):
        with self.lock:
            self.controls = {key: value for key, value in self.controls.items()
                if not value.finished or time.monotonic() - (value.finished_at or 0) < 300}
            # 活动线程有四槽限制，已结束正文缓冲也须限量，避免快速短对话积累内存。
            finished = sorted(((key, value) for key, value in self.controls.items() if value.finished),
                key=lambda pair: pair[1].finished_at or 0)
            for key, _ in finished[:max(0, len(self.controls) - 16)]:
                if key != turn_id:
                    self.controls.pop(key, None)
            previous = self.controls.get(turn_id)
            if self.closing or (previous and previous.thread and previous.thread.is_alive()):
                raise DomainError(409, 'native_chat_monitor_busy', '该对话正在处理，请核对原运行', {'turn_id': turn_id})
            if not self.slots.acquire(blocking=False):
                raise DomainError(503, 'native_chat_capacity_reached', '当前对话处理中，请稍后发起新操作', {'turn_id': turn_id})
            control = previous or TurnBuffer(actor.user_id, actor.workspace_id)
            if control.user_id != actor.user_id or control.workspace_id != actor.workspace_id:
                self.slots.release()
                raise DomainError(403, 'native_chat_monitor_denied', '对话监控归属不一致')
            control.finished, control.finished_at = False, None
            self.controls[turn_id] = control
            def run():
                try:
                    asyncio.run(NativeTurnRunner(self.request, actor, turn_id, control, payload, action_id).execute())
                finally:
                    control.finish()
                    self.slots.release()
            thread = Thread(target=run, daemon=True, name='native-chat-' + turn_id)
            control.thread = thread
            try:
                thread.start()
            except BaseException:
                control.finish()
                self.slots.release()
                raise DomainError(503, 'native_chat_monitor_unavailable', '本次对话监控未启动，请核对原运行', {'turn_id': turn_id}) from None
            return control
    def active(self, turn_id):
        with self.lock:
            control = self.controls.get(turn_id)
            return bool(control and control.thread and control.thread.is_alive())
    def buffer(self, turn_id, actor):
        with self.lock:
            value = self.controls.get(turn_id)
            if value and (value.user_id != actor.user_id or value.workspace_id != actor.workspace_id):
                raise DomainError(404, 'native_turn_not_found', '对话运行不存在')
            return value
    def close(self):
        with self.lock:
            self.closing = True
            values = list(self.controls.values())
        for value in values:
            value.shutdown_requested.set()
        deadline = time.monotonic() + 5
        for value in values:
            if value.thread:
                value.thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(value.thread and value.thread.is_alive() for value in values):
            raise RuntimeError('native_chat_monitor_cleanup_unconfirmed')


def runtime_for(request):
    with _manager_lock:
        value = getattr(request.app.state, 'native_chat_runtime', None)
        if value is None:
            value = NativeChatRuntime(request)
            request.app.state.native_chat_runtime = value
        return value


def stream_events(request, actor, turn_id, after=0):
    """每次交付前重核当前会话与来源；不订阅其他聊天，也不重新派发生成。"""
    runtime = runtime_for(request)
    control = runtime.buffer(turn_id, actor)
    if control is None:
        yield b'event: reconcile_required\ndata: {"reason":"event_buffer_unavailable"}\n\n'
        return
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        frames, done, gap = control.read(after)
        if gap:
            yield b'event: reconcile_required\ndata: {"reason":"event_buffer_gap"}\n\n'
            return
        for frame in frames or [None]:
            try:
                with request.app.state.database.sessions() as session:
                    fresh = current_actor(session, actor, request)
                    turn = session.get(NativeChatTurn, turn_id)
                    if turn is None:
                        raise DomainError(404, 'native_turn_not_found', '对话运行不存在')
                    authorize_turn_sources(session, fresh, turn)
                    chat = session.get(NativeConversation, turn.conversation_id)
                    if chat is None:
                        raise DomainError(404, 'native_chat_not_found', '对话不存在')
                    authorize_conversation_sources(session, fresh, chat)
            except DomainError:
                yield b'event: access_revoked\ndata: {"reason":"access_unavailable"}\n\n'
                return
            if frame is None:
                continue
            after = frame['sequence']
            yield ('id: ' + str(after) + '\ndata: ' + json.dumps(frame, ensure_ascii=False, allow_nan=False) + '\n\n').encode()
        if done:
            return
        if not frames:
            time.sleep(0.25)


@asynccontextmanager
async def native_chat_lifespan(app):
    try:
        yield
    finally:
        value = getattr(app.state, 'native_chat_runtime', None)
        if value is not None:
            await asyncio.to_thread(value.close)
