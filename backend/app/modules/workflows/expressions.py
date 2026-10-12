"""Deterministic typed rules; no eval, Python calls, attribute access or I/O.

中文补充：可视化规则节点的表达式引擎。设计底线是"**完全可判定、无副作用**"：
表达式的结构只能是 ``{"op", "args"}``、``{"field"}``、``{"value"}`` 三种形态，
没有任何求值、属性访问、函数调用或 I/O 能力，因此规则可以在保存时就穷举校验，
执行时也只需按固定语义求值。

关键约定：
- ``MISSING`` 是一个哨兵对象，用来区分"字段不存在"与"字段值为 None"。
  两者在大多数运算里都会失败，但 ``coalesce``/``missing``/``present`` 需要区分它们，
  这是本模块最容易出错的地方。
- ``RuleBudget`` 由整条流程共享：每行、每个运算都消耗预算，防止复杂规则在大批量数据上失控。
- 比较与集合运算使用 ``canonical`` 的"类型化身份"，字符串 "1"、数字 1、布尔 True 互不相等；
  数值走 Decimal 精确运算，避免浮点误差影响判定。
- ``formula_expression`` 只把算术公式（字段名、数值、加减乘除、括号、正负号）翻译成同一套
  表达式树，用 AST 白名单实现，不暴露 Python 语义。
- 无值即失败：除显式处理缺失的运算外，任何缺失字段都会抛 ValidationError，
  绝不把"没读到数据"当成 0、False 或合格。
"""
from __future__ import annotations

import ast
import json
import time
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from typing import Any

from app.errors import ValidationError
from app.modules.collection.extraction import json_value
from app.modules.collection.transport import validate_response_tree

MISSING = object()
# 运算符 -> 允许的参数个数区间（含边界）。校验与执行都以此为唯一来源，
# 新增运算符必须同时补 evaluate 的分支。
OPERATORS = {
    "add": (2, 2), "sub": (2, 2), "mul": (2, 2), "div": (2, 2),
    "eq": (2, 2), "ne": (2, 2), "gt": (2, 2), "gte": (2, 2), "lt": (2, 2), "lte": (2, 2),
    "between": (3, 3), "outside": (3, 3), "and": (1, 32), "or": (1, 32), "not": (1, 1),
    "union": (2, 2), "intersection": (2, 2), "difference": (2, 2), "in": (2, 2), "not_in": (2, 2),
    "missing": (1, 1), "present": (1, 1), "coalesce": (2, 16), "length": (1, 1),
}


@dataclass
class RuleBudget:
    """A workflow shares a finite operation/time budget across every input row.

    中文补充：整条流程共用一个实例（由 ``execute_graph`` 按 ``timeout_seconds`` 创建），
    因此"10000 行 × 多条件规则"这类组合不会绕过时间限制。剩余额度用完或超时立即失败，
    不允许降级成"部分判定"。
    """

    deadline: float
    remaining: int = 5_000_000

    def consume(self, count: int = 1) -> None:
        """扣减额度并检查截止时间；超限抛 ValidationError（提示减少数据或简化规则）。

        热路径上会被极频繁调用，因此这里只做两次比较，不做日志或统计。
        """
        self.remaining -= count
        if self.remaining < 0 or time.monotonic() >= self.deadline:
            raise ValidationError("规则计算超出时间或操作预算，请减少数据或简化规则")


def validate_expression(expression: Any, *, depth: int = 0, count: list[int] | None = None) -> None:
    """Validate every branch before execution, including short-circuited branches.

    中文补充：递归校验整棵表达式树——所有分支（含 and/or 短路后不会走到的分支）都必须合法，
    这样"某个条件永远不会被求值"不会成为绕过校验的理由。限制：最多 24 层、512 个节点，
    常量单个不超过 64 KB（防 DoS 与超长配置）；字段名不得为空、超 120 字符或是原型污染名。
    ``count`` 用可变列表在递归中累计节点数，属于内部实现细节，调用方无需传。
    异常：ValidationError。
    """
    count = [0] if count is None else count
    count[0] += 1
    if depth > 24 or count[0] > 512 or not isinstance(expression, dict):
        raise ValidationError("规则必须为有限表达式对象，最多 24 层、512 项")
    if set(expression) == {"value"}:
        validate_response_tree(expression["value"])
        if len(json.dumps(expression["value"], ensure_ascii=False, allow_nan=False).encode()) > 65536:
            raise ValidationError("单个规则常量超过 64 KB")
        return
    if set(expression) == {"field"}:
        field = expression["field"]
        if not isinstance(field, str) or not field.strip() or len(field) > 120 or field in {"__proto__", "constructor", "prototype"}:
            raise ValidationError("字段引用必须是有效的完整字段名")
        return
    if set(expression) != {"op", "args"} or not isinstance(expression.get("op"), str) or expression["op"] not in OPERATORS:
        raise ValidationError("包含不支持的规则运算或多余配置")
    limits = OPERATORS[expression["op"]]
    args = expression["args"]
    if not isinstance(args, list) or not limits[0] <= len(args) <= limits[1]:
        raise ValidationError("规则运算的参数数量不正确", operator=expression["op"])
    for arg in args:
        validate_expression(arg, depth=depth + 1, count=count)


def number(value: Any) -> Decimal:
    """Convert explicitly numeric operations without treating null/bool as zero.

    中文补充：缺失值、None、布尔、字典、列表一律拒绝——布尔是 int 的子类，
    若不显式排除，``True`` 会被当成 1 参与阈值比较。同时限制精度与数量级
    （有效位 1000、指数 1000、adjusted 1000），避免超长数字拖慢或溢出。
    异常：ValidationError。
    """
    if value is MISSING or value is None or isinstance(value, (bool, dict, list)):
        raise ValidationError("数值运算收到缺失、空值、布尔或复合值，不能作为 0 处理")
    try:
        result = Decimal(str(value).strip())
    except DecimalException as exc:
        raise ValidationError("字段不是有效数值，请检查字段类型和运算口径") from exc
    if not result.is_finite() or abs(result.adjusted()) > 1000 or len(result.as_tuple().digits) > 1000 or abs(result.as_tuple().exponent) > 1000:
        raise ValidationError("数值超出支持的精度或数量级")
    return result


def boolean(value: Any) -> bool:
    """Logical rules accept booleans, never truthy strings such as 'false'.

    中文补充：用 ``type(value) is not bool`` 而不是 ``not value``，因为字符串 "false"、
    数字 0、空列表都是"真值/假值"语义模糊的输入；逻辑运算只接受明确的布尔判断结果，
    否则报错要求用户显式改用比较运算。
    """
    if type(value) is not bool:
        raise ValidationError("AND/OR/NOT 的输入必须是明确的布尔判断结果")
    return value


def canonical(value: Any) -> str:
    """Stable typed set identity; a string ID, boolean and number stay distinct.

    中文补充：为集合运算与等值比较提供可哈希的"类型化身份"。
    数值统一走 Decimal 并用 2200 位精度归一化，否则 ``normalize()`` 会使用进程默认的
    28 位 decimal 上下文，把高精度的设备标识号/金额判成相等（这会直接导致漏判）。
    返回字符串前缀是类型名，因此 ``1``（number:1）、``"1"``（str:"1"）、
    ``True``（bool:true）互不相等。
    异常：ValidationError（MISSING）。
    """
    if value is MISSING:
        raise ValidationError("集合或比较运算包含缺失字段")
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        numeric = number(value)
        # normalize() otherwise uses the process' 28-digit decimal context and
        # collapses distinct high-precision device identifiers and amounts.
        with localcontext() as context:
            context.prec = 2200
            return "number:" + ("0" if numeric == 0 else str(numeric.normalize()))
    return type(value).__name__ + ":" + json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def set_operation(left: Any, right: Any, operator: str, budget: RuleBudget, *, keys: list[str] | None = None) -> list:
    """Stable union/intersection/difference, retaining left rows for matching keys.

    中文补充：交并差按"键"匹配时保留**左侧**记录（右侧只用于判断是否存在），
    因此输出行的字段结构始终与左侧一致；``keys`` 为空时比较整条记录的类型化身份。
    重复键取首次出现者（``setdefault``），保证结果顺序稳定可复现。
    限制：两侧合计最多 20000 项；``keys`` 指定的字段必须全部存在。
    返回列表；异常：ValidationError。副作用：消耗预算。
    """
    if not isinstance(left, list) or not isinstance(right, list):
        raise ValidationError("交并差运算需要两组数组或记录")
    if len(left) + len(right) > 20000:
        raise ValidationError("集合输入合计不能超过 20000 项")
    def key(item):
        """把一条记录折算成集合匹配键：给定了 keys 就只比较这些字段，否则比较整条记录。

        记录缺少任一匹配字段直接报错（不能把"字段缺失"当成"不匹配"）。
        """
        budget.consume()
        if keys:
            if not isinstance(item, dict) or any(name not in item for name in keys):
                raise ValidationError("集合记录缺少选择的匹配字段")
            return json.dumps([canonical(item[name]) for name in keys], ensure_ascii=False)
        return canonical(item)
    left_map, right_map = {}, {}
    for item in left:
        left_map.setdefault(key(item), item)
    for item in right:
        right_map.setdefault(key(item), item)
    if operator == "union":
        return list(left_map.values()) + [value for name, value in right_map.items() if name not in left_map]
    if operator == "intersection":
        return [value for name, value in left_map.items() if name in right_map]
    if operator == "difference":
        return [value for name, value in left_map.items() if name not in right_map]
    raise ValidationError("集合运算必须为 union/intersection/difference")


def calculate(operator: str, left: Any, right: Any) -> Decimal:
    """Compute exact finite addition/multiplication; division uses 80-digit precision.

    中文补充：加减乘用 2200 位精度精确计算（十进制下不会丢精度），除法是唯一可能无限循环的
    运算，因此单独限制为 80 位有效数字；除数为 0 明确报错而不是返回 0/None。
    异常：ValidationError（非数值、除零、超出 Decimal 范围）。
    """
    a, b = number(left), number(right)
    if operator == "div" and b == 0:
        raise ValidationError("除数为 0，不能生成有效计算结果")
    with localcontext() as context:
        context.prec = 2200 if operator != "div" else 80
        try:
            result = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b, "div": lambda: a / b}[operator]()
        except DecimalException as exc:
            raise ValidationError("数值计算超出范围") from exc
    return number(result)


def evaluate(expression: dict, row: dict, budget: RuleBudget) -> Any:
    """Evaluate a validated expression against one record with explicit missing values.

    中文补充：调用前必须先通过 ``validate_expression``（本函数假设结构合法，
    对未知运算符只报"未知运算"）。求值语义要点：

    - ``and``/``or`` 短路求值，但每一项都必须是明确布尔值（见 ``boolean``）；
    - ``coalesce`` 逐个求值，返回第一个既非 MISSING 也非 None 的值，全无则返回 None；
    - 除 ``missing``/``present`` 外，任一参数为 MISSING 立即报错——**缺失字段不会退化为 None**，
      这样"数据没读到"与"字段值为空"在业务上能被区分；
    - ``in``/``not_in`` 的第二项必须是数组，逐个比较类型化身份并消耗预算；
    - 比较运算要求两侧都是数值（``eq``/``ne`` 例外，用类型化身份比较）。

    返回：Python 原生值或 MISSING；异常：ValidationError。副作用：消耗预算。
    """
    budget.consume()
    if "value" in expression:
        return expression["value"]
    if "field" in expression:
        return row.get(expression["field"], MISSING)
    op, args = expression["op"], expression["args"]
    if op == "and":
        return all(boolean(evaluate(arg, row, budget)) for arg in args)
    if op == "or":
        return any(boolean(evaluate(arg, row, budget)) for arg in args)
    if op == "coalesce":
        for arg in args:
            value = evaluate(arg, row, budget)
            if value is not MISSING and value is not None:
                return value
        return None
    values = [evaluate(arg, row, budget) for arg in args]
    if op in {"missing", "present"}:
        missing = values[0] is MISSING or values[0] is None
        return missing if op == "missing" else not missing
    if any(value is MISSING for value in values):
        raise ValidationError("规则引用的字段不存在；请检查字段映射或显式处理缺失值")
    if op == "not":
        return not boolean(values[0])
    if op == "length":
        if not isinstance(values[0], (list, dict, str)):
            raise ValidationError("长度运算需要文本、数组或对象")
        return len(values[0])
    if op in {"add", "sub", "mul", "div"}:
        return calculate(op, *values)
    if op in {"eq", "ne"}:
        same = canonical(values[0]) == canonical(values[1])
        return same if op == "eq" else not same
    if op in {"gt", "gte", "lt", "lte"}:
        a, b = map(number, values)
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op in {"between", "outside"}:
        value, lower, upper = map(number, values)
        if lower > upper:
            raise ValidationError("范围下限不能大于上限")
        inside = lower <= value <= upper
        return inside if op == "between" else not inside
    if op in {"union", "intersection", "difference"}:
        return set_operation(values[0], values[1], op, budget)
    if op in {"in", "not_in"}:
        if not isinstance(values[1], list):
            raise ValidationError("属于/不属于运算的第二项必须是集合数组")
        target = canonical(values[0])
        matched = False
        for value in values[1]:
            budget.consume()
            if canonical(value) == target:
                matched = True
                break
        return matched if op == "in" else not matched
    raise ValidationError("未知运算")


def formula_expression(formula: str) -> dict:
    """Translate arithmetic-only formulas to the same safe expression tree.

    中文补充：公式模式（算术节点的"公式"字段）的翻译器，用 AST 白名单实现：
    只允许字段名、数值常量、加减乘除、括号与一元正负号；出现函数调用、属性访问、下标、
    比较或任何其他节点即报错（文案明确列出允许的范围）。一元负号翻译成 ``sub(0, x)``，
    保持与表达式树一致的语义。数值常量用源码片段转成字符串再走 ``number``，
    避免浮点字面量丢精度。翻译结果最后再过一遍 ``validate_expression``。
    限制：公式最长 2000 字符、嵌套最深 24 层。异常：ValidationError。
    """
    if not isinstance(formula, str) or not formula.strip() or len(formula) > 2000:
        raise ValidationError("计算公式不能为空，最长 2000 字符")
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValidationError("计算公式格式无效") from exc
    operators = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div"}
    def translate(node, depth=0):
        """把单个 AST 节点翻译成表达式树；白名单之外的任何语法都报错。

        数值常量取源码片段（``ast.get_source_segment``）而不是 ``node.value``，
        保证 "0.1" 这类字面量按原文本走 Decimal，不经过二进制浮点。
        """
        if depth > 24:
            raise ValidationError("计算公式嵌套过深")
        if isinstance(node, ast.Name):
            return {"field": node.id}
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return {"value": str(number(ast.get_source_segment(formula.strip(), node)))}
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            return {"op": operators[type(node.op)], "args": [translate(node.left, depth + 1), translate(node.right, depth + 1)]}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return {"op": "sub" if isinstance(node.op, ast.USub) else "add", "args": [{"value": 0}, translate(node.operand, depth + 1)]}
        raise ValidationError("公式仅允许字段名、数值、加减乘除和括号；不允许函数、属性、索引或代码执行")
    result = translate(tree.body)
    validate_expression(result)
    return result


def evaluate_result(expression: dict, row: dict, budget: RuleBudget) -> Any:
    """Return JSON-safe exact decimals while refusing an unresolved missing field.

    中文补充：计算结果写入记录前必须经过这里：把 MISSING 视为错误（不能把"字段不存在"
    变成 null 写进结果），并把 Decimal 转成 JSON 可序列化的值。
    异常：ValidationError。
    """
    value = evaluate(expression, row, budget)
    if value is MISSING:
        raise ValidationError("计算结果引用了缺失字段")
    return json_value(value)
