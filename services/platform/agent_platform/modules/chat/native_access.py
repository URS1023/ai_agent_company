"""复用原生组成员增量API，不修改模型ACL、不提升账号角色，也不生成任何模型请求。"""

from datetime import timezone
import time

from sqlalchemy import exists, select, update
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.adapters.native import segment
from agent_platform.core.errors import DomainError
from ..integrations.chat_model_config import chat_model_policy
from .native_models import NativeChatIdentity, NativeConversation, utcnow

ACCESS_SECONDS = 60
IN_PROGRESS = 'native_chat_model_access_in_progress'


def validate_owned_group(group, *, group_id, workspace_id, integration_id):
    """每次写成员前核完整归属，不能把平台用户加入管理员任意选择的已有服务组。"""
    expected = {'purpose': 'chat-model-access', 'workspace_id': workspace_id,
        'integration_id': integration_id, 'version': 1}
    data = group.get('data') if isinstance(group, dict) and isinstance(group.get('data'), dict) else {}
    config = data.get('config') if isinstance(data.get('config'), dict) else {}
    marker = data.get('platform')
    if (not isinstance(group, dict) or group.get('id') != group_id or group.get('parent_group_id') is not None
            or 'parent_group_id' not in group or group.get('permissions') != {} or marker != expected
            or not isinstance(marker, dict) or type(marker.get('version')) is not int
            or config.get('share') is not False):
        raise DomainError(409, 'native_chat_model_group_invalid', '专属聊天组归属或权限已变化，请联系管理员核对')


class NativeModelAccess:
    """一个有界授权预约只添加准确普通账号，未知后由显式核对先读成员而不重复建号。"""
    def __init__(self, service, identity, integration):
        self.service, self.session, self.actor = service, service.session, service.actor
        self.identity, self.integration = identity, integration
        self.identity_id = identity.id
        self.policy = chat_model_policy(integration)
        self.deadline = time.monotonic() + ACCESS_SECONDS
        self.finalizing = False

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DomainError(504, 'native_chat_model_access_timeout', '模型授权核对达到本次总时限')
        return min(15, remaining)

    def current_binding(self):
        self.session.expire_all()
        current = self.service.integration(self.integration['id'])
        if (current['base_url'] != self.identity.integration_url
                or chat_model_policy(current) != self.policy
                or current.get('credential_ref') != self.integration.get('credential_ref')):
            raise DomainError(409, 'native_chat_model_policy_changed', '模型授权策略已变化，请重新核对')

    def claim(self, *, reconcile):
        self.session.refresh(self.identity)
        if (self.identity.user_id != self.actor.user_id or self.identity.workspace_id != self.actor.workspace_id
                or self.identity.integration_id != self.integration['id']
                or self.identity.integration_url != self.integration['base_url']
                or self.identity.status not in {'ready', 'access_pending'} or not self.identity.native_user_id):
            raise DomainError(409, 'native_chat_identity_requires_review', '请先核对原生账号')
        if self.identity.status == 'access_pending' and self.identity.error_code != 'native_chat_model_access_required':
            stamp = self.identity.updated_at
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            if not reconcile or (self.identity.error_code == IN_PROGRESS
                    and (utcnow() - stamp).total_seconds() < ACCESS_SECONDS + 5):
                raise DomainError(409, 'native_chat_model_access_requires_review', '请核对已有模型授权操作',
                    {'identity_id': self.identity.id, 'status': self.identity.status})
        active = self.session.scalar(select(NativeConversation.id).where(
            NativeConversation.identity_id == self.identity.id, NativeConversation.active_turn_id.is_not(None)).limit(1))
        if active is not None:
            raise DomainError(409, 'native_chat_model_access_busy', '请先处理当前对话运行后再核对模型权限')
        changed = self.session.execute(update(NativeChatIdentity).where(NativeChatIdentity.id == self.identity.id,
            NativeChatIdentity.revision == self.identity.revision,
            NativeChatIdentity.status == self.identity.status,
            ~exists(select(NativeConversation.id).where(NativeConversation.identity_id == self.identity.id,
                NativeConversation.active_turn_id.is_not(None)))).values(status='access_pending', error_code=IN_PROGRESS,
            revision=self.identity.revision + 1, updated_at=utcnow()))
        if not changed.rowcount:
            raise DomainError(409, 'native_chat_model_access_conflict', '模型授权已在处理中，请核对原记录')
        self.session.commit()
        self.session.refresh(self.identity)

    def call(self, adapter, method, path, *, payload=None):
        # 不在跨网络期间持有SQLite写事务；每个请求收紧到本阶段剩余绝对预算。
        self.session.commit()
        adapter.connection = type(adapter.connection)(adapter.connection.base_url, adapter.connection.token,
            timeout=self.remaining(), auth_required=True)
        return adapter.json_call(method, path, payload=payload)

    def groups(self, adapter):
        groups = self.call(adapter, 'GET', '/api/v1/groups/')
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get('id'), str) for group in groups):
            raise DomainError(502, 'native_chat_model_groups_invalid', '原生用户组核对结果不正确')
        return {group['id'] for group in groups}

    def prepare(self, *, reconcile=False):
        if self.policy is None:
            return
        self.actor.require('m02.write')
        try:
            self.claim(reconcile=reconcile)
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '模型授权预约未确认，请核对原身份',
                {'identity_id': self.identity_id}) from None
        try:
            ordinary = self.service.user_adapter(self.identity, self.integration, allow_access_pending=True,
                timeout_seconds=self.remaining())
            credential = self.service.integrations.secret(self.integration.get('credential_ref'))
            admin = self.service.adapter(self.integration, credential, timeout_seconds=self.remaining())
            group_path = '/api/v1/groups/id/' + segment(self.policy['group_id'])
            group = self.call(admin, 'GET', group_path)
            validate_owned_group(group, group_id=self.policy['group_id'], workspace_id=self.actor.workspace_id,
                integration_id=self.integration['id'])
            if self.policy['group_id'] not in self.groups(ordinary):
                self.current_binding()
                added = self.call(admin, 'POST', group_path + '/users/add',
                    payload={'user_ids': [self.identity.native_user_id]})
                validate_owned_group(added, group_id=self.policy['group_id'], workspace_id=self.actor.workspace_id,
                    integration_id=self.integration['id'])
                if self.policy['group_id'] not in self.groups(ordinary):
                    raise DomainError(502, 'native_chat_model_membership_unconfirmed', '原生账号入组结果尚未确认')
            visible = self.call(ordinary, 'GET', '/api/models')
            items = visible.get('data') if isinstance(visible, dict) else visible
            if not isinstance(items, list) or not set(self.policy['model_ids']).issubset({
                    item.get('id') for item in items if isinstance(item, dict) and isinstance(item.get('id'), str)}):
                raise DomainError(409, 'native_chat_models_unavailable', '指定模型当前不可用，请联系管理员核对授权')
            self.current_binding()
            self.finalizing = True
            self.finish('ready', None)
        except SQLAlchemyError:
            self.session.rollback()
            raise DomainError(503, 'native_chat_state_unavailable', '模型授权状态未保存，请核对原身份',
                {'identity_id': self.identity_id}) from None
        except DomainError as error:
            if not self.finalizing and self.identity.status == 'access_pending':
                self.finish('access_pending', error.code)
            raise DomainError(error.status, error.code, error.message,
                {'identity_id': self.identity.id, 'status': self.identity.status}) from None

    def finish(self, status, code):
        changed = self.session.execute(update(NativeChatIdentity).where(NativeChatIdentity.id == self.identity.id,
            NativeChatIdentity.status == 'access_pending', NativeChatIdentity.error_code == IN_PROGRESS,
            NativeChatIdentity.revision == self.identity.revision).values(
            status=status, error_code=code, revision=self.identity.revision + 1, updated_at=utcnow()))
        if not changed.rowcount:
            raise DomainError(409, 'native_chat_model_access_conflict', '原生身份已变化，请核对模型授权记录')
        self.session.commit()
        self.session.refresh(self.identity)
