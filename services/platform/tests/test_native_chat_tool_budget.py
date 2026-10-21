"""工具策略与预算签名的纯/tmpSQLite/Mock回归；不调用原生模型或实际服务。"""

from copy import deepcopy
import asyncio
from datetime import timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import httpx
import jwt
import pytest

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import ResourceRecord, ResourceReference, Task
from agent_platform.modules.chat.native_models import NativeChatIdentity, NativeChatTurn, NativeConversation, NativeTurnAction, utcnow
from agent_platform.modules.chat.native_tool_policy import (
    configured_tool_policy, freeze_tool_policy, frozen_tool_policy, validate_tool_policy_config, validate_frozen_policy,
)
from agent_platform.modules.chat.native_tool_budget import (
    HEADER, TOKEN_TYPE, ISSUER, AUDIENCE, PATCH_ID, CAPABILITY_PATH, canonical_body, budget_header, verify_capability,
)
from agent_platform.modules.chat.native_turns import NativeTurnService
from agent_platform.modules.integrations.schemas import IntegrationPatch
from agent_platform.modules.integrations.service import IntegrationService
from test_native_chat_identity import context
from test_native_chat_turns import turns, execute

ALIAS = 'platform-business-fixture'
SERVER = 'business-readonly-fixture'
SECRET = 'separate-native-turn-budget-secret-' + 'b' * 40


def configuration():
    return {'id': 'instance-a', 'revision': 3, 'engine': 'openwebui', 'chat_model_group_id': 'group-a',
        'chat_model_ids': ['ds-fixture', ALIAS], 'delegation_service_identity_id': 'service-engine',
        'delegation_jwt_secret_ref': 'forward-secret', 'chat_budget_secret_ref': 'budget-secret',
        'chat_tool_policy': {'alias_model_id': ALIAS, 'server_id': SERVER}}


def test_policy_defaults_are_backend_only_and_freeze_does_not_affect_ordinary_model():
    data = configuration()
    policy = configured_tool_policy(data)
    assert [policy[key] for key in ('max_tool_calls', 'max_tool_iterations', 'max_model_requests', 'max_seconds')] == [4, 2, 3, 90]
    assert freeze_tool_policy(data, 'ds-fixture') is None
    frozen = freeze_tool_policy(data, ALIAS)
    assert frozen['integration_revision'] == 3 and frozen['budget_secret_ref'] == 'budget-secret'
    assert validate_frozen_policy(frozen, model_id=ALIAS) == frozen
    assert frozen_tool_policy(SimpleNamespace(request_metadata={}, model_id=ALIAS)) is None
    data['chat_tool_policy']['max_tool_calls'] = 1
    assert validate_frozen_policy(frozen, model_id=ALIAS)['max_tool_calls'] == 4


@pytest.mark.parametrize('change', ['bool_version', 'bool_cap', 'zero', 'too_many', 'deadline', 'extra', 'wildcard', 'server_url',
                                  'missing_secret', 'not_granted', 'same_key_ref', 'wrong_engine'])
def test_invalid_policy_is_not_downgraded_to_unrestricted_chat(change):
    data = configuration()
    policy = data['chat_tool_policy']
    if change == 'bool_version': policy['version'] = True
    elif change == 'bool_cap': policy['max_tool_calls'] = True
    elif change == 'zero': policy['max_model_requests'] = 0
    elif change == 'too_many': policy['max_tool_iterations'] = 17
    elif change == 'deadline': policy['max_seconds'] = 91
    elif change == 'extra': policy['tools'] = []
    elif change == 'wildcard': policy['alias_model_id'] = '*'
    elif change == 'server_url': policy['server_id'] = 'http://arbitrary'
    elif change == 'missing_secret': data['chat_budget_secret_ref'] = None
    elif change == 'not_granted': data['chat_model_ids'] = ['ds-fixture']
    elif change == 'same_key_ref': data['chat_budget_secret_ref'] = data['delegation_jwt_secret_ref']
    else: data['engine'] = 'dify'
    with pytest.raises(DomainError): configured_tool_policy(data)


def test_policy_write_requires_admin_and_independent_secret_value_but_unchanged_edit_is_allowed():
    data = configuration()
    vault = SimpleNamespace(get=lambda ref, workspace, session=None: SECRET if ref == 'budget-secret' else 'f' * 64)
    ordinary = Actor('user', 'ws', frozenset({'m02.*'}))
    with pytest.raises(DomainError) as denied: validate_tool_policy_config(ordinary, data, vault=vault)
    assert denied.value.status == 403
    admin = Actor('user', 'ws', frozenset({'m15.admin'}))
    validate_tool_policy_config(admin, data, vault=vault)
    validate_tool_policy_config(ordinary, data, existing=deepcopy(data), vault=vault)
    with pytest.raises(DomainError):
        validate_tool_policy_config(admin, data, vault=SimpleNamespace(get=lambda *args, **kwargs: SECRET))
    cleared = {**data, 'chat_tool_policy': None, 'chat_budget_secret_ref': None}
    with pytest.raises(DomainError): validate_tool_policy_config(ordinary, cleared, existing=data, vault=vault)
    validate_tool_policy_config(admin, cleared, existing=data, vault=vault)


@pytest.mark.parametrize('change', ['extra', 'missing', 'wrong_alias', 'revision', 'secret'])
def test_malformed_frozen_budget_never_uses_current_configuration(change):
    value = freeze_tool_policy(configuration(), ALIAS)
    if change == 'extra': value['enabled'] = True
    elif change == 'missing': del value['max_model_requests']
    elif change == 'wrong_alias': value['alias_model_id'] = 'another-model'
    elif change == 'revision': value['integration_revision'] = True
    else: value['budget_secret_ref'] = None
    with pytest.raises(DomainError) as error: validate_frozen_policy(value, model_id=ALIAS)
    assert error.value.status == 503


def test_integration_dto_keeps_policy_defaults_and_does_not_allow_browser_runtime_fields():
    parsed = IntegrationPatch(revision=2, chat_tool_policy={'alias_model_id': ALIAS, 'server_id': SERVER},
        chat_budget_secret_ref='budget-secret').model_dump(exclude_unset=True)
    assert parsed['chat_tool_policy']['alias_model_id'] == ALIAS
    from agent_platform.modules.chat.native_router import TurnInput, GenerationInput
    for cls, body in ((TurnInput, {'content': '问题'}), (GenerationInput, {})):
        for extra in ({'tools': []}, {'tool_ids': ['server:untrusted']}, {'tool_policy': {}}, {'tool_budget': {'calls_used': 0}}):
            with pytest.raises(ValueError): cls(revision=1, model_id=ALIAS, **extra, **body)
    with pytest.raises(ValueError):
        IntegrationPatch(revision=1, chat_tool_policy={'version': True, 'alias_model_id': ALIAS, 'server_id': SERVER})


@pytest.fixture
def budget(turns):
    session, actor, request, fake, chat = turns
    vault = request.app.state.secret_vault
    ref = vault.put(SECRET, workspace_id=actor.workspace_id, actor_id=actor.user_id, session=session)['id']
    forwarding = vault.put('distinct-tool-forward-secret-' + 'f' * 40, workspace_id=actor.workspace_id,
        actor_id=actor.user_id, session=session)['id']
    record = session.get(ResourceRecord, 'instance-a')
    record.data = {**record.data, **{key: value for key, value in configuration().items() if key not in {'id', 'revision'}},
        'chat_budget_secret_ref': ref, 'delegation_jwt_secret_ref': forwarding}
    session.commit()
    state = SimpleNamespace(headers=[], capability={'version': 1, 'patch_id': PATCH_ID,
        'alias_model_id': ALIAS, 'server_id': SERVER, 'ledger_ready': True}, invalid_alias=False, after_capability=None)
    def upstream(req):
        path = req.url.path
        data = json.loads(req.content) if req.content else {}
        if path == '/api/models':
            fake.calls.append((req.method, path, data, None))
            return httpx.Response(200, json={'data': [{'id': 'ds-fixture'}, {'id': ALIAS, 'owned_by': 'openai',
                'info': {'base_model_id': 'ds-fixture', 'meta': {'capabilities': {'builtin_tools': state.invalid_alias}}}}]})
        if path == CAPABILITY_PATH:
            fake.calls.append((req.method, path, data, None))
            if state.after_capability:
                state.after_capability()
            if state.capability is None: return httpx.Response(404, json={'detail': 'no budget implementation'})
            return httpx.Response(200, json=state.capability)
        if path == '/api/chat/completions' and data.get('model') == ALIAS:
            fake.calls.append((req.method, path, deepcopy(data), None))
            state.headers.append(req.headers.get(HEADER))
            assert 'tools' not in data and data['tool_ids'] == ['server:' + SERVER]
            assert data['params']['function_calling'] == 'native' and data['skill_ids'] == []
            assert data['features']['memory'] is False and data['background_tasks'] == {}
            claims = jwt.decode(req.headers[HEADER], SECRET, algorithms=['HS256'], audience=AUDIENCE, issuer=ISSUER)
            assert claims['body_sha256'] == hashlib.sha256(canonical_body(data)).hexdigest()
            assert claims['message_id'] == data['id'] and data['session_id'] == 'native-namespace-sid'
            answer = fake.save_messages(data)
            fake.events.append({'type': 'chat:completion', 'data': {'done': answer['done'], 'content': answer['content']}})
            return httpx.Response(200, content=b'null', headers={'Content-Type': 'application/json'})
        if path.endswith('/resolve'):
            state.headers.append(req.headers.get(HEADER))
        return fake(req)
    request.app.state.native_http_transport = httpx.MockTransport(upstream)
    return SimpleNamespace(parts=turns, service=NativeTurnService(session, actor, request), state=state, secret_ref=ref)


def reserve_budget(budget, *, model=ALIAS, key='tool-send', **extra):
    chat = budget.parts[0].get(NativeConversation, budget.parts[4]['id'])
    body = {'revision': chat.revision, 'model_id': model, 'content': '只调用Mock工具模型', 'parent_message_id': None,
        'max_tokens': 64, 'temperature': 0, 'attachment_ids': [], **extra}
    row, payload = budget.service.reserve(chat.id, body, key)
    return row, payload, body


def test_alias_reservation_freezes_policy_and_secret_ref_before_single_dispatch(budget):
    row, payload, body = reserve_budget(budget)
    policy = frozen_tool_policy(row)
    assert policy['max_tool_calls'] == 4 and row.request_metadata['tool_budget'] == {'version': 1, 'calls_used': 0}
    from sqlalchemy import select
    link = budget.parts[0].scalar(select(ResourceReference).where(ResourceReference.source_id == row.id))
    assert link.target_id == budget.secret_ref
    finished, _, _ = execute(budget.parts, row, payload)
    assert finished.status == 'succeeded', finished.error_code
    assert budget.parts[0].get(Task, row.task_id).status == 'succeeded'
    claims = jwt.decode(budget.state.headers[0], SECRET, algorithms=['HS256'], audience=AUDIENCE, issuer=ISSUER)
    assert jwt.get_unverified_header(budget.state.headers[0])['typ'] == TOKEN_TYPE
    assert claims['jti'] == row.id == claims['dispatch_nonce'] and claims['dispatch_kind'] == 'completion'
    assert claims['max_tool_calls'] == 4 and claims['max_model_requests'] == 3 and claims['max_tool_iterations'] == 2
    assert claims['max_tokens'] == 64 and claims['deadline'] == claims['exp']
    assert claims['sub'] == budget.parts[0].get(NativeChatIdentity, row.identity_id).native_user_id
    assert budget.service.reserve(row.conversation_id, body, 'tool-send')[1] is None
    assert sum(path == '/api/chat/completions' for _, path, _, _ in budget.parts[3].calls) == 1
    assert 'eyJ' not in json.dumps(row.request_metadata) and 'tools' not in budget.service.detail_turn(row.id)


def test_ordinary_model_payload_and_no_extra_budget_preflight_are_unchanged(budget):
    row, payload, _ = reserve_budget(budget, model='ds-fixture')
    assert frozen_tool_policy(row) is None and payload['tools'] == [] and payload['tool_ids'] == []
    assert payload['params']['function_calling'] == 'legacy'
    finished, _, _ = execute(budget.parts, row, payload)
    assert finished.status == 'succeeded' and budget.state.headers == []
    assert not any(path == CAPABILITY_PATH for _, path, _, _ in budget.parts[3].calls)


@pytest.mark.parametrize('change', ['missing', 'version', 'server', 'alias', 'ledger', 'extra'])
def test_unpatched_or_wrong_native_budget_implementation_blocks_before_model(budget, change):
    if change == 'missing': budget.state.capability = None
    elif change == 'version': budget.state.capability['version'] = True
    elif change == 'server': budget.state.capability['server_id'] = 'other'
    elif change == 'alias': budget.state.capability['alias_model_id'] = 'other'
    elif change == 'ledger': budget.state.capability['ledger_ready'] = False
    else: budget.state.capability['secret'] = 'must-not-be-returned'
    row, payload, body = reserve_budget(budget)
    finished, _, _ = execute(budget.parts, row, payload)
    assert finished.status == 'failed'
    assert budget.parts[0].get(NativeConversation, row.conversation_id).active_turn_id is None
    assert budget.service.reserve(row.conversation_id, body, 'tool-send')[1] is None
    assert not any(path == '/api/chat/completions' for _, path, _, _ in budget.parts[3].calls)


def test_alias_with_builtin_tools_enabled_is_rejected_before_turn_reservation(budget):
    budget.state.invalid_alias = True
    with pytest.raises(DomainError) as error: reserve_budget(budget)
    assert error.value.code == 'native_chat_tool_alias_invalid'
    from sqlalchemy import select
    assert budget.parts[0].scalar(select(NativeChatTurn)) is None


def test_resolution_preserves_original_deadline_and_uses_persisted_action_nonce(budget):
    session, actor, request, fake, _ = budget.parts
    row, payload, _ = reserve_budget(budget)
    fake.complete = False
    fake.save_messages(payload, pending=True)
    row.status = 'waiting_approval'
    deadline = row.deadline_at
    session.get(Task, row.task_id).status = 'waiting_review'
    session.commit()
    body = {'call_id': 'approval-1', 'action': 'approve'}
    action, fresh = budget.service.reserve_action(row.id, 'resolve', body, 'approval-tool')
    assert fresh and row.deadline_at.replace(tzinfo=timezone.utc) == deadline.replace(tzinfo=timezone.utc)
    finished, _, _ = execute(budget.parts, row, body, action.id)
    assert finished.status == 'succeeded', finished.error_code
    claims = jwt.decode(budget.state.headers[0], SECRET, algorithms=['HS256'], audience=AUDIENCE, issuer=ISSUER)
    assert claims['dispatch_kind'] == 'resolution' and claims['dispatch_nonce'] == action.id and claims['jti'] == row.id
    assert claims['deadline'] == int(deadline.timestamp())
    assert claims['body_sha256'] == hashlib.sha256(canonical_body(body)).hexdigest()
    assert not any(path == '/api/chat/completions' for _, path, _, _ in fake.calls)


def test_expired_resolution_rejected_but_exact_stop_can_clean_without_extending_model_deadline(budget):
    session, _, _, fake, _ = budget.parts
    row, payload, _ = reserve_budget(budget)
    fake.complete = False
    fake.save_messages(payload, pending=True)
    row.status = 'waiting_approval'
    session.get(Task, row.task_id).status = 'waiting_review'
    row.deadline_at = utcnow() - timedelta(seconds=1)
    deadline = row.deadline_at
    session.commit()
    with pytest.raises(DomainError) as error:
        budget.service.reserve_action(row.id, 'resolve', {'call_id': 'approval-1', 'action': 'approve'}, 'too-late')
    assert error.value.code == 'native_chat_deadline_exceeded'
    action, fresh = budget.service.reserve_action(row.id, 'stop', {}, 'stop-tool')
    assert fresh and row.deadline_at.replace(tzinfo=timezone.utc) == deadline.replace(tzinfo=timezone.utc)
    finished, _, runner = execute(budget.parts, row, {}, action.id)
    assert finished.status == 'cancelled' and runner.budget_stop_cleanup
    assert budget.state.headers == []
    assert not any(path == '/api/chat/completions' for _, path, _, _ in fake.calls)


@pytest.mark.parametrize('resume', [False, True])
def test_cancel_during_capability_probe_never_posts_completion_or_approval(budget, resume):
    session, _, _, fake, _ = budget.parts
    row, payload, _ = reserve_budget(budget)
    action = None
    if resume:
        fake.complete = False
        fake.save_messages(payload, pending=True)
        row.status = 'waiting_approval'
        session.get(Task, row.task_id).status = 'waiting_review'
        session.commit()
        payload = {'call_id': 'approval-1', 'action': 'approve'}
        action, _ = budget.service.reserve_action(row.id, 'resolve', payload, 'resolve-before-cancel')
    def cancel():
        # 使用真实预约服务同时更新Turn和Task，模拟另一个已通过鉴权的停止请求。
        session.expire_all()
        stop, created = budget.service.reserve_action(row.id, 'stop', {}, 'cancel-probe')
        assert created
        budget.state.stop_action_id = stop.id
    budget.state.after_capability = cancel
    finished, _, _ = execute(budget.parts, row, payload, action.id if action else None)
    assert finished.status == 'cancelled', finished.error_code
    assert not any(path == '/api/chat/completions' or path.endswith('/resolve') for _, path, _, _ in fake.calls)
    assert any(path.endswith('/stop') for _, path, _, _ in fake.calls) is resume
    assert session.get(NativeTurnAction, budget.state.stop_action_id).status == 'succeeded'
    if action:
        assert session.get(NativeTurnAction, action.id).status == 'failed'


def test_budget_header_refuses_unreserved_nonce_and_exact_body_hash_changes(budget):
    session, actor, request, _, _ = budget.parts
    row, payload, _ = reserve_budget(budget)
    identity = session.get(NativeChatIdentity, row.identity_id)
    with pytest.raises(DomainError):
        budget_header(request, session, actor, row, identity,
            '/api/v1/chats/' + row.native_chat_id + '/messages/' + row.assistant_message_id + '/resolve', {}, action_id='forged')
    one = jwt.decode(budget_header(request, session, actor, row, identity, '/api/chat/completions', payload), SECRET,
        algorithms=['HS256'], audience=AUDIENCE, issuer=ISSUER)
    changed = deepcopy(payload)
    changed['user_message']['content'] = '不同正文导致不同HTTP请求摘要'
    two = jwt.decode(budget_header(request, session, actor, row, identity, '/api/chat/completions', changed),
        SECRET, algorithms=['HS256'], audience=AUDIENCE, issuer=ISSUER)
    assert one['body_sha256'] != two['body_sha256']
    assert canonical_body({'b': 2, '中': '文', 'a': 1}) == canonical_body({'a': 1, '中': '文', 'b': 2})
    with pytest.raises(DomainError):
        budget_header(request, session, actor, row, identity, '/api/chat/completions', {**payload, 'tools': []})


def test_budget_secret_delete_protection_survives_clearing_current_configuration(budget):
    session, actor, request, _, _ = budget.parts
    from agent_platform.modules.integrations.models import NativeRun
    NativeRun.__table__.create(session.bind)
    row, _, _ = reserve_budget(budget)
    integration = session.get(ResourceRecord, 'instance-a')
    integration.data = {**integration.data, 'chat_tool_policy': None, 'chat_budget_secret_ref': None}
    session.commit()
    references = IntegrationService(session, actor, request).credential_references(budget.secret_ref)
    assert any(item['kind'] == 'native_chat_turn_budget' and item['id'] == row.id for item in references)


def test_transport_only_adds_bounded_budget_header_to_exact_generation_writes():
    from agent_platform.adapters.native import NativeConnection
    from agent_platform.modules.chat.native_transport import NativeChatAdapter
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'status': True})
    adapter = NativeChatAdapter(NativeConnection('http://127.0.0.1:13000', 'ordinary-native-jwt'),
        transport=httpx.MockTransport(upstream))
    asyncio.run(adapter.async_call('POST', '/api/chat/completions', payload={}, budget_token='header.payload.signature'))
    assert calls[0].headers[HEADER] == 'header.payload.signature'
    assert calls[0].headers['authorization'] == 'Bearer ordinary-native-jwt'
    for method, path, token in (('GET', CAPABILITY_PATH, 'header.payload.signature'),
                                ('POST', '/api/v1/auths/signin', 'header.payload.signature'),
                                ('POST', '/arbitrary/resolve', 'header.payload.signature'),
                                ('POST', '/api/chat/completions', 'bad\r\nheader')):
        with pytest.raises(DomainError): asyncio.run(adapter.async_call(method, path, budget_token=token))
    assert len(calls) == 1
