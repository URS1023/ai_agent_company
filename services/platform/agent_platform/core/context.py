"""逐请求验证身份、工作区和最新授权，不信任浏览器自报角色。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import hmac

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session
from .errors import DomainError
from .models import LoginSession, Membership, Role, User, Workspace


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: str
    workspace_id: str
    permissions: frozenset[str]
    roles: tuple[str, ...] = ()
    organization_id: str = ''
    is_admin: bool = False
    session_id: str = ''

    def can(self, permission: str) -> bool:
        return any(fnmatchcase(permission, rule) for rule in self.permissions)

    def require(self, permission: str):
        if not self.can(permission):
            raise DomainError(403, 'permission_denied', '当前账号没有此操作权限')


def resolve_actor(session: Session, login: LoginSession) -> Actor:
    user = session.get(User, login.user_id)
    workspace = session.get(Workspace, login.workspace_id)
    if user is None or user.disabled or workspace is None or workspace.archived:
        raise DomainError(401, 'session_revoked', '登录或工作区权限已失效，请重新登录')
    if workspace.organization_id != user.organization_id:
        raise DomainError(403, 'workspace_denied', '无法访问该工作区')
    if user.is_admin:
        return Actor(user.id, workspace.id, frozenset({'*'}), ('admin',), user.organization_id, True, login.id)
    member = session.scalar(select(Membership).where(
        Membership.user_id == user.id, Membership.workspace_id == workspace.id,
        Membership.disabled.is_(False)))
    if member is None:
        raise DomainError(403, 'workspace_denied', '当前账号未获该工作区授权')
    roles = list(session.scalars(select(Role).where(
        Role.id.in_(member.role_ids), Role.workspace_id == workspace.id))) if member.role_ids else []
    return Actor(user.id, workspace.id, frozenset(p for role in roles for p in role.permissions),
                 tuple(role.name for role in roles), user.organization_id, False, login.id)


def get_actor(request: Request, session: Session = Depends(get_session)) -> Actor:
    authorization = request.headers.get('authorization', '')
    bearer = authorization.lower().startswith('bearer ')
    token = authorization[7:] if bearer else request.cookies.get('agent_session', '')
    if not token:
        raise DomainError(401, 'authentication_required', '请先登录')
    login = session.scalar(select(LoginSession).where(
        LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest(),
        LoginSession.revoked.is_(False)))
    if login is None or datetime.fromisoformat(login.expires_at) <= datetime.now(timezone.utc):
        raise DomainError(401, 'session_expired', '登录已过期，请重新登录')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not bearer:
        supplied = request.headers.get('x-csrf-token', '')
        if not supplied or not hmac.compare_digest(supplied, login.csrf_token):
            raise DomainError(403, 'csrf_failed', '页面登录状态已更新，请刷新后重试')
    actor = resolve_actor(session, login)
    service_resolver = getattr(request.app.state, 'service_actor_resolver', None)
    if service_resolver is not None:
        actor = service_resolver(session, login, actor, bearer)
    elif bearer:
        raise DomainError(401, 'service_token_required', '请使用专门签发的服务访问令牌')
    request.state.actor = actor
    return actor


def require_permission(permission: str):
    def dependency(actor: Actor = Depends(get_actor)) -> Actor:
        actor.require(permission)
        return actor
    return dependency
