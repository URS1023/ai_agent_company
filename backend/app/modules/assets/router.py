"""Documented APIs for the authorized asset and semantic catalog."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.assets import service
from app.modules.assets.schemas import AnnotationInput, AssetRelationInput, BusinessTermInput, MetricDefinitionInput


router = APIRouter(prefix="/api/v1/assets", tags=["数据与资料目录"])
Context = Annotated[AuthContext, Depends(get_current_context)]
Keyword = Annotated[str, Query(max_length=200, description="名称、说明、别名或标签关键词")]
Limit = Annotated[int, Query(ge=1, le=200, description="本页条数")]
Offset = Annotated[int, Query(ge=0, le=100000, description="分页偏移")]
Version = Annotated[int, Query(ge=1, description="界面读取到的乐观锁版本")]


@router.get("/types", response_model=Envelope[list[dict]], operation_id="assets_types_list", summary="列出可访问的资源类型")
def asset_types(ctx: Context):
    """需要 assets.view；类型和数量均经过源模块权限及数据范围过滤。"""
    # GET /api/v1/assets/types —— 返回当前账号可查看的资产类型及其数量（受权限范围约束）。
    #
    return Envelope(data=service.list_types(ctx))


@router.get("/catalog", response_model=Envelope[dict], operation_id="assets_catalog_list", summary="检索已授权业务资源")
def asset_catalog(
    ctx: Context,
    keyword: Keyword = "",
    asset_type: str = Query("", alias="type", max_length=48, description="资源类型，空值检索全部可访问类型"),
    category: str = Query("", max_length=120),
    business_domain: str = Query("", alias="businessDomain", max_length=120),
    status: str = Query("", max_length=32),
    limit: Limit = 50,
    offset: Offset = 0,
):
    """不复制数据、不探测外部连接，也不返回连接串、密钥或无权资源的名称。"""
    # GET /api/v1/assets —— 资产目录：按类型聚合当前账号可见的资源，支持关键字与分页。
    # 注意：可见范围完全由服务端权限决定，不接受前端传入的"某工作区/某所有者"筛选。
    #
    return Envelope(data=service.list_catalog(
        ctx, keyword=keyword, asset_type=asset_type, category=category,
        business_domain=business_domain, status=status, limit=limit, offset=offset,
    ))


@router.get("/catalog/{asset_type}/{asset_id}", response_model=Envelope[dict], operation_id="assets_catalog_get", summary="读取资源目录详情")
def asset_detail(asset_type: str, asset_id: str, ctx: Context):
    """详情只含安全摘要；数据库字段来自已保存的元数据快照，打开页面不会连接外部系统。"""
    # GET /api/v1/assets/{asset_type}/{asset_id} —— 资产详情（含标注、新鲜度与关联）。异常：404。
    #
    return Envelope(data=service.get_asset(ctx, asset_type, asset_id))


@router.put("/catalog/{asset_type}/{asset_id}/annotation", response_model=Envelope[dict], operation_id="assets_annotation_save", summary="维护资源别名、分类和字段说明")
def annotation_save(asset_type: str, asset_id: str, payload: AnnotationInput, ctx: Context):
    """需要 assets.edit 和源资源查看权限；修改目录元数据不会改写原业务资源。"""
    # POST /api/v1/assets/{asset_type}/{asset_id}/annotation —— 保存资产标注（乐观锁）。
    # 异常：404 资源不存在、403 无 assets.edit 权限、409 版本冲突。
    #
    return Envelope(data=service.save_annotation(ctx, asset_type, asset_id, payload))


@router.delete("/catalog/{asset_type}/{asset_id}/annotation", response_model=Envelope[MutationResult], operation_id="assets_annotation_archive", summary="归档资源目录说明")
def annotation_archive(asset_type: str, asset_id: str, ctx: Context, version: Version):
    """需要 assets.delete；仅归档附加目录信息，绝不删除原业务数据。"""
    # DELETE /api/v1/assets/{asset_type}/{asset_id}/annotation —— 归档资产标注（保留历史）。异常：404 / 409。
    #
    service.remove_annotation(ctx, asset_type, asset_id, version)
    return Envelope(data=MutationResult(id=asset_id, deleted=True))


@router.get("/terms", response_model=Envelope[dict], operation_id="assets_terms_list", summary="查询业务术语")
def term_list(ctx: Context, keyword: Keyword = "", status: str = Query("", max_length=20),
              limit: Limit = 100, offset: Offset = 0):
    """需要 assets.view；术语遵守当前角色的数据范围。"""
    # GET /api/v1/assets/terms —— 分页查询业务术语（keyword/status 筛选）。
    #
    return Envelope(data=service.list_terms(ctx, keyword=keyword, status=status, limit=limit, offset=offset))


@router.post("/terms", status_code=201, response_model=Envelope[dict], operation_id="assets_terms_create", summary="新增业务术语")
def term_create(payload: BusinessTermInput, ctx: Context):
    """需要 assets.create；编码在工作区内唯一。"""
    # POST /api/v1/assets/terms —— 新建业务术语。异常：409 名称重复、422 参数非法。
    #
    return Envelope(data=service.save_term(ctx, payload))


@router.put("/terms/{term_id}", response_model=Envelope[dict], operation_id="assets_terms_update", summary="修改业务术语")
def term_update(term_id: str, payload: BusinessTermInput, ctx: Context):
    """需要 assets.edit 和当前 version；并发覆盖返回 409。"""
    # PUT /api/v1/assets/terms/{term_id} —— 更新业务术语（乐观锁）。异常：404 / 409。
    #
    return Envelope(data=service.save_term(ctx, payload, term_id))


@router.delete("/terms/{term_id}", response_model=Envelope[MutationResult], operation_id="assets_terms_archive", summary="归档业务术语")
def term_archive(term_id: str, ctx: Context, version: Version):
    """需要 assets.delete；归档保留历史口径。"""
    # DELETE /api/v1/assets/terms/{term_id} —— 归档业务术语。异常：404 / 409。
    #
    service.remove_term(ctx, term_id, version)
    return Envelope(data=MutationResult(id=term_id, deleted=True))


@router.get("/metrics", response_model=Envelope[dict], operation_id="assets_metrics_list", summary="查询指标定义")
def metric_list(ctx: Context, keyword: Keyword = "", status: str = Query("", max_length=20),
                limit: Limit = 100, offset: Offset = 0):
    """指标来源会再次检查源资源权限；已撤销来源不会通过指标泄露。"""
    # GET /api/v1/assets/metrics —— 分页查询指标定义（keyword/status 筛选）。
    #
    return Envelope(data=service.list_metrics(ctx, keyword=keyword, status=status, limit=limit, offset=offset))


@router.post("/metrics", status_code=201, response_model=Envelope[dict], operation_id="assets_metrics_create", summary="新增指标定义")
def metric_create(payload: MetricDefinitionInput, ctx: Context):
    """需要 assets.create；每个来源必须是当前账号可查看的真实目录资源。"""
    # POST /api/v1/assets/metrics —— 新建指标定义。异常：409 名称重复、422 来源重复或参数非法。
    #
    return Envelope(data=service.save_metric(ctx, payload))


@router.get("/metrics/{metric_id}", response_model=Envelope[dict], operation_id="assets_metrics_get", summary="读取指标定义")
def metric_get(metric_id: str, ctx: Context):
    """需要 assets.view；返回公式、维度、粒度和仍有权限的来源。"""
    # GET /api/v1/assets/metrics/{metric_id} —— 指标定义详情。异常：404。
    #
    return Envelope(data=service._metric_view(ctx, service.get_metric(ctx, metric_id)))


@router.put("/metrics/{metric_id}", response_model=Envelope[dict], operation_id="assets_metrics_update", summary="修改指标定义")
def metric_update(metric_id: str, payload: MetricDefinitionInput, ctx: Context):
    """需要 assets.edit 和当前 version；目录保存公式但不在此接口执行。"""
    # PUT /api/v1/assets/metrics/{metric_id} —— 更新指标定义（乐观锁）。异常：404 / 409。
    #
    return Envelope(data=service.save_metric(ctx, payload, metric_id))


@router.delete("/metrics/{metric_id}", response_model=Envelope[MutationResult], operation_id="assets_metrics_archive", summary="归档指标定义")
def metric_archive(metric_id: str, ctx: Context, version: Version):
    """需要 assets.delete；保留报表和查询历史中的指标口径。"""
    # DELETE /api/v1/assets/metrics/{metric_id} —— 归档指标定义。异常：404 / 409。
    #
    service.remove_metric(ctx, metric_id, version)
    return Envelope(data=MutationResult(id=metric_id, deleted=True))


@router.get("/relations", response_model=Envelope[dict], operation_id="assets_relations_list", summary="查询资源关系与血缘")
def relation_list(ctx: Context, status: str = Query("active", max_length=20),
                  limit: Limit = 100, offset: Offset = 0):
    """关系两端都必须仍在当前账号权限范围内，否则整条关系不返回。"""
    # GET /api/v1/assets/relations —— 分页查询资产关系（默认只看 active）。
    #
    return Envelope(data=service.list_relations(ctx, status=status, limit=limit, offset=offset))


@router.post("/relations", status_code=201, response_model=Envelope[dict], operation_id="assets_relations_create", summary="建立资源关系或血缘")
def relation_create(payload: AssetRelationInput, ctx: Context):
    """需要 assets.create；两端必须是当前可访问资源，不能建立自环。"""
    # POST /api/v1/assets/relations —— 新建资产关系。异常：422 自环或两端不可见、409 关系已存在。
    #
    return Envelope(data=service.save_relation(ctx, payload))


@router.put("/relations/{relation_id}", response_model=Envelope[dict], operation_id="assets_relations_update", summary="修改资源关系")
def relation_update(relation_id: str, payload: AssetRelationInput, ctx: Context):
    """需要 assets.edit 和当前 version。"""
    # PUT /api/v1/assets/relations/{relation_id} —— 更新资产关系（乐观锁）。异常：404 / 409。
    #
    return Envelope(data=service.save_relation(ctx, payload, relation_id))


@router.delete("/relations/{relation_id}", response_model=Envelope[MutationResult], operation_id="assets_relations_archive", summary="归档资源关系")
def relation_archive(relation_id: str, ctx: Context, version: Version):
    """需要 assets.delete；不删除关系两端的业务资源。"""
    # DELETE /api/v1/assets/relations/{relation_id} —— 归档资产关系。异常：404 / 409。
    #
    service.remove_relation(ctx, relation_id, version)
    return Envelope(data=MutationResult(id=relation_id, deleted=True))

