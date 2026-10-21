"""新临时SQLite、平台文件和MockTransport验证附件预约/副本归属/撤权，不请求实际模型。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import httpx
import jwt
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.core.context import Actor, resolve_actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import FileEntry, LoginSession, Membership, ResourceGrant, ResourceRecord, Role
from agent_platform.modules.chat.native_file_models import NativeChatFile
from agent_platform.modules.chat.native_file_router import router
from agent_platform.modules.chat.native_files import (
    NativeFileService, authorize_native_file_source, file_dict, project_conversation_attachments, resolve_turn_attachments,
)
from agent_platform.modules.chat.native_models import NativeChatIdentity, NativeChatTurn, NativeConversation
from test_delegated_business_tools import site as delegated_site


@pytest.fixture
def context(delegated_site):
    site = delegated_site
    site.app.state.settings = site.settings
    state = SimpleNamespace(calls=[], copies={}, upload_status='pending', fault=None, observed_quarantine=False)
    with site.db.sessions.begin() as session:
        role = session.get(Role, 'human-role')
        role.permissions = role.permissions + ['m02.write', 'm02.delete']
        token = jwt.encode({'id': 'native-human', 'sub': 'native-human', 'exp': int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())},
                           'mock-native-signing-' + 'a' * 40, algorithm='HS256')
        ref = site.app.state.secret_vault.put(json.dumps({'identity_id': 'native-identity',
            'native_user_id': 'native-human', 'token': token}), workspace_id='ws', actor_id='human', session=session)['id']
        session.get(NativeChatIdentity, 'native-identity').credential_ref = ref
        session.add(LoginSession(id='human-login', token_hash=sha256(b'human-session').hexdigest(), user_id='human',
            workspace_id='ws', csrf_token='human-csrf', expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()))

    def handler(request):
        state.calls.append((request.method, request.url.path))
        assert request.url.host == '127.0.0.1'
        if request.url.path == '/api/v1/auths/':
            return httpx.Response(200, json={'id': 'native-human', 'role': 'user'})
        if request.method == 'POST' and request.url.path == '/api/v1/files/':
            assert request.url.params['process'] == 'true' and request.url.params['process_in_background'] == 'true'
            assert b'real attachment text' in request.content
            match = re.search(rb'filename="aic-([a-f0-9-]+)-', request.content)
            assert match
            mapping_id = match.group(1).decode()
            with site.db.sessions() as session:
                row = session.get(NativeChatFile, mapping_id)
                assert row.status == 'uploading'
                original = session.get(FileEntry, row.file_id)
                assert original.sha256 == sha256(b'real attachment text').hexdigest()
                result = {'id': 'native-' + mapping_id, 'user_id': 'native-human',
                    'filename': 'aic-' + row.id + '-' + row.source_name, 'data': {'status': state.upload_status},
                    'meta': {'size': row.source_size, 'file_hash': row.source_sha256, 'data': {'platform_upload_id': row.id}}}
            state.copies[result['id']] = deepcopy(result)
            if state.fault == 'upload_timeout':
                raise httpx.ReadTimeout('unit-only unknown dispatch', request=request)
            if state.fault == 'foreign_owner': result['user_id'] = 'someone-else'
            if state.fault == 'wrong_hash': result['meta']['file_hash'] = 'b' * 64
            return httpx.Response(200, json=result)
        if request.url.path == '/api/v1/files/search':
            assert request.url.params['content'] == 'false' and request.url.params['limit'] == '2'
            pattern = request.url.params['filename'][:-1]
            results = [deepcopy(value) for value in state.copies.values() if value['filename'].startswith(pattern)]
            if state.fault == 'ambiguous': results *= 2
            return httpx.Response(200, json=results) if results else httpx.Response(404)
        if request.url.path.endswith('/process/status'):
            return httpx.Response(200, json={'status': state.upload_status})
        if request.method == 'DELETE':
            native_id = request.url.path.rsplit('/', 1)[-1]
            with site.db.sessions() as session:
                row = session.scalar(select(NativeChatFile).where(NativeChatFile.native_file_id == native_id))
                assert row.status == 'quarantined'
                state.observed_quarantine = True
            if state.fault == 'delete_timeout':
                raise httpx.ReadTimeout('unit-only delete response lost', request=request)
            return httpx.Response(200, json={'message': 'File deleted successfully'})
        raise AssertionError('未登记的HTTP路径: ' + str(request.url))

    site.app.state.native_http_transport = httpx.MockTransport(handler)
    site.app.include_router(router, prefix='/api/v1/chat')
    site.client.cookies.set('agent_session', 'human-session')
    with site.db.sessions() as session:
        actor = resolve_actor(session, LoginSession(user_id='human', workspace_id='ws'))
        request = SimpleNamespace(app=site.app)
        yield SimpleNamespace(site=site, session=session, actor=actor, request=request,
            service=NativeFileService(session, actor, request), state=state)


def upload(context, key='file-intent'):
    return context.service.upload('native-identity', 'manual.txt', b'real attachment text', key)


def ready(context):
    context.state.upload_status = 'completed'
    return upload(context)


def test_upload_reservation_is_committed_then_pending_then_explicit_ready(context):
    first = upload(context)
    assert first['status'] == 'processing' and first['native_status'] == 'pending'
    assert 'native_file_id' not in first and 'token' not in json.dumps(first)
    row = context.session.get(NativeChatFile, first['id'])
    conversation = context.session.get(NativeConversation, 'conversation')
    with pytest.raises(DomainError):
        resolve_turn_attachments(context.session, context.actor, conversation, [row.id])
    context.state.upload_status = 'completed'
    completed = context.service.refresh(row.id, first['revision'])
    assert completed['status'] == 'ready'
    resolved = resolve_turn_attachments(context.session, context.actor, conversation, [row.id])
    assert resolved['native_files'][0]['id'] == row.native_file_id
    assert resolved['sources'][0]['file']['sha256'] == sha256(b'real attachment text').hexdigest()
    assert len([call for call in context.state.calls if call == ('POST', '/api/v1/files/')]) == 1


def test_idempotent_same_intent_never_reuploads_and_changed_content_conflicts(context):
    first = ready(context)
    count = len(context.state.calls)
    assert upload(context)['id'] == first['id'] and len(context.state.calls) == count
    with pytest.raises(DomainError) as error:
        context.service.upload('native-identity', 'manual.txt', b'changed contents', 'file-intent')
    assert error.value.code == 'native_file_intent_conflict' and len(context.state.calls) == count


def test_unknown_upload_is_not_replayed_and_explicit_reconciliation_adopts_only_own_copy(context):
    context.state.fault = 'upload_timeout'
    unknown = upload(context)
    assert unknown['status'] == 'unknown' and unknown['error_code'] == 'native_file_timeout'
    count = len(context.state.calls)
    assert upload(context)['status'] == 'unknown' and len(context.state.calls) == count
    context.state.fault = None
    adopted = context.service.reconcile(unknown['id'], unknown['revision'])
    assert adopted['status'] == 'processing'
    assert len([call for call in context.state.calls if call[0] == 'POST' and call[1] == '/api/v1/files/']) == 1


@pytest.mark.parametrize('fault', ['foreign_owner', 'wrong_hash'])
def test_native_owner_and_raw_hash_mismatch_never_becomes_usable(context, fault):
    context.state.fault = fault
    result = upload(context)
    assert result['status'] == 'unknown' and result['error_code'] == 'native_file_binding_invalid'
    with pytest.raises(DomainError):
        resolve_turn_attachments(context.session, context.actor, context.session.get(NativeConversation, 'conversation'), [result['id']])


def test_source_revision_change_revokes_history_without_http_and_cross_user_is_denied(context):
    result = ready(context)
    conversation = context.session.get(NativeConversation, 'conversation')
    source = resolve_turn_attachments(context.session, context.actor, conversation, [result['id']])['sources'][0]
    before = len(context.state.calls)
    original = context.session.get(FileEntry, result['source_file']['id'])
    original.revision += 1
    context.session.flush()
    with pytest.raises(DomainError) as error:
        authorize_native_file_source(context.session, context.actor, source)
    assert error.value.status == 403 and len(context.state.calls) == before
    with pytest.raises(DomainError):
        authorize_native_file_source(context.session, Actor('other-human', 'ws', frozenset({'m02.read'})), source)


def test_file_grant_revocation_hides_list_and_blocks_original_download_and_history(context):
    result = ready(context)
    original = context.session.get(FileEntry, result['source_file']['id'])
    original.owner_id = 'other-human'
    grant = ResourceGrant(workspace_id='ws', user_id='human', resource_type='file', resource_id=original.id, actions=['read'])
    context.session.add(grant)
    context.session.commit()
    conversation = context.session.get(NativeConversation, 'conversation')
    receipt = resolve_turn_attachments(context.session, context.actor, conversation, [result['id']])['sources'][0]
    context.session.delete(grant)
    context.session.commit()
    assert context.service.list()['items'] == [] and context.service.list()['total'] == 0
    with pytest.raises(DomainError):
        authorize_native_file_source(context.session, context.actor, receipt)
    response = context.site.client.get('/api/v1/chat/native/files/' + result['id'] + '/source')
    assert response.status_code == 404


def test_delete_quarantines_before_http_and_failed_cleanup_does_not_revive_source(context):
    result = ready(context)
    receipt = resolve_turn_attachments(context.session, context.actor,
        context.session.get(NativeConversation, 'conversation'), [result['id']])['sources'][0]
    context.state.fault = 'delete_timeout'
    removed = context.service.remove(result['id'], result['revision'])
    assert context.state.observed_quarantine and removed['status'] == 'quarantined' and removed['cleanup_status'] == 'unknown'
    with pytest.raises(DomainError):
        authorize_native_file_source(context.session, context.actor, receipt)
    assert context.service.files.get(result['source_file']['id'])['sha256'] == result['source_file']['sha256']


def test_successful_delete_keeps_platform_original_and_import_can_create_new_generation(context):
    result = ready(context)
    removed = context.service.remove(result['id'], result['revision'])
    assert removed['status'] == 'deleted' and removed['cleanup_status'] == 'deleted'
    recreated = context.service.import_file('native-identity', result['source_file']['id'], 'second-generation')
    assert recreated['id'] != result['id'] and recreated['status'] == 'ready'
    assert context.session.get(NativeChatFile, recreated['id']).generation == 2


@pytest.mark.parametrize('name', ['../manual.txt', 'C:\\manual.txt', 'manual.exe', 'video.mp4', 'image.png'])
def test_unsupported_or_path_filenames_never_reach_native(context, name):
    with pytest.raises(DomainError):
        context.service.upload('native-identity', name, b'real attachment text', 'bad-format')
    assert context.state.calls == []


def test_source_body_integrity_and_cross_identity_selection_are_checked(context):
    result = ready(context)
    conversation = context.session.get(NativeConversation, 'conversation')
    conversation.identity_id = 'another-identity'
    with pytest.raises(DomainError):
        resolve_turn_attachments(context.session, context.actor, conversation, [result['id']])
    conversation.identity_id = 'native-identity'
    path = context.service.files.path(result['source_file']['id'])
    path.write_bytes(b'tampered')
    context.session.get(FileEntry, result['source_file']['id']).revision += 1
    context.session.commit()
    with pytest.raises(DomainError) as error:
        context.service.import_file('native-identity', result['source_file']['id'], 'tamper-intent')
    # 已有同源预约不会重发；新修订导入必须实际核原件字节。
    if error.value.code != 'native_file_integrity_failed':
        raise error.value


def test_raw_upload_reservation_failure_only_cleans_its_new_unique_path(context, monkeypatch):
    existing = context.service.files.store_bytes('keep.txt', b'preserve', 'text/plain')
    preserved = context.service.files.path(existing['id'])
    context.session.commit()
    def fail(*args, **kwargs):
        raise SQLAlchemyError('injected-reservation-error')
    monkeypatch.setattr(context.service, '_reserve', fail)
    with pytest.raises(SQLAlchemyError):
        upload(context)
    assert preserved.read_bytes() == b'preserve'
    assert len([path for path in context.site.settings.files_dir.rglob('*') if path.is_file()]) == 1
    assert context.state.calls == []


def test_public_upload_source_download_and_request_schema_reject_native_id(context):
    client = context.site.client
    response = client.post('/api/v1/chat/native/identities/native-identity/uploads',
        files={'file': ('manual.txt', b'real attachment text', 'text/plain')},
        headers={'X-CSRF-Token': 'human-csrf', 'Idempotency-Key': 'ui-intent'})
    assert response.status_code == 201 and response.json()['status'] == 'processing'
    mapping_id = response.json()['id']
    downloaded = client.get('/api/v1/chat/native/files/' + mapping_id + '/source')
    assert downloaded.status_code == 200 and downloaded.content == b'real attachment text'
    invalid = client.post('/api/v1/chat/native/identities/native-identity/file-imports',
        json={'file_id': response.json()['source_file']['id'], 'native_file_id': 'untrusted'},
        headers={'X-CSRF-Token': 'human-csrf', 'Idempotency-Key': 'invalid-import'})
    assert invalid.status_code == 422


def test_processing_failure_is_visible_and_does_not_offer_a_usable_attachment(context):
    context.state.upload_status = 'failed'
    result = upload(context)
    assert result['status'] == 'failed' and result['error_code'] == 'native_file_processing_failed'
    with pytest.raises(DomainError):
        resolve_turn_attachments(context.session, context.actor,
            context.session.get(NativeConversation, 'conversation'), [result['id']])


def test_ambiguous_reconciliation_and_duplicate_selection_fail_closed(context):
    result = ready(context)
    context.state.fault = 'ambiguous'
    reconciled = context.service.reconcile(result['id'], result['revision'])
    assert reconciled['status'] == 'failed' and reconciled['error_code'] == 'native_file_copy_ambiguous'
    with pytest.raises(DomainError):
        resolve_turn_attachments(context.session, context.actor,
            context.session.get(NativeConversation, 'conversation'), [result['id'], result['id']])


def test_revision_conflict_and_size_limit_stop_before_native_call(context, monkeypatch):
    result = ready(context)
    count = len(context.state.calls)
    with pytest.raises(DomainError) as error:
        context.service.remove(result['id'], result['revision'] + 1)
    assert error.value.code == 'revision_conflict'
    monkeypatch.setattr('agent_platform.modules.chat.native_files.MAX_BYTES', 5)
    with pytest.raises(DomainError) as error:
        context.service.upload('native-identity', 'too-big.txt', b'real attachment text', 'too-large')
    assert error.value.status == 413 and len(context.state.calls) == count


def test_migration12_matches_frozen_model_and_reverses_only_its_own_table(tmp_path):
    """仅新建单表临时库验证12的结构，不触已有平台head或原生数据库。"""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/20261021_12_原生聊天附件.py'
    spec = importlib.util.spec_from_file_location('attachment_migration12', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == '20261021_11'
    engine = create_engine('sqlite:///' + (tmp_path / 'only-migration12.sqlite3').as_posix())
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            metadata = inspect(connection)
            actual = {column['name']: column for column in metadata.get_columns('native_chat_files')}
            assert set(actual) == set(NativeChatFile.__table__.columns.keys())
            for column in NativeChatFile.__table__.columns:
                assert actual[column.name]['nullable'] == column.nullable
                assert str(actual[column.name]['type']) == str(column.type)
            assert {index['name'] for index in metadata.get_indexes('native_chat_files')} == {
                'ix_native_chat_files_' + field for field in ('workspace_id', 'user_id', 'identity_id', 'integration_id', 'file_id')}
            assert len(metadata.get_unique_constraints('native_chat_files')) == 3
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            assert not inspect(connection).has_table('native_chat_files')
    finally:
        engine.dispose()


def test_history_attachment_projection_uses_exact_message_receipts_and_current_authorization(context):
    result = ready(context)
    conversation = context.session.get(NativeConversation, 'conversation')
    receipt = resolve_turn_attachments(context.session, context.actor, conversation, [result['id']])['sources'][0]
    context.session.get(NativeChatTurn, 'turn').sources = [receipt]
    context.session.flush()
    count = len(context.state.calls)
    projected = project_conversation_attachments(context.session, context.actor, conversation,
        message_ids={'user-message', 'assistant-message'})
    assert list(projected) == ['user-message'] and projected['user-message'][0]['id'] == result['id']
    assert 'native_file_id' not in str(projected) and len(context.state.calls) == count
    assert project_conversation_attachments(context.session, context.actor, conversation, message_ids={'different-message'}) == {}
    context.session.get(FileEntry, result['source_file']['id']).revision += 1
    context.session.flush()
    with pytest.raises(DomainError):
        project_conversation_attachments(context.session, context.actor, conversation, message_ids={'user-message'})
    assert len(context.state.calls) == count


def test_fork_projection_requires_both_inherited_receipt_and_message_in_its_native_tree(context):
    result = ready(context)
    original = context.session.get(NativeConversation, 'conversation')
    receipt = resolve_turn_attachments(context.session, context.actor, original, [result['id']])['sources'][0]
    context.session.get(NativeChatTurn, 'turn').sources = [receipt]
    fork = NativeConversation(id='fork', workspace_id='ws', user_id='human', integration_id='instance',
        identity_id='native-identity', native_chat_id='fork-native-chat', request_hash='b' * 64,
        status='ready', inherited_sources=[])
    context.session.add(fork)
    context.session.flush()
    assert project_conversation_attachments(context.session, context.actor, fork, message_ids={'user-message'}) == {}
    fork.inherited_sources = [receipt]
    context.session.flush()
    assert project_conversation_attachments(context.session, context.actor, fork, message_ids={'other-message'}) == {}
    projected = project_conversation_attachments(context.session, context.actor, fork, message_ids={'user-message'})
    assert projected['user-message'][0]['id'] == result['id']
    fork.identity_id = 'other-identity'
    context.session.flush()
    assert project_conversation_attachments(context.session, context.actor, fork, message_ids={'user-message'}) == {}
