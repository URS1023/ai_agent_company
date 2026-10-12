"""Documented HTTP source configuration, extraction, collection and history APIs."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.common.contracts import Envelope, MutationResult, PagedEnvelope
from app.common.deps import AuthContext, get_current_context
from app.modules.analytics.schemas import HistoryView
from app.modules.collection import execution, sources
from app.modules.collection.schemas import CollectionResult, CollectInput, ExtractionPreview, HttpSourceInput, HttpSourceView

router = APIRouter(prefix="/api/v1/collection", tags=["HTTP 数据采集"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("/sources", response_model=PagedEnvelope[HttpSourceView], operation_id="collection_sources_list", summary="列出授权 HTTP 数据源")
def list_sources(ctx: Context, limit: int = Query(100, ge=1, le=500, description="每页条数"), offset: int = Query(0, ge=0, description="分页偏移")):
    """需要 datasources.view；按当前工作区、动作与数据范围过滤，不返回凭据值。"""
    return PagedEnvelope(**sources.list_sources(ctx, limit=limit, offset=offset, paged=True))


@router.post("/sources", status_code=201, response_model=Envelope[HttpSourceView], operation_id="collection_sources_create", summary="创建 HTTP 单条或批量采集配置")
def create_source(payload: HttpSourceInput, ctx: Context):
    """需要 datasources.create；不发送网络请求。内网授权需 system.admin，POST 自动授权需 datasources.publish。"""
    return Envelope(data=sources.create_source(ctx, payload))


@router.get("/sources/{source_id}", response_model=Envelope[HttpSourceView], operation_id="collection_sources_get", summary="读取 HTTP 配置及版本")
def get_source(source_id: str, ctx: Context):
    """需要 datasources.view；凭据仅返回名称，停用资源仍可查看。"""
    return Envelope(data=sources.source_view(sources.http_source(ctx, source_id)))


@router.put("/sources/{source_id}", response_model=Envelope[HttpSourceView], operation_id="collection_sources_update", summary="按版本编辑 HTTP 配置")
def update_source(source_id: str, payload: HttpSourceInput, ctx: Context):
    """需要 datasources.edit；旧版本返回 409，不覆盖他人修改；变更内网目标及自动 POST 重新校验授权。"""
    return Envelope(data=sources.update_source(ctx, source_id, payload))


@router.delete("/sources/{source_id}", response_model=Envelope[MutationResult], operation_id="collection_sources_delete", summary="移除 HTTP 配置并保留历史")
def delete_source(source_id: str, ctx: Context, version: int = Query(ge=1, description="当前配置版本，过期返回 409")):
    """需要 datasources.delete；软删除，不删除结果、运行或历史加密凭据，不操作外部设备。"""
    sources.remove_source(ctx, source_id, version)
    return Envelope(data=MutationResult(id=source_id, deleted=True))


@router.post("/sources/{source_id}/collect", response_model=Envelope[CollectionResult], operation_id="collection_sources_collect", summary="执行已保存 HTTP 采集",
             responses={207: {"model": Envelope[CollectionResult], "description": "部分成功或被截断"},
                        502: {"model": Envelope[CollectionResult], "description": "全部失败，仍返回逐条结果与追溯标识"}})
def collect(source_id: str, payload: CollectInput, ctx: Context, response: Response):
    """需要 datasources.execute 及源范围；POST 需明确确认。无跳转、代理继承或自动重试。全部失败 502，部分完成 207。"""
    result = execution.collect(ctx, source_id, payload)
    response.status_code = 200 if result.status == "success" else 207 if result.status == "partial" else 502
    return Envelope(success=result.success, data=result, message=result.errorMessage)


@router.post("/extract-preview", response_model=Envelope[dict], operation_id="collection_extract_preview", summary="从 JSON 样本预览可选字段")
def extract_preview(payload: ExtractionPreview, ctx: Context):
    """需要 datasources.view；只处理用户给出的样本，不发送 HTTP，不保存样本或执行历史。"""
    return Envelope(data=execution.extraction_preview(ctx, payload))


@router.get("/sources/{source_id}/history", response_model=PagedEnvelope[HistoryView], operation_id="collection_history_list", summary="分页查看 HTTP 采集历史")
def history_list(source_id: str, ctx: Context, limit: int = Query(100, ge=1, le=500, description="每页条数"), offset: int = Query(0, ge=0, description="分页偏移")):
    """需要 datasources.view 及源/结果范围；保留失败与部分成功记录，撤销源权限后不可再读。"""
    return PagedEnvelope(**execution.list_history(ctx, source_id, limit=limit, offset=offset, paged=True))


@router.get("/history/{history_id}", response_model=Envelope[CollectionResult], operation_id="collection_history_get", summary="读取真实采集结果及逐条状态")
def history_get(history_id: str, ctx: Context):
    """需要 datasources.view；不重新执行请求，返回已持久化结果、来源序号、配置版本及运行标识。"""
    return Envelope(data=execution.historical_result(ctx, history_id))
