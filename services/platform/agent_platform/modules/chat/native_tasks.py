"""内部聊天任务投影只核真实绑定；公共Task读取继续调用native_sources严格授权。"""

from sqlalchemy import update

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Notification, Task, TaskEvent, now_iso
from agent_platform.core.tasks import TRANSITIONS
from .native_models import NativeConversation, NativeChatIdentity

STATUS = {'reserved': 'running', 'running': 'running', 'waiting_approval': 'waiting_review',
    'succeeded': 'succeeded', 'failed': 'failed', 'unknown': 'unknown',
    'cancel_requested': 'cancel_requested', 'cancelled': 'cancelled'}


def bound_task(session, turn):
    task = session.get(Task, turn.task_id) if turn.task_id else None
    chat, identity = session.get(NativeConversation, turn.conversation_id), session.get(NativeChatIdentity, turn.identity_id)
    if (task is None or chat is None or identity is None or task.kind != 'platform_native_chat'
            or task.workspace_id != turn.workspace_id or task.user_id != turn.user_id
            or task.inputs.get('turn_id') != turn.id or task.inputs.get('chat_id') != chat.id
            or chat.identity_id != identity.id or identity.user_id != turn.user_id or chat.user_id != turn.user_id
            or chat.workspace_id != turn.workspace_id or identity.workspace_id != turn.workspace_id
            or chat.integration_id != turn.integration_id or identity.integration_id != turn.integration_id
            or chat.native_chat_id != turn.native_chat_id):
        raise DomainError(409, 'native_chat_task_binding_invalid', '对话运行与任务关联不一致')
    return task


def project_turn(session, turn, *, event_type=None, metadata=None):
    """已执行的终态不能因来源撤权而丢失；不保存消息正文、模型delta或原生令牌。"""
    task = bound_task(session, turn)
    status = STATUS.get(turn.status)
    if status is None:
        raise DomainError(409, 'native_chat_state_invalid', '对话运行状态不支持')
    if ((status == 'cancel_requested' and task.status in {'waiting_review', 'unknown'})
            or (status == 'waiting_review' and task.status == 'unknown')):
        changed = session.execute(update(Task).where(Task.id == task.id, Task.revision == task.revision).values(
            status='running', revision=task.revision + 1, updated_at=now_iso()))
        if not changed.rowcount:
            raise DomainError(409, 'native_chat_task_state_conflict', '对话任务状态已变化')
        session.refresh(task)
        session.add(TaskEvent(task_id=task.id, type='state_changed', data={'status': 'running', 'turn_id': turn.id}))
    if status != task.status:
        if status not in TRANSITIONS.get(task.status, set()):
            raise DomainError(409, 'native_chat_task_state_conflict', '对话任务状态已变化')
        changed = session.execute(update(Task).where(Task.id == task.id, Task.revision == task.revision).values(
            status=status, revision=task.revision + 1, updated_at=now_iso(), source_run_id=turn.id,
            outputs={'turn_id': turn.id, 'chat_id': turn.conversation_id},
            error={'code': turn.error_code, 'message': turn.error} if turn.error_code else None))
        if not changed.rowcount:
            raise DomainError(409, 'native_chat_task_state_conflict', '对话任务状态已变化')
        session.refresh(task)
        session.add(TaskEvent(task_id=task.id, type='state_changed', data={'status': status, 'turn_id': turn.id}))
        if status in {'succeeded', 'failed', 'unknown', 'waiting_review', 'cancelled'}:
            session.add(Notification(workspace_id=turn.workspace_id, user_id=turn.user_id,
                title='对话' + {'succeeded': '已完成', 'failed': '未完成', 'unknown': '需要核对',
                    'waiting_review': '等待确认', 'cancelled': '已停止'}[status], resource_type='task', resource_id=task.id))
    if event_type:
        session.add(TaskEvent(task_id=task.id, type='native_chat_event', data={
            'turn_id': turn.id, 'chat_id': turn.conversation_id, 'event_type': event_type, 'metadata': metadata or {}}))
    session.flush()
