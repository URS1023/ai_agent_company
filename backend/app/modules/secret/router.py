# -*- coding: utf-8 -*-
"""密钥管理路由（HTTP 前缀 ``/api/secrets``）。

职责：列出当前工作区的密钥元信息、加密创建密钥、吊销（软删除）密钥。明文只在创建时
一次性交给 ``secret.service`` 加密落库，此后任何接口都不会回显明文或密文。

依赖：``common.deps`` 提供登录上下文与数据范围绑定，``common.authorization`` 提供权限校验，
``secret.service`` 负责 AES-GCM 加解密与状态流转。

约束：
- 读 / 建 / 删分别要求 ``secrets.view`` / ``secrets.create`` / ``secrets.delete``。
- 所有查询与写入都限定在 ``ctx.workspace_id`` 内；密钥可用性由 ``status == "active"`` 且
  ``deleted_at is None`` 共同决定，两个条件缺一不可。
- 删除是不可恢复的软删除，因此路由层必须先做存在性校验再调用 service。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...common.audit import audit
from ...common.deps import AuthContext, get_current_context
from ...common.authorization import require_permission
from ...errors import NotFoundError
from ...ids import new_id
from ...models import Secret
from sqlalchemy import select
from . import service

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


class SecretCreate(BaseModel):
    """创建密钥请求体。

    ``value`` 是一次性明文输入，仅在服务端内存中加密后落库，任何响应都不回显；
    ``provider`` 为用途标签（如 api_key / db_password / oauth），默认 ``generic``。
    """

    name: str
    provider: str = "generic"
    value: str


class SecretOut(BaseModel):
    """密钥元信息出参：只有 ID、名称、用途标签与状态，绝不含明文或密文。"""

    id: str
    name: str
    provider: str
    status: str


@router.get("")
def list_secrets(ctx: AuthContext = Depends(get_current_context)):
    """列出当前工作区未删除的密钥元信息。

    HTTP：``GET /api/secrets``。权限：``secrets.view``。

    返回：``{"items": [{"id", "name", "provider", "status"}], "count": n}``，按
    ``created_at`` 倒序。过滤条件只有"本工作区 + 未软删除"，不按归属人收窄可见范围，
    因此可见性完全由 ``secrets.view`` 的授权范围决定；也不返回 ciphertext / nonce /
    key_version 等敏感列。

    副作用：无（只读，不写审计）。
    """
    require_permission(ctx, "secrets.view")
    rows = ctx.db.execute(
        select(Secret).where(Secret.workspace_id == ctx.workspace_id, Secret.deleted_at.is_(None))
        .order_by(Secret.created_at.desc())
    ).scalars().all()
    return {"items": [{"id": s.id, "name": s.name, "provider": s.provider, "status": s.status} for s in rows],
            "count": len(rows)}


@router.post("", status_code=201)
def create_secret(payload: SecretCreate, ctx: AuthContext = Depends(get_current_context)):
    """加密创建一个新密钥。

    HTTP：``POST /api/secrets``（201）。权限：``secrets.create``。

    参数：
        payload: 见 ``SecretCreate``；``value`` 明文只在此处短暂存在，不落库、不入日志。

    返回：``{"id", "name", "provider"}``，便于调用方立即拿到引用 ID（不含明文）。
    副作用：写 ``secrets`` 表，``owner_user_id`` 记为当前用户，初始状态 ``active``；
    数据提交后追加 ``secret.create`` 审计记录并二次提交（两步非同一事务）。
    """
    require_permission(ctx, "secrets.create")
    row = service.create_secret(ctx.db, workspace_id=ctx.workspace_id,
                                owner_user_id=ctx.user_id, name=payload.name,
                                value=payload.value, provider=payload.provider)
    ctx.db.commit()
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="secret.create", resource_type="secret", resource_id=row.id)
    ctx.db.commit()
    return {"id": row.id, "name": row.name, "provider": row.provider}


@router.delete("/{secret_id}")
def revoke_secret(secret_id: str, ctx: AuthContext = Depends(get_current_context)):
    """吊销（软删除）指定密钥。

    HTTP：``DELETE /api/secrets/{secret_id}``。权限：``secrets.delete``。

    参数：
        secret_id: 密钥 ID。不存在、已被软删除或不属于当前工作区时统一抛 ``NotFoundError``
            （故意不区分，避免通过错误信息探测其他工作区的资源）。

    返回：``{"ok": True}``。
    副作用：``service.revoke_secret`` 将状态置为 ``revoked`` 并写 ``deleted_at`` 后自行提交，
    随后追加 ``secret.revoke`` 审计记录再次提交；操作不可恢复，引用该密钥的运行配置
    （capabilities、数据源等）会随之失效。
    """
    require_permission(ctx, "secrets.delete")
    row = ctx.db.get(Secret, secret_id)
    if not row or row.workspace_id != ctx.workspace_id or row.deleted_at is not None:
        raise NotFoundError("密钥不存在")
    service.revoke_secret(ctx.db, secret_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="secret.revoke", resource_type="secret", resource_id=secret_id)
    ctx.db.commit()
    return {"ok": True}
