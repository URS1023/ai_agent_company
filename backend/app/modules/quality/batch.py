"""Per-sample reports from immutable detector references within one shared workflow.

职责：质检"逐样本判定"节点 —— 把上游检测引用按样本分组，为每个样本生成一份独立质检报告，
并把逐项结果汇成可展示的行与问题清单。

位置与依赖：工作流节点实现（``sampleMode='input'`` 的对应逻辑）；依赖
``quality.detection.get_detection`` 复核证据、``quality.inspections.create_inspection`` 落报告，
在同一事务内完成（不单独 commit）。

约束（防止"批次拼接"造假）：
- 输入只接受 ``{sampleId, detectionId}`` 两种键；人工录入请改用固定样本模式；
- 同一样本不得重复引用同一检测（避免歧义报告）；
- 每个检测证据必须**来自本次执行与所选设备**，且证据里的样本集合必须与本次引用集合**完全一致**
  （既不能漏样本，也不能多带样本）；
- 样本数 1–100；无检测结果时直接报错 —— **空批次不等于合格**。
"""
from hashlib import sha256

from app.common.deps import AuthContext
from app.errors import ValidationError
from app.modules.quality.detection import get_detection
from app.modules.quality.schemas import InspectionInput, QualityConfig
from app.modules.workflows.expressions import RuleBudget


def evaluate_batch(ctx: AuthContext, config: QualityConfig, value: object, budget: RuleBudget,
                   *, execution_id: str, node_id: str) -> dict:
    """Validate all references first, then create atomic per-sample reports in the node transaction.

    （中文补充）先全量校验引用（分组去重、样本数范围、证据归属与样本集合一致），
    再逐个样本建报告，因此**校验失败不会留下半批报告**。

    返回 ``{qualityInspectionIds, reports, sampleCount, data, issues, totalCount, truncated:False}``：
    ``data`` 是逐项结果行（带 qualityInspectionId/sampleId/deviceId 便于前端定位），
    ``issues`` 只包含非 passed 项（failed → critical，其余 → warning）。
    ``requestKey`` 由 ``node_id + 样本编号`` 哈希派生，保证同一节点同一样本重复执行时幂等。
    异常：引用形状错误、样本重复引用、证据不属于本次执行/设备、样本集合不一致、
    样本数不在 1–100 时抛 ``ValidationError``；副作用：写质检报告（由 ``create_inspection``），
    不额外提交事务。
    """
    from app.modules.quality.inspections import create_inspection
    from app.modules.workflows.nodes import records

    grouped: dict[str, list[str]] = {}
    for item in records(value):
        budget.consume()
        sample, detection_id = item.get("sampleId"), item.get("detectionId")
        if set(item) != {"sampleId", "detectionId"} or not isinstance(sample, str) or not sample.strip() or len(sample) > 160:
            raise ValidationError("逐样本判定仅接受实际检测引用和非空样本编号；人工录入请使用固定样本模式")
        if not isinstance(detection_id, str) or not 1 <= len(detection_id) <= 26:
            raise ValidationError("检测引用无效")
        ids = grouped.setdefault(sample, [])
        if detection_id in ids:
            raise ValidationError("同一样本重复引用检测结果，未生成歧义报告")
        ids.append(detection_id)
    if not 1 <= len(grouped) <= 100:
        raise ValidationError("逐样本判定需要 1–100 个样本；无检测结果不能当作合格")
    # A batch cannot silently combine old runs, omit samples, or attach another device.
    for detection_id in {item for ids in grouped.values() for item in ids}:
        evidence = get_detection(ctx, detection_id)
        if evidence.execution_id != execution_id or evidence.device_id != config.deviceId:
            raise ValidationError("批次检测证据必须来自本次工作流及所选设备")
        expected = {item["sampleId"] for item in evidence.results}
        supplied = {sample for sample, ids in grouped.items() if detection_id in ids}
        if expected != supplied:
            raise ValidationError("批次引用遗漏或增加了检测样本，请检查汇合映射")
    reports, rows, issues = [], [], []
    for sample, ids in grouped.items():
        budget.consume()
        sample_key = sha256((node_id + ":" + sample).encode()).hexdigest()
        payload = InspectionInput(templateId=config.templateId, templateRevision=config.templateRevision,
            deviceId=config.deviceId, sampleId=sample, requestKey=f"batch:{execution_id}:{sample_key}", detectionIds=ids)
        report = create_inspection(ctx, payload, execution_id=execution_id, node_id=node_id)
        identity = {"qualityInspectionId": report.id, "sampleId": sample, "deviceId": report.device_id}
        reports.append({**identity, "outcome": report.outcome})
        for item in report.results:
            index = len(rows)
            rows.append({**identity, **item})
            if item["outcome"] != "passed":
                issues.append({"rowIndex": index, "ruleId": item["metricId"], "ruleName": item["name"],
                    "severity": "critical" if item["outcome"] == "failed" else "warning", "message": item["reason"], **identity})
    return {"qualityInspectionIds": [item["qualityInspectionId"] for item in reports], "reports": reports,
            "sampleCount": len(reports), "data": rows, "issues": issues, "totalCount": len(rows), "truncated": False}
