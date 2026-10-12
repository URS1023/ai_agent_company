"""Contracts for login policy, trusted devices and OIDC administration.

职责：安全治理相关的请求/响应契约（登录策略、设备审批、OIDC 提供方），被
governance_router.py 用作 response_model，被 security_service / oidc_service 用作入参类型。
复用 schemas.InputModel（extra="forbid"），因此 OIDC 与策略更新都必须显式携带 version。

关键约定：
- 策略与设备更新均为部分更新：字段为 None 表示“不修改”，但不允许显式传 null（见各 service 的
  version 校验与 setattr 逻辑）。
- client_secret 标了 writeOnly：只进不出，任何响应都不会回显密钥（库里存的是密钥模块的引用 id）。
- token_auth_method 的三个取值决定令牌交换方式，改动后需要重新 discover/联调。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.system.schemas import InputModel


class SecurityPolicyUpdate(InputModel):
    """登录策略部分更新：version 必填；cidrs 传空列表表示清空该名单；max_failed_attempts/lock_minutes
    为预留字段（当前登录链路只消费 IP、可信设备与会话上限）。"""
    version: int = Field(ge=1)
    enabled: bool | None = None
    allowed_cidrs: list[str] | None = Field(default=None, max_length=100)
    blocked_cidrs: list[str] | None = Field(default=None, max_length=100)
    require_trusted_device: bool | None = None
    max_failed_attempts: int | None = Field(default=None, ge=2, le=20)
    lock_minutes: int | None = Field(default=None, ge=1, le=1440)
    max_active_sessions: int | None = Field(default=None, ge=1, le=100)
    session_overflow: Literal["revoke_oldest", "block"] | None = None


class DeviceDecision(InputModel):
    """设备审批决定：status 只允许 trusted（批准并清理撤销时间）或 revoked（撤销）；
    label 为可选的重命名，version 必填防止覆盖其他管理员刚刚做出的审批。"""
    version: int = Field(ge=1)
    status: Literal["trusted", "revoked"]
    label: str | None = Field(default=None, max_length=160)


class OidcProviderCreate(InputModel):
    """新建 OIDC 提供方：issuer/authorization/token/jwks 四个地址由 oidc_service._validate_url
    校验（HTTPS、无凭据、无片段、目标解析符合出网策略）；enabled=True 时要求 default_role_code
    对应角色存在且启用，且机密客户端必须提供 client_secret（公共客户端用 token_auth_method="none"）。"""
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    issuer_url: str = Field(min_length=8, max_length=1000)
    authorization_endpoint: str = Field(min_length=8, max_length=1000)
    token_endpoint: str = Field(min_length=8, max_length=1000)
    jwks_uri: str = Field(min_length=8, max_length=1000)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(default="", max_length=1000, repr=False, json_schema_extra={"writeOnly": True})
    token_auth_method: Literal["client_secret_basic", "client_secret_post", "none"] = "client_secret_basic"
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"], min_length=1, max_length=20)
    claims: dict[str, str] = Field(default_factory=dict)
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    auto_provision: bool = False
    default_role_code: str = Field(default="member", min_length=1, max_length=80)
    allow_private: bool = False
    enabled: bool = False


class OidcProviderUpdate(InputModel):
    """修改 OIDC 提供方：version 必填，code 不可修改（sso 回调与身份标识依赖它）；
    client_secret 传非空值表示轮换密钥（旧密钥立即标记 revoked），留空表示保持不变。"""
    version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    issuer_url: str | None = Field(default=None, min_length=8, max_length=1000)
    authorization_endpoint: str | None = Field(default=None, min_length=8, max_length=1000)
    token_endpoint: str | None = Field(default=None, min_length=8, max_length=1000)
    jwks_uri: str | None = Field(default=None, min_length=8, max_length=1000)
    client_id: str | None = Field(default=None, min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, max_length=1000, repr=False, json_schema_extra={"writeOnly": True})
    token_auth_method: Literal["client_secret_basic", "client_secret_post", "none"] | None = None
    scopes: list[str] | None = Field(default=None, min_length=1, max_length=20)
    claims: dict[str, str] | None = None
    allowed_domains: list[str] | None = Field(default=None, max_length=100)
    auto_provision: bool | None = None
    default_role_code: str | None = Field(default=None, min_length=1, max_length=80)
    allow_private: bool | None = None
    enabled: bool | None = None


class SecurityPolicyOut(InputModel):
    """登录策略输出：response_model 只用于文档展示，实际响应由 security_service.policy_out 生成，
    本类字段必须与之保持一致（新增字段时两处同步）。"""
    id: str
    version: int
    enabled: bool
    allowed_cidrs: list[str]
    blocked_cidrs: list[str]
    require_trusted_device: bool
    max_failed_attempts: int
    lock_minutes: int
    max_active_sessions: int
    session_overflow: str
    updated_at: datetime
