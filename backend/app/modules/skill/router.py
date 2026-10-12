# -*- coding: utf-8 -*-
"""Skill HTTP 路由（``/api/skills``）。

中文补充：技能（"这类任务该怎么做"）的增删改查与生命周期接口：
创建/列表/详情/新增版本/发布/试运行/复制/共享/归档（DELETE 是归档别名）。
鉴权：全部端点要求登录上下文；每个改变状态的端点都会先用 get_skill 校验
工作区归属（跨工作区返回 404），再调用 skill 服务，最后写 audit 审计记录。
发布与共享属于"对外可见性"变更，草稿则不影响任何线上行为。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from typing import Literal

from pydantic import BaseModel, Field

from ...common.audit import audit
from ...common.deps import AuthContext, get_current_context
from ...models import SkillVersion
from sqlalchemy import select
from . import service as skill_service

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillCreate(BaseModel):
    """新建技能请求：名称、描述与 spec（instructions/rules/推荐工具等）。"""

    name: str
    description: str = ""
    spec: dict = Field(default_factory=dict)


class VersionPayload(BaseModel):
    """新增版本请求：完整的新 spec（全量覆盖，不做字段合并）。"""

    spec: dict


class TestPayload(BaseModel):
    """试运行请求：模拟的用户输入文本。"""

    input: str


class ClonePayload(BaseModel):
    """复制请求：副本名称，留空则用"原名 副本"，副本固定为 private。"""

    new_name: str = Field(default="", max_length=200)


class SharePayload(BaseModel):
    """共享请求：目标可见范围，只支持 private / workspace。"""

    visibility: Literal["private", "workspace"] = "workspace"


@router.post("", status_code=201)
def create(payload: SkillCreate, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills：创建技能草稿（201），并写 skill.create 审计。

    创建后仍需发布才会被 AI 使用；此处不校验额外权限码，沿用登录 + 工作区隔离。
    """
    result = skill_service.create_skill(ctx.db, workspace_id=ctx.workspace_id,
                                        owner_user_id=ctx.user_id, name=payload.name,
                                        description=payload.description, spec=payload.spec)
    ctx.db.commit()
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="skill.create", resource_type="skill", resource_id=result["skill_id"])
    ctx.db.commit()
    return result


@router.get("")
def list_all(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/skills：列出本工作区未归档技能（精简字段，不含 spec）。"""
    rows = skill_service.list_skills(ctx.db, ctx.workspace_id)
    return {"items": [{"id": s.id, "name": s.name, "description": s.description,
                       "status": s.status} for s in rows], "count": len(rows)}


@router.get("/business-inventory")
def business_inventory(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/skills/business-inventory：业务技能清单与发布版本（只读，需 skills.view）。

    P2.5 落地：返回受控目录业务技能当前版本哈希、已发布 manifest 版本与漂移名单；
    本接口不读取技能正文、不改变执行路径，供管理页做版本/发布核对。
    注意：静态路由须在 ``/{skill_id}`` 之前注册，否则会被动态段吞掉。
    """
    from ...common.authorization import require_permission
    from . import inventory
    require_permission(ctx, "skills.view")
    items = inventory.collect_inventory()
    manifest = inventory.read_manifest()
    drift_info = inventory.drift(items)
    return {
        "skills": [{k: v for k, v in item.items() if k != "resources"}
                   for item in items],
        "manifest": {"version": manifest.get("version"), "syncedAt": manifest.get("syncedAt"),
                     "published": sorted(manifest.get("skills") or {})},
        "drift": drift_info,
    }


@router.get("/{skill_id}")
def detail(skill_id: str, ctx: AuthContext = Depends(get_current_context)):
    """GET /api/skills/{skill_id}：技能详情 + 全部版本（按版本号倒序，含 spec）。"""
    skill = skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    versions = ctx.db.execute(select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                              .order_by(SkillVersion.version_number.desc())).scalars().all()
    return {"id": skill.id, "name": skill.name, "description": skill.description,
            "status": skill.status, "visibility": skill.visibility,
            "published_version_id": skill.published_version_id,
            "versions": [{"version_id": v.id, "version_number": v.version_number,
                          "status": v.status, "spec": v.spec} for v in versions]}


@router.post("/{skill_id}/versions", status_code=201)
def new_version(skill_id: str, payload: VersionPayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills/{skill_id}/versions：追加新草稿版本（201）。

    先校验技能归属再写入；新版本会把技能状态退回 draft，需重新发布才生效。
    """
    skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    nv = skill_service.new_draft_version(ctx.db, skill_id, ctx.user_id, payload.spec)
    return {"version_id": nv.id, "version_number": nv.version_number, "status": "draft"}


@router.post("/{skill_id}/publish")
def publish(skill_id: str, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills/{skill_id}/publish：发布最新版本并写 skill.publish 审计。

    发布后 published_version_id 指向该版本；历史版本不受影响。
    """
    skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    lv = skill_service.publish(ctx.db, skill_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="skill.publish", resource_type="skill", resource_id=skill_id)
    ctx.db.commit()
    return {"ok": True, "version_id": lv.id, "status": "published"}


@router.post("/{skill_id}/test-run")
def test_run(skill_id: str, payload: TestPayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills/{skill_id}/test-run：用最新 spec 跑一次模型调用并留存测试记录。

    副作用：真实调用模型（有计费）并写 TestRun；不改变技能的发布状态。
    """
    skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    tr = skill_service.test_run(ctx.db, workspace_id=ctx.workspace_id, skill_id=skill_id,
                                created_by=ctx.user_id, input_text=payload.input)
    ctx.db.commit()
    return {"test_run_id": tr.id, "status": tr.status, "output": tr.output_json}


@router.post("/{skill_id}/clone", status_code=201)
def clone(skill_id: str, payload: ClonePayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills/{skill_id}/clone：复制为独立草稿（201）并写 skill.clone 审计。

    副本可见性固定 private，需要重新发布后才会被 AI 使用。
    """
    result = skill_service.clone(
        ctx.db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
        skill_id=skill_id, new_name=payload.new_name,
    )
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="skill.clone", resource_type="skill", resource_id=result["skill_id"],
          metadata={"source_id": skill_id})
    ctx.db.commit()
    return result


@router.post("/{skill_id}/share")
def share(skill_id: str, payload: SharePayload, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills/{skill_id}/share：修改可见范围（private / workspace）并写审计。"""
    skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    skill_service.share(ctx.db, skill_id, payload.visibility)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="skill.share", resource_type="skill", resource_id=skill_id,
          metadata={"visibility": payload.visibility})
    ctx.db.commit()
    return {"ok": True, "visibility": payload.visibility}


@router.post("/{skill_id}/archive")
def archive(skill_id: str, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/skills/{skill_id}/archive：归档（软删除，保留历史版本与测试记录）。"""
    skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    skill_service.archive(ctx.db, skill_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="skill.archive", resource_type="skill", resource_id=skill_id,
          metadata={"retained_history": True})
    ctx.db.commit()
    return {"ok": True}


@router.delete("/{skill_id}")
def delete(skill_id: str, ctx: AuthContext = Depends(get_current_context)):
    """DELETE /api/skills/{skill_id}：归档的兼容别名。

    行为与 archive 完全一致，只在审计里标记 legacy_delete_alias，便于后续统计
    还有谁在用旧接口；响应显式返回 history_retained 说明未物理删除。
    """
    skill_service.get_skill(ctx.db, ctx.workspace_id, skill_id)
    skill_service.archive(ctx.db, skill_id)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="skill.archive", resource_type="skill", resource_id=skill_id,
          metadata={"retained_history": True, "legacy_delete_alias": True})
    ctx.db.commit()
    return {"ok": True, "history_retained": True}
