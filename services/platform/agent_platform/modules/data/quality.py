"""已授权快照的质量规则；完整失败行号与有限原值示例，不修改或重新查询源数据。"""
from pydantic import ValidationError

from .processing import Budget, COMPARISONS, key
from .schemas import QualityRule
from .values import decimal_value, missing, typed
from agent_platform.core.errors import DomainError


def invalid(message, path):
    return DomainError(422, "data_quality_rule_invalid", message, {"path": path})


def normalize_rules(data, columns):
    rules = [({"type": "required", "field": field, "allow_missing": False}, f"required_fields.{i}") for i, field in enumerate(data.get("required_fields", []))]
    rules += [({"type": "unique", "field": field, "allow_missing": False}, f"unique_fields.{i}") for i, field in enumerate(data.get("unique_fields", []))]
    rules += [(rule, f"rules.{i}") for i, rule in enumerate(data.get("rules", []))]
    if not rules:
        raise invalid("请至少选择一项检查规则", "rules")
    normalized = []
    for rule, path in rules:
        try:
            rule = QualityRule.model_validate(rule).model_dump()
        except ValidationError as exc:
            raise invalid("质量规则配置无效", path) from exc
        if rule["field"] not in columns:
            raise invalid("规则引用了查询外字段", path + ".field" if path.startswith("rules.") else path)
        if rule.get("other_field") is not None and rule["other_field"] not in columns:
            raise invalid("规则引用了查询外字段", path + ".other_field")
        kind = rule["type"]
        try:
            if kind == "range":
                low = decimal_value(rule["minimum"]) if rule["minimum"] is not None else None
                high = decimal_value(rule["maximum"]) if rule["maximum"] is not None else None
                if low is None and high is None or low is not None and high is not None and low > high:
                    raise ValueError("请设置有效上下限")
            elif kind == "enum":
                if not rule["values"] or any(missing(value) for value in rule["values"]):
                    raise ValueError("请填写非空允许值")
                for value in rule["values"]:
                    typed(value, rule["data_type"])
            elif kind == "length":
                low, high = rule["min_length"], rule["max_length"]
                if low is None and high is None or low is not None and high is not None and low > high:
                    raise ValueError("请设置有效长度范围")
            elif kind == "compare" and rule["other_field"] is None:
                raise ValueError("请选择另一个比较字段")
            elif kind == "compare" and rule["data_type"] == "boolean" and rule["operator"] not in ("eq", "ne"):
                raise ValueError("布尔字段只支持相等比较")
        except (ValueError, ArithmeticError) as exc:
            raise invalid(str(exc), path) from exc
        if kind == "required":
            rule["allow_missing"] = False
        normalized.append(rule)
    return normalized


def reason_for(row, rule):
    value, kind = row.get(rule["field"]), rule["type"]
    if kind == "unique":
        return None
    if missing(value):
        return "缺少必填值" if kind == "required" or not rule["allow_missing"] else None
    if kind == "required":
        return None
    try:
        if kind == "range":
            number = decimal_value(value)
            if rule["minimum"] is not None and number < decimal_value(rule["minimum"]):
                return "低于最小值"
            if rule["maximum"] is not None and number > decimal_value(rule["maximum"]):
                return "超过最大值"
        elif kind == "enum":
            if typed(value, rule["data_type"]) not in [typed(item, rule["data_type"]) for item in rule["values"]]:
                return "不在允许值中"
        elif kind == "type":
            typed(value, rule["data_type"])
        elif kind == "length":
            if not isinstance(value, str):
                return "值不是文字"
            if rule["min_length"] is not None and len(value) < rule["min_length"]:
                return "文字长度不足"
            if rule["max_length"] is not None and len(value) > rule["max_length"]:
                return "文字长度超出范围"
        elif kind == "compare":
            other = row.get(rule["other_field"])
            if missing(other):
                return None if rule["allow_missing"] else "比较字段缺值"
            if not COMPARISONS[rule["operator"]](typed(value, rule["data_type"]), typed(other, rule["data_type"])):
                return "字段比较不满足条件"
    except (ValueError, ArithmeticError, TypeError) as exc:
        return str(exc)
    return None


def check_quality(rows, columns, data, *, truncated=False):
    rules, checks, budget = normalize_rules(data, columns), [], Budget()
    for rule in rules:
        failures, skipped, duplicate_excess = {}, 0, 0
        groups = {}
        for row_number, row in enumerate(rows, 1):
            budget.spend()
            value = row.get(rule["field"])
            if rule["type"] != "required" and rule["allow_missing"] and (missing(value) or rule["type"] == "compare" and missing(row.get(rule["other_field"]))):
                skipped += 1
                continue
            reason = reason_for(row, rule)
            if reason:
                failures[row_number] = reason
            elif rule["type"] == "unique":
                try:
                    signature = key(typed(value, rule["data_type"]))
                    groups.setdefault(signature, []).append(row_number)
                except (ValueError, ArithmeticError) as exc:
                    failures[row_number] = str(exc)
        if rule["type"] == "unique":
            for members in groups.values():
                if len(members) > 1:
                    duplicate_excess += len(members) - 1
                    failures.update({number: "与其他行重复" for number in members})
        numbers, examples = sorted(failures), []
        for number in numbers[:20]:
            value = rows[number - 1].get(rule["field"])
            example = {"row_number": number, "value": preview(value), "reason": failures[number]}
            if isinstance(value, (dict, list)) or isinstance(value, str) and len(value) > 500:
                example["value_truncated"] = True
            if rule["type"] == "compare":
                other = rows[number - 1].get(rule["other_field"])
                example["other_value"] = preview(other)
            examples.append(example)
        checks.append({"name": rule["name"] or rule["field"], "field": rule["field"], "rule": rule["type"], "rule_definition": rule,
                       "outcome": "failed" if numbers else "incomplete" if truncated or not rows else "passed",
                       "count": duplicate_excess + sum(reason != "与其他行重复" for reason in failures.values()) if rule["type"] == "unique" else len(numbers),
                       "affected_count": len(numbers), "row_numbers": numbers, "skipped_count": skipped,
                       "examples": examples, "examples_truncated": len(numbers) > 20})
    issues = [check for check in checks if check["affected_count"]]
    return {"rows_checked": len(rows), "truncated": truncated, "scope": "returned_rows",
            "outcome": "failed" if issues else "incomplete" if truncated or not rows else "passed", "issues": issues, "checks": checks}


def preview(value):
    """示例有明确大小边界，完整原值仍由同一查询的原始行读取。"""
    if isinstance(value, dict):
        return {"type": "object", "keys": [str(item)[:80] for item in list(value)[:20]]}
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    return value[:500] if isinstance(value, str) else value
