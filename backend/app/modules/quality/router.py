"""Typed quality APIs shared by normal pages and permission-aware tools.

职责：质检域对外的 REST 层（前缀 ``/api/v1/quality``，标签"质量检查与追溯"），
覆盖指标/模板及其修订、质检记录与复核、图片、普通运行表单、质检向导、产品目录与让步流程。

位置与依赖：**薄路由层** —— 只做参数解析、权限/响应包装（``Envelope`` / ``MutationResult``）
与 HTTP 语义映射，业务逻辑全部在 ``registry`` / ``inspections`` / ``runs`` /
``workflow_builder`` / ``product_catalog`` / ``concessions`` 中；同一套接口同时服务页面与
经授权的 AI 工具，因此每个路由的 docstring 都写明所需权限与副作用（也是工具描述文案来源）。

统一约定：
- 分页 ``limit``（1–100）/ ``offset``，且是"已授权结果内的偏移"；
- 删除/归档/复核等写操作携带乐观锁版本，过期返回 409；
- 图片响应统一走 ``image_response``（禁止缓存、禁止内联脚本），且**不在 URL 里放令牌**。
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, UploadFile, File as FileForm
from fastapi.responses import HTMLResponse, Response

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.common.authorization import require_permission
from app.errors import NotFoundError
from app.modules.quality import images
from app.modules.quality import registry, inspections
from app.modules.quality import runs
from app.modules.quality import workflow_builder
from app.modules.quality import product_catalog
from app.modules.quality.run_plans import build_plan
from app.modules.quality.run_schemas import QualityRunInput, QualityRunPlan, QualityRunView
from app.modules.quality.workflow_schemas import QualityWorkflowInput, QualityWorkflowView
from app.modules.quality.schemas import (
    MetricInput, MetricView, TemplateInput, TemplateView, RevisionView,
    InspectionInput, InspectionView, ReviewInput, QualityImageView,
)
from app.modules.quality.concession_schemas import (
    ConcessionDecisionInput, ConcessionEvidenceInput, ConcessionRequestView,
    ConcessionRuleInput, ConcessionRuleView, ConcessionSourceOption, ConcessionSourcePage,
)
from app.modules.quality import concessions

router = APIRouter(prefix="/api/v1/quality", tags=["质量检查与追溯"])
Context = Annotated[AuthContext, Depends(get_current_context)]
Limit = Annotated[int, Query(ge=1, le=100, description="每页条数")]
Offset = Annotated[int, Query(ge=0, le=100000, description="授权结果分页偏移")]
Keyword = Annotated[str, Query(max_length=160, description="名称或样本检索")]
Version = Annotated[int, Query(ge=1, description="当前乐观锁版本")]
IMAGE_RESPONSES = {200: {"description": "授权且哈希核验通过的原始图片字节；Cache-Control: no-store",
                         "content": {mime: {"schema": {"type": "string", "format": "binary"}}
                                     for mime in ("image/png", "image/jpeg", "image/webp")}}}


@router.get("/products", response_model=Envelope[dict], operation_id="quality_products_list",
            summary="查询按产品和检测要求配置的质检工作流入口")
def quality_products(ctx: Context):
    """需要 quality.view、workflows.view、数据源及各工作流当前资源权限；仅返回当前工作区配置。目录数量标注导入快照时间；进入页面不会创建或执行工作流。"""
    return Envelope(data=product_catalog.catalogue(ctx))


@router.get("/concession-rules", response_model=Envelope[list[ConcessionRuleView]],
            operation_id="quality_concession_rules_list", summary="查询来料质量让步规则")
def list_concession_rules(ctx: Context, limit: Limit = 30, offset: Offset = 0, keyword: Keyword = ""):
    """需要 quality.view；只显示当前用户已授权且仍可配置的规则，规则变更不改写已有申请快照。"""
    return Envelope(data=concessions.list_rules(ctx, limit=limit, offset=offset, keyword=keyword))


@router.post("/concession-rules", status_code=201, response_model=Envelope[ConcessionRuleView],
             operation_id="quality_concession_rules_create", summary="新增来料质量让步规则")
def create_concession_rule(payload: ConcessionRuleInput, ctx: Context):
    """需要 quality.create；扣量方式、匹配指标、标准界限和舍入方式必须显式配置，不使用会议示例或系统默认比例。"""
    return Envelope(data=concessions.save_rule(ctx, payload))


@router.put("/concession-rules/{rule_id}", response_model=Envelope[ConcessionRuleView],
            operation_id="quality_concession_rules_update", summary="修改来料质量让步规则")
def update_concession_rule(rule_id: str, payload: ConcessionRuleInput, ctx: Context):
    """需要 quality.edit 与当前版本；已有让步申请保留创建时的规则和计算快照。"""
    return Envelope(data=concessions.save_rule(ctx, payload, rule_id))


@router.delete("/concession-rules/{rule_id}", response_model=Envelope[MutationResult],
               operation_id="quality_concession_rules_delete", summary="停用来料质量让步规则")
def remove_concession_rule(rule_id: str, ctx: Context, version: Version):
    """需要 quality.delete；仅软删除规则，历史申请和审批审计仍可追溯。"""
    concessions.remove_rule(ctx, rule_id, version)
    return Envelope(data=MutationResult(id=rule_id, deleted=True))


@router.get("/concession-sources", response_model=Envelope[ConcessionSourcePage],
            operation_id="quality_concession_sources_list", summary="查询可用于让步的已完成质量证据")
def list_concession_sources(ctx: Context, kind: Literal["workflow", "inspection"] = "workflow",
                            limit: Limit = 30, offset: Offset = 0):
    """需要 quality.view 和来源当前读取权限；按来源类型分页，不把客户端输入当成自动来源。"""
    items, total, has_more = concessions.list_source_options(ctx, kind=kind, limit=limit, offset=offset)
    return Envelope(data=ConcessionSourcePage(items=items, total=total, limit=limit, offset=offset,
                                               hasMore=has_more))


@router.get("/concessions", response_model=Envelope[list[ConcessionRequestView]],
            operation_id="quality_concessions_list", summary="查询让步申请与审批状态")
def list_concession_requests(ctx: Context, limit: Limit = 30, offset: Offset = 0,
                             status: str = Query(default="", max_length=32)):
    """需要 quality.view；候选结算重量只有 approved 状态才可作为结算输入。"""
    return Envelope(data=concessions.list_requests(ctx, limit=limit, offset=offset, status=status))


@router.post("/concessions/evaluate", status_code=201, response_model=Envelope[ConcessionRequestView],
             operation_id="quality_concessions_evaluate", summary="按实际证据生成让步申请")
def evaluate_concession(payload: ConcessionEvidenceInput, ctx: Context):
    """需要 quality.create；服务只保存用户已授权来源的实际原始重量、实测值和标准快照；缺数据、无规则或多规则匹配均进入待确认，不生成可结算重量。"""
    return Envelope(data=concessions.create_request(ctx, payload))


@router.post("/concessions/{request_id}/decision", response_model=Envelope[ConcessionRequestView],
             operation_id="quality_concessions_decide", summary="审批让步申请")
def decide_concession(request_id: str, payload: ConcessionDecisionInput, ctx: Context):
    """需要 quality.review 和当前版本；批准后才暴露 settlementWeight，驳回和待确认状态始终不可结算。"""
    return Envelope(data=concessions.decide_request(ctx, request_id, payload))


@router.post("/workflows", status_code=201, response_model=Envelope[QualityWorkflowView],
             operation_id="quality_workflows_create", summary="从普通质检页面创建并发布检测流程")
def create_quality_workflow(payload: QualityWorkflowInput, ctx: Context):
    """需要 quality.view/create、workflows.view/create/publish 及全部设备、模板、指标、HTTP 服务和可选写入目标读取权限。用户逐组选择设备、固定模板修订及每个指标的检测服务；接口在任何持久化前校验完整覆盖、版本、请求模板和图可执行性，不调用设备。创建、首个发布修订、防重复凭据和审计同事务提交。"""
    return Envelope(data=workflow_builder.create_published(ctx, payload))


@router.get("/workflows/{workflow_id}/run-plan", response_model=Envelope[QualityRunPlan], operation_id="quality_run_plan", summary="读取普通质检运行方案")
def run_plan(workflow_id: str, ctx: Context):
    """需要 quality.view 与流程及完整来源读取权限。仅解析已发布流程的设备/固定模板/指标/写入映射，不调用检测服务、不自动选择模板。"""
    plan, _ = build_plan(ctx, workflow_id)
    return Envelope(data=plan)


@router.post("/workflows/{workflow_id}/runs", response_model=Envelope[QualityRunView], operation_id="quality_run_submit", summary="从普通表单运行多设备多样本质检")
def run_submit(workflow_id: str, payload: QualityRunInput, ctx: Context, response: Response):
    """需要 quality.execute、workflows.execute 及全部设备/指标/数据/原图权限；图片外发另需 files.export，落库需明确确认。最多100个设备样本组合；全批预检后使用共享工作流，无默认模板。相同键同参数只返回历史，参数不同409；202运行中，502检测执行失败仍返回实际逐样本状态。"""
    view = runs.submit(ctx, workflow_id, payload)
    response.status_code = 202 if view.execution.status == "running" else 502 if view.execution.status == "failed" else 200
    return Envelope(data=view)


@router.get("/workflows/{workflow_id}/runs/{execution_id}", response_model=Envelope[QualityRunView], operation_id="quality_run_result", summary="查看批次质检逐样本报告状态")
def run_result(workflow_id: str, execution_id: str, ctx: Context):
    """需要 quality.view 与实际工作流、设备、模板、数据源和原图的当前读取权限。未生成报告明确列出，不把失败样本当合格；查看和刷新不会重发检测。"""
    return Envelope(data=runs.result(ctx, workflow_id, execution_id))


@router.post("/images", status_code=201, response_model=Envelope[QualityImageView], operation_id="quality_images_upload", summary="上传质检原图并校验实际内容")
def upload_image(ctx: Context, file: UploadFile = FileForm(...)):
    """需要 quality.create、files.create 和 files.view。最多 5 MB 的静态 PNG/JPEG/WebP；保存原字节，不调用模型。"""
    for permission in ("quality.create", "files.create", "files.view"):
        require_permission(ctx, permission)
    # Decode and file I/O stay in FastAPI's synchronous worker, not its event loop.
    data = file.file.read(images.MAX_IMAGE_BYTES + 1)
    return Envelope(data=images.upload_image(ctx, file.filename or "", data))


@router.get("/images/{file_id}", response_model=Envelope[QualityImageView], operation_id="quality_images_get", summary="核验可用于质检的原图资料")
def get_image(file_id: str, ctx: Context):
    """需要 quality.view 与 files.view 的数据范围；验证实际图片、尺寸和哈希，不接受外部 URL。"""
    require_permission(ctx, "quality.view")
    view, _ = images.image_content(ctx, file_id)
    return Envelope(data=view)


def image_response(view: QualityImageView, data: bytes) -> Response:
    """Never place tokens in image URLs or permit cached content after permission revocation.

    （中文补充）统一的图片响应构造：类型取解码结果（不信任扩展名），响应头固定
    ``no-store``（权限被收回后不得再命中缓存）+ ``nosniff`` + CSP 沙箱 + ``inline`` 展示。
    调用方必须已经完成授权与哈希校验；本函数不做权限判断。
    """
    return Response(data, media_type=view.mimeType, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'", "Content-Disposition": "inline"})


@router.get("/images/{file_id}/content", response_class=Response, responses=IMAGE_RESPONSES, operation_id="quality_images_content", summary="授权预览质检图片")
def get_image_content(file_id: str, ctx: Context):
    """需要 Bearer 会话、quality.view 与 files.view；只读取原图，不请求任意 URL，不接受 URL 会话令牌。"""
    require_permission(ctx, "quality.view")
    return image_response(*images.image_content(ctx, file_id))


@router.get("/inspections/{inspection_id}/images/{file_id}", response_class=Response, responses=IMAGE_RESPONSES, operation_id="quality_inspection_image", summary="按固定报告证据预览原图与缺陷")
def get_inspection_image(inspection_id: str, file_id: str, ctx: Context):
    """需要完整报告证据链及 files.view 权限。核验关联与原哈希；软删除后仅历史报告可读，不重跑检测。"""
    row = inspections.get_inspection(ctx, inspection_id)
    snapshot = next((item["provenance"]["image"] for item in row.results if item.get("provenance", {}).get("image", {}).get("id") == file_id), None)
    if snapshot is None:
        raise NotFoundError("图片不属于此质检报告")
    return image_response(*images.image_content(ctx, file_id, snapshot=snapshot))

@router.get("/metrics", response_model=Envelope[list[MetricView]], operation_id="quality_metrics_list", summary="查询质检指标")
def list_metrics(ctx: Context, limit: Limit = 30, offset: Offset = 0, keyword: Keyword = ""):
    """需要 quality.view；按动作数据范围及关联指标权限分页，不显示未经授权的记录。"""
    return Envelope(data=registry.listing(ctx, "metrics", limit=limit, offset=offset, keyword=keyword))


@router.post("/metrics", status_code=201, response_model=Envelope[MetricView], operation_id="quality_metrics_create", summary="新增质检指标")
def create_metric(payload: MetricInput, ctx: Context):
    """需要 quality.create；保存不调用设备；模板明确选择指标并固化完整标准。"""
    return Envelope(data=registry.save(ctx, "metrics", payload))


@router.get("/metrics/{record_id}", response_model=Envelope[MetricView], operation_id="quality_metrics_get", summary="读取质检指标")
def get_metric(record_id: str, ctx: Context):
    """需要 quality.view；软删除配置不能用于新检验，原始标准仍通过历史修订读取。"""
    row = registry.record(ctx, "metrics", record_id)
    return Envelope(data=registry.view("metrics", row))


@router.put("/metrics/{record_id}", response_model=Envelope[MetricView], operation_id="quality_metrics_update", summary="修改质检指标")
def update_metric(record_id: str, payload: MetricInput, ctx: Context):
    """需要 quality.edit 与 version；旧版本冲突返回 409。修改不改写已有模板标准及报告。"""
    return Envelope(data=registry.save(ctx, "metrics", payload, record_id))


@router.delete("/metrics/{record_id}", response_model=Envelope[MutationResult], operation_id="quality_metrics_delete", summary="移除质检指标并保留历史")
def remove_metric(record_id: str, ctx: Context, version: Version):
    """需要 quality.delete；仅软删除配置，不删除实际检测、模板修订和报告。"""
    registry.remove(ctx, "metrics", record_id, version)
    return Envelope(data=MutationResult(id=record_id, deleted=True))


@router.get("/templates", response_model=Envelope[list[TemplateView]], operation_id="quality_templates_list", summary="查询质检模板")
def list_templates(ctx: Context, limit: Limit = 30, offset: Offset = 0, keyword: Keyword = ""):
    """需要 quality.view；按动作数据范围及关联指标权限分页，不显示未经授权的记录。"""
    return Envelope(data=registry.listing(ctx, "templates", limit=limit, offset=offset, keyword=keyword))


@router.post("/templates", status_code=201, response_model=Envelope[TemplateView], operation_id="quality_templates_create", summary="新增质检模板")
def create_template(payload: TemplateInput, ctx: Context):
    """需要 quality.create；保存不调用设备；模板明确选择指标并固化完整标准。"""
    return Envelope(data=registry.save(ctx, "templates", payload))


@router.get("/templates/{record_id}", response_model=Envelope[TemplateView], operation_id="quality_templates_get", summary="读取质检模板")
def get_template(record_id: str, ctx: Context):
    """需要 quality.view；软删除配置不能用于新检验，原始标准仍通过历史修订读取。"""
    row = registry.record(ctx, "templates", record_id)
    registry.template_revision(ctx, row.id, row.version)
    return Envelope(data=registry.view("templates", row))


@router.put("/templates/{record_id}", response_model=Envelope[TemplateView], operation_id="quality_templates_update", summary="修改质检模板")
def update_template(record_id: str, payload: TemplateInput, ctx: Context):
    """需要 quality.edit 与 version；旧版本冲突返回 409。修改不改写已有模板标准及报告。"""
    return Envelope(data=registry.save(ctx, "templates", payload, record_id))


@router.delete("/templates/{record_id}", response_model=Envelope[MutationResult], operation_id="quality_templates_delete", summary="移除质检模板并保留历史")
def remove_template(record_id: str, ctx: Context, version: Version):
    """需要 quality.delete；仅软删除配置，不删除实际检测、模板修订和报告。"""
    registry.remove(ctx, "templates", record_id, version)
    return Envelope(data=MutationResult(id=record_id, deleted=True))


@router.get("/templates/{template_id}/revisions", response_model=Envelope[list[RevisionView]], operation_id="quality_templates_revisions", summary="选择固定模板与指标标准修订")
def revisions(template_id: str, ctx: Context, limit: Limit = 30, offset: Offset = 0):
    """需要 quality.view；每个修订包含原始指标标准，当前权限撤销仍然生效。"""
    return Envelope(data=registry.revisions(ctx, template_id, limit=limit, offset=offset))


@router.get("/inspections", response_model=Envelope[list[InspectionView]], operation_id="quality_inspections_list", summary="查询质检记录和结论")
def list_inspections(ctx: Context, limit: Limit = 30, offset: Offset = 0, keyword: Keyword = "", outcome: str = ""):
    """需要 quality.view；按样本和原始结论查询，人工复核结论独立展示，归档记录不在常规列表。"""
    return Envelope(data=inspections.list_inspections(ctx, limit=limit, offset=offset, keyword=keyword, outcome=outcome))


@router.post("/inspections", status_code=201, response_model=Envelope[InspectionView], operation_id="quality_inspections_create", summary="按明确模板创建质量检查")
def create_inspection(payload: InspectionInput, ctx: Context):
    """需要 quality.create 与设备/模板/来源读取权限；未提交的模板指标明确记为未完成；重复提交键不会新增记录。"""
    return Envelope(data=inspections.inspection_view(ctx, inspections.create_inspection(ctx, payload)))


@router.get("/inspections/{inspection_id}", response_model=Envelope[InspectionView], operation_id="quality_inspections_get", summary="读取质检结果、固定标准和复核历史")
def get_inspection(inspection_id: str, ctx: Context):
    """需要 quality.view 和当前完整证据链读取权限；不会重跑设备，归档后仍可追溯。"""
    return Envelope(data=inspections.inspection_view(ctx, inspections.get_inspection(ctx, inspection_id)))


@router.post("/inspections/{inspection_id}/reviews", response_model=Envelope[InspectionView], operation_id="quality_inspections_review", summary="追加逐项人工复核")
def review(inspection_id: str, payload: ReviewInput, ctx: Context):
    """需要专门的 quality.review 权限和当前版本；必须填写依据，不修改原始实测值、缺陷或判断。"""
    return Envelope(data=inspections.review(ctx, inspection_id, payload))


@router.delete("/inspections/{inspection_id}", response_model=Envelope[MutationResult], operation_id="quality_inspections_archive", summary="归档质检记录并保留证据")
def archive(inspection_id: str, ctx: Context, version: Version):
    """需要 quality.delete；归档不物理删除，直接详情与历史追溯仍受授权保护。"""
    inspections.archive(ctx, inspection_id, version)
    return Envelope(data=MutationResult(id=inspection_id, deleted=True))


@router.get("/inspections/{inspection_id}/export", response_class=HTMLResponse, operation_id="quality_inspections_export", summary="导出质量检查报告")
def export(inspection_id: str, ctx: Context):
    """需要 quality.export；从持久化原始证据与复核历史导出转义 HTML，不重新调用检测服务。"""
    return HTMLResponse(inspections.export_report(ctx, inspection_id),
        headers={"Content-Disposition": 'attachment; filename="quality-inspection.html"',
                 "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
