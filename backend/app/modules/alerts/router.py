"""Ordinary equipment and alert APIs, usable by both UI and authorized AI tools.

职责：设备预警域对外的 REST 层（前缀 ``/api/v1/alerts``，标签"设备预警与设备台账"），
覆盖设备台账、名片图、测点、专家经验、预警策略与修订、向导建流程、报告与问题处置。

位置与依赖：**薄路由层** —— 只做参数解析、权限/响应包装（``Envelope`` / ``MutationResult``）
与 HTTP 语义映射，业务逻辑全部在 ``registry`` / ``reports`` / ``workflow_builder`` /
``card_images`` / ``device_catalog`` 中；同一套接口同时服务页面与经授权的 AI 工具调用，
因此每个路由的 docstring 都写明所需权限与副作用（也是工具描述文案来源）。

统一约定：
- 鉴权由 ``get_current_context`` 提供上下文，具体权限在各服务函数内校验；
  ``version`` 查询参数是删除等操作的乐观锁版本，过期返回 409。
- 分页统一 ``limit``（1–200）/ ``offset``，且是"已授权结果内的偏移"。
- 列表/详情返回 ``Envelope``，写操作返回创建或变更后的视图；报告导出返回
  ``text/html`` 附件并带 CSP 沙箱头（内容来自用户数据，必须限制脚本执行）。
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, UploadFile, File as FileForm
from fastapi.responses import HTMLResponse, Response

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.alerts import registry, reports, workflow_builder
from app.modules.alerts import card_images, device_catalog
from app.modules.alerts.schemas import AdviceInput, AlertRuleInput, DeviceInput, PointInput, ProblemAction
from app.modules.alerts.workflow_schemas import AlertWorkflowInput, AlertWorkflowView

router = APIRouter(prefix="/api/v1/alerts", tags=["设备预警与设备台账"])
Context = Annotated[AuthContext, Depends(get_current_context)]
Limit = Annotated[int, Query(ge=1, le=200, description="每页条数")]
Offset = Annotated[int, Query(ge=0, le=100000, description="已授权结果的分页偏移")]
Keyword = Annotated[str, Query(max_length=200, description="按名称检索")]
Version = Annotated[int, Query(ge=1, description="读取到的记录版本，防止删除他人的新修改")]


@router.get("/device-catalogue", response_model=Envelope[dict], operation_id="equipment_device_catalogue", summary="按业务分页查看设备卡片")
def device_cards(ctx: Context, domain: Literal["alert", "quality"] = "alert", limit: Limit = 12,
                 offset: Offset = 0, keyword: Keyword = ""):
    """需要 devices.view 与数据源读取权限；按业务域返回已授权设备卡片，总数与分页同口径。"""
    return Envelope(data=device_catalog.catalogue(ctx, domain=domain, limit=limit, offset=offset, keyword=keyword))


@router.post("/card-images/{kind}/{target_id}", response_model=Envelope[dict], operation_id="business_card_image_upload", summary="可选上传设备或质检产品图片")
def upload_card_image(kind: Literal["device", "product"], target_id: str, ctx: Context,
                      version: Annotated[int, Query(ge=0)] = 0, file: UploadFile = FileForm(...)):
    """需要目标对象编辑权限与 files.create/view；上传即压缩为 WEBP 并保存新版本。

    ``version`` 传前端已读到的版本（从未上传过传 0），版本过期返回 409；
    只读取 ``MAX_IMAGE_BYTES + 1`` 字节用于判超限，超限由图片校验统一拒绝。
    """
    card_images.authorize_target(ctx, kind, target_id, edit=True)
    return Envelope(data=card_images.save(ctx, kind, target_id, file.file.read(card_images.MAX_IMAGE_BYTES + 1), version))


@router.delete("/card-images/{kind}/{target_id}", response_model=Envelope[dict], operation_id="business_card_image_remove", summary="移除卡片图片并恢复默认图")
def remove_card_image(kind: Literal["device", "product"], target_id: str, ctx: Context, version: Version):
    """需要目标对象编辑权限；解除图片关联（文件保留），版本不匹配返回 409。"""
    return Envelope(data=card_images.remove(ctx, kind, target_id, version))


@router.get("/card-images/{kind}/{target_id}/content", response_class=Response, operation_id="business_card_image_content", summary="授权读取设备或产品卡片图片")
def card_image_content(kind: Literal["device", "product"], target_id: str, ctx: Context):
    """需要目标对象查看权限；返回已校验大小与哈希的 WEBP 字节。

    响应头固定 ``no-store`` + ``nosniff``：图片属业务数据，不允许中间缓存或被浏览器猜类型。
    """
    return Response(card_images.content(ctx, kind, target_id), media_type="image/webp",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/devices", response_model=Envelope[list[dict]], operation_id="equipment_devices_list", summary="查询共享设备台账")
def devices(ctx: Context, limit: Limit = 50, offset: Offset = 0, keyword: Keyword = ""):
    """需要 devices.view；预警与质检共用设备，按工作区、动作数据范围及数据源权限分页。"""
    return Envelope(data=registry.list_records(ctx, "devices", limit=limit, offset=offset, keyword=keyword))


@router.post("/devices", status_code=201, response_model=Envelope[dict], operation_id="equipment_devices_create", summary="新增预警或质检设备")
def create_device(payload: DeviceInput, ctx: Context):
    """需要 devices.create 和所选数据源读取权限；保存不请求设备、不自动启用调度。"""
    return Envelope(data=registry.save_record(ctx, "devices", payload))


@router.get("/devices/{record_id}", response_model=Envelope[dict], operation_id="equipment_devices_get", summary="读取设备配置")
def get_device(record_id: str, ctx: Context):
    """需要 devices.view；只返回数据源标识，不返回连接密码或令牌。"""
    return Envelope(data=registry.record_view(ctx, "devices", registry.get_record(ctx, "devices", record_id)))


@router.put("/devices/{record_id}", response_model=Envelope[dict], operation_id="equipment_devices_update", summary="修改设备台账或运行状态")
def update_device(record_id: str, payload: DeviceInput, ctx: Context):
    """需要 devices.edit；必须携带 version。停用或维护后禁止新预警，不改历史设备快照。"""
    return Envelope(data=registry.save_record(ctx, "devices", payload, record_id))


@router.delete("/devices/{record_id}", response_model=Envelope[MutationResult], operation_id="equipment_devices_delete", summary="移除设备并保留历史")
def remove_device(record_id: str, ctx: Context, version: Version):
    """需要 devices.delete；软删除，保留测点、规则、报告的引用与历史，不删除来源数据。"""
    registry.remove_record(ctx, "devices", record_id, version)
    return Envelope(data=MutationResult(id=record_id, deleted=True))


@router.get("/points", response_model=Envelope[list[dict]], operation_id="equipment_points_list", summary="查询物联网测点")
def points(ctx: Context, limit: Limit = 50, offset: Offset = 0, keyword: Keyword = "", deviceId: str = ""):
    """需要 devices.view；可指定设备过滤，额外检查父设备及其来源权限。"""
    return Envelope(data=registry.list_records(ctx, "points", limit=limit, offset=offset, keyword=keyword, device_id=deviceId))


@router.post("/points", status_code=201, response_model=Envelope[dict], operation_id="equipment_points_create", summary="新增设备测点")
def create_point(payload: PointInput, ctx: Context):
    """需要 devices.create；设备内测点号唯一，字段名对应真实采集字段，保存不会读取设备。"""
    return Envelope(data=registry.save_record(ctx, "points", payload))


@router.get("/points/{record_id}", response_model=Envelope[dict], operation_id="equipment_points_get", summary="读取测点配置")
def get_point(record_id: str, ctx: Context):
    """需要 devices.view；采集间隔是台账配置，不能据此推断后台定时任务已经启动。"""
    return Envelope(data=registry.record_view(ctx, "points", registry.get_record(ctx, "points", record_id)))


@router.put("/points/{record_id}", response_model=Envelope[dict], operation_id="equipment_points_update", summary="修改设备测点")
def update_point(record_id: str, payload: PointInput, ctx: Context):
    """需要 devices.edit 和 version；不能把现有测点移动到另一台设备。"""
    return Envelope(data=registry.save_record(ctx, "points", payload, record_id))


@router.delete("/points/{record_id}", response_model=Envelope[MutationResult], operation_id="equipment_points_delete", summary="移除测点")
def remove_point(record_id: str, ctx: Context, version: Version):
    """需要 devices.delete；软删除测点，不修改历史采集数据或执行结果。"""
    registry.remove_record(ctx, "points", record_id, version)
    return Envelope(data=MutationResult(id=record_id, deleted=True))


@router.get("/advice", response_model=Envelope[list[dict]], operation_id="alerts_advice_list", summary="查询专家经验与预案")
def advice(ctx: Context, limit: Limit = 50, offset: Offset = 0, keyword: Keyword = ""):
    """需要 alerts.view；按当前用户的数据范围分页，不包含其他工作区的工况知识。"""
    return Envelope(data=registry.list_records(ctx, "advice", limit=limit, offset=offset, keyword=keyword))


@router.post("/advice", status_code=201, response_model=Envelope[dict], operation_id="alerts_advice_create", summary="新增专家经验")
def create_advice(payload: AdviceInput, ctx: Context):
    """需要 alerts.create；录入工况、告警内容和处置预案，不自动执行其文本内容。"""
    return Envelope(data=registry.save_record(ctx, "advice", payload))


@router.get("/advice/{record_id}", response_model=Envelope[dict], operation_id="alerts_advice_get", summary="读取专家经验")
def get_advice(record_id: str, ctx: Context):
    """需要 alerts.view 及该经验的资源范围；文本只作为业务知识返回。"""
    return Envelope(data=registry.record_view(ctx, "advice", registry.get_record(ctx, "advice", record_id)))


@router.put("/advice/{record_id}", response_model=Envelope[dict], operation_id="alerts_advice_update", summary="修改专家经验")
def update_advice(record_id: str, payload: AdviceInput, ctx: Context):
    """需要 alerts.edit 与 version；不会改写已被规则修订固化的旧预案。"""
    return Envelope(data=registry.save_record(ctx, "advice", payload, record_id))


@router.delete("/advice/{record_id}", response_model=Envelope[MutationResult], operation_id="alerts_advice_delete", summary="移除专家经验")
def remove_advice(record_id: str, ctx: Context, version: Version):
    """需要 alerts.delete；软删除并保留原规则、报告的经验快照。"""
    registry.remove_record(ctx, "advice", record_id, version)
    return Envelope(data=MutationResult(id=record_id, deleted=True))


@router.get("/rules", response_model=Envelope[list[dict]], operation_id="alerts_rules_list", summary="查询设备预警策略")
def rules(ctx: Context, limit: Limit = 50, offset: Offset = 0, keyword: Keyword = "", deviceId: str = ""):
    """需要 alerts.view；策略引用的设备也必须可读，规则由专用工作流节点执行。"""
    return Envelope(data=registry.list_records(ctx, "rules", limit=limit, offset=offset, keyword=keyword, device_id=deviceId))


@router.post("/rules", status_code=201, response_model=Envelope[dict], operation_id="alerts_rules_create", summary="创建固定版本预警策略")
def create_rule(payload: AlertRuleInput, ctx: Context):
    """需要 alerts.create；检查所有表达式，固化首个规则及专家经验快照，不自动执行。"""
    return Envelope(data=registry.save_record(ctx, "rules", payload))


@router.get("/rules/{record_id}", response_model=Envelope[dict], operation_id="alerts_rules_get", summary="读取预警策略")
def get_rule(record_id: str, ctx: Context):
    """需要 alerts.view 和设备读取权限；返回最新编辑版及其版本号。"""
    return Envelope(data=registry.record_view(ctx, "rules", registry.get_record(ctx, "rules", record_id)))


@router.put("/rules/{record_id}", response_model=Envelope[dict], operation_id="alerts_rules_update", summary="保存预警策略新修订")
def update_rule(record_id: str, payload: AlertRuleInput, ctx: Context):
    """需要 alerts.edit 与 version；创建新修订，已发布工作流固定引用的旧修订不会被替换。停用影响后续所有修订执行。"""
    return Envelope(data=registry.save_record(ctx, "rules", payload, record_id))


@router.delete("/rules/{record_id}", response_model=Envelope[MutationResult], operation_id="alerts_rules_delete", summary="移除预警策略")
def remove_rule(record_id: str, ctx: Context, version: Version):
    """需要 alerts.delete；保留所有修订和报告，阻止新执行，不删除问题记录。"""
    registry.remove_record(ctx, "rules", record_id, version)
    return Envelope(data=MutationResult(id=record_id, deleted=True))


@router.get("/rules/{record_id}/revisions", response_model=Envelope[list[dict]], operation_id="alerts_rules_revisions", summary="读取预警策略修订")
def rule_revisions(record_id: str, ctx: Context):
    """需要 alerts.view；返回最近 100 个仍有来源权限的固定修订，供工作流显式选择。"""
    return Envelope(data=registry.list_rule_revisions(ctx, record_id))


@router.post("/workflows", status_code=201, response_model=Envelope[AlertWorkflowView],
             operation_id="alerts_workflows_create", summary="配置并发布完整设备预警流程")
def create_alert_workflow(payload: AlertWorkflowInput, ctx: Context):
    """需要 alerts.publish、workflows.create/publish 及全部设备、测点和来源读取权限。用户手动选择测点与固定来源版本；HTTP 复用已保存的单条/批量请求体和 JSONPath，数据库仅在创建时读取结构并校验只读 SQL，不读取业务行。接口一次生成字段提取、加减乘除、策略判定、可选通知/写回/调度节点并发布；任何外部请求或写入只会在后续明确运行时发生。"""
    return Envelope(data=workflow_builder.create_published(ctx, payload))


@router.get("/reports", response_model=Envelope[list[dict]], operation_id="alerts_reports_list", summary="查询实际预警报告")
def report_list(ctx: Context, limit: Limit = 50, offset: Offset = 0, deviceId: str = "", ruleId: str = ""):
    """需要 alerts.view 及工作流、设备、来源权限；技术运行状态与业务判定分开展示，无数据不视为正常。"""
    return Envelope(data=reports.list_reports(ctx, limit=limit, offset=offset, device_id=deviceId, rule_id=ruleId))


@router.get("/reports/{report_id}", response_model=Envelope[dict], operation_id="alerts_reports_get", summary="读取报告与原始判定证据")
def report_detail(report_id: str, ctx: Context):
    """需要 alerts.view；返回数据、逐项判定、设备/规则快照和执行来源，读取不会再次调用设备。"""
    return Envelope(data=reports.report_view(ctx, reports.get_report(ctx, report_id), detail=True))


@router.get("/reports/{report_id}/export", response_class=HTMLResponse, operation_id="alerts_reports_export", summary="下载可独立查看的预警报告")
def report_export(report_id: str, ctx: Context):
    """需要 alerts.export 及证据链读取权限；导出 HTML 已转义来源文本，不重新运行设备或模型。"""
    content = reports.export_report(ctx, report_id)
    return HTMLResponse(content, headers={"Content-Disposition": f'attachment; filename="alert-report-{report_id}.html"',
                                         "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


@router.get("/problems", response_model=Envelope[list[dict]], operation_id="alerts_problems_list", summary="查询预警问题列表")
def problem_list(ctx: Context, limit: Limit = 50, offset: Offset = 0, reportId: str = "",
                 status: Annotated[str, Query(pattern="^(|open|acknowledged|resolved|ignored)$", description="处置状态筛选")] = ""):
    """需要 alerts.view；按报告或人工状态筛选，处理问题不会改写报告的原始问题数。"""
    return Envelope(data=reports.list_problems(ctx, limit=limit, offset=offset, report_id=reportId, status=status))


@router.post("/problems/{problem_id}/actions", response_model=Envelope[dict], operation_id="alerts_problems_handle", summary="确认、解决、忽略或重开问题")
def problem_action(problem_id: str, payload: ProblemAction, ctx: Context):
    """需要 alerts.edit；必须填写处置理由与当前版本，追加不可覆盖的处置记录，原始判定保持不变。"""
    return Envelope(data=reports.handle_problem(ctx, problem_id, payload))


@router.get("/problems/{problem_id}/actions", response_model=Envelope[list[dict]], operation_id="alerts_problems_actions", summary="查看问题处置历史")
def problem_history(problem_id: str, ctx: Context, limit: Limit = 50, offset: Offset = 0):
    """需要 alerts.view；按时间分页列出处理人、原状态、新状态和理由，历史不能从此接口修改。"""
    return Envelope(data=reports.problem_events(ctx, problem_id, limit=limit, offset=offset))
