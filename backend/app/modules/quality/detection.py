"""Real HTTP detector adapters; no mock defects, measurements or fallback success.

职责：质检"实际检测"节点 —— 调用已保存的 HTTP 检测服务（外观视觉 / 成分），
把服务返回的原始结果整形成逐样本的检测证据（``QualityDetection``），供后续判定与追溯使用。

位置与依赖：由工作流检测节点（``qc-appearance`` / ``qc-component``）调用；依赖
``collection.collect`` 执行 HTTP 采集（带权限、超时与行数上限），``quality.detector_inputs``
准备请求参数与样本图片，``quality.registry`` 取指标，``alerts.registry`` 校验设备与检测服务。

核心纪律（**不得编造数值**的落点）：本模块是"服务没给就不认"的地方 ——
- 一律使用真实 HTTP 调用的返回，无模拟缺陷、无实测替代值、无"失败就当成功"的回退；
- 缺少字段/数组/明确结论时**拒绝生成证据**，而不是按默认值（例如"无缺陷"）继续；
- 服务返回不全（样本不齐、版本不一致）时整体拒绝，宁可无结论也不留"部分合格"的证据；
- 单次样本上限 1000，超限报错而不截断（截断会让"少检了几个"看起来像"都检过了"）。
无数据（0 个样本）时状态为 ``no_data``，**不能解释为合格**。
"""
from copy import deepcopy

from pydantic import ValidationError as ContractError

from app.common.deps import AuthContext
from app.common.owned_records import owned_record
from app.errors import ToolExecutionError, ValidationError
from app.models import DataSource
from app.modules.analytics.connections import source_version
from app.modules.alerts.registry import detector_source_ids, device, record_view
from app.modules.collection import execution as collection
from app.modules.collection.sources import http_source
from app.modules.quality.models import QualityDetection
from app.modules.quality.detector_inputs import detector_parameters, validate_input_template
from app.modules.quality.images import authorize_evidence_images
from app.modules.quality.registry import record, view
from app.modules.quality.schemas import DetectorConfig, Observation
from app.modules.system.service_helpers import flush_changes


def validate_detector(ctx: AuthContext, kind: str, config: DetectorConfig, *, active: bool = False, execute: bool = False):
    """Version, action and device/source checks happen before the first HTTP call.

    （中文补充）检测节点执行前的统一校验，返回 ``(指标, 设备, 数据源)``。
    ``kind`` 决定所需指标类别：``qc-appearance`` → appearance，其余 → component。
    ``execute=True`` 时权限动作用 ``quality.execute``（真正要发起外部调用），否则只校验查看权。
    ``active=True``（即将真实检测）时额外要求：
    - 指标版本与数据源版本必须与节点固定的版本一致（不一致提示重新确认节点，不允许"跑新版"）；
    - 指标类别、设备用途（须含 quality 域）、数据源是否在设备的检测服务集合内必须全部匹配；
    - 数据源必须是 HTTP 类型且处于 active；
    - 输入模板（请求体/JSONPath/参数）必须校验通过。
    约束：所有校验都在**第一次 HTTP 调用之前**完成；任何失败都不产生外部请求与副作用。
    """
    metric = record(ctx, "metrics", config.metricId, "execute" if execute else "view", active=active, deleted=not active)
    equipment = device(ctx, config.deviceId, active=active, deleted=not active)
    source = owned_record(ctx, DataSource, config.datasourceId, "datasources.view")
    category = "appearance" if kind == "qc-appearance" else "component"
    if active:
        if metric.version != config.metricVersion or source_version(source) != config.datasourceVersion:
            raise ValidationError("质检指标或检测服务配置版本已变化，请重新确认节点")
        if metric.category != category or source.id not in detector_source_ids(ctx, equipment) or "quality" not in equipment.domains:
            raise ValidationError("检测节点、指标类别、设备用途或设备数据源不匹配")
        http_source(ctx, source.id)
        if source.status != "active":
            raise ValidationError("检测数据源已停用")
        validate_input_template(kind, config, source)
    return metric, equipment, source


def detect(ctx: AuthContext, kind: str, config: DetectorConfig, budget, *, execution_id: str, node_id: str, value=None) -> dict:
    """Call the saved detector request, validating complete per-sample evidence.

    （中文补充）执行一次真实检测并落证据。入参：``config`` 固定指标/设备/数据源与版本，
    ``value`` 为上游记录（提供样本与图片），``budget`` 控制超时与规则预算，
    ``execution_id`` / ``node_id`` 标识节点执行。

    返回：``{qualityDetectionId, data:[{sampleId, detectionId}], totalCount, truncated:False,
    status}``；``status`` 为 ``completed``（有样本）或 ``no_data``（零样本）。

    拒绝（抛 ``ValidationError`` / ``ToolExecutionError``，均不生成任何证据）的情形：
    - 实际来源版本与节点固定版本不一致（宁可失败也不接受"来源变了的结果"）；
    - 采集未成功或结果被截断（无替代结果）；
    - ``sampleId`` 缺失/重复/超长，或返回了未请求的样本；
    - 返回样本与请求行不一致（``rowOrigins`` 对不上，防止把别的样本缺陷挂到原图）；
    - 外观：没有明确的 ``defects`` 数组与 ``imageRef``/已请求图片 —— **缺字段不等于无缺陷**；
    - 成分：``components`` 非数组，或指定成分出现次数不是恰好 1 次（缺或多都判不出合格结论）；
    - 结果不符合 ``Observation`` 契约（字段/数值/缺陷结构非法）；
    - 请求了 N 个样本但未逐一返回（不留"部分合格"证据）；
    - 样本数超过 1000（报错而不截断）。

    副作用：写 ``quality_detections``（含指标、设备、数据源版本、queryId/runId 快照）并 flush；
    不 commit（由引擎统一提交）。指标类别决定证据字段：外观存 defects，成分存 measuredValue/unit。
    """
    from app.modules.workflows.nodes import bounded_output

    metric, equipment, source = validate_detector(ctx, kind, config, active=True, execute=True)
    detected_version = source_version(source)
    parameters, sample_images = detector_parameters(ctx, kind, config, value, budget)
    result = collection.collect(ctx, source.id, automatic=True, permission="quality.execute", deadline=budget.deadline, parameters=parameters)
    if result.datasourceVersion != config.datasourceVersion:
        raise ValidationError("实际检测来源版本与节点固定版本不一致，未接受此检测结果")
    if not result.success or result.truncated:
        raise ToolExecutionError("检测服务未完整成功，未生成替代检测结果", query_id=result.queryId)
    observations, seen = [], set()
    for index, item in enumerate(result.data):
        budget.consume()
        sample = item.get("sampleId")
        # sampleId 是"一条证据对应一个样本"的锚点，缺失或重复都无法定位到具体样本。
        if not isinstance(sample, str) or not sample.strip() or len(sample) > 160 or sample in seen:
            raise ValidationError("检测服务必须返回唯一且非空的 sampleId")
        seen.add(sample)
        if parameters is not None and sample not in sample_images:
            raise ValidationError("检测返回了未请求的样本，未接受此检测结果")
        if parameters is not None:
            # 行来源校验：确认该结果确实由本次请求的第 origin 行产生，防止错位关联图片。
            origin = result.rowOrigins[index] if index < len(result.rowOrigins) else -1
            if not 0 <= origin < len(parameters) or sample != parameters[origin]["sampleId"]:
                raise ValidationError("检测结果样本与其实际请求不一致，不能将其他样本的缺陷关联到原图")
        image = sample_images.get(sample)
        try:
            if metric.category == "appearance":
                # 必须显式给出 defects 数组；"没有该字段"不能被解释成"没有缺陷"。
                if "defects" not in item or not isinstance(item["defects"], list) or not (item.get("imageRef") or image):
                    raise ValidationError("视觉服务缺少明确的 defects 数组或 imageRef，不能当作无缺陷")
                observation = Observation(metricId=metric.id, imageRef=item.get("imageRef", ""),
                                          imageFileId=image["id"] if image else None, defects=item["defects"])
            else:
                components = item.get("components")
                if not isinstance(components, list) or any(not isinstance(component, dict) for component in components):
                    raise ValidationError("成分服务必须返回 components 数组")
                # 恰好一条：0 条说明没测到该成分，多条说明结果不可判读，都不能当合格。
                matches = [component for component in components if component.get("componentName") == metric.component_name]
                if len(matches) != 1:
                    raise ValidationError("检测结果缺少指定成分或同一成分重复，未生成合格结论")
                measured = matches[0]
                observation = Observation(metricId=metric.id, value=measured.get("measuredValue"), unit=measured.get("unit", ""))
        except ContractError as exc:
            raise ValidationError("检测结果字段、数值或缺陷结构不符合质检契约，请检查服务配置") from exc
        observations.append({"sampleId": sample, "observation": observation.model_dump(mode="json"), **({"image": image} if image else {})})
    # 请求了样本就必须逐个有结果；否则整批拒绝，避免"未返回的样本被当作通过"。
    if parameters is not None and seen != sample_images.keys():
        raise ValidationError("检测服务未返回每个请求样本的完整结果，未生成部分合格证据")
    if len(observations) > 1000:
        raise ValidationError("单次质检检测最多 1000 个样本，超限不截断")
    bounded_output(observations)
    # 证据快照固化指标/设备/来源版本与查询标识，后续指标或数据源被修改不影响历史追溯。
    evidence = QualityDetection(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, device_id=equipment.id,
        metric_id=metric.id, datasource_id=source.id, execution_id=execution_id, node_id=node_id,
        snapshot={"metric": view("metrics", metric), "device": record_view(ctx, "devices", equipment),
                  "datasourceId": source.id, "datasourceVersion": detected_version, "queryId": result.queryId,
                  "collectionRunId": result.runId, "kind": kind, "requestMode": config.requestMode}, results=deepcopy(observations))
    ctx.db.add(evidence)
    flush_changes(ctx)
    return {"qualityDetectionId": evidence.id, "data": [{"sampleId": item["sampleId"], "detectionId": evidence.id} for item in observations],
            "totalCount": len(observations), "truncated": False, "status": "completed" if observations else "no_data"}


def get_detection(ctx: AuthContext, detection_id: str) -> QualityDetection:
    """Old detector results remain protected by their actual workflow/source chain.

    （中文补充）读取历史检测证据，需要 ``quality.view``，并按**当时**的引用链复核当前权限：
    指标（允许已删除，因证据里已固化）→ 设备（允许已删除）→ 采集数据源 → 证据中的图片
    （``authorize_evidence_images``）→ 执行来源（``get_execution``）。
    因此设备改绑数据源或图片权限变化后，旧证据不会成为绕过授权的旁路。只读。
    """
    from app.modules.workflows.execution import get_execution
    from app.modules.workflows.models import WorkflowExecution

    row = owned_record(ctx, QualityDetection, detection_id, "quality.view")
    record(ctx, "metrics", row.metric_id, deleted=True)
    device(ctx, row.device_id, deleted=True)
    owned_record(ctx, DataSource, row.datasource_id, "datasources.view")
    authorize_evidence_images(ctx, row.results)
    execution = ctx.db.get(WorkflowExecution, row.execution_id)
    get_execution(ctx, execution.workflow_id, execution.id)
    return row
