"""对已授权查询快照执行固定处理步骤，保留原始行号，不执行SQL或任意脚本。"""
from copy import deepcopy
import json
import operator
from time import monotonic

from pydantic import TypeAdapter, ValidationError

from agent_platform.core.errors import DomainError
from .schemas import ProcessingOperation
from .values import ARITHMETIC, calculate, decimal_value, json_scalar, missing, typed

OPERATIONS = TypeAdapter(list[ProcessingOperation])
COMPARISONS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le}


def failure(message, path, row_number=None):
    fields = {"path": path}
    if row_number is not None:
        fields["row_number"] = row_number
    return DomainError(422, "data_processing_invalid", message, fields)


class Budget:
    """纯处理有独立时间/工作量预算，失败不重试，也不把正常源标记为连接异常。"""
    def __init__(self):
        self.deadline, self.work = monotonic() + 2, 0

    def spend(self, amount=1):
        self.work += amount
        if self.work > 2_000_000 or monotonic() > self.deadline:
            raise DomainError(422, "data_processing_budget_exceeded", "处理工作量超出预算，请减少步骤或缩小数据范围")


def key(value):
    """区分空值、文字和布尔，避免旧str(None)与真实文本同键。"""
    return (type(value).__name__, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def require_fields(fields, columns, path):
    if len(set(fields)) != len(fields) or not set(fields).issubset(columns):
        raise failure("字段不存在或重复", path)


def validate_condition(condition, columns, path):
    require_fields([condition["field"]], columns, path + ".field")
    op, value = condition["operator"], condition.get("value")
    if op in ("is_null", "not_null"):
        if value is not None:
            raise failure("空值判断无需填写值", path + ".value")
        return
    if op == "contains" and condition["data_type"] != "text":
        raise failure("包含文本只用于文字类型", path + ".data_type")
    if condition["data_type"] == "boolean" and op in ("gt", "gte", "lt", "lte"):
        raise failure("布尔值只支持相等和包含判断", path + ".operator")
    values = value if isinstance(value, list) else [value]
    if (op in ("in", "not_in")) != isinstance(value, list) or not 1 <= len(values) <= 100 or any(missing(item) for item in values):
        raise failure("请填写有效比较值，包含条件使用1至100项数组", path + ".value")
    try:
        for item in values:
            typed(item, condition["data_type"])
    except (ValueError, ArithmeticError) as exc:
        raise failure(str(exc), path + ".value") from exc


def matches(row, condition):
    value, op = row.get(condition["field"]), condition["operator"]
    if op in ("is_null", "not_null"):
        return missing(value) if op == "is_null" else not missing(value)
    if missing(value):
        return False
    left = typed(value, condition["data_type"])
    right = condition["value"]
    if op in ("in", "not_in"):
        included = left in [typed(item, condition["data_type"]) for item in right]
        return included if op == "in" else not included
    right = typed(right, condition["data_type"])
    return right in left if op == "contains" else COMPARISONS[op](left, right)


def validate_expression(node, columns, path, *, depth=1, counter=None):
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if depth > 8 or counter[0] > 100:
        raise failure("派生表达式过于复杂", path)
    if "field" in node:
        require_fields([node["field"]], columns, path + ".field")
    elif "value" in node:
        # 常量必须也经过有限标量校验；文字空格保留给拼接，不自动修剪。
        value = node["value"]
        if isinstance(value, str) and len(value) > 10000:
            raise failure("常量文字过长", path + ".value")
        if isinstance(value, float):
            try:
                decimal_value(value)
            except (ValueError, ArithmeticError) as exc:
                raise failure(str(exc), path + ".value") from exc
    else:
        op, args = node["op"], node["args"]
        if op in ("add", "subtract", "multiply", "divide") and len(args) != 2 or op == "round" and len(args) != 1:
            raise failure("算术需要两个参数，舍入需要一个参数", path + ".args")
        if op != "round" and node.get("digits") is not None:
            raise failure("只有舍入可以设置小数位数", path + ".digits")
        for index, child in enumerate(args):
            validate_expression(child, columns, path + f".args.{index}", depth=depth + 1, counter=counter)


def expression_value(node, row, budget):
    budget.spend()
    if "field" in node:
        return row.get(node["field"])
    if "value" in node:
        return node["value"]
    op = node["op"]
    if op == "coalesce":
        for child in node["args"]:
            result = expression_value(child, row, budget)
            if not missing(result):
                return result
        return None
    values = [expression_value(child, row, budget) for child in node["args"]]
    if any(missing(value) for value in values):
        return None
    if op == "concat":
        result = "".join(typed(value, "text") for value in values)
        if len(result) > 10000:
            raise ValueError("派生文字超过长度上限")
        return result
    return calculate(op, [decimal_value(value) for value in values], digits=node.get("digits") if node.get("digits") is not None else 2)


def aggregate(rows, columns, lineages, spec, budget, path):
    groups, definitions = spec["group_by"], spec["aggregations"]
    require_fields(groups, columns, path + ".group_by")
    names = [item["name"] for item in definitions]
    if len(set(names)) != len(names) or set(names).intersection(groups):
        raise failure("聚合名称须唯一且不能覆盖分组字段", path + ".aggregations")
    for index, item in enumerate(definitions):
        if item["function"] != "count" and item.get("field") is None:
            raise failure("请选择聚合字段", path + f".aggregations.{index}.field")
        require_fields([item[field] for field in ("field", "unit_field") if item.get(field)], columns, path + f".aggregations.{index}")
        if item["function"] in ("sum", "avg") and item["data_type"] != "decimal":
            raise failure("求和与平均值需要数值类型", path + f".aggregations.{index}.data_type")
    grouped = {}
    for row, lineage in zip(rows, lineages):
        budget.spend()
        group_key = tuple(key(row.get(field)) for field in groups)
        grouped.setdefault(group_key, []).append((row, lineage))
    if not rows and not groups:
        grouped[()] = []
    output, output_lineages = [], []
    counts = [{"name": item["name"], "function": item["function"], "input_count": 0, "value_count": 0, "missing_count": 0} for item in definitions]
    for members in grouped.values():
        result = {field: members[0][0].get(field) for field in groups}
        for index, item in enumerate(definitions):
            selected = [(row, lineage) for row, lineage in members if item.get("field") is None or not missing(row.get(item["field"]))]
            counts[index]["input_count"] += len(members)
            counts[index]["value_count"] += len(selected)
            counts[index]["missing_count"] += len(members) - len(selected)
            budget.spend(len(members))
            function = item["function"]
            try:
                units = {key(row.get(item["unit_field"])) for row, _ in selected} if item.get("unit_field") else set()
                if item.get("unit_field") and (len(units) > 1 or any(missing(row.get(item["unit_field"])) for row, _ in selected)):
                    raise ValueError("计量单位不一致或缺失，请按单位分组")
                if function == "count":
                    value = len(selected)
                elif function == "count_distinct":
                    value = len({key(row.get(item["field"])) for row, _ in selected})
                else:
                    values = []
                    for row, lineage in selected:
                        try:
                            values.append(typed(row[item["field"]], item["data_type"]))
                        except (ValueError, ArithmeticError) as exc:
                            raise failure(str(exc), path + f".aggregations.{index}.field", lineage[0] if lineage else None) from exc
                    value = None if not values else calculate(function, values) if function in ("sum", "avg") else min(values) if function == "min" else max(values)
                result[item["name"]] = json_scalar(value)
            except (ValueError, ArithmeticError) as exc:
                raise failure(str(exc), path + f".aggregations.{index}") from exc
        output.append(result)
        output_lineages.append(sorted({source for _, lineage in members for source in lineage}))
    return output, groups + names, output_lineages, counts


def run_pipeline(source_rows, source_columns, operations):
    """按顺序处理副本，任一步失败都不返回部分成功；原查询与行号保持可追溯。"""
    try:
        operations = [item.model_dump() for item in OPERATIONS.validate_python(operations)]
    except ValidationError as exc:
        error = exc.errors()[0]
        raise failure("处理步骤配置无效", "operations." + ".".join(str(item) for item in error["loc"])) from exc
    if not 1 <= len(operations) <= 20 or len(source_rows) > 10000:
        raise failure("步骤或输入记录数超出范围", "operations")
    rows, columns = deepcopy(source_rows), list(source_columns)
    lineages, summaries, budget = [[index + 1] for index in range(len(rows))], [], Budget()
    for index, operation in enumerate(operations):
        path, kind, before = f"operations.{index}", operation["type"], len(rows)
        coerced_rows, aggregation_stats = [], None
        if kind in ("select", "deduplicate"):
            fields = operation.get("fields") or columns
            require_fields(fields, columns, path + ".fields")
            if kind == "select":
                rows, columns = [{field: row.get(field) for field in fields} for row in rows], fields
            else:
                seen, kept, kept_lineages = set(), [], []
                for row, lineage in zip(rows, lineages):
                    signature = tuple(key(row.get(field)) for field in fields)
                    if signature not in seen:
                        seen.add(signature)
                        kept.append(row)
                        kept_lineages.append(lineage)
                rows, lineages = kept, kept_lineages
        elif kind in ("filter", "drop_null"):
            conditions = operation.get("conditions") or [{"field": operation["field"], "operator": "not_null", "data_type": "text"}]
            for condition_index, condition in enumerate(conditions):
                validate_condition(condition, columns, path + f".conditions.{condition_index}")
            kept, kept_lineages = [], []
            for row, lineage in zip(rows, lineages):
                budget.spend(len(conditions))
                try:
                    results = [matches(row, condition) for condition in conditions]
                    retain = any(results) if operation.get("match") == "any" else all(results)
                except (ValueError, ArithmeticError, TypeError) as exc:
                    raise failure(str(exc), path + ".conditions", lineage[0] if lineage else None) from exc
                if retain:
                    kept.append(row)
                    kept_lineages.append(lineage)
            rows, lineages = kept, kept_lineages
        elif kind in ("rename", "cast", "derive"):
            target = operation.get("output_field") or operation.get("field")
            if target in columns and not (kind == "cast" and target == operation["field"]):
                raise failure("输出字段已存在", path + ".output_field")
            if kind != "derive":
                require_fields([operation["field"]], columns, path + ".field")
            else:
                validate_expression(operation["expression"], columns, path + ".expression")
            for row, lineage in zip(rows, lineages):
                budget.spend()
                try:
                    value = row.get(operation["field"]) if kind == "rename" else typed(row.get(operation["field"]), operation["data_type"]) if kind == "cast" else expression_value(operation["expression"], row, budget)
                    row[target] = json_scalar(value)
                except (ValueError, ArithmeticError, TypeError) as exc:
                    if operation.get("on_error", "fail") == "null":
                        row[target] = None
                        coerced_rows.extend(lineage)
                    else:
                        raise failure(str(exc), path + (".expression" if kind == "derive" else ".field"), lineage[0] if lineage else None) from exc
                if kind == "rename":
                    row.pop(operation["field"])
            if kind == "rename":
                columns = [target if field == operation["field"] else field for field in columns]
            elif target not in columns:
                columns.append(target)
        else:
            rows, columns, lineages, aggregation_stats = aggregate(rows, columns, lineages, operation, budget, path)
        budget.spend(before)
        if len(columns) > 100:
            raise failure("输出字段数量超出范围", path)
        summary = {"step": index + 1, "type": kind, "input_rows": before, "output_rows": len(rows)}
        if coerced_rows:
            summary.update(coerced_null_count=len(set(coerced_rows)), coerced_null_row_numbers=sorted(set(coerced_rows)))
        if aggregation_stats is not None:
            summary["aggregation_stats"] = aggregation_stats
        summaries.append(summary)
        if len(json.dumps(rows, ensure_ascii=False, default=str).encode()) > 8 * 1024 * 1024:
            raise DomainError(422, "data_processing_budget_exceeded", "处理结果超过大小上限，请减少字段或缩小范围")
    return {"columns": columns, "rows": rows, "row_count": len(rows), "source_row_numbers": lineages, "steps": summaries, "arithmetic": ARITHMETIC}
