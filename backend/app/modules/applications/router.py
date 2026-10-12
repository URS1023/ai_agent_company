"""Documented APIs for application assembly, publication and launch history."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.applications import service
from app.modules.applications.schemas import (
    ApplicationInput,
    CloneInput,
    LaunchInput,
    PublishInput,
    RecordVersionInput,
)

router = APIRouter(prefix="/api/v1/applications", tags=["应用与解决方案"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("", response_model=Envelope[dict], operation_id="applications_list", summary="查询应用与解决方案")
def applications_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    status: str = Query("", max_length=24),
    kind: str = Query("", max_length=24),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    """按 applications.view 的数据范围和发布受众返回，不泄露未授权依赖资源。"""
    # GET /api/v1/applications —— 查询应用列表（关键字/类型/状态 + 分页）。可见性含发布受众过滤。
    #
    return Envelope(data=service.list_applications(
        ctx, keyword=keyword, status=status, kind=kind, limit=limit, offset=offset,
    ))


@router.get("/catalog", response_model=Envelope[dict], operation_id="applications_resource_catalog", summary="查询可绑定资源与受众")
def applications_resource_catalog(ctx: Context):
    """仅列出当前账号可查看的真实资源；角色和用户选项还需要对应系统查看权限。"""
    # GET /api/v1/applications/resource-catalog —— 返回当前账号可绑定的各类资源目录。
    #
    return Envelope(data=service.resource_catalog(ctx))


@router.post("", response_model=Envelope[dict], status_code=201, operation_id="applications_create", summary="创建应用草稿")
def applications_create(payload: ApplicationInput, ctx: Context):
    """创建稳定应用标识和首个不可变草稿版本；不会自动发布。"""
    # POST /api/v1/applications —— 新建应用（含第一版草稿）。异常：409 code 重复、422 规格非法。
    #
    return Envelope(data=service.create_application(ctx, payload))


@router.get("/{application_id}", response_model=Envelope[dict], operation_id="applications_get", summary="读取应用详情")
def applications_get(application_id: str, ctx: Context):
    """GET /api/v1/applications/{application_id} —— 应用详情。异常：404 / 403 不在发布受众内。"""
    return Envelope(data=service.get_application(ctx, application_id))


@router.put("/{application_id}", response_model=Envelope[dict], operation_id="applications_version_create", summary="创建应用新版本")
def applications_version_create(application_id: str, payload: ApplicationInput, ctx: Context):
    """不覆盖历史版本；已发布版本继续可用，直到明确发布新草稿。"""
    # POST /api/v1/applications/{application_id}/versions —— 新建草稿版本（不发布）。异常：404 / 409。
    #
    return Envelope(data=service.create_version(ctx, application_id, payload))


@router.post("/{application_id}/publish", response_model=Envelope[dict], operation_id="applications_publish", summary="发布最新应用版本")
def applications_publish(application_id: str, payload: PublishInput, ctx: Context):
    """发布前重新核验所有必需资源、当前工作区、权限、状态和受众；失败不改变线上版本。"""
    # POST /api/v1/applications/{application_id}/publish —— 发布最新草稿为正式版本。
    # 异常：409（记录版本冲突或不是最新草稿）、422（资源未就绪）。
    #
    return Envelope(data=service.publish_application(
        ctx, application_id, record_version=payload.version, release_version=payload.release_version,
    ))


@router.post("/{application_id}/disable", response_model=Envelope[dict], operation_id="applications_disable", summary="停用应用入口")
def applications_disable(application_id: str, payload: RecordVersionInput, ctx: Context):
    """停用后保留正式版本和启动记录，恢复需再次明确发布。"""
    # POST /api/v1/applications/{application_id}/disable —— 停用应用（保留版本与启动历史）。异常：404 / 409。
    #
    return Envelope(data=service.disable_application(ctx, application_id, payload.version))


@router.post("/{application_id}/clone", response_model=Envelope[dict], status_code=201, operation_id="applications_clone", summary="复制为新应用草稿")
def applications_clone(application_id: str, payload: CloneInput, ctx: Context):
    """POST /api/v1/applications/{application_id}/clone —— 复制应用（复制最新版本规格）。异常：404 / 409。"""
    return Envelope(data=service.clone_application(ctx, application_id, payload))


@router.delete("/{application_id}", response_model=Envelope[MutationResult], operation_id="applications_archive", summary="归档应用")
def applications_archive(
    application_id: str,
    ctx: Context,
    version: int = Query(ge=1, description="界面读取到的应用记录乐观锁版本"),
):
    """只归档应用入口；版本、审计和历史运行均保留。"""
    # DELETE /api/v1/applications/{application_id} —— 归档应用（保留历史）。异常：404 / 409。
    #
    service.archive_application(ctx, application_id, version)
    return Envelope(data=MutationResult(id=application_id, deleted=True))


@router.post("/{application_id}/launch", response_model=Envelope[dict], operation_id="applications_launch", summary="启动正式应用")
def applications_launch(application_id: str, payload: LaunchInput, ctx: Context):
    """解析固定站内入口，校验输入与底层权限，并在统一运行中心记录本次启动。"""
    # POST /api/v1/applications/{application_id}/launch —— 启动应用。
    # 要点：必须有正式版本；必填资源不可用会失败，可选资源降级；带幂等键时重复提交返回同一启动记录。
    # 副作用：可能真实执行绑定的工作流/Bot（写操作需 confirmWrites）。异常：404 / 409 / 422。
    #
    return Envelope(data=service.launch_application(ctx, application_id, payload))


@router.get("/{application_id}/launches", response_model=Envelope[list[dict]], operation_id="applications_launches_list", summary="查询应用启动记录")
def applications_launches_list(
    application_id: str,
    ctx: Context,
    limit: int = Query(100, ge=1, le=200),
):
    """GET /api/v1/applications/{application_id}/launches —— 查询启动历史（倒序）。异常：404。"""
    return Envelope(data=service.list_launches(ctx, application_id, limit=limit))
