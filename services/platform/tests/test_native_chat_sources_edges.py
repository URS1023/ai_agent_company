"""新临时SQLite验证原生来源边界，只调用纯守卫；不连接原生服务或业务数据源。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import FileEntry, ResourceRecord, Task
from agent_platform.modules.chat.native_models import NativeChatTurn, NativeConversation
from agent_platform.modules.chat.native_sources import (
    append_turn_sources, authorize_conversation_sources, authorize_native_chat_task,
    authorize_sources, resolve_delegation_context,
)
from agent_platform.modules.integrations.delegated_sources import capture_source
from agent_platform.modules.manufacturing.models import Product
from test_delegated_business_tools import site
from test_native_chat_files import context as attachment_context, delegated_site, ready


def delegation(session):
    return resolve_delegation_context(session, engine_identity_id='engine', native_user_id='native-human',
        native_chat_id='native-chat', native_message_id='assistant-message')


@pytest.mark.parametrize('fault', ['expired', 'active_pointer', 'archived', 'cross_engine'])
def test_current_turn_and_instance_boundary_are_required(site, fault):
    with site.db.sessions.begin() as session:
        if fault == 'expired':
            session.get(NativeChatTurn, 'turn').deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if fault == 'active_pointer':
            session.get(NativeConversation, 'conversation').active_turn_id = 'different-turn'
        if fault == 'archived':
            session.get(ResourceRecord, 'instance').archived = True
        if fault == 'cross_engine':
            row = session.get(ResourceRecord, 'instance')
            row.data = {**row.data, 'delegation_service_identity_id': 'foreign-engine'}
        session.flush()
        with pytest.raises(DomainError) as error:
            delegation(session)
        assert error.value.status == 403


def test_append_preserves_and_deduplicates_all_earlier_sources(site):
    with site.db.sessions.begin() as session:
        actor = Actor('human', 'ws', frozenset({'m02.read', 'm10.read', 'm11.read'}))
        session.add(Product(id='product', workspace_id='ws', name='隔离产品'))
        session.flush()
        first = capture_source(session, actor, 'device', 'device-one', {})
        second = capture_source(session, actor, 'product', 'product', {})
        context = delegation(session)
        append_turn_sources(session, context, [first])
        append_turn_sources(session, context, [second, deepcopy(first)])
        row = session.get(NativeChatTurn, 'turn')
        assert len(row.sources) == 2 and first in row.sources and second in row.sources
        assert row.revision == 3


def test_concurrent_receipt_change_never_loses_a_committed_source(site):
    with site.db.sessions() as original:
        actor = Actor('human', 'ws', frozenset({'m02.read', 'm10.read'}))
        first = capture_source(original, actor, 'device', 'device-one', {})
        context = delegation(original)
        # 保留第一次读取的版本，另一个独立事务先提交来源。
        original.get(NativeChatTurn, 'turn')
        with site.db.sessions.begin() as writer:
            writer.execute(update(NativeChatTurn).where(NativeChatTurn.id == 'turn').values(sources=[first], revision=2))
        try:
            append_turn_sources(original, context, [])
            original.commit()
        except DomainError as error:
            assert error.code == 'native_source_conflict'
            original.rollback()
        with site.db.sessions() as check:
            assert first in check.get(NativeChatTurn, 'turn').sources


def test_stop_during_tool_read_prevents_late_receipt_write(site):
    with site.db.sessions.begin() as session:
        actor = Actor('human', 'ws', frozenset({'m02.read', 'm10.read'}))
        context = delegation(session)
        receipt = capture_source(session, actor, 'device', 'device-one', {})
        session.get(NativeChatTurn, 'turn').status = 'cancelled'
        session.flush()
        with pytest.raises(DomainError) as error:
            append_turn_sources(session, context, [receipt])
        assert error.value.status == 403 and session.get(NativeChatTurn, 'turn').sources == []


def test_administrator_cannot_read_another_person_chat_task(site):
    with site.db.sessions.begin() as session:
        task = Task(id='native-task', workspace_id='ws', user_id='human', kind='platform_native_chat',
            title='本人对话', status='running', inputs={'turn_id': 'turn', 'chat_id': 'conversation'})
        session.add(task)
        session.get(NativeChatTurn, 'turn').task_id = task.id
        session.flush()
        authorize_native_chat_task(session, Actor('human', 'ws', frozenset({'m02.read'})), task)
        with pytest.raises(DomainError) as error:
            authorize_native_chat_task(session, Actor('other-human', 'ws', frozenset({'*'}), is_admin=True), task)
        assert error.value.code == 'native_chat_task_access_denied'
        session.get(NativeChatTurn, 'turn').task_id = 'different-task'
        with pytest.raises(DomainError):
            authorize_native_chat_task(session, Actor('human', 'ws', frozenset({'m02.read'})), task)


def test_attachment_receipt_revocation_reaches_history_and_task_guard_without_http(attachment_context):
    context = attachment_context
    result = ready(context)
    from agent_platform.modules.chat.native_files import resolve_turn_attachments
    conversation = context.session.get(NativeConversation, 'conversation')
    receipts = resolve_turn_attachments(context.session, context.actor, conversation, [result['id']])['sources']
    turn = context.session.get(NativeChatTurn, 'turn')
    turn.sources = receipts
    task = Task(id='file-native-task', workspace_id='ws', user_id='human', kind='platform_native_chat',
        title='附件对话', status='running', inputs={'turn_id': turn.id, 'chat_id': conversation.id})
    context.session.add(task)
    turn.task_id = task.id
    context.session.flush()
    count = len(context.state.calls)
    authorize_conversation_sources(context.session, context.actor, conversation)
    authorize_native_chat_task(context.session, context.actor, task)
    context.session.get(FileEntry, result['source_file']['id']).deleted = True
    context.session.flush()
    for check in (lambda: authorize_conversation_sources(context.session, context.actor, conversation),
                  lambda: authorize_native_chat_task(context.session, context.actor, task)):
        with pytest.raises(DomainError) as error:
            check()
        assert error.value.status in {403, 404}
    assert len(context.state.calls) == count


@pytest.mark.parametrize('source', [{'source_kind': 'native_file', 'version': 1}, {'source_kind': 'unknown'}])
def test_malformed_receipts_do_not_fall_back_to_unchecked_history(site, source):
    with site.db.sessions() as session:
        with pytest.raises(DomainError):
            authorize_sources(session, Actor('human', 'ws', frozenset({'m02.read'})), [source])
