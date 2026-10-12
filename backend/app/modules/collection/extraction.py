"""JSONPath record/field extraction with explicit types and bounded evaluation."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import islice
from typing import Any

from jsonpath import JSONPath, JSONPathEnvironment
from jsonpath.exceptions import JSONPathError
from jsonpath.selectors import Filter, SliceSelector, WildcardSelector

from app.errors import ValidationError
from app.modules.collection.transport import validate_response_tree

MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_OPERATIONS = 3_000_000
MAX_EVALUATION_SECONDS = 5


@dataclass
class EvaluationBudget:
    """One response's shared budget, including every field and nested filter."""

    deadline: float
    remaining: int = MAX_OPERATIONS

    def consume(self, count: int = 1) -> None:
        """扣减操作预算；预算耗尽或超过截止时间即抛 ``ValidationError``。

        预算与 deadline 必须同时生效：单靠计数挡不住“少量操作但每次都很慢”的路径
        （例如大数组切片），单靠时间又挡不住海量小操作。
        """
        self.remaining -= count
        if self.remaining < 0 or time.monotonic() > self.deadline:
            raise ValidationError("JSONPath 提取超过计算预算，请缩小响应范围或简化路径")


_budget: ContextVar[EvaluationBudget | None] = ContextVar("collection_path_budget", default=None)


def consume(count: int = 1) -> None:
    """Charge collection traversal without sharing state between requests."""

    # 记账挂在 ContextVar 上而不是全局计数器：并发请求各算各的，也不会因为某次
    # 提取异常而污染其他请求的预算；没有预算上下文（未在 evaluation_budget 内）时是空操作。
    if budget := _budget.get():
        budget.consume(count)


class BudgetDict(dict):
    """Count traversal by wildcard/recursive selectors as well as item access."""

    nodes = 1

    def __getitem__(self, key):
        """索引取值也记一次操作，避免 filter/通配符之外的下标访问绕过预算。"""
        consume()
        return super().__getitem__(key)

    def __iter__(self):
        """遍历逐项记账：JSONPath 通配符与递归选择器最终都会落到这里。"""
        for key in super().__iter__():
            consume()
            yield key

    def items(self):
        """items() 是等价于 iter() 的遍历路径，同样必须记账。"""
        for pair in super().items():
            consume()
            yield pair

    def values(self):
        """values() 同上，漏掉任意一个遍历入口都会让预算形同虚设。"""
        for item in super().values():
            consume()
            yield item


class BudgetList(list):
    """An array remains an array; no JSONPath library monkey-patching is needed."""

    nodes = 1

    def __iter__(self):
        """遍历逐项记账；切片返回值重新包装并统计节点数，保证预算跨切片累计。"""
        for item in super().__iter__():
            consume()
            yield item

    def __getitem__(self, key):
        """下标/切片访问记账；切片额外按长度计费并保留 nodes 估算。"""
        consume()
        value = super().__getitem__(key)
        if isinstance(key, slice):
            consume(len(value))
            result = BudgetList(value)
            result.nodes = sum(getattr(item, "nodes", 1) for item in value) + 1
            return result
        return value


def budget_tree(value: Any) -> Any:
    """Wrap JSON containers once, retaining exact decimals and original strings."""
    consume()
    if isinstance(value, dict):
        result = BudgetDict({key: budget_tree(item) for key, item in value.items()})
        result.nodes = 1 + sum(getattr(item, "nodes", 1) for item in dict.values(result))
        return result
    if isinstance(value, list):
        result = BudgetList([budget_tree(item) for item in value])
        result.nodes = 1 + sum(getattr(item, "nodes", 1) for item in list.__iter__(result))
        return result
    return value


class ExtractionEnvironment(JSONPathEnvironment):
    """Standard paths and comparisons, without executable or regex extensions."""

    max_recursion_depth = 48

    def setup_function_extensions(self) -> None:
        """移除 match/search 两个正则函数扩展。

        无界回溯的正则不是安全的字段提取操作：用户提供的路径可以构造灾难性回溯，
        在服务端占满 CPU，因此这里不开放正则函数（其余函数保持可用）。
        """
        super().setup_function_extensions()
        # Unbounded backtracking regex is not a safe field-extraction operation.
        self.function_extensions.pop("match", None)
        self.function_extensions.pop("search", None)

    def getitem(self, obj: Any, key: Any) -> Any:
        """所有 JSONPath 取值都会经过这里，因此它是最可靠的记账点。"""
        consume()
        return super().getitem(obj, key)

    def compare(self, left: object, operator: str, right: object) -> bool:
        """比较操作按两侧节点数量计费：过滤条件可能作用于整个数组，成本必须计入预算。"""
        consume(getattr(left, "nodes", 1) + getattr(right, "nodes", 1))
        return super().compare(left, operator, right)


_environment = ExtractionEnvironment(strict=True, filter_caching=False)


@lru_cache(maxsize=512)
def compile_path(path: str):
    """Validate a rooted JSONPath without executing it or returning parser internals."""
    if not isinstance(path, str) or not path.strip().startswith("$") or len(path) > 500:
        raise ValueError("JSONPath 必须从 $ 开始，最长 500 字符")
    try:
        return _environment.compile(path.strip())
    except (JSONPathError, RecursionError) as exc:
        raise ValueError("JSONPath 格式无效或使用了未开放的函数；支持字段、索引、切片、递归和条件过滤") from exc


def iter_matches(path: str, value: Any):
    """Evaluate against JSON values, including scalar strings, without re-parsing them."""
    consume()
    # The library treats a plain input string as serialized JSON. A wrapper
    # makes scalar records unambiguous and preserves $ as the record's root.
    if isinstance(value, str):
        if path.strip() == "$":
            yield value
        return
    try:
        for item in compile_path(path).finditer(value):
            consume()
            yield item.value
    except (JSONPathError, ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("JSONPath 无法应用于当前响应，请检查路径和字段类型") from exc


def matches(path: str, value: Any, *, limit: int) -> list[Any]:
    """Cap field matches, without silently selecting the first of multiple values."""
    return list(islice(iter_matches(path, value), limit))


def empty_selection(path: str, value: Any) -> bool:
    """Allow an empty wildcard/filter/slice; missing named fields remain errors."""
    if isinstance(value, str):
        return False
    compiled = compile_path(path)
    for index, segment in enumerate(compiled.segments):
        prefix = JSONPath(env=_environment, segments=compiled.segments[:index + 1])
        if next(iter(prefix.finditer(value)), None) is not None:
            continue
        if not all(isinstance(item, (Filter, SliceSelector, WildcardSelector)) for item in segment.selectors):
            return False
        parent = JSONPath(env=_environment, segments=compiled.segments[:index])
        return any(isinstance(item.value, (list, dict)) for item in parent.finditer(value))
    return False


def json_value(value: Any) -> Any:
    """Keep decimal precision and identifiers when returning data to JavaScript."""
    consume()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("结果不能包含非有限数")
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_value(item) for item in value]
    return value


def convert_value(value: Any, field_type: str) -> Any:
    """Apply only the explicitly selected conversion; never coerce identifiers."""
    if value is None or field_type in {"auto", "json"}:
        return json_value(value)
    if field_type == "string":
        if isinstance(value, (dict, list)):
            return json.dumps(json_value(value), ensure_ascii=False, allow_nan=False)
        return "true" if value is True else "false" if value is False else str(value)
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        if value in (0, "0", "false", "False"):
            return False
        if value in (1, "1", "true", "True"):
            return True
        raise ValueError("不是明确的布尔值")
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise ValueError("不是数值")
    number = Decimal(str(value).strip())
    if not number.is_finite():
        raise ValueError("不是有限数")
    if abs(number.adjusted()) > 1000 or abs(number.as_tuple().exponent) > 1000 or len(number.as_tuple().digits) > 1000:
        raise ValueError("数值超出支持的精度或数量级")
    if field_type == "decimal":
        return str(number)
    if number != number.to_integral_value():
        raise ValueError("整数转换会丢失小数")
    return json_value(int(number))


def select_fields(record: Any, fields: list) -> dict:
    """Extract selected fields, distinguishing missing, null, zero and multi-match."""
    if not fields:
        row = record if isinstance(record, dict) else {"value": record}
        if len(row) > 200:
            raise ValidationError("记录字段超过 200 个，请明确选择需要的输出字段")
        return json_value(row)
    output = {}
    for field in fields:
        values = matches(field.path, record, limit=10001 if field.multiple else 2)
        if not field.multiple and len(values) > 1:
            raise ValidationError("字段路径匹配多个值，请启用多值或缩小路径", field=field.name)
        if len(values) > 10000:
            raise ValidationError("单个多值字段超过 10000 项", field=field.name)
        if field.required and (not values or any(value is None or value == "" for value in values)):
            raise ValidationError("必填字段缺失或为空", field=field.name)
        try:
            converted = [convert_value(value, field.type) for value in values]
        except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
            raise ValidationError("字段不能转换为指定类型", field=field.name, expected=field.type) from exc
        output[field.name] = converted if field.multiple else converted[0] if converted else None
    return output


@contextmanager
def evaluation_budget(deadline: float):
    """Scope the entire extraction so one pathological field cannot stall a batch."""
    token = _budget.set(EvaluationBudget(min(deadline, time.monotonic() + MAX_EVALUATION_SECONDS)))
    try:
        yield
    finally:
        _budget.reset(token)


def extract_records(payload: Any, config, *, limit: int, deadline: float) -> tuple[list[dict], bool]:
    """Select records, flatten selected arrays once, and extract fields atomically."""
    validate_response_tree(payload)
    with evaluation_budget(deadline):
        value = budget_tree(payload)
        selected = iter_matches(config.record_path, value)
        found = False
        output = []
        size = 0
        for item in selected:
            found = True
            records = item if isinstance(item, list) else [item]
            for record in records:
                if len(output) >= limit:
                    return output, True
                row = select_fields(record, config.fields)
                size += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                if size > MAX_RESULT_BYTES:
                    raise ValidationError("提取结果超过 8 MB，请减少输出字段或最大行数")
                output.append(row)
        if not found and not empty_selection(config.record_path, value):
            raise ValidationError("记录 JSONPath 没有匹配项，请检查响应结构；空数组与路径缺失不同")
        return output, False
