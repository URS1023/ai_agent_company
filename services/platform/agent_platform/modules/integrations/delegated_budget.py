"""在业务工具读取之前提交一次额度消费，避免并行请求越过当前Turn总预算。"""

from copy import deepcopy

from sqlalchemy import select, update

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task, TaskEvent
from ..chat.native_models import NativeChatTurn, NativeConversation, NativeChatIdentity, utcnow
from ..chat.native_sources import ACTIVE_TURNS
from ..chat.native_tasks import bound_task
from ..chat.native_tool_policy import frozen_tool_policy


def reserve_tool_call(session, actor, context, operation_id: str) -> bool:
    """消费已冻结策略的一次调用并提交；错误或空结果不返还，不自动重试CAS。

    调用方先验引擎证书、人类签名与准确活动Turn；本函数不依据现配置补旧策略。
    提交后调用方必须重新认证并开启新读取事务，再执行领域服务。旧无工具策略的
    Turn沿既有路径，真正选择新专属alias时由后台预约强制固定策略。
    """
    turn = session.get(NativeChatTurn, context.get('turn_id'))
    if (turn is None or turn.workspace_id != actor.workspace_id or turn.user_id != actor.user_id
            or turn.workspace_id != context.get('workspace_id') or turn.user_id != context.get('actor_user_id')
            or turn.conversation_id != context.get('chat_id') or turn.identity_id != context.get('identity_id')
            or turn.integration_id != context.get('instance_id')):
        raise DomainError(403, 'native_delegation_denied', '对话委托归属已变化')
    policy = frozen_tool_policy(turn)
    if policy is None:
        return False
    task = bound_task(session, turn)
    budget = (turn.request_metadata or {}).get('tool_budget')
    limit = policy['max_tool_calls']
    if (not isinstance(budget, dict) or set(budget) != {'version', 'calls_used'}
            or type(budget.get('version')) is not int or budget['version'] != 1
            or type(budget.get('calls_used')) is not int or not 0 <= budget['calls_used'] <= limit):
        raise DomainError(503, 'native_tool_budget_invalid', '对话工具额度记录不完整')
    if budget['calls_used'] >= limit:
        raise DomainError(429, 'native_tool_budget_exhausted', '本次对话已达到工具调用上限')
    metadata = deepcopy(turn.request_metadata)
    metadata['tool_budget']['calls_used'] += 1
    current_chat = select(NativeConversation.id).where(
        NativeConversation.id == turn.conversation_id,
        NativeConversation.active_turn_id == turn.id,
        NativeConversation.status == 'ready', NativeConversation.identity_id == turn.identity_id,
        NativeConversation.workspace_id == turn.workspace_id, NativeConversation.user_id == turn.user_id,
        NativeConversation.integration_id == turn.integration_id,
        NativeConversation.native_chat_id == turn.native_chat_id).exists()
    current_identity = select(NativeChatIdentity.id).where(
        NativeChatIdentity.id == turn.identity_id, NativeChatIdentity.status == 'ready',
        NativeChatIdentity.user_id == turn.user_id, NativeChatIdentity.workspace_id == turn.workspace_id,
        NativeChatIdentity.integration_id == turn.integration_id).exists()
    active_task = select(Task.id).where(Task.id == task.id, Task.revision == task.revision,
        Task.status.in_({'running', 'waiting_review'})).exists()
    changed = session.execute(update(NativeChatTurn).where(
        NativeChatTurn.id == turn.id, NativeChatTurn.revision == turn.revision,
        NativeChatTurn.workspace_id == actor.workspace_id, NativeChatTurn.user_id == actor.user_id,
        NativeChatTurn.conversation_id == turn.conversation_id, NativeChatTurn.identity_id == turn.identity_id,
        NativeChatTurn.status.in_(ACTIVE_TURNS), NativeChatTurn.deadline_at > utcnow(),
        current_chat, current_identity, active_task).values(request_metadata=metadata, revision=turn.revision + 1),
        execution_options={'synchronize_session': False})
    if changed.rowcount != 1:
        session.rollback()
        raise DomainError(409, 'native_tool_budget_conflict', '对话状态或工具额度已变化，请核对本次运行')
    session.add(TaskEvent(task_id=task.id, type='native_tool_call_reserved', data={
        'turn_id': turn.id, 'operation_id': operation_id,
        'calls_used': metadata['tool_budget']['calls_used'], 'max_tool_calls': limit}))
    session.commit()
    session.expire_all()
    return True
