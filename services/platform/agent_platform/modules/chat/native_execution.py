"""监控原生一次聊天请求与真实Socket事件；这里没有模型生成或工具调用循环。"""

import asyncio
from datetime import datetime, timezone
import time

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.adapters.native import segment
from agent_platform.core.context import resolve_actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import LoginSession
from .native_models import NativeChatTurn, NativeTurnAction, NativeConversation, utcnow
from .native_service import NativeChatService
from .native_sources import authorize_conversation_sources
from .native_socket import NativeSocket
from .native_tasks import project_turn
from .native_turns import assistant_from_chat, pending_calls, awaiting_approvals, visible_reply_text


def public_event_data(value, depth=0):
    """转发正文与工具状态，但不转发内部会话凭据、请求头或原生附件下载地址。"""
    if depth > 64:
        raise DomainError(502, 'native_chat_event_budget_exceeded', '对话事件层级超过读取预算')
    if isinstance(value, list):
        return [public_event_data(item, depth + 1) for item in value]
    if isinstance(value, dict):
        hidden = {'meta', 'metadata', 'authorization', 'token', 'access_token', 'refresh_token',
            'password', 'headers', 'cookie', 'session_id'}
        result = {key: public_event_data(item, depth + 1) for key, item in value.items()
            if key.lower() not in hidden and key != 'files'}
        if isinstance(value.get('files'), list):
            result['files'] = [{'type': 'file', 'name': item.get('name', '附件')}
                for item in value['files'] if isinstance(item, dict)]
        return result
    return value


def current_actor(session, actor, request):
    """持续流每次交付前重查当前平台会话与角色，退出登录或撤权后不继续转发正文。"""
    resolver = getattr(request.app.state, 'native_chat_actor_resolver', None)
    if resolver is not None:
        return resolver(session, actor)
    login = session.get(LoginSession, actor.session_id)
    if (login is None or login.revoked or login.user_id != actor.user_id or login.workspace_id != actor.workspace_id
            or datetime.fromisoformat(login.expires_at) <= datetime.now(timezone.utc)):
        raise DomainError(401, 'native_chat_session_revoked', '对话登录状态已失效')
    value = resolve_actor(session, login)
    value.require('m02.read')
    return value


class NativeTurnRunner:
    """单独线程拥有一个Session；Socket回调只排队，状态和事件在本线程串行提交。"""
    def __init__(self, request, actor, turn_id, control, payload, action_id=None):
        self.request, self.actor, self.turn_id = request, actor, turn_id
        self.control, self.payload, self.action_id = control, payload, action_id
        self.adapter, self.identity, self.turn, self.chat, self.socket = None, None, None, None, None
        self.native_chat_id = None
        self.dispatched, self.storage_failed = False, False
        self.deadline = None

    def remaining(self, *, cleanup=False):
        value = self.deadline - time.monotonic() - (2 if cleanup else 5)
        if value <= 0:
            raise DomainError(504, 'native_chat_deadline_exceeded', '本次对话达到总时限，请核对运行记录')
        return value

    def fresh(self):
        self.session.expire_all()
        self.actor = current_actor(self.session, self.actor, self.request)
        self.turn = self.session.get(NativeChatTurn, self.turn_id)
        if self.turn is None:
            raise DomainError(404, 'native_turn_not_found', '对话运行不存在')
        self.chat = self.session.get(NativeConversation, self.turn.conversation_id)
        if (self.turn.user_id != self.actor.user_id or self.turn.workspace_id != self.actor.workspace_id
                or self.chat is None or self.chat.active_turn_id != self.turn_id):
            raise DomainError(409, 'native_chat_binding_invalid', '对话运行归属已变化')
        if self.turn.status != 'cancel_requested':
            authorize_conversation_sources(self.session, self.actor, self.chat)
        self.session.commit()

    async def call(self, method, path, *, payload=None, allow_null=False, cleanup=False):
        timeout = min(3 if cleanup else 20, self.remaining(cleanup=cleanup))
        expires = time.monotonic() + timeout
        work = asyncio.create_task(self.adapter.async_call(method, path, payload=payload, allow_null=allow_null,
            timeout_seconds=timeout))
        try:
            while True:
                if self.control.shutdown_requested.is_set() and not cleanup:
                    raise DomainError(503, 'native_chat_runtime_stopping', '服务正在关闭，请核对原运行')
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise DomainError(504, 'native_chat_timeout', '原生对话响应超时，请核对原操作')
                done, _ = await asyncio.wait({work}, timeout=min(0.2, remaining))
                if done:
                    return work.result()
        finally:
            if not work.done():
                work.cancel()
                try:
                    await work
                except (Exception, asyncio.CancelledError):
                    pass

    async def message(self, *, cleanup=False):
        result = await self.call('GET', '/api/v1/chats/' + segment(self.turn.native_chat_id), cleanup=cleanup)
        NativeChatService._native_chat(result, self.identity, expected_chat=self.turn.native_chat_id)
        return assistant_from_chat(result, self.turn)

    def save_state(self, status, *, code=None, error=None):
        self.turn.status, self.turn.error_code, self.turn.error = status, code, error
        self.turn.revision += 1
        if status in {'succeeded', 'failed', 'cancelled'}:
            self.session.execute(update(NativeConversation).where(NativeConversation.id == self.chat.id,
                NativeConversation.active_turn_id == self.turn.id).values(active_turn_id=None))
            for action in self.session.scalars(select(NativeTurnAction).where(NativeTurnAction.turn_id == self.turn.id,
                    NativeTurnAction.kind == 'stop', NativeTurnAction.status == 'reserved')):
                action.status, action.result = 'succeeded', {'confirmed': True, 'already_finished': status != 'cancelled'}
        project_turn(self.session, self.turn)
        self.session.commit()
        self.control.publish('state', {'status': status, 'turn_id': self.turn_id, 'error_code': code})

    @staticmethod
    def event_metadata(event):
        value = event.get('data') if isinstance(event.get('data'), dict) else {}
        usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
        return {'done': value.get('done') is True, 'has_error': bool(value.get('error')),
            'usage': {key: usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                if type(usage.get(key)) in {int, float}},
            'pending_call_ids': [item.get('call_id') or item.get('id') for item in awaiting_approvals(value)]}

    async def stop(self, action=None):
        """POST成功后仍核准确助手done及原生任务清空，不拿200冒充停止完成。"""
        if action is not None:
            action.status = 'dispatching'
            self.session.commit()
        before = None
        try:
            before = await self.message(cleanup=True)
        except DomainError as exc:
            if exc.code != 'native_chat_message_binding_invalid':
                raise
        if before is not None and before.get('done') is True and not pending_calls(before):
            self.save_state('failed' if before.get('error') or not visible_reply_text(before) else 'succeeded')
            result = {'already_finished': True, 'confirmed': True}
        else:
            result = await self.call('POST', f'/api/tasks/chat/{segment(self.turn.native_chat_id)}/stop', cleanup=True)
            tasks = await self.call('GET', f'/api/tasks/chat/{segment(self.turn.native_chat_id)}', cleanup=True)
            message = await self.message(cleanup=True)
            confirmed = (isinstance(result, dict) and result.get('status') is True and isinstance(tasks, dict)
                and tasks.get('task_ids') == [] and message.get('done') is True and not pending_calls(message))
            result = {'confirmed': confirmed}
            if not confirmed:
                raise DomainError(502, 'native_chat_stop_unconfirmed', '原生停止结果尚未确认，请核对原运行')
            self.save_state('cancelled')
        if action is not None:
            action.status, action.result = 'succeeded', result
            self.session.commit()
        return result

    async def dispatch(self):
        self.fresh()
        self.actor.require('m02.write')
        if self.turn.status == 'cancel_requested' and self.action_id is None:
            # 该线程尚未发起completion，数据库取消标记先于唯一派发，可确认未生成。
            self.save_state('cancelled')
            for action in self.session.scalars(select(NativeTurnAction).where(NativeTurnAction.turn_id == self.turn.id,
                    NativeTurnAction.kind == 'stop', NativeTurnAction.status == 'reserved')):
                action.status, action.result = 'succeeded', {'confirmed': True, 'not_dispatched': True}
            self.session.commit()
            return False
        if self.action_id:
            action = self.session.get(NativeTurnAction, self.action_id)
            if action is None or action.turn_id != self.turn.id or action.status != 'reserved':
                raise DomainError(409, 'native_chat_action_conflict', '该对话动作已派发或需要核对')
            if action.kind == 'stop':
                self.dispatched = True
                await self.stop(action)
                return False
            action.status = 'dispatching'
        else:
            if self.turn.status != 'reserved':
                raise DomainError(409, 'native_chat_dispatch_conflict', '该消息已经派发或需要核对')
            self.turn.status = 'running'
        project_turn(self.session, self.turn)
        self.session.commit()
        self.dispatched = True
        if self.action_id:
            response = await self.call('POST', f'/api/v1/chats/{segment(self.turn.native_chat_id)}/messages/'
                f'{segment(self.turn.assistant_message_id)}/resolve', payload=self.payload)
            if not isinstance(response, dict) or response.get('status') is not True:
                raise DomainError(502, 'native_chat_approval_unconfirmed', '原生服务未确认本次动作')
            action.status = 'succeeded'
            action.result = {'accepted': True}
        else:
            self.payload['session_id'] = self.sid
            response = await self.call('POST', '/api/chat/completions', payload=self.payload, allow_null=True)
        if response is not None:
            if not isinstance(response, dict) or response.get('chat_id') not in {None, self.turn.native_chat_id}:
                raise DomainError(502, 'native_chat_dispatch_invalid', '原生服务返回了其他对话标识')
            ids = response.get('task_ids', [])
            if not isinstance(ids, list) or len(ids) > 1 or any(not isinstance(value, str) for value in ids):
                raise DomainError(502, 'native_chat_dispatch_invalid', '原生任务标识不正确')
            self.turn.native_task_ids = ids
        self.session.commit()
        return True

    async def monitor(self):
        while True:
            self.remaining()
            self.fresh()
            action = self.session.scalar(select(NativeTurnAction).where(NativeTurnAction.turn_id == self.turn_id,
                NativeTurnAction.kind == 'stop', NativeTurnAction.status == 'reserved').order_by(NativeTurnAction.created_at).limit(1))
            if action is not None:
                await self.stop(action)
                return
            if self.control.shutdown_requested.is_set():
                raise DomainError(503, 'native_chat_runtime_stopping', '服务正在关闭，请核对原对话运行')
            self.session.commit()
            event = await self.socket.next_event(min(0.5, self.remaining()))
            if event is None:
                continue
            self.fresh()
            # 取消仍可清理已派发任务，但来源撤权后不能交付任何正文事件。
            authorize_conversation_sources(self.session, self.actor, self.chat)
            metadata = self.event_metadata(event)
            project_turn(self.session, self.turn, event_type=event['type'], metadata=metadata)
            self.session.commit()
            if event['type'] == 'chat:message:files':
                self.control.publish('attachments_updated', {'turn_id': self.turn_id})
            else:
                self.control.publish(event['type'], public_event_data(event.get('data')))
            if metadata['pending_call_ids']:
                message = await self.message()
                if awaiting_approvals(message):
                    self.save_state('waiting_approval')
                    return
            if metadata['done'] or metadata['has_error'] or event['type'] in {'chat:active', 'chat:tasks:cancel', 'chat:message:error'}:
                data = event.get('data') if isinstance(event.get('data'), dict) else {}
                if event['type'] == 'chat:active' and data.get('active') is not False:
                    continue
                message = await self.message()
                if message.get('done') is True and not pending_calls(message):
                    failed = bool(message.get('error')) or not visible_reply_text(message)
                    self.save_state('failed' if failed else 'succeeded',
                        code='native_chat_model_failed' if failed else None,
                        error='原生对话未返回有效回答，请查看消息记录' if failed else None)
                    return

    async def execute(self):
        with self.request.app.state.database.sessions() as session:
            self.session = session
            try:
                self.fresh()
                deadline = self.turn.deadline_at
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                self.deadline = time.monotonic() + max(0, (deadline - utcnow()).total_seconds())
                service = NativeChatService(session, self.actor, self.request)
                self.identity, integration = service.identity(self.turn.identity_id)
                self.native_chat_id = self.turn.native_chat_id
                self.adapter = service.user_adapter(self.identity, integration, verify_profile=False, allow_refresh=False)
                session.commit()
                profile = await self.call('GET', '/api/v1/auths/')
                service._user(profile, self.identity.native_user_id)
                factory = getattr(self.request.app.state, 'native_chat_socket_factory', NativeSocket)
                self.socket = factory(self.adapter, self.turn.native_chat_id, self.turn.assistant_message_id)
                self.sid = await self.socket.connect(min(5, self.remaining()))
                if await self.dispatch():
                    await self.monitor()
            except SQLAlchemyError:
                # 存储失败只停止本次准确chat；此后不再发补偿SQL或重新派发。
                self.storage_failed = True
                session.rollback()
                if self.dispatched and self.adapter is not None:
                    try:
                        await self.call('POST', f'/api/tasks/chat/{segment(self.native_chat_id)}/stop', cleanup=True)
                    except Exception:
                        pass
                self.control.publish('state', {'status': 'unknown', 'turn_id': self.turn_id,
                    'error_code': 'native_chat_state_unavailable', 'stop_confirmed': False})
            except Exception as exc:
                if self.dispatched and self.adapter is not None:
                    try:
                        # 清理HTTP只能用剩余3秒，不延长模型运行或重发completion。
                        await self.call('POST', f'/api/tasks/chat/{segment(self.native_chat_id)}/stop', cleanup=True)
                    except Exception:
                        pass
                try:
                    self.turn = session.get(NativeChatTurn, self.turn_id)
                    self.chat = session.get(NativeConversation, self.turn.conversation_id)
                    known_reject = isinstance(exc, DomainError) and exc.fields.get('native_http_status') in {400, 401, 403, 404, 409, 422, 429}
                    # 动作失败不能把先前已派发的生成标成失败或释放会话，须另读准确消息核对。
                    terminal_reject = self.action_id is None and (known_reject or not self.dispatched)
                    self.save_state('failed' if terminal_reject else 'unknown',
                        code=exc.code if isinstance(exc, DomainError) else 'native_chat_runtime_failed',
                        error='本次生成未完成，请核对原运行；系统不会重新派发')
                    if self.action_id:
                        action = session.get(NativeTurnAction, self.action_id)
                        action.status = 'failed' if known_reject else 'unknown'
                        session.commit()
                except SQLAlchemyError:
                    session.rollback()
                    self.storage_failed = True
            finally:
                if self.socket is not None:
                    try:
                        await asyncio.wait_for(self.socket.close(), timeout=max(0.01, min(2,
                            self.deadline - time.monotonic() if self.deadline is not None else 2)))
                    except (Exception, asyncio.CancelledError):
                        pass
                self.control.finish()
