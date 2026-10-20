"""本地身份服务：密码只保存强哈希，会话和CSRF独立轮换。"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from threading import Lock
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .context import Actor, resolve_actor
from .errors import DomainError
from .models import LoginSession, Membership, Role, User, Workspace
from .settings import Settings

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


class LoginBudget:
    """限制同地址与账号连续失败；成功清除，防止在线口令猜测。"""

    def __init__(self, max_keys: int = 4096):
        self.attempts: dict[str, list[float]] = {}
        self.lock = Lock()
        self.max_keys = max_keys

    def _prune(self, now: float):
        """清理过期键并限制总量，避免随机账号耗尽进程内存。"""
        for key in list(self.attempts):
            values = [t for t in self.attempts[key] if now - t < 900]
            if values:
                self.attempts[key] = values[-10:]
            else:
                self.attempts.pop(key, None)
        if len(self.attempts) >= self.max_keys:
            raise DomainError(429, 'login_rate_limited', '登录请求过多，请稍后再试')

    def check(self, key: str):
        with self.lock:
            now = time.monotonic()
            if key not in self.attempts:
                self._prune(now)
            values = [t for t in self.attempts.get(key, []) if now - t < 900]
            if len(values) >= 10:
                raise DomainError(429, 'login_rate_limited', '尝试次数过多，请稍后再试')
            if values:
                self.attempts[key] = values

    def failure(self, key: str):
        with self.lock:
            if key not in self.attempts:
                self._prune(time.monotonic())
            self.attempts[key] = (self.attempts.get(key, []) + [time.monotonic()])[-10:]

    def success(self, key: str):
        with self.lock:
            self.attempts.pop(key, None)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def available_workspaces(session: Session, user: User) -> list[Workspace]:
    query = select(Workspace).where(Workspace.organization_id == user.organization_id,
                                    Workspace.archived.is_(False))
    if not user.is_admin:
        ids = select(Membership.workspace_id).where(Membership.user_id == user.id, Membership.disabled.is_(False))
        query = query.where(Workspace.id.in_(ids))
    return list(session.scalars(query.order_by(Workspace.name)))


def start_session(session: Session, user: User, settings: Settings) -> tuple[LoginSession, str]:
    workspaces = available_workspaces(session, user)
    if not workspaces:
        raise DomainError(403, 'no_workspace', '账号尚未获得工作区授权，请联系管理员')
    token = secrets.token_urlsafe(40)
    row = LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                       workspace_id=workspaces[0].id, csrf_token=secrets.token_urlsafe(32),
                       expires_at=(datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)).isoformat())
    session.add(row)
    session.flush()
    return row, token


def workspace_dict(row: Workspace) -> dict:
    return {k: getattr(row, k) for k in ('id', 'name', 'factory_id', 'industry', 'revision', 'archived')}


def me_dict(session: Session, login: LoginSession, actor: Actor | None = None) -> dict:
    actor = actor or resolve_actor(session, login)
    user = session.get(User, login.user_id)
    workspace = session.get(Workspace, login.workspace_id)
    return {'user': {'id': user.id, 'username': user.username, 'display_name': user.display_name,
                     'is_admin': user.is_admin, 'revision': user.revision},
            'workspace': workspace_dict(workspace),
            'workspaces': [workspace_dict(w) for w in available_workspaces(session, user)],
            'roles': list(actor.roles), 'permissions': sorted(actor.permissions), 'csrf_token': login.csrf_token}
