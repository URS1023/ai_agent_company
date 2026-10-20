"""证书登记/轮换与断言交换；重放账本、令牌及撤销关联保持同一数据库事务。"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import jwt
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ...core.context import resolve_actor
from ...core.errors import DomainError
from ...core.models import LoginSession
from ...core.records import RecordStore
from .client_certificate_crypto import (
    ASSERTION_TYPE, assertion_header, certificate_active, configured_audience,
    parse_public_certificate, verify_assertion,
)
from .client_certificate_models import (
    ServiceCertificateTokenBinding, ServiceClientAssertion, ServiceClientCertificate,
)
from .models import ServiceAccess, ServiceIdentity
from .security import PERMISSION_UNIVERSE, validate_delegation
from .service_accounts import mint_service_token, service_get


class ClientAuthenticationError(Exception):
    """OAuth令牌端点使用标准error字段，绝不把断言或验签细节回显给调用方。"""
    def __init__(self, code="invalid_client", message="客户端认证失败", status=401):
        self.code, self.message, self.status = code, message, status


def certificate_dict(row):
    now = datetime.now(timezone.utc)
    status = ("revoked" if row.revoked_at else "disabled" if not row.enabled
              else "pending" if now < datetime.fromisoformat(row.not_before)
              else "expired" if now >= datetime.fromisoformat(row.not_after) else "active")
    return {**{key: getattr(row, key) for key in (
        "id", "workspace_id", "identity_id", "name", "fingerprint_sha256", "subject_text", "issuer_text",
        "serial_number", "algorithm", "not_before", "not_after", "enabled", "revoked_at", "revocation_reason",
        "replaced_by_id", "revision", "created_at",
    )}, "status": status}


class ClientCertificateService:
    def __init__(self, session, actor):
        self.session, self.actor = session, actor

    def identity(self, identity_id, *, write=False):
        self.actor.require("m14.admin" if write else "m14.read")
        identity = service_get(self.session, self.actor, identity_id)
        if write:
            validate_delegation(self.actor, identity.permissions)
        return identity

    def get(self, identity_id, certificate_id, *, write=False):
        self.identity(identity_id, write=write)
        cert = self.session.scalar(select(ServiceClientCertificate).where(
            ServiceClientCertificate.id == certificate_id,
            ServiceClientCertificate.identity_id == identity_id,
            ServiceClientCertificate.workspace_id == self.actor.workspace_id))
        if cert is None:
            raise DomainError(404, "client_certificate_missing", "客户端证书不存在")
        return cert

    def list(self, identity_id):
        self.identity(identity_id)
        rows = list(self.session.scalars(select(ServiceClientCertificate).where(
            ServiceClientCertificate.identity_id == identity_id,
            ServiceClientCertificate.workspace_id == self.actor.workspace_id).order_by(ServiceClientCertificate.created_at.desc())))
        return {"items": [certificate_dict(row) for row in rows], "total": len(rows)}

    def register(self, identity_id, name, pem):
        self.identity(identity_id, write=True)
        fields = parse_public_certificate(pem)
        duplicate = self.session.scalar(select(ServiceClientCertificate.id).where(
            ServiceClientCertificate.identity_id == identity_id,
            ServiceClientCertificate.fingerprint_sha256 == fields["fingerprint_sha256"]))
        if duplicate:
            raise DomainError(409, "client_certificate_duplicate", "该证书已登记", {"certificate_pem": "请使用另一张证书"})
        row = ServiceClientCertificate(workspace_id=self.actor.workspace_id, identity_id=identity_id,
                                       name=name, created_by=self.actor.user_id, **fields)
        self.session.add(row)
        self.session.flush()
        RecordStore(self.session, self.actor).audit("client_certificate.register", "service_identity", identity_id,
            {"certificate_id": row.id, "fingerprint_sha256": row.fingerprint_sha256})
        return row

    def change(self, identity_id, certificate_id, revision, changes, action="update"):
        row = self.get(identity_id, certificate_id, write=True)
        if row.revoked_at:
            raise DomainError(409, "client_certificate_revoked", "该证书已撤销")
        changed = self.session.execute(update(ServiceClientCertificate).where(
            ServiceClientCertificate.id == row.id, ServiceClientCertificate.workspace_id == self.actor.workspace_id,
            ServiceClientCertificate.revision == revision, ServiceClientCertificate.revoked_at.is_(None)
        ).values(**changes, revision=revision + 1), execution_options={"synchronize_session": False})
        if changed.rowcount != 1:
            raise DomainError(409, "revision_conflict", "证书已更新，请刷新")
        if changes.get("enabled") is False or changes.get("revoked_at"):
            # 停用后重新启用也不能复活旧令牌，必须由调用方重新认证。
            access_ids = select(ServiceCertificateTokenBinding.service_access_id).where(
                ServiceCertificateTokenBinding.certificate_id == row.id,
                ServiceCertificateTokenBinding.workspace_id == self.actor.workspace_id)
            login_ids = select(ServiceAccess.session_id).where(ServiceAccess.id.in_(access_ids))
            self.session.execute(update(LoginSession).where(LoginSession.id.in_(login_ids)).values(revoked=True))
            self.session.execute(update(ServiceAccess).where(ServiceAccess.id.in_(access_ids)).values(revoked=True))
        self.session.refresh(row)
        RecordStore(self.session, self.actor).audit("client_certificate." + action, "service_identity", identity_id,
            {"certificate_id": row.id, "fields": sorted(changes)})
        return row

    def rotate(self, identity_id, certificate_id, revision, name, pem):
        old = self.get(identity_id, certificate_id, write=True)
        if old.revision != revision or old.revoked_at:
            raise DomainError(409, "revision_conflict", "证书已更新，请刷新")
        replacement = self.register(identity_id, name, pem)
        if not certificate_active(replacement):
            raise DomainError(422, "replacement_not_active", "替换证书尚未生效", {"certificate_pem": "请选择当前有效的证书"})
        self.change(identity_id, certificate_id, revision, {
            "revoked_at": datetime.now(timezone.utc).isoformat(), "revocation_reason": "证书轮换",
            "replaced_by_id": replacement.id,
        }, "rotate")
        return certificate_dict(replacement)


def validate_certificate_binding(session, token: ServiceAccess, identity: ServiceIdentity):
    """仅证书认证签发的令牌需要关联校验；手动短令牌保留原有行为。"""
    binding = session.scalar(select(ServiceCertificateTokenBinding).where(
        ServiceCertificateTokenBinding.service_access_id == token.id))
    if binding is None:
        return
    cert = session.get(ServiceClientCertificate, binding.certificate_id)
    if (cert is None or binding.workspace_id != token.workspace_id
            or cert.workspace_id != token.workspace_id or cert.identity_id != identity.id
            or not certificate_active(cert)):
        raise DomainError(401, "client_certificate_inactive", "客户端证书已失效")


def exchange_assertion(session, settings, fields: dict):
    """只在验签、最新身份范围及防重放校验均通过后签发十分钟以内的短令牌。"""
    audience = configured_audience(settings)
    if fields.get("grant_type") != "client_credentials":
        raise ClientAuthenticationError("unsupported_grant_type", "不支持该授权方式", 400)
    if fields.get("client_assertion_type") != ASSERTION_TYPE:
        raise ClientAuthenticationError("invalid_request", "客户端断言类型无效", 400)
    client_id, assertion = fields.get("client_id", ""), fields.get("client_assertion", "")
    try:
        header = assertion_header(assertion)
        identity = session.scalar(select(ServiceIdentity).where(ServiceIdentity.id == client_id))
        cert = session.scalar(select(ServiceClientCertificate).where(
            ServiceClientCertificate.id == header["kid"], ServiceClientCertificate.identity_id == client_id))
        if (identity is None or not identity.enabled or cert is None
                or cert.workspace_id != identity.workspace_id or not certificate_active(cert)
                or header.get("alg") != cert.algorithm):
            raise ValueError()
        claims = verify_assertion(assertion, cert, client_id, audience)
        actor = resolve_actor(session, LoginSession(user_id=identity.user_id, workspace_id=identity.workspace_id))
    except (jwt.PyJWTError, ValueError, TypeError, UnicodeError, DomainError):
        raise ClientAuthenticationError() from None
    identity_actor = replace(actor, permissions=frozenset(identity.permissions), is_admin=False)
    effective = [p for p in PERMISSION_UNIVERSE if actor.can(p) and identity_actor.can(p)]
    requested = fields.get("scope")
    try:
        if requested is None:
            scopes = effective
        else:
            scopes = validate_delegation(actor, requested.split())
            validate_delegation(identity_actor, scopes)
        if not scopes:
            raise ValueError()
    except (DomainError, ValueError):
        raise ClientAuthenticationError("invalid_scope", "请求权限超出当前授权范围", 400) from None
    # 唯一约束是并发下的最终重放保护；与令牌和绑定同事务，不先提交一次性凭据。
    try:
        session.add(ServiceClientAssertion(workspace_id=identity.workspace_id, identity_id=identity.id,
            certificate_id=cert.id, jti_sha256=sha256(claims["jti"].encode()).hexdigest(),
            expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat()))
        session.flush()
    except IntegrityError:
        raise ClientAuthenticationError() from None
    now = datetime.now(timezone.utc)
    expires = min(now + timedelta(minutes=10), datetime.fromisoformat(cert.not_after))
    raw, access = mint_service_token(session, actor, identity, scopes, "证书接入 · " + cert.name, expires.isoformat())
    session.add(ServiceCertificateTokenBinding(workspace_id=identity.workspace_id,
                certificate_id=cert.id, service_access_id=access.id))
    session.flush()
    return {"access_token": raw, "token_type": "Bearer", "expires_in": max(0, int((expires - now).total_seconds())),
            "scope": " ".join(scopes)}
