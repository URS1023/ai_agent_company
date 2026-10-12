"""Documented APIs for the unified document and presentation center."""
from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File as FileForm, Form, Query, Response, UploadFile

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_content_auth, get_current_context
from app.modules.documents import service
from app.modules.documents.schemas import (
    CopyInput,
    ExecuteInput,
    GenerationInput,
    PresentationPlanInput,
    PresentationSlideBatchInput,
)

router = APIRouter(prefix="/api/v1/documents", tags=["文档与 PPT"])
Context = Annotated[AuthContext, Depends(get_current_context)]
ContentContext = Annotated[AuthContext, Depends(get_content_auth)]
Version = Annotated[int, Query(ge=1, description="界面读取到的乐观锁版本")]


@router.get("/generations", response_model=Envelope[dict], operation_id="document_generations_list", summary="查询文档生成记录")
def generation_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    output_type: str = Query("", alias="outputType", max_length=24),
    status: str = Query("", max_length=24),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    """需要 documents.view；按角色数据范围返回生成记录，不列出他人未授权产物。"""
    # GET /api/v1/documents/generations —— 查询生成记录（keyword/outputType/status + 分页）。只读。
    #
    return Envelope(data=service.list_generations(
        ctx, keyword=keyword, output_type=output_type, status=status, limit=limit, offset=offset,
    ))


@router.post("/generations", response_model=Envelope[dict], status_code=201, operation_id="document_generations_create", summary="创建生成草稿")
def generation_create(payload: GenerationInput, ctx: Context):
    """PPT 必须明确选择模板；系统不会使用第一套或默认模板。"""
    # POST /api/v1/documents/generations —— 新建生成草稿。异常：404 模板/来源不可见、422 参数非法。
    #
    return Envelope(data=service.save_generation(ctx, payload))


@router.get("/generations/{generation_id}", response_model=Envelope[dict], operation_id="document_generations_get", summary="读取生成草稿或结果")
def generation_get(generation_id: str, ctx: Context):
    """GET /api/v1/documents/generations/{generation_id} —— 生成记录详情（含结构化内容）。异常：404。"""
    row = service.get_generation(ctx, generation_id)
    return Envelope(data=service.generation_view(ctx, row, include_spec=True))


@router.put("/generations/{generation_id}", response_model=Envelope[dict], operation_id="document_generations_update", summary="修改尚未完成的生成草稿")
def generation_update(generation_id: str, payload: GenerationInput, ctx: Context):
    """已完成产物不可覆盖；需复制为新草稿后再修改。"""
    # PUT /api/v1/documents/generations/{generation_id} —— 更新草稿（乐观锁）。
    # 异常：404 / 409（非 draft/failed 状态不可改）。
    #
    return Envelope(data=service.save_generation(ctx, payload, generation_id))


@router.delete("/generations/{generation_id}", response_model=Envelope[MutationResult], operation_id="document_generations_archive", summary="归档生成记录")
def generation_archive(generation_id: str, ctx: Context, version: Version):
    """仅归档业务记录，不删除已有产物和审计追溯。"""
    # DELETE /api/v1/documents/generations/{generation_id} —— 归档生成记录。异常：404 / 409。
    #
    service.archive_generation(ctx, generation_id, version)
    return Envelope(data=MutationResult(id=generation_id, deleted=True))


@router.post("/generations/{generation_id}/copy", response_model=Envelope[dict], status_code=201, operation_id="document_generations_copy", summary="复制为可编辑草稿")
def generation_copy(generation_id: str, payload: CopyInput, ctx: Context):
    """POST /api/v1/documents/generations/{generation_id}/copy —— 复制为新草稿（产物不复制）。异常：404。"""
    return Envelope(data=service.copy_generation(ctx, generation_id, payload))


@router.post("/generations/{generation_id}/execute", response_model=Envelope[dict], operation_id="document_generations_execute", summary="生成真实文件")
def generation_execute(generation_id: str, payload: ExecuteInput, ctx: Context):
    """同步完成结构编写、真实渲染、页数校验、产物落库及运行追溯；失败不会伪装成功。"""
    # POST /api/v1/documents/generations/{generation_id}/execute —— 执行生成。
    # 状态守卫：completed/archived/running 都会 409，等价于重复执行的熔断；failed 允许重跑。
    # 副作用：调用模型（AI 模式）、写出真实文件并落产物；失败文案分业务异常与未预期异常两档。
    #
    return Envelope(data=service.execute_generation(ctx, generation_id, payload.version))


@router.get("/generations/{generation_id}/events", response_model=Envelope[list[dict]], operation_id="document_generation_events_list", summary="查询生成阶段记录")
def generation_events(generation_id: str, ctx: Context):
    """GET /api/v1/documents/generations/{generation_id}/events —— 查询阶段事件（执行进度与失败原因）。"""
    return Envelope(data=service.list_events(ctx, generation_id))


@router.get("/generations/{generation_id}/presentation-draft", response_model=Envelope[dict], operation_id="document_presentation_draft_get", summary="读取分阶段 PPT 编写进度")
def presentation_draft_get(generation_id: str, ctx: Context):
    """GET /api/v1/documents/generations/{generation_id}/presentation-draft —— 查询 PPT 分阶段草稿状态。
    返回是否已建大纲、已写/缺失页号与下一步动作；异常：404 / 422（非 PPT）。
    """
    return Envelope(data=service.presentation_status(ctx, generation_id))


@router.post("/generations/{generation_id}/presentation-plan", response_model=Envelope[dict], operation_id="document_presentation_plan_create", summary="建立 PPT 大纲")
def presentation_plan_create(generation_id: str, payload: PresentationPlanInput, ctx: Context):
    """指定总页数包含封面和结束页；正文按大纲生成并分批编写。"""
    # POST /api/v1/documents/generations/{generation_id}/presentation-plan —— 建立 PPT 大纲（分阶段第一步）。
    # 异常：404 / 409（状态、版本或大纲已存在）/ 422（非人工 PPT 草稿）。
    #
    return Envelope(data=service.create_presentation_plan(ctx, generation_id, payload))


@router.post("/generations/{generation_id}/presentation-slides", response_model=Envelope[dict], operation_id="document_presentation_slides_write", summary="分批写入 PPT 正文页")
def presentation_slides_write(generation_id: str, payload: PresentationSlideBatchInput, ctx: Context):
    """每批最多四页，全部正文完成后才能执行真实 PPT 渲染。"""
    # POST /api/v1/documents/generations/{generation_id}/presentation-slides —— 分批写入正文页（分阶段第二步）。
    # 异常：404 / 409 / 422。
    #
    return Envelope(data=service.write_presentation_slides(ctx, generation_id, payload))


@router.get("/generations/{generation_id}/preview", operation_id="document_generation_preview", summary="预览生成结果")
def generation_preview(
    generation_id: str,
    ctx: ContentContext,
    slide: int = Query(1, ge=1, le=50, description="PPT 或 PDF 的页码，从 1 开始"),
):
    """GET /api/v1/documents/generations/{generation_id}/preview —— 预览生成结果（PPT 按页出图）。
    权限：内容读取权限（get_content_auth）。异常：404 / 422 页码越界。
    """
    data, mime_type = service.generation_preview(ctx, generation_id, slide)
    return Response(content=data, media_type=mime_type, headers={"Cache-Control": "private, max-age=300"})


@router.get("/generations/{generation_id}/download", operation_id="document_generation_download", summary="下载生成文件")
def generation_download(generation_id: str, ctx: ContentContext, attachment: bool = Query(True)):
    """GET /api/v1/documents/generations/{generation_id}/download —— 下载生成产物。
    权限：内容读取权限。异常：404（记录或产物缺失）。
    """
    _row, artifact, data = service.generation_content(ctx, generation_id)
    disposition = "attachment; filename=generated; filename*=UTF-8''" + quote(artifact.name)
    # 浏览器 fetch 取字节时由前端 Blob 下载控制文件名，不能再携带附件头，
    # 否则部分浏览器会接管 PDF 响应并交给 fetch 一个空的 204。
    # 直接打开下载地址仍默认返回原附件头；预览和产物 MIME 元数据不受影响。
    headers = {"X-Content-Type-Options": "nosniff"}
    if attachment:
        headers["Content-Disposition"] = disposition
    media_type = artifact.mime_type if attachment else "application/octet-stream"
    return Response(content=data, media_type=media_type, headers=headers)


@router.get("/templates", response_model=Envelope[dict], operation_id="document_templates_list", summary="列出可选 PPT 模板")
def template_list(ctx: Context):
    """返回 20 套内置模板及当前数据范围内的客户模板，不返回 defaultId。"""
    # GET /api/v1/documents/templates —— 列出可用模板（内置 + 客户上传），供 PPT 必选模板的下拉框使用。
    #
    return Envelope(data=service.list_templates(ctx))


@router.post("/templates", response_model=Envelope[dict], status_code=201, operation_id="document_templates_upload", summary="上传客户 PPTX 模板")
async def template_upload(
    ctx: Context,
    file: UploadFile = FileForm(..., description="不超过 50MB 的 PPTX 文件"),
    name: str = Form(default="", max_length=100),
):
    """POST /api/v1/documents/templates —— 上传客户 PPT 模板（multipart，校验为可解析的 pptx）。
    异常：422 文件类型或内容不合法。副作用：写对象存储与模板登记记录，并写审计。
    """
    data = await file.read()
    return Envelope(data=service.upload_template(
        ctx,
        filename=file.filename or "template.pptx",
        mime_type=file.content_type or "application/octet-stream",
        data=data,
        display_name=name,
    ))


@router.get("/templates/{template_id}/preview", operation_id="document_templates_preview", summary="预览 PPT 模板")
def template_preview(template_id: str, ctx: ContentContext):
    """GET /api/v1/documents/templates/{template_id}/preview —— 预览模板封面。权限：内容读取权限。异常：404。"""
    data, mime_type = service.template_preview(ctx, template_id)
    return Response(content=data, media_type=mime_type, headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/templates/{template_id}", response_model=Envelope[MutationResult], operation_id="document_templates_delete", summary="移除客户 PPT 模板")
def template_delete(template_id: str, ctx: Context):
    """内置模板不可删除；仍被活动草稿引用的客户模板也不会被移除。"""
    # DELETE /api/v1/documents/templates/{template_id} —— 删除上传的客户模板（内置模板不可删）。异常：404。
    #
    service.delete_template(ctx, template_id)
    return Envelope(data=MutationResult(id=template_id, deleted=True))
