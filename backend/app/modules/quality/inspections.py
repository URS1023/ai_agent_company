"""Immutable original inspections, append-only reviews and escaped report exports.

职责：质检记录（inspection）的核心服务 —— 创建记录（含逐项判定）、读取、人工复核、
归档与报告导出。

位置与依赖：被 ``quality.router``（REST）、``quality.batch``（逐样本节点）、
``quality.concessions``（让步来源）与质检工作流判定节点调用；判定委托给
``quality.assessment``（纯函数），证据委托给 ``quality.detection`` 与 ``quality.images``。

核心不变式：
- 记录的 ``results`` 是**原始逐项判定**，人工复核只追加 ``QualityReview``，
  有效结论（``effectiveOutcome``）在读取时按"最新复核覆盖对应指标"计算，原文永不改写；
- 提交幂等：``(工作区, 用户, requestKey)`` 唯一 + 摘要比对，重复提交返回原记录；
- 读取（``get_inspection``）会按**当时的引用链**复核当前权限：模板修订、设备（允许已删）、
  当时的采集数据源、引用的检测证据与图片、以及来源执行；
- 导出为自包含 HTML（全部插值转义），**不重新调用检测服务**。
"""
from copy import deepcopy
from hashlib import sha256
from html import escape
import json

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import ConflictError, ValidationError
from app.models import DataSource
from app.modules.alerts.registry import authorized_page, device, record_view
from app.modules.collection.transport import validate_body_secrets
from app.modules.quality.assessment import assess, overall
from app.modules.quality.detection import get_detection
from app.modules.quality.images import authorize_evidence_images, image_content
from app.modules.quality.models import QualityInspection, QualityReview
from app.modules.quality.registry import template_revision, validate_inspection
from app.modules.quality.schemas import InspectionInput, InspectionView, MetricView, Observation, ReviewInput
from app.modules.system.service_helpers import check_version, commit_change, flush_changes


def get_inspection(ctx: AuthContext, inspection_id: str, action: str = "view") -> QualityInspection:
    """Require current permissions for all actual, not just current, sources.

    （中文补充）按 ``quality.<action>`` 权限取记录，并复核其**全部实际来源**的当前权限：
    模板修订（含其指标）、设备（允许已删除）、记录快照里的旧数据源、引用的每个检测证据、
    证据图片、以及来源执行（若有）。任一失权即不可见，防止"设备改绑数据源/图片被收回"
    后旧记录成为绕过授权的旁路。只读。
    """
    row = owned_record(ctx, QualityInspection, inspection_id, "quality." + action)
    template_revision(ctx, row.template_id, row.snapshot["templateRevision"])
    device(ctx, row.device_id, deleted=True)
    old_source = row.snapshot["device"].get("datasourceId")
    if old_source:
        owned_record(ctx, DataSource, old_source, "datasources.view")
    for detection_id in row.snapshot["detectionIds"]:
        get_detection(ctx, detection_id)
    authorize_evidence_images(ctx, row.results)
    if row.execution_id:
        from app.modules.workflows.execution import get_execution
        from app.modules.workflows.models import WorkflowExecution
        execution = ctx.db.get(WorkflowExecution, row.execution_id)
        get_execution(ctx, execution.workflow_id, execution.id)
    return row


def inspection_view(ctx: AuthContext, row: QualityInspection) -> InspectionView:
    """Compute reviewed conclusion separately from the permanent machine/manual original.

    （中文补充）输出视图同时给出 ``outcome``（原始，不可变）与 ``effectiveOutcome``
    （按复核历史计算）。计算口径：复核按 ``(inspection_version, id)`` 排序，
    **同一指标取最后一次复核结论**，未复核的指标沿用原始判定；
    复核结论 ``retest`` 在汇总时按 ``review`` 处理（要求复检不等于合格）。
    若记录来自工作流，额外补 ``workflowId``、``fromQualityForm``（是否普通表单创建）与
    该执行的写库凭据（``writeOperations``）。只读。
    """
    events = ctx.db.scalars(select(QualityReview).where(QualityReview.workspace_id == ctx.workspace_id,
        QualityReview.inspection_id == row.id).order_by(QualityReview.inspection_version, QualityReview.id)).all()
    latest = {event.metric_id: event.decision for event in events}
    outcomes = [latest.get(item["metricId"], item["outcome"]) for item in row.results]
    writes = []
    workflow_id, from_form = None, False
    if row.execution_id:
        from app.modules.workflows.models import WorkflowExecution
        execution = ctx.db.get(WorkflowExecution, row.execution_id)
        workflow_id = execution.workflow_id
        from_form = isinstance(execution.input_json.get("__qualityForm"), dict)
        from app.modules.writeback.execution import for_execution
        writes = [item.model_dump(mode="json") for item in for_execution(ctx, row.execution_id)]
    return InspectionView(id=row.id, templateId=row.template_id, deviceId=row.device_id, sampleId=row.sample_id,
        outcome=row.outcome, effectiveOutcome=overall(["review" if item == "retest" else item for item in outcomes]),
        status=row.status, version=row.version, snapshot=row.snapshot, results=row.results,
        reviews=[{"id": event.id, "metricId": event.metric_id, "decision": event.decision, "note": event.note,
                  "actorId": event.owner_user_id, "version": event.inspection_version, "createTime": event.created_at.isoformat()} for event in events],
        executionId=row.execution_id, workflowId=workflow_id, fromQualityForm=from_form,
        nodeId=row.node_id, createTime=row.created_at.isoformat(), writeOperations=writes)


def _observations(ctx: AuthContext, payload: InspectionInput, metrics: dict) -> tuple[dict, dict]:
    """把人工录入与检测证据合并成 ``({metricId: Observation}, {metricId: 来源})``。

    约束（防止用错类别的证据顶替）：
    - 只接受模板内的指标，且同一指标不得重复提供（否则结论有歧义）；
    - 外观指标不得用数值代替缺陷检测，数值/成分指标不得用缺陷数组/图片代替实测值；
    - 检测证据必须属于所选设备、其指标版本必须与固定模板修订一致，
      且**必须含当前样本的唯一一条结果**（0 条或多条都拒绝）；
    - 带 ``imageFileId`` 的人工观察值会即时校验图片（含历史快照一致性）并写入来源快照。
    ``provenance`` 记录 ``manual``（含操作者）或 ``detector``（含检测标识/数据源/查询标识/图片），
    供报告区分人工与自动证据。异常：以上任一不满足抛 ``ValidationError``。
    """
    observations, provenance = {}, {}
    def add(observation, origin):
        metric_id = observation.metricId
        if metric_id not in metrics or metric_id in observations:
            raise ValidationError("提交包含模板外指标或重复指标，未保存歧义质检结果")
        category = metrics[metric_id].category
        if category == "appearance" and observation.value is not None:
            raise ValidationError("外观指标不能用数值代替缺陷检测")
        if category != "appearance" and (observation.defects is not None or observation.imageRef or observation.imageFileId):
            raise ValidationError("数值或成分指标不能用缺陷数组代替实测值")
        if observation.imageFileId:
            snapshot = origin.get("image")
            image, _ = image_content(ctx, observation.imageFileId, snapshot=snapshot)
            origin = {**origin, "image": image.model_dump(mode="json")}
        observations[metric_id], provenance[metric_id] = observation, origin
    for observation in payload.observations:
        add(observation, {"mode": "manual", "actorId": ctx.user_id})
    if len(set(payload.detectionIds)) != len(payload.detectionIds):
        raise ValidationError("检测证据标识不能重复")
    for detection_id in payload.detectionIds:
        detection = get_detection(ctx, detection_id)
        if detection.device_id != payload.deviceId:
            raise ValidationError("检测证据不属于所选设备")
        metric = metrics.get(detection.metric_id)
        if metric is None or detection.snapshot["metric"]["version"] != metric.version:
            raise ValidationError("检测指标版本与固定模板标准不一致，请选择对应模板修订")
        matches = [item for item in detection.results if item["sampleId"] == payload.sampleId]
        if len(matches) != 1:
            raise ValidationError("检测证据没有此样本的唯一结果；请检查样本标识")
        add(Observation.model_validate(matches[0]["observation"]), {"mode": "detector", "detectionId": detection.id,
            "datasourceId": detection.datasource_id, "queryId": detection.snapshot["queryId"], **({"image": matches[0]["image"]} if matches[0].get("image") else {})})
    return observations, provenance


def create_inspection(ctx: AuthContext, payload: InspectionInput, *, execution_id: str | None = None, node_id: str = "") -> QualityInspection:
    """Persist every template item, missing ones included, with idempotent submission.

    （中文补充）权限：人工录入需 ``quality.create``，工作流节点调用（带 ``execution_id``）需
    ``quality.execute``。流程：结构/敏感字段校验 → 幂等查重（同键不同参数 409，同键同参数返回原记录）
    → 校验模板修订与设备 → 合并观察值 → **对模板里的每个指标**逐项判定
    （未提供证据的指标也会生成 ``incomplete`` 行，不会从结果中消失）→ 落库。
    副作用：写 ``quality_inspections``（含模板/设备/检测引用快照）；人工录入路径写审计，
    工作流路径由引擎统一提交。返回记录行（调用方可用 ``inspection_view`` 转视图）。
    """
    from app.modules.workflows.graph import validate_json

    require_permission(ctx, "quality.execute" if execution_id else "quality.create")
    values = payload.model_dump(mode="json")
    validate_json(values)
    validate_body_secrets(values)
    digest = sha256(json.dumps({"payload": values, "executionId": execution_id, "nodeId": node_id}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    previous = ctx.db.scalar(select(QualityInspection).where(QualityInspection.workspace_id == ctx.workspace_id,
        QualityInspection.owner_user_id == ctx.user_id, QualityInspection.request_key == payload.requestKey))
    if previous:
        if previous.request_hash != digest:
            raise ConflictError("此提交键已用于其他质检参数，请查看原记录或新建一次检验")
        return get_inspection(ctx, previous.id)
    revision, equipment = validate_inspection(ctx, payload, active=True, execute=bool(execution_id))
    metrics = {item["id"]: MetricView.model_validate(item) for item in revision.snapshot["metrics"]}
    observations, provenance = _observations(ctx, payload, metrics)
    results = [assess(metric, observations.get(metric_id), provenance.get(metric_id, {"mode": "missing"})).model_dump(mode="json") for metric_id, metric in metrics.items()]
    validate_json(results, maximum=8 * 1024 * 1024)
    row = QualityInspection(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, template_id=payload.templateId,
        device_id=equipment.id, execution_id=execution_id, node_id=node_id, sample_id=payload.sampleId,
        request_key=payload.requestKey, request_hash=digest, outcome=overall([item["outcome"] for item in results]),
        snapshot={"template": deepcopy(revision.snapshot), "templateRevision": revision.revision,
                  "device": record_view(ctx, "devices", equipment), "detectionIds": payload.detectionIds, "inspectorId": ctx.user_id}, results=results)
    ctx.db.add(row)
    flush_changes(ctx)
    if not execution_id:
        commit_change(ctx, "quality.inspection.create", "quality", row.id, {"outcome": row.outcome})
    return row


def evaluate_node(ctx: AuthContext, config, value, budget, *, execution_id: str, node_id: str) -> dict:
    """Shared workflow assessment accepts real evidence references or explicit manual rows.

    （中文补充）工作流判定节点入口。``sampleMode='input'`` 时转交 ``quality.batch`` 逐样本出报告；
    否则按固定样本模式处理上游记录：带 ``detectionId`` 的项必须是 ``{detectionId, sampleId}``
    且样本号与配置一致（**不允许提交其他样本或替换检测内容**），其余项按人工观察值解析。
    请求键固定为 ``workflow:<执行>:<节点>``，因此同一次执行同一节点重复运行是幂等的。
    返回 ``{qualityInspectionId, outcome, sampleId, data, totalCount, truncated:False, issues}``；
    异常：引用形状不符或检测引用跨样本时抛 ``ValidationError``。
    """
    from app.modules.workflows.nodes import records

    if config.sampleMode == "input":
        from app.modules.quality.batch import evaluate_batch
        return evaluate_batch(ctx, config, value, budget, execution_id=execution_id, node_id=node_id)

    observations, detection_ids = [], []
    for item in records(value):
        budget.consume()
        if "detectionId" in item:
            if set(item) != {"detectionId", "sampleId"} or item["sampleId"] != config.sampleId:
                raise ValidationError("请先筛选当前样本的检测引用；不能提交其他样本或替换检测内容")
            detection_ids.append(item["detectionId"])
        else:
            observations.append(Observation.model_validate(item))
    payload = InspectionInput(**config.model_dump(exclude={"sampleMode"}), requestKey=f"workflow:{execution_id}:{node_id}",
                              observations=observations, detectionIds=detection_ids)
    row = create_inspection(ctx, payload, execution_id=execution_id, node_id=node_id)
    return {"qualityInspectionId": row.id, "outcome": row.outcome, "sampleId": row.sample_id,
            "data": row.results, "totalCount": len(row.results), "truncated": False,
            "issues": [{"rowIndex": index, "ruleId": item["metricId"], "ruleName": item["name"],
                        "severity": "critical" if item["outcome"] == "failed" else "warning",
                        "message": item["reason"], "outcome": item["outcome"]}
                       for index, item in enumerate(row.results) if item["outcome"] != "passed"]}


def list_inspections(ctx: AuthContext, *, limit: int, offset: int, keyword: str = "", outcome: str = "") -> list[InspectionView]:
    """分页列出有效质检记录（``quality.view``），可按样本号关键字与**原始结论**过滤。

    只返回 ``status='active'``（已归档记录需通过详情查看）；``outcome`` 过滤的是原始结论，
    不是复核后的有效结论。逐行经 ``get_inspection`` 复核来源链，不可见行不计入偏移。
    ``keyword`` 匹配样本号并使用 ``autoescape``。只读。
    """
    query = visible_records(ctx, QualityInspection, "quality.view").where(QualityInspection.status == "active")
    if keyword:
        query = query.where(QualityInspection.sample_id.contains(keyword, autoescape=True))
    if outcome:
        query = query.where(QualityInspection.outcome == outcome)
    rows = authorized_page(ctx, query.order_by(QualityInspection.created_at.desc(), QualityInspection.id),
                           lambda row: get_inspection(ctx, row.id), limit=limit, offset=offset)
    return [inspection_view(ctx, row) for row in rows]


def review(ctx: AuthContext, inspection_id: str, payload: ReviewInput) -> InspectionView:
    """Append an attributed human conclusion; original measurements never change.

    （中文补充）需要 ``quality.review``。要求记录仍为 ``active``、指标属于该记录模板、
    且 ``version`` 为当前版本（过期 409）。写入一条 ``QualityReview``（含操作者、理由、
    当时的记录版本）并递增记录版本，随后返回重新计算的视图。
    **原始实测值与原始判定永不修改**，复核只是追加结论。
    """
    row = get_inspection(ctx, inspection_id, "review")
    check_version(row, payload.version)
    if row.status != "active" or payload.metricId not in row.snapshot["template"]["metricIds"]:
        raise ValidationError("不能复核已归档记录或模板外指标")
    row.version += 1
    ctx.db.add(QualityReview(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, inspection_id=row.id,
        metric_id=payload.metricId, decision=payload.decision, note=payload.note, inspection_version=row.version))
    commit_change(ctx, "quality.inspection.review", "quality", row.id, {"metric_id": payload.metricId, "decision": payload.decision})
    return inspection_view(ctx, row)


def archive(ctx: AuthContext, inspection_id: str, version: int) -> None:
    """归档质检记录（``quality.delete`` + 正确版本）：置 ``archived`` 并递增版本。

    归档后不能再复核（``review`` 会拒绝），但记录与证据永久保留、仍可查看与导出。
    """
    row = get_inspection(ctx, inspection_id, "delete")
    check_version(row, version)
    row.status, row.version = "archived", row.version + 1
    commit_change(ctx, "quality.inspection.archive", "quality", row.id)


def export_report(ctx: AuthContext, inspection_id: str) -> str:
    """Self-contained report from stored evidence only; never call a detector again.

    （中文补充）需要 ``quality.export``（``get_inspection(..., "export")``）。
    返回完整 HTML 字符串：样本号、原始判定与复核后结论并列、逐指标表格
    （指标/原始判定/理由）、完整证据与复核历史 JSON，以及内嵌原图与缺陷框
    （``image_export.export_images``）。所有插值经 ``escape``，新增字段必须保持转义。
    报告只呈现**已保存证据**，不重新调用检测服务，因此内容与当时一致（可复现）。
    """
    row = get_inspection(ctx, inspection_id, "export")
    result = inspection_view(ctx, row)
    from app.modules.quality.image_export import export_images
    pictures = export_images(ctx, row.results)
    labels = {"passed": "合格", "failed": "不合格", "review": "待复核", "incomplete": "未完成检测"}
    rows = "".join("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in
        (item.name, labels[item.outcome], item.reason)) + "</tr>" for item in result.results)
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>质量检查报告</title>"
            "<style>body{font:15px sans-serif;max-width:1100px;margin:32px auto;padding:20px;color:#17263c}table{border-collapse:collapse;width:100%}td,th{border:1px solid #dce2ec;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            f"<h1>质量检查报告 · {escape(row.sample_id)}</h1><p>原始判定：{labels[row.outcome]} · 复核后：{labels[result.effectiveOutcome]}</p>"
            "<table><tr><th>指标</th><th>原始判定</th><th>理由</th></tr>" + rows + "</table><h2>原始证据、固定标准与复核历史</h2><pre>"
            + escape(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)) + "</pre>" + pictures + "</html>")
