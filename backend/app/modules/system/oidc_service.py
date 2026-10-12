"""OIDC administration and authorization-code + PKCE login flow.

Only signed ID tokens are accepted. State is single-use, PKCE verifiers are
encrypted at rest, redirects are fixed to this application, and provider
secrets remain in the existing AES-GCM secret vault.

职责：OIDC 提供方的配置管理（增删改查、发现文档探测）与“授权码 + PKCE”登录流程的完整校验链。

位置与依赖：对外路由前缀 /api/v1/system（governance_router.py）—— GET/POST /sso-providers、
PATCH /sso-providers/{id}、POST /sso-providers/{id}/discover、DELETE /sso-providers/{id}；
免登录入口在鉴权模块（/sso/... 的发起与回调）里调用 begin_authorization / finish_authorization /
public_providers。依赖：密钥模块（client secret 与 PKCE verifier 的加解密）、
collection.transport.resolve_destination（出网目标校验）、security_service（登录策略与证据）、
bootstrap.ensure_system_roles（默认 SSO 角色）。

校验链（按执行顺序，任何一步失败都中止登录并回退事务）：
1. state：回调携带的 state 只做 sha256 后与 state_digest 比对，命中记录必须存在、未 consumed、
   未过期；读取时用 SELECT ... FOR UPDATE，保证同一 state 只能被消费一次（防重放/并发双击）。
2. PKCE：发起的 code_verifier 加密存储，回调时解密后随 code 提交给令牌端点；授权请求发送
   code_challenge=S256(verifier)，可抵御授权码被截获后的直接兑换。
3. 签名：ID Token 必须为三段式 JWT，alg 仅接受 RS256/PS256/ES256，按 kid 从 jwks_uri 取公钥验签
   （不支持 alg=none、不支持对称密钥 HS*、不支持 JWE 加密令牌）。
4. claims：iss 必须与配置的 issuer 完全一致（忽略结尾斜杠）；aud 必须包含本 client_id，
   且当 aud 含多个值时必须提供 azp=client_id；exp 允许 60 秒时钟偏移，iat 不允许晚于现在 120 秒；
   nonce 用 compare_digest 与发起时记录的值比对；sub 必须非空；
   若提供 at_hash/c_hash 则校验其与 access_token/authorization code 的绑定关系。
5. 身份映射：按 DEFAULT_CLAIMS + provider.claims 的键映射取 sub/email/name/username，可选校验
   email_verified 与 allowed_domains；sub 作为本地身份标识（provider="oidc-<provider_id 前 12 位哈希>"）。
6. 登录策略：调用 security_service.evaluate_login（IP/设备/会话上限）并写入会话与登录事件。

尚未实现（改动前请先确认产品需求）：
- 用户组/角色映射：claims 映射只允许 subject/email/name/username 四个键（_apply_payload 会拒绝
  其它键），因此无法按 IdP 的 group/roles 自动映射本地角色，SSO 用户一律使用 default_role_code；
- 单点登出：没有 RP-initiated logout、back-channel logout，也不处理刷新令牌（不保存 refresh_token，
  会话过期后需重新走 SSO）；
- JWKS 缓存：每次验签都实时拉取 jwks_uri，未做缓存/轮换预热；密钥轮换期间可能多一次网络往返；
- 用户组自动降权：auto_provision 只负责创建，不会在 IdP 侧移除用户时回收本地成员关系；
- SSO 强制：不支持“某组织必须走 SSO、禁止本地密码登录”的策略。
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import select

from app.common.audit import audit
from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.crypto import decrypt_secret, encrypt_secret
from app.errors import AuthError, ConflictError, NotFoundError, ValidationError
from app.ids import new_id
from app.models import (
    AuthIdentity, LocalCredential, OrganizationMember, SessionToken, User,
    Workspace, WorkspaceMember,
)
from app.modules.collection.transport import resolve_destination
from app.modules.secret.service import create_secret, revoke_secret
from app.modules.system.bootstrap import ensure_system_roles
from app.modules.system.models import (
    MemberProfile, OidcLoginState, OidcProvider, OrganizationProfile, Role,
    UserRole, WorkspaceLifecycle,
)
from app.modules.system.security_schemas import OidcProviderCreate, OidcProviderUpdate
from app.modules.system.service_helpers import check_version
from app.security import generate_session_token

# 默认 claim 映射键：可被 provider.claims 覆盖，但键集合固定为这四个，
# 校验在 _apply_payload 中执行（不允许配置 group/roles 等未支持键，避免出现“配了不生效”的映射）。
DEFAULT_CLAIMS = {
    "subject": "sub", "email": "email", "name": "name", "username": "preferred_username",
}


def _b64url_decode(value: str) -> bytes:
    """JWT 段的 base64url 解码（自动补齐 '=' 填充，兼容省略填充的 IdP 实现）。"""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64url(value: bytes) -> str:
    """base64url 编码并去掉填充，用于 code_challenge 与 at_hash/c_hash 比较。"""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _validate_url(value: str, allow_private: bool, *, name: str) -> str:
    """校验 OIDC 端点地址：必须是 HTTPS（除显式允许内网时），且无凭据、无片段、无重定向风险。

    参数：value 待校验地址；allow_private 为 True 时允许本机/内网地址（联调场景）；
    name 用于拼接中文错误信息（Issuer/授权端点/令牌端点/JWKS 地址等）。
    返回：去掉首尾空白与结尾斜杠的地址。
    异常：ValidationError —— 协议或格式非法、非 HTTPS 且未允许内网、目标解析不通过出网策略。
    约束：调用 resolve_destination 做 DNS/私网地址检查（deadline 4 秒），可防止 SSRF 与
    "https://合法域名 → 内网 IP" 这类跳转型攻击；本函数是唯一允许校验外部地址的入口。
    """
    clean = value.strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValidationError(f"{name} 必须是无凭据、无片段的 HTTP(S) 地址")
    if parsed.scheme != "https" and not allow_private:
        raise ValidationError(f"{name} 必须使用 HTTPS；本机/内网联调需显式允许内网地址")
    try:
        resolve_destination(clean, allow_private=allow_private, deadline=time.monotonic() + 4)
    except Exception as exc:
        raise ValidationError(f"{name} 目标地址不可用或不符合网络安全策略") from exc
    return clean


def _validate_role(db, workspace_id: str, code: str) -> Role:
    """校验 SSO 自动开通使用的默认角色存在且启用。

    参数：db；workspace_id；code 角色编码（provider.default_role_code）。
    返回：启用的 Role。异常：ValidationError（角色不存在或已停用）。
    副作用：先调用 ensure_system_roles 补齐内置角色（幂等，可能插入内置角色行），因此
    在“配置刚启用”的场景下也能拿到 workspace_admin/member/viewer。
    """
    ensure_system_roles(db, workspace_id)
    role = db.scalar(select(Role).where(
        Role.workspace_id == workspace_id, Role.code == code, Role.enabled.is_(True),
    ))
    if role is None:
        raise ValidationError("默认 SSO 角色不存在或已停用")
    return role


def provider_out(row: OidcProvider, *, workspace_name: str = "") -> dict:
    """提供方输出结构（管理页与创建/修改响应共用）。

    参数：row 提供方行；workspace_name 由调用方传入（管理页需要展示所属工作区名称）。
    返回：包含端点、client_id、claims 合并默认值、探测状态与 version 的字典。
    安全约束：只输出 client_secret_configured（布尔），绝不返回密钥内容或 client_secret_id 之外的信息。
    """
    return {
        "id": row.id, "workspace_id": row.workspace_id, "workspace_name": workspace_name,
        "code": row.code, "display_name": row.display_name, "issuer_url": row.issuer_url,
        "authorization_endpoint": row.authorization_endpoint,
        "token_endpoint": row.token_endpoint, "jwks_uri": row.jwks_uri,
        "client_id": row.client_id, "client_secret_configured": bool(row.client_secret_id),
        "token_auth_method": row.token_auth_method, "scopes": row.scopes,
        "claims": {**DEFAULT_CLAIMS, **(row.claims or {})},
        "allowed_domains": row.allowed_domains, "auto_provision": row.auto_provision,
        "default_role_code": row.default_role_code, "allow_private": row.allow_private,
        "enabled": row.enabled, "last_probe_at": row.last_probe_at,
        "last_probe_status": row.last_probe_status, "last_probe_message": row.last_probe_message,
        "version": row.version, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def list_admin(ctx: AuthContext) -> list[dict]:
    """列出当前工作区的 OIDC 提供方（GET /api/v1/system/sso-providers，需要 system.security.view）。

    返回：provider_out 列表。异常：PermissionErrorEx（缺少查看权限）。
    副作用：只读；不含 client secret，也不含其它工作区的配置。
    """
    require_permission(ctx, "system.security.view")
    rows = ctx.db.scalars(select(OidcProvider).where(
        OidcProvider.workspace_id == ctx.workspace_id,
    ).order_by(OidcProvider.display_name, OidcProvider.id)).all()
    return [provider_out(row, workspace_name=ctx.workspace.name) for row in rows]


def _apply_payload(ctx: AuthContext, row: OidcProvider, values: dict, *, creating: bool) -> None:
    """把创建/修改入参规范化后写入提供方对象（创建与修改共用的唯一落值入口）。

    参数：ctx；row 目标行（创建时是未入库的新对象）；values 为 payload.model_dump 的结果
    （修改时已 exclude_unset）；creating 用于决定“未提供字段”的默认值来源：创建时按字段默认，
    修改时沿用行内现有值。
    异常：ValidationError —— 端点地址非法、启用了但默认角色不存在、启用的机密客户端没有密钥、
    scope 缺少 openid、claims 含不支持的键。
    副作用：
    - 端点地址统一经过 _validate_url；
    - 若传入非空 client_secret，则调用密钥模块创建新密钥、把 row.client_secret_id 指向它，
      并把旧密钥标记为 revoked（轮换语义：改了密码就必须重新登录一次）；
    - 字符串字段 strip；allowed_domains 去重小写；scopes 去重且强制包含 openid；
      claims 过滤空值。kind：本函数不提交事务，由 create_provider/update_provider 负责。
    """
    allow_private = bool(values.get("allow_private", row.allow_private if not creating else False))
    for field, label in {
        "issuer_url": "Issuer", "authorization_endpoint": "授权端点",
        "token_endpoint": "令牌端点", "jwks_uri": "JWKS 地址",
    }.items():
        current = values.get(field, getattr(row, field, ""))
        values[field] = _validate_url(current, allow_private, name=label)
    if values.get("enabled", row.enabled if not creating else False):
        _validate_role(ctx.db, ctx.workspace_id, values.get("default_role_code", row.default_role_code))
        method = values.get("token_auth_method", row.token_auth_method)
        secret_value = values.get("client_secret")
        if method != "none" and not (secret_value or row.client_secret_id):
            raise ValidationError("启用的机密客户端必须配置 client secret")
    secret_value = values.pop("client_secret", None)
    if secret_value is not None and secret_value.strip():
        secret = create_secret(
            ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
            name=f"oidc-{row.code}-{new_id()}", value=secret_value.strip(), provider="oidc_client_secret",
        )
        previous = row.client_secret_id
        row.client_secret_id = secret.id
        if previous:
            previous_row = ctx.db.get(__import__("app.models", fromlist=["Secret"]).Secret, previous)
            if previous_row and previous_row.workspace_id == ctx.workspace_id:
                previous_row.status = "revoked"
                previous_row.deleted_at = datetime.utcnow()
                previous_row.updated_at = datetime.utcnow()
    for key, value in values.items():
        if key == "version":
            continue
        if isinstance(value, str):
            value = value.strip()
        if key == "allowed_domains":
            value = sorted({item.strip().lower() for item in value if item.strip()})
        elif key == "scopes":
            value = list(dict.fromkeys(item.strip() for item in value if item.strip()))
            if "openid" not in value:
                raise ValidationError("OIDC scope 必须包含 openid")
        elif key == "claims":
            unknown = set(value) - set(DEFAULT_CLAIMS)
            if unknown:
                raise ValidationError("声明映射包含不支持的键", claims=sorted(unknown))
            value = {key: str(item).strip() for key, item in value.items() if str(item).strip()}
        setattr(row, key, value)


def create_provider(ctx: AuthContext, payload: OidcProviderCreate) -> dict:
    """新建 OIDC 提供方（POST /api/v1/system/sso-providers，需要 system.security.manage）。

    参数：ctx；payload 为 OidcProviderCreate。返回：provider_out。
    异常：ValidationError（地址/角色/密钥/scope/claims 校验失败）；ConflictError（同工作区内 code 重复）；
    PermissionErrorEx（缺少管理权限）。
    副作用：写 system_oidc_providers 与（可选）密钥模块、写 oidc_provider.create 审计并提交。
    注意 code 会被规范化为小写且不可再修改；新建默认 enabled=False，需显式启用后才出现在登录页。
    """
    require_permission(ctx, "system.security.manage")
    row = OidcProvider(
        id=new_id(), workspace_id=ctx.workspace_id, code=payload.code.strip().lower(),
        display_name=payload.display_name.strip(), issuer_url=payload.issuer_url,
        authorization_endpoint=payload.authorization_endpoint, token_endpoint=payload.token_endpoint,
        jwks_uri=payload.jwks_uri, client_id=payload.client_id.strip(), client_secret_id=None,
    )
    values = payload.model_dump()
    values["code"] = row.code
    _apply_payload(ctx, row, values, creating=True)
    ctx.db.add(row)
    try:
        ctx.db.flush()
    except Exception as exc:
        ctx.db.rollback()
        raise ConflictError("当前工作区已存在相同 SSO 编码") from exc
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="oidc_provider.create", resource_type="oidc_provider", resource_id=row.id,
          metadata={"code": row.code, "enabled": row.enabled})
    ctx.db.commit()
    return provider_out(row, workspace_name=ctx.workspace.name)


def update_provider(ctx: AuthContext, provider_id: str, payload: OidcProviderUpdate) -> dict:
    """修改 OIDC 提供方（PATCH /api/v1/system/sso-providers/{id}，需要 system.security.manage）。

    参数：ctx；provider_id；payload 为 OidcProviderUpdate（version 必填）。返回：provider_out。
    异常：NotFoundError（配置不属于本工作区）；ConflictError（版本冲突）；ValidationError（同 _apply_payload）。
    副作用：更新配置行、必要时轮换 client secret（旧密钥立即 revoked）、写 oidc_provider.update 审计
    （只记录字段名，不记录密钥值）并提交。启用/停用会立即影响登录页显示的 SSO 入口。
    """
    require_permission(ctx, "system.security.manage")
    row = ctx.db.scalar(select(OidcProvider).where(
        OidcProvider.id == provider_id, OidcProvider.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("SSO 配置不存在")
    check_version(row, payload.version)
    values = payload.model_dump(exclude_unset=True)
    _apply_payload(ctx, row, values, creating=False)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="oidc_provider.update", resource_type="oidc_provider", resource_id=row.id,
          metadata={"fields": sorted(payload.model_fields_set - {"client_secret", "version"})})
    ctx.db.commit()
    return provider_out(row, workspace_name=ctx.workspace.name)


def delete_provider(ctx: AuthContext, provider_id: str) -> None:
    """删除 OIDC 提供方（DELETE /api/v1/system/sso-providers/{id}，需要 system.security.manage）。

    参数：ctx；provider_id。返回：无。
    异常：NotFoundError（配置不存在于本工作区）；ConflictError（仍处于启用状态，需先停用）；
    PermissionErrorEx（缺少管理权限）。
    副作用：删除配置行、把其 client secret 标记为 revoked（不物理删除密钥记录，保留审计），
    并写 oidc_provider.delete 审计后提交。已通过该提供方登录的用户不受影响（会话独立）。
    """
    require_permission(ctx, "system.security.manage")
    row = ctx.db.scalar(select(OidcProvider).where(
        OidcProvider.id == provider_id, OidcProvider.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("SSO 配置不存在")
    if row.enabled:
        raise ConflictError("请先停用 SSO 配置再删除")
    if row.client_secret_id:
        secret = ctx.db.get(__import__("app.models", fromlist=["Secret"]).Secret, row.client_secret_id)
        if secret and secret.workspace_id == ctx.workspace_id:
            secret.status = "revoked"
            secret.deleted_at = datetime.utcnow()
            secret.updated_at = datetime.utcnow()
    ctx.db.delete(row)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="oidc_provider.delete", resource_type="oidc_provider", resource_id=provider_id)
    ctx.db.commit()


def discover_provider(ctx: AuthContext, provider_id: str) -> dict:
    """读取 OIDC 发现文档并校验签名密钥（POST /api/v1/system/sso-providers/{id}/discover）。

    用途：管理员点“探测”时验证 issuer/授权端点/令牌端点/JWKS 是否可用，并把探测结果落库供列表展示。
    参数：ctx；provider_id。返回：{status, key_count|error, latency_ms}。
    异常：NotFoundError（配置不属于本工作区）；PermissionErrorEx（缺少管理权限）；
    注意：网络/格式错误不会抛出 —— 会被捕获后写成 last_probe_status="failed"，
    这样管理员能在页面上看到具体失败原因而不是只拿到一个 500。
    副作用：成功时用发现文档覆盖三个端点地址（以 IdP 为准，避免手工填错），并更新
    last_probe_at/status/message，写 oidc_provider.discover 审计后提交；探测使用的地址同样
    经过 _validate_url（含出网策略），并拒绝跟随重定向（follow_redirects=False）。
    """
    require_permission(ctx, "system.security.manage")
    row = ctx.db.scalar(select(OidcProvider).where(
        OidcProvider.id == provider_id, OidcProvider.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("SSO 配置不存在")
    discovery_url = _validate_url(
        row.issuer_url.rstrip("/") + "/.well-known/openid-configuration",
        row.allow_private, name="OIDC 发现地址",
    )
    started = time.perf_counter()
    try:
        response = httpx.get(discovery_url, timeout=8.0, follow_redirects=False,
                             headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        if str(data.get("issuer", "")).rstrip("/") != row.issuer_url.rstrip("/"):
            raise ValueError("发现文档 issuer 与配置不一致")
        endpoints = {
            "authorization_endpoint": _validate_url(data.get("authorization_endpoint", ""), row.allow_private, name="授权端点"),
            "token_endpoint": _validate_url(data.get("token_endpoint", ""), row.allow_private, name="令牌端点"),
            "jwks_uri": _validate_url(data.get("jwks_uri", ""), row.allow_private, name="JWKS 地址"),
        }
        jwks = httpx.get(endpoints["jwks_uri"], timeout=8.0, follow_redirects=False,
                         headers={"Accept": "application/json"})
        jwks.raise_for_status()
        keys = jwks.json().get("keys") or []
        if not any(item.get("kty") in {"RSA", "EC"} and item.get("use", "sig") == "sig" for item in keys):
            raise ValueError("JWKS 未提供可用签名密钥")
        for key, value in endpoints.items():
            setattr(row, key, value)
        row.last_probe_status = "passed"
        row.last_probe_message = f"发现文档和 {len(keys)} 个签名密钥可用"
        details = {"status": "passed", "key_count": len(keys), "latency_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        row.last_probe_status = "failed"
        row.last_probe_message = f"{type(exc).__name__}: {str(exc)[:400]}"
        details = {"status": "failed", "error": row.last_probe_message,
                   "latency_ms": int((time.perf_counter() - started) * 1000)}
    row.last_probe_at = datetime.utcnow()
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="oidc_provider.discover", resource_type="oidc_provider", resource_id=row.id,
          metadata={"status": row.last_probe_status})
    ctx.db.commit()
    return details


def public_providers(db) -> list[dict]:
    """登录页使用的 SSO 入口列表（无需登录即可调用）。

    筛选规则（三重过滤，防止把已停用/已归档组织的入口暴露给匿名用户）：
    1. provider.enabled 为 True；2. 其工作区生命周期为 active；3. 其组织状态为 active。
    返回：{id, code, name, workspace_id, workspace_name} 列表 —— 只暴露展示所需字段，
    不含端点、client_id 等配置细节（避免匿名枚举攻击面）。
    副作用：只读；调用方不得依赖返回顺序做业务判断（排序仅用于展示稳定）。
    """
    rows = db.execute(select(OidcProvider, Workspace).join(
        Workspace, Workspace.id == OidcProvider.workspace_id,
    ).where(OidcProvider.enabled.is_(True)).order_by(Workspace.name, OidcProvider.display_name)).all()
    items = []
    for row, workspace in rows:
        lifecycle = db.scalar(select(WorkspaceLifecycle).where(
            WorkspaceLifecycle.workspace_id == workspace.id,
        ))
        organization = db.scalar(select(OrganizationProfile).where(
            OrganizationProfile.organization_id == workspace.organization_id,
        ))
        if lifecycle and lifecycle.status != "active":
            continue
        if organization and organization.status != "active":
            continue
        items.append({"id": row.id, "code": row.code, "name": row.display_name,
                      "workspace_id": workspace.id, "workspace_name": workspace.name})
    return items


def begin_authorization(db, provider_id: str, request, device_id: str | None) -> dict:
    """SSO 登录第 1 步：生成 state/nonce/PKCE 并返回授权跳转地址（免登录接口）。

    参数：db；provider_id 必须存在且 enabled；request 用于固定 redirect_uri（取 base_url + /sso/callback）
    与记录发起时的 IP/UA；device_id 前端设备标识（发起的这一刻才有）。
    返回：{authorization_url, expires_in: 600}。
    异常：NotFoundError（提供方不存在/已停用、工作区不存在）；AuthError（工作区已归档）。
    副作用：写入一行 OidcLoginState（明文 state 不落库，只存 sha256；PKCE verifier 用
    AES-GCM 加密存储），并提交。redirect_uri 在发起时确定，回调必须原样使用，避免开放重定向；
    device_hash 也在此刻固化，回调阶段用它恢复设备身份（redirect 后前端标识不可用）。
    """
    row = db.scalar(select(OidcProvider).where(
        OidcProvider.id == provider_id, OidcProvider.enabled.is_(True),
    ))
    if row is None:
        raise NotFoundError("SSO 登录方式不存在或已停用")
    workspace = db.get(Workspace, row.workspace_id)
    if workspace is None:
        raise NotFoundError("SSO 工作区不存在")
    lifecycle = db.scalar(select(WorkspaceLifecycle).where(WorkspaceLifecycle.workspace_id == workspace.id))
    if lifecycle and lifecycle.status != "active":
        raise AuthError("SSO 工作区已归档")
    redirect_uri = str(request.base_url).rstrip("/") + "/sso/callback"
    state_value = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(28)
    cipher, cipher_nonce, version = encrypt_secret(verifier)
    from app.modules.system.security_service import client_ip, hash_device, user_agent
    db.add(OidcLoginState(
        id=new_id(), workspace_id=row.workspace_id, provider_id=row.id,
        state_digest=hashlib.sha256(state_value.encode()).hexdigest(), oidc_nonce=nonce,
        verifier_ciphertext=cipher, verifier_nonce=cipher_nonce, verifier_key_version=version,
        redirect_uri=redirect_uri, device_hash=hash_device(row.workspace_id, device_id),
        ip_address=client_ip(request), user_agent=user_agent(request),
        created_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    db.commit()
    query = urlencode({
        "response_type": "code", "client_id": row.client_id,
        "redirect_uri": redirect_uri, "scope": " ".join(row.scopes),
        "state": state_value, "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    })
    return {"authorization_url": row.authorization_endpoint + ("&" if "?" in row.authorization_endpoint else "?") + query,
            "expires_in": 600}


def _verify_signature(token: str, row: OidcProvider) -> tuple[dict, dict]:
    """验证 ID Token 签名并返回 (header, claims)。

    参数：token 三段式 JWT；row 提供方配置（用于取 jwks_uri 与 allow_private）。
    返回：header 与 payload 字典（此时尚未校验 iss/aud/exp/nonce，见 _validate_claims）。
    异常：AuthError —— 结构不是三段、无法解析、算法不受支持（只允许 RS256/PS256/ES256）、
    找不到 kid 匹配的公钥、签名验证失败。
    副作用：向 jwks_uri 发起一次实时 HTTP 请求（每次验签都拉取，没有本地缓存；不跟随重定向）。
    实现要点：RSA 用 RS256(PKCS1v15)/PS256(PSS, salt=32) 区分填充方式；ES256 要求 P-256 曲线且
    签名为 64 字节原生的 r||s（需要转成 DER 后才能交给 cryptography 校验）。
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("OIDC ID Token 格式无效")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except Exception as exc:
        raise AuthError("OIDC ID Token 无法解析") from exc
    alg = header.get("alg")
    if alg not in {"RS256", "PS256", "ES256"}:
        raise AuthError("OIDC 签名算法不受支持", algorithm=alg)
    jwks_url = _validate_url(row.jwks_uri, row.allow_private, name="JWKS 地址")
    response = httpx.get(jwks_url, timeout=8.0, follow_redirects=False, headers={"Accept": "application/json"})
    response.raise_for_status()
    keys = response.json().get("keys") or []
    key = next((item for item in keys if item.get("kid") == header.get("kid") and item.get("alg", alg) == alg), None)
    if key is None:
        raise AuthError("OIDC 找不到对应的签名密钥")
    signed = (parts[0] + "." + parts[1]).encode("ascii")
    try:
        if alg in {"RS256", "PS256"} and key.get("kty") == "RSA":
            public = rsa.RSAPublicNumbers(
                int.from_bytes(_b64url_decode(key["e"]), "big"),
                int.from_bytes(_b64url_decode(key["n"]), "big"),
            ).public_key()
            pad = padding.PKCS1v15() if alg == "RS256" else padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32)
            public.verify(signature, signed, pad, hashes.SHA256())
        elif alg == "ES256" and key.get("kty") == "EC" and key.get("crv") == "P-256":
            public = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_b64url_decode(key["x"]), "big"),
                int.from_bytes(_b64url_decode(key["y"]), "big"), ec.SECP256R1(),
            ).public_key()
            if len(signature) != 64:
                raise ValueError("invalid ES256 signature")
            der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
            public.verify(der, signed, ec.ECDSA(hashes.SHA256()))
        else:
            raise ValueError("key type does not match algorithm")
    except Exception as exc:
        raise AuthError("OIDC ID Token 签名校验失败") from exc
    return header, claims


def _validate_claims(claims: dict, row: OidcProvider, login_state: OidcLoginState,
                     access_token: str, code: str) -> None:
    """校验 ID Token 的业务声明（签名通过之后的第二道关，逐项失败即拒绝登录）。

    参数：claims 已验签的载荷；row 提供方配置；login_state 本次登录状态（携带发起时的 nonce）；
    access_token 与 code 仅用于可选的 at_hash/c_hash 绑定校验。
    返回：无（全部通过时静默返回）。
    异常：AuthError —— 下列任一失败都会带上可展示的中文原因：
    - iss 与配置 issuer 不一致（忽略结尾斜杠差异）；
    - aud 不包含本 client_id；或 aud 有多个值但 azp != client_id（防止令牌被其他客户端误用）；
    - exp 已过期（容忍 60 秒时钟偏移）；iat 晚于当前时间 120 秒（签发时间异常）；
    - nonce 与发起时记录的值不一致（用 compare_digest 常数时间比较，防重放）；
    - sub 为空；
    - 提供了 at_hash/c_hash 但与本次 access_token/code 不匹配（S256 前 16 字节 base64url）。
    副作用：无。约束：本函数不校验 email/域名（那属于身份映射阶段），也不校验 jwks 上的 kid 轮换。
    """
    now = int(datetime.utcnow().timestamp())
    if str(claims.get("iss", "")).rstrip("/") != row.issuer_url.rstrip("/"):
        raise AuthError("OIDC issuer 校验失败")
    audience = claims.get("aud")
    audiences = {audience} if isinstance(audience, str) else set(audience or [])
    if row.client_id not in audiences:
        raise AuthError("OIDC audience 校验失败")
    if len(audiences) > 1 and claims.get("azp") != row.client_id:
        raise AuthError("OIDC authorized party 校验失败")
    if int(claims.get("exp") or 0) < now - 60:
        raise AuthError("OIDC ID Token 已过期")
    if int(claims.get("iat") or 0) > now + 120:
        raise AuthError("OIDC ID Token 签发时间异常")
    if not secrets.compare_digest(str(claims.get("nonce", "")), login_state.oidc_nonce):
        raise AuthError("OIDC nonce 校验失败")
    if not str(claims.get("sub", "")).strip():
        raise AuthError("OIDC ID Token 缺少 subject")
    for claim_name, value in (("at_hash", access_token), ("c_hash", code)):
        expected = claims.get(claim_name)
        if expected:
            actual = _b64url(hashlib.sha256(value.encode()).digest()[:16])
            if not secrets.compare_digest(str(expected), actual):
                raise AuthError(f"OIDC {claim_name} 校验失败")


def _exchange(row: OidcProvider, state: OidcLoginState, code: str) -> dict:
    """用授权码 + PKCE verifier 调用令牌端点换取令牌。

    参数：row 提供方配置；state 登录状态（解密出 verifier、取发起时固定的 redirect_uri）；
    code 回调带来的授权码。
    返回：令牌响应字典（必须同时包含 id_token 与 access_token）。
    异常：AuthError —— 未配置 client secret（机密客户端）、令牌端点返回 >=400、响应缺少 id_token/access_token。
    副作用：一次出站 POST（超时 12 秒、不跟随重定向）；读取密钥模块时需要独立会话
    （new_session），因为密钥解密可能发生在与业务事务不同的连接上；本次不保存 refresh_token。
    认证方式：client_secret_basic 用 HTTP Basic；client_secret_post 放进表单；none 表示公共客户端，
    只带 client_id 并依赖 PKCE。
    """
    verifier = decrypt_secret(state.verifier_ciphertext, state.verifier_nonce, state.verifier_key_version)
    data = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": state.redirect_uri, "code_verifier": verifier}
    auth = None
    if row.token_auth_method == "none":
        data["client_id"] = row.client_id
    else:
        if not row.client_secret_id:
            raise AuthError("OIDC client secret 未配置")
        from app.modules.secret.service import decrypt as decrypt_vault_secret
        from app.db import new_session
        with new_session() as secret_db:
            secret_value = decrypt_vault_secret(secret_db, row.client_secret_id)
        if row.token_auth_method == "client_secret_basic":
            auth = (row.client_id, secret_value)
        else:
            data.update({"client_id": row.client_id, "client_secret": secret_value})
    token_url = _validate_url(row.token_endpoint, row.allow_private, name="令牌端点")
    response = httpx.post(token_url, data=data, auth=auth, timeout=12.0, follow_redirects=False,
                          headers={"Accept": "application/json"})
    if response.status_code >= 400:
        raise AuthError("OIDC 令牌交换失败", status=response.status_code)
    payload = response.json()
    if not payload.get("id_token") or not payload.get("access_token"):
        raise AuthError("OIDC 令牌响应缺少 ID Token 或 Access Token")
    return payload


def _identity_from_claims(db, row: OidcProvider, claims: dict) -> tuple[User, AuthIdentity, str]:
    """把已校验的 claims 映射为本地身份（第三步：身份映射与自动开通）。

    参数：db；row 提供方配置；claims 已通过签名与 iss/aud/exp/nonce 校验的载荷。
    返回：(user, identity, username)，username 用于登录事件与审计展示。
    异常：AuthError —— 邮箱未验证（email_verified 显式为 false）、邮箱域名不在 allowed_domains、
    已存在账号但被停用、账号未映射且未开启 auto_provision、账号与工作区无关系且未开启 auto_provision。
    副作用：
    - auto_provision 开启时新建 users/AuthIdentity（provider 键为 "oidc-<provider_id 前 12 位哈希>"，
      不写 LocalCredential，即 SSO 用户没有本地密码）；
    - 首次进入该工作区时补建 OrganizationMember、WorkspaceMember、MemberProfile、UserRole
      （角色取自 default_role_code，workspace_admin 会同步 member.role="admin"）。
    约束：身份以 (provider_key, sub) 唯一定位，因此同一 IdP 用户在不同工作区被视为同一账号；
    未实现的用户组/角色映射意味着角色只在首次开通时按 default_role_code 决定，后续不会随 IdP 变化。
    """
    mapping = {**DEFAULT_CLAIMS, **(row.claims or {})}
    subject = str(claims.get(mapping["subject"], "")).strip()
    email = str(claims.get(mapping["email"], "")).strip().lower()
    display_name = str(claims.get(mapping["name"], "") or email or subject).strip()[:200]
    username = str(claims.get(mapping["username"], "") or email or subject).strip()[:200]
    if email and claims.get("email_verified") is False:
        raise AuthError("企业身份提供方未验证该邮箱地址")
    if row.allowed_domains:
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain not in set(row.allowed_domains):
            raise AuthError("该企业账号域名未获准访问此工作区")
    provider_key = "oidc-" + hashlib.sha256(row.id.encode()).hexdigest()[:12]
    identity = db.scalar(select(AuthIdentity).where(
        AuthIdentity.provider == provider_key, AuthIdentity.username_normalized == subject,
    ))
    if identity:
        user = db.get(User, identity.user_id)
        if user is None or user.status != "active":
            raise AuthError("SSO 账号已停用")
    else:
        if not row.auto_provision:
            raise AuthError("此 SSO 账号尚未映射，请联系管理员")
        now = datetime.utcnow()
        user = User(id=new_id(), display_name=display_name, status="active", created_at=now, updated_at=now)
        identity = AuthIdentity(id=new_id(), user_id=user.id, provider=provider_key,
                                username_normalized=subject, created_at=now)
        db.add_all([user, identity])
        db.flush()
    membership = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == row.workspace_id, WorkspaceMember.user_id == user.id,
    ))
    if membership is None:
        if not row.auto_provision:
            raise AuthError("此 SSO 账号尚未加入工作区")
        workspace = db.get(Workspace, row.workspace_id)
        now = datetime.utcnow()
        if db.scalar(select(OrganizationMember.id).where(
            OrganizationMember.organization_id == workspace.organization_id,
            OrganizationMember.user_id == user.id,
        )) is None:
            db.add(OrganizationMember(id=new_id(), organization_id=workspace.organization_id,
                                      user_id=user.id, role="member", created_at=now))
        role = _validate_role(db, row.workspace_id, row.default_role_code)
        membership = WorkspaceMember(id=new_id(), workspace_id=row.workspace_id, user_id=user.id,
                                     role="admin" if role.code == "workspace_admin" else "member", created_at=now)
        db.add_all([
            membership,
            MemberProfile(id=new_id(), workspace_id=row.workspace_id, user_id=user.id,
                          department_id=None, position_ids=[], employee_code="", email=email,
                          phone="", roles_initialized=True, created_at=now, updated_at=now),
            UserRole(id=new_id(), workspace_id=row.workspace_id, user_id=user.id,
                     role_id=role.id, created_at=now, updated_at=now),
        ])
    return user, identity, username


def finish_authorization(db, *, state_value: str, code: str, request) -> dict:
    """SSO 登录第 2 步：回调处理（校验 state → 换令牌 → 验签与校验 claims → 建身份 → 发本地会话）。

    参数：db；state_value 回调带回的 state 明文（只用于哈希比对）；code 授权码；
    request 当前回调请求（用于构造登录证据）。
    返回：登录成功载荷（由 auth 模块 _build_login_payload 统一构造，含本地会话令牌）。
    异常：AuthError —— 状态无效/已使用/已过期、提供方已停用、令牌交换失败、签名或 claims 校验失败、
    登录策略拒绝（IP/设备/会话上限），外部异常统一包装成“OIDC 登录验证失败”。
    执行顺序与并发保护：
    1. 用 sha256(state) 命中记录并加行锁（with_for_update），立即标记 consumed_at，
       保证同一 state 只被消费一次（并发/重放均失败）；
    2. 校验提供方仍启用，交换令牌，验签，校验 claims；
    3. 身份映射/自动开通；
    4. evaluate_login 复用工作区登录策略（IP、设备、会话上限）。此处传入 device_hash_override
       与发起时记录的 IP/UA，避免用内网回调地址或服务端 UA 覆盖真实证据；
    5. 写入本地会话（凭据版本取本地密码凭据，SSO 用户无本地凭据时使用 1）、安全上下文与
       auth.oidc_login 审计，提交后返回登录载荷。
    副作用：更新 OidcLoginState（consumed_at）、可能新建用户/成员关系、撤销超限的旧会话、
    写入 SessionToken/SessionContext/LoginEvent/AuditLog 并提交。
    异常分支同样先 commit：确保“状态被消费”“失败事件”等证据不会因后续 500 被回滚。
    """
    digest = hashlib.sha256(state_value.encode()).hexdigest()
    state = db.scalar(select(OidcLoginState).where(OidcLoginState.state_digest == digest).with_for_update())
    if state is None or state.consumed_at is not None or state.expires_at < datetime.utcnow():
        raise AuthError("OIDC 登录状态无效、已使用或已过期")
    state.consumed_at = datetime.utcnow()
    row = db.scalar(select(OidcProvider).where(
        OidcProvider.id == state.provider_id, OidcProvider.workspace_id == state.workspace_id,
        OidcProvider.enabled.is_(True),
    ))
    if row is None:
        db.commit()
        raise AuthError("OIDC 登录方式已停用")
    try:
        tokens = _exchange(row, state, code)
        _, claims = _verify_signature(tokens["id_token"], row)
        _validate_claims(claims, row, state, tokens["access_token"], code)
        user, identity, username = _identity_from_claims(db, row, claims)
        workspace = db.get(Workspace, row.workspace_id)
        from app.modules.system.security_service import evaluate_login, record_success
        # The original socket/device evidence was captured before the external redirect.
        synthetic = type("OidcRequest", (), {"client": request.client, "headers": request.headers})()
        security_context = evaluate_login(
            db, workspace=workspace, user_id=user.id, username=username,
            auth_method="oidc", request=synthetic, device_id=None,
            device_hash_override=state.device_hash,
        )
        security_context["device_hash"] = state.device_hash
        security_context["ip_address"] = state.ip_address
        security_context["user_agent"] = state.user_agent
        credential = db.scalar(select(LocalCredential).join(
            AuthIdentity, AuthIdentity.id == LocalCredential.auth_identity_id,
        ).where(AuthIdentity.user_id == user.id, AuthIdentity.provider == "local"))
        raw_token, token_digest = generate_session_token()
        now = datetime.utcnow()
        from app.config import settings
        session = SessionToken(
            id=new_id(), user_id=user.id, token_digest=token_digest,
            credential_version=credential.credential_version if credential else 1,
            expires_at=now + timedelta(seconds=settings.SESSION_TTL_SECONDS),
            idle_expires_at=now + timedelta(seconds=settings.IDLE_TTL_SECONDS),
            last_seen_at=now,
        )
        db.add(session)
        db.flush()
        record_success(db, workspace_id=workspace.id, session_id=session.id, user_id=user.id,
                       username=username, auth_method="oidc", security_context=security_context)
        audit(db, workspace_id=workspace.id, actor_user_id=user.id,
              action="auth.oidc_login", resource_type="user", resource_id=user.id,
              metadata={"provider_id": row.id})
        db.commit()
        from app.modules.auth.service import _build_login_payload
        return _build_login_payload(db, user.id, raw_token, preferred_identity=identity,
                                    preferred_workspace_id=workspace.id, username_override=username)
    except AuthError:
        db.commit()
        raise
    except Exception as exc:
        db.commit()
        raise AuthError("OIDC 登录验证失败", error_type=type(exc).__name__) from exc
