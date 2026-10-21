"""纯分支计划及新临时SQLite/Mock原生协议回归；没有真实HTTP、模型或业务库。"""

from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.models import FileEntry, Task
from agent_platform.modules.chat.native_branches import (
    inspect_branch_source, plan_branch, verify_branch_source, verify_branch_user,
)
from agent_platform.modules.chat.native_execution import public_event_data
from agent_platform.modules.chat.native_file_models import NativeChatFile
from agent_platform.modules.chat.native_files import resolve_turn_attachments
from agent_platform.modules.chat.native_models import NativeConversation, NativeChatIdentity, NativeChatTurn, utcnow
from agent_platform.modules.chat.native_router import GenerationInput, EditAndSendInput, router
from agent_platform.modules.chat.native_turns import NativeTurnService, branch_messages, turn_dict
from test_native_chat_identity import context
from test_native_chat_turns import turns, reserve, execute


def history():
    return {'currentId': 'a1', 'messages': {
        'u0': {'id': 'u0', 'role': 'user', 'content': '第一问', 'parentId': None, 'childrenIds': ['a0', 'a0-other']},
        'a0': {'id': 'a0', 'role': 'assistant', 'content': '祖先回答', 'done': True, 'parentId': 'u0', 'childrenIds': ['u1']},
        'a0-other': {'id': 'a0-other', 'role': 'assistant', 'content': '其他分支', 'done': True,
                     'parentId': 'u0', 'childrenIds': []},
        'u1': {'id': 'u1', 'role': 'user', 'content': '第二问', 'parentId': 'a0', 'childrenIds': ['a1']},
        'a1': {'id': 'a1', 'role': 'assistant', 'content': '旧回答', 'done': True,
               'parentId': 'u1', 'childrenIds': []},
    }}


def plan(tree=None, kind='regenerate', target='a1', *, files=None, **kwargs):
    source = inspect_branch_source(tree or history(), kind, target, native_user_id='native-owner')
    return source, plan_branch(source, assistant_message_id='new-answer', model_id='ds-fixture',
        timestamp=1, native_files=files or [], **kwargs)


def test_regeneration_reuses_user_and_replays_only_selected_ancestors_without_mutation():
    tree = history()
    before = deepcopy(tree)
    source, result = plan(tree)
    assert result.user_message_id == 'u1' and result.parent_message_id == 'a0'
    assert result.user_message['childrenIds'] == ['a1', 'new-answer']
    assert [item['content'] for item in result.messages] == ['第一问', '祖先回答', '第二问']
    assert tree == before and '旧回答' not in repr(result) and '第二问' not in repr(source)
    assert result.request_metadata['source_message_id'] == 'a1'
    assert not {'content', 'messages', 'token'} & result.request_metadata.keys()


def test_finished_failed_answer_can_regenerate_and_structured_question_stays_structured():
    tree = history()
    tree['messages']['a1']['error'] = {'content': '已保存的上游失败'}
    tree['messages']['u1']['content'] = [{'type': 'text', 'text': '结构化原问题'}]
    _, result = plan(tree)
    assert result.user_message['content'] == tree['messages']['u1']['content']
    assert result.messages[-1]['content'] == tree['messages']['u1']['content']
    assert isinstance(result.messages[-1]['content'], list)


def test_edit_send_creates_sibling_user_and_first_question_exception_is_only_verified_plan():
    source, result = plan(kind='edit_send', target='u0', new_user_message_id='edited-u', content='修改后的问题')
    assert result.user_message_id == 'edited-u' and result.parent_message_id is None
    assert result.user_message['childrenIds'] == ['new-answer']
    assert result.messages == [{'role': 'user', 'content': '修改后的问题', 'files': []}]
    _, regen = plan(target='a0')
    assert regen.parent_message_id is None and regen.user_message_id == 'u0'
    with pytest.raises(DomainError) as error:
        branch_messages(history(), None)
    assert error.value.code == 'native_chat_parent_required'


@pytest.mark.parametrize('case', ['unfinished', 'wrong_role', 'foreign_user', 'missing', 'cycle', 'bad_children', 'root_assistant'])
def test_invalid_original_branch_is_rejected(case):
    tree = history()
    messages = tree['messages']
    if case == 'unfinished': messages['a1']['done'] = False
    elif case == 'wrong_role': messages['u1']['role'] = 'assistant'
    elif case == 'foreign_user': messages['u1']['user_id'] = 'other-user'
    elif case == 'missing': del messages['u1']
    elif case == 'cycle': messages['u0']['parentId'] = 'a0'
    elif case == 'bad_children': messages['a0']['childrenIds'] = []
    elif case == 'root_assistant': messages['a0']['parentId'] = None
    with pytest.raises(DomainError):
        plan(tree)


@pytest.mark.parametrize('changes', [
    {'content': '不允许改题'}, {'new_user_message_id': 'new-u'}, {'attachment_mode': 'replace'},
])
def test_regeneration_cannot_modify_original_question_or_attachments(changes):
    with pytest.raises(DomainError) as error:
        plan(**changes)
    assert error.value.code == 'native_chat_regeneration_changes_user'


def test_inherited_file_ids_must_match_exact_original_order_and_edit_can_explicitly_remove():
    tree = history()
    tree['messages']['u1']['files'] = [{'type': 'file', 'id': 'native-file', 'name': '原文.txt'}]
    source, result = plan(tree, files=[{'type': 'file', 'id': 'native-file', 'name': '已授权原文.txt'}])
    assert result.files[0]['id'] == 'native-file'
    with pytest.raises(DomainError): plan(tree)
    edited_source, edited = plan(tree, kind='edit_send', target='u1', new_user_message_id='new-u',
        content='删除附件后发问', attachment_mode='replace')
    assert edited.files == [] and edited.request_metadata['expected_native_file_ids'] == []
    verify_branch_user(edited.user_message, edited.request_metadata)


@pytest.mark.parametrize('case', ['content', 'file', 'ancestor_content', 'ancestor_output', 'parent'])
def test_source_changes_are_detected_by_snapshot_before_dispatch(case):
    tree = history()
    source, result = plan(tree)
    if case == 'content': tree['messages']['u1']['content'] = '原题被覆盖'
    elif case == 'file': tree['messages']['u1']['files'] = [{'type': 'file', 'id': 'injected'}]
    elif case == 'ancestor_content': tree['messages']['a0']['content'] = '不同上下文'
    elif case == 'ancestor_output': tree['messages']['a0']['output'] = [{'type': 'message', 'content': []}]
    else: tree['messages']['u1']['parentId'] = 'a0-other'
    with pytest.raises(DomainError):
        verify_branch_source(tree, result.request_metadata, native_user_id='native-owner')


def test_added_siblings_and_model_tracking_do_not_change_original_input_snapshot():
    tree = history()
    _, result = plan(tree)
    tree['messages']['u1']['childrenIds'].append('new-answer')
    tree['messages']['u1']['models'] = ['other-model']
    tree['messages']['a1']['content'] = '原回答修订不在本次重放输入内'
    verify_branch_source(tree, result.request_metadata, native_user_id='native-owner')
    changed = deepcopy(result.user_message)
    changed['content'] = '被原生覆盖的题目'
    with pytest.raises(DomainError): verify_branch_user(changed, result.request_metadata)


def test_native_id_collisions_and_malformed_children_are_not_silently_converted():
    source = inspect_branch_source(history(), 'regenerate', 'a1', native_user_id='native-owner')
    with pytest.raises(DomainError):
        plan_branch(source, assistant_message_id='a0', model_id='ds', timestamp=1, native_files=[])
    tree = history()
    tree['messages']['u1']['childrenIds'] = 'a1'
    with pytest.raises(DomainError): plan(tree)


def test_dto_distinguishes_inherit_remove_and_rejects_regen_content_and_native_history():
    base = {'revision': 2, 'model_id': 'ds'}
    assert EditAndSendInput(**base, content='修改').model_dump()['attachment_ids'] is None
    assert EditAndSendInput(**base, content='修改', attachment_ids=[]).attachment_ids == []
    for extra in ({'content': '修改'}, {'attachment_ids': []}, {'parent_message_id': 'self-reported'}):
        with pytest.raises(ValidationError): GenerationInput(**base, **extra)
    with pytest.raises(ValidationError): EditAndSendInput(**base, content='修改', history={})
    paths = {route.path for route in router.routes}
    assert '/native/conversations/{chat_id}/messages/{assistant_message_id}/regenerations' in paths
    assert '/native/conversations/{chat_id}/messages/{user_message_id}/edit-and-send' in paths


def test_chat_messages_event_keeps_partial_tree_and_strips_private_file_metadata():
    event = {'messages': {'u': {'id': 'u', 'role': 'user', 'files': [{'type': 'file', 'id': 'private-native-id',
        'url': 'http://127.0.0.1:13000/private', 'name': '原文.txt', 'meta': {'token': 'secret'}}],
        'meta': {'headers': {'Authorization': 'secret'}}, 'parentId': None}}, 'currentId': 'a'}
    result = public_event_data(event)
    assert result['currentId'] == 'a' and set(result['messages']) == {'u'}
    assert result['messages']['u']['files'] == [{'type': 'file', 'name': '原文.txt'}]
    assert 'private-native-id' not in json.dumps(result) and 'secret' not in json.dumps(result)


@pytest.fixture
def branches(turns):
    fake = turns[3]
    original = fake.save_messages
    def preserve_tree(data, **kwargs):
        # 按官方insert_chat_turn的关键语义保留原user内容/附件，不以假模型循环测试。
        messages = fake.chats[data['chat_id']]['chat']['history']['messages']
        before = deepcopy(messages)
        answer = original(data, **kwargs)
        user_id = data['user_message']['id']
        if user_id in before:
            saved = before[user_id]
            saved['models'] = data['user_message'].get('models', saved.get('models', []))
            saved.setdefault('childrenIds', []).append(data['id'])
            messages[user_id] = saved
            parent = saved.get('parentId')
            if parent is not None:
                messages[parent]['childrenIds'] = before[parent]['childrenIds']
        return answer
    fake.save_messages = preserve_tree
    row, payload, _ = reserve(turns, key='initial-send')
    finished, _, _ = execute(turns, row, payload)
    assert finished.status == 'succeeded'
    fake.calls.clear()
    return SimpleNamespace(parts=turns, old=finished, service=NativeTurnService(*turns[:3]))


def branch_request(branches, kind='regenerate', *, key='branch-1', extra=None):
    session = branches.parts[0]
    chat = session.get(NativeConversation, branches.old.conversation_id)
    body = {'revision': chat.revision, 'model_id': 'ds-fixture', 'max_tokens': 64, 'temperature': 0}
    if kind == 'edit_send': body.update(content='修改后提出新问题', attachment_ids=None)
    body.update(extra or {})
    source = branches.old.assistant_message_id if kind == 'regenerate' else branches.old.user_message_id
    row, payload = branches.service.reserve(chat.id, body, key, intent_kind=kind, source_message_id=source)
    return row, payload, body, source


@pytest.mark.parametrize('kind', ['regenerate', 'edit_send'])
def test_branch_uses_one_native_dispatch_preserves_old_tree_and_same_key_returns_original(branches, kind):
    parts, old = branches.parts, branches.old
    fake = parts[3]
    before = deepcopy(fake.chats[old.native_chat_id]['chat']['history']['messages'])
    row, payload, body, source = branch_request(branches, kind)
    assert not any(path == '/api/chat/completions' for _, path, _, _ in fake.calls)
    assert row.parent_message_id is None and payload['parent_id'] is None
    assert 'assistant_message_id' not in payload
    assert (row.user_message_id == old.user_message_id) is (kind == 'regenerate')
    assert row.assistant_message_id not in before
    finished, _, _ = execute(parts, row, payload)
    assert finished.status == 'succeeded', finished.error_code
    messages = fake.chats[old.native_chat_id]['chat']['history']['messages']
    assert messages[old.assistant_message_id] == before[old.assistant_message_id]
    assert messages[old.user_message_id]['content'] == before[old.user_message_id]['content']
    assert old.assistant_message_id in messages[old.user_message_id]['childrenIds']
    assert messages[row.assistant_message_id]['parentId'] == row.user_message_id
    assert finished.request_metadata['intent_kind'] == kind
    projected = turn_dict(finished)
    assert projected['intent_kind'] == kind and projected['source_message_id'] == source
    assert not {'native_chat_id', 'expected_native_file_ids', 'source_snapshot_sha256'} & projected.keys()
    prior, no_payload = branches.service.reserve(row.conversation_id, body, 'branch-1', intent_kind=kind, source_message_id=source)
    assert prior.id == row.id and no_payload is None
    assert sum(path == '/api/chat/completions' for _, path, _, _ in fake.calls) == 1
    assert parts[0].get(Task, row.task_id).status == 'succeeded'


def test_branch_unknown_is_not_replayed_and_cross_intent_same_key_conflicts(branches):
    parts = branches.parts
    row, payload, body, source = branch_request(branches)
    parts[3].complete = False
    row, _, _ = execute(parts, row, payload)
    assert row.status == 'unknown'
    assert branches.service.reserve(row.conversation_id, body, 'branch-1', intent_kind='regenerate', source_message_id=source)[1] is None
    with pytest.raises(DomainError) as conflict:
        branches.service.reserve(row.conversation_id, body, 'branch-1', intent_kind='edit_send', source_message_id=branches.old.user_message_id)
    assert conflict.value.code == 'idempotency_conflict'
    assert sum(path == '/api/chat/completions' for _, path, _, _ in parts[3].calls) == 1


@pytest.mark.parametrize('kind', ['regenerate', 'edit_send'])
def test_original_source_changed_before_dispatch_fails_without_model_and_keeps_reservation(branches, kind):
    parts = branches.parts
    row, payload, body, source = branch_request(branches, kind)
    parts[3].chats[row.native_chat_id]['chat']['history']['messages'][branches.old.user_message_id]['content'] = '原生UI改题'
    finished, _, _ = execute(parts, row, payload)
    assert finished.status == 'failed' and finished.error_code == 'native_chat_branch_source_changed'
    assert parts[0].get(NativeConversation, row.conversation_id).active_turn_id is None
    assert branches.service.reserve(row.conversation_id, body, 'branch-1', intent_kind=kind, source_message_id=source)[1] is None
    assert not any(path == '/api/chat/completions' for _, path, _, _ in parts[3].calls)


def test_dispatched_user_changed_never_confirms_success_and_stops_exact_chat(branches):
    parts = branches.parts
    row, payload, _, _ = branch_request(branches, 'edit_send')
    def change():
        parts[3].chats[row.native_chat_id]['chat']['history']['messages'][row.user_message_id]['content'] = '外部覆盖'
    parts[3].after_completion = change
    finished, _, _ = execute(parts, row, payload)
    assert finished.status == 'unknown' and finished.error_code == 'native_chat_message_binding_invalid'
    assert any(path == '/api/tasks/chat/' + row.native_chat_id + '/stop' for _, path, _, _ in parts[3].calls)
    assert parts[0].get(NativeConversation, row.conversation_id).active_turn_id == row.id


def test_branch_stop_uses_exact_new_assistant_without_touching_old_answer_or_dispatching_again(branches):
    parts = branches.parts
    row, payload, _, _ = branch_request(branches)
    old_answer = deepcopy(parts[3].chats[row.native_chat_id]['chat']['history']['messages'][branches.old.assistant_message_id])
    parts[3].complete = False
    parts[3].save_messages(payload)
    row.status = 'running'
    parts[0].commit()
    action, created = branches.service.reserve_action(row.id, 'stop', {}, 'stop-branch')
    assert created
    finished, _, _ = execute(parts, row, {}, action.id)
    assert finished.status == 'cancelled'
    assert parts[3].chats[row.native_chat_id]['chat']['history']['messages'][branches.old.assistant_message_id] == old_answer
    assert branches.service.reserve_action(row.id, 'stop', {}, 'stop-branch')[1] is False
    assert not any(path == '/api/chat/completions' for _, path, _, _ in parts[3].calls)


def test_branch_cas_stale_revision_and_identity_not_ready_leave_no_orphan_tasks(branches):
    session, _, _, fake, _ = branches.parts
    chat = session.get(NativeConversation, branches.old.conversation_id)
    before = len(list(session.scalars(select(Task))))
    with pytest.raises(DomainError) as error:
        branch_request(branches, extra={'revision': chat.revision - 1})
    assert error.value.code == 'native_chat_edit_conflict'
    assert len(list(session.scalars(select(Task)))) == before
    assert len(list(session.scalars(select(NativeChatTurn)))) == 1
    identity = session.get(NativeChatIdentity, chat.identity_id)
    identity.status = 'access_pending'
    session.commit()
    with pytest.raises(DomainError): branch_request(branches)
    assert len(list(session.scalars(select(Task)))) == before
    assert not any(path == '/api/chat/completions' for _, path, _, _ in fake.calls)


def attach_original(branches):
    """在新临时库建立已处理副本，不调用上传、解析或向量服务。"""
    session, actor, request, fake, _ = branches.parts
    Base.metadata.create_all(session.bind, tables=[FileEntry.__table__, NativeChatFile.__table__])
    source = FileService(session, actor, request.app.state.settings).store_bytes('manual.txt', b'owned test source', 'text/plain')
    mapping = NativeChatFile(id='mapping-old', workspace_id=actor.workspace_id, user_id=actor.user_id,
        identity_id=branches.old.identity_id, integration_id=branches.old.integration_id, file_id=source['id'],
        source_revision=source['revision'], source_sha256=source['sha256'], source_size=source['size'],
        source_name=source['name'], source_content_type=source['content_type'], request_key='mock-file', request_hash='fixture',
        native_file_id='native-file-old', status='ready', deadline_at=utcnow() + timedelta(seconds=60))
    session.add(mapping)
    session.flush()
    chat = session.get(NativeConversation, branches.old.conversation_id)
    resolved = resolve_turn_attachments(session, actor, chat, [mapping.id])
    branches.old.sources = resolved['sources']
    fake.chats[chat.native_chat_id]['chat']['history']['messages'][branches.old.user_message_id]['files'] = resolved['native_files']
    session.commit()
    return mapping, source


@pytest.mark.parametrize('selection', ['inherit', 'remove', 'replace'])
def test_edit_attachment_semantics_and_original_attachment_survives(branches, selection):
    mapping, source = attach_original(branches)
    extra = {} if selection == 'inherit' else {'attachment_ids': [] if selection == 'remove' else [mapping.id]}
    row, payload, _, _ = branch_request(branches, 'edit_send', extra=extra)
    assert payload['files'] == ([] if selection == 'remove' else payload['user_message']['files'])
    assert [file['id'] for file in payload['files']] == ([] if selection == 'remove' else ['native-file-old'])
    assert row.request_metadata['attachment_ids'] == ([] if selection == 'remove' else [mapping.id])
    finished, _, _ = execute(branches.parts, row, payload)
    assert finished.status == 'succeeded', finished.error_code
    original = branches.parts[3].chats[row.native_chat_id]['chat']['history']['messages'][branches.old.user_message_id]
    assert original['files'][0]['id'] == 'native-file-old'
    assert bool(finished.sources) is (selection != 'remove')


def test_regeneration_inherits_exact_platform_file_and_revoked_original_blocks_all_new_branches(branches):
    mapping, _ = attach_original(branches)
    row, payload, _, _ = branch_request(branches)
    assert payload['files'][0]['id'] == 'native-file-old' and row.sources[0]['mapping_id'] == mapping.id
    execute(branches.parts, row, payload)
    session, actor, _, fake, _ = branches.parts
    original = session.get(FileEntry, mapping.file_id)
    original.owner_id = 'different-user'
    original.visibility = 'private'
    session.commit()
    before = len(fake.calls)
    with pytest.raises(DomainError) as error: branch_request(branches, 'edit_send', key='after-revoke', extra={'attachment_ids': []})
    assert error.value.status in {403, 404} and len(fake.calls) == before


def test_cross_user_and_workspace_cannot_branch_another_users_chat(branches):
    session, actor, request, fake, _ = branches.parts
    for other in (Actor('other', actor.workspace_id, frozenset({'*'}), is_admin=True),
                  Actor(actor.user_id, 'other-workspace', actor.permissions)):
        service = NativeTurnService(session, other, request)
        before = len(fake.calls)
        with pytest.raises(DomainError) as error:
            service.reserve(branches.old.conversation_id, {}, 'forged', intent_kind='regenerate',
                source_message_id=branches.old.assistant_message_id)
        assert error.value.status == 404 and len(fake.calls) == before


def continue_once(branches):
    session = branches.parts[0]
    chat = session.get(NativeConversation, branches.old.conversation_id)
    body = {'revision': chat.revision, 'model_id': 'ds-fixture', 'content': '第二个问题',
        'parent_message_id': branches.old.assistant_message_id, 'max_tokens': 64, 'temperature': 0, 'attachment_ids': []}
    turn, payload = branches.service.reserve(chat.id, body, 'second-normal-send')
    result, _, _ = execute(branches.parts, turn, payload)
    assert result.status == 'succeeded'
    branches.old = result


@pytest.mark.parametrize('kind', ['regenerate', 'edit_send'])
def test_nested_branch_keeps_original_parent_links_and_all_old_siblings(branches, kind):
    first = branches.old
    continue_once(branches)
    fake = branches.parts[3]
    messages = fake.chats[first.native_chat_id]['chat']['history']['messages']
    before = deepcopy(messages)
    row, payload, _, _ = branch_request(branches, kind)
    assert row.parent_message_id == first.assistant_message_id
    assert [item['content'] for item in payload['messages'][:-1]] == [
        before[first.user_message_id]['content'], before[first.assistant_message_id]['content']]
    result, _, _ = execute(branches.parts, row, payload)
    assert result.status == 'succeeded', result.error_code
    assert messages[first.assistant_message_id]['childrenIds'].count(branches.old.user_message_id) == 1
    if kind == 'regenerate':
        assert messages[first.assistant_message_id]['childrenIds'] == before[first.assistant_message_id]['childrenIds']
        assert messages[branches.old.user_message_id]['childrenIds'] == [branches.old.assistant_message_id, row.assistant_message_id]
    else:
        assert messages[first.assistant_message_id]['childrenIds'] == [branches.old.user_message_id, row.user_message_id]
        assert messages[branches.old.user_message_id] == before[branches.old.user_message_id]


def test_replayed_business_receipts_are_frozen_and_current_revocation_still_blocks(branches, monkeypatch):
    from agent_platform.modules.chat import native_sources
    receipt = {'version': 1, 'workspace_id': branches.parts[1].workspace_id,
        'resource': {'kind': 'device', 'id': 'device-fixture'}, 'queries': [], 'files': []}
    revoked, checked = False, []
    def guard(session, actor, sources):
        checked.extend(deepcopy(sources))
        if revoked and sources:
            raise DomainError(403, 'delegated_source_denied', '模拟当前来源撤权')
    monkeypatch.setattr(native_sources, 'authorize_sources', guard)
    first = branches.old
    first.sources = [deepcopy(receipt)]
    branches.parts[0].commit()
    continue_once(branches)
    row, payload, _, _ = branch_request(branches)
    assert row.sources == [receipt] and checked
    execute(branches.parts, row, payload)
    revoked = True
    before = len(branches.parts[3].calls)
    with pytest.raises(DomainError) as error:
        branch_request(branches, 'edit_send', key='revoked-business-source')
    assert error.value.status == 403 and len(branches.parts[3].calls) == before


def test_ancestor_attachments_are_not_misattributed_to_edited_user(branches):
    attach_original(branches)
    first = branches.old
    continue_once(branches)
    row, payload, _, _ = branch_request(branches, 'edit_send', extra={'attachment_ids': []})
    assert row.sources == [] and payload['files'] == []
    assert payload['messages'][0]['files'][0]['id'] == 'native-file-old'
    result, _, _ = execute(branches.parts, row, payload)
    assert result.status == 'succeeded'
    detail = branches.service.detail(row.conversation_id)
    assert [item['id'] for item in detail['message_attachments'][first.user_message_id]] == ['mapping-old']
    assert row.user_message_id not in detail['message_attachments']
