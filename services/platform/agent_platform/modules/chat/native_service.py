"""平台用户到Open WebUI普通账号与会话树的装配，不实现模型或工具循环。"""

from copy import deepcopy
import hashlib
import json
import math
import secrets
import time

import jwt
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent_platform.adapters.native import NativeConnection, validate_base_url
from agent_platform.core.datetimes import utc_storage_iso
from agent_platform.core.errors import DomainError
from agent_platform.core.models import ResourceReference, SecretEntry, User
from agent_platform.modules.integrations.service import IntegrationService, INTEGRATION
from .native_models import NativeChatIdentity, NativeConversation, NativeChatTurn, new_id, utcnow
from .native_sources import authorize_conversation_sources
from .native_transport import NativeChatAdapter


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def intent_key(value):
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 160
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise DomainError(422, 'native_chat_intent_invalid', '操作编号格式不正确')
    return value


def identity_dict(row):
    return {'id': row.id, 'integration_id': row.integration_id, 'status': row.status,
        'error_code': row.error_code, 'revision': row.revision, 'created_at': utc_storage_iso(row.created_at)}


def conversation_dict(row):
    return {'id': row.id, 'integration_id': row.integration_id, 'identity_id': row.identity_id,
        'title': row.title, 'status': row.status, 'revision': row.revision, 'active_turn_id': row.active_turn_id,
        'created_at': utc_storage_iso(row.created_at), 'updated_at': utc_storage_iso(row.updated_at)}


def public_history(history):
    """原消息内容和树关系保留；内部会话元数据与原生附件下载地址不交给浏览器。"""
    result = deepcopy(history)
    if not isinstance(result, dict) or not isinstance(result.get('messages', {}), dict):
        raise DomainError(502, 'native_chat_history_invalid', '原生消息树格式不正确')
    for message in result.get('messages', {}).values():
        if not isinstance(message, dict):
            raise DomainError(502, 'native_chat_history_invalid', '原生消息格式不正确')
        message.pop('meta', None)
        message.pop('metadata', None)
        if isinstance(message.get('files'), list):
            message['files'] = [{'type': 'file', 'name': item.get('name', '附件')}
                for item in message['files'] if isinstance(item, dict)]
    return result


class NativeChatService:
    """短事务预约后才调用原生HTTP；未知操作留在原记录，不能靠刷新再次建号或建会话。"""

    def __init__(self, session, actor, request):
        self.session, self.actor, self.request = session, actor, request
        self.integrations = IntegrationService(session, actor, request)
        self._adapters = {}
        self.vault = getattr(request.app.state, 'secret_vault', None)
        if self.vault is None:
            raise DomainError(503, 'secret_store_unavailable', '凭据存储尚未就绪')

    def integration(self, integration_id):
        integration = self.integrations.integration(integration_id)
        if integration.get('archived'):
            raise DomainError(503, 'integration_disabled', '该服务连接已停用')
        if integration.get('engine') != 'openwebui':
            raise DomainError(422, 'native_chat_engine_invalid', '该连接不支持原生对话')
        # 本阶段只使用明确配置的本机实例；不借系统代理或自动回退到其他服务。
        integration['base_url'] = validate_base_url(integration['base_url'], allow_remote=False)
        return integration

    def adapter(self, integration, token, *, timeout_seconds=20):
        return NativeChatAdapter(NativeConnection(integration['base_url'], token, timeout=timeout_seconds, auth_required=bool(token)),
            transport=getattr(self.request.app.state, 'native_http_transport', None))

    def instances(self):
        self.actor.require('m02.read')
        rows = self.integrations.list_all(INTEGRATION)
        return {'items': [{'id': item['id'], 'name': item.get('name', ''), 'engine': 'openwebui'}
            for item in rows if item.get('engine') == 'openwebui' and item.get('enabled', True)]}

    def identity(self, identity_id):
        self.actor.require('m02.read')
        row = self.session.get(NativeChatIdentity, identity_id)
        if row is None or row.workspace_id != self.actor.workspace_id or row.user_id != self.actor.user_id:
            raise DomainError(404, 'native_chat_identity_not_found', '对话身份不存在')
        integration = self.integration(row.integration_id)
        if integration['base_url'] != row.integration_url:
            raise DomainError(409, 'native_chat_instance_changed', '对话实例地址已变化，请核对身份绑定')
        return row, integration

    def _bundle(self, row):
        try:
            bundle = json.loads(self.vault.get(row.credential_ref, self.actor.workspace_id, session=self.session))
        except (ValueError, TypeError):
            raise DomainError(503, 'native_chat_credential_invalid', '对话身份凭据需要重新核对') from None
        if not isinstance(bundle, dict) or bundle.get('identity_id') != row.id:
            raise DomainError(503, 'native_chat_credential_invalid', '对话身份凭据需要重新核对')
        return bundle

    def _save_bundle(self, row, bundle):
        entry = self.session.get(SecretEntry, row.credential_ref)
        self.vault.rotate(row.credential_ref, json.dumps(bundle), self.actor.workspace_id,
            self.actor.user_id, entry.revision, session=self.session)

    @staticmethod
    def _user(result, expected_user=None):
        if (not isinstance(result, dict) or not isinstance(result.get('id'), str) or not result['id']
                or result.get('role') != 'user' or (expected_user and result['id'] != expected_user)):
            raise DomainError(502, 'native_chat_identity_invalid', '原生服务没有返回匹配的普通用户身份')
        return result

    def _record_identity_result(self, row, bundle, result, *, prepare_access=False, preserve_access_state=False):
        self._user(result, row.native_user_id)
        revision, previous_status, previous_error = row.revision, row.status, row.error_code
        if not isinstance(result.get('token'), str) or not result['token']:
            status, error_code = 'auth_required', 'native_chat_mfa_required'
        else:
            try:
                claims = jwt.decode(result['token'], options={'verify_signature': False})
            except jwt.PyJWTError:
                raise DomainError(502, 'native_chat_credential_invalid', '原生身份返回了无效令牌') from None
            if claims.get('id') != result['id']:
                raise DomainError(502, 'native_chat_credential_invalid', '原生令牌与普通用户不一致')
            bundle.update(token=result['token'], native_user_id=result['id'])
            self._save_bundle(row, bundle)
            from ..integrations.chat_model_config import chat_model_policy
            if preserve_access_state and previous_status == 'access_pending':
                status, error_code = previous_status, previous_error
            elif prepare_access and chat_model_policy(self.integration(row.integration_id)) is not None:
                status, error_code = 'access_pending', 'native_chat_model_access_required'
            else:
                status, error_code = 'ready', None
        # 刷新JWT不能覆盖同时提交的模型授权预约，Vault轮换与身份CAS共用当前事务。
        changed = self.session.execute(update(NativeChatIdentity).where(NativeChatIdentity.id == row.id,
            NativeChatIdentity.revision == revision, NativeChatIdentity.status == previous_status).values(
            native_user_id=result['id'], status=status, error_code=error_code, revision=revision + 1))
        if not changed.rowcount:
            self.session.rollback()
            raise DomainError(409, 'native_chat_identity_conflict', '对话身份已变化，请核对原操作')
        self.session.commit()
        self.session.refresh(row)
        return identity_dict(row)

    def _prepare_model_access(self, row, integration, *, reconcile=False):
        from .native_access import NativeModelAccess
        if row.status in {'ready', 'access_pending'}:
            NativeModelAccess(self, row, integration).prepare(reconcile=reconcile)
        return identity_dict(row)

    def provision_identity(self, integration_id):
        self.actor.require('m02.write')
        integration = self.integration(integration_id)
        existing = self.session.scalar(select(NativeChatIdentity).where(
            NativeChatIdentity.workspace_id == self.actor.workspace_id,
            NativeChatIdentity.user_id == self.actor.user_id, NativeChatIdentity.integration_id == integration_id))
        if existing:
            if existing.status == 'ready':
                return self._prepare_model_access(existing, integration)
            raise DomainError(409, 'native_chat_identity_requires_review', '请核对已有对话身份，系统不会重复建号',
                {'identity_id': existing.id, 'status': existing.status})
        row = NativeChatIdentity(id=new_id(), workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            integration_id=integration_id, integration_revision=integration['revision'], integration_url=integration['base_url'])
        bundle = {'identity_id': row.id, 'email': 'chat-' + row.id.replace('-', '') + '@localhost',
            'password': secrets.token_urlsafe(40) + 'Aa1!', 'token': None}
        try:
            secret = self.vault.put(json.dumps(bundle), workspace_id=self.actor.workspace_id, actor_id=self.actor.user_id,
                name='对话身份 ' + row.id, kind='native_chat_identity', session=self.session)
            row.credential_ref = secret['id']
            self.session.add(row)
            self.session.add(ResourceReference(workspace_id=self.actor.workspace_id,
                source_type='native_chat_identity', source_id=row.id, target_type='secret', target_id=secret['id']))
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise DomainError(409, 'native_chat_identity_requires_review', '该对话身份已在准备中，请刷新核对') from None
        identity_id = row.id
        try:
            bootstrap = self.integrations.secret(integration.get('credential_ref'))
            user = self.session.get(User, self.actor.user_id)
            name = user.display_name if user is not None else '平台用户'
            self.session.commit()
            result = self.adapter(integration, bootstrap).add_user(bundle['email'], bundle['password'], name)
            self._record_identity_result(row, bundle, result, prepare_access=True)
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '身份状态保存未确认，请核对原记录', {'identity_id': identity_id}) from None
        except DomainError as exc:
            row.status, row.error_code = 'unknown', exc.code
            self.session.commit()
            raise DomainError(exc.status, exc.code, exc.message, {'identity_id': row.id}) from None
        return self._prepare_model_access(row, integration)

    def reconcile_identity(self, identity_id):
        """显式用预约前已加密的准确邮箱/密码核对，永远不重新调用add。"""
        self.actor.require('m02.write')
        row, integration = self.identity(identity_id)
        from ..integrations.chat_model_config import chat_model_policy
        if row.status in {'ready', 'access_pending'} and chat_model_policy(integration) is not None:
            return self._prepare_model_access(row, integration, reconcile=True)
        bundle = self._bundle(row)
        self.session.commit()
        result = self.adapter(integration, '').signin(bundle['email'], bundle['password'])
        self._record_identity_result(row, bundle, result, prepare_access=True)
        return self._prepare_model_access(row, integration, reconcile=True)

    def user_adapter(self, row, integration, *, verify_profile=True, allow_refresh=True, timeout_seconds=None,
                     allow_access_pending=False):
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        def remaining():
            budget = deadline - time.monotonic() if deadline is not None else 20
            if budget <= 0:
                raise DomainError(504, 'native_chat_timeout', '身份核验达到本次总时限')
            return min(20, budget)
        allowed_status = {'ready', 'access_pending'} if allow_access_pending else {'ready'}
        if row.status not in allowed_status:
            raise DomainError(409, 'native_chat_identity_requires_review', '对话身份尚未就绪', {'identity_id': row.id})
        bundle = self._bundle(row)
        token = bundle.get('token')
        if not isinstance(token, str) or not token:
            raise DomainError(409, 'native_chat_identity_requires_review', '请核对对话身份凭据')
        try:
            claims = jwt.decode(token, options={'verify_signature': False})
            expires = claims.get('exp')
        except jwt.PyJWTError:
            raise DomainError(503, 'native_chat_credential_invalid', '对话身份令牌格式不正确') from None
        if (claims.get('id') != row.native_user_id or (expires is not None
                and (type(expires) not in {int, float} or not math.isfinite(expires)))):
            raise DomainError(503, 'native_chat_credential_invalid', '对话身份令牌与当前用户不一致')
        if expires is not None and expires <= utcnow().timestamp() + 120:
            if not allow_refresh:
                raise DomainError(409, 'native_chat_token_expiring', '对话身份即将到期，请先刷新身份验证')
            self.session.commit()
            result = self.adapter(integration, '', timeout_seconds=remaining()).signin(bundle['email'], bundle['password'])
            self._record_identity_result(row, bundle, result, preserve_access_state=allow_access_pending)
            if row.status not in allowed_status:
                raise DomainError(409, 'native_chat_mfa_required', '原生身份需要完成验证')
            token = result['token']
        cache_key = (row.id, hashlib.sha256(token.encode()).hexdigest(), verify_profile)
        if cache_key not in self._adapters:
            adapter = self.adapter(integration, token, timeout_seconds=remaining())
            if verify_profile:
                self.session.commit()
                self._user(adapter.profile(), row.native_user_id)
            self._adapters[cache_key] = adapter
        return self._adapters[cache_key]

    def chat(self, chat_id, *, sources=True):
        self.actor.require('m02.read')
        row = self.session.get(NativeConversation, chat_id)
        if (row is None or row.workspace_id != self.actor.workspace_id or row.user_id != self.actor.user_id
                or row.status == 'deleted'):
            raise DomainError(404, 'native_chat_not_found', '对话不存在')
        identity, integration = self.identity(row.identity_id)
        if row.integration_id != identity.integration_id:
            raise DomainError(409, 'native_chat_binding_invalid', '对话与身份关联不一致')
        if sources:
            authorize_conversation_sources(self.session, self.actor, row)
        return row, identity, integration

    def list(self, *, limit=50, offset=0):
        self.actor.require('m02.read')
        rows = self.session.scalars(select(NativeConversation).where(
            NativeConversation.workspace_id == self.actor.workspace_id, NativeConversation.user_id == self.actor.user_id,
            NativeConversation.status != 'deleted').order_by(NativeConversation.updated_at.desc()))
        visible = []
        for row in rows:
            try:
                self.chat(row.id)
                visible.append(row)
            except DomainError as exc:
                if exc.status not in {403, 404}:
                    raise
        return {'items': [conversation_dict(row) for row in visible[offset:offset + min(limit, 100)]], 'total': len(visible)}

    @staticmethod
    def _native_chat(result, identity, *, expected_chat=None):
        if (not isinstance(result, dict) or not isinstance(result.get('id'), str)
                or result.get('user_id') != identity.native_user_id
                or (expected_chat and result['id'] != expected_chat) or not isinstance(result.get('chat'), dict)):
            raise DomainError(502, 'native_chat_binding_invalid', '原生对话与当前用户绑定不一致')
        return result

    def create(self, identity_id, title='新对话', *, idempotency_key=None):
        self.actor.require('m02.write')
        identity, integration = self.identity(identity_id)
        adapter = self.user_adapter(identity, integration)
        key = intent_key(idempotency_key)
        digest = fingerprint({'identity_id': identity_id, 'title': title})
        if key:
            prior = self.session.scalar(select(NativeConversation).where(NativeConversation.workspace_id == self.actor.workspace_id,
                NativeConversation.user_id == self.actor.user_id, NativeConversation.integration_id == integration['id'],
                NativeConversation.creation_key == key))
            if prior:
                if prior.request_hash != digest:
                    raise DomainError(409, 'idempotency_conflict', '该操作编号已用于其他会话')
                return conversation_dict(prior)
        row = NativeConversation(id=new_id(), workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            integration_id=identity.integration_id, identity_id=identity.id, title=title, creation_key=key, request_hash=digest)
        self.session.add(row)
        self.session.commit()
        chat_id = row.id
        try:
            result = self._native_chat(adapter.create_chat(title), identity)
            row.native_chat_id, row.status = result['id'], 'ready'
            row.revision += 1
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '会话创建结果未保存，请核对原记录', {'chat_id': chat_id}) from None
        except DomainError as exc:
            row.status = 'unknown'
            self.session.commit()
            raise DomainError(exc.status, exc.code, exc.message, {'chat_id': row.id}) from None
        return conversation_dict(row)

    def read_native_chat(self, chat_id):
        """后台继续对话读取原消息树；公开投影不能用于回送已裁剪的附件或内部上下文。"""
        row, identity, integration = self.chat(chat_id)
        if row.status != 'ready' or not row.native_chat_id:
            raise DomainError(409, 'native_chat_requires_review', '原对话状态需要核对')
        adapter = self.user_adapter(identity, integration)
        self.session.commit()
        result = self._native_chat(adapter.chat(row.native_chat_id), identity, expected_chat=row.native_chat_id)
        return row, result

    def detail(self, chat_id):
        row, _, _ = self.chat(chat_id)
        if row.status != 'ready' or not row.native_chat_id:
            return {**conversation_dict(row), 'history': None, 'message_attachments': {}}
        row, result = self.read_native_chat(chat_id)
        history = public_history(result['chat'].get('history', {}))
        from .native_files import project_conversation_attachments
        attachments = project_conversation_attachments(self.session, self.actor, row,
            message_ids=list(history.get('messages', {})))
        # messages与history不能形成第二个泄漏内部meta/原生下载URL的出口。
        flat = result['chat'].get('messages', [])
        if not isinstance(flat, list):
            raise DomainError(502, 'native_chat_history_invalid', '原生消息列表格式不正确')
        projected = public_history({'messages': {str(index): item for index, item in enumerate(flat)}})['messages']
        return {**conversation_dict(row), 'history': history, 'messages': list(projected.values()),
            'message_attachments': attachments, 'native_context_usage': result.get('context_usage')}

    def _reserve_edit(self, row, revision):
        if type(revision) is not int or revision < 1:
            raise DomainError(422, 'native_chat_revision_required', '请先读取最新对话', {'revision': '缺少版本号'})
        changed = self.session.execute(update(NativeConversation).where(NativeConversation.id == row.id,
            NativeConversation.workspace_id == self.actor.workspace_id, NativeConversation.revision == revision,
            NativeConversation.active_turn_id.is_(None), NativeConversation.status == 'ready').values(
            status='editing', revision=revision + 1))
        if not changed.rowcount:
            raise DomainError(409, 'native_chat_edit_conflict', '对话已变化或仍有运行，请先刷新核对')
        self.session.refresh(row)
        self.session.commit()

    def edit(self, chat_id, revision, *, title=None, message_id=None, content=None):
        self.actor.require('m02.write')
        row, identity, integration = self.chat(chat_id)
        adapter = self.user_adapter(identity, integration)
        if (title is None) == (message_id is None):
            raise DomainError(422, 'native_chat_edit_invalid', '请明确本次修改内容')
        if message_id:
            detail = self.detail(chat_id)
            messages = detail['history'].get('messages', {})
            if message_id not in messages:
                raise DomainError(404, 'native_chat_message_not_found', '消息不存在')
        self._reserve_edit(row, revision)
        try:
            result = adapter.rename_chat(row.native_chat_id, title) if title is not None else adapter.edit_message(
                row.native_chat_id, message_id, content)
            self._native_chat(result, identity, expected_chat=row.native_chat_id)
            row.status = 'ready'
            if title is not None:
                row.title = title
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '修改结果未保存，请核对原对话', {'chat_id': chat_id}) from None
        except DomainError as exc:
            row.status = 'unknown'
            self.session.commit()
            raise DomainError(exc.status, exc.code, exc.message, {'chat_id': row.id}) from None
        return conversation_dict(row)

    def fork(self, chat_id, revision, message_id, *, idempotency_key=None):
        self.actor.require('m02.write')
        original, identity, integration = self.chat(chat_id)
        adapter = self.user_adapter(identity, integration)
        detail = self.detail(chat_id)
        if message_id not in (detail['history'] or {}).get('messages', {}):
            raise DomainError(404, 'native_chat_message_not_found', '分叉消息不存在')
        key = intent_key(idempotency_key)
        digest = fingerprint({'source_chat': chat_id, 'message_id': message_id})
        if key:
            prior = self.session.scalar(select(NativeConversation).where(NativeConversation.workspace_id == self.actor.workspace_id,
                NativeConversation.user_id == self.actor.user_id, NativeConversation.integration_id == integration['id'],
                NativeConversation.creation_key == key))
            if prior:
                if prior.request_hash != digest:
                    raise DomainError(409, 'idempotency_conflict', '该操作编号已用于其他分叉')
                return conversation_dict(prior)
        sources = deepcopy(original.inherited_sources)
        for turn in self.session.scalars(select(NativeChatTurn).where(NativeChatTurn.conversation_id == original.id)):
            sources.extend(deepcopy(turn.sources))
        # 保守保留原会话全部来源，不能从未授权旧分支复制正文后删除证据。
        self._reserve_edit(original, revision)
        row = NativeConversation(id=new_id(), workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            identity_id=identity.id, integration_id=integration['id'], title=original.title + '（分叉）',
            creation_key=key, request_hash=digest, inherited_sources=sources)
        self.session.add(row)
        self.session.commit()
        fork_id = row.id
        try:
            result = self._native_chat(adapter.fork_chat(original.native_chat_id, message_id), identity)
            row.native_chat_id, row.status, original.status = result['id'], 'ready', 'ready'
            row.revision += 1
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '分叉结果尚未保存，请核对原操作', {'chat_id': fork_id}) from None
        except DomainError as exc:
            row.status, original.status = 'unknown', 'unknown'
            self.session.commit()
            raise DomainError(exc.status, exc.code, exc.message, {'chat_id': fork_id}) from None
        return conversation_dict(row)

    def delete(self, chat_id, revision):
        self.actor.require('m02.delete')
        row, identity, integration = self.chat(chat_id, sources=False)
        adapter = self.user_adapter(identity, integration)
        self._reserve_edit(row, revision)
        try:
            if adapter.remove_chat(row.native_chat_id) is not True:
                raise DomainError(502, 'native_chat_delete_unconfirmed', '原生服务未确认删除')
            row.status = 'deleted'
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '删除结果尚未保存，请核对原操作', {'chat_id': chat_id}) from None
        except DomainError as exc:
            row.status = 'unknown'
            self.session.commit()
            raise DomainError(exc.status, exc.code, exc.message, {'chat_id': chat_id}) from None
        return {'id': chat_id, 'deleted': True}
