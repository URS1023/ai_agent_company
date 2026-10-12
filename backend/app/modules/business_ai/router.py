"""Documented AI business actions; chat tools call the same service functions."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.contracts import Envelope
from app.common.deps import AuthContext, get_current_context
from app.modules.business_ai import service
from app.modules.business_ai.schemas import (
    AlertOverviewInput, AlertOverviewView, BusinessActionCapability, DashboardDraftInput,
    DashboardDraftView, DataSourceDiscoveryInput, DataSourceDiscoveryView,
)

router = APIRouter(prefix="/api/v1/ai/business-actions", tags=["AI 工作台业务能力"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("", response_model=Envelope[list[BusinessActionCapability]], operation_id="ai_business_actions_list",
            summary="读取当前用户可用的 AI 业务动作目录")
def list_business_actions(ctx: Context):
    """返回真实注册状态、参数 Schema、权限、副作用、幂等和结果组件；未实现能力不会登记为可执行。"""
    return Envelope(data=service.action_catalog(ctx))


@router.post("/data-sources/discover", response_model=Envelope[DataSourceDiscoveryView],
             operation_id="ai_business_datasources_discover", summary="发现当前授权的分析数据源")
def discover_data_sources(payload: DataSourceDiscoveryInput, ctx: Context):
    """需要 datasources.view；只读取启用源和缓存元数据，不测试连接、不返回凭据、不默认选择首项。"""
    return Envelope(data=service.discover_data_sources(ctx, payload))


@router.post("/alerts/overview", response_model=Envelope[AlertOverviewView],
             operation_id="ai_business_alerts_overview", summary="在 AI 工作台查看设备预警与问题")
def view_alerts(payload: AlertOverviewInput, ctx: Context):
    """需要 alerts.view；读取当前授权报告及问题，不请求设备、不执行流程、不改变问题处置状态。"""
    return Envelope(data=service.alert_overview(ctx, payload))


@router.post("/dashboards/drafts", response_model=Envelope[DashboardDraftView], status_code=201,
             operation_id="ai_business_dashboard_draft_create", summary="生成并保存真实数据数字大屏草稿")
def create_dashboard_draft(payload: DashboardDraftInput, ctx: Context):
    """需要大屏查看/创建/执行及数据源查看权限；auto/manual 另需模板查看。逐组件 SQL 验证后保存草稿，
    不自动发布、不生产写回；requestKey 防止网络重试重复创建，同键不同参数返回 409。
    """
    return Envelope(data=service.create_dashboard_draft(ctx, payload))

