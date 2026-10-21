"""引擎证书、原生用户签名及有效对话任务三重校验；不修改全局登录协议。

用户JWT适配Open WebUI backend/open_webui/utils/headers.py的_mint_forward_user_jwt，
保留其HS256/issuer/sub/iat/exp协议，仅将其用于已绑定引擎的工具身份，不用作平台登录。
"""

from dataclasses import replace
from datetime import datetime, timezone
import re
import time

import jwt
from sqlalchemy import select

from agent_platform.core.context import resolve_actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import LoginSession, Membership, ResourceRecord
from ..identity.client_certificate_models import ServiceCertificateTokenBinding
from ..identity.client_certificate_service import validate_certificate_binding
from ..identity.models import ServiceAccess, ServiceIdentity
from ..identity.security import PERMISSION_UNIVERSE
from .delegated_contract import HEADERS


def engine_identity(session, actor, request):
    """普通浏览器会话及手工服务令牌不能进入委托入口，必须使用有效证书短令牌。"""
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise DomainError(401, "delegation_certificate_required", "请使用引擎证书认证")
    access = session.scalar(select(ServiceAccess).where(ServiceAccess.session_id == actor.session_id,
        ServiceAccess.workspace_id == actor.workspace_id))
    identity = session.get(ServiceIdentity, access.identity_id) if access else None
    login = session.get(LoginSession, actor.session_id)
    now = datetime.now(timezone.utc)
    if (identity is None or login is None or not identity.enabled or identity.user_id != actor.user_id
            or identity.workspace_id != actor.workspace_id or login.user_id != actor.user_id
            or login.workspace_id != actor.workspace_id or login.revoked or access.revoked
            or datetime.fromisoformat(access.expires_at) <= now or datetime.fromisoformat(login.expires_at) <= now):
        raise DomainError(401, "delegation_engine_inactive", "引擎身份已失效")
    binding = session.scalar(select(ServiceCertificateTokenBinding).where(
        ServiceCertificateTokenBinding.service_access_id == access.id,
        ServiceCertificateTokenBinding.workspace_id == actor.workspace_id))
    if binding is None:
        raise DomainError(401, "delegation_certificate_required", "请使用引擎证书认证")
    validate_certificate_binding(session, access, identity)
    actor.require("m02.read")
    return identity


def _signed_subject(assertion, secret):
    """适配Open WebUI原生HS256转发JWT，严格固定issuer和五分钟有效期，不采信明文用户头。"""
    try:
        if not isinstance(assertion, str) or not 1 <= len(assertion) <= 8192 or not isinstance(secret, str) or len(secret) < 32:
            raise ValueError()
        claims = jwt.decode(assertion, secret, algorithms=["HS256"], issuer="open-webui",
            options={"require": ["sub", "iss", "iat", "exp"]}, leeway=5)
        issued, expires = claims["iat"], claims["exp"]
        if (type(issued) is not int or type(expires) is not int or not 0 < expires - issued <= 300
                or issued > time.time() + 5 or not isinstance(claims["sub"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", claims["sub"])):
            raise ValueError()
        return claims["sub"]
    except (jwt.PyJWTError, ValueError, TypeError, UnicodeError):
        raise DomainError(401, "delegation_user_assertion_invalid", "对话用户签名缺失或已失效") from None


def resolve_delegated_actor(session, actor, request):
    """从后台绑定找到验签密钥，再用精确原生三元组解析人类主体；不接受自报平台身份。"""
    engine_identity(session, actor, request)
    if any(len(request.headers.getlist(header)) != 1 for header in HEADERS):
        raise DomainError(401, "delegation_user_assertion_invalid", "对话用户签名或任务标识缺失")
    candidates = list(session.scalars(select(ResourceRecord).where(
        ResourceRecord.workspace_id == actor.workspace_id, ResourceRecord.kind == "integration_instance",
        ResourceRecord.deleted.is_(False), ResourceRecord.archived.is_(False),
        ResourceRecord.data["delegation_service_identity_id"].as_string() == actor.user_id).limit(2)))
    if len(candidates) != 1:
        raise DomainError(503, "delegation_instance_unavailable", "引擎委托绑定尚未就绪")
    integration = candidates[0]
    ref = integration.data.get("delegation_jwt_secret_ref")
    if integration.data.get("engine") != "openwebui" or not integration.data.get("enabled", True) or not isinstance(ref, str) or not ref:
        raise DomainError(503, "delegation_instance_unavailable", "引擎委托绑定尚未就绪")
    try:
        vault = getattr(request.app.state, "secret_vault", None)
        if vault is None:
            raise DomainError(503, "delegation_signing_key_unavailable", "对话用户签名配置不可用")
        secret = vault.get(ref, actor.workspace_id, session=session)
    except DomainError:
        raise DomainError(503, "delegation_signing_key_unavailable", "对话用户签名配置不可用") from None
    native_user_id = _signed_subject(request.headers.get(HEADERS[0]), secret)
    chat_id, message_id = (request.headers.get(header, "") for header in HEADERS[1:])
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value) for value in (chat_id, message_id)):
        raise DomainError(403, "delegation_turn_required", "缺少有效的当前对话任务")
    from ..chat.native_sources import resolve_delegation_context
    context = resolve_delegation_context(session, engine_identity_id=actor.user_id,
        native_user_id=native_user_id, native_chat_id=chat_id, native_message_id=message_id)
    if context["instance_id"] != integration.id or context["workspace_id"] != actor.workspace_id or not context.get("active"):
        raise DomainError(403, "native_delegation_denied", "对话委托归属不一致")
    user_id = context["actor_user_id"]
    if session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.user_id == user_id)):
        raise DomainError(403, "delegation_human_required", "该对话身份不能代表人员操作")
    member = session.scalar(select(Membership).where(Membership.user_id == user_id,
        Membership.workspace_id == actor.workspace_id, Membership.disabled.is_(False)))
    if member is None:
        raise DomainError(403, "workspace_denied", "当前账号未获该工作区授权")
    human = resolve_actor(session, LoginSession(user_id=user_id, workspace_id=actor.workspace_id))
    permissions = frozenset(permission for permission in PERMISSION_UNIVERSE
                            if human.can(permission) and actor.can(permission))
    delegated = replace(human, permissions=permissions, is_admin=False, session_id="")
    delegated.require("m02.read")
    return delegated, context
