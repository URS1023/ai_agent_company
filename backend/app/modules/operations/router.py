"""Documented APIs for run tracing, health, recovery and audit review.

中文补充：运维中心路由层，统一前缀 `/api/v1/operations`（标签"运行与运维"），
由 `app/modules/router_index.py` 注册。本层只做参数校验与鉴权声明，业务计算全部下沉到
`service`；除 `POST /runs/recover` 外均为只读 GET。三类视图的数据来源分别是：
运行记录 = runs 及其步骤/工具调用/模型调用/事件子表，审计日志 = audit_logs，
业务运营 = 知识库 / Bot / 应用的真实业务表。权限不在本层判断，而由 service 内的
`require_permission` 与数据范围过滤统一执行，因此"有路由权限"不等于"能看到数据"。
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.common.contracts import Envelope
from app.common.deps import AuthContext, get_current_context
from app.modules.operations import service
from app.modules.operations.schemas import RecoverStaleInput

router = APIRouter(prefix="/api/v1/operations", tags=["运行与运维"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("/summary", response_model=Envelope[dict], operation_id="operations_summary", summary="读取当前授权范围的运行健康摘要")
def operations_summary(
    ctx: Context,
    threshold_seconds: int = Query(900, alias="thresholdSeconds", ge=60, le=86400),
):
    """所有数量来自当前工作区；没有下层权限时对应数据明确为空。

    `GET /api/v1/operations/summary`，权限 `operations.view`（缺失即 403）。
    只读：`database` 字段由服务端真实执行一次只读探针（`SELECT 1`）得出，`api` 恒为
    `healthy`（能进入本处理函数即证明进程存活，不做额外探测）。
    越过权限的字段返回 None 而非 0，前端据此区分"无数据"与"无权限"；
    `canRecover` 要求同时具备 `operations.edit` 与 `runs.edit`。
    """
    return Envelope(data=service.summary(ctx, threshold_seconds=threshold_seconds))


@router.get("/runs", response_model=Envelope[dict], operation_id="operations_runs_list", summary="筛选运行记录")
def operations_runs_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    status: Literal["", "created", "running", "waiting", "completed", "failed", "cancelled"] = "",
    profile: str = Query("", max_length=100),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    """筛选运行记录（运行记录视图）。

    `GET /api/v1/operations/runs`，需要 `operations.view` 与 `runs.view`；数据源为当前
    工作区的 runs 表（标题取自会话，未归档会话缺失时回落到派生名）。只读，无副作用。
    `keyword` 同时匹配运行 ID、会话标题、发起人显示名与错误摘要；`status` 为空表示不筛，
    其余取值即 runs 的状态语义：`created/running/waiting` 为非终态（`waiting` 指等待用户
    输入），`completed/failed/cancelled` 为终态。`limit/offset` 为分页，`profiles` 仅统计
    当前页出现过的模型配置，供筛选下拉使用。
    """
    return Envelope(data=service.list_runs(
        ctx, keyword=keyword, status=status, profile=profile, limit=limit, offset=offset,
    ))


@router.get("/runs/{run_id}", response_model=Envelope[dict], operation_id="operations_runs_get", summary="读取运行步骤和调用链")
def operations_runs_get(run_id: str, ctx: Context):
    """仅返回当前 runs.view 数据范围内的运行；敏感参数在服务端遮蔽。

    `GET /api/v1/operations/runs/{run_id}`，需要 `operations.view` 与 `runs.view`。
    超出数据范围与记录不存在返回同一 404，避免探测他人运行是否存在。只读：返回该运行的
    步骤、工具调用、模型调用、事件时间线与产物清单（产物仅在具备 `files.view` 时附加，
    否则为空数组），所有载荷再次经过凭据遮蔽后才输出。
    """
    return Envelope(data=service.run_detail(ctx, run_id))


@router.post("/runs/recover", response_model=Envelope[dict], operation_id="operations_runs_recover", summary="预览或恢复中断运行")
def operations_runs_recover(payload: RecoverStaleInput, ctx: Context):
    """默认 dryRun；正式恢复需 operations.edit 与 runs.edit，并写入审计日志。

    `POST /api/v1/operations/runs/recover`，权限 `operations.edit`（数据范围另取
    `runs.edit`）。两段式语义：请求体 `dryRun=true`（默认）只预览将要处理的运行与跳过原因，
    不落库、不写审计；`dryRun=false` 才把确认已中断的运行标记为失败（`error_code` 为
    `stale_run_recovered`）并补一条用户可见的中断说明，同时写入
    `operations.run.recover` 审计记录。整个批次在同一事务内提交；只改状态，不删除任何
    步骤、工具调用或历史记录，因此该操作可审计但不可"回滚数据"。
    """
    return Envelope(data=service.recover(ctx, payload))


@router.get("/audit", response_model=Envelope[dict], operation_id="operations_audit_list", summary="筛选审计日志")
def operations_audit_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    action: str = Query("", max_length=100),
    resource_type: str = Query("", alias="resourceType", max_length=64),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    """筛选审计日志（审计日志视图）。

    `GET /api/v1/operations/audit`，需要 `operations.view` 与 `audit.view`。数据源是
    `audit_logs` 表——由各业务写入方（`app/common/audit.py` 的 `audit()`）在动作发生时
    落库，本接口只读取不生成，因此"审计条数"反映的是真实发生的动作而非访问次数。
    只读，无副作用。`action`/`resourceType` 为精确匹配的枚举筛选，`keyword` 匹配动作、
    资源类型/ID、运行 ID 与操作者姓名或登录名；`actions`/`resourceTypes` 仅聚合当前页，
    供前端筛选下拉使用。`actor_user_id` 为空的历史条目按"系统"归类。
    """
    return Envelope(data=service.list_audit(
        ctx, keyword=keyword, action=action, resource_type=resource_type,
        limit=limit, offset=offset,
    ))


@router.get("/business", response_model=Envelope[dict], operation_id="operations_business_summary", summary="读取知识、Bot 或应用运营看板")
def operations_business_summary(
    ctx: Context,
    domain: Literal["knowledge", "bots", "applications"] = Query(description="运营对象"),
    object_id: str = Query("", alias="objectId", max_length=26, description="可选的具体知识库、Bot 或应用标识"),
    days: int = Query(30, ge=7, le=365, description="趋势日期范围，返回连续完整日期序列"),
):
    """按对象模块权限和数据范围聚合真实记录，返回完整图表分类、序列、治理项和错误下钻。

    `GET /api/v1/operations/business`，需要 `operations.view`，并按 `domain` 追加对应的
    模块权限：knowledge 需 `knowledge.view`、bots 需 `bots.view`、applications 需
    `applications.view`；`objectId` 落在数据范围外统一按 404 处理。数据源为各域真实业务表
    （知识库/文档/分段、Bot 与 Bot 调试运行、应用与启动记录），不使用统计快照表；
    缺失日期补零成完整连续序列。只读，无副作用。
    """
    return Envelope(data=service.business_operations(ctx, domain, days, object_id))
