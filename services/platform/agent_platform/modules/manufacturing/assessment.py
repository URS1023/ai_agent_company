"""复用Test5 quality.assessment纯判定；适配平台snake_case字段。"""
from decimal import Decimal

from .schemas import Metric, Observation


def overall(outcomes: list[str]) -> str:
    """失败、缺证据、待复核依次优先；没有结果不能判为合格。"""
    for value in ("failed", "incomplete", "review"):
        if value in outcomes:
            return value
    return "passed" if outcomes else "incomplete"


def assess(metric: Metric, observation: Observation | None, provenance: dict) -> dict:
    """按固定标准与原始单位判定，不把缺失或单位不一致当成合格。"""
    if observation is None:
        outcome, reason = "incomplete", "尚未提交该指标结果"
    elif metric.category == "appearance":
        outcome, reason = _appearance(metric, observation)
    elif observation.value is None:
        outcome, reason = "incomplete", "未取得有效实测值"
    elif observation.unit != metric.unit:
        outcome, reason = "review", f"实测单位{observation.unit!r}与标准单位{metric.unit!r}不一致"
    else:
        value = Decimal(observation.value)
        outside = (metric.standard_min is not None and value < Decimal(metric.standard_min)) or (metric.standard_max is not None and value > Decimal(metric.standard_max))
        outcome = "failed" if outside else "passed"
        reason = "超出标准" if outside else "在标准内"
    return {"metric_id": metric.id, "name": metric.name, "category": metric.category,
            "outcome": outcome, "reason": reason,
            "observation": observation.model_dump() if observation else None, "provenance": provenance}


def _appearance(metric: Metric, observation: Observation) -> tuple[str, str]:
    """显式空缺陷数组与未提交结果不同；低置信度结论必须复核。"""
    if not observation.image_ref or observation.defects is None:
        return "incomplete", "缺少图像来源或完整检测结果"
    if not observation.defects:
        return "passed", "检测结果明确返回无缺陷"
    if any(d.severity in metric.blocking_severities and Decimal(d.confidence) >= Decimal(metric.minimum_confidence) for d in observation.defects):
        return "failed", "发现达到判定置信度的不合格缺陷"
    if any(d.severity == "review" or Decimal(d.confidence) < Decimal(metric.minimum_confidence) for d in observation.defects):
        return "review", "存在低置信度或待复核缺陷"
    return "passed", "缺陷未达到配置的不合格级别"
