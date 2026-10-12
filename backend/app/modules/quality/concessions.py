"""Configurable incoming-quality concession rules and approval state machine.

职责：来料（进料）质检的**让步/特采**闭环 —— 规则维护、证据采集与校验、规则匹配试算、
以及审批决策。核心问题是"某一批来料不合格时，是否可按既定规则扣量后让步接收"。

位置与依赖：REST 入口在 ``quality.router``；来源可以是已保存的工作流执行结果、质检记录
（``QualityInspection``）或人工录入（数据源直接来源被明确拒绝）——所有来源都必须能被
授权校验并留下字段路径，``_authorize_source`` / ``derive_*`` 负责这两件事。

状态机（``QualityConcessionRequest.status``）：
- ``needs_confirmation``：证据不足、规则不唯一或不匹配 —— **等待业务人员确认/补数据**，
  不是终态，也不代表允许让步；
- ``pending``：唯一匹配规则且已算出候选扣量与候选结算重量，等待有权限人员审批；
- ``approved``：审批通过，此时才写入 ``settlement_weight``（**唯一可用于结算的重量**）；
- ``rejected``：审批驳回，``settlement_weight`` 保持为空。
申请一旦生成，证据（来源快照、实测值、规则快照与规则版本）即不可修改；
审批只更新状态、意见、审批人与时间，并递增版本。

限制与约束：
- 计算一律用 ``Decimal`` 且放大精度（见 ``evaluate_rule``），禁止用浮点近似结算重量；
- 金额/重量边界严格：扣量为负、超过原始重量、结算重量不大于 0 都直接拒绝计算；
- 批准时会**重新校验规则仍是 active 且版本未变**，否则要求按最新规则重新评估（409）；
- 规则匹配不唯一（同时命中多条）时不自动选一条，而是转人工确认；
- 凭据类字段名（password/token/... 见 ``_sensitive_source_name``）禁止作为来源路径。
"""
from __future__ import annotations

import hashlib
import json
from itertools import islice
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP
from typing import Any

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.modules.collection.transport import validate_body_secrets
from app.modules.quality.concession_schemas import (
    ConcessionDecisionInput,
    ConcessionEvidenceInput,
    ConcessionRequestView,
    ConcessionRuleInput,
    ConcessionRuleView,
    ConcessionSourceField,
    ConcessionSourceOption,
)
from app.modules.quality.models import QualityConcessionRequest, QualityConcessionRule
from app.modules.system.service_helpers import check_version, commit_change, flush_changes

# 规则中声明的小数舍入方式 → decimal 常量。键名与 ``ConcessionRuleInput.roundingMode``
# 的字面量必须一一对应；``exact`` 不在此表中，代表"保留精确结果、不舍入"。
ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "down": ROUND_DOWN,
    "up": ROUND_UP,
}


def _sensitive_source_name(value: str) -> bool:
    """判断字段名/路径片段是否疑似凭据（凭据不得作为让步来源路径）。

    先把名称归一化（转小写、去掉非字母数字），因此 ``access_key`` 与 ``accessKey``、
    ``api-key`` 与 ``apikey`` 都能命中同一 token；这是**黑名单式**防护，
    新增凭据类字段名时应同步补充 ``tokens``。
    """
    normalized = "".join(char for char in value.lower() if char.isalnum())
    return any(token in normalized for token in (
        "password", "secret", "token", "authorization", "cookie", "apikey",
        "accesskey", "privatekey", "clientkey", "credential",
    ))


def _single_source_value(result: dict[str, Any], path: str) -> Any:
    """Read one explicitly configured workflow output path, never an arbitrary URL.

    （中文补充）从**已保存的执行结果**里按 JSONPath 取一个值。约束：
    路径必须以 ``$`` 开头（只允许配置好的输出路径，不接受任意查询/URL），
    不得命中凭据类名称，且必须**恰好匹配一个值**（0 个或多个都报错，避免"取第一个"式误读）。
    由 ``collection.extraction.matches`` 在有限预算树上求值，因此异常深/大的结果不会失控。
    异常：路径非法、命中凭据名、匹配数不是 1 时抛 ``ValidationError``。
    """
    from app.modules.collection.extraction import budget_tree, matches

    if not isinstance(path, str) or not path.strip().startswith("$"):
        raise ValidationError("工作流来源字段必须是以 $ 开头的 JSONPath")
    if _sensitive_source_name(path):
        raise ValidationError("工作流来源字段不能读取凭据或会话字段")
    found = matches(path, budget_tree(result), limit=2)
    if len(found) != 1:
        raise ValidationError("工作流来源字段必须唯一匹配一个实际结果值")
    return found[0]


def derive_workflow_evidence(result: dict[str, Any], source: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    """Build evidence from a persisted execution result and declared JSONPaths.

    （中文补充）以"**已保存的执行结果**"为准重建证据，客户端提交的值只能用于**交叉核对**：
    - 原始重量：客户端提交值必须与执行结果一致（否则拒绝），最终一律采用执行结果的值；
    - 产品编码：有配置路径时同样以执行结果为准；未配置路径时置空
      （自动规则需要"来源里的产品"，不能用客户端选的产品顶替）；
    - 实测指标：遍历 ``fields`` 中以 ``.value`` 结尾的键（键名去掉后缀即 metricId），
      同族还可选 ``.unit`` / ``.standardMin`` / ``.standardMax`` 路径；
    - 至少要有一种实测值路径，否则抛 ``ValidationError``（无实测值的让步申请没有意义）。
    返回补充后的 evidence 字典（含 ``measurements``）；所有数值以字符串保存，避免精度丢失。
    """
    fields = source.get("fields") or {}
    evidence = dict(supplied)
    actual_weight = _single_source_value(result, fields["originalWeight"]) if fields.get("originalWeight") else None
    if fields.get("originalWeight"):
        actual_weight = str(actual_weight) if actual_weight is not None else None
        if supplied.get("originalWeight") is not None and actual_weight is None:
            raise ValidationError("已保存工作流结果中的原始重量为空")
        if supplied.get("originalWeight") is not None and not _same_decimal(supplied["originalWeight"], actual_weight):
            raise ValidationError("提交的原始重量与已保存工作流结果不一致")
        evidence["originalWeight"] = actual_weight
    if fields.get("productCode"):
        actual_product = _single_source_value(result, fields["productCode"])
        actual_product = str(actual_product) if actual_product is not None else ""
        if supplied.get("productCode") and supplied["productCode"] != actual_product:
            raise ValidationError("提交的产品编码与已保存工作流结果不一致")
        evidence["productCode"] = actual_product
    else:
        # A product-scoped automatic rule needs the product from the saved
        # execution, not a client-selected substitute with no source path.
        evidence["productCode"] = ""
    measurements = []
    for key, path in fields.items():
        if not key.endswith(".value"):
            continue
        metric_id = key[:-6]
        unit_path = fields.get(metric_id + ".unit")
        actual_value = _single_source_value(result, path)
        actual_unit = _single_source_value(result, unit_path) if unit_path else None
        item = {"metricId": metric_id, "value": str(actual_value) if actual_value is not None else None,
                "unit": str(actual_unit) if actual_unit is not None else "", "sourceField": path}
        for suffix, output_key in ((".standardMin", "standardMin"), (".standardMax", "standardMax")):
            standard_path = fields.get(metric_id + suffix)
            if standard_path:
                value = _single_source_value(result, standard_path)
                item[output_key] = str(value) if value is not None else None
        measurements.append(item)
    if not measurements:
        raise ValidationError("工作流来源未配置任何实测值 JSONPath")
    evidence["measurements"] = measurements
    return evidence


def derive_inspection_measurements(row) -> list[dict[str, Any]]:
    """Extract the immutable observation and fixed standards from a quality report.

    （中文补充）从质检记录里取"不可变"的实测值与当时固定的标准上下限：
    实测值来自 ``row.results[*].observation``，标准来自 ``row.snapshot['template']`` 的指标定义
    —— 即**质检当时**的口径，不受指标后续修改影响。
    ``sourceField`` 用合成路径 ``inspection.results[<metricId>].observation.value`` 标识来源，
    便于审批人追溯。无 ``metricId`` 的结果行直接跳过；纯读取，无副作用。
    """
    metric_rows = {item["id"]: item for item in row.snapshot["template"].get("metrics", [])}
    derived = []
    for result in row.results:
        metric_id = result.get("metricId")
        observation = result.get("observation") or {}
        metric = metric_rows.get(metric_id) or {}
        if not metric_id:
            continue
        derived.append({"metricId": metric_id, "value": observation.get("value"), "unit": observation.get("unit", ""),
                        "standardMin": metric.get("standardMin"), "standardMax": metric.get("standardMax"),
                        "sourceField": f"inspection.results[{metric_id}].observation.value"})
    return derived


def inspection_original_weight(row) -> str | None:
    """Use a stored source snapshot only when a quality report actually contains weight.

    （中文补充）优先读 ``snapshot['source']['originalWeight']``，兼容旧结构直接放在
    ``snapshot['originalWeight']`` 的情况；两者都没有、或值为空白时返回 ``None``
    —— 返回 ``None`` 表示"质检记录里没有重量"，此时不允许客户端自带重量（见 ``create_request``），
    必须改用工作流来源或明确的人工来源。
    """
    snapshot = row.snapshot or {}
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    if "originalWeight" in source:
        value = source["originalWeight"]
    elif "originalWeight" in snapshot:
        value = snapshot["originalWeight"]
    else:
        value = None
    return str(value) if value is not None and str(value).strip() else None


def _decimal(value: str | None, label: str, *, positive: bool = False) -> Decimal:
    """把字符串转成精确 ``Decimal``，失败即抛业务错误（不做任何兜底默认值）。

    ``positive=True`` 时要求有限且大于 0；否则只要求有限。
    空值/空白视为"缺少该值"并报错 —— 让步计算不接受缺失值，
    因为任何默认值都会直接改变结算重量。``label`` 用于生成面向业务的中文错误信息。
    """
    if value is None or not str(value).strip():
        raise ValidationError(f"缺少{label}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{label}不是有效的精确数值") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ValidationError(f"{label}必须是有限正数" if positive else f"{label}必须是有限数值")
    return number


def _rule_values(rule: QualityConcessionRule | dict[str, Any]) -> dict[str, Any]:
    """把规则 ORM 行规整成计算用字典（已是字典则原样返回）。

    ORM 字段名（下划线）与规则快照/对外契约的驼峰键在这里做一次映射；
    ``rule_snapshot`` 就存这个形状，因此**评估逻辑只依赖该形状**，
    即使规则后续被修改或删除，历史申请仍按快照复算。
    """
    if isinstance(rule, dict):
        return rule
    return {
        "id": rule.id,
        "code": rule.code,
        "name": rule.name,
        "productCodes": list(rule.product_codes or []),
        "metricIds": list(rule.metric_ids or []),
        "weightUnit": rule.weight_unit,
        "metricUnit": rule.metric_unit,
        "standardMin": rule.standard_min,
        "standardMax": rule.standard_max,
        "minDeviation": rule.min_deviation,
        "maxDeviation": rule.max_deviation,
        "deductionMode": rule.deduction_mode,
        "deductionValue": rule.deduction_value,
        "roundingScale": rule.rounding_scale,
        "roundingMode": rule.rounding_mode,
        "status": rule.status,
        "description": rule.description,
    }


def _measurement_values(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把实测值列表转成 ``{metricId: item}``，忽略无 ``metricId`` 的脏行。

    同名指标后者覆盖前者（正常数据不会重复；重复时以最后一条为准，不做合并）。
    """
    result: dict[str, dict[str, Any]] = {}
    for item in evidence.get("measurements") or []:
        metric_id = str(item.get("metricId") or "").strip()
        if metric_id:
            result[metric_id] = item
    return result


def _same_decimal(left: Any, right: Any) -> bool:
    """按数值比较两个值是否相等（``"1.50"`` 与 ``1.5`` 视为相等）。

    ``None`` 只在两边都是 ``None`` 时为真；无法解析为数值时退化为字符串精确比较，
    避免为了"可比较"而把非法值静默转成默认值。
    """
    if left is None or right is None:
        return left is None and right is None
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def _identity_matches(rule: dict[str, Any], evidence: dict[str, Any]) -> tuple[bool, str]:
    """判断证据是否落在规则的适用范围（产品、单位、必需指标及其标准）。

    返回 ``(是否适用, 不适用原因)``。检查项：
    产品编码（规则配了范围才校验）、原始重量单位、必需指标是否齐全、每个指标的单位，
    以及规则固定了标准上下限时两者必须**数值相等**（防止规则与实测标准口径漂移）。
    只要有一项不符即返回 False —— 调用方据此把申请转为"需业务确认"而不是硬算结果。
    """
    product_codes = [str(item).strip() for item in rule.get("productCodes") or []]
    product_code = str(evidence.get("productCode") or "").strip()
    if product_codes and product_code not in product_codes:
        return False, "产品编码不在规则适用范围"
    if str(evidence.get("weightUnit") or "").strip() != str(rule.get("weightUnit") or "").strip():
        return False, "原始重量单位与规则重量单位不一致"
    required = [str(item).strip() for item in rule.get("metricIds") or []]
    measurements = _measurement_values(evidence)
    missing = [metric_id for metric_id in required if metric_id not in measurements]
    if missing:
        return False, "缺少规则要求的实测指标：" + ", ".join(missing)
    for metric_id in required:
        item = measurements[metric_id]
        if str(item.get("unit") or "").strip() != str(rule.get("metricUnit") or "").strip():
            return False, f"指标 {metric_id} 的实测单位与规则指标单位不一致"
        if rule.get("standardMin") is not None and not _same_decimal(item.get("standardMin"), rule["standardMin"]):
            return False, f"指标 {metric_id} 的标准下限与规则不一致"
        if rule.get("standardMax") is not None and not _same_decimal(item.get("standardMax"), rule["standardMax"]):
            return False, f"指标 {metric_id} 的标准上限与规则不一致"
    return True, ""


def evaluate_rule(rule: QualityConcessionRule | dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Pure evaluator. It returns a confirmation state instead of guessing missing data.

    （中文补充）让步规则的**纯函数**试算：不写库、不读库、不做权限判断，可安全地用于
    预演与历史复算。返回字典含 ``status``（``pending`` 表示可提交审批，``needs_confirmation``
    表示需人工确认）、``matched``、``reason``，命中时附 ``deviation`` / ``originalWeight`` /
    ``proposedDeduction`` / ``proposedSettlementWeight`` / ``measurements``。

    关键：**结果只有"可审批"或"需确认"两种**，绝不会在没有依据时编出一个结算重量
    （缺重量、缺实测、数值非法都返回 ``needs_confirmation``）。

    精度：输入允许最多 100 位有效数字与 10^100 量级，而 ``Decimal`` 默认精度 28 会静默舍入，
    因此这里把上下文精度放大到 512 位再计算 —— 结算重量不允许被隐式四舍五入。
    """
    # Inputs permit up to 100 significant digits and exponent magnitude 100.
    # Decimal's default precision of 28 would silently round exact settlement.
    with localcontext() as context:
        context.prec = 512
        return _evaluate_rule(rule, evidence)


def _evaluate_rule(rule: QualityConcessionRule | dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """``evaluate_rule`` 的实际计算体（在放大精度上下文中执行）。

    计算顺序与每一步的拒绝理由：
    1) 适用范围（``_identity_matches``）不符 → ``matched=False``；
    2) 原始重量缺失/非正 → 无法算结算重量，转确认；
    3) 每个必需指标：实测值 + 上下限必须齐全，且实测值必须**超出**标准（偏差 > 0），
       否则视为"未不合格"、规则不匹配；偏差取超出上下限的最大值；
    4) 规则若配了偏差上下限，超出即不匹配；
    5) 扣量：``rate`` 模式为"原始重量 × 比例"（比例 > 1 直接拒绝），``weight`` 模式为固定重量；
       未知模式拒绝；扣量必须 > 0 且 ≤ 原始重量；
    6) 结算重量 = 原始重量 − 扣量，必须 > 0；若规则要求舍入，则按指定小数位与方式舍入，
       **舍入后会重新检查扣量与结算重量是否仍落在安全边界内**（舍入可能把结果推到 0 或负数）；
    7) 全部通过才返回 ``pending`` + 候选扣量/结算重量 + 各指标偏差快照。
    计算过程中任何缺失/非法格式都会落到 ``needs_confirmation``（``matched=True``），
    提示"实测值或标准缺失/格式无效，不能计算"，而不是抛异常中断整批。
    """
    values = _rule_values(rule)
    identity_ok, identity_reason = _identity_matches(values, evidence)
    if not identity_ok:
        return {"status": "needs_confirmation", "matched": False, "reason": identity_reason}
    if evidence.get("originalWeight") is None or not str(evidence.get("originalWeight")).strip():
        return {"status": "needs_confirmation", "matched": True, "reason": "缺少实际原始重量，不能计算结算重量"}
    try:
        original = _decimal(evidence.get("originalWeight"), "原始重量", positive=True)
    except ValidationError as exc:
        return {"status": "needs_confirmation", "matched": True, "reason": exc.message}
    measurements = _measurement_values(evidence)
    deviations: list[Decimal] = []
    snapshots: list[dict[str, Any]] = []
    try:
        for metric_id in values["metricIds"]:
            item = measurements[metric_id]
            value = _decimal(item.get("value"), f"指标 {metric_id} 实测值")
            lower = _decimal(item.get("standardMin"), f"指标 {metric_id} 标准下限") if item.get("standardMin") is not None else None
            upper = _decimal(item.get("standardMax"), f"指标 {metric_id} 标准上限") if item.get("standardMax") is not None else None
            # 只取"超出标准"的那一侧偏差；在标准内则为 0，规则随即判为不匹配。
            deviation = max((lower - value) if lower is not None and value < lower else Decimal("0"),
                            (value - upper) if upper is not None and value > upper else Decimal("0"))
            if deviation <= 0:
                return {"status": "needs_confirmation", "matched": False, "reason": f"指标 {metric_id} 实测值未超出标准，规则不匹配"}
            deviations.append(deviation)
            snapshots.append({"metricId": metric_id, "value": item.get("value"), "unit": item.get("unit"),
                              "standardMin": item.get("standardMin"), "standardMax": item.get("standardMax"),
                              "sourceField": item.get("sourceField"), "deviation": str(deviation)})
        # 多指标时按最大偏差与规则区间比对（保守口径：以最严重的偏差决定是否超区间）。
        deviation = max(deviations)
        if values.get("minDeviation") is not None and deviation < _decimal(values["minDeviation"], "规则偏差下限"):
            return {"status": "needs_confirmation", "matched": False, "reason": "实测偏差低于规则下限"}
        if values.get("maxDeviation") is not None and deviation > _decimal(values["maxDeviation"], "规则偏差上限"):
            return {"status": "needs_confirmation", "matched": False, "reason": "实测偏差高于规则上限"}
        configured = _decimal(values.get("deductionValue"), "规则扣量值", positive=True)
        if values.get("deductionMode") == "rate":
            if configured > 1:
                return {"status": "needs_confirmation", "matched": False, "reason": "规则扣量比例超过 1，不能计算"}
            deduction = original * configured
        elif values.get("deductionMode") == "weight":
            deduction = configured
        else:
            return {"status": "needs_confirmation", "matched": False, "reason": "规则扣量方式未被支持"}
        if deduction <= 0 or deduction > original:
            return {"status": "needs_confirmation", "matched": False, "reason": "规则扣量不能为负数或超过原始重量"}
        settlement = original - deduction
        if settlement <= 0:
            return {"status": "needs_confirmation", "matched": False, "reason": "规则计算出的结算重量必须大于 0"}
        scale = values.get("roundingScale")
        rounding_mode = values.get("roundingMode", "exact")
        if scale is not None:
            settlement = settlement.quantize(Decimal(1).scaleb(-int(scale)), rounding=ROUNDING[rounding_mode])
            # 舍入后再算一次扣量并复核边界：舍入可能把结算重量推到 0 或使扣量超过原始重量。
            deduction = original - settlement
            if settlement <= 0 or settlement > original or deduction <= 0 or deduction > original:
                return {"status": "needs_confirmation", "matched": False, "reason": "明确舍入后扣量或结算重量越过安全边界"}
        return {"status": "pending", "matched": True, "reason": "规则已唯一匹配，等待有权限人员审批",
                "deviation": str(deviation), "originalWeight": str(original), "proposedDeduction": str(deduction),
                "proposedSettlementWeight": str(settlement), "measurements": snapshots}
    except (KeyError, TypeError, InvalidOperation, ValueError, ValidationError) as exc:
        return {"status": "needs_confirmation", "matched": True, "reason": "实测值或标准缺失/格式无效，不能计算"}


def rule_view(row: QualityConcessionRule) -> ConcessionRuleView:
    """规则行 → 对外视图（字段名转驼峰，并补 ``createTime`` / ``updateTime``）。

    注意视图里带 ``version``：前端编辑规则时必须回传该版本（乐观锁，过期 409）。
    """
    return ConcessionRuleView(id=row.id, version=row.version, code=row.code, name=row.name,
                              productCodes=list(row.product_codes or []), metricIds=list(row.metric_ids or []),
                              weightUnit=row.weight_unit, metricUnit=row.metric_unit,
                              standardMin=row.standard_min, standardMax=row.standard_max,
                              minDeviation=row.min_deviation, maxDeviation=row.max_deviation,
                              deductionMode=row.deduction_mode, deductionValue=row.deduction_value,
                              roundingScale=row.rounding_scale, roundingMode=row.rounding_mode, status=row.status,
                              description=row.description, createTime=row.created_at.isoformat(),
                              updateTime=row.updated_at.isoformat())


def list_rules(ctx: AuthContext, *, limit: int, offset: int, keyword: str = "") -> list[ConcessionRuleView]:
    """分页列出未删除的让步规则（``quality.view``），按更新时间倒序。

    ``keyword`` 只匹配规则名称且使用 ``autoescape`` 转义通配符。
    这里用**直接分页**而非逐行授权（规则本身无父资源），所以总数与页大小一致。
    """
    require_permission(ctx, "quality.view")
    query = visible_records(ctx, QualityConcessionRule, "quality.view").where(QualityConcessionRule.status != "deleted")
    if keyword:
        query = query.where(QualityConcessionRule.name.contains(keyword, autoescape=True))
    rows = ctx.db.scalars(query.order_by(QualityConcessionRule.updated_at.desc(), QualityConcessionRule.id).limit(limit).offset(offset)).all()
    return [rule_view(row) for row in rows]


def _source_paths(value: Any, *, path: str = "$", depth: int = 0, blocked: bool = False):
    """Expose selectable paths only; never transfer persisted execution values.

    （中文补充）遍历一份执行结果，产出**可选的 JSONPath 及类型**（``(path, dataType)``），
    供前端下拉选择"来源字段"——只给路径与类型，**不把执行结果的值传出去**（避免泄露业务数据，
    也让来源声明与取值两件事分离）。

    限制（超过即停止该分支，不报错）：
    - 深度 > 8 或路径长度 > 500（防止病态结构把界面撑爆）；
    - 名为凭据类字段的键会被整支跳过（见 ``_sensitive_source_name``）；
    - 数组最多展开前 50 个元素（后续元素靠 ``$[...]`` 通配语义表示，不再逐个列举）；
    - 键名做反转义处理，确保生成的 JSONPath 里引号可被正确解析。
    ``blocked`` 参数保留给调用方在递归中提前截断使用（当前实现只在入口处判断）。
    """
    if depth > 8 or len(path) > 500 or blocked:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _sensitive_source_name(key_text):
                continue
            safe_key = key_text.replace("\\", "\\\\").replace("'", "\\'")
            yield from _source_paths(child, path=path + "['" + safe_key + "']", depth=depth + 1)
        return
    if isinstance(value, list):
        for index, child in enumerate(value[:50]):
            yield from _source_paths(child, path=path + "[" + str(index) + "]", depth=depth + 1)
        return
    if value is None:
        data_type = "empty"
    elif isinstance(value, bool):
        data_type = "boolean"
    elif isinstance(value, (int, float, Decimal)):
        data_type = "number"
    else:
        data_type = "text"
    yield path, data_type


def _authorized_page(ctx, query, resolver, *, limit: int, offset: int):
    """Page authorized results, peeking one extra; never count inaccessible rows.

    （中文补充）本模块专用的"先授权后分页"实现，与 ``alerts.registry.authorized_page``
    的区别是：返回 ``(items, accepted, has_more)``，且**多取一条**用于判断是否还有下一页
    （``has_more``），同时 ``accepted`` 只统计"通过授权"的行，因此对外总数不会泄露不可见记录量。
    批量 100 行；被 ``resolver`` 拒绝（``NotFoundError`` / ``PermissionErrorEx``）的行直接跳过。
    约束：``limit`` 语义是"最多返回多少条已授权记录"，与 SQL 的 LIMIT 无关，调用方不要把它当页大小。
    """
    items = []
    accepted = 0
    scan_offset = 0
    batch_size = 100
    while True:
        rows = ctx.db.execute(query.limit(batch_size).offset(scan_offset)).all()
        scan_offset += len(rows)
        for row in rows:
            try:
                item = resolver(row)
            except (NotFoundError, PermissionErrorEx):
                continue
            accepted += 1
            if accepted <= offset:
                continue
            items.append(item)
            if len(items) > limit:
                return items[:limit], None, True
        if len(rows) < batch_size:
            return items, accepted, False


def list_source_options(ctx: AuthContext, *, kind: str, limit: int, offset: int) -> tuple[list[ConcessionSourceOption], int | None, bool]:
    """Resolve historical source authorization before applying visible offsets.

    （中文补充）列出可选的让步来源（``kind`` 为 workflow 或 inspection），
    返回 ``(选项列表, 已授权总数或 None, 是否还有更多)``。

    要点：
    - 工作流来源只取 ``completed`` 执行，并限制在本工作区、未删除、属于 quality 业务域的流程；
    - 排序用**窄列**（id/流程/状态）而不是可能达 MB 级的执行结果 JSON，
      每行的结果由 ``resolver`` 单独加载并授权后再解析路径；
    - 工作流选项的 ``fields`` 最多取 120 个路径；``hasOriginalWeight`` 表示结果里是否出现
      ``originalWeight``，前端据此决定是否允许自动算结算重量；
    - 质检来源选项的 ``measurementCount`` 是质检结果条数、``hasOriginalWeight`` 取决于快照；
    - 权限：``quality.view``；工作流分支额外要求 ``workflows.view``，没有该权限时返回空列表
      （不报错、不泄露流程存在性）。
    """
    require_permission(ctx, "quality.view")
    from app.modules.quality.models import QualityInspection
    if kind == "workflow":
        from app.common.authorization import access_policy
        if "workflows.view" not in access_policy(ctx).permissions:
            return [], 0, False
        from app.modules.workflows.models import Workflow, WorkflowExecution
        from app.modules.workflows.service import _domain_filter
        from app.modules.workflows import execution
        quality_workflows = select(Workflow.id).select_from(Workflow).where(
            Workflow.workspace_id == ctx.workspace_id, Workflow.status != "deleted", _domain_filter(ctx, "quality"),
        )
        base = visible_records(ctx, WorkflowExecution, "workflows.view").where(
            WorkflowExecution.status == "completed", WorkflowExecution.workflow_id.in_(quality_workflows),
        )
        # Sort narrow references, never the potentially megabyte-sized saved JSON.
        # The resolver loads and authorizes each selected execution separately.
        ordered = base.with_only_columns(
            WorkflowExecution.id, WorkflowExecution.workflow_id, WorkflowExecution.status,
            maintain_column_froms=True,
        ).order_by(WorkflowExecution.created_at.desc(), WorkflowExecution.id)

        def resolve_workflow(row):
            # 逐条加载并授权完整执行视图，再从中抽取可选路径；结果值不随选项返回。
            execution_view = execution.get_execution(ctx, row.workflow_id, row.id)
            paths = list(islice(_source_paths(execution_view.result or {}), 120))
            fields = [ConcessionSourceField(key=path.rsplit("[", 1)[-1].strip("']") or path,
                                            path=path, dataType=data_type) for path, data_type in paths]
            workflow_name = str((execution_view.snapshot or {}).get("name") or row.workflow_id)
            return ConcessionSourceOption(kind="workflow", recordId=row.id, workflowId=row.workflow_id,
                label=f"{workflow_name} · {row.id}", status=row.status, fields=fields,
                hasOriginalWeight=any(field.key == "originalWeight" for field in fields),
                measurementCount=len(fields))

        return _authorized_page(ctx, ordered, resolve_workflow, limit=limit, offset=offset)
    if kind == "inspection":
        from app.modules.quality.inspections import get_inspection
        # 只列有效质检记录；逐条 get_inspection 复核设备/指标/图片等完整证据链权限。
        base = visible_records(ctx, QualityInspection, "quality.view").where(QualityInspection.status == "active")
        ordered = base.with_only_columns(QualityInspection.id, maintain_column_froms=True).order_by(
            QualityInspection.created_at.desc(), QualityInspection.id)

        def resolve_inspection(row):
            row = get_inspection(ctx, row.id)
            return ConcessionSourceOption(kind="inspection", recordId=row.id, inspectionId=row.id,
                sampleId=row.sample_id, label=f"质检记录 · {row.sample_id}", status=row.status, fields=[],
                hasOriginalWeight=inspection_original_weight(row) is not None,
                measurementCount=len(row.results or []))

        return _authorized_page(ctx, ordered, resolve_inspection, limit=limit, offset=offset)
    raise ValidationError("来源类型未被支持")


def get_rule(ctx: AuthContext, rule_id: str, action: str = "view", *, deleted: bool = False) -> QualityConcessionRule:
    """读取规则（``quality.<action>`` 权限）；默认拒绝已删除规则。

    ``deleted=True`` 仅供历史申请/审批回读使用（申请里固化了规则快照，规则本身可能已被移除）。
    """
    row = owned_record(ctx, QualityConcessionRule, rule_id, "quality." + action)
    if row.status == "deleted" and not deleted:
        raise NotFoundError("让步规则已移除")
    return row


def save_rule(ctx: AuthContext, payload: ConcessionRuleInput, rule_id: str | None = None) -> ConcessionRuleView:
    """新建/修改让步规则（新建需 ``quality.create``，修改需 ``quality.edit`` 且携带版本）。

    顺序：权限 → 敏感字段校验（规则文本不得夹带凭据）→ 乐观锁/新建 → 逐字段赋值 → flush → 审计。
    规则本身的取值约束（比例 ≤ 1、``exact`` 必须无小数位等）由 ``ConcessionRuleInput`` 校验，
    这里不重复判断。**修改规则不会影响已生成的申请**：申请中保存的是规则快照与规则版本，
    只有"审批时"才会再校验规则是否仍为 active 且版本未变（见 ``decide_request``）。
    """
    require_permission(ctx, "quality.edit" if rule_id else "quality.create")
    validate_body_secrets(payload.model_dump(mode="json"))
    values = payload.model_dump(exclude={"version"}, mode="json")
    if rule_id:
        row = get_rule(ctx, rule_id, "edit")
        check_version(row, payload.version)
        row.version += 1
    else:
        row = QualityConcessionRule(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.code = values["code"]
    row.name = values["name"]
    row.product_codes = values["productCodes"]
    row.metric_ids = values["metricIds"]
    row.weight_unit = values["weightUnit"]
    row.metric_unit = values["metricUnit"]
    row.standard_min = values["standardMin"]
    row.standard_max = values["standardMax"]
    row.min_deviation = values["minDeviation"]
    row.max_deviation = values["maxDeviation"]
    row.deduction_mode = values["deductionMode"]
    row.deduction_value = values["deductionValue"]
    row.rounding_scale = values["roundingScale"]
    row.rounding_mode = values["roundingMode"]
    row.status = values["status"]
    row.description = values["description"]
    flush_changes(ctx)
    commit_change(ctx, "quality.concession_rule.save", "quality.concession_rule", row.id, {"version": row.version})
    return rule_view(row)


def remove_rule(ctx: AuthContext, rule_id: str, version: int) -> None:
    """软删规则（``quality.delete`` + 正确版本）：置 ``deleted`` 并递增版本。

    不物理删除：历史申请与审批记录仍引用该规则及其快照，必须保持可追溯。
    """
    row = get_rule(ctx, rule_id, "delete")
    check_version(row, version)
    row.status, row.version = "deleted", row.version + 1
    commit_change(ctx, "quality.concession_rule.remove", "quality.concession_rule", row.id)


def _authorize_source(ctx: AuthContext, source: dict[str, Any]) -> None:
    """按来源类型校验当前账号对来源记录的权限。

    datasource → 需数据源可见（但注意 ``create_request`` 会拒绝把 datasource 直接当来源）；
    workflow → 需流程可见；inspection → 走 ``get_inspection`` 的完整证据链校验；
    manual → 无外部记录可校验（由调用方保证实测值由人工明确录入）。
    未知类型抛 ``ValidationError``。
    """
    kind = source.get("kind")
    if kind == "datasource":
        from app.common.owned_records import owned_record
        from app.models import DataSource
        owned_record(ctx, DataSource, source["datasourceId"], "datasources.view")
    elif kind == "workflow":
        from app.modules.workflows.service import get_workflow
        get_workflow(ctx, source["workflowId"])
    elif kind == "inspection":
        from app.modules.quality.inspections import get_inspection
        get_inspection(ctx, source["recordId"])
    elif kind != "manual":
        raise ValidationError("来源类型未被支持")


def request_view(row: QualityConcessionRequest) -> ConcessionRequestView:
    """申请行 → 对外视图。含候选值（``proposedDeduction`` / ``proposedSettlementWeight``）
    与最终值（``settlementWeight``）：只有 ``approved`` 的最终值非空，
    前端与下游结算必须只使用 ``settlementWeight``，不要把候选值当结果。
    """
    return ConcessionRequestView(id=row.id, version=row.version, requestKey=row.request_key,
        inspectionId=row.inspection_id, ruleId=row.rule_id, ruleVersion=row.rule_version, ruleSnapshot=row.rule_snapshot,
        sampleId=row.sample_id, productCode=row.product_code, weightUnit=row.weight_unit,
        source=row.source_snapshot, measurements=row.measurements, originalWeight=row.original_weight,
        deviation=row.deviation, proposedDeduction=row.proposed_deduction,
        proposedSettlementWeight=row.proposed_settlement_weight, settlementWeight=row.settlement_weight,
        status=row.status, reason=row.reason, decisionNote=row.decision_note, decidedBy=row.decided_by,
        decidedAt=row.decided_at.isoformat() if row.decided_at else None,
        createTime=row.created_at.isoformat(), updateTime=row.updated_at.isoformat())


def get_request(ctx: AuthContext, request_id: str, action: str = "view") -> QualityConcessionRequest:
    """读取让步申请（``quality.<action>``），并回溯校验其来源链的当前权限。

    校验：工作流来源 → 重新校验该执行；质检来源 → ``get_inspection``；规则（允许已删除）。
    因此来源记录被删/失权后，申请不再可见（防旁路）。只读。
    """
    row = owned_record(ctx, QualityConcessionRequest, request_id, "quality." + action)
    source = row.source_snapshot or {}
    if source.get("kind") == "workflow":
        from app.modules.workflows.execution import get_execution
        get_execution(ctx, source["workflowId"], source["recordId"])
    if row.inspection_id:
        from app.modules.quality.inspections import get_inspection
        get_inspection(ctx, row.inspection_id)
    if row.rule_id:
        get_rule(ctx, row.rule_id, deleted=True)
    return row


def _active_rules(ctx: AuthContext) -> list[QualityConcessionRule]:
    """本工作区当前可见且启用的规则，按业务编码排序（保证匹配顺序稳定、便于复现）。"""
    rows = ctx.db.scalars(visible_records(ctx, QualityConcessionRule, "quality.view").where(QualityConcessionRule.status == "active").order_by(QualityConcessionRule.code)).all()
    return list(rows)


def _match_rule(ctx: AuthContext, payload: ConcessionEvidenceInput, evidence: dict[str, Any]) -> tuple[QualityConcessionRule | None, dict[str, Any]]:
    """选择适用规则并试算，返回 ``(规则或 None, 评估结果)``。

    决策顺序（**唯一性优先**）：
    1) 指定了 ``ruleId``：直接用它（必须仍为 active），不自动换规则；
    2) 未指定：先按身份条件筛出候选，再逐个评估；恰好一条给出 ``pending`` 才自动采用；
    3) 同时命中多条 → 返回"需业务人员明确选择"（**绝不自动挑一条**，否则扣量口径会随机化）；
    4) 候选恰好一条 → 采用它（即便结果是需确认，也带上它的解释）；
    5) 无候选 → 需确认（没有唯一匹配的启用规则）。
    返回的规则对象用于固化 ``rule_id`` / ``rule_version`` / ``rule_snapshot``。
    """
    if payload.ruleId:
        row = get_rule(ctx, payload.ruleId, "view")
        if row.status != "active":
            return row, {"status": "needs_confirmation", "matched": False, "reason": "指定让步规则未启用"}
        return row, evaluate_rule(row, evidence)
    candidates = [row for row in _active_rules(ctx) if _identity_matches(_rule_values(row), evidence)[0]]
    evaluated = [(row, evaluate_rule(row, evidence)) for row in candidates]
    matched = [(row, result) for row, result in evaluated
               if result.get("status") == "pending" and result.get("matched")]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        return None, {"status": "needs_confirmation", "matched": False,
                      "reason": "实测偏差同时命中多个让步规则，需业务人员明确选择"}
    if len(candidates) == 1:
        return candidates[0], evaluated[0][1]
    if not candidates:
        return None, {"status": "needs_confirmation", "matched": False,
                      "reason": "没有唯一匹配的启用让步规则"}
    return None, {"status": "needs_confirmation", "matched": False,
                  "reason": "存在多个候选规则但当前证据不能唯一匹配，需补充数据或明确选择"}


def create_request(ctx: AuthContext, payload: ConcessionEvidenceInput) -> ConcessionRequestView:
    """提交让步申请：校验证据、匹配规则并落一条不可变申请（``quality.create``）。

    来源分支（``source.kind``）的强制约束 —— 目的都是"证据必须能被追溯到已授权记录"：
    - ``inspection``：``source.recordId`` 必须等于 ``inspectionId``，样本标识也必须一致；
      实测值一律用质检记录派生（客户端提交值只用于比对，不一致直接拒绝），
      原始重量只能取质检快照；快照无重量时**不允许客户端自带**（提示改用工作流或人工来源）。
    - ``workflow``：必须是 ``completed`` 的执行，实测值/重量/产品编码由
      ``derive_workflow_evidence`` 从已保存结果按声明路径重建（客户端值只做一致性核对）。
    - ``datasource``：**直接拒绝** —— 数据源必须先由授权工作流固化执行结果，
      不能把客户端字段伪装成"已读取的数据"。
    - ``manual``：至少一条实测值，且保持 manual 标记。
    非质检来源还要校验：原始重量与每条实测值都必须挂到已声明的来源字段上（``sourceField``），
    否则拒绝（防止来源不明的数值进入审批）。

    幂等：``request_key`` + 证据摘要 ``request_hash``；同键同证据返回既有申请，同键不同证据 409。
    并发：插入冲突（唯一约束）时会回滚并重查该键，避免双击提交造出两条申请。

    规则匹配：由 ``_match_rule`` 决定，只有唯一命中才得到 ``pending``（可审批），
    否则落 ``needs_confirmation`` 并附带 ``reason`` 供业务补充数据或明确选择规则。
    副作用：写 ``quality_concession_requests``（含规则快照）并写审计。
    """
    require_permission(ctx, "quality.create")
    evidence = payload.model_dump(mode="json", by_alias=True)
    validate_body_secrets(evidence)
    _authorize_source(ctx, evidence["source"])
    source_kind = evidence["source"].get("kind")
    inspection = None
    if source_kind == "inspection":
        if not payload.inspectionId or evidence["source"].get("recordId") != payload.inspectionId:
            raise ValidationError("质检记录来源必须与 inspectionId 一致")
        from app.modules.quality.inspections import get_inspection
        inspection = get_inspection(ctx, payload.inspectionId)
        if inspection.sample_id != payload.sampleId:
            raise ValidationError("让步申请样本标识必须与质检记录一致")
        if payload.measurements:
            # 客户端值只用于比对，任何不一致都拒绝；最终值一律取质检记录派生的结果。
            supplied = {item.metricId: item.model_dump(mode="json", by_alias=True) for item in payload.measurements}
            actual = {item["metricId"]: item for item in derive_inspection_measurements(inspection)}
            for metric_id, item in supplied.items():
                if item.get("value") != (actual.get(metric_id) or {}).get("value"):
                    raise ValidationError("质检记录来源的实测值不能由客户端替换")
        evidence["measurements"] = derive_inspection_measurements(inspection)
        stored_weight = inspection_original_weight(inspection)
        if stored_weight is None and payload.originalWeight is not None:
            raise ValidationError("质检记录快照没有原始重量；请改用已保存工作流来源或明确 manual 来源")
        if stored_weight is not None and payload.originalWeight is not None and payload.originalWeight != stored_weight:
            raise ValidationError("提交的原始重量与质检记录快照不一致")
        evidence["originalWeight"] = stored_weight
    elif source_kind == "workflow":
        if not evidence["source"].get("workflowId"):
            raise ValidationError("工作流来源必须指定 workflowId")
        from app.modules.workflows import execution
        execution_view = execution.get_execution(ctx, evidence["source"]["workflowId"], evidence["source"]["recordId"])
        if execution_view.status != "completed":
            raise ValidationError("只能从已完成的工作流执行记录生成让步申请")
        evidence = derive_workflow_evidence(execution_view.result or {}, evidence["source"], evidence)
    elif source_kind == "datasource":
        raise ValidationError("数据源来源必须先由授权工作流固化执行结果；不能把客户端字段伪装成已读取数据")
    elif source_kind == "manual" and not payload.measurements:
        raise ValidationError("手工来源至少需要一条实测值，并明确保持 manual 标记")
    if source_kind != "inspection":
        # 来源字段白名单：键（别名）与路径都算已声明，其余字段一律视为来源不明。
        source_fields = set(evidence["source"].get("fields", {}).keys()) | set(evidence["source"].get("fields", {}).values())
        if evidence.get("originalWeight") is not None and "originalWeight" not in source_fields:
            raise ValidationError("原始重量必须关联来源字段")
        missing_fields = [item.get("sourceField", "") for item in evidence.get("measurements", [])
                          if item.get("sourceField", "") not in source_fields]
        if missing_fields:
            raise ValidationError("实测值必须关联来源字段：" + ", ".join(missing_fields[:5]))
    if payload.inspectionId and inspection is None:
        from app.modules.quality.inspections import get_inspection
        inspection = get_inspection(ctx, payload.inspectionId)
        if inspection.sample_id != payload.sampleId:
            raise ValidationError("让步申请样本标识必须与质检记录一致")
    # 证据摘要用于幂等比对：排序键 + 紧凑分隔符，保证同等证据得到同等摘要。
    digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    existing = ctx.db.scalar(select(QualityConcessionRequest).where(
        QualityConcessionRequest.workspace_id == ctx.workspace_id,
        QualityConcessionRequest.owner_user_id == ctx.user_id,
        QualityConcessionRequest.request_key == payload.requestKey,
    ))
    if existing:
        if existing.request_hash != digest:
            raise ConflictError("此提交键已用于其他让步证据，请更换提交键")
        return request_view(get_request(ctx, existing.id))
    rule, result = _match_rule(ctx, payload, evidence)
    # 固化规则 id/版本/快照与试算结果；settlement_weight 留空，只有审批通过才赋值。
    values = {
        "workspace_id": ctx.workspace_id, "owner_user_id": ctx.user_id, "request_key": payload.requestKey,
        "request_hash": digest, "inspection_id": payload.inspectionId, "rule_id": rule.id if rule else None,
        "rule_version": rule.version if rule else None, "rule_snapshot": _rule_values(rule) if rule else {},
        "sample_id": payload.sampleId, "product_code": evidence.get("productCode", ""),
        "weight_unit": evidence["weightUnit"], "source_snapshot": evidence["source"],
        "measurements": result.get("measurements", evidence.get("measurements", [])),
        "original_weight": result.get("originalWeight", evidence.get("originalWeight")),
        "deviation": result.get("deviation"), "proposed_deduction": result.get("proposedDeduction"),
        "proposed_settlement_weight": result.get("proposedSettlementWeight"), "settlement_weight": None,
        "status": result.get("status", "needs_confirmation"), "reason": result.get("reason", "需补充业务确认"),
        "decision_note": "", "decided_by": None, "decided_at": None,
    }
    row = QualityConcessionRequest(**values)
    ctx.db.add(row)
    try:
        flush_changes(ctx)
        commit_change(ctx, "quality.concession.request", "quality.concession", row.id,
                      {"status": row.status, "rule_id": row.rule_id, "sample_id": row.sample_id})
    except ConflictError:
        # 并发双击：唯一约束命中后回滚并重查，返回已存在的那条（幂等语义）。
        ctx.db.rollback()
        existing = ctx.db.scalar(select(QualityConcessionRequest).where(
            QualityConcessionRequest.workspace_id == ctx.workspace_id,
            QualityConcessionRequest.owner_user_id == ctx.user_id,
            QualityConcessionRequest.request_key == payload.requestKey,
        ))
        if existing is None:
            raise
        if existing.request_hash != digest:
            raise ConflictError("此提交键已用于其他让步证据，请更换提交键")
        return request_view(get_request(ctx, existing.id))
    return request_view(row)


def list_requests(ctx: AuthContext, *, limit: int, offset: int, status: str = "") -> list[ConcessionRequestView]:
    """分页列出让步申请（``quality.view``），可按处置状态过滤。

    排序用窄列（id）而非整行，随后逐条经 ``get_request`` 复核来源链权限；
    不可见记录不计入偏移，因此返回条数可能少于请求条数（属预期）。
    """
    require_permission(ctx, "quality.view")
    query = visible_records(ctx, QualityConcessionRequest, "quality.view")
    if status:
        query = query.where(QualityConcessionRequest.status == status)
    ordered = query.with_only_columns(QualityConcessionRequest.id, maintain_column_froms=True).order_by(
        QualityConcessionRequest.created_at.desc(), QualityConcessionRequest.id)
    items, _, _ = _authorized_page(ctx, ordered, lambda row: request_view(get_request(ctx, row.id)),
                                   limit=limit, offset=offset)
    return items


def decide_request(ctx: AuthContext, request_id: str, payload: ConcessionDecisionInput) -> ConcessionRequestView:
    """审批让步申请（``quality.review``）：批准或驳回，并记录审批人与时间。

    可审批的前提（任一不满足即拒绝）：
    - 申请状态必须是 ``pending`` 且已有候选结算重量（``needs_confirmation`` 说明规则不唯一或
      证据不足，**不能审批**，需先补数据或指定规则）；
    - ``payload.version`` 必须是读到的当前版本（行锁 + 乐观锁，防止覆盖他人决策）。
    批准时的额外校验（关键安全约束）：申请里固化的规则必须**仍然存在、仍为 active、
    且版本未变**，否则报 409 要求按最新规则重新评估 ——
    防止"提交后规则被改小扣量"这类先提交再改规则的操作绕过审批口径。
    副作用：更新 status / decision_note / decided_by / decided_at（UTC）并递增版本；
    仅批准时写入 ``settlement_weight = proposed_settlement_weight``，驳回时保持为空；
    写审计（``quality.concession.approved|rejected``）。不可修改证据与规则快照。
    """
    require_permission(ctx, "quality.review")
    row = get_request(ctx, request_id, "review")
    # 行锁 + populate_existing：拿最新库值，避免两个审批人基于旧状态各批一次。
    row = ctx.db.scalar(select(QualityConcessionRequest).where(
        QualityConcessionRequest.workspace_id == ctx.workspace_id,
        QualityConcessionRequest.id == row.id,
    ).execution_options(populate_existing=True).with_for_update())
    if row is None:
        raise NotFoundError("让步申请已不存在")
    check_version(row, payload.version)
    if row.status != "pending" or not row.proposed_settlement_weight:
        raise ValidationError("缺少唯一规则或申请已处理，不能审批结算重量")
    if payload.decision == "approved":
        if not row.rule_id or row.rule_version is None:
            raise ValidationError("申请缺少固定让步规则，不能批准")
        current_rule = get_rule(ctx, row.rule_id, "view", deleted=True)
        current_rule = ctx.db.scalar(select(QualityConcessionRule).where(
            QualityConcessionRule.workspace_id == ctx.workspace_id,
            QualityConcessionRule.id == current_rule.id,
        ).execution_options(populate_existing=True).with_for_update())
        if current_rule is None:
            raise ConflictError("让步规则已不存在，请按最新规则重新评估")
        if current_rule.status != "active" or current_rule.version != row.rule_version:
            raise ConflictError("让步规则已停用或发生变化，请按最新规则重新评估")
    row.version += 1
    row.status = payload.decision
    row.decision_note = payload.note
    row.decided_by = ctx.user_id
    # 审批时间统一存 UTC（本地展示时再按用户时区换算）。
    row.decided_at = datetime.utcnow()
    row.settlement_weight = row.proposed_settlement_weight if payload.decision == "approved" else None
    commit_change(ctx, "quality.concession." + payload.decision, "quality.concession", row.id,
                  {"status": row.status, "settlement_weight": row.settlement_weight,
                   "rule_version": row.rule_version})
    return request_view(row)
