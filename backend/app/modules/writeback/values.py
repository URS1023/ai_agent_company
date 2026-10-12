"""Explicit JSONPath mapping and loss-aware conversion to reflected SQL types.

中文补充（写回取值与类型转换）：
- 职责：把上游节点的数据按节点配置（JSONPath 或字面量）映射成目标表的行，并在
  写入前做严格类型转换。原则是"宁可拒绝，不静默损失"：不做隐式四舍五入、
  不把容器转成字符串、不截断超出长度的值——转换失败直接抛 ValidationError，
  因为写错的外部数据无法自动纠正。
- 上游完整性：value 带 truncated=true / success=false / status ∈ {partial, failed}
  时整批拒绝，避免把截断的中间结果写进业务表。
- 批量上界：记录必须是对象数组且不超过 1000 行；不静默截断（超限即报错）。
- 性能与限额：JSONPath 求值与逐行转换都受 RuleBudget（deadline + 步数）约束。
"""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import math
from typing import Any

from sqlalchemy import Column, Table, types

from app.errors import ValidationError
from app.modules.collection.extraction import budget_tree, evaluation_budget, matches
from app.modules.workflows.expressions import RuleBudget
from app.modules.writeback.schemas import OutputTableConfig


def single(path: str, value: Any) -> Any:
    """Extract exactly one value, retaining arrays for the record selector.

    中文补充：JSONPath 必须**恰好命中一个值**：0 个（缺失）或多个（数组）都拒绝，
    避免"取到第一个"造成的静默错值。limit=2 是为了区分"一个"与"多个"，
    不需要枚举全部匹配项。异常：抛 ValidationError。
    """
    found = matches(path, value, limit=2)
    if len(found) != 1:
        raise ValidationError("写入映射的 JSONPath 必须匹配一个值，不接受缺失或多值")
    return found[0]


def convert(value: Any, column: Column, provider: str) -> Any:
    """Never stringify containers into text or silently round precise decimal input.

    中文补充：单个值 → 目标字段类型的受控转换。要点：
    - NULL 只有在字段可空时才允许，否则报错（不偷偷写默认值）；
    - Integer 按字段位宽（含 TINYINT/MEDIUMINT/BIGINT 与 unsigned）做范围校验，
      且必须是整数值；Float 要求有限；Numeric 保留 Decimal，超小数位直接拒绝；
    - SQLite 的 NUMERIC 用整数存储，超出 2^53 或非整数会提示改用 TEXT/缩放整数列；
    - DateTime/Time 的时区信息必须与字段定义一致（有 tz 对无 tz 是数据错误）；
    - String 不接受 dict/list/bool，含 NUL 或超长即拒绝，Enum 还要在允许值内；
    - 未支持的类型一律拒绝（不降级为字符串）。
    异常：一律抛 ValidationError，并带上具体字段名便于定位。
    """
    if value is None:
        if not column.nullable:
            raise ValidationError("非空目标字段缺少值", column=column.name)
        return None
    kind = column.type
    try:
        if isinstance(kind, types.JSON):
            json.dumps(value, allow_nan=False)
            return value
        if isinstance(kind, types.Boolean):
            if not isinstance(value, bool):
                raise ValueError()
            return value
        if isinstance(kind, (types.Integer, types.Numeric, types.Float)):
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError()
            number = Decimal(str(value))
            if not number.is_finite() or len(number.as_tuple().digits) > 100 or abs(number.adjusted()) > 100:
                raise ValueError()
            if isinstance(kind, types.Integer):
                bits = 64 if provider == "sqlite" or isinstance(kind, types.BigInteger) else 16 if isinstance(kind, types.SmallInteger) else 32
                bits = {"TINYINT": 8, "MEDIUMINT": 24}.get(type(kind).__name__, bits)
                lower, upper = (0, 2**bits) if getattr(kind, "unsigned", False) else (-(2**(bits - 1)), 2**(bits - 1))
                if number != number.to_integral_value() or not lower <= number < upper:
                    raise ValueError()
                return int(number)
            if isinstance(kind, types.Float):
                result = float(number)
                if not math.isfinite(result):
                    raise ValueError()
                return result
            if provider == "sqlite":
                if number != number.to_integral_value() or abs(number) > 2**53:
                    raise ValidationError("SQLite NUMERIC 无法保证此精确值，请选择 TEXT 字段或缩放后的整数列")
                return int(number)
            scale, precision = getattr(kind, "scale", None), getattr(kind, "precision", None)
            digits, exponent = list(number.as_tuple().digits), number.as_tuple().exponent
            while len(digits) > 1 and digits[-1] == 0:
                digits.pop()
                exponent += 1
            if scale is not None and exponent < -scale:
                raise ValidationError("精确数值超过目标小数位，未自动舍入", column=column.name)
            if precision and max(number.adjusted() + 1, 0) > precision - (scale or 0):
                raise ValueError()
            return number
        if isinstance(kind, types.DateTime):
            parsed = datetime.fromisoformat(value)
            if bool(parsed.tzinfo) != bool(kind.timezone):
                raise ValueError()
            return parsed
        if isinstance(kind, types.Date):
            return date.fromisoformat(value)
        if isinstance(kind, types.Time):
            parsed = time.fromisoformat(value)
            if bool(parsed.tzinfo) != bool(kind.timezone):
                raise ValueError()
            return parsed
        if isinstance(kind, types.String):
            if isinstance(value, (dict, list, bool)):
                raise ValueError()
            result = str(value)
            if "\0" in result or (kind.length and len(result) > kind.length):
                raise ValueError()
            if isinstance(kind, types.Enum) and result not in kind.enums:
                raise ValueError()
            return result
    except (ValueError, TypeError, InvalidOperation, OverflowError) as cause:
        raise ValidationError("写入值与目标字段类型、长度或精度不匹配", column=column.name) from cause
    raise ValidationError("目标字段类型暂不支持受控写入", column=column.name)


def mapped_rows(config: OutputTableConfig, value: Any, table: Table, provider: str, budget: RuleBudget) -> list[dict[str, Any]]:
    """Validate the whole bounded batch before the first external mutation.

    中文补充：把节点配置与上游数据整批映射成可写入的行（在**第一次外部写之前**
    完成全部校验，因此失败时目标库一定没被改动）。
    步骤：建立受保护的取值副本 → 取记录数组（≤1000 行、必须为对象数组）→
    逐行按字段映射并转换 → update 模式额外校验定位键非空、非结构化、批次内不重复。
    批量内重复定位键会拒绝，因为按批次顺序覆盖是隐式行为，容易造成"最后一行赢"的误解。
    返回：行字典列表；异常：任一校验失败抛 ValidationError。
    """
    with evaluation_budget(budget.deadline):
        protected = budget_tree(value)
        records = single(config.record_path, protected)
        if not isinstance(records, list) or len(records) > 1000 or any(not isinstance(row, dict) for row in records):
            raise ValidationError("写入记录必须为对象数组，整批不超过 1000 行；不会静默截断")
        if isinstance(value, dict) and (value.get("truncated") or value.get("success") is False or value.get("status") in {"partial", "failed"}):
            raise ValidationError("上游记录不完整，不能自动写入目标表")
        rows, keys = [], set()
        for record in records:
            budget.consume()
            row = {}
            for field in config.fields:
                raw = field.value if field.origin == "literal" else single(field.path, protected if field.origin == "input" else record)
                row[field.column] = convert(raw, table.c[field.column], provider)
            if config.mode == "update":
                key = tuple(row[field] for field in config.keys)
                if any(item is None or isinstance(item, (list, dict)) for item in key):
                    raise ValidationError("更新定位键不能为 null 或结构化值")
                if key in keys:
                    raise ValidationError("同一批次包含重复定位键，不能按隐含顺序覆盖")
                keys.add(key)
            rows.append(row)
        return rows
