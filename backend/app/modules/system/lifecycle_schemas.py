"""Contracts for organization and workspace lifecycle administration.

职责：组织/工作区生命周期与成员调动的请求/响应契约，供 governance_router.py 用作
response_model、lifecycle_service 用作入参类型（复用 schemas.InputModel，extra="forbid"）。

关键约定：
- 所有 Update 契约都必填 version（乐观锁，对应元数据表 version 列）；
- status 只允许 active/archived，归档是唯一可用的下线方式（不提供删除）；
- 编码规则由 lifecycle_service.CODE_RE 再次校验，本文件的 pattern 只做第一道格式拦截。

说明：这些字段目前没有逐项 description，前端文案以 governance_router 的 summary/OpenAPI 为准。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.system.schemas import InputModel


class OrganizationCreate(InputModel):
    """创建组织：必须同时给出初始工作区编码与名称 —— 组织下至少要有一个工作区，否则无处登录。"""
    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    initial_workspace_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]+$")
    initial_workspace_name: str = Field(min_length=1, max_length=200)


class OrganizationUpdate(InputModel):
    """修改组织：version 必填；status=archived 要求该组织下已无启用工作区；code 不可修改。"""
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "archived"] | None = None


class WorkspaceCreate(InputModel):
    """在当前组织内新建工作区：code 在组织内唯一；创建者自动成为该工作区管理员。"""
    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)


class WorkspaceUpdate(InputModel):
    """修改工作区：version 必填；不能归档当前工作区，也不能让组织失去全部启用工作区。"""
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "archived"] | None = None


class MemberTransfer(InputModel):
    """成员跨工作区调动：mode=copy 保留源关系，mode=move 额外解除源关系（会校验最后一个管理员
    与部门负责人引用）；target_role_code 必须指向目标工作区中启用中的角色。"""
    user_id: str = Field(min_length=1, max_length=26)
    target_workspace_id: str = Field(min_length=1, max_length=26)
    mode: Literal["copy", "move"] = "copy"
    target_role_code: str = Field(default="member", min_length=1, max_length=80)


class OrganizationLifecycleOut(InputModel):
    """组织生命周期输出：current 表示是否为操作人当前所在组织（前端据此禁用归档操作）。"""
    id: str
    code: str
    name: str
    description: str
    status: str
    version: int
    workspace_count: int
    active_workspace_count: int
    current: bool
    created_at: datetime
    updated_at: datetime


class WorkspaceLifecycleOut(InputModel):
    """工作区生命周期输出：actor_role 为操作人在该工作区的角色（非成员为空），
    current 表示当前工作区（不能归档自身）。"""
    id: str
    organization_id: str
    code: str
    name: str
    description: str
    status: str
    version: int
    member_count: int
    current: bool
    actor_role: str | None = None
    created_at: datetime
    updated_at: datetime


class TransferResult(InputModel):
    """调动结果：message 为可直接展示的中文结果说明（区分“已复制”与“已调动”）。"""
    ok: bool = True
    user_id: str
    source_workspace_id: str
    target_workspace_id: str
    mode: Literal["copy", "move"]
    message: str
