# -*- coding: utf-8 -*-
"""Capability Builder HTTP 路由（``/api/capability``，中文标签"capability"）。

中文补充：这是"自然语言构建企业 AI 能力"的旧版接口集合，与
modules/capabilities 的管理台接口并存：
- POST /api/capability/analyze：意图分类（判断要建哪种能力对象）。
- POST /api/capability/from-run：从一次运行沉淀 Skill/Plugin 草稿（201）。
- POST /api/capability/compile-arguments：把自然语言编译为工具参数（不执行）。
- POST /api/capability/modify：按自然语言修改已有 Skill/Plugin，产出新草稿版本（201）。

鉴权：所有端点都要求登录上下文（get_current_context）；modify 走
service.modify_authorized，会对目标资源做 skills.edit / plugins.edit 资源级校验。
生成结果一律是草稿，不会自动发布。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...common.deps import AuthContext, get_current_context
from . import service as capability_service

router = APIRouter(prefix="/api/capability", tags=["capability"])


class AnalyzeRequest(BaseModel):
    """意图分类请求：待判断的自然语言文本。"""

    text: str


class FromRunRequest(BaseModel):
    """能力沉淀请求：来源运行 ID 与目标类型（skill | plugin）。"""

    run_id: str
    want: str = "skill"  # skill | plugin


class ModifyRequest(BaseModel):
    """能力修改请求：目标资源类型/ID 与自然语言修改要求。"""

    resource_type: str
    resource_id: str
    instruction: str


class CompileRequest(BaseModel):
    """工具参数编译请求：工具名、业务意图与调用方提供的引用数据。"""

    tool_name: str
    intent: str
    references: dict = Field(default_factory=dict)


@router.post("/analyze")
def analyze(payload: AnalyzeRequest, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/capability/analyze：判断文本想创建/修改哪类能力对象。

    返回 {"object","reason"}；分类失败由 planner 回落到 other，不会 500。
    """
    return capability_service.analyze(ctx.db, ctx.workspace_id, payload.text)


@router.post("/from-run", status_code=201)
def from_run(payload: FromRunRequest, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/capability/from-run：从运行记录生成 Skill 或 Plugin 草稿（201）。

    模型产出的内容只作为草稿落库；工具引用会被白名单过滤。需要读取该运行的权限
    （资源级校验在 capabilities.service.generate_from_run 与 capability 服务内）。
    """
    return capability_service.generate_from_run(ctx.db, workspace_id=ctx.workspace_id,
                                                run_id=payload.run_id, want=payload.want,
                                                owner_user_id=ctx.user_id)


@router.post("/compile-arguments")
def compile_arguments(payload: CompileRequest, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/capability/compile-arguments：编译工具参数（不执行工具）。

    只做"意图 → 参数"的确定性产物，找不到工具时原样抛 NotFoundError 以便调用方
    区分"工具不存在"与"参数不合格"。
    """
    from ...errors import NotFoundError
    try:
        result = capability_service.compile_tool_arguments(ctx.db, ctx.workspace_id,
                                                           payload.tool_name, payload.intent,
                                                           payload.references)
    except NotFoundError as nf:
        raise nf
    return result


@router.post("/modify", status_code=201)
def modify(payload: ModifyRequest, ctx: AuthContext = Depends(get_current_context)):
    """POST /api/capability/modify：按自然语言修改 Skill/Plugin，生成新草稿版本（201）。

    必须走 modify_authorized：仅有 capabilities.create 不足以修改他人资源，
    还需 skills.edit / plugins.edit 的资源级权限。
    """
    return capability_service.modify_authorized(
        ctx, resource_type=payload.resource_type, resource_id=payload.resource_id,
        instruction=payload.instruction,
    )
