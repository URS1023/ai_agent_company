"""字典、参数、公告和表单分别校验；JSONSchema与预览共享同一声明式字段。"""
from datetime import date, datetime, timezone
import json
from math import isfinite
import re

from ...core.errors import DomainError

FIELD_TYPES = {"text", "textarea", "number", "integer", "select", "boolean", "date"}


def invalid(message, field="definition"):
    raise DomainError(422, "support_definition_invalid", message, {field: message})


def text(value, label, maximum=200, empty=False):
    if not isinstance(value, str) or (not value.strip() and not empty) or len(value) > maximum:
        invalid(f"请填写有效的{label}")
    return value.strip()


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except OverflowError:
        return False


def timestamp(value):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, AttributeError, TypeError):
        invalid("请选择包含时区的发布时间与失效时间")


def choices(items, *, dictionary=False):
    if not isinstance(items, list) or len(items) > 200:
        invalid("选项须为不超过200项的列表")
    result, seen, defaults = [], set(), 0
    allowed = {"label", "value", "enabled", "order", "is_default", "tag_type"} if dictionary else {"label", "value"}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - allowed:
            invalid("选项字段无效")
        label, value = text(item.get("label"), "选项名称"), text(item.get("value"), "选项值")
        if value in seen:
            invalid("选项值不能重复")
        seen.add(value)
        output = {"label": label, "value": value}
        if dictionary:
            enabled, default, order = item.get("enabled", True), item.get("is_default", False), item.get("order", index)
            tag = item.get("tag_type", "info")
            if type(enabled) is not bool or type(default) is not bool or type(order) is not int or not 0 <= order <= 10000 or tag not in {"info", "success", "warning", "danger", "primary"}:
                invalid("字典选项状态、排序或显示颜色无效")
            if default and not enabled:
                invalid("默认选项不能停用")
            defaults += int(default)
            output.update(enabled=enabled, is_default=default, order=order, tag_type=tag)
        result.append(output)
    if defaults > 1:
        invalid("只能选择一个默认选项")
    return sorted(result, key=lambda item: item.get("order", 0))


def validate_definition(kind, definition):
    try:
        if len(json.dumps(definition, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 100000:
            invalid("配置超过100KB，请缩小内容")
    except (TypeError, ValueError, RecursionError):
        invalid("配置只接受有效JSON和有限数值")
    if kind == "dictionary":
        if set(definition) - {"entries", "description"}:
            invalid("字典字段无效")
        return {"entries": choices(definition.get("entries", []), dictionary=True), "description": text(definition.get("description", ""), "说明", 2000, True)}
    if kind == "parameter":
        if set(definition) - {"value_type", "value", "description"} or "value" not in definition:
            invalid("请填写参数类型和值")
        kind_of_value, value = definition.get("value_type", "string"), definition.get("value")
        valid = {"string": isinstance(value, str), "number": finite(value), "boolean": type(value) is bool, "json": isinstance(value, (dict, list))}
        if not valid.get(kind_of_value):
            invalid("参数值与所选类型不一致", "value")
        return {"value_type": kind_of_value, "value": value, "description": text(definition.get("description", ""), "说明", 2000, True)}
    if kind == "announcement":
        if set(definition) - {"category", "content", "publish_at", "expires_at"} or definition.get("category", "announcement") not in {"notice", "announcement"}:
            invalid("公告字段或类型无效")
        publish, expires = timestamp(definition.get("publish_at")), timestamp(definition.get("expires_at"))
        if publish and expires and expires <= publish:
            invalid("失效时间须晚于发布时间", "expires_at")
        return {"category": definition.get("category", "announcement"), "content": text(definition.get("content"), "公告内容", 20000), "publish_at": publish, "expires_at": expires}
    if kind != "form":
        invalid("配置类型不存在")
    if set(definition) - {"fields", "description"}:
        invalid("表单字段无效")
    fields = definition.get("fields", [])
    if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
        invalid("表单需要1到100个字段")
    output, names = [], set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - {"key", "label", "kind", "required", "placeholder", "minimum", "maximum", "max_length", "choices"}:
            invalid("表单字段配置无效")
        key = field.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) or key in {"__proto__", "prototype", "constructor"} or key in names:
            invalid("字段编码须为不重复的字母、数字、下划线组合")
        names.add(key)
        field_kind, required = field.get("kind", "text"), field.get("required", False)
        if field_kind not in FIELD_TYPES or type(required) is not bool:
            invalid("字段类型或必填状态无效")
        item = {"key": key, "label": text(field.get("label"), "字段名称"), "kind": field_kind, "required": required, "placeholder": text(field.get("placeholder", ""), "占位内容", 200, True)}
        if field_kind in {"number", "integer"}:
            for bound in ("minimum", "maximum"):
                value = field.get(bound)
                if value is not None:
                    if not finite(value) or field_kind == "integer" and type(value) is not int:
                        invalid("数值上下限须与字段类型一致")
                    item[bound] = value
            if item.get("minimum", float("-inf")) > item.get("maximum", float("inf")):
                invalid("最小值不能大于最大值")
        elif field_kind in {"text", "textarea"}:
            maximum = field.get("max_length", 1000)
            if type(maximum) is not int or not 1 <= maximum <= 20000:
                invalid("文本长度须为1到20000")
            item["max_length"] = maximum
        elif field_kind == "select":
            item["choices"] = choices(field.get("choices", []))
            if not item["choices"]:
                invalid("下拉字段至少需要一个选项")
        output.append(item)
    return {"fields": output, "description": text(definition.get("description", ""), "说明", 2000, True)}


def form_schema(resource):
    properties, required = {}, []
    for field in resource["definition"]["fields"]:
        kind = field["kind"]
        item = {"title": field["label"], "type": {"integer": "integer", "number": "number", "boolean": "boolean"}.get(kind, "string"), "x-control": kind}
        if kind in {"number", "integer"}:
            item.update({key: field[key] for key in ("minimum", "maximum") if key in field})
        elif kind in {"text", "textarea"}:
            item["maxLength"] = field["max_length"]
            if field["required"]:
                item["minLength"] = 1
        elif kind == "select":
            item["enum"] = [choice["value"] for choice in field["choices"]]
            item["x-options"] = field["choices"]
        elif kind == "date":
            item["format"] = "date"
        properties[field["key"]] = item
        if field["required"]:
            required.append(field["key"])
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": resource["name"], "type": "object", "additionalProperties": False, "properties": properties, "required": required}


def validate_values(resource, values):
    """预览不提交业务数据；只返回字段错误，输入值不写入审计或修订。"""
    errors, fields = {}, resource["definition"]["fields"]
    if len(values) > 100 or set(values) - {field["key"] for field in fields}:
        errors["_form"] = "包含表单以外的字段"
    for field in fields:
        key, kind = field["key"], field["kind"]
        value = values.get(key)
        if value is None or value == "":
            if field["required"]:
                errors[key] = f"请填写{field['label']}"
            continue
        if kind in {"number", "integer"}:
            if not finite(value) or kind == "integer" and type(value) is not int:
                errors[key] = "请输入有效的" + ("整数" if kind == "integer" else "数值")
            elif value < field.get("minimum", float("-inf")) or value > field.get("maximum", float("inf")):
                errors[key] = "数值超出允许范围"
        elif kind == "boolean":
            if type(value) is not bool:
                errors[key] = "请选择有效状态"
        elif not isinstance(value, str):
            errors[key] = "请输入文字"
        elif kind in {"text", "textarea"} and (len(value) > field["max_length"] or field["required"] and not value.strip()):
            errors[key] = "文字为空或超过长度限制"
        elif kind == "select" and value not in [choice["value"] for choice in field["choices"]]:
            errors[key] = "请选择列表中的选项"
        elif kind == "date":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                errors[key] = "请选择有效日期"
    return {"valid": not errors, "errors": errors, "revision": resource["revision"]}
