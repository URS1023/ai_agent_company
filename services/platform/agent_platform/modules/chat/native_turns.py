"""单次用户意图的预约与准确消息校验；模型/工具循环全部留在Open WebUI。"""

from copy import deepcopy
from datetime import timedelta
import json
import time

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent_platform.core.datetimes import utc_storage_iso
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task, TaskEvent
from .native_models import NativeChatIdentity, NativeChatTurn, NativeTurnAction, NativeConversation, new_id, utcnow
from .native_service import NativeChatService, fingerprint, intent_key
from .native_sources import authorize_turn_sources
from .native_tasks import project_turn

MAX_SECONDS = 90
TERMINAL = {'succeeded', 'failed', 'cancelled'}
REPLAY_KEYS = {'id', 'role', 'content', 'output', 'files', 'contextSummary', 'usage', 'model', 'meta'}


def turn_dict(turn):
    return {key: getattr(turn, key) for key in ('id', 'conversation_id', 'identity_id', 'model_id', 'status',
        'user_message_id', 'assistant_message_id', 'parent_message_id', 'idempotency_key', 'task_id',
        'error_code', 'error', 'revision')} | {'created_at': utc_storage_iso(turn.created_at),
        'updated_at': utc_storage_iso(turn.updated_at), 'deadline_at': utc_storage_iso(turn.deadline_at)}


def branch_messages(history, parent_id):
    """只沿原生parentId读取所选分支，不重排整树或把客户端messages当可信历史。"""
    messages = history.get('messages', {}) if isinstance(history, dict) else {}
    if not isinstance(messages, dict):
        raise DomainError(502, 'native_chat_history_invalid', '原生消息树格式不正确')
    if parent_id is None:
        if messages:
            raise DomainError(422, 'native_chat_parent_required', '请选择继续对话的消息')
        return []
    parent = messages.get(parent_id)
    if not isinstance(parent, dict) or parent.get('role') != 'assistant' or parent.get('done') is not True:
        raise DomainError(409, 'native_chat_parent_invalid', '该助手消息尚未完成或不在当前对话中')
    result, seen, current = [], set(), parent_id
    while current is not None:
        if current in seen or len(seen) >= 1000 or current not in messages:
            raise DomainError(502, 'native_chat_history_invalid', '原生消息树关联不正确')
        seen.add(current)
        value = messages[current]
        if not isinstance(value, dict) or value.get('id') != current:
            raise DomainError(502, 'native_chat_history_invalid', '原生消息标识不一致')
        result.append({key: deepcopy(item) for key, item in value.items() if key in REPLAY_KEYS})
        current = value.get('parentId')
    result.reverse()
    return result


def assistant_from_chat(result, turn):
    """200/null与Socket完成提示都不是成功证据；必须读取准确助手及父用户消息。"""
    messages = ((result.get('chat') or {}).get('history') or {}).get('messages', {})
    message = messages.get(turn.assistant_message_id) if isinstance(messages, dict) else None
    parent = messages.get(turn.user_message_id) if isinstance(messages, dict) else None
    if (not isinstance(message, dict) or not isinstance(parent, dict)
            or message.get('id') != turn.assistant_message_id or parent.get('id') != turn.user_message_id
            or message.get('role') != 'assistant' or parent.get('role') != 'user'
            or message.get('parentId') != turn.user_message_id or parent.get('parentId') != turn.parent_message_id
            or message.get('model') != turn.model_id
            or turn.assistant_message_id not in (parent.get('childrenIds') or [])):
        raise DomainError(502, 'native_chat_message_binding_invalid', '原生助手消息与本次运行不一致')
    return message


def pending_calls(message):
    """沿用原生pending_tool_calls口径，已有function_call_output就不再待处理。"""
    output = message.get('output') if isinstance(message.get('output'), list) else []
    finished = {item.get('call_id') for item in output if isinstance(item, dict)
        and item.get('type') == 'function_call_output'}
    return [item for item in output if isinstance(item, dict)
        and item.get('type') == 'function_call' and (item.get('call_id') or item.get('id')) not in finished
        and item.get('status') in {'pending', 'queued', 'requires_approval', 'in_progress'}]


def awaiting_approvals(message):
    """运行中的工具不是待用户确认；只有未批准的原生pending调用可恢复。"""
    return [item for item in pending_calls(message) if item.get('approved') is not True
        and item.get('status') in {'pending', 'queued', 'requires_approval'}]


def visible_reply_text(message):
    """复用已验收verifier的原生message投影；reasoning不当作可见回答。"""
    content = message.get('content')
    if isinstance(content, str) and content.strip():
        return content
    texts = []
    for item in message.get('output') if isinstance(message.get('output'), list) else []:
        if not isinstance(item, dict) or item.get('type') != 'message' or not isinstance(item.get('content'), list):
            continue
        text = ''.join(part['text'] for part in item['content'] if isinstance(part, dict) and isinstance(part.get('text'), str))
        if text.strip():
            texts.append(text)
    return '\n'.join(texts)


class NativeTurnService(NativeChatService):
    def turn(self, turn_id, *, sources=True):
        row = self.session.get(NativeChatTurn, turn_id)
        if row is None or row.user_id != self.actor.user_id or row.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'native_turn_not_found', '对话运行不存在')
        self.chat(row.conversation_id, sources=sources)
        if sources:
            authorize_turn_sources(self.session, self.actor, row)
        return row

    def models(self, identity_id):
        identity, integration = self.identity(identity_id)
        adapter = self.user_adapter(identity, integration)
        self.session.commit()
        result = adapter.models()
        items = result.get('data') if isinstance(result, dict) else result
        if not isinstance(items, list):
            raise DomainError(502, 'native_chat_models_invalid', '原生模型列表格式不正确')
        from ..integrations.chat_model_config import chat_model_policy
        policy = chat_model_policy(integration)
        return {'items': [{'id': item['id'], 'name': item.get('name', item['id'])}
            for item in items if isinstance(item, dict) and isinstance(item.get('id'), str)
            and (policy is None or item['id'] in policy['model_ids'])]}

    def reserve(self, chat_id, body, key):
        self.actor.require('m02.write')
        key = intent_key(key)
        if key is None:
            raise DomainError(422, 'native_chat_intent_required', '请为本次发送提供操作编号')
        chat, identity, integration = self.chat(chat_id)
        digest = fingerprint({'chat_id': chat_id, **body})
        prior = self.session.scalar(select(NativeChatTurn).where(NativeChatTurn.conversation_id == chat_id,
            NativeChatTurn.idempotency_key == key))
        if prior:
            if prior.request_hash != digest:
                raise DomainError(409, 'idempotency_conflict', '该操作编号已用于其他消息', {'turn_id': prior.id})
            authorize_turn_sources(self.session, self.actor, prior)
            return prior, None
        if chat.status != 'ready' or chat.active_turn_id:
            raise DomainError(409, 'native_chat_busy', '请先核对当前对话运行')
        if body.get('model_id') not in {item['id'] for item in self.models(identity.id)['items']}:
            raise DomainError(422, 'native_chat_model_denied', '该模型当前不可用', {'model_id': '请选择可用模型'})
        _, native = self.read_native_chat(chat_id)
        messages = branch_messages(native['chat'].get('history'), body.get('parent_message_id'))
        if len(json.dumps({'messages': messages, 'content': body['content']}, ensure_ascii=False,
                allow_nan=False).encode()) > 1024 * 1024:
            raise DomainError(422, 'native_chat_context_budget_exceeded', '当前分支内容过多，请从较短分支继续')
        attachment_ids = body.get('attachment_ids', [])
        native_files, sources = [], []
        if attachment_ids:
            from .native_files import resolve_turn_attachments
            attachments = resolve_turn_attachments(self.session, self.actor, chat, attachment_ids)
            native_files, sources = attachments['native_files'], attachments['sources']
        user_id, assistant_id, turn_id, task_id = new_id(), new_id(), new_id(), new_id()
        turn = NativeChatTurn(id=turn_id, workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            integration_id=integration['id'], identity_id=identity.id, conversation_id=chat.id, native_chat_id=chat.native_chat_id,
            user_message_id=user_id, assistant_message_id=assistant_id, parent_message_id=body.get('parent_message_id'),
            model_id=body['model_id'], idempotency_key=key, request_hash=digest, task_id=task_id, sources=sources,
            request_metadata={'revision': body['revision'], 'content_sha256': fingerprint(body['content']),
                'content_chars': len(body['content']), 'max_tokens': body['max_tokens'], 'temperature': body['temperature'],
                'attachment_ids': attachment_ids}, deadline_at=utcnow() + timedelta(seconds=MAX_SECONDS))
        task = Task(id=task_id, workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            kind='platform_native_chat', title=chat.title, engine='openwebui', status='running', access_permission='m02.read',
            inputs={'turn_id': turn_id, 'chat_id': chat.id}, outputs={}, idempotency_key='native-chat:' + turn_id,
            input_hash=digest)
        self.session.add_all([turn, task, TaskEvent(task_id=task_id, type='created', data={'turn_id': turn_id, 'status': 'running'})])
        try:
            changed = self.session.execute(update(NativeConversation).where(NativeConversation.id == chat.id,
                NativeConversation.revision == body['revision'], NativeConversation.active_turn_id.is_(None),
                NativeConversation.status == 'ready', exists(select(NativeChatIdentity.id).where(
                    NativeChatIdentity.id == identity.id, NativeChatIdentity.status == 'ready'))).values(
                    active_turn_id=turn_id, revision=body['revision'] + 1))
            if not changed.rowcount:
                raise DomainError(409, 'native_chat_edit_conflict', '对话已变化，请读取最新版本')
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            prior = self.session.scalar(select(NativeChatTurn).where(NativeChatTurn.conversation_id == chat_id,
                NativeChatTurn.idempotency_key == key))
            if prior is None or prior.request_hash != digest:
                raise DomainError(409, 'idempotency_conflict', '该对话操作已被其他请求占用') from None
            return prior, None
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '发送预约保存未确认，请按原编号核对', {'turn_id': turn_id}) from None
        except DomainError:
            self.session.rollback()
            raise
        user_message = {'id': user_id, 'parentId': body.get('parent_message_id'), 'childrenIds': [assistant_id],
            'role': 'user', 'content': body['content'], 'models': [body['model_id']], 'timestamp': int(time.time()),
            **({'files': native_files} if native_files else {})}
        payload = {'model': body['model_id'], 'chat_id': chat.native_chat_id, 'id': assistant_id,
            'user_message': user_message, 'messages': messages + [{'role': 'user', 'content': body['content']}],
            'stream': True, 'tools': [], 'tool_ids': [], 'skill_ids': [], 'files': native_files, 'filter_ids': [],
            'background_tasks': {}, 'features': {'memory': False, 'web_search': False, 'image_generation': False,
                'code_interpreter': False}, 'params': {'max_tokens': body['max_tokens'], 'temperature': body['temperature'],
                    'function_calling': 'legacy'}}
        return turn, payload

    def start(self, chat_id, body, key):
        turn, payload = self.reserve(chat_id, body, key)
        if payload is not None:
            from .native_events import runtime_for
            try:
                runtime_for(self.request).launch(turn.id, self.actor, payload)
            except DomainError:
                # 线程未启动，准确预约仍保留；原操作编号不得再次派发。
                turn.status, turn.error_code = 'failed', 'native_chat_monitor_unavailable'
                turn.error, turn.revision = '本次生成未派发，请发起新的操作', turn.revision + 1
                self.session.execute(update(NativeConversation).where(NativeConversation.id == chat_id,
                    NativeConversation.active_turn_id == turn.id).values(active_turn_id=None))
                project_turn(self.session, turn)
                self.session.commit()
                raise
        return turn_dict(turn)

    def detail_turn(self, turn_id):
        return turn_dict(self.turn(turn_id))

    def list_turns(self, chat_id, *, key=None):
        self.chat(chat_id)
        query = select(NativeChatTurn).where(NativeChatTurn.conversation_id == chat_id)
        if key is not None:
            query = query.where(NativeChatTurn.idempotency_key == intent_key(key))
        rows = self.session.scalars(query.order_by(NativeChatTurn.created_at.desc()).limit(100))
        return {'items': [turn_dict(row) for row in rows]}

    def reserve_action(self, turn_id, kind, body, key):
        self.actor.require('m02.write')
        key = intent_key(key)
        if key is None:
            raise DomainError(422, 'native_chat_intent_required', '请为本次操作提供操作编号')
        turn = self.turn(turn_id, sources=kind != 'stop')
        digest = fingerprint({'kind': kind, **body})
        prior = self.session.scalar(select(NativeTurnAction).where(NativeTurnAction.turn_id == turn.id,
            NativeTurnAction.idempotency_key == key))
        if prior:
            if prior.request_hash != digest:
                raise DomainError(409, 'idempotency_conflict', '该操作编号已用于其他动作')
            return prior, False
        if kind == 'resolve' and turn.status != 'waiting_approval':
            raise DomainError(409, 'native_chat_approval_not_pending', '该消息当前没有待处理确认')
        if kind == 'stop' and turn.status in TERMINAL:
            raise DomainError(409, 'native_chat_already_finished', '该对话运行已结束')
        pending = select(NativeTurnAction).where(NativeTurnAction.turn_id == turn.id,
            NativeTurnAction.status.in_({'reserved', 'dispatching', 'unknown'}))
        if kind == 'stop':
            pending = pending.where(NativeTurnAction.kind == 'stop')
        if self.session.scalar(pending.limit(1)) is not None:
            raise DomainError(409, 'native_chat_action_requires_review', '已有操作尚未确认，请先核对原操作')
        if kind == 'resolve':
            chat, identity, integration = self.chat(turn.conversation_id)
            adapter = self.user_adapter(identity, integration)
            self.session.commit()
            message = assistant_from_chat(self._native_chat(adapter.chat(chat.native_chat_id), identity,
                expected_chat=chat.native_chat_id), turn)
            if body['call_id'] not in {item.get('call_id') or item.get('id') for item in awaiting_approvals(message)}:
                raise DomainError(409, 'native_chat_approval_not_pending', '该确认已经处理或不属于此消息')
        action = NativeTurnAction(id=new_id(), workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            turn_id=turn.id, kind=kind, idempotency_key=key, request_hash=digest,
            request_metadata={k: v for k, v in body.items() if k != 'answers'} | {
                'answers_sha256': fingerprint(body.get('answers')), 'previous_status': turn.status})
        self.session.add(action)
        try:
            changed = self.session.execute(update(NativeChatTurn).where(NativeChatTurn.id == turn.id,
                NativeChatTurn.revision == turn.revision, NativeChatTurn.status == turn.status).values(
                status='cancel_requested' if kind == 'stop' else 'running', revision=turn.revision + 1,
                deadline_at=utcnow() + timedelta(seconds=MAX_SECONDS)))
            if not changed.rowcount:
                raise DomainError(409, 'native_chat_action_conflict', '对话状态已变化，请核对最新记录')
            self.session.refresh(turn)
            project_turn(self.session, turn)
            self.session.commit()
        except (DomainError, IntegrityError):
            self.session.rollback()
            raise DomainError(409, 'native_chat_action_conflict', '对话动作已变化，请核对原操作') from None
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '动作预约尚未确认，请按原编号核对', {'turn_id': turn_id}) from None
        return action, True

    @staticmethod
    def action_dict(action):
        return {key: getattr(action, key) for key in ('id', 'turn_id', 'kind', 'status', 'result')} | {
            'created_at': utc_storage_iso(action.created_at), 'updated_at': utc_storage_iso(action.updated_at)}

    def action(self, turn_id, kind, body, key):
        action, created = self.reserve_action(turn_id, kind, body, key)
        turn = self.turn(turn_id, sources=kind != 'stop')
        if created:
            from .native_events import runtime_for
            runtime = runtime_for(self.request)
            # 正在监控的线程串行接收停止预约；暂停的审批才新建监控，绝不重发completion。
            if kind != 'stop' or not runtime.active(turn_id):
                try:
                    runtime.launch(turn_id, self.actor, body, action_id=action.id)
                except DomainError:
                    action.status = 'failed'
                    turn.status, turn.error_code = 'unknown', 'native_chat_monitor_unavailable'
                    project_turn(self.session, turn)
                    self.session.commit()
                    raise
        return {'action': self.action_dict(action), 'turn': turn_dict(turn)}

    def reconcile(self, turn_id):
        """只读准确助手确认历史终态；不重新派发模型、停止或批准请求。"""
        turn = self.turn(turn_id)
        from .native_events import runtime_for
        if runtime_for(self.request).active(turn_id):
            return turn_dict(turn)
        if turn.status in TERMINAL:
            return turn_dict(turn)
        chat, identity, integration = self.chat(turn.conversation_id)
        adapter = self.user_adapter(identity, integration)
        self.session.commit()
        result = self._native_chat(adapter.chat(chat.native_chat_id), identity, expected_chat=chat.native_chat_id)
        try:
            message = assistant_from_chat(result, turn)
        except DomainError as exc:
            if exc.code != 'native_chat_message_binding_invalid':
                raise
            # 查不到准确消息也不能假定没有派发；原预约继续阻止重放。
            turn.status, turn.error_code = 'unknown', 'native_chat_message_unconfirmed'
        else:
            if awaiting_approvals(message):
                turn.status, turn.error_code = 'waiting_approval', None
            elif message.get('done') is True and not pending_calls(message):
                failed = bool(message.get('error')) or not visible_reply_text(message)
                turn.status = 'failed' if failed else 'succeeded'
                turn.error_code = 'native_chat_model_failed' if failed else None
            else:
                turn.status, turn.error_code = 'unknown', 'native_chat_terminal_unconfirmed'
        turn.error = '原生结果尚未确认，请核对消息记录' if turn.error_code else None
        turn.revision += 1
        if turn.status in TERMINAL:
            self.session.execute(update(NativeConversation).where(NativeConversation.id == chat.id,
                NativeConversation.active_turn_id == turn.id).values(active_turn_id=None))
        project_turn(self.session, turn)
        self.session.commit()
        return turn_dict(turn)
