"""原生会话、当前人类用户与可信工具来源的授权闭环；不读取原生数据库。"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select, update

from agent_platform.core.errors import DomainError
from agent_platform.core.models import ResourceRecord
from .native_models import NativeChatIdentity, NativeConversation, NativeChatTurn

ACTIVE_TURNS = {'running', 'waiting_approval'}


def resolve_delegation_context(session, *, engine_identity_id, native_user_id, native_chat_id, native_message_id):
    """双证明之后仍按精确原生用户/chat/assistant核有效Turn，不能委托其他会话。"""
    if any(not isinstance(value, str) or not value or len(value) > 200
            for value in (engine_identity_id, native_user_id, native_chat_id, native_message_id)):
        raise DomainError(403, 'native_delegation_denied', '对话委托标识不完整')
    row = session.scalar(select(NativeChatTurn).join(NativeChatIdentity,
        NativeChatTurn.identity_id == NativeChatIdentity.id).join(ResourceRecord,
        NativeChatTurn.integration_id == ResourceRecord.id).where(
        ResourceRecord.data['delegation_service_identity_id'].as_string() == engine_identity_id,
        NativeChatIdentity.native_user_id == native_user_id,
        NativeChatTurn.native_chat_id == native_chat_id,
        NativeChatTurn.assistant_message_id == native_message_id))
    deadline = row.deadline_at.replace(tzinfo=timezone.utc) if row is not None and row.deadline_at.tzinfo is None else (
        row.deadline_at if row is not None else None)
    if row is None or row.status not in ACTIVE_TURNS or deadline <= datetime.now(timezone.utc):
        raise DomainError(403, 'native_delegation_denied', '对话委托不存在或已结束')
    identity = session.get(NativeChatIdentity, row.identity_id)
    chat = session.get(NativeConversation, row.conversation_id)
    integration = session.get(ResourceRecord, row.integration_id)
    if (identity is None or chat is None or integration is None or integration.deleted or integration.archived
            or integration.kind != 'integration_instance' or integration.workspace_id != row.workspace_id
            or integration.data.get('engine') != 'openwebui' or not integration.data.get('enabled', True)
            or integration.data.get('delegation_service_identity_id') != engine_identity_id
            or identity.status != 'ready' or chat.status != 'ready' or chat.active_turn_id != row.id
            or identity.user_id != row.user_id or chat.user_id != row.user_id
            or identity.workspace_id != row.workspace_id or chat.workspace_id != row.workspace_id
            or identity.integration_id != row.integration_id or chat.integration_id != row.integration_id
            or chat.identity_id != identity.id or chat.native_chat_id != row.native_chat_id
            or integration.data.get('base_url', '').rstrip('/') != identity.integration_url):
        raise DomainError(403, 'native_delegation_denied', '对话身份或实例绑定已变化')
    return {'actor_user_id': row.user_id, 'workspace_id': row.workspace_id, 'turn_id': row.id,
        'chat_id': chat.id, 'instance_id': row.integration_id, 'identity_id': identity.id,
        'native_chat_id': row.native_chat_id, 'native_message_id': row.assistant_message_id,
        'status': row.status, 'active': True, 'engine_identity_id': engine_identity_id}


def append_turn_sources(session, context, sources):
    """可信派发器只追加无正文的来源收据；CAS防并行工具覆盖已记录来源。"""
    turn = session.get(NativeChatTurn, context.get('turn_id'))
    if (turn is None or turn.workspace_id != context.get('workspace_id')
            or turn.user_id != context.get('actor_user_id') or turn.conversation_id != context.get('chat_id')
            or turn.identity_id != context.get('identity_id') or turn.status not in ACTIVE_TURNS):
        raise DomainError(403, 'native_delegation_denied', '对话委托已结束或归属不一致')
    identity = session.get(NativeChatIdentity, turn.identity_id)
    verified = resolve_delegation_context(session, engine_identity_id=context.get('engine_identity_id'),
        native_user_id=identity.native_user_id if identity else None,
        native_chat_id=turn.native_chat_id, native_message_id=turn.assistant_message_id)
    if any(verified.get(key) != context.get(key) for key in ('turn_id', 'chat_id', 'instance_id', 'identity_id')):
        raise DomainError(403, 'native_delegation_denied', '对话委托在执行期间已变化')
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise DomainError(422, 'native_source_invalid', '对话来源记录格式不正确')
    for source in sources:
        if (set(source) != {'version', 'workspace_id', 'resource', 'queries', 'files'}
                or source['version'] != 1 or source['workspace_id'] != turn.workspace_id
                or not isinstance(source['resource'], dict) or not isinstance(source['queries'], list)
                or not isinstance(source['files'], list)):
            raise DomainError(422, 'native_source_invalid', '对话来源收据不完整')
    try:
        encoded = json.dumps(sources, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise DomainError(422, 'native_source_invalid', '对话来源记录必须是有限JSON数据') from None
    if len(encoded.encode()) > 65536:
        raise DomainError(422, 'native_source_budget_exceeded', '本次工具来源记录超过预算')
    records = {hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest(): item
        for item in (turn.sources or []) + sources}
    if len(records) > 500:
        raise DomainError(422, 'native_source_budget_exceeded', '本轮引用来源过多')
    changed = session.execute(update(NativeChatTurn).where(NativeChatTurn.id == turn.id,
        NativeChatTurn.revision == turn.revision, NativeChatTurn.status.in_(ACTIVE_TURNS)).values(
        sources=list(records.values()), revision=turn.revision + 1))
    if not changed.rowcount:
        raise DomainError(409, 'native_source_conflict', '对话来源已变化，请核对本次工具记录')
    session.refresh(turn)


def authorize_sources(session, actor, sources):
    """读取历史及继续前逐条重核当前资源权限；守卫未安装时拒绝已有敏感来源。"""
    if not sources:
        return
    for source in sources:
        try:
            if source.get('source_kind') == 'native_file':
                from .native_files import authorize_native_file_source
                authorize_native_file_source(session, actor, source)
            else:
                from ..integrations.delegated_tools import authorize_delegated_source
                authorize_delegated_source(session, actor, source)
        except ImportError:
            raise DomainError(503, 'native_source_guard_unavailable', '对话来源授权暂不可用') from None


def authorize_turn_sources(session, actor, turn):
    if turn.workspace_id != actor.workspace_id or turn.user_id != actor.user_id:
        raise DomainError(404, 'native_turn_not_found', '对话运行不存在')
    actor.require('m02.read')
    authorize_sources(session, actor, turn.sources)


def authorize_conversation_sources(session, actor, chat):
    if chat.workspace_id != actor.workspace_id or chat.user_id != actor.user_id:
        raise DomainError(404, 'native_chat_not_found', '对话不存在')
    actor.require('m02.read')
    authorize_sources(session, actor, chat.inherited_sources)
    for turn in session.scalars(select(NativeChatTurn).where(NativeChatTurn.conversation_id == chat.id)):
        authorize_turn_sources(session, actor, turn)


def authorize_native_chat_task(session, actor, task):
    """普通管理员同样不能默认读取他人聊天；所有公共任务出口共用严格双向来源守卫。"""
    turn_id = task.inputs.get('turn_id') if isinstance(task.inputs, dict) else None
    turn = session.get(NativeChatTurn, turn_id) if turn_id else None
    chat = session.get(NativeConversation, turn.conversation_id) if turn is not None else None
    identity = session.get(NativeChatIdentity, turn.identity_id) if turn is not None else None
    if (task.kind != 'platform_native_chat' or turn is None or chat is None or identity is None
            or task.workspace_id != actor.workspace_id or task.user_id != actor.user_id
            or turn.workspace_id != actor.workspace_id or turn.user_id != actor.user_id
            or task.id != turn.task_id or task.inputs.get('chat_id') != chat.id
            or chat.workspace_id != turn.workspace_id or chat.user_id != turn.user_id
            or identity.workspace_id != turn.workspace_id or identity.user_id != turn.user_id
            or chat.identity_id != identity.id or chat.integration_id != turn.integration_id
            or identity.integration_id != turn.integration_id or chat.native_chat_id != turn.native_chat_id):
        raise DomainError(403, 'native_chat_task_access_denied', '当前账号无法访问任务引用的对话')
    authorize_conversation_sources(session, actor, chat)
