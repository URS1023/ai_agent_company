"""Chat model choices must change actual execution, with workspace isolation.

中文说明：选择的模型必须真的影响本轮执行，且不同工作区之间互相隔离。
验证行为：可选模型列表需鉴权、脱敏且只含可用对话模型；普通用户可不经模型管理自行选择；
非法模型在写消息/运行之前被拒；所选模型持久化且嵌套生成继承；任务级选择不越界到其它工作区；
官方 DeepSeek 在两条路径上都使用受支持的工具模式。
隔离方式：monkeypatch + client 夹具（内存 SQLite + TestClient），不连业务库与外部模型。
"""
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Message, ModelProfile, Run
from app.modules.openwebui import runtime as native_runtime
from app.modules.runtime.service import create_message, finalize_run
from chat_native_fakes import block_live_native
from app.modules.runtime.task_control import TaskControl, current
from app.providers import model


def save(client, headers, name='alternate', **overrides):
    payload = {'profile_name': name, 'provider': 'deepseek', 'model': 'deepseek-flash',
               'endpoint': 'https://api.deepseek.com', 'api_key': 'isolated-key',
               'capabilities': ['chat', 'tools'], 'enabled': True, **overrides}
    response = client.post('/api/models/profiles', headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_choices_are_authenticated_sanitized_and_only_usable_chat_models(client, administrator):
    save(client, administrator)
    save(client, administrator, 'disabled', enabled=False)
    save(client, administrator, 'vectors', capabilities=['embedding'])
    save(client, administrator, 'nokey', api_key='')
    assert client.get('/api/chat/models').status_code == 401
    response = client.get('/api/chat/models', headers=administrator)
    assert response.status_code == 200
    items = response.json()['items']
    assert 'alternate' in {i['profile_name'] for i in items}
    assert not {'disabled', 'vectors', 'nokey'} & {i['profile_name'] for i in items}
    assert all(set(i) == {'profile_name', 'model', 'label', 'default'} for i in items)
    assert 'isolated-key' not in response.text and 'endpoint' not in response.text


def test_ordinary_chat_user_can_choose_without_model_administration(client, administrator, monkeypatch):
    from test_system_foundation import activate_user, create_role, create_user
    save(client, administrator)
    role = create_role(client, administrator, code='chat_model_user',
                       permissions=['home.view', 'chat.view', 'chat.create', 'chat.execute'])
    create_user(client, administrator, [role['id']], username='chat-model-user')
    member = activate_user(client, username='chat-model-user')
    assert client.get('/api/models/profiles', headers=member).status_code == 403
    choices = client.get('/api/chat/models', headers=member)
    assert choices.status_code == 200, choices.text
    assert 'alternate' in {item['profile_name'] for item in choices.json()['items']}
    seen = []
    def reply(db, run, *args, **kwargs):
        seen.append(run.model_profile)
        create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id, role='assistant', content='普通用户模型选择成功')
        finalize_run(db, run, status='completed')
        db.commit()
        yield {'type': 'run.completed', 'payload': {'status': 'completed'}}
    monkeypatch.setattr(native_runtime, 'run_chat', reply)
    cid = client.post('/api/chat/conversations', headers=member, json={}).json()['id']
    sent = client.post(f'/api/chat/conversations/{cid}/send', headers=member,
                       json={'content': '你好', 'model_profile': 'alternate'})
    assert 'run.completed' in sent.text, sent.text
    assert seen == ['alternate']


@pytest.mark.parametrize('name', ['missing', 'disabled', 'vectors'])
def test_invalid_model_rejected_before_any_message_or_run(client, administrator, db, name):
    save(client, administrator, 'disabled', enabled=False)
    save(client, administrator, 'vectors', capabilities=['embedding'])
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    sent = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                       json={'content': 'hello', 'model_profile': name})
    assert sent.status_code == 422, sent.text
    assert not db.scalars(select(Message).where(Message.conversation_id == cid)).all()
    assert not db.scalars(select(Run).where(Run.conversation_id == cid)).all()


def test_selected_model_persists_and_nested_generation_inherits_it(client, administrator, db, monkeypatch):
    save(client, administrator)
    seen = []
    def reply(db, run, *args, **kwargs):
        seen.append((run.model_profile, model._chat_profile('reasoning', run.workspace_id)))
        create_message(db, workspace_id=run.workspace_id, conversation_id=run.conversation_id, role='assistant', content='隔离回归回答')
        finalize_run(db, run, status='completed')
        db.commit()
        yield {'type': 'run.completed', 'payload': {'status': 'completed'}}
    monkeypatch.setattr(native_runtime, 'run_chat', reply)
    cid = client.post('/api/chat/conversations', headers=administrator, json={}).json()['id']
    sent = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                       json={'content': '你好', 'model_profile': 'alternate'})
    assert 'run.completed' in sent.text, sent.text
    assert seen == [('alternate', 'alternate')]
    detail = client.get(f'/api/chat/conversations/{cid}', headers=administrator).json()
    assert detail['last_run']['model_profile'] == 'alternate'
    assert detail['last_run']['context_snapshot']['model_profile'] == 'alternate'
    row = db.scalar(select(ModelProfile).where(ModelProfile.profile_name == 'alternate'))
    row.enabled = False
    db.commit()
    again = client.post(f'/api/chat/conversations/{cid}/send', headers=administrator,
                        json={'content': '继续', 'model_profile': 'alternate'})
    assert again.status_code == 422
    assert len(db.scalars(select(Run).where(Run.conversation_id == cid)).all()) == 1


def test_task_choice_does_not_override_other_workspaces_or_nonchat_capabilities():
    token = current.set(TaskControl(time.monotonic()+30, model_profile='alternate', model_workspace_id='one'))
    try:
        assert model._chat_profile('reasoning', 'one') == 'alternate'
        assert model._chat_profile('reasoning', 'two') == 'reasoning'
        assert model._chat_profile('embedding', 'one') == 'embedding'
        assert model._chat_profile('vision', 'one') == 'vision'
    finally:
        current.reset(token)
    assert model._chat_profile('reasoning', 'one') == 'reasoning'


@pytest.mark.parametrize('stream', [False, True])
def test_official_deepseek_uses_supported_tool_mode_in_both_paths(monkeypatch, stream):
    captured = []
    monkeypatch.setattr(model, '_profile_config', lambda *a, **kw: {
        'base_url': 'https://api.deepseek.com', 'api_key': 'isolated-key', 'model': 'deepseek-flash'})
    monkeypatch.setattr(model, '_governed_call', lambda *a: None)
    monkeypatch.setattr(model, '_persist_usage', lambda **kw: None)
    class Stream:
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='2', tool_calls=None), finish_reason='stop')])
        def close(self):
            pass
    def create(**kwargs):
        captured.append(kwargs)
        if stream:
            return Stream()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='2', tool_calls=None), finish_reason='stop')])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    fake.with_options = lambda **kwargs: fake
    fake.close = lambda: None
    monkeypatch.setattr(model, '_client', lambda *a: fake)
    fn = model.stream_chat if stream else model.chat_for_run
    assert fn('alternate', [{'role': 'user', 'content': '1+1'}])['content'] == '2'
    assert captured[0]['extra_body'] == {'thinking': {'type': 'disabled'}}
    assert model._chat_protocol_options({'base_url': 'https://other.invalid'}) == {}
