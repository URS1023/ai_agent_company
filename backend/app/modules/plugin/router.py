# -*- coding: utf-8 -*-
"""Plugin HTTP 路由（``/api/plugins``）。

中文补充：业务能力包（Plugin = Skill + 工具 + 资源绑定）的管理接口：
创建/列表/详情/新增版本/发布/复制/试运行/共享/归档（DELETE 是归档别名）。
鉴权：全部端点要求登录上下文；每个改变状态的端点先 get_plugin 校验工作区归属，
写操作完成后统一写 audit 审计记录。发布与共享影响对外可见性，草稿不影响线上。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...common.audit import audit
from ...common.deps import AuthContext, get_current_context
from ...models import PluginVersion
from . import service as plugin_service

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginCreate(BaseModel):
    """新建插件请求：名称、描述与 spec（instructions/tool_refs/skill_refs/resource_bindings）。"""

    name: str
    description: str = ""
    spec: dict = Field(default_factory=dict)


class VersionPayload(BaseModel):
    """新增版本请求：新 spec（与旧 spec 浅合并，未提及字段保持原样）。"""

    spec: dict


class ClonePayload(BaseModel):
    """复制请求：副本名称，留空则用"原名 副本"；副本固定 private。"""

    new_name: str = ""


class SharePayload(BaseModel):
    """共享请求：目标可见范围（private / workspace）。"""

    visibility: Literal["private", "workspace"] = "workspace"


@router.post("", status_code=201)
def create(payload: PluginCreate, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins：创建插件草稿（201）并写 plugin.create 审计。

    工具/技能引用在服务层被解析为冻结版本；解析不到的引用会被丢弃（不报错）。
    """
    result = plugin_service.create_plugin(ctx.db, workspace_id=ctx.workspace_id,
                                          owner_user_id=ctx.user_id, name=payload.name,
                                          description=payload.description, spec=payload.spec)
    ctx.db.commit()
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="plugin.create", resource_type="plugin", resource_id=result["plugin_id"])
    ctx.db.commit()
    return result


@router.get("")
def list_all(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/plugins：列出本工作区未归档插件（精简字段，不含 spec 与绑定）。"""
    rows = plugin_service.list_plugins(ctx.db, ctx.workspace_id)
    return {"items": [{"id": p.id, "name": p.name, "description": p.description,
                       "status": p.status, "visibility": p.visibility,
                       "published_version_id": p.published_version_id} for p in rows],
            "count": len(rows)}


@router.get("/{plugin_id}")
def detail(plugin_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/plugins/{plugin_id}：插件详情 + 全部版本（倒序，含 spec）。"""
    plugin = plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    versions = ctx.db.execute(select(PluginVersion).where(PluginVersion.plugin_id == plugin_id)
                              .order_by(PluginVersion.version_number.desc())).scalars().all()
    return {"id": plugin.id, "name": plugin.name, "description": plugin.description,
            "status": plugin.status, "visibility": plugin.visibility,
            "versions": [{"version_id": v.id, "version_number": v.version_number,
                          "status": v.status, "spec": v.spec} for v in versions]}


@router.post("/{plugin_id}/versions", status_code=201)
def new_version(plugin_id: str, payload: VersionPayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins/{plugin_id}/versions：追加新草稿版本（201），状态退回 draft。"""
    plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    nv = plugin_service.new_draft_version(ctx.db, plugin_id, ctx.user_id, payload.spec, ctx.workspace_id)
    return {"version_id": nv.id, "version_number": nv.version_number, "status": "draft"}


@router.post("/{plugin_id}/publish")
def publish(plugin_id: str, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins/{plugin_id}/publish：发布最新版本并写 plugin.publish 审计。"""
    plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    lv = plugin_service.publish(ctx.db, plugin_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="plugin.publish", resource_type="plugin", resource_id=plugin_id)
    ctx.db.commit()
    return {"ok": True, "version_id": lv.id, "status": "published"}


@router.post("/{plugin_id}/clone", status_code=201)
def clone(plugin_id: str, payload: ClonePayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins/{plugin_id}/clone：复制为独立 private 草稿（201）并写审计。"""
    result = plugin_service.clone(ctx.db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                                  plugin_id=plugin_id, new_name=payload.new_name)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="plugin.clone", resource_type="plugin", resource_id=result["plugin_id"],
          metadata={"source_id": plugin_id})
    ctx.db.commit()
    return result


class TestRunPayload(BaseModel):
    """试运行请求：模拟用户输入，缺省给一句示例指令，方便直接点"测试"。"""

    input: str = "请按本插件方式处理：给我一份示例输出。"


@router.post("/{plugin_id}/test-run")
def test_run(plugin_id: str, payload: TestRunPayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins/{plugin_id}/test-run：真实跑一次 agent 并返回回复/工具调用/工件。

    副作用较重：会新建会话与 Run、调用模型、可能产生工件；服务层把执行异常转成
    {"status":"failed"} 返回而不是抛错，便于管理台展示失败原因。
    """
    plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    result = plugin_service.test_run(ctx.db, workspace_id=ctx.workspace_id,
                                     user_id=ctx.user_id, plugin_id=plugin_id,
                                     input_text=payload.input)
    ctx.db.commit()
    return result


@router.post("/{plugin_id}/share")
def share(plugin_id: str, payload: SharePayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins/{plugin_id}/share：修改可见范围并写 plugin.share 审计。"""
    plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    plugin_service.share(ctx.db, plugin_id, payload.visibility)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="plugin.share", resource_type="plugin", resource_id=plugin_id,
          metadata={"visibility": payload.visibility})
    ctx.db.commit()
    return {"ok": True, "visibility": payload.visibility}


@router.post("/{plugin_id}/archive")
def archive(plugin_id: str, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/plugins/{plugin_id}/archive：归档（软删除，保留版本与绑定历史）。"""
    plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    plugin_service.archive(ctx.db, plugin_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="plugin.archive", resource_type="plugin", resource_id=plugin_id,
          metadata={"retained_history": True})
    ctx.db.commit()
    return {"ok": True}


@router.delete("/{plugin_id}")
def delete(plugin_id: str, ctx: AuthContext = Depends(get_current_context)):
    """DELETE /api/plugins/{plugin_id}：归档的兼容别名，审计标记 legacy_delete_alias。"""
    plugin_service.get_plugin(ctx.db, ctx.workspace_id, plugin_id)
    plugin_service.archive(ctx.db, plugin_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="plugin.archive", resource_type="plugin", resource_id=plugin_id,
          metadata={"retained_history": True, "legacy_delete_alias": True})
    ctx.db.commit()
    return {"ok": True, "history_retained": True}
