# -*- coding: utf-8 -*-
"""Legacy /api/datasources compatibility router.

兼容旧版管理接口：只做请求体适配，真正的创建/列表/测试/删除都转发到
app.modules.analytics.sources，权限、数据范围、只读连接与脱敏错误都由那套服务保证
（本模块不额外放宽权限，也不返回凭据）。它与 /api/v1/analytics 下的正式接口并存，
新前端应使用正式接口。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...common.audit import audit
from ...common.deps import AuthContext, get_current_context
from ..analytics import sources
from ..analytics.schemas import SourceInput
from . import service as ds_service

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


class DSCreate(BaseModel):
    """旧版创建数据源入参：provider 只接受 mysql/postgres（其余类型走新版接口）。"""

    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="mysql", pattern="^(mysql|postgres)$")
    connection_config: dict = Field(default_factory=dict)
    password: str | None = None


@router.post("", status_code=201)
def create_ds(payload: DSCreate, ctx: AuthContext = Depends(get_current_context)):
    """兼容入口：POST /api/datasources，转发到 analytics.create_source。

    权限与校验完全由 sources.create_source 决定（datasources.create）；密码加密保存，
    只返回 id/name/provider，不回传任何凭据。副作用：建库记录并可能触发一次元数据同步。
    """
    config = payload.connection_config
    row = sources.create_source(ctx, SourceInput(
        name=payload.name, type=payload.provider, host=config.get("host") or "",
        port=config.get("port"), username=config.get("username") or config.get("user") or "",
        databaseName=config.get("databaseName") or config.get("database") or "",
        password=payload.password or config.get("password"),
        description=config.get("description") or "",
    ))
    return {"id": row.id, "name": row.name, "provider": row.type}


@router.get("")
def list_ds(ctx: AuthContext = Depends(get_current_context)):
    """兼容入口：GET /api/datasources，返回精简列表（id/name/provider/status）。

    权限与数据范围沿用 sources.list_sources；不回传主机、账号或凭据信息。
    """
    rows = sources.list_sources(ctx)
    return {"items": [{"id": d.id, "name": d.name, "provider": d.type,
                       "status": "active" if d.enabled else "disabled"} for d in rows], "count": len(rows)}


@router.post("/{ds_id}/test")
def test_ds(ds_id: str, ctx: AuthContext = Depends(get_current_context)):
    """兼容入口：POST /api/datasources/{ds_id}/test，执行真实只读连接测试。

    需要 datasources.execute 与数据源数据范围；失败时返回脱敏错误，不回传凭据。
    """
    return sources.test_source(ctx, source_id=ds_id)


@router.delete("/{ds_id}")
def delete_ds(ds_id: str, ctx: AuthContext = Depends(get_current_context)):
    """兼容入口：DELETE /api/datasources/{ds_id}，软删除数据源并保留查询追溯。

    需要 datasources.delete；不删除用户外部数据库、受管数据文件与历史结果。
    """
    sources.remove_source(ctx, ds_id)
    return {"ok": True}
