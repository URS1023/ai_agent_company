"""原生附件预约、显式核对和原件授权；不自行解析正文或调用模型。"""

from datetime import timedelta, timezone
from hashlib import sha256
from pathlib import Path
import re
from time import monotonic

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent_platform.core.datetimes import utc_storage_iso
from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.models import ResourceReference
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import get_settings
from .native_file_models import NativeChatFile, now
from .native_file_transport import NativeFileTransport
from .native_models import NativeChatIdentity, NativeChatTurn, new_id
from .native_service import NativeChatService, fingerprint, intent_key

MAX_BYTES = 20 * 1024 * 1024
FORMATS = {'.txt': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv',
           '.json': 'application/json', '.log': 'text/plain', '.pdf': 'application/pdf'}
SOURCE_KEYS = {'source_kind', 'version', 'workspace_id', 'user_id', 'mapping_id', 'identity_id',
               'integration_id', 'file', 'native_file_id'}


def file_dict(row):
    return {'id': row.id, 'identity_id': row.identity_id, 'status': row.status, 'native_status': row.native_status,
        'error_code': row.error_code, 'cleanup_status': row.cleanup_status, 'revision': row.revision,
        'source_file': {'id': row.file_id, 'name': row.source_name, 'revision': row.source_revision,
                        'sha256': row.source_sha256, 'size': row.source_size, 'content_type': row.source_content_type},
        'deadline_at': utc_storage_iso(row.deadline_at), 'created_at': utc_storage_iso(row.created_at),
        'updated_at': utc_storage_iso(row.updated_at)}


def _source(session, actor, row):
    original = FileService(session, actor, None).get(row.file_id)
    if original['revision'] != row.source_revision or original['sha256'] != row.source_sha256 or original['size'] != row.source_size:
        raise DomainError(403, 'native_file_source_changed', '附件原件已变化，请重新选择文件')
    return original


def _mapping(session, actor, mapping_id):
    if not isinstance(mapping_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', mapping_id):
        raise DomainError(404, 'native_file_not_found', '附件不存在')
    row = session.get(NativeChatFile, mapping_id)
    if row is None or row.workspace_id != actor.workspace_id or row.user_id != actor.user_id:
        raise DomainError(404, 'native_file_not_found', '附件不存在')
    return row


def _identity_binding(session, actor, row):
    identity = session.get(NativeChatIdentity, row.identity_id)
    integration = RecordStore(session, actor).get('integration_instance', row.integration_id)
    if (identity is None or identity.status != 'ready' or identity.user_id != actor.user_id
            or identity.workspace_id != actor.workspace_id or identity.integration_id != row.integration_id
            or integration.get('archived') or not integration.get('enabled', True)
            or integration.get('engine') != 'openwebui' or integration.get('base_url', '').rstrip('/') != identity.integration_url):
        raise DomainError(403, 'native_file_binding_denied', '附件对话身份或实例已不可用')
    return identity


def _receipt(row):
    return {'source_kind': 'native_file', 'version': 1, 'workspace_id': row.workspace_id, 'user_id': row.user_id,
        'mapping_id': row.id, 'identity_id': row.identity_id, 'integration_id': row.integration_id,
        'file': {'id': row.file_id, 'revision': row.source_revision, 'sha256': row.source_sha256},
        'native_file_id': row.native_file_id}


def authorize_native_file_source(session, actor, source):
    """历史、分叉及继续聊天只核当前授权和固定副本，不访问上游或重新向量化。"""
    actor.require('m02.read')
    if (not isinstance(source, dict) or set(source) != SOURCE_KEYS or source.get('source_kind') != 'native_file'
            or type(source.get('version')) is not int or source['version'] != 1
            or source.get('workspace_id') != actor.workspace_id or source.get('user_id') != actor.user_id
            or not isinstance(source.get('file'), dict) or set(source['file']) != {'id', 'revision', 'sha256'}
            or type(source['file'].get('revision')) is not int):
        raise DomainError(403, 'native_file_source_denied', '当前账号无法访问该附件来源')
    row = _mapping(session, actor, source.get('mapping_id'))
    if row.status != 'ready' or not row.native_file_id or _receipt(row) != source:
        raise DomainError(403, 'native_file_source_denied', '该附件已隔离、删除或来源不一致')
    _identity_binding(session, actor, row)
    _source(session, actor, row)


def resolve_turn_attachments(session, actor, conversation, attachment_ids):
    """只把本人当前会话身份下已就绪的平台附件ID映射为原生模型输入。"""
    actor.require('m02.read')
    if (not isinstance(attachment_ids, list) or len(attachment_ids) > 10
            or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) for value in attachment_ids)
            or len(set(attachment_ids)) != len(attachment_ids)):
        raise DomainError(422, 'native_file_selection_invalid', '请选择不重复的附件，最多10个')
    if conversation.user_id != actor.user_id or conversation.workspace_id != actor.workspace_id:
        raise DomainError(404, 'native_chat_not_found', '对话不存在')
    native_files, sources = [], []
    for mapping_id in attachment_ids:
        row = _mapping(session, actor, mapping_id)
        if row.identity_id != conversation.identity_id or row.integration_id != conversation.integration_id:
            raise DomainError(403, 'native_file_binding_denied', '附件不属于当前对话身份')
        source = _receipt(row)
        authorize_native_file_source(session, actor, source)
        native_files.append({'type': 'file', 'id': row.native_file_id, 'name': row.source_name,
                             'collection_name': 'file-' + row.native_file_id, 'status': 'uploaded'})
        sources.append(source)
    return {'native_files': native_files, 'sources': sources}


def project_conversation_attachments(session, actor, conversation, *, message_ids):
    """把已验证原消息树中的用户消息映射为平台附件，分叉仅承认精确继承收据。

    message_ids由服务端读取、核验原生chat后传入，不能使用浏览器自报的文件URL。
    不遍历其他账号，也不按名称猜来源；映射只返回平台ID与已授权的原件元数据。
    """
    actor.require('m02.read')
    if conversation.user_id != actor.user_id or conversation.workspace_id != actor.workspace_id:
        raise DomainError(404, 'native_chat_not_found', '对话不存在')
    if (not isinstance(message_ids, (list, tuple, set, frozenset)) or len(message_ids) > 1000
            or any(not isinstance(value, str) or not 1 <= len(value) <= 100 for value in message_ids)):
        raise DomainError(422, 'native_attachment_history_budget', '消息树过大或格式不正确，无法映射附件')
    selected = set(message_ids)
    if not selected:
        return {}
    inherited = {fingerprint(source) for source in (conversation.inherited_sources or [])
                 if isinstance(source, dict) and source.get('source_kind') == 'native_file'}
    turns = session.scalars(select(NativeChatTurn).where(
        NativeChatTurn.workspace_id == actor.workspace_id, NativeChatTurn.user_id == actor.user_id,
        NativeChatTurn.identity_id == conversation.identity_id,
        NativeChatTurn.integration_id == conversation.integration_id,
        NativeChatTurn.user_message_id.in_(selected)).order_by(NativeChatTurn.created_at, NativeChatTurn.id))
    projected, seen = {}, set()
    for turn in turns:
        for source in turn.sources or []:
            if not isinstance(source, dict) or source.get('source_kind') != 'native_file':
                continue
            if turn.conversation_id != conversation.id and fingerprint(source) not in inherited:
                continue
            authorize_native_file_source(session, actor, source)
            if source['identity_id'] != conversation.identity_id or source['integration_id'] != conversation.integration_id:
                raise DomainError(403, 'native_file_binding_denied', '历史附件与对话身份不一致')
            marker = (turn.user_message_id, source['mapping_id'])
            if marker in seen:
                continue
            seen.add(marker)
            projected.setdefault(turn.user_message_id, []).append(file_dict(_mapping(session, actor, source['mapping_id'])))
    return projected


class NativeFileService:
    """短事务先预约；未知上传不重传，删除先隔离来源再执行本人副本清理。"""
    def __init__(self, session, actor, request):
        self.session, self.actor, self.request = session, actor, request
        self.settings = get_settings(request)
        self.files = FileService(session, actor, self.settings)
        self.chat = NativeChatService(session, actor, request)

    def get(self, mapping_id, *, source=True):
        self.actor.require('m02.read')
        row = _mapping(self.session, self.actor, mapping_id)
        self.chat.identity(row.identity_id)
        if source:
            _source(self.session, self.actor, row)
        return row

    def list(self, *, identity_id=None, limit=50, offset=0):
        self.actor.require('m02.read')
        clauses = [NativeChatFile.workspace_id == self.actor.workspace_id, NativeChatFile.user_id == self.actor.user_id]
        if identity_id:
            self.chat.identity(identity_id)
            clauses.append(NativeChatFile.identity_id == identity_id)
        visible = []
        for row in self.session.scalars(select(NativeChatFile).where(*clauses).order_by(NativeChatFile.created_at.desc())):
            try:
                self.get(row.id)
            except DomainError as error:
                if error.status in {403, 404}:
                    continue
                raise
            visible.append(row)
        return {'items': [file_dict(row) for row in visible[offset:offset + limit]], 'total': len(visible)}

    @staticmethod
    def _format(name, content=None):
        if (not isinstance(name, str) or not 1 <= len(name) <= 180 or any(c in name for c in '/\\:')
                or any(ord(c) < 32 for c in name)
                or Path(name).suffix.lower() not in FORMATS):
            raise DomainError(422, 'native_file_format_unsupported', '请选择TXT、MD、CSV、JSON、LOG或PDF文件', {'file': '文件格式或名称不支持'})
        if content is not None and (not content or len(content) > MAX_BYTES):
            raise DomainError(413, 'native_file_size_invalid', '附件为空或超过20MB')
        return FORMATS[Path(name).suffix.lower()]

    def _prior(self, identity_id, key, digest):
        row = self.session.scalar(select(NativeChatFile).where(NativeChatFile.identity_id == identity_id,
            NativeChatFile.request_key == key, NativeChatFile.workspace_id == self.actor.workspace_id,
            NativeChatFile.user_id == self.actor.user_id))
        if row and row.request_hash != digest:
            raise DomainError(409, 'native_file_intent_conflict', '该上传编号已用于其他文件')
        if row:
            _source(self.session, self.actor, row)
        return row

    def upload(self, identity_id, name, content, key):
        self.actor.require('m02.write')
        identity, _ = self.chat.identity(identity_id)
        content_type = self._format(name, content)
        key = intent_key(key)
        if key is None:
            raise DomainError(422, 'native_file_intent_required', '缺少本次上传编号')
        digest = fingerprint({'identity_id': identity_id, 'name': name, 'sha256': sha256(content).hexdigest(), 'size': len(content)})
        prior = self._prior(identity_id, key, digest)
        if prior:
            return file_dict(prior)
        mapping_id, created_path = new_id(), None
        try:
            original = self.files.store_bytes(name, content, content_type,
                source={'type': 'native_chat_file', 'id': mapping_id}, visibility='private')
            created_path = self.files.path(original['id'])
            row = self._reserve(identity, original, key, digest, mapping_id=mapping_id)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            if created_path is not None:
                created_path.unlink(missing_ok=True)
            raise DomainError(409, 'native_file_reservation_conflict', '已有相同附件预约，请刷新核对') from None
        except Exception:
            # 仅清理本次store_bytes排他新建路径；不删除复用原件，不在失败事务后发查询。
            self.session.rollback()
            if created_path is not None:
                created_path.unlink(missing_ok=True)
            raise
        return self._send(row, content)

    def import_file(self, identity_id, file_id, key):
        self.actor.require('m02.write')
        identity, _ = self.chat.identity(identity_id)
        original = self.files.get(file_id)
        self._format(original['name'])
        if not 0 < original['size'] <= MAX_BYTES:
            raise DomainError(413, 'native_file_size_invalid', '附件为空或超过20MB')
        key = intent_key(key)
        if key is None:
            raise DomainError(422, 'native_file_intent_required', '缺少本次上传编号')
        digest = fingerprint({'identity_id': identity_id, 'file_id': file_id,
                              'revision': original['revision'], 'sha256': original['sha256']})
        prior = self._prior(identity_id, key, digest)
        if prior:
            return file_dict(prior)
        existing = self.session.scalar(select(NativeChatFile).where(NativeChatFile.identity_id == identity_id,
            NativeChatFile.file_id == file_id, NativeChatFile.source_revision == original['revision'],
            NativeChatFile.source_sha256 == original['sha256'], NativeChatFile.status != 'deleted'))
        if existing:
            return file_dict(existing)
        with self.files.path(file_id).open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) != original['size'] or sha256(content).hexdigest() != original['sha256']:
            raise DomainError(403, 'native_file_integrity_failed', '原件内容与登记摘要不一致')
        try:
            row = self._reserve(identity, original, key, digest)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise DomainError(409, 'native_file_reservation_conflict', '已有相同附件预约，请刷新核对') from None
        except SQLAlchemyError:
            self.session.rollback()
            raise
        return self._send(row, content)

    def _reserve(self, identity, original, key, digest, *, mapping_id=None):
        previous = self.session.scalar(select(func.max(NativeChatFile.generation)).where(
            NativeChatFile.identity_id == identity.id, NativeChatFile.file_id == original['id'],
            NativeChatFile.source_revision == original['revision'], NativeChatFile.source_sha256 == original['sha256'])) or 0
        row = NativeChatFile(id=mapping_id or new_id(), workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            identity_id=identity.id, integration_id=identity.integration_id, file_id=original['id'], source_revision=original['revision'],
            source_sha256=original['sha256'], source_size=original['size'], source_name=original['name'],
            source_content_type=self._format(original['name']), generation=previous + 1,
            request_key=key, request_hash=digest, status='uploading', deadline_at=now() + timedelta(seconds=60))
        self.session.add(row)
        self.session.flush()
        if mapping_id is None:
            self.session.add(ResourceReference(workspace_id=self.actor.workspace_id, source_type='native_chat_file',
                source_id=row.id, target_type='file', target_id=row.file_id))
        return row

    def _change(self, row, **values):
        changed = self.session.execute(update(NativeChatFile).where(NativeChatFile.id == row.id,
            NativeChatFile.workspace_id == self.actor.workspace_id, NativeChatFile.user_id == self.actor.user_id,
            NativeChatFile.revision == row.revision).values(**values, revision=row.revision + 1, updated_at=now()))
        if not changed.rowcount:
            raise DomainError(409, 'revision_conflict', '附件已更新，请刷新')
        self.session.commit()
        self.session.refresh(row)

    def _transport(self, row, deadline):
        identity, integration = self.chat.identity(row.identity_id)
        adapter = self.chat.user_adapter(identity, integration, timeout_seconds=max(0, deadline - monotonic()))
        return identity, NativeFileTransport(adapter, deadline=deadline)

    def _adopt(self, row, identity, result):
        meta = result.get('meta') if isinstance(result, dict) else None
        if (not isinstance(meta, dict) or result.get('user_id') != identity.native_user_id
                or not isinstance(result.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', result['id'])
                or result.get('filename') != 'aic-' + row.id + '-' + row.source_name
                or meta.get('file_hash') != row.source_sha256 or type(meta.get('size')) is not int or meta['size'] != row.source_size
                or not isinstance(meta.get('data'), dict) or meta['data'].get('platform_upload_id') != row.id):
            raise DomainError(502, 'native_file_binding_invalid', '原生附件与本次原件绑定不一致')
        row.native_file_id = result['id']
        state = result.get('data') or {}
        if not isinstance(state, dict):
            raise DomainError(502, 'native_file_response_invalid', '附件服务返回格式不正确')
        return state.get('status', 'pending')

    @staticmethod
    def _state(status):
        if status not in {'pending', 'processing', 'completed', 'failed'}:
            raise DomainError(502, 'native_file_status_invalid', '附件服务返回了未知处理状态')
        return {'completed': 'ready', 'failed': 'failed'}.get(status, 'processing')

    def _failure(self, row, error, *, quarantine=False):
        unknown = error.status in {503, 504} or error.code in {'native_file_binding_invalid', 'native_file_response_invalid', 'native_file_response_too_large'}
        self._change(row, status='quarantined' if quarantine else 'unknown' if unknown else 'failed',
                     error_code=error.code)

    def _send(self, row, content):
        absolute = row.deadline_at.replace(tzinfo=timezone.utc) if row.deadline_at.tzinfo is None else row.deadline_at
        deadline = monotonic() + min(60, max(0, (absolute - now()).total_seconds()))
        try:
            identity, transport = self._transport(row, deadline)
            _source(self.session, self.actor, row)
            status = self._adopt(row, identity, transport.upload(row, content))
            _source(self.session, self.actor, row)
            self._change(row, native_file_id=row.native_file_id, native_status=status, status=self._state(status),
                         error_code='native_file_processing_failed' if status == 'failed' else None)
        except SQLAlchemyError:
            self.session.rollback()
            raise
        except DomainError as error:
            self._failure(row, error, quarantine=error.status in {403, 404})
        return file_dict(row)

    def refresh(self, mapping_id, revision):
        self.actor.require('m02.write')
        row = self.get(mapping_id)
        if row.revision != revision:
            raise DomainError(409, 'revision_conflict', '附件已更新，请刷新')
        if row.status in {'deleted', 'quarantined'} or not row.native_file_id:
            raise DomainError(409, 'native_file_requires_reconciliation', '请先核对原附件记录')
        try:
            _, transport = self._transport(row, monotonic() + 60)
            result = transport.status(row.native_file_id)
            status = result.get('status') if isinstance(result, dict) else None
            _source(self.session, self.actor, row)
            self._change(row, native_status=status, status=self._state(status),
                         error_code='native_file_processing_failed' if status == 'failed' else None)
        except SQLAlchemyError:
            self.session.rollback()
            raise
        except DomainError as error:
            self._failure(row, error, quarantine=error.status in {403, 404} or error.code == 'native_resource_missing')
        return file_dict(row)

    def reconcile(self, mapping_id, revision):
        self.actor.require('m02.write')
        row = self.get(mapping_id, source=False)
        if row.revision != revision:
            raise DomainError(409, 'revision_conflict', '附件已更新，请刷新')
        if row.status == 'deleted':
            return file_dict(row)
        quarantined = row.status == 'quarantined'
        if quarantined:
            self.actor.require('m02.delete')
        else:
            _source(self.session, self.actor, row)
        try:
            identity, transport = self._transport(row, monotonic() + 60)
            results = transport.search(row)
            if results is None:
                self._change(row, error_code='native_file_copy_not_found')
                return file_dict(row)
            if not isinstance(results, list) or len(results) != 1:
                raise DomainError(409, 'native_file_copy_ambiguous', '发现多条副本，请由管理员核对')
            status = self._adopt(row, identity, results[0])
            if not quarantined:
                _source(self.session, self.actor, row)
            self._change(row, native_file_id=row.native_file_id, native_status=status,
                status='quarantined' if quarantined else self._state(status), error_code=None)
        except SQLAlchemyError:
            self.session.rollback()
            raise
        except DomainError as error:
            self._failure(row, error, quarantine=quarantined or error.status in {403, 404})
        return file_dict(row)

    def remove(self, mapping_id, revision):
        self.actor.require('m02.delete')
        row = self.get(mapping_id, source=False)
        if row.revision != revision:
            raise DomainError(409, 'revision_conflict', '附件已更新，请刷新')
        if row.status == 'deleted':
            return file_dict(row)
        self._change(row, status='quarantined', cleanup_status='pending', error_code=None)
        if not row.native_file_id:
            self._change(row, cleanup_status='unknown', error_code='native_file_requires_reconciliation')
            return file_dict(row)
        try:
            _, transport = self._transport(row, monotonic() + 60)
            result = transport.delete(row.native_file_id)
            if result is not None and (not isinstance(result, dict) or not isinstance(result.get('message'), str)):
                raise DomainError(502, 'native_file_delete_unconfirmed', '附件清理结果尚未确认')
            self.session.execute(delete(ResourceReference).where(ResourceReference.workspace_id == self.actor.workspace_id,
                ResourceReference.source_type == 'native_chat_file', ResourceReference.source_id == row.id,
                ResourceReference.target_type == 'file', ResourceReference.target_id == row.file_id))
            self._change(row, status='deleted', cleanup_status='not_found' if result is None else 'deleted', error_code=None)
        except SQLAlchemyError:
            self.session.rollback()
            raise
        except DomainError as error:
            self._change(row, status='quarantined', cleanup_status='unknown', error_code=error.code)
        return file_dict(row)
