"""Independent report, template and prompt modules with documented operations.

本模块是报表/数字大屏域的 HTTP 入口，只做参数绑定、权限校验与调用服务层，不承载业务规则。
对外挂载三组路由（均在末尾统一 include 到 ``router``）：

- ``/api/v1/reports``：报表与大屏的列表/详情/保存/删除、修订与恢复、组件数据刷新与钻取、
  试运行查询，以及 ``/widget/ai-generate``、``/screen/ai-generate`` 两个 AI 生成入口和生成记录查询；
- ``/api/v1/report-templates``：模板列表/详情/创建/导入导出/修订/使用记录/发布/恢复/回收站；
- ``/api/v1/report-prompts``：提示词分类与模板的增删改查、复用与内容修订。

关键依赖：``app.modules.reports`` 下的 content/data/generation/prompts/service/templates 六个
服务模块，以及 ``common.authorization.require_permission`` 与 ``common.deps`` 的鉴权上下文。

重要约束：权限按动作拆分（``dashboards.view/create/edit/delete/execute/publish``、
``templates.view/create/edit/delete``、``prompts.view/create/edit/delete``），发布是独立权限点；
模板相关接口在未选择或找不到模板时**不会回退到默认模板**；生成类接口返回的每个指标都必须
经过真实查询验证，失败时不返回静态演示结果。
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.common.authorization import require_permission
from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import QueryInput, QueryResult
from app.modules.collection.schemas import CollectionResult, HttpWidgetPreview
from app.modules.reports import content, data, generation, prompts, service, templates
from app.modules.reports.schemas import (
    CategoryInput, DrilldownInput, GenerationInput, ScreenGenerationInput, PromptInput, ReportInput,
    ReportView, RestoreInput, TemplateFromReportInput, TemplateInput, TemplatePackageInput,
    TemplatePublishInput, TemplateRestoreInput,
)

router = APIRouter()
reports = APIRouter(prefix="/api/v1/reports", tags=["报表与数字大屏"])
template_router = APIRouter(prefix="/api/v1/report-templates", tags=["大屏模板中心"])
prompt_router = APIRouter(prefix="/api/v1/report-prompts", tags=["提示词中心"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@reports.post("/http-query", response_model=Envelope[CollectionResult], operation_id="reports_preview_http", summary="试运行授权 HTTP 组件数据绑定")
def preview_http(payload: HttpWidgetPreview, ctx: Context):
    """需要 dashboards.execute 与数据源查看权限；仅执行已批准自动只读采集的固定配置，不接受任意 URL/请求体。"""
    return Envelope(data=data.http_widget_data(ctx, payload.datasource_id))


@reports.get("/list", response_model=Envelope[list[ReportView]], operation_id="reports_list", summary="列出可访问的报表与大屏")
def list_reports(ctx: Context, keyword: str = Query("", max_length=200, description="报表名称关键词")):
    """普通报表列表，不依赖聊天记录；同时过滤报表与已绑定数据源权限。"""
    return Envelope(data=service.list_reports(ctx, keyword))


@reports.get("/component-catalog", response_model=Envelope[dict], operation_id="reports_component_catalog", summary="读取完整迁移组件目录")
def component_catalog(ctx: Context):
    """目录保留全部智慧云脑 Cloud Brain 类型、分类及工具栏；目录数量不是渲染验收结果。"""
    require_permission(ctx, "dashboards.view")
    return Envelope(data=content.component_catalog())


@reports.get("/source-catalog", response_model=Envelope[dict], operation_id="reports_source_catalog", summary="查看智慧云脑 Cloud Brain 直接报表来源证据")
def report_source_catalog(ctx: Context):
    """返回当前离线镜像的源记录摘要、组件计数和内容哈希；运行时不访问外部来源服务。"""
    require_permission(ctx, "dashboards.view")
    catalog = content.lynx_report_catalog()
    return Envelope(data={key: catalog[key] for key in (
        "schemaVersion", "sourceSystem", "sourceEndpoint", "sourceRecordCount",
        "sourceRecordsSha256", "reportCount", "enabledReportCount",
        "duplicateNameCount", "componentCounts", "catalogSha256", "records",
    )})


@reports.post("", response_model=Envelope[ReportView], status_code=201, operation_id="reports_create", summary="从空白或明确选择的模板新建报表")
def create_report(payload: ReportInput, ctx: Context):
    """需要 dashboards.create；手选模式必须提供模板，空模板不会回退到第一项。"""
    return Envelope(data=service.save_report(ctx, payload))


@reports.post("/widget/ai-generate", response_model=Envelope[dict], operation_id="reports_ai_widget", summary="根据真实数据生成单个可编辑组件")
def generate_widget(payload: GenerationInput, ctx: Context):
    """需要 dashboards.execute；模型生成的 SQL 逐条执行验证，失败不返回静态演示结果。"""
    return Envelope(data=generation.generate(ctx, payload, kind="widget"))


@reports.post("/screen/ai-generate", response_model=Envelope[dict], operation_id="reports_ai_screen", summary="生成完整可编辑数字大屏")
def generate_screen(payload: ScreenGenerationInput, ctx: Context):
    """可多数据源分区分析；遵循 auto/manual/none 模板策略，记录真实生成阶段和每个查询来源。"""
    return Envelope(data=generation.generate(ctx, payload, kind="screen"))


@reports.get("/generations/{generation_id}", response_model=Envelope[dict], operation_id="reports_generation_detail", summary="查看大屏生成记录与真实阶段")
def generation_detail(generation_id: str, ctx: Context):
    """重新验证资源范围和已解析的数据源权限，不把模拟计时进度当作实际完成进度。"""
    return Envelope(data=generation.generation_view(ctx, generation_id))


@reports.post("/query", response_model=Envelope[QueryResult], operation_id="reports_preview_query", summary="试运行设计器中尚未保存的组件查询")
def preview_query(payload: QueryInput, ctx: Context):
    """需要 dashboards.execute；与人工分析相同的只读、字段和参数限制。"""
    return Envelope(data=execute_query(ctx, payload, permission="dashboards.execute"))


@reports.post("/drilldown", response_model=Envelope[dict], operation_id="reports_drilldown", summary="按已保存组件配置查询钻取明细")
def drilldown(payload: DrilldownInput, ctx: Context):
    """从服务端报表读取 SQL，只接收报表 ID、组件 ID 和参数，不执行客户端临时拼接 SQL。"""
    return Envelope(data=data.drilldown(ctx, payload))


@reports.get("/{report_id}/widgets/{widget_id}/data", response_model=Envelope[dict], operation_id="reports_widget_data", summary="刷新已保存组件的真实数据")
def widget_data(report_id: str, widget_id: str, ctx: Context):
    """只读查看者可刷新其有权查看的已保存绑定，不因此获得任意 SQL 执行权限。"""
    return Envelope(data=data.refresh_widget(ctx, report_id, widget_id))


@reports.get("/{report_id}/revisions", response_model=Envelope[list[dict]], operation_id="reports_revisions", summary="查看报表完整修订历史")
def report_revisions(report_id: str, ctx: Context):
    """历史快照保留画布、组件、模板和来源；读取前检查报表与数据源权限。"""
    return Envelope(data=service.revisions(ctx, report_id))


@reports.post("/{report_id}/restore", response_model=Envelope[ReportView], operation_id="reports_restore", summary="恢复历史版本并创建新修订")
def restore_report(report_id: str, payload: RestoreInput, ctx: Context):
    """需要 dashboards.edit；原有历史不覆盖，恢复后为草稿并再次验证全部引用。"""
    return Envelope(data=service.restore_report(ctx, report_id, payload))


@reports.get("/{report_id}", response_model=Envelope[ReportView], operation_id="reports_detail", summary="读取独立报表或大屏的完整配置")
def report_detail(report_id: str, ctx: Context):
    """刷新或直接访问设计器时均可恢复同一持久化资源，不要求存在 AI 会话。"""
    return Envelope(data=service.report_view(service.get_report(ctx, report_id)))


@reports.put("/{report_id}", response_model=Envelope[ReportView], operation_id="reports_update", summary="保存完整报表配置或修改名称")
def update_report(report_id: str, payload: ReportInput, ctx: Context):
    """需要 dashboards.edit；必须提交加载时的 version，并发修改返回 409。"""
    return Envelope(data=service.save_report(ctx, payload, report_id))


@reports.delete("/{report_id}", response_model=Envelope[MutationResult], operation_id="reports_delete", summary="移除报表并保留修订历史")
def delete_report(report_id: str, ctx: Context):
    """需要 dashboards.delete；不删除源数据库、源文件或其他报表。"""
    service.remove_report(ctx, report_id)
    return Envelope(data=MutationResult(id=report_id, deleted=True))


@template_router.get("", response_model=Envelope[list[dict]], operation_id="report_templates_list", summary="列出 20 套高保真与工作区自定义大屏模板")
def list_templates(
    ctx: Context,
    keyword: str = Query("", max_length=200, description="按名称、描述或标签搜索"),
    include_deleted: bool = Query(False, alias="includeDeleted", description="是否包含当前工作区已删除的模板"),
):
    """返回 33 套智慧云脑 Cloud Brain 实际大屏的数据空白模板及工作区自定义模板；来源模板使用真实报表截图。"""
    return Envelope(data=templates.list_templates(ctx, keyword, include_deleted=include_deleted))


@template_router.post("", response_model=Envelope[dict], status_code=201, operation_id="report_templates_create", summary="创建自定义大屏模板")
def create_template(payload: TemplateInput, ctx: Context):
    """需要 templates.create；移除私有数据、查询和凭据，并锁定完整视觉骨架。"""
    return Envelope(data=templates.save_template(ctx, payload))


@template_router.post(
    "/from-report/{report_id}",
    response_model=Envelope[dict],
    status_code=201,
    operation_id="report_templates_create_from_report",
    summary="将已确认报表保存为数据空白的锁定模板",
)
def create_template_from_report(report_id: str, payload: TemplateFromReportInput, ctx: Context):
    """源报表不变；模板删除数据源、SQL、参数、结果和凭据，只保留画布、组件、布局、样式及真实缩略图。"""
    return Envelope(data=templates.create_from_report(ctx, report_id, payload))


@template_router.post("/import", response_model=Envelope[dict], status_code=201, operation_id="report_templates_import", summary="校验并原子导入大屏模板包")
def import_template(payload: TemplatePackageInput, ctx: Context):
    """需要 templates.create；校验格式与 SHA-256 后清除绑定、查询、数据和凭据，再创建工作区模板。"""
    return Envelope(data=templates.import_template(ctx, payload))


@template_router.get("/{template_id}", response_model=Envelope[dict], operation_id="report_templates_detail", summary="读取指定大屏模板与预览")
def get_template(template_id: str, ctx: Context):
    """没有指定或找不到模板时不会选择默认模板。"""
    return Envelope(data=templates.get_template(ctx, template_id))


@template_router.get("/{template_id}/export", response_model=Envelope[dict], operation_id="report_templates_export", summary="导出无业务数据的可校验模板包")
def export_template(template_id: str, ctx: Context):
    """需要 templates.view；接口导入、自定义和停用模板均可导出，包内不包含 SQL、数据源、结果或凭据。"""
    return Envelope(data=templates.export_template(ctx, template_id))


@template_router.get("/{template_id}/revisions", response_model=Envelope[list[dict]], operation_id="report_templates_revisions", summary="查看模板不可变修订历史")
def template_revisions(template_id: str, ctx: Context):
    """返回自定义模板每次创建、编辑、停用、发布和恢复的不可变快照。"""
    return Envelope(data=templates.revisions(ctx, template_id))


@template_router.get("/{template_id}/usage", response_model=Envelope[list[dict]], operation_id="report_templates_usage", summary="查看模板真实使用记录")
def template_usage(template_id: str, ctx: Context, limit: int = Query(100, ge=1, le=500, description="最多返回的使用事件")):
    """只记录实际用模板创建报表的服务端事件；列表计数受当前数据范围约束。"""
    return Envelope(data=templates.usage_history(ctx, template_id, limit))


@template_router.put("/{template_id}", response_model=Envelope[dict], operation_id="report_templates_update", summary="按版本修改自定义模板")
def update_template(template_id: str, payload: TemplateInput, ctx: Context):
    """平台来源模板不能直接覆盖；自定义模板按乐观锁版本更新。"""
    return Envelope(data=templates.save_template(ctx, payload, template_id))


@template_router.post("/{template_id}/publish", response_model=Envelope[dict], operation_id="report_templates_publish", summary="按版本重新发布停用模板")
def publish_template(template_id: str, payload: TemplatePublishInput, ctx: Context):
    """需要 templates.edit；必须提交当前版本，平台来源模板不可修改。"""
    return Envelope(data=templates.publish_template(ctx, template_id, payload))


@template_router.post("/{template_id}/restore", response_model=Envelope[dict], operation_id="report_templates_restore", summary="恢复模板历史并创建新修订")
def restore_template(template_id: str, payload: TemplateRestoreInput, ctx: Context):
    """需要 templates.edit；历史快照保持不变，恢复结果始终生成新的可追溯版本。"""
    return Envelope(data=templates.restore_template(ctx, template_id, payload))


@template_router.post("/{template_id}/recover", response_model=Envelope[dict], operation_id="report_templates_recover", summary="从模板回收站恢复")
def recover_template(template_id: str, ctx: Context):
    """恢复当前工作区软删除的平台来源模板或自定义模板，不改写模板内容。"""
    return Envelope(data=templates.recover_template(ctx, template_id))


@template_router.delete("/{template_id}", response_model=Envelope[MutationResult], operation_id="report_templates_delete", summary="将大屏模板移入回收站")
def disable_template(template_id: str, ctx: Context):
    """平台来源模板和自定义模板均为工作区软删除且可恢复；已有报表不受影响。"""
    templates.remove_template(ctx, template_id)
    return Envelope(data=MutationResult(id=template_id, deleted=True))


@prompt_router.get("/categories", response_model=Envelope[list[dict]], operation_id="report_prompt_categories_list", summary="列出提示词分类")
def prompt_categories(ctx: Context):
    """保留智慧云脑 Cloud Brain 全部 9 个制造业分类，并支持工作区内自定义分类。"""
    return Envelope(data=prompts.list_categories(ctx))


@prompt_router.post("/categories", response_model=Envelope[dict], status_code=201, operation_id="report_prompt_categories_create", summary="新建提示词分类")
def create_prompt_category(payload: CategoryInput, ctx: Context):
    """需要 prompts.create；普通模块界面和设计器提示词面板共用此操作。"""
    return Envelope(data=prompts.save_category(ctx, payload))


@prompt_router.put("/categories/{category_id}", response_model=Envelope[dict], operation_id="report_prompt_categories_update", summary="按版本编辑提示词分类")
def update_prompt_category(category_id: str, payload: CategoryInput, ctx: Context):
    """需要 prompts.edit；不能覆盖系统预置分类。"""
    return Envelope(data=prompts.save_category(ctx, payload, category_id))


@prompt_router.delete("/categories/{category_id}", response_model=Envelope[MutationResult], operation_id="report_prompt_categories_delete", summary="移除空的自定义提示词分类")
def delete_prompt_category(category_id: str, ctx: Context):
    """分类仍有未移除提示词时返回 409；不会遗留孤立提示词。"""
    prompts.remove_category(ctx, category_id)
    return Envelope(data=MutationResult(id=category_id, deleted=True))


@prompt_router.get("/templates", response_model=Envelope[list[dict]], operation_id="report_prompts_list", summary="搜索和筛选可复用提示词")
def prompt_list(ctx: Context, category_id: str | None = Query(None, alias="categoryId", description="提示词分类"),
                keyword: str = Query("", max_length=200, description="名称、正文、说明和标签关键词"),
                mode: Literal["screen", "widget", "query"] | None = Query(None, description="使用场景")):
    """保留 36 条智慧云脑 Cloud Brain 制造业提示词；用户可直接从提示词模块管理和复用。"""
    return Envelope(data=prompts.list_prompts(ctx, category_id=category_id, keyword=keyword, mode=mode))


@prompt_router.post("/templates", response_model=Envelope[dict], status_code=201, operation_id="report_prompts_create", summary="保存自定义提示词")
def create_prompt(payload: PromptInput, ctx: Context):
    """需要 prompts.create；记录初始内容版本，不执行正文中的工具或权限指令。"""
    return Envelope(data=prompts.save_prompt(ctx, payload))


@prompt_router.put("/templates/{prompt_id}", response_model=Envelope[dict], operation_id="report_prompts_update", summary="按版本修改提示词")
def update_prompt(prompt_id: str, payload: PromptInput, ctx: Context):
    """需要 prompts.edit；原始版本保留，内置模板只允许复制。"""
    return Envelope(data=prompts.save_prompt(ctx, payload, prompt_id))


@prompt_router.delete("/templates/{prompt_id}", response_model=Envelope[MutationResult], operation_id="report_prompts_delete", summary="移除自定义提示词")
def delete_prompt(prompt_id: str, ctx: Context):
    """需要 prompts.delete；保留已使用内容版本以供运行追溯。"""
    prompts.remove_prompt(ctx, prompt_id)
    return Envelope(data=MutationResult(id=prompt_id, deleted=True))


@prompt_router.post("/templates/{prompt_id}/use", response_model=Envelope[dict], operation_id="report_prompts_use", summary="复用提示词并记录使用次数")
def use_prompt(prompt_id: str, ctx: Context):
    """仅复用正文，不自动执行其业务；使用次数增加不会覆盖编辑中的内容版本。"""
    return Envelope(data=prompts.use_prompt(ctx, prompt_id))


@prompt_router.get("/templates/{prompt_id}/revisions", response_model=Envelope[list[dict]], operation_id="report_prompts_revisions", summary="查看提示词内容版本")
def prompt_revisions(prompt_id: str, ctx: Context):
    """受当前提示词权限保护的不可变内容修订记录。"""
    return Envelope(data=prompts.prompt_revisions(ctx, prompt_id))


router.include_router(reports)
router.include_router(template_router)
router.include_router(prompt_router)
