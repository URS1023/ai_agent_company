# -*- coding: utf-8 -*-
"""认证模块的请求/响应模型（Pydantic）。

与 ``auth.router`` 一一对应：LoginRequest / SsoAuthorizeRequest 为入参，
LoginResponse（含 UserOut、WorkspaceOut）为出参，PasswordChangeRequest 用于改密。

约束：
- 所有含口令或设备标识的字段都标了 ``repr=False``，避免它们出现在日志/异常回显中，
  新增敏感字段时必须沿用该约定。
- 字段长度上限即接口契约的一部分（如 device_id 至少 16 位），前端校验需与之一致。
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """本地登录入参。

    ``workspace_id`` 仅在多工作区账号需要指定落点时传；``device_id`` 由浏览器生成，
    服务端只保存工作区隔离后的哈希，不回显原文。
    """

    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    workspace_id: str | None = Field(default=None, max_length=26, description="多工作区账号可指定本次登录工作区")
    device_id: str | None = Field(default=None, min_length=16, max_length=200, description="浏览器生成的随机设备标识；服务端只保存工作区隔离哈希", repr=False)


class SsoAuthorizeRequest(BaseModel):
    """发起 OIDC 授权请求的入参；device_id 不写入授权 URL，仅随服务端状态保存。"""

    device_id: str | None = Field(default=None, min_length=16, max_length=200, description="浏览器随机设备标识；不写入 URL", repr=False)


class UserOut(BaseModel):
    """登录响应中的用户信息；不含口令哈希、令牌等敏感字段。

    ``must_change_password`` 为真时前端必须强制跳转改密页。
    """

    id: str
    display_name: str
    username: str
    status: str
    must_change_password: bool = False


class WorkspaceOut(BaseModel):
    """登录响应中的工作区条目，``role`` 是该用户在该工作区内的角色码。"""

    id: str
    organization_id: str
    name: str
    role: str


class LoginResponse(BaseModel):
    """登录/SSO 回调成功后的统一响应；``token`` 为明文会话令牌，仅在本次响应中出现一次。"""

    token: str
    user: UserOut
    workspaces: List[WorkspaceOut]
    expire_at: str


class LoginResult:
    """占位类型，当前未被引用；保留以避免破坏历史导入。"""

    pass


class PasswordChangeRequest(BaseModel):
    """Self-service password change; plaintext is never returned or audited.

    自助改密入参：明文口令只用于本次校验，既不回显也不写入审计日志。
    ``new_password`` 的长度下限与 ``auth.service.change_password`` 的强度校验保持一致。
    """

    current_password: str = Field(min_length=1, max_length=256, description="当前密码", repr=False)
    new_password: str = Field(min_length=10, max_length=256, description="新密码，至少 10 位且包含字母数字", repr=False)
