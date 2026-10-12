# -*- coding: utf-8 -*-
"""工具参数的 JSON Schema 受控子集与业务规则校验。

- 网关在调用任何 provider 之前都会执行 ``validate_arguments``，因此这里既是
  模型输出与工具契约的边界，也是"业务规则校验（x-rule）"的挂载点；
  provider 内不应重复实现类型/枚举校验。
- 保存与运行共享结构检查；组合、枚举、数值及容器约束均执行，
  不支持的关键字和远程引用直接拒绝，不发起网络请求。
- 兼容性：模型偶尔会把对象/数组序列化成字符串再传入，``_coerce_json_containers``
  只在 schema 明确要求容器时解一层，声明为 string 的字段不会被误解码。
- 失败一律抛 SchemaValidationError（中文提示 + 字段路径），由网关决定是
  抛给调用方还是回给模型。
"""
from __future__ import annotations

import json
import math
import re
from fractions import Fraction
from functools import lru_cache
from typing import Any, Callable, Dict
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ...errors import SchemaValidationError


class RuleValidator:
    """Custom business validators receive (value, schema, context) and raise or return.

    中文补充：业务规则校验器注册表。校验器约定"不通过就抛 SchemaValidationError"，
    返回 None 表示通过；``context`` 是工具执行上下文，可用于判断调用者身份或工作区，
    但校验器**不得**产生副作用（写库/发请求），因为它会被重复调用（anyOf/oneOf 探测）。
    """

    def __init__(self):
        """初始化空规则表；注册在模块加载时通过模块级 rule_validators 完成。"""
        self._registry: Dict[str, Callable] = {}

    def register(self, name: str, fn: Callable) -> None:
        """注册/覆盖一条规则（同名覆盖，便于测试替换）。名称对应 schema 的 x-rule 值。"""
        self._registry[name] = fn

    def run(self, name: str, value: Any, schema: dict, context: dict):
        """执行命名规则；未注册时拒绝，避免声明的业务约束被静默跳过。"""
        fn = self._registry.get(name)
        if fn is None:
            raise SchemaValidationError("工具声明了未登记的业务规则")
        fn(value, schema, context)


rule_validators = RuleValidator()

SCHEMA_KEYS = {
    "$id", "$schema", "type", "title", "description", "default", "examples", "enum", "const",
    "properties", "required", "additionalProperties", "items", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern",
    "format", "minItems", "maxItems", "uniqueItems", "anyOf", "oneOf", "allOf", "not",
    "x-rule", "x-threshold", "x-lookup", "x-must-resolve",
}


def validate_schema(schema: dict, rules: RuleValidator | None = None) -> dict:
    """保存和执行复用同一子集契约；元结构校验只用内置规则，绝不解析远程引用。"""
    try:
        serialized = json.dumps(schema, ensure_ascii=False, allow_nan=False, sort_keys=True)
        serialized_bytes = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaValidationError("工具 Schema 必须是有效 JSON") from exc
    if serialized_bytes > 128 * 1024:
        raise SchemaValidationError("单个工具 Schema 不能超过 128 KB")
    known_rules = tuple(sorted(rules._registry)) if rules is not None else ()
    _validate_schema_document(serialized, known_rules)
    return schema


@lru_cache(maxsize=256)
def _validate_schema_document(serialized: str, known_rules: tuple[str, ...]) -> None:
    """只缓存结构校验成功；键包含完整内容和规则目录，原地改动不会沿用旧结论。"""
    schema = json.loads(serialized)
    pending = [(schema, 0)]
    seen = 0
    while pending:
        node, depth = pending.pop()
        seen += 1
        if seen > 5000 or depth > 24:
            raise SchemaValidationError("工具 Schema 节点过多或嵌套过深")
        if not isinstance(node, dict):
            raise SchemaValidationError("工具 Schema 必须是对象")
        if set(node) - SCHEMA_KEYS:
            raise SchemaValidationError("工具 Schema 包含当前执行器不支持的关键字")
        properties = node.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 300:
            raise SchemaValidationError("工具 Schema 的 properties 必须是对象且最多 300 项")
        for name, child in properties.items():
            if not isinstance(name, str) or not name or name in {"__proto__", "prototype", "constructor"}:
                raise SchemaValidationError("工具 Schema 含不安全字段名")
            pending.append((child, depth + 1))
        for keyword in ("items", "not"):
            if keyword in node:
                pending.append((node[keyword], depth + 1))
        additional = node.get("additionalProperties", True)
        if isinstance(additional, dict):
            pending.append((additional, depth + 1))
        elif not isinstance(additional, bool):
            raise SchemaValidationError("additionalProperties 必须是布尔值或 Schema 对象")
        for keyword in ("anyOf", "oneOf", "allOf"):
            if keyword in node:
                branches = node[keyword]
                if not isinstance(branches, list) or not 1 <= len(branches) <= 20:
                    raise SchemaValidationError(f"{keyword} 必须包含 1–20 个分支")
                pending.extend((child, depth + 1) for child in branches)
        pattern = node.get("pattern")
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            raise SchemaValidationError("工具 Schema 的 enum 必须是非空数组")
        if pattern is not None:
            if not isinstance(pattern, str) or len(pattern) > 500:
                raise SchemaValidationError("工具 Schema 的 pattern 格式不正确")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise SchemaValidationError("工具 Schema 的 pattern 不是有效正则表达式") from exc
        if "format" in node and (not isinstance(node["format"], str)
                or node["format"] not in {"date", "date-time", "email", "uri"}):
            raise SchemaValidationError("工具 Schema 声明了未支持的格式")
        if "x-rule" in node:
            name = node["x-rule"]
            if not isinstance(name, str) or name not in known_rules:
                raise SchemaValidationError("工具声明了未登记的业务规则")
        if "x-lookup" in node and (not isinstance(node["x-lookup"], str) or not node["x-lookup"].strip()):
            raise SchemaValidationError("引用查找名称必须是非空字符串")
        if "x-must-resolve" in node and not isinstance(node["x-must-resolve"], bool):
            raise SchemaValidationError("强制引用解析标记必须是布尔值")
        if node.get("x-must-resolve") is True and not node.get("x-lookup"):
            raise SchemaValidationError("强制引用解析必须声明引用查找名称")
        if "x-threshold" in node and (isinstance(node["x-threshold"], bool)
                or not isinstance(node["x-threshold"], (int, float))
                or isinstance(node["x-threshold"], float) and not math.isfinite(node["x-threshold"])):
            raise SchemaValidationError("业务规则阈值必须是有限数值")
    try:
        # 拒绝 NaN/Infinity；Draft 元模型只检查本地结构，不访问声明的 URL。
        json.dumps(schema, allow_nan=False)
        Draft202012Validator.check_schema(schema)
    except (SchemaError, TypeError, ValueError, RecursionError) as exc:
        raise SchemaValidationError("工具 Schema 的类型或约束声明无效") from exc


def _json_equal(left: Any, right: Any) -> bool:
    """按 JSON 语义比较：布尔值不等于数字，容器递归比较且忽略对象键顺序。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _json_identity(value: Any):
    """构造有类型的等价键，让大数组去重为线性扫描而非逐项两两比较。"""
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, dict):
        return ("object", frozenset((key, _json_identity(item)) for key, item in value.items()))
    if isinstance(value, list):
        return ("array", tuple(_json_identity(item) for item in value))
    return ("scalar", value)


def _fmt_errors(path: str, msg: str) -> str:
    """统一错误前缀：``$arguments.字段[下标]`` 形式，便于模型定位到具体参数。"""
    return f"{path}: {msg}"


def _schema_types(schema: dict) -> set[str]:
    """把 schema 的 type 归一化为集合（支持字符串与列表两种写法，缺失返回空集）。"""
    expected = schema.get("type")
    if isinstance(expected, str):
        return {expected}
    if isinstance(expected, list):
        return {str(item) for item in expected}
    return set()


def _coerce_json_containers(value: Any, schema: dict) -> Any:
    """仅当契约要求容器时，解开模型多序列化的一层 JSON。

    对"被模型多序列化了一层"的容器做一次解包，并把规范十进制字符串还原成
    整数（仅 integer 字段）。字段声明为 string 时绝不解读，非法 JSON 仍然报错，
    因此这里是"修复可安全修复的写法"，不是宽松解析。
    """
    expected = _schema_types(schema)
    # integer 字段收到纯数字字符串时按整数还原：模型经常把 ID/数量写成 "12"。
    # 只接受规范十进制写法且长度受限，防止把超大数字串或前导零当成合法输入。
    if expected == {"integer"} and isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,14})", value):
        value = int(value)
    container_types = expected.intersection({"object", "array"})
    if isinstance(value, str) and container_types and "string" not in expected:
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            decoded = value
        if (("object" in container_types and isinstance(decoded, dict))
                or ("array" in container_types and isinstance(decoded, list))):
            value = decoded

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        return {
            key: _coerce_json_containers(item, properties[key]) if key in properties else item
            for key, item in value.items()
        }
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_coerce_json_containers(item, schema["items"]) for item in value]
    return value


def validate_value(value: Any, schema: dict, path: str = "$", context: dict = None,
                   rule_validators: RuleValidator = None) -> None:
    """先检查完整契约和 JSON 值，再逐项执行；组合分支不能跳过同级约束。"""
    validate_schema(schema, rule_validators)
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaValidationError(_fmt_errors(path, "参数必须是有效 JSON，数值必须有限")) from exc
    _validate_value(value, schema, path, context, rule_validators)


def _validate_value(value: Any, schema: dict, path: str, context: dict | None,
                    rule_validators: RuleValidator | None) -> None:
    """递归执行已检查过结构的契约；业务规则需无副作用以支持分支试探。"""
    if "const" in schema:
        if not _json_equal(value, schema["const"]):
            raise SchemaValidationError(_fmt_errors(path, "不符合固定值约束"))
    if "enum" in schema and not any(_json_equal(value, option) for option in schema["enum"]):
        raise SchemaValidationError(_fmt_errors(path, "值不在允许的枚举内"))
    for sub in schema.get("allOf", []):
        _validate_value(value, sub, path, context, rule_validators)
    if "not" in schema:
        try:
            _validate_value(value, schema["not"], path, context, rule_validators)
        except SchemaValidationError:
            pass
        else:
            raise SchemaValidationError(_fmt_errors(path, "命中了禁止的参数结构"))
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            try:
                _validate_value(value, sub, path, context, rule_validators)
                break
            except SchemaValidationError:
                continue
        else:
            raise SchemaValidationError(_fmt_errors(path, "不满足 anyOf 约束"))
    if "oneOf" in schema:
        matches = 0
        for sub in schema["oneOf"]:
            try:
                _validate_value(value, sub, path, context, rule_validators)
                matches += 1
            except SchemaValidationError:
                pass
        if matches != 1:
            raise SchemaValidationError(_fmt_errors(path, "必须恰好满足 oneOf 中的一个子结构"))

    expected = schema.get("type")
    value_type = "null" if value is None else ("array" if isinstance(value, list) else
                                               ("object" if isinstance(value, dict) else
                                                ("integer" if isinstance(value, int) and not isinstance(value, bool) else
                                                 ("number" if isinstance(value, (int, float)) and not isinstance(value, bool) else
                                                  ("boolean" if isinstance(value, bool) else
                                                   "string")))))

    def type_ok(expected_type: str) -> bool:
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "null":
            return value is None
        return True

    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(type_ok(t) for t in types):
            raise SchemaValidationError(
                _fmt_errors(path, f"类型错误: 期望 {expected}, 实际 {value_type}"))

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern:
            if not re.search(pattern, value):
                raise SchemaValidationError(_fmt_errors(path, f"不匹配 pattern {pattern}"))
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if min_len is not None and len(value) < min_len:
            raise SchemaValidationError(_fmt_errors(path, f"长度过短: {len(value)} < {min_len}"))
        if max_len is not None and len(value) > max_len:
            raise SchemaValidationError(_fmt_errors(path, f"长度过长: {len(value)} > {max_len}"))
        fmt = schema.get("format")
        if fmt == "date":
            from datetime import date
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("date")
                date.fromisoformat(value)
            except ValueError as exc:
                raise SchemaValidationError(_fmt_errors(path, "日期必须是有效的 YYYY-MM-DD")) from exc
        elif fmt == "date-time":
            try:
                from datetime import datetime
                # 兼容既有无时区本地时间，但不能把只有日期的字符串当作日期时间。
                if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
                    raise ValueError("date-time")
                datetime.fromisoformat(value)
            except (ValueError, TypeError):
                raise SchemaValidationError(_fmt_errors(path, "时间格式不合法"))
        elif fmt == "email":
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                raise SchemaValidationError(_fmt_errors(path, "邮箱格式不合法"))
        elif fmt == "uri":
            try:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or re.search(r"\s|[\x00-\x1f\x7f]", value):
                    raise ValueError("uri")
                parsed.port
            except ValueError as exc:
                raise SchemaValidationError(_fmt_errors(path, "URL 格式不合法")) from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        mn, mx = schema.get("minimum"), schema.get("maximum")
        if mn is not None and value < mn:
            raise SchemaValidationError(_fmt_errors(path, f"小于最小值 {mn}"))
        if mx is not None and value > mx:
            raise SchemaValidationError(_fmt_errors(path, f"大于最大值 {mx}"))
        lower, upper = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if lower is not None and value <= lower:
            raise SchemaValidationError(_fmt_errors(path, f"必须大于 {lower}"))
        if upper is not None and value >= upper:
            raise SchemaValidationError(_fmt_errors(path, f"必须小于 {upper}"))
        divisor = schema.get("multipleOf")
        if divisor is not None and (Fraction(str(value)) / Fraction(str(divisor))).denominator != 1:
            raise SchemaValidationError(_fmt_errors(path, f"必须为 {divisor} 的整数倍"))

    if isinstance(value, list):
        min_i, max_i = schema.get("minItems"), schema.get("maxItems")
        if min_i is not None and len(value) < min_i:
            raise SchemaValidationError(_fmt_errors(path, f"数组项过少: {len(value)} < {min_i}"))
        if max_i is not None and len(value) > max_i:
            raise SchemaValidationError(_fmt_errors(path, f"数组项过多: {len(value)} > {max_i}"))
        if schema.get("uniqueItems"):
            unique = set()
            for item in value:
                identity = _json_identity(item)
                if identity in unique:
                    raise SchemaValidationError(_fmt_errors(path, "数组不允许重复项"))
                unique.add(identity)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                _validate_value(item, item_schema, f"{path}[{i}]", context, rule_validators)

    if isinstance(value, dict):
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise SchemaValidationError(_fmt_errors(path, f"缺少必填字段 {name}"))
        for name in value:
            if name in props:
                child = props[name]
                _validate_value(value[name], child, f"{path}.{name}", context, rule_validators)
            else:
                if schema.get("additionalProperties") is False:
                    raise SchemaValidationError(_fmt_errors(path, f"不允许的字段 {name}"))
                additional = schema.get("additionalProperties")
                if isinstance(additional, dict):
                    _validate_value(value[name], additional, f"{path}.{name}", context, rule_validators)

    # 当前节点只执行一次业务规则；父节点不重复执行子字段的规则。
    if rule_validators:
        rule = schema.get("x-rule")
        if rule:
            rule_validators.run(rule, value, schema, context or {})


# ------------------------------------------------------------------
# Built-in business rule validators (schema keyword x-rule)
# ------------------------------------------------------------------
def _rule_positive_number(value, schema, context):
    """x-rule=positive_number：数值必须为正（bool 视为非数值，避免 True 通过）。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
        raise SchemaValidationError(f"必须为正数，实际值 {value}")


def _rule_not_past_date(value, schema, context):
    """x-rule=not_past_date：日期不得早于今天（按 UTC 比较）。

    非字符串或无法解析的值在此不报错：格式合法性由 format 关键字负责，
    这里只做业务语义（不能是过去）判断，避免两条规则给出互相矛盾的提示。
    """
    if isinstance(value, str):
        try:
            from datetime import datetime
            d = datetime.fromisoformat(value)
            if d.date() < datetime.utcnow().date():
                raise SchemaValidationError(f"日期不能早于今天: {value}")
        except ValueError:
            pass


def _rule_not_blank(value, schema, context):
    """x-rule=not_blank：字符串不能只有空白字符（minLength 拦不住全空格）。"""
    if isinstance(value, str) and not value.strip():
        raise SchemaValidationError("字段不能为空白")


def _rule_min_amount(value, schema, context):
    """x-rule=min_amount：金额不得低于 schema 的 x-threshold（阈值随 schema 配置）。"""
    threshold = schema.get("x-threshold")
    if threshold is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and value < threshold:
        raise SchemaValidationError(f"金额不得低于 {threshold}，实际 {value}")


rule_validators.register("positive_number", _rule_positive_number)
rule_validators.register("not_past_date", _rule_not_past_date)
rule_validators.register("not_blank", _rule_not_blank)
rule_validators.register("min_amount", _rule_min_amount)


def validate_arguments(arguments: Any, input_schema: dict, context: dict = None) -> Any:
    """按 ToolSpec 契约校验并规范化模型参数，失败抛出 SchemaValidationError。

    这是网关的公开校验入口。``arguments`` 允许是 dict，或模型给出的
    JSON 字符串（会先解析）；其他类型直接拒绝。``input_schema`` 为空时只保证参数
    是 JSON 对象而不做字段校验（内置无参工具属于这种情况）。
    返回：校验并归一化后的参数 dict（对象/数组的字符串化写法已被还原），
    调用方应使用返回值而不是原始入参。
    """
    if not isinstance(arguments, dict):
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, RecursionError) as exc:
                raise SchemaValidationError("参数不是合法 JSON，或数字与嵌套超过解析范围") from exc
        else:
            raise SchemaValidationError("参数必须是 JSON 对象")
    if not isinstance(arguments, dict):
        raise SchemaValidationError("参数必须是 JSON 对象")
    if input_schema:
        validate_schema(input_schema, rule_validators)
        arguments = _coerce_json_containers(arguments, input_schema)
        validate_value(arguments, input_schema, "$arguments", context, rule_validators)
    else:
        validate_value(arguments, {}, "$arguments", context, rule_validators)
    return arguments
