"""Normal quality-form execution delegates to the same authorized, idempotent workflow engine.

职责：质检"普通运行页"的服务层 —— 提交一次质检运行，并回读该运行的逐样本结果。

位置与依赖：被 ``quality.router`` 调用；执行本身委托给共享工作流引擎
（``workflows.execution.start``），因此与手工运行流程享受同一套授权、幂等与审计；
运行表单以 ``__qualityForm`` 键随执行输入保存，回读时据此还原。

关键约束：
- 提交前**预检全部内容**（含每个设备标准组的图片与展开后的请求参数），
  任何一项不通过都不会发出检测请求、也不会启动流程；
- 不修改已发布流程的图；不自动重试有副作用的操作；
- 必须与运行方案（``build_plan``）的版本、发布修订、设备标准组**完全一致**，
  漏组或多组都拒绝（防止漏检）；
- 有目标表写入时必须显式 ``confirmWrites``；
- 幂等键只用于"不确定提交时重查既有运行"，**不会自动重新检测**。
"""
import time

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, ValidationError
from app.modules.collection.parameters import bind_parameters
from app.modules.collection.sources import source_config
from app.modules.collection.transport import validate_body_secrets
from app.modules.quality.detector_inputs import detector_parameters
from app.modules.quality.detection import validate_detector
from app.modules.quality.inspections import get_inspection, inspection_view
from app.modules.quality.models import QualityInspection
from app.modules.quality.run_plans import build_plan
from app.modules.quality.registry import validate_inspection
from app.modules.quality.run_schemas import QualityRunInput, QualityRunView
from app.modules.quality.schemas import DetectorConfig, QualityConfig
from app.modules.workflows.execution import execution_summary, get_execution, start
from app.modules.workflows.expressions import RuleBudget
from app.modules.workflows.graph import authorize_graph, validate_json
from app.modules.workflows.models import WorkflowExecution
from app.modules.workflows.schemas import RunInput


def result(ctx: AuthContext, workflow_id: str, execution_id: str) -> QualityRunView:
    """Reload stored batch/report metadata without sending any request to the detector.

    （中文补充）回读一次质检运行的结果：从执行输入里取出 ``__qualityForm`` 还原表单，
    按图中的 ``qc-evaluate`` 节点补设备/模板名称（每个节点都会复核当前权限），
    再把已生成的质检报告按 ``(节点, 样本)`` 对齐成 ``samples`` 列表。
    **未生成报告的样本显式标记 ``outcome='not_generated'``**，不会从列表中消失。
    需要 ``quality.view``；异常：该执行不是普通质检表单创建的（缺 ``__qualityForm``）时抛
    ``ValidationError``，提示去工作流运行记录查看。只读，不向检测服务发任何请求。
    """
    require_permission(ctx, "quality.view")
    execution = get_execution(ctx, workflow_id, execution_id)
    form = execution.inputs.get("__qualityForm")
    if not isinstance(form, dict):
        raise ValidationError("此运行不是普通质检表单创建，请在工作流运行记录查看")
    payload = QualityRunInput.model_validate(form)
    labels = {}
    for node in execution.snapshot["graph"]["nodes"]:
        if node["kind"] == "qc-evaluate":
            standard, equipment = validate_inspection(ctx, QualityConfig.model_validate(node["config"]))
            labels[node["id"]] = {"deviceName": equipment.name, "templateName": standard.snapshot["name"],
                                  "templateRevision": standard.revision}
    reports = ctx.db.scalars(select(QualityInspection).where(QualityInspection.workspace_id == ctx.workspace_id,
                            QualityInspection.execution_id == execution_id)).all()
    by_sample = {}
    for row in reports:
        view = inspection_view(ctx, get_inspection(ctx, row.id))
        by_sample[(row.node_id, row.sample_id)] = {"inspectionId": row.id, "outcome": row.outcome,
            "effectiveOutcome": view.effectiveOutcome, "deviceId": row.device_id, "status": row.status,
            "deviceName": row.snapshot["device"]["name"], "templateName": row.snapshot["template"]["name"]}
    samples = [{"nodeId": group.nodeId, "sampleId": sample.sampleId, **labels.get(group.nodeId, {}),
                **by_sample.get((group.nodeId, sample.sampleId), {"inspectionId": None, "outcome": "not_generated"})}
               for group in payload.groups for sample in group.samples]
    persisted = ctx.db.get(WorkflowExecution, execution_id)
    return QualityRunView(execution=execution_summary(persisted), samples=samples)


def submit(ctx: AuthContext, workflow_id: str, payload: QualityRunInput) -> QualityRunView:
    """Preflight the complete form before HTTP; never modify published graphs or retry side effects.

    （中文补充）需要 ``quality.execute`` + ``workflows.execute``。执行顺序：
    结构/敏感字段校验 → 幂等键查重（同键不同参数报 409，同键同参数返回既有运行结果）→
    ``build_plan`` 取运行方案 → 校验版本/发布修订/设备标准组集合一致 → 有写入时要求确认 →
    ``authorize_graph(execute=True)`` → 逐组展开样本参数并**逐个设备完成图片与请求校验**
    （外观组不接受无关图片关联）→ 启动共享工作流。
    副作用：启动一次工作流执行（可能触发真实 HTTP 检测与目标表写入）；
    任何预检失败都发生在启动之前，不会留下半成品运行。返回 ``result`` 视图。
    """
    require_permission(ctx, "quality.execute")
    require_permission(ctx, "workflows.execute")
    canonical = payload.model_dump(mode="json")
    validate_json(canonical)
    validate_body_secrets(canonical)
    previous = ctx.db.scalar(select(WorkflowExecution).where(WorkflowExecution.workspace_id == ctx.workspace_id,
        WorkflowExecution.owner_user_id == ctx.user_id, WorkflowExecution.idempotency_key == payload.idempotencyKey))
    if previous:
        if previous.workflow_id != workflow_id or previous.input_json.get("__qualityForm") != canonical:
            raise ConflictError("此运行键已用于其他参数；请查看原运行，不能覆盖或自动重发")
        return result(ctx, workflow_id, previous.id)
    plan, graph = build_plan(ctx, workflow_id)
    if plan.version != payload.version or plan.publishedRevision != payload.publishedRevision:
        raise ConflictError("工作流版本已变化，请重新加载并明确选择当前运行方案")
    if {item["nodeId"] for item in plan.groups} != {item.nodeId for item in payload.groups}:
        raise ValidationError("提交必须包含所选流程的全部设备标准组，不能遗漏或增加")
    if plan.writes and not payload.confirmWrites:
        raise ValidationError("请核对目标表和字段后明确确认实际写入；尚未发送检测请求")
    authorize_graph(ctx, graph, active=True, execute=True)
    submitted = {item.nodeId: item for item in payload.groups}
    inputs = {"__qualityForm": canonical}
    budget = RuleBudget(time.monotonic() + 30)
    for group in plan.groups:
        samples = submitted[group["nodeId"]].samples
        if not group["imageRequired"] and any(sample.imageFileId for sample in samples):
            raise ValidationError("此设备标准组不检测外观，请移除无关图片关联")
        for detector in group["detectors"]:
            config = DetectorConfig.model_validate({key: detector[key] for key in DetectorConfig.model_fields})
            rows = []
            for sample in samples:
                row = {config.sampleField: sample.sampleId}
                if detector["kind"] == "qc-appearance":
                    row[config.imageField] = sample.imageFileId
                rows.append(row)
            key = detector["inputKey"]
            if key in inputs:
                for old, new in zip(inputs[key], rows, strict=True):
                    if any(field in old and old[field] != value for field, value in new.items()):
                        raise ValidationError("检测节点样本字段映射冲突，请为其选择独立开始输入字段")
                    old.update(new)
            else:
                inputs[key] = rows
            # Fully validate every device's files and expanded request before any group starts.
            _, _, source = validate_detector(ctx, detector["kind"], config, active=True, execute=True)
            parameters, _ = detector_parameters(ctx, detector["kind"], config, rows, budget)
            bind_parameters(source_config(source), parameters)
    run = start(ctx, workflow_id, RunInput(version=payload.version, inputs=inputs, mode="published",
        idempotencyKey=payload.idempotencyKey, timeoutSeconds=payload.timeoutSeconds, confirmWrites=payload.confirmWrites))
    return result(ctx, workflow_id, run.id)
