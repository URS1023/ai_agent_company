"""临时SQLite与Mock原生HTTP/Socket验证一次派发，不启动服务或调用模型。"""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from test_native_chat_identity import context, ready_chat, NativeFake
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task, TaskEvent, Notification
from agent_platform.modules.chat.native_models import NativeConversation, NativeChatTurn, NativeTurnAction
from agent_platform.modules.chat.native_turns import NativeTurnService, branch_messages, assistant_from_chat
from agent_platform.modules.chat.native_execution import NativeTurnRunner
from agent_platform.modules.chat.native_events import TurnBuffer
from agent_platform.modules.chat.native_socket import NativeSocket


class SocketFake:
    """准确聊天的事件替身；没有模型生成或工具执行逻辑。"""
    def __init__(self, fake, adapter, chat, message):
        self.fake, self.chat, self.message = fake, chat, message
        self.closed = False
        fake.socket = self
    async def connect(self, timeout):
        return 'native-namespace-sid'
    async def next_event(self, timeout):
        if self.fake.events:
            return self.fake.events.pop(0)
        raise DomainError(503, 'native_chat_socket_closed', '模拟连接中断')
    async def close(self):
        self.closed = True


class ChatFake(NativeFake):
    def __init__(self):
        super().__init__()
        self.events, self.socket = [], None
        self.complete, self.stop_confirms, self.null_response = True, True, True
        self.after_completion = None
    def save_messages(self, data, *, pending=False):
        chat = self.chats[data['chat_id']]['chat']
        user = deepcopy(data['user_message'])
        messages = chat['history']['messages']
        messages[user['id']] = user
        if user['parentId']:
            messages[user['parentId']].setdefault('childrenIds', []).append(user['id'])
        answer = {'id': data['id'], 'parentId': user['id'], 'childrenIds': [], 'role': 'assistant',
            'model': data['model'], 'done': self.complete, 'content': '真实原生替身回答' if self.complete else '',
            'output': [{'type': 'function_call', 'call_id': 'approval-1', 'status': 'pending'}] if pending else []}
        messages[data['id']] = answer
        chat['history']['currentId'] = data['id']
        return answer
    def __call__(self, request):
        path, method = request.url.path, request.method
        data = json.loads(request.content) if request.content else {}
        if path == '/api/models':
            self.calls.append((method, path, deepcopy(data), None))
            return httpx.Response(200, json={'data': [{'id': 'ds-fixture', 'name': '授权模型'}]})
        if path == '/api/chat/completions':
            self.calls.append((method, path, deepcopy(data), None))
            assert data['session_id'] == 'native-namespace-sid'
            assert data['tools'] == [] and data['tool_ids'] == [] and data['features']['memory'] is False
            assert data['background_tasks'] == {} and data['params']['function_calling'] == 'legacy'
            answer = self.save_messages(data)
            self.events.append({'type': 'chat:completion', 'data': {'done': answer['done'],
                'content': answer['content'], 'usage': {'prompt_tokens': 4, 'completion_tokens': 5}}})
            if self.after_completion:
                self.after_completion()
            if self.null_response:
                return httpx.Response(200, content=b'null', headers={'Content-Type': 'application/json'})
            return httpx.Response(200, json={'status': True, 'chat_id': data['chat_id'], 'task_ids': ['native-task-fixture']})
        if path.startswith('/api/tasks/chat/'):
            self.calls.append((method, path, data, None))
            chat = self.chats[path.split('/')[4]]['chat']
            if method == 'POST' and self.stop_confirms:
                chat['history']['messages'][chat['history']['currentId']]['done'] = True
                chat['history']['messages'][chat['history']['currentId']]['output'] = []
            return httpx.Response(200, json={'status': True} if method == 'POST' else {
                'task_ids': [] if self.stop_confirms else ['still-running']})
        if path.endswith('/resolve'):
            self.calls.append((method, path, data, None))
            chat = self.chats[path.split('/')[4]]['chat']
            answer = chat['history']['messages'][path.split('/')[-2]]
            answer.update(done=True, content='原生审批后回答', output=[])
            self.events.append({'type': 'chat:completion', 'data': {'done': True}})
            return httpx.Response(200, json={'status': True, 'chat_id': path.split('/')[4], 'task_ids': ['resume-1']})
        return super().__call__(request)


@pytest.fixture
def turns(context):
    session, actor, request, _, engine = context
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (Task, TaskEvent, Notification)])
    fake = ChatFake()
    request.app.state.native_http_transport = httpx.MockTransport(fake)
    request.app.state.database = SimpleNamespace(sessions=sessionmaker(engine, expire_on_commit=False))
    request.app.state.native_chat_actor_resolver = lambda current, original: original
    request.app.state.native_chat_socket_factory = lambda *args: SocketFake(fake, *args)
    chat = ready_chat((session, actor, request, fake, engine))
    yield session, actor, request, fake, chat


def service(turns):
    return NativeTurnService(turns[0], turns[1], turns[2])


def reserve(turns, key='send-1'):
    chat = turns[4]
    body = {'revision': chat['revision'], 'model_id': 'ds-fixture', 'content': '只用于Mock的消息',
        'parent_message_id': None, 'max_tokens': 64, 'temperature': 0, 'attachment_ids': []}
    row, payload = service(turns).reserve(chat['id'], body, key)
    return row, payload, body


def execute(turns, row, payload, action_id=None):
    control = TurnBuffer(turns[1].user_id, turns[1].workspace_id)
    runner = NativeTurnRunner(turns[2], turns[1], row.id, control, payload, action_id)
    asyncio.run(runner.execute())
    turns[0].expire_all()
    return turns[0].get(NativeChatTurn, row.id), control, runner


@pytest.mark.parametrize('null_response', [True, False])
def test_saved_completion_uses_namespace_socket_and_exact_persisted_assistant(turns, null_response):
    turns[3].null_response = null_response
    row, payload, body = reserve(turns)
    assert turns[0].get(Task, row.task_id).inputs == {'turn_id': row.id, 'chat_id': row.conversation_id}
    finished, buffer, _ = execute(turns, row, payload)
    assert finished.status == 'succeeded', (finished.error_code, turns[3].calls, buffer.read(0))
    assert turns[0].get(Task, row.task_id).status == 'succeeded'
    assert turns[0].get(NativeConversation, row.conversation_id).active_turn_id is None
    assert buffer.read(0)[0][0]['data']['content'] == '真实原生替身回答'
    prior, dispatched = service(turns).reserve(row.conversation_id, body, 'send-1')
    assert prior.id == row.id and dispatched is None
    assert sum(path == '/api/chat/completions' for _, path, _, _ in turns[3].calls) == 1
    events = list(turns[0].scalars(select(TaskEvent).where(TaskEvent.task_id == row.task_id)))
    persisted = json.dumps([event.data for event in events], ensure_ascii=False)
    assert '真实原生替身回答' not in persisted and 'token' not in persisted.replace('prompt_tokens', '').replace('completion_tokens', '')


def test_disconnect_is_unknown_and_same_intent_never_dispatches_again(turns):
    turns[3].complete = False
    row, payload, body = reserve(turns)
    row, _, _ = execute(turns, row, payload)
    assert row.status == 'unknown'
    assert service(turns).reserve(row.conversation_id, body, 'send-1')[1] is None
    service(turns).reconcile(row.id)
    assert sum(path == '/api/chat/completions' for _, path, _, _ in turns[3].calls) == 1
    assert service(turns).detail_turn(row.id)['status'] in {'succeeded', 'failed', 'unknown'}


def test_request_hash_conflict_and_stale_revision_do_not_leave_orphan_tasks(turns):
    row, _, body = reserve(turns)
    with pytest.raises(DomainError) as conflict:
        service(turns).reserve(row.conversation_id, {**body, 'content': 'changed'}, 'send-1')
    assert conflict.value.code == 'idempotency_conflict'
    turns[0].rollback()
    assert len(list(turns[0].scalars(select(NativeChatTurn)))) == 1
    assert len(list(turns[0].scalars(select(Task)))) == 1


@pytest.mark.parametrize('confirmed', [True, False])
def test_stop_requires_task_clear_and_exact_assistant_not_only_http_200(turns, confirmed):
    fake = turns[3]
    fake.complete, fake.stop_confirms = False, confirmed
    row, payload, _ = reserve(turns)
    fake.save_messages(payload)
    row.status = 'running'
    turns[0].commit()
    action, _ = service(turns).reserve_action(row.id, 'stop', {}, 'stop-1')
    finished, _, _ = execute(turns, row, {}, action.id)
    assert finished.status == ('cancelled' if confirmed else 'unknown')
    result = turns[0].get(NativeTurnAction, action.id)
    assert result.status == ('succeeded' if confirmed else 'unknown')
    assert not any(path == '/api/chat/completions' for _, path, _, _ in fake.calls)


def test_approval_resumes_original_message_only_and_repeated_action_is_readonly(turns):
    fake = turns[3]
    fake.complete = False
    row, payload, _ = reserve(turns)
    fake.save_messages(payload, pending=True)
    row.status = 'waiting_approval'
    turns[0].get(Task, row.task_id).status = 'waiting_review'
    turns[0].commit()
    body = {'call_id': 'approval-1', 'action': 'approve'}
    action, _ = service(turns).reserve_action(row.id, 'resolve', body, 'approve-1')
    finished, _, _ = execute(turns, row, body, action.id)
    assert finished.status == 'succeeded', finished.error_code
    assert service(turns).reserve_action(row.id, 'resolve', body, 'approve-1')[1] is False
    assert sum(path.endswith('/resolve') for _, path, _, _ in fake.calls) == 1
    assert not any(path == '/api/chat/completions' for _, path, _, _ in fake.calls)


def test_database_event_failure_stops_exact_chat_without_compensation_sql(turns, monkeypatch):
    from agent_platform.modules.chat import native_execution
    real = native_execution.project_turn
    def failure(session, turn, **kwargs):
        if kwargs.get('event_type'):
            raise SQLAlchemyError('only-fixture')
        return real(session, turn, **kwargs)
    monkeypatch.setattr(native_execution, 'project_turn', failure)
    row, payload, _ = reserve(turns)
    _, control, runner = execute(turns, row, payload)
    assert runner.storage_failed is True
    assert any(path == '/api/tasks/chat/' + row.native_chat_id + '/stop' for _, path, _, _ in turns[3].calls)
    assert control.read(0)[0][-1]['data']['status'] == 'unknown'
    assert turns[0].get(Task, row.task_id).status == 'running'


def test_socket_discards_other_chat_other_message_and_internal_events():
    async def run():
        socket = NativeSocket(None, 'chat-a', 'assistant-a')
        event = {'chat_id': 'chat-a', 'message_id': 'assistant-a', 'data': {'type': 'chat:completion', 'data': {'done': True}}}
        for changes in ({'chat_id': 'chat-b'}, {'message_id': 'assistant-b'}, {'internal': True}):
            await socket.receive(event | changes)
        assert socket.queue.empty()
        await socket.receive(event)
        assert (await socket.next_event(0.1))['data']['done'] is True
    asyncio.run(run())


def test_message_binding_and_branch_cycles_are_rejected(turns):
    row, payload, _ = reserve(turns)
    turns[3].save_messages(payload)
    result = turns[3].chats[row.native_chat_id]
    result['chat']['history']['messages'][row.user_message_id]['childrenIds'] = ['unrelated']
    with pytest.raises(DomainError):
        assistant_from_chat(result, row)
    messages = {'a': {'id': 'a', 'role': 'assistant', 'done': True, 'parentId': 'a'}}
    with pytest.raises(DomainError):
        branch_messages({'messages': messages}, 'a')


def test_real_background_monitor_owns_its_session_and_exposes_only_platform_turn(turns):
    from agent_platform.modules.chat.native_events import runtime_for
    body = {'revision': turns[4]['revision'], 'model_id': 'ds-fixture', 'content': '后台线程纯Mock',
        'parent_message_id': None, 'max_tokens': 64, 'temperature': 0, 'attachment_ids': []}
    result = service(turns).start(turns[4]['id'], body, 'background-1')
    control = runtime_for(turns[2]).buffer(result['id'], turns[1])
    control.thread.join(timeout=3)
    assert not control.thread.is_alive() and control.finished
    turns[0].expire_all()
    assert service(turns).detail_turn(result['id'])['status'] == 'succeeded'
    assert not {'token', 'native_user_id', 'native_chat_id'} & set(result)
    assert service(turns).start(turns[4]['id'], body, 'background-1')['id'] == result['id']
    assert sum(path == '/api/chat/completions' for _, path, _, _ in turns[3].calls) == 1
    runtime_for(turns[2]).close()


def test_monitor_start_failure_keeps_failed_reservation_without_model_dispatch(turns):
    turns[2].app.state.native_chat_runtime = SimpleNamespace(launch=lambda *args: (_ for _ in ()).throw(
        DomainError(503, 'native_chat_monitor_unavailable', '模拟线程无法启动')))
    body = {'revision': turns[4]['revision'], 'model_id': 'ds-fixture', 'content': '未派发',
        'parent_message_id': None, 'max_tokens': 64, 'temperature': 0, 'attachment_ids': []}
    with pytest.raises(DomainError):
        service(turns).start(turns[4]['id'], body, 'start-failed')
    row = turns[0].scalar(select(NativeChatTurn))
    assert row.status == 'failed' and turns[0].get(Task, row.task_id).status == 'failed'
    assert service(turns).reserve(row.conversation_id, body, 'start-failed')[1] is None
    assert not any(path == '/api/chat/completions' for _, path, _, _ in turns[3].calls)


def test_event_subscription_rechecks_access_between_each_frame_and_disconnect_does_not_cancel(turns):
    from agent_platform.modules.chat.native_events import NativeChatRuntime, stream_events
    row, _, _ = reserve(turns)
    runtime = NativeChatRuntime(turns[2])
    turns[2].app.state.native_chat_runtime = runtime
    control = TurnBuffer(turns[1].user_id, turns[1].workspace_id)
    runtime.controls[row.id] = control
    control.publish('chat:completion', {'content': 'first'})
    control.publish('chat:completion', {'content': 'second'})
    events = stream_events(turns[2], turns[1], row.id)
    assert b'first' in next(events)
    def revoked(*args):
        raise DomainError(403, 'forbidden', '模拟当前授权已撤回')
    turns[2].app.state.native_chat_actor_resolver = revoked
    assert b'access_revoked' in next(events)
    assert list(events) == [] and not control.shutdown_requested.is_set()
    assert not any(path.endswith('/stop') for _, path, _, _ in turns[3].calls)


def test_public_history_omits_native_download_credentials_and_retains_original_tree(turns):
    from agent_platform.modules.chat.native_service import public_history
    source = {'currentId': 'message', 'messages': {'message': {'id': 'message', 'parentId': None,
        'childrenIds': [], 'content': '原文本', 'meta': {'token': 'fixture-secret'},
        'files': [{'id': 'private-native-file', 'name': '操作规程.pdf', 'url': 'http://native/private'}]}}}
    result = public_history(source)
    assert result['messages']['message']['files'] == [{'type': 'file', 'name': '操作规程.pdf'}]
    assert result['messages']['message']['content'] == '原文本'
    assert 'meta' not in result['messages']['message'] and 'meta' in source['messages']['message']


def test_event_projection_does_not_forward_native_auth_or_download_endpoints():
    from agent_platform.modules.chat.native_execution import public_event_data
    value = {'content': '答案', 'meta': {'token': 'fixture-secret'}, 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': '保留正文'}],
         'headers': {'Authorization': 'fixture-secret'}}],
        'files': [{'id': 'native-private', 'name': '说明.pdf', 'url': 'http://native/private'}]}
    result = public_event_data(value)
    assert 'fixture-secret' not in json.dumps(result) and 'native-private' not in json.dumps(result)
    assert result['output'][0]['content'][0]['text'] == '保留正文'


def test_native_json_reads_have_absolute_deadline_including_slow_chunks():
    from agent_platform.modules.chat.native_transport import NativeChatAdapter
    from agent_platform.adapters.native import NativeConnection
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{'
            await asyncio.sleep(0.03)
            yield b'}'
    adapter = NativeChatAdapter(NativeConnection('http://127.0.0.1:13000', 'fixture-token', timeout=0.01),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Slow())))
    with pytest.raises(DomainError) as failure:
        adapter.profile()
    assert failure.value.code == 'native_chat_timeout'


def test_completed_or_approved_tool_calls_are_not_presented_as_pending_approval():
    from agent_platform.modules.chat.native_turns import pending_calls, awaiting_approvals
    value = {'output': [{'type': 'function_call', 'call_id': 'finished', 'status': 'pending'},
        {'type': 'function_call_output', 'call_id': 'finished'},
        {'type': 'function_call', 'call_id': 'running', 'status': 'in_progress', 'approved': True},
        {'type': 'function_call', 'call_id': 'queued', 'status': 'queued', 'approved': True},
        {'type': 'function_call', 'call_id': 'ask', 'status': 'requires_approval'}]}
    assert {item['call_id'] for item in pending_calls(value)} == {'running', 'queued', 'ask'}
    assert [item['call_id'] for item in awaiting_approvals(value)] == ['ask']


def test_stale_revision_rolls_back_reserved_turn_and_task_before_any_dispatch(turns):
    chat = turns[0].get(NativeConversation, turns[4]['id'])
    chat.revision += 1
    turns[0].commit()
    with pytest.raises(DomainError) as conflict:
        reserve(turns)
    assert conflict.value.code == 'native_chat_edit_conflict'
    assert list(turns[0].scalars(select(NativeChatTurn))) == []
    assert list(turns[0].scalars(select(Task))) == []


def test_shutdown_cancels_owned_http_and_cleanup_calls_have_separate_three_second_cap():
    import time
    seen, cancelled = [], []
    control = TurnBuffer('user', 'workspace')
    runner = NativeTurnRunner(None, None, 'turn', control, {})
    runner.deadline = time.monotonic() + 90
    async def blocked(*args, **kwargs):
        seen.append(kwargs['timeout_seconds'])
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(True)
    runner.adapter = SimpleNamespace(async_call=blocked)
    async def run():
        task = asyncio.create_task(runner.call('POST', '/api/chat/completions'))
        await asyncio.sleep(0.01)
        control.shutdown_requested.set()
        with pytest.raises(DomainError) as failure:
            await asyncio.wait_for(task, 0.8)
        assert failure.value.code == 'native_chat_runtime_stopping'
        async def cleanup(*args, **kwargs):
            seen.append(kwargs['timeout_seconds'])
            return {'status': True}
        runner.adapter.async_call = cleanup
        assert await runner.call('POST', '/api/tasks/chat/owned/stop', cleanup=True) == {'status': True}
    asyncio.run(run())
    assert cancelled == [True] and seen[-1] <= 3
