"""适配Test5宽表系列推导，明确字段映射并保持缺值、百分比和分轴语义。"""
from decimal import Decimal, InvalidOperation, localcontext


CHART_VARIANTS = {"JStackBar", "JMultipleBar", "JPercentBar", "JMixLineBar", "JMultipleLine", "JSmoothLine", "JStepLine", "JNegativeBar"}
CARTESIAN_COMPONENTS = CHART_VARIANTS | {"JBar", "JHorizontalBar", "JLine", "JArea"}


def _number(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or len(str(value)) > 200:
        raise ValueError("数值格式无效")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("请选择数值字段") from exc
    if not result.is_finite() or abs(result) > Decimal("1e100"):
        raise ValueError("数值超出图表范围")
    return result


def series_data(widget, rows):
    """按显式字段形成宽表系列；不制造第二系列，不将缺失数据补成零。"""
    component = widget.get("component")
    binding = widget.get("binding") or {}
    fields = binding.get("value_fields") or [binding.get("value_field", "value")]
    label = binding.get("label_field", "name")
    secondary_fields = binding.get("secondary_value_fields", [])
    secondary = set(secondary_fields) if isinstance(secondary_fields, list) and all(isinstance(field, str) for field in secondary_fields) else {"__invalid__"}
    option = widget.get("config", {}).get("option", {})
    source_series = option.get("series", [])
    source_series = [source_series] if isinstance(source_series, dict) else source_series if isinstance(source_series, list) else []
    result = {"status": "empty", "categories": [], "series": [], "stacked": component in {"JStackBar", "JPercentBar"}, "percent": component == "JPercentBar", "horizontal": component == "JHorizontalBar", "smooth": component == "JSmoothLine", "step": component == "JStepLine", "component": component}
    if not rows:
        return result
    if not isinstance(fields, list) or not 1 <= len(fields) <= 8 or any(not isinstance(field, str) or not field for field in fields) or len(set(fields)) != len(fields):
        return {**result, "status": "needs_mapping", "message": "请选择1到8个不同的数值字段"}
    if secondary and (component != "JMixLineBar" or not secondary.issubset(fields[1:])):
        return {**result, "status": "needs_mapping", "message": "请为组合图选择正确的右轴字段"}
    if any(not isinstance(row, dict) or label not in row or any(field not in row for field in fields) for row in rows):
        return {**result, "status": "needs_mapping", "message": "请选择结果中实际存在的分类和数值字段"}
    if len(rows) > 2000:
        return {**result, "status": "invalid_data", "message": "图表超过2000个分类，请缩小查询范围"}
    try:
        for index, field in enumerate(fields):
            source = source_series[index] if index < len(source_series) and isinstance(source_series[index], dict) else {}
            points = [_number(row[field]) for row in rows]
            is_line = component in {"JLine", "JArea", "JMultipleLine", "JSmoothLine", "JStepLine"} or (component == "JMixLineBar" and index > 0)
            source_step = source.get("step")
            step = source_step if source_step in ("start", "middle", "end") else "end" if result["step"] or source_step is True else False
            result["series"].append({"field": field, "name": field, "axis": 1 if field in secondary else 0, "kind": "line" if is_line else "bar", "smooth": result["smooth"] or source.get("smooth") is True, "step": step, "values": points, "raw_values": [None if value is None else str(value) for value in points]})
        if result["percent"]:
            if any(value is not None and value < 0 for series in result["series"] for value in series["values"]):
                return {**result, "status": "invalid_data", "message": "百分比堆叠图需要非负数值"}
            # 同一分类分别归一化；缺少任意组成时，整根柱缺失，不能把残余分量伪装成100%。
            with localcontext() as context:
                context.prec = 60
                for index in range(len(rows)):
                    points = [series["values"][index] for series in result["series"]]
                    total = sum(points) if all(value is not None for value in points) else None
                    for series in result["series"]:
                        value = series["values"][index]
                        series["values"][index] = value / total * 100 if total else None
    except (ValueError, InvalidOperation) as exc:
        return {**result, "status": "invalid_data", "message": str(exc)}
    result["categories"] = [str(row[label]) if row[label] is not None else "" for row in rows]
    result["status"] = "ready" if any(value is not None for series in result["series"] for value in series["values"]) else "empty"
    return result


def public_series_model(widget, rows):
    """对外几何模型只输出十进制字符串；原始值用于提示，数值近似仅发生在画图时。"""
    data = series_data(widget, rows)
    result = {"status": data["status"], "categories": data["categories"], "percentage": data["percent"], "horizontal": data["horizontal"], "series": []}
    if data.get("message"):
        result["message"] = data["message"]
    for item in data["series"]:
        series = {"field": item["field"], "name": item["name"], "values": [None if value is None else str(value) for value in item["values"]], "raw_values": item["raw_values"], "axis": "secondary" if item["axis"] else "primary", "type": item["kind"], "smooth": bool(item["smooth"]), "step": item["step"]}
        if data["stacked"]:
            series["stack"] = "total"
        result["series"].append(series)
    return result
