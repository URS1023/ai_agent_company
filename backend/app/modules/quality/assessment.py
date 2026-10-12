"""Pure per-metric judgments; missing evidence never becomes a passing result.

职责：质检的**纯判定逻辑** —— 按固定指标标准逐项给出 passed / failed / review / incomplete，
并汇总为整体结论。

位置与依赖：被 ``quality.inspections`` 与质检工作流节点调用；只依赖 ``schemas`` 的类型，
不访问数据库、不做权限判断、无副作用，因此可安全地离线复算历史记录。

核心纪律（**缺证据不等于合格**）：
- 未提交检测结果、外观检测缺图像来源或缺完整结果数组 → ``incomplete``；
- 实测单位与标准单位不一致 → ``review``（不自动换算，也不按"看起来差不多"放过）；
- 低置信度或标注为待复核的缺陷 → ``review``（不能自动判合格）；
- 只有明确返回"无缺陷"或全部缺陷未达不合格级别时才 ``passed``。
"""
from decimal import Decimal

from app.modules.quality.schemas import Assessment, MetricView, Observation


def overall(outcomes: list[str]) -> str:
    """Keep failure, missing evidence and review distinct from passed.

    （中文补充）汇总多个指标的结论，优先级：``failed`` > ``incomplete`` > ``review`` > ``passed``。
    即**只要有一项不合格就是不合格**；没有不合格但有缺证据/待复核时，不会退化成合格；
    空列表返回 ``incomplete``（没有任何判定依据，不能当作通过）。
    """
    for outcome in ("failed", "incomplete", "review"):
        if outcome in outcomes:
            return outcome
    return "passed" if outcomes else "incomplete"


def assess(metric: MetricView, observation: Observation | None, provenance: dict) -> Assessment:
    """Use exactly the pinned criterion and declared unit for this metric.

    （中文补充）按"该指标当时固定的标准"判定一条观察值，返回 ``Assessment``（含判定理由原文）。
    ``provenance`` 由调用方给出（manual / detector 及检测标识），用于区分人工与自动证据。
    判定分支：无观察值 → incomplete；外观类走 ``_appearance``；数值类无实测值 → incomplete；
    单位不一致 → review；否则用 ``Decimal`` 与标准上下限比较（含端点）得出 passed/failed。
    注意标准可能只配一侧（另一侧"不限"），比较时按 None 跳过。
    """
    if observation is None:
        outcome, reason = "incomplete", "模板指标尚未提交检测结果"
    elif metric.category == "appearance":
        outcome, reason = _appearance(metric, observation)
    elif observation.value is None:
        outcome, reason = "incomplete", "未取得有效实测值"
    elif observation.unit != metric.unit:
        outcome, reason = "review", f"实测单位 {observation.unit!r} 与标准单位 {metric.unit!r} 不一致，未自动换算"
    else:
        value = Decimal(observation.value)
        outside = (metric.standard_min is not None and value < Decimal(metric.standard_min)) or (metric.standard_max is not None and value > Decimal(metric.standard_max))
        outcome = "failed" if outside else "passed"
        reason = f"实测 {observation.value}{observation.unit}，标准 [{metric.standard_min or '不限'}, {metric.standard_max or '不限'}]，" + ("超出标准" if outside else "在标准内")
    return Assessment(metricId=metric.id, name=metric.name, category=metric.category, outcome=outcome,
                      reason=reason, observation=observation, provenance=provenance)


def _appearance(metric: MetricView, observation: Observation) -> tuple[str, str]:
    """外观指标的判定：区分"明确无缺陷"与"没有检测结果"。

    - 缺图像来源或缺 ``defects`` 数组 → ``incomplete``（**未检测不等于未发现缺陷**）；
    - 显式空数组 → ``passed``（服务明确表示无缺陷，且有图像来源可追溯）；
    - 存在达到"不合格级别 + 判定置信度"的缺陷 → ``failed``；
    - 存在待复核（severity='review'）或低于置信度的缺陷 → ``review``（不能自动判合格）；
    - 其余（缺陷均未达不合格级别）→ ``passed``。
    置信度用 ``Decimal`` 比较，避免浮点误差把临界值判反。
    """
    if not (observation.imageRef or observation.imageFileId) or observation.defects is None:
        return "incomplete", "外观检测缺少图像来源或完整检测结果；未检测不等于未发现缺陷"
    if not observation.defects:
        return "passed", "检测结果明确返回无缺陷；图像来源已保留"
    if any(item.severity in metric.blocking_severities and Decimal(item.confidence) >= Decimal(metric.minimum_confidence) for item in observation.defects):
        return "failed", f"发现 {len(observation.defects)} 处缺陷，包含达到判定置信度的不合格级别"
    if any(item.severity == "review" or Decimal(item.confidence) < Decimal(metric.minimum_confidence) for item in observation.defects):
        return "review", "存在待复核或低置信度缺陷，不能自动判为合格"
    return "passed", f"发现 {len(observation.defects)} 处缺陷，均未达到所配置的不合格级别"
