"""Local integration clients and public-certificate trust records.

中文补充（集成客户端与证书）：
- 组成：IntegrationClient（第三方调用的客户端凭据 + 回调地址 + scope）、
  IntegrationAccessToken（短期访问令牌）、TrustedCertificate（公开证书信任记录）。
- 凭据只存摘要：client_secret / access_token 均只落 sha256 摘要 + 前缀（便于识别 + 判等），
  明文只在创建/轮换/换取令牌的响应里返回一次（secretShownOnce=True），之后无法再取回。
- 授权收敛（两条独立约束）：scope 必须是 ALL_PERMISSIONS 内已登记的权限，
  且必须是**创建者当前权限的子集**——客户端不能凭空获得创建者都没有的能力；
  令牌的 actor 是创建者，因此其数据范围随创建者当前状态变化（换取时重新校验成员有效性）。
- 回调地址：必须 HTTPS（仅 localhost/127.0.0.1/::1 允许 HTTP），不允许内嵌账号密码或片段。
- 轮换/停用会立即撤销该客户端所有未撤销令牌（revoked_at 置现在），不必等 TTL 到期。
- 证书：只接受公开证书，检测到 "PRIVATE KEY" 直接拒绝；用 SHA-256 指纹去重。
- 权限：查看 system.development.view，管理 system.development.manage；
  换取令牌 exchange_client_token 不走用户上下文，而是校验客户端密钥本身。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.config import settings
from app.errors import AuthError, ConflictError, ValidationError
from app.models import User, WorkspaceMember
from app.modules.system.permissions import ALL_PERMISSIONS
from app.modules.system.service_helpers import check_version, commit_change, flush_changes, scoped_record

from .models import IntegrationAccessToken, IntegrationClient, TrustedCertificate
from .schemas import CertificateInput, ClientTokenInput, IntegrationClientInput


def _secret() -> tuple[str, str, str]:
    """生成客户端密钥：返回 (明文, sha256 摘要, 前 12 位前缀)。

    明文只在响应里返回一次；摘要用于校验，前缀用于界面辨认"是哪一把密钥"。
    """
    value = "t5c_" + secrets.token_urlsafe(36)
    return value, hashlib.sha256(value.encode()).hexdigest(), value[:12]


def _client_id() -> str:
    """生成公开的 client_id（非机密，可以出现在前端与日志中）。"""
    return "t5_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24]


def _validate_redirects(values: list[str]) -> list[str]:
    """校验并去重回调地址：公网必须 HTTPS，仅本机回环允许 HTTP。

    同时拒绝内嵌用户名/密码与 URL 片段，避免回调被改写造成凭据泄露。
    异常：不合规抛 ValidationError。
    """
    result = []
    for value in values:
        parsed = urlparse(value.strip())
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme not in ({"http", "https"} if local else {"https"}) or not parsed.hostname:
            raise ValidationError("回调地址必须使用 HTTPS；仅本机 localhost/127.0.0.1 可使用 HTTP")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValidationError("回调地址不能包含账号、密码或片段")
        result.append(value.strip())
    return list(dict.fromkeys(result))


def _validate_scopes(ctx: AuthContext, scopes: list[str]) -> list[str]:
    """校验并去重 scope：必须已登记，且不超过当前用户自身的权限集合。

    第二条是关键的提权防线：否则任何能建客户端的人都可以申请自己并不具备的权限
    （如 system.admin），再用客户端令牌调用。异常：越界抛 ValidationError 并列出违规项。
    """
    normalized = list(dict.fromkeys(scopes))
    unknown = sorted(set(normalized) - set(ALL_PERMISSIONS))
    if unknown:
        raise ValidationError("存在未登记的接口权限", scopes=unknown)
    # Client scopes are always a subset of the creator's current permissions.
    from app.common.authorization import access_policy

    excess = sorted(set(normalized) - set(access_policy(ctx).permissions))
    if excess:
        raise ValidationError("不能授予超过当前用户权限的客户端范围", scopes=excess)
    return normalized


def client_view(row: IntegrationClient) -> dict:
    """客户端视图：只含 secretPrefix 与元数据，绝不含密钥明文或摘要。"""
    return {
        "id": row.id, "name": row.name, "clientId": row.client_id,
        "secretPrefix": row.secret_prefix, "redirectUris": row.redirect_uris,
        "scopes": row.scopes, "enabled": row.enabled, "lastUsedAt": row.last_used_at.isoformat() if row.last_used_at else None,
        "createdBy": row.created_by, "version": row.version,
        "createTime": row.created_at.isoformat(), "updateTime": row.updated_at.isoformat(),
    }


def list_clients(ctx: AuthContext, keyword: str = "") -> list[dict]:
    """列出本工作区集成客户端（按更新时间倒序）；权限 system.development.view。

    keyword 用 autoescape=True 匹配名称/client_id，避免用户输入被当作 LIKE 通配符。
    """
    require_permission(ctx, "system.development.view")
    statement = select(IntegrationClient).where(IntegrationClient.workspace_id == ctx.workspace_id)
    if keyword:
        statement = statement.where(or_(
            IntegrationClient.name.contains(keyword, autoescape=True),
            IntegrationClient.client_id.contains(keyword, autoescape=True),
        ))
    return [client_view(row) for row in ctx.db.scalars(
        statement.order_by(IntegrationClient.updated_at.desc(), IntegrationClient.id)
    ).all()]


def create_client(ctx: AuthContext, payload: IntegrationClientInput) -> dict:
    """创建集成客户端；权限 system.development.manage。

    新建时 version 必须为 0（防止误用编辑语义）；返回体额外带 clientSecret 与
    secretShownOnce=True——这是唯一能看到明文的时机，界面必须提示用户立即保存。
    副作用：写库 + 审计日志（记录 clientId 与 scopes）。
    """
    require_permission(ctx, "system.development.manage")
    if payload.version:
        raise ValidationError("创建客户端时 version 必须为 0")
    plaintext, digest, prefix = _secret()
    row = IntegrationClient(
        workspace_id=ctx.workspace_id, name=payload.name.strip(), client_id=_client_id(),
        secret_digest=digest, secret_prefix=prefix,
        redirect_uris=_validate_redirects(payload.redirect_uris),
        scopes=_validate_scopes(ctx, payload.scopes), enabled=payload.enabled, created_by=ctx.user_id,
    )
    ctx.db.add(row)
    flush_changes(ctx)
    commit_change(ctx, "system.integration_client.create", "integration_client", row.id,
                  {"clientId": row.client_id, "scopes": row.scopes})
    return {**client_view(row), "clientSecret": plaintext, "secretShownOnce": True}


def update_client(ctx: AuthContext, record_id: str, payload: IntegrationClientInput) -> dict:
    """修改客户端名称/回调/scope/启停；权限 system.development.manage。

    version 乐观锁；scope 会重新按"当前用户权限子集"校验（若创建者权限被收紧，
    下次修改时也会被要求同步收敛）。本函数不改动密钥，轮换请用 rotate_secret。
    """
    require_permission(ctx, "system.development.manage")
    row = scoped_record(ctx, IntegrationClient, record_id)
    check_version(row, payload.version)
    row.name = payload.name.strip()
    row.redirect_uris = _validate_redirects(payload.redirect_uris)
    row.scopes = _validate_scopes(ctx, payload.scopes)
    row.enabled = payload.enabled
    row.version += 1
    commit_change(ctx, "system.integration_client.update", "integration_client", row.id,
                  {"enabled": row.enabled, "scopes": row.scopes})
    return client_view(row)


def rotate_secret(ctx: AuthContext, record_id: str, version: int) -> dict:
    """轮换客户端密钥并立即撤销其全部未撤销令牌；权限 system.development.manage。

    停用的客户端不允许轮换（要求先启用并确认回调范围），避免"停用状态下换密钥"产生
    误解为可用。返回新明文密钥（仅此一次）。
    副作用：改 secret_digest/secret_prefix、批量置 revoked_at、写审计日志。
    """
    require_permission(ctx, "system.development.manage")
    row = scoped_record(ctx, IntegrationClient, record_id)
    check_version(row, version)
    if not row.enabled:
        raise ConflictError("停用客户端不能轮换密钥；请先启用并确认回调范围")
    plaintext, digest, prefix = _secret()
    row.secret_digest, row.secret_prefix = digest, prefix
    ctx.db.execute(update(IntegrationAccessToken).where(
        IntegrationAccessToken.workspace_id == ctx.workspace_id,
        IntegrationAccessToken.client_record_id == row.id,
        IntegrationAccessToken.revoked_at.is_(None),
    ).values(revoked_at=datetime.utcnow()))
    row.version += 1
    commit_change(ctx, "system.integration_client.rotate", "integration_client", row.id)
    return {**client_view(row), "clientSecret": plaintext, "secretShownOnce": True}


def disable_client(ctx: AuthContext, record_id: str, version: int) -> dict:
    """停用客户端并立即撤销其全部未撤销令牌；权限 system.development.manage。

    停用是即时生效的（不等令牌 TTL），因此可作为应急止损手段。
    """
    require_permission(ctx, "system.development.manage")
    row = scoped_record(ctx, IntegrationClient, record_id)
    check_version(row, version)
    row.enabled, row.version = False, row.version + 1
    ctx.db.execute(update(IntegrationAccessToken).where(
        IntegrationAccessToken.workspace_id == ctx.workspace_id,
        IntegrationAccessToken.client_record_id == row.id,
        IntegrationAccessToken.revoked_at.is_(None),
    ).values(revoked_at=datetime.utcnow()))
    commit_change(ctx, "system.integration_client.disable", "integration_client", row.id)
    return client_view(row)


def exchange_client_token(db: Session, payload: ClientTokenInput) -> dict:
    """Exchange a client secret for a short-lived, scope-limited service token.

    中文补充：第三方以 client_id + client_secret 换取短期访问令牌（这是唯一无需
    用户登录态的入口）。校验要点：
    - 必须同时命中 client_id 与 workspace_id，且客户端处于启用状态；
    - 密钥用 secrets.compare_digest 做常量时间比较，防时序侧信道；
    - 失败统一返回"客户端标识或密钥错误"，不区分是哪一个错（防枚举）；
    - 令牌的 actor_user_id 固定为创建者，换取时重新校验该用户仍 active 且仍是成员，
      否则视为"执行身份不可用"拒绝——创建者被移除权限后令牌路径随之失效；
    - scope 原样复制客户端的 scope 列表（不放大）。
    返回：accessToken/tokenType/expiresIn/expiresAt/scopes（明文令牌只此一次）。
    副作用：写 IntegrationAccessToken、更新 last_used_at（不递增客户端配置版本）、写审计日志。
    """
    row = db.scalar(select(IntegrationClient).where(
        IntegrationClient.client_id == payload.client_id,
        IntegrationClient.workspace_id == payload.workspace_id,
    ))
    digest = hashlib.sha256(payload.client_secret.encode("utf-8")).hexdigest()
    if row is None or not row.enabled or not secrets.compare_digest(row.secret_digest, digest):
        raise AuthError("客户端标识或密钥错误")
    user = db.get(User, row.created_by)
    member = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == row.workspace_id,
        WorkspaceMember.user_id == row.created_by,
    ))
    if user is None or user.status != "active" or member is None:
        raise AuthError("客户端执行身份不可用，请由管理员重新创建客户端")
    raw = "t5a_" + secrets.token_urlsafe(40)
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=settings.INTEGRATION_TOKEN_TTL_SECONDS)
    token = IntegrationAccessToken(
        workspace_id=row.workspace_id, client_record_id=row.id, actor_user_id=row.created_by,
        token_digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(), scopes=list(row.scopes),
        expires_at=expires_at, last_used_at=now,
    )
    db.add(token)
    # Usage telemetry must not advance the client's configuration version.
    db.execute(update(IntegrationClient).where(IntegrationClient.id == row.id).values(
        last_used_at=now,
    ).execution_options(synchronize_session=False))
    from app.common.audit import audit
    audit(db, workspace_id=row.workspace_id, actor_user_id=row.created_by,
          action="system.integration_client.token", resource_type="integration_client", resource_id=row.id,
          metadata={"scopes": row.scopes, "expiresAt": expires_at.isoformat()})
    db.commit()
    return {
        "accessToken": raw, "tokenType": "Bearer",
        "expiresIn": settings.INTEGRATION_TOKEN_TTL_SECONDS,
        "expiresAt": expires_at.isoformat(), "workspaceId": row.workspace_id,
        "scopes": list(row.scopes),
    }


def certificate_view(row: TrustedCertificate) -> dict:
    """证书视图：返回指纹/主体/颁发者/有效期等公开信息。

    注意 ``pem`` 字段**不在此返回**（证书本体属内部信任材料，需要下载时另有出口）。
    """
    return {
        "id": row.id, "name": row.name, "kind": row.kind,
        "fingerprintSha256": row.fingerprint_sha256, "subject": row.subject,
        "issuer": row.issuer, "serialNumber": row.serial_number,
        "notBefore": row.not_before.isoformat(), "notAfter": row.not_after.isoformat(),
        "enabled": row.enabled, "createdBy": row.created_by, "version": row.version,
        "createTime": row.created_at.isoformat(), "updateTime": row.updated_at.isoformat(),
    }


def list_certificates(ctx: AuthContext) -> list[dict]:
    """列出本工作区信任证书（按到期时间升序，便于发现即将过期项）；权限 system.development.view。"""
    require_permission(ctx, "system.development.view")
    rows = ctx.db.scalars(select(TrustedCertificate).where(
        TrustedCertificate.workspace_id == ctx.workspace_id,
    ).order_by(TrustedCertificate.not_after, TrustedCertificate.name)).all()
    return [certificate_view(row) for row in rows]


def add_certificate(ctx: AuthContext, payload: CertificateInput) -> dict:
    """登记一张公开证书；权限 system.development.manage。

    只读边界：明确拒绝私钥（内容含 "PRIVATE KEY"），本接口不生成、不保存任何私钥；
    解析失败按"无法解析 X.509 PEM"报错；同一指纹重复登记抛 ConflictError（幂等保护）。
    有效期统一转 UTC naive 存储。副作用：写库 + 审计日志（记录指纹与到期时间）。
    """
    require_permission(ctx, "system.development.manage")
    if "PRIVATE KEY" in payload.pem.upper():
        raise ValidationError("此页面只接受公开证书，禁止上传私钥")
    try:
        certificate = x509.load_pem_x509_certificate(payload.pem.encode("utf-8"))
    except Exception as exc:
        raise ValidationError("无法解析 X.509 PEM 证书") from exc
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    existing = ctx.db.scalar(select(TrustedCertificate).where(
        TrustedCertificate.workspace_id == ctx.workspace_id,
        TrustedCertificate.fingerprint_sha256 == fingerprint,
    ))
    if existing:
        raise ConflictError("该证书已登记", certificate_id=existing.id)
    not_before = certificate.not_valid_before_utc.astimezone(timezone.utc).replace(tzinfo=None)
    not_after = certificate.not_valid_after_utc.astimezone(timezone.utc).replace(tzinfo=None)
    row = TrustedCertificate(
        workspace_id=ctx.workspace_id, name=payload.name.strip(), kind=payload.kind,
        fingerprint_sha256=fingerprint, subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(), serial_number=format(certificate.serial_number, "x"),
        not_before=not_before, not_after=not_after, pem=payload.pem.strip() + "\n",
        enabled=payload.enabled, created_by=ctx.user_id,
    )
    ctx.db.add(row)
    flush_changes(ctx)
    commit_change(ctx, "system.certificate.add", "trusted_certificate", row.id,
                  {"fingerprint": fingerprint, "kind": row.kind, "notAfter": not_after.isoformat()})
    return certificate_view(row)


def disable_certificate(ctx: AuthContext, record_id: str, version: int) -> dict:
    """停用证书（软停用，记录保留以便回溯）；权限 system.development.manage。

    停用后该证书不再被信任；version 乐观锁防止覆盖他人改动。
    """
    require_permission(ctx, "system.development.manage")
    row = scoped_record(ctx, TrustedCertificate, record_id)
    check_version(row, version)
    row.enabled, row.version = False, row.version + 1
    commit_change(ctx, "system.certificate.disable", "trusted_certificate", row.id)
    return certificate_view(row)
