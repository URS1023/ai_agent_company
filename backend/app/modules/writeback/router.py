"""Normal UI and AI contracts for explicit targets and actual write receipts.

中文补充（写回 HTTP 层）：
- 路由前缀 ``/api/v1/writeback``：/targets 管理显式写入白名单，
  /operations 读取某次运行的真实写入凭据。
- 鉴权：所有端点依赖登录上下文；服务层再按 datasources.view / edit / writeback
  的资源级权限与数据范围过滤。写操作必须携带 version 做乐观锁。
- 语义约定：删除只是撤销授权（软删除），不删外部业务数据；读取回执不会重发写入，
  状态未知时明确返回 unconfirmed/uncertain，绝不把未知当成功或零影响。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.contracts import Envelope, MutationResult, PagedEnvelope
from app.common.deps import AuthContext, get_current_context
from app.modules.writeback import execution, targets
from app.modules.writeback.schemas import OperationView, TargetInput, TargetView

router = APIRouter(prefix="/api/v1/writeback", tags=["数据库结果写入"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("/targets", response_model=PagedEnvelope[TargetView], operation_id="writeback_targets_list", summary="查询有权限的写入目标")
def list_targets(ctx: Context, limit: int = Query(50, ge=1, le=200, description="每页条数"), offset: int = Query(0, ge=0, le=100000, description="分页偏移")):
    """按当前数据源读取权限过滤后分页；只返回配置，不测试连接或写入数据库。"""
    return PagedEnvelope(**targets.list_targets(ctx, limit=limit, offset=offset, paged=True))


@router.get("/targets/{target_id}", response_model=Envelope[TargetView], operation_id="writeback_targets_get", summary="读取指定写入目标")
def get_target(target_id: str, ctx: Context):
    """需要目标数据源的读取权限。已停用配置可供历史追溯，不代表仍获准执行。"""
    row, _ = targets.get_target(ctx, target_id)
    return Envelope(data=targets.target_view(row))


@router.post("/targets", response_model=Envelope[TargetView], status_code=201, operation_id="writeback_targets_create", summary="建立显式表字段写入白名单")
def create_target(payload: TargetInput, ctx: Context):
    """需要目标源 datasources.edit、datasources.view 和高影响 datasources.writeback 权限。实时核对普通表及字段，保存不写业务记录。"""
    return Envelope(data=targets.save_target(ctx, payload))


@router.put("/targets/{target_id}", response_model=Envelope[TargetView], operation_id="writeback_targets_update", summary="修改或停用写入目标")
def update_target(target_id: str, payload: TargetInput, ctx: Context):
    """使用 version 防止覆盖他人修改；修改后原工作流固定版本不能继续写入，必须显式重新选择。不能改绑另一数据源。"""
    return Envelope(data=targets.save_target(ctx, payload, target_id))


@router.delete("/targets/{target_id}", response_model=Envelope[MutationResult], operation_id="writeback_targets_remove", summary="移除写入授权但保留凭据")
def remove_target(target_id: str, ctx: Context, version: int = Query(ge=1, description="当前目标版本")):
    """需要数据源修改和写入权限；仅软删除配置，不删除目标表、业务数据或历史写入凭据。"""
    targets.remove_target(ctx, target_id, version)
    return Envelope(data=MutationResult(id=target_id, deleted=True))


@router.get("/operations", response_model=Envelope[list[OperationView]], operation_id="writeback_operations_list", summary="读取一次工作流的实际写入凭据")
def list_operations(ctx: Context, execution_id: str = Query(alias="executionId", min_length=1, max_length=26, description="工作流执行标识")):
    """需要运行记录及所有来源权限；最多对应图内 100 个节点。读取不重发写入，提交状态未知时明确返回 unconfirmed/uncertain。"""
    return Envelope(data=execution.for_execution(ctx, execution_id))
