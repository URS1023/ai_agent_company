# -*- coding: utf-8 -*-
"""模型与用量治理的 HTTP 入口（路由前缀 ``/api/models``，tags=["模型与用量治理"]）。

职责：把"模型角色配置、连通性探测、月度预算、用量与费用"暴露给管理端页面
（前端消费方为 ``apps/web/assets/platform/model-governance.js``），业务实现全部在 ``service.py``，
本文件只做参数校验（Pydantic 模型）、权限判定、事务提交与审计。

依赖：``common.deps.get_current_context`` 提供登录态、工作区与 db 会话；``common.authorization``
给出 ``models.*`` 权限判定；``common.audit.audit`` 记录配置类写操作；``models.ModelProfile``
用于按 id 定位数据库配置行；密钥本身由 ``service`` 交给 ``modules.secret`` 保存（本层不可见明文）。

权限约定：每个端点显式声明 ``models.view/create/edit/delete/execute/export`` 动作。
动态模型名称不能改变动作；POST 同名覆盖还要在工作区锁内校验 edit 权限及其资源范围。
``models.*`` 属于平台管理域，内置"业务成员"角色不含这些权限，必须由管理员显式授予。

约束：
- 所有端点均无 ``response_model``，返回裸 dict（前端已按此契约消费），改字段名等于改对外 API；
- 配置类端点按 ``ctx.workspace_id`` 隔离，跨工作区访问一律 404，不泄漏"是否存在"；
- 探测类端点会真实出网（见 ``service.probe_effective_profile``），无 SSRF 目标校验，属已知边界。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...common.audit import audit
from ...common.deps import AuthContext, get_current_context
from ...common.authorization import require_permission
from ...errors import NotFoundError
from ...models import ModelProfile
from . import service
from .runtime_options import ModelRuntimeOptions, public_runtime_options

router = APIRouter(prefix="/api/models", tags=["模型与用量治理"], responses={
    401: {"description": "未登录或会话失效"},
    403: {"description": "没有模型查看、修改、测试或导出权限"},
    404: {"description": "模型配置不存在或不属于当前工作区"},
    409: {"description": "预算或配置版本冲突"},
    422: {"description": "端点、价格、预算或能力参数不符合约束"},
})


class ProfilePricing(BaseModel):
    """模型计价的请求体片段：按"每百万 token"的单价填写。

    两档单价均为可选（``None`` 表示未配置价格，不是 0 元）；只填输入或只填输出也合法，
    缺的那一档在计费时按 0 参与计算。``currency`` 只允许 3 位币种代码，真正校验在
    ``service.normalize_pricing``（不在白名单内会 422/业务异常）。
    """

    input_price_per_million: float | None = Field(default=None, ge=0, le=1_000_000)
    output_price_per_million: float | None = Field(default=None, ge=0, le=1_000_000)
    currency: str = Field(default="CNY", min_length=3, max_length=3)


class ProfileCreate(BaseModel):
    """新增模型角色（profile）的请求体。

    ``api_key`` 只在请求体里出现一次，service 会立刻交给 ``modules.secret`` 加密保存并只留引用，
    因此接口从不回显密钥（响应只有 ``api_key_configured``）。``profile_name`` 必须匹配
    ``service.PROFILE_NAME_RE``（字母开头），端点的 422 只覆盖长度约束。
    """

    profile_name: str = Field(min_length=2, max_length=100)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    runtime_options: ModelRuntimeOptions | None = None
    pricing: ProfilePricing | None = None
    enabled: bool = True


class ProfileUpdate(BaseModel):
    """修改模型角色的请求体：字段与 ``ProfileCreate`` 一致（不含 ``profile_name``，角色名取自路径）。

    注意：``pricing=None`` 会被 service 视为"未计价"，即保存时清空已配置单价；
    因此旧管理页（payload 不带 pricing）保存会静默清掉单价，改前端前请先看该页的提交字段。
    """

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    # 省略或 null 保留既有运行选项；传空对象明确恢复默认。
    runtime_options: ModelRuntimeOptions | None = None
    pricing: ProfilePricing | None = None
    enabled: bool = True


class CostPolicyUpdate(BaseModel):
    """月度预算策略的请求体（乐观锁：必须带上读到的 ``version``）。

    ``monthly_budget=None`` 表示"只统计、不设额度"，此时无论 ``hard_limit`` 勾选与否都不会拦截调用；
    ``hard_limit=True`` 的拦截效果还依赖"调用已被计价"，未配置单价时不会触发（见
    ``service.enforce_budget``）。版本不一致时 service 抛 409，界面需要重新拉取后再保存。
    """

    version: int = Field(ge=0, description="当前预算策略版本；首次保存传 0")
    monthly_budget: float | None = Field(default=None, ge=0, le=1_000_000_000, description="空值表示只统计、不设置额度")
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    warn_percent: int = Field(default=80, ge=1, le=100)
    hard_limit: bool = Field(default=False, description="达到预算后拒绝新的模型调用")


def _admin(ctx: AuthContext, action: str) -> None:
    """校验路由明确声明的动作，不能将中间件推导或动态模型名作为授权依据。"""
    require_permission(ctx, f"models.{action}")


@router.get("/marketplace", operation_id="models_marketplace", summary="查看按业务角色整理的模型接入模板")
def model_marketplace(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/models/marketplace —— 返回按角色（推理/向量/视觉/重排）整理的接入模板。

    权限：``models.view``（"marketplace" 不是关键字末段，GET 推导为查看权）。
    返回：``{"items": [...]}``，模板里的模型名/端点取自部署环境变量与内置项的建议值，
    **不含任何密钥与真实单价**（价格未配置时 ``pricing.configured=false``，不臆造价格）。
    副作用：无（纯读，不写库、不出网）。
    """
    _admin(ctx, "view")
    return {"items": service.marketplace()}


@router.get("/usage", operation_id="models_usage_summary", summary="汇总模型调用量、Token、费用和未计价用量")
def usage_summary(
    start: Annotated[str | None, Query(description="开始日期 YYYY-MM-DD，默认最近 30 天")] = None,
    end: Annotated[str | None, Query(description="结束日期 YYYY-MM-DD，含当天")] = None,
    profile_name: Annotated[str, Query(max_length=100, description="按模型角色筛选，空值为全部")] = "",
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/models/usage —— 用量与费用汇总（按天、按模型、按来源）。

    权限：``models.view``。
    参数：``start``/``end`` 为 ``YYYY-MM-DD``，缺省为最近 30 天；跨度上限 366 天，
    格式或跨度不合法由 service 抛 ValidationError（422）。``profile_name`` 空串表示全部角色。
    返回：``{range, totals, daily, models, sources, budget}``；未计价的调用与 Token 单独计入
    ``totals.unpriced_calls`` / ``unpriced_tokens``，不会以 0 元混进费用。``budget`` 附带本月
    已花费与 ``usage_percent``，``warning``/``exceeded`` 在未设额度时恒为 false。
    副作用：无（纯读）。
    """
    _admin(ctx, "view")
    return service.usage_summary(ctx.db, ctx.workspace_id, start=start, end=end,
                                 profile_name=profile_name.strip())


@router.get("/usage/export", operation_id="models_export_usage", summary="导出逐次模型调用用量 CSV")
def export_usage(
    start: Annotated[str | None, Query(description="开始日期 YYYY-MM-DD")] = None,
    end: Annotated[str | None, Query(description="结束日期 YYYY-MM-DD")] = None,
    ctx: AuthContext = Depends(get_current_context),
):
    """GET /api/models/usage/export —— 导出区间内逐次调用的用量 CSV（文件下载）。

    权限：``models.export``（路径末段 ``export`` 会把动作从"查看"提升为"导出与下载"，
    只给 ``models.view`` 的账号在这里会被拒）。
    参数同 ``/usage``，非法日期抛 ValidationError（422）。
    返回：``text/csv; charset=utf-8`` 附件（带 UTF-8 BOM，便于 Excel 正确识别中文表头）。
    副作用：无写库；响应头 ``Cache-Control: no-store`` 是因为费用数据不适合被中间层缓存，
    文件名同时给出 ASCII 与 RFC 5987 的 ``filename*`` 以兼容中文名下载。
    """
    _admin(ctx, "export")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"模型用量-{stamp}.csv"
    return Response(
        service.usage_csv(ctx.db, ctx.workspace_id, start=start, end=end),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=model-usage.csv; filename*=UTF-8''{quote(name)}",
                 "Cache-Control": "no-store"},
    )


@router.get("/cost-policy", operation_id="models_get_cost_policy", summary="查看模型月度预算策略")
def get_cost_policy(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/models/cost-policy —— 读取工作区的模型月度预算策略。

    权限：``models.view``。
    返回：``{monthly_budget, currency, warn_percent, hard_limit, version}``；
    从未保存过时返回默认值且 ``version=0``（``monthly_budget=None`` 表示只统计、不设额度）。
    副作用：无（纯读）。
    """
    _admin(ctx, "view")
    return service.get_cost_policy(ctx.db, ctx.workspace_id)


@router.put("/cost-policy", operation_id="models_update_cost_policy", summary="修改模型月度预算和提醒策略")
def put_cost_policy(payload: CostPolicyUpdate, ctx: AuthContext = Depends(get_current_context)):
    """PUT /api/models/cost-policy —— 保存月度预算、提醒比例与硬限制开关。

    权限：``models.edit``（PUT 推导为修改权）。
    请求：``version`` 必须是读到的当前版本（首次为 0），不一致抛 409（ConflictError 带 current_version）。
    返回：保存后的策略（重新读库，带新的 ``version``）。
    副作用：写 ``SystemSetting``（key = ``models.cost_policy``）并写审计（``models.cost_policy_update``），
    在同一事务内提交。
    注意：勾选 ``hard_limit`` 只是"具备拦截意图"，是否真能拦住取决于调用是否已计价，
    未配置单价时预算恒为 0，硬限制不会触发（见 ``service.enforce_budget``）。
    """
    _admin(ctx, "edit")
    return service.update_cost_policy(
        ctx.db, ctx.workspace_id, actor_user_id=ctx.user_id,
        monthly_budget=payload.monthly_budget, currency=payload.currency,
        warn_percent=payload.warn_percent, hard_limit=payload.hard_limit,
        version=payload.version,
    )


@router.get("/profiles", operation_id="models_list_profiles", summary="查看当前工作区有效模型配置")
def list_profiles(ctx: AuthContext = Depends(get_current_context)):
    """GET /api/models/profiles —— 列出"当前工作区真正会生效"的模型角色。

    权限：``models.view``。
    返回：``{"items": [...], "count": n}``，每项含 ``source``（database/environment）、
    ``api_key_configured``、``secret_source``（profile/environment/missing）、``last_probe`` 与 ``pricing``。
    只回显"是否已配置密钥"，不返回 endpoint 之外的凭据或密钥引用 id；
    数据库行缺失的角色以环境默认值补齐，因此列表总是包含 reasoning/embedding/vision。
    副作用：无（纯读；这里读到的 ``api_key_configured`` 与运行时可执行性同源，见 service 说明）。
    """
    _admin(ctx, "view")
    items = service.list_effective_profiles(ctx.db, ctx.workspace_id)
    return {"items": items, "count": len(items)}


@router.post("/profiles", status_code=201, operation_id="models_create_profile", summary="新增模型角色配置")
def create_profile(
    payload: ProfileCreate,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/models/profiles —— 新增（或覆盖）一个模型角色配置，成功返回 201。

    权限：``models.create``；覆盖已有名称时还要求 ``models.edit`` 及对应资源范围。
    请求：``api_key`` 为空串表示"不改动/不配置专用密钥"，非空则新建密钥并吊销该角色旧密钥；
    ``pricing=None`` 表示未计价（不是 0 元）。
    返回：``{id, profile_name, api_key_configured}`` —— 不含密钥明文或密文。
    异常：角色名/端点/价格不合法抛 ValidationError（422）；工作区行不存在抛 NotFoundError（404）。
    副作用：写 ``model_profiles`` 与 ``capabilities_json``（含计价快照与密钥引用）、可能新建/吊销
    ``secrets`` 行，并写审计（``model_profile.upsert``）。
    事务边界：业务写与审计是两次 ``commit`` —— 若审计那次提交失败，配置变更已落库但缺审计记录，
    排查"改了但没有审计"时按此解释。
    """
    _admin(ctx, "create")
    row = service.upsert_profile(
        ctx.db,
        ctx=ctx,
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        profile_name=payload.profile_name,
        provider=payload.provider,
        model=payload.model,
        endpoint=payload.endpoint,
        api_key=payload.api_key,
        capabilities=payload.capabilities,
        runtime_options=payload.runtime_options.model_dump(exclude_none=True) if payload.runtime_options is not None else None,
        pricing=payload.pricing.model_dump() if payload.pricing else None,
        enabled=payload.enabled,
    )
    ctx.db.commit()
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="model_profile.upsert",
        resource_type="model_profile",
        resource_id=row.id,
        metadata={"profile_name": row.profile_name, "model": row.model},
    )
    ctx.db.commit()
    return {
        "id": row.id,
        "profile_name": row.profile_name,
        "api_key_configured": bool((row.capabilities_json or {}).get("secret_id")),
        "runtime_options": public_runtime_options(row.capabilities_json or {}),
    }


@router.put("/profiles/{profile_name}", operation_id="models_update_profile", summary="修改模型角色、端点、密钥和计价")
def update_profile(
    profile_name: str,
    payload: ProfileUpdate,
    ctx: AuthContext = Depends(get_current_context),
):
    """PUT /api/models/profiles/{profile_name} —— 按角色名覆盖保存配置（幂等 upsert，不返回 404）。

    权限：``models.edit``。请求体与 ``create_profile`` 相同（见 ``ProfileUpdate`` 关于清空单价的说明）。
    返回：``{id, profile_name, api_key_configured}``。
    异常：角色名、端点或价格不合法抛 ValidationError（422）。
    副作用：同 ``create_profile``（写配置、按需换密钥、写审计 ``model_profile.update``），
    并会把同名的重复 active 行标为 superseded，保证"一个角色只有一行生效"。
    模型名即使为 probe/export/publish 等动作词，仍按 ``models.edit`` 校验。
    """
    _admin(ctx, "edit")
    row = service.upsert_profile(
        ctx.db,
        ctx=ctx,
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        profile_name=profile_name,
        provider=payload.provider,
        model=payload.model,
        endpoint=payload.endpoint,
        api_key=payload.api_key,
        capabilities=payload.capabilities,
        runtime_options=payload.runtime_options.model_dump(exclude_none=True) if payload.runtime_options is not None else None,
        pricing=payload.pricing.model_dump() if payload.pricing else None,
        enabled=payload.enabled,
    )
    ctx.db.commit()
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="model_profile.update",
        resource_type="model_profile",
        resource_id=row.id,
        metadata={"profile_name": row.profile_name, "model": row.model},
    )
    ctx.db.commit()
    return {
        "id": row.id,
        "profile_name": row.profile_name,
        "api_key_configured": bool((row.capabilities_json or {}).get("secret_id")),
        "runtime_options": public_runtime_options(row.capabilities_json or {}),
    }


@router.post("/probe/{profile_name}", operation_id="models_probe_effective_profile", summary="测试模型角色的实际能力")
def probe_effective(
    profile_name: str,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/models/probe/{profile_name} —— 对"当前生效的配置"发起一次真实连通性测试。

    权限：``models.execute``（路径中含 ``probe`` 会把动作判为"执行"，只有查看权的账号会被拒）。
    路径参数是角色名（reasoning/embedding/vision 或自定义名），不要求存在数据库配置行 ——
    无行时按环境默认值探测（工作区隔离仍生效）。
    返回：``{"status": "passed"|"failed", "details": {...}}``，**失败也返回 200**（错误分类在
    ``details.error`` 里），这是前端已消费的既有契约，调用方不能靠状态码判断连通性。
    副作用：真实出网调用供应商接口（向量/视觉/对话三种方式之一），并按调用来源
    ``source=model_probe`` 记入用量账本；同时把探测结果落 ``model_probe_results`` 行。
    已知边界：端点由管理员配置，探测不会拦截内网/回环/云元数据地址，也不做 DNS 重绑定校验，
    因此具备配置权限者可借探测让服务器请求任意地址（SSRF），且工作区密钥会随请求发出。
    """
    _admin(ctx, "execute")
    result = service.probe_effective_profile(
        ctx.db,
        workspace_id=ctx.workspace_id,
        profile_name=profile_name,
    )
    ctx.db.commit()
    return result


@router.post("/profiles/{profile_id}/probe", operation_id="models_probe_profile", summary="测试指定模型配置")
def probe_profile(
    profile_id: str,
    ctx: AuthContext = Depends(get_current_context),
):
    """POST /api/models/profiles/{profile_id}/probe —— 按数据库行 id 探测指定配置。

    权限：``models.execute``（路径末段为 ``probe``）。
    路径参数：``profile_id`` 必须是本工作区、``status="active"`` 的 ``ModelProfile`` 行，
    否则抛 NotFoundError（404）—— 跨工作区 id 也走 404，不区分"不存在"与"不属于你"。
    返回与副作用同 ``probe_effective``（真实出网、记用量、落探测结果），
    差别只是入口先按 id 定位到角色名再走同一套探测逻辑。
    """
    _admin(ctx, "execute")
    row = ctx.db.get(ModelProfile, profile_id)
    if not row or row.workspace_id != ctx.workspace_id or row.status != "active":
        raise NotFoundError("模型配置不存在")
    result = service.probe_effective_profile(
        ctx.db,
        workspace_id=ctx.workspace_id,
        profile_name=row.profile_name,
    )
    ctx.db.commit()
    return result


@router.delete("/profiles/{profile_id}", operation_id="models_delete_profile", summary="停用模型配置并撤销专用密钥")
def delete_profile(
    profile_id: str,
    ctx: AuthContext = Depends(get_current_context),
):
    """DELETE /api/models/profiles/{profile_id} —— **停用**而非删除数据库行。

    权限：``models.delete``（DELETE 推导为删除权）。
    语义（与界面文案"模型配置已停用"一致）：把该行置为 ``enabled=False`` 并撤销其专用密钥，
    但保留这一行继续遮蔽环境默认配置 —— 因此删除后该角色会以"配置已停用"直接失败，
    环境默认值不会自动接管；想恢复环境默认需重新保存该角色（或清理该行）。
    返回：``{"ok": True}``；行不存在或不属于当前工作区抛 NotFoundError（404）。
    副作用：写 ``model_profiles`` 与 ``secrets``（吊销 + 软删除时间），并写审计
    （``model_profile.delete``，resource_id 记录的是被停用的 profile_id）。
    """
    _admin(ctx, "delete")
    service.disable_profile(
        ctx.db,
        workspace_id=ctx.workspace_id,
        profile_id=profile_id,
    )
    ctx.db.commit()
    audit(
        ctx.db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        action="model_profile.delete",
        resource_type="model_profile",
        resource_id=profile_id,
    )
    ctx.db.commit()
    return {"ok": True}
