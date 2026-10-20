"""服务身份和外部调用方使用独立用户/角色及短期Bearer，不冒充浏览器会话。"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from typing import Literal

from ...core.auth import PASSWORD_HASHER
from ...core.context import Actor, require_permission
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.models import LoginSession, Membership, Role, User
from ...core.records import RecordStore
# 服务Bearer也可用于未启用身份管理页面的部署，必须在建库前注册关联模型。
from . import client_certificate_models as _client_certificate_models
from .models import ServiceAccess, ServiceIdentity
from .schemas import IdentityInput, RevisionInput
from .security import PERMISSION_UNIVERSE, validate_delegation

router = APIRouter()


def resolve_service_actor(session, login: LoginSession, actor: Actor, bearer: bool) -> Actor:
    """Cookie与服务Bearer分离，并按最新角色、身份范围及令牌范围计算交集。"""
    token = session.scalar(select(ServiceAccess).where(ServiceAccess.session_id == login.id))
    if not bearer:
        if token is not None:
            raise DomainError(401, 'service_token_cookie_denied', '服务令牌不能作为浏览器会话使用')
        return actor
    if token is None:
        raise DomainError(401, 'browser_token_bearer_denied', '浏览器会话不能作为服务令牌使用')
    identity = session.get(ServiceIdentity, token.identity_id)
    if token.revoked or datetime.fromisoformat(token.expires_at) <= datetime.now(timezone.utc) or identity is None or not identity.enabled:
        raise DomainError(401, 'service_token_revoked', '服务令牌已过期或撤销')
    if token.workspace_id != login.workspace_id or identity.workspace_id != login.workspace_id or identity.user_id != actor.user_id:
        raise DomainError(403, 'service_workspace_denied', '服务令牌只能用于签发工作区')
    # 本地导入避免路由装配循环；证书到期/停用/撤销每次请求重新生效。
    from .client_certificate_service import validate_certificate_binding
    validate_certificate_binding(session, token, identity)
    identity_actor = replace(actor, permissions=frozenset(identity.permissions))
    scope_actor = replace(actor, permissions=frozenset(token.scopes))
    permissions = frozenset(p for p in PERMISSION_UNIVERSE if actor.can(p) and identity_actor.can(p) and scope_actor.can(p))
    return replace(actor, permissions=permissions, is_admin=False)


class ServiceCreate(IdentityInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    caller_kind: Literal['external', 'automation', 'integration'] = 'external'


class ServicePatch(RevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None


class TokenCreate(IdentityInput):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = Field(default=None, max_length=100)
    ttl_minutes: int = Field(default=30, ge=1, le=60)


def service_dict(row):
    return {k: getattr(row, k) for k in ('id', 'name', 'workspace_id', 'user_id', 'caller_kind', 'description', 'permissions', 'enabled', 'revision', 'created_at')}


def token_dict(row):
    return {k: getattr(row, k) for k in ('id', 'identity_id', 'name', 'workspace_id', 'scopes', 'expires_at', 'revoked', 'created_at')}


def service_get(session, actor, id):
    row = session.scalar(select(ServiceIdentity).where(ServiceIdentity.id == id, ServiceIdentity.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'service_identity_missing', '服务身份不存在')
    return row


def mint_service_token(session, actor, identity, scopes, name, expires):
    """复用同一短令牌持久化路径；调用方先验证授权，事务由请求依赖统一提交。"""
    token = 'service_' + secrets.token_urlsafe(40)
    login = LoginSession(user_id=identity.user_id, workspace_id=identity.workspace_id,
        token_hash=sha256(token.encode()).hexdigest(), csrf_token=secrets.token_urlsafe(32), expires_at=expires)
    session.add(login)
    session.flush()
    row = ServiceAccess(workspace_id=identity.workspace_id, identity_id=identity.id, session_id=login.id,
        name=name[:120], scopes=scopes, expires_at=expires, created_by=actor.user_id)
    session.add(row)
    session.flush()
    RecordStore(session, actor).audit('service_token.create', 'service_identity', identity.id,
        {'token_id': row.id, 'expires_at': expires})
    return token, row


@router.get('/service-identities')
def service_list(actor: Actor = Depends(require_permission('m14.read')), session: Session = Depends(get_session)):
    items = [service_dict(row) for row in session.scalars(select(ServiceIdentity).where(ServiceIdentity.workspace_id == actor.workspace_id))]
    return {'items': items, 'total': len(items)}


@router.post('/service-identities', status_code=201)
def create_service(body: ServiceCreate, actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    permissions = validate_delegation(actor, body.permissions)
    # 独立账号没有可用的人类登录密码；真实权限仍由Membership/Role承担。
    user = User(organization_id=actor.organization_id, username='svc.' + secrets.token_hex(16),
        display_name=body.name, password_hash=PASSWORD_HASHER.hash(secrets.token_urlsafe(48)))
    role = Role(workspace_id=actor.workspace_id, name='服务:' + body.name + ':' + secrets.token_hex(4), permissions=permissions)
    session.add_all([user, role])
    session.flush()
    session.add(Membership(workspace_id=actor.workspace_id, user_id=user.id, role_ids=[role.id]))
    row = ServiceIdentity(workspace_id=actor.workspace_id, user_id=user.id, role_id=role.id,
        name=body.name, description=body.description, caller_kind=body.caller_kind,
        permissions=permissions, created_by=actor.user_id)
    session.add(row)
    session.flush()
    RecordStore(session, actor).audit('service_identity.create', 'service_identity', row.id)
    return service_dict(row)


@router.patch('/service-identities/{id}')
def update_service(id: str, body: ServicePatch, actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    row = service_get(session, actor, id)
    validate_delegation(actor, row.permissions)
    changes = body.model_dump(exclude_unset=True, exclude={'revision'})
    if 'permissions' in changes:
        changes['permissions'] = validate_delegation(actor, changes['permissions'])
    changed = session.execute(update(ServiceIdentity).where(ServiceIdentity.id == id,
        ServiceIdentity.workspace_id == actor.workspace_id, ServiceIdentity.revision == body.revision).values(
            **changes, revision=body.revision + 1), execution_options={'synchronize_session': False})
    if changed.rowcount != 1:
        raise DomainError(409, 'revision_conflict', '服务身份已更新，请刷新')
    if 'permissions' in changes:
        role = session.get(Role, row.role_id)
        updated = session.execute(update(Role).where(Role.id == role.id, Role.revision == role.revision).values(
            permissions=changes['permissions'], revision=role.revision + 1), execution_options={'synchronize_session': False})
        if updated.rowcount != 1:
            raise DomainError(409, 'revision_conflict', '关联角色已更新，请刷新')
        session.expire(role)
    if changes.get('enabled') is False:
        sessions = select(ServiceAccess.session_id).where(ServiceAccess.identity_id == id)
        session.execute(update(LoginSession).where(LoginSession.id.in_(sessions)).values(revoked=True))
        session.execute(update(ServiceAccess).where(ServiceAccess.identity_id == id).values(revoked=True))
    session.refresh(row)
    RecordStore(session, actor).audit('service_identity.update', 'service_identity', id, {'fields': sorted(changes)})
    return service_dict(row)


@router.get('/service-identities/{id}/tokens')
def token_list(id: str, actor: Actor = Depends(require_permission('m14.read')), session: Session = Depends(get_session)):
    service_get(session, actor, id)
    items = [token_dict(row) for row in session.scalars(select(ServiceAccess).where(ServiceAccess.identity_id == id, ServiceAccess.workspace_id == actor.workspace_id))]
    return {'items': items, 'total': len(items)}


@router.post('/service-identities/{id}/tokens', status_code=201)
def issue_token(id: str, body: TokenCreate, actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    identity = service_get(session, actor, id)
    if not identity.enabled:
        raise DomainError(409, 'service_identity_disabled', '该服务身份已停用')
    validate_delegation(actor, identity.permissions)
    scopes = validate_delegation(actor, body.scopes if body.scopes is not None else identity.permissions)
    validate_delegation(replace(actor, permissions=frozenset(identity.permissions)), scopes)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=body.ttl_minutes)).isoformat()
    token, row = mint_service_token(session, actor, identity, scopes, body.name, expires)
    return {**token_dict(row), 'token': token, 'token_type': 'Bearer'}


@router.delete('/service-identities/{id}/tokens/{token_id}')
def revoke_token(id: str, token_id: str, actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    identity = service_get(session, actor, id)
    validate_delegation(actor, identity.permissions)
    row = session.scalar(select(ServiceAccess).where(ServiceAccess.id == token_id,
        ServiceAccess.identity_id == id, ServiceAccess.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'service_token_missing', '服务令牌不存在')
    row.revoked = True
    session.execute(update(LoginSession).where(LoginSession.id == row.session_id).values(revoked=True))
    RecordStore(session, actor).audit('service_token.revoke', 'service_identity', id, {'token_id': token_id})
    return {'id': token_id, 'revoked': True}
