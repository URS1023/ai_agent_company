"""Documented workflow graph, version, publish, execution and history interfaces.

中文补充：``/api/v1/workflows`` 路由层。这里只做三件事——声明 HTTP 语义（方法/路径/状态码/
权限/摘要）、把请求交给 service、把异常交给统一错误处理；业务规则一律不写在路由里。

约定：
- 每个路由的 docstring 说明"需要什么权限 + 有什么副作用 + 明确不做什么"，
  这些说明同时被 OpenAPI 文档使用，属于对外契约，修改行为时必须同步更新。
- 读接口（catalog/value-sources/validate/runs/report 等）都强调"不执行"：
  校验不发送 HTTP/SQL/模型调用，报告查询不重跑流程。
- 写接口一律要求显式版本号或唯一键，避免并发覆盖与重复执行。
"""
from typing import Annotated
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from app.common.authorization import require_permission
from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.workflows import execution, generation, service
from app.modules.workflows.catalog import node_catalog
from app.modules.workflows.graph import authorize_graph, inspect_graph
from app.modules.workflows.value_schemas import ReadValuesConfig
from app.modules.workflows.schemas import (
    DeviceWorkflowEntryInput, DeviceWorkflowEntryView, ExecutionControlInput, ExecutionSummary,
    ExecutionView, FlowGraph, RestoreInput, RunInput, VersionInput, WorkflowGenerateInput,
    WorkflowInput, WorkflowView,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["共享可视化工作流"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get('/business-results', response_model=Envelope[dict], operation_id='workflows_business_results',
            summary='查询已保存的设备和质检业务报告',
            description='按工作流、业务域及资源读取权限分页查询已保存报告，不触发外部查询或重新运行工作流。')
def business_results(ctx: Context, kind: Literal['quality', 'alert'] = 'quality',
                     reference: str = Query('', max_length=26), keyword: str = Query('', max_length=200),
                     start: str = '', end: str = '', limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
    """需要 quality.view 或 alerts.view 以及 workflows.view；只读已保存报告。

    ``reference`` 同时匹配执行 id、统一运行 id 与工作流 id（见 business_results 模块说明），
    传工作流 id 时返回该流程最近的一批报告。不触发外部查询，也不重新运行工作流。
    """
    from app.modules.workflows.business_results import list_reports
    return Envelope(data=list_reports(ctx, kind=kind, reference=reference, keyword=keyword,
                                     start=start, end=end, limit=limit, offset=offset))


@router.get('/business-results/{execution_id}', response_model=Envelope[dict], operation_id='workflows_business_result',
            summary='阅读已保存的完整业务报告和逐项结果',
            description='验证工作流、业务域及资源读取权限后返回已保存报告和逐项结果；无权访问时不暴露记录。')
def business_result(ctx: Context, execution_id: str, kind: Literal['quality', 'alert'] = 'quality'):
    """需要 quality.view 或 alerts.view 以及 workflows.view；返回单份已保存报告与逐项结果。

    资源权限不足时按"没有找到"处理，不暴露记录是否存在；不重跑任何规则或模型。
    """
    from app.modules.workflows.business_results import read_report
    return Envelope(data=read_report(ctx, execution_id, kind=kind))


@router.get("/catalog", response_model=Envelope[list[dict]], operation_id="workflows_node_catalog", summary="查看节点目录与配置说明")
def catalog(ctx: Context):
    """需要 workflows.view；包含来源、迁移状态和可执行节点的完整配置 Schema。迁移中节点不能发布。"""
    require_permission(ctx, "workflows.view")
    return Envelope(data=node_catalog())


@router.get("/value-sources", response_model=Envelope[list[dict]], operation_id="workflows_value_sources", summary="选择读取值节点的数据集与字段", description="返回当前用户有权使用的数据集、字段与参数，不返回 SQL 或连接凭据。")
def value_sources(ctx: Context, datasourceId: str):
    """需要 workflows.view；返回该数据源下可选的数据集、字段与参数。

    不返回 SQL 原文或连接凭据（见 value_nodes.source_options）。
    """
    require_permission(ctx, "workflows.view")
    from app.modules.workflows.value_nodes import source_options
    return Envelope(data=source_options(ctx, datasourceId))


@router.post("/value-preview", response_model=Envelope[dict], operation_id="workflows_value_preview", summary="预览选表读取的前20条实际数据")
def value_preview(payload: ReadValuesConfig, ctx: Context):
    """需要 workflows.execute 与数据源读取权限；按选表、字段和筛选只读预览，最多20行，不接收用户编写的 SQL。"""
    require_permission(ctx, "workflows.execute")
    from app.errors import ValidationError
    from app.modules.workflows.table_values import read_table_values
    if payload.read_mode != "table":
        raise ValidationError("此预览用于直接选表读取")
    return Envelope(data=read_table_values(ctx, payload, {}, preview=True))


@router.post("/validate", response_model=Envelope[dict], operation_id="workflows_graph_validate", summary="检查流程图和字段配置")
def validate(payload: FlowGraph, ctx: Context):
    """需要 workflows.view 与来源读取权限；只检查图、输入映射和配置，不执行 HTTP、SQL 或模型调用。"""
    require_permission(ctx, "workflows.view")
    result = inspect_graph(payload)
    authorize_graph(ctx, payload)
    return Envelope(data=result)


@router.post("/generate", response_model=Envelope[dict], operation_id="workflows_ai_generate", summary="根据业务目标生成可编辑工作流草稿")
def generate(payload: WorkflowGenerateInput, ctx: Context):
    """需要 workflows.create；仅把用户明确勾选且有权读取的数据源目录交给模型。结构输出自动校正并重试，模型仍不可用时返回安全可编辑草稿；不会自动保存、发布、运行、请求外部接口或补造资源标识。"""
    return Envelope(data=generation.generate(ctx, payload))


@router.get("", response_model=Envelope[list[WorkflowView]], operation_id="workflows_list", summary="分页查询当前授权工作流")
def list_flows(ctx: Context, limit: int = Query(50, ge=1, le=200, description="每页条数"),
               offset: int = Query(0, ge=0, description="分页偏移"), keyword: str = Query("", max_length=200, description="名称关键词"),
               domain: Literal["general", "analysis", "alert", "quality"] | None = Query(None, description="可选业务域；在授权分页前过滤")):
    """按 workflows.view 的工作区、创建人、资源授权、数据源权限和可选业务域过滤。未完成草稿仍可编辑。"""
    return Envelope(data=service.list_workflows(ctx, limit=limit, offset=offset, keyword=keyword, domain=domain))


@router.post("", status_code=201, response_model=Envelope[WorkflowView], operation_id="workflows_create", summary="创建可复用工作流草稿")
def create(payload: WorkflowInput, ctx: Context):
    """需要 workflows.create；保存图及不可变修订，不自动发布或执行。禁止未知节点、重复标识和明文凭据配置。"""
    return Envelope(data=service.save_workflow(ctx, payload))


@router.post("/device-entry", response_model=Envelope[DeviceWorkflowEntryView],
             operation_id="workflows_device_entry", summary="选择设备后直接打开其业务工作流")
def device_entry(payload: DeviceWorkflowEntryInput, ctx: Context):
    """读取已有绑定；首次进入需要 workflows.create 并只创建可编辑草稿，不发布、不运行、不请求设备。"""
    return Envelope(data=service.ensure_device_workflow(ctx, payload))


@router.get("/{workflow_id}", response_model=Envelope[WorkflowView], operation_id="workflows_get", summary="读取工作流草稿与发布状态")
def get(workflow_id: str, ctx: Context):
    """需要 workflows.view 及其数据源权限；返回最新编辑版本与独立的已发布修订号。"""
    return Envelope(data=service.workflow_view(service.get_workflow(ctx, workflow_id)))


@router.put("/{workflow_id}", response_model=Envelope[WorkflowView], operation_id="workflows_update", summary="按版本保存流程草稿")
def update(workflow_id: str, payload: WorkflowInput, ctx: Context):
    """需要 workflows.edit；必须提交 version，过期返回 409。保存新修订不会覆盖正在使用的发布版本。"""
    return Envelope(data=service.save_workflow(ctx, payload, workflow_id))


@router.delete("/{workflow_id}", response_model=Envelope[MutationResult], operation_id="workflows_delete", summary="移除流程并保留追溯")
def delete(workflow_id: str, ctx: Context, version: int = Query(ge=1, description="当前编辑版本")):
    """需要 workflows.delete；软删除，保留修订与实际执行记录，不删除外部数据。"""
    service.remove(ctx, workflow_id, version)
    return Envelope(data=MutationResult(id=workflow_id, deleted=True))


@router.post("/{workflow_id}/publish", response_model=Envelope[WorkflowView], operation_id="workflows_publish", summary="校验并发布固定修订")
def publish(workflow_id: str, payload: VersionInput, ctx: Context):
    """需要 workflows.publish；检查可达性、分支、字段配置、来源权限和状态，不发送业务请求。"""
    return Envelope(data=service.change_status(ctx, workflow_id, payload.version, publish=True))


@router.post("/{workflow_id}/disable", response_model=Envelope[WorkflowView], operation_id="workflows_disable", summary="停用正式流程")
def disable(workflow_id: str, payload: VersionInput, ctx: Context):
    """需要 workflows.publish；停止后续正式运行，保留既有记录；不宣称中断已经开始的外部请求。"""
    return Envelope(data=service.change_status(ctx, workflow_id, payload.version, publish=False))


@router.get("/{workflow_id}/revisions", response_model=Envelope[list[dict]], operation_id="workflows_revisions_list", summary="查看最近一百个不可变修订")
def revisions(workflow_id: str, ctx: Context):
    """需要 workflows.view；被撤销数据源权限的历史快照不可读取。恢复另建修订，不修改旧版本。"""
    return Envelope(data=service.list_revisions(ctx, workflow_id))


@router.post("/{workflow_id}/restore", response_model=Envelope[WorkflowView], operation_id="workflows_revision_restore", summary="将历史修订恢复为新草稿")
def restore(workflow_id: str, payload: RestoreInput, ctx: Context):
    """需要 workflows.edit 与当前 version；保持正式发布修订不变，恢复后可再次编辑和发布。"""
    return Envelope(data=service.restore(ctx, workflow_id, payload.version, payload.revision))


@router.get("/{workflow_id}/published-revision", response_model=Envelope[dict],
            operation_id="workflows_published_revision", summary="读取正式运行的固定发布输入契约")
def published_revision(workflow_id: str, ctx: Context):
    """需要 workflows.view；直接读取当前发布修订及图资源权限，不受历史列表数量限制。"""
    return Envelope(data=service.published_revision_view(ctx, workflow_id))


@router.get("/{workflow_id}/export", response_model=Envelope[dict], operation_id="workflows_export", summary="导出可迁移的流程定义")
def export(workflow_id: str, ctx: Context):
    """需要 workflows.export；导出名称、图和业务域，不包含运行输入、源凭据或历史结果。导入以创建草稿接口重新校验资源授权。"""
    row = service.get_workflow(ctx, workflow_id, "workflows.export")
    return Envelope(data={"schemaVersion": 1, "workflow": service.snapshot(row)})


@router.post("/{workflow_id}/runs", response_model=Envelope[ExecutionView], operation_id="workflows_execute", summary="执行已发布流程或试运行草稿",
             responses={202: {"model": Envelope[ExecutionView], "description": "相同任务仍在运行，不重复执行"},
                        502: {"model": Envelope[ExecutionView], "description": "执行失败，返回持久化节点状态和追溯标识"}})
def run(workflow_id: str, payload: RunInput, ctx: Context, response: Response):
    """需要 workflows.execute；草稿另需 edit。唯一键防止重复设备请求。HTTP 自动采集需源授权；失败不写假结果。"""
    result = execution.start(ctx, workflow_id, payload)
    # 状态码映射是接口契约的一部分：仍在运行/等待（含已受理取消请求）返回 202，
    # 正常结束（完成或已取消）返回 200，失败返回 502 —— 失败时仍返回持久化的节点状态与追溯标识。
    response.status_code = 202 if result.status in {"running", "waiting", "cancel_requested"} else 200 if result.status in {"completed", "cancelled"} else 502
    return Envelope(success=result.status in {"completed", "waiting", "cancelled"}, data=result, message=result.errorMessage)


@router.get("/{workflow_id}/runs", response_model=Envelope[list[ExecutionSummary]], operation_id="workflows_runs_list", summary="分页查看真实流程运行记录")
def runs(workflow_id: str, ctx: Context, limit: int = Query(50, ge=1, le=200, description="每页条数"),
         offset: int = Query(0, ge=0, description="分页偏移"),
         keyword: str = Query("", max_length=200, description="运行 ID、修订或错误关键词"),
         status: Literal["all", "completed", "failed", "active", "cancelled"] = Query("all"),
         days: int = Query(0, ge=0, le=90, description="最近自然日，包含今天；0 为全部时间"),
         time_zone: str = Query("UTC", alias="timeZone", min_length=1, max_length=80, description="浏览器 IANA 时区")):
    """需要 workflows.view；按当前权限过滤工作流、执行记录和快照中的数据源，不重新执行流程。"""
    return Envelope(data=execution.list_executions(ctx, workflow_id, limit=limit, offset=offset,
                    keyword=keyword, status=status, days=days, time_zone=time_zone))


@router.get("/{workflow_id}/monitor", response_model=Envelope[dict], operation_id="workflows_monitor", summary="查看工作流真实运行指标")
def monitor(workflow_id: str, ctx: Context,
            days: int = Query(7, ge=1, le=90, description="统计最近自然日，包含今天"),
            time_zone: str = Query("UTC", alias="timeZone", min_length=1, max_length=80, description="浏览器 IANA 时区")):
    """需要 workflows.view；仅聚合当前仍有权查看的运行、用户、耗时、模型用量和实际计费。"""
    return Envelope(data=execution.monitor(ctx, workflow_id, days=days, time_zone=time_zone))


@router.get("/{workflow_id}/runs/{execution_id}", response_model=Envelope[ExecutionView], operation_id="workflows_runs_get", summary="读取节点结果与问题列表")
def run_detail(workflow_id: str, execution_id: str, ctx: Context):
    """需要 workflows.view 及来源读取权限；返回执行快照、逐项判定、问题和查询标识，已删除流程的历史仍受权限保护。"""
    return Envelope(data=execution.get_execution(ctx, workflow_id, execution_id))


@router.post("/{workflow_id}/runs/{execution_id}/resume", response_model=Envelope[ExecutionView], operation_id="workflows_runs_resume", summary="恢复等待中的工作流")
def resume_run(workflow_id: str, execution_id: str, payload: ExecutionControlInput, ctx: Context, response: Response):
    """需要 workflows.execute；按执行版本与唯一键恢复提问或到期延迟节点，不重跑已经完成的 HTTP、SQL、工具和写回节点。"""
    result = execution.resume(ctx, workflow_id, execution_id, payload)
    # 恢复后仍在运行/再次进入等待返回 202；completed/cancelled 返回 200；failed 返回 502。
    response.status_code = 202 if result.status in {"running", "waiting"} else 200 if result.status in {"completed", "cancelled"} else 502
    return Envelope(success=result.status in {"completed", "waiting", "cancelled"}, data=result, message=result.errorMessage)


@router.post("/{workflow_id}/runs/{execution_id}/cancel", response_model=Envelope[ExecutionView], operation_id="workflows_runs_cancel", summary="取消工作流运行")
def cancel_run(workflow_id: str, execution_id: str, payload: ExecutionControlInput, ctx: Context, response: Response):
    """需要 workflows.execute；等待任务立即取消，运行中任务在当前不可撤回节点完成后停止，不伪装已撤回外部请求。"""
    result = execution.cancel(ctx, workflow_id, execution_id, payload)
    response.status_code = 202 if result.status == "cancel_requested" else 200
    return Envelope(success=True, data=result)
