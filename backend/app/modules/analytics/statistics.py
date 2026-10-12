"""对返回记录生成可追溯的描述统计、趋势和图表建议。"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from statistics import median

NUMERIC_TYPES = ("int", "decimal", "float", "double", "numeric", "number", "real", "long", "short")
DATE_TYPES = ("date", "datetime", "timestamp")
MAX_ARITHMETIC_DIGITS = 4096


def _arithmetic_precision(numbers: list[Decimal], *, products: bool = False) -> int | None:
    """按实际位宽预留进位与除法精度，并限制异常指数造成的计算开销。"""
    nonzero = [value for value in numbers if value]
    if not nonzero:
        return 34
    highest = max(value.adjusted() for value in nonzero)
    lowest = min(value.as_tuple().exponent for value in nonzero)
    # 总和需要容纳最大整数位到最小小数位；平方及回归额外保留一倍位宽。
    width = highest - lowest + 1
    precision = max(34, width * (2 if products else 1) + 2 * len(str(len(numbers))) + 16)
    if precision > MAX_ARITHMETIC_DIGITS or max(abs(highest), abs(lowest)) > MAX_ARITHMETIC_DIGITS:
        return None
    return precision


def _arithmetic_policy(precision: int) -> dict:
    """公开合计与除法口径，循环小数不冒充无限精度结果。"""
    return {"sumExact": True, "precision": precision, "divisionRounding": "ROUND_HALF_EVEN"}


def _unit_column(column: dict) -> bool:
    """判断列是否为计量单位列（注释含“计量单位/数量单位”或字段名是 unit/uom）。

    单位列本身不能参与数值聚合：对单位做求和没有业务含义。
    """
    name = str(column.get('columnName') or '').lower()
    label = str(column.get('comment') or '')
    return bool(re.search(r'计量单位|数量单位', label)
                or re.search(r'(?:^|_)(?:unit|uom)(?:_?id|_?code)?$', name))


def _quantity_column(column: dict) -> bool:
    """判断列是否为“数量类”指标（数量/库存/重量…），并排除金额与比率类字段。

    用于识别跨单位求和会失真的字段（例如既有“桶”又有“件”的数量列），
    命中后统计阶段会标记 aggregationBlocked 而不是给出合计。
    """
    text = str(column.get('columnName') or '') + ' ' + str(column.get('comment') or '')
    return bool(re.search(r'数量|库存|重量|体积|stock|quantity|qty|weight|volume', text, re.I)
                and not re.search(r'金额|单价|成本|比例|率|price|amount|cost|rate|ratio', text, re.I))


def numeric(value) -> Decimal | None:
    """把值转成 Decimal；布尔、容器、空串与非有限数一律返回 None（表示“不是数值”）。

    用 Decimal 而不是 float 是为了不丢失金额精度；返回 None 与返回 0 语义不同，
    调用方据此区分“缺失”和“真实为零”。
    """
    if value is None or isinstance(value, (bool, dict, list)) or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    """仅格式化数值，不经过受当前精度影响的 normalize 舍入。"""

    if not value:
        return "0"
    # 异常科学计数值保持精确指数表示，避免展开为巨大的字符串。
    if max(abs(value.adjusted()), abs(value.as_tuple().exponent)) > MAX_ARITHMETIC_DIGITS:
        return str(value)
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _temporal(value) -> datetime | None:
    """把日期/时间值解析成无时区的 UTC datetime；无法解析时返回 None。

    兼容三类真实返回：驱动原生 date/datetime、ISO 字符串、以及 SQL 分组产生的
    “月桶”（如 DATE_FORMAT 的 ``%Y-%m``，补成当月 1 日），这样图表与趋势仍保持
    真实时间顺序。带时区的值统一换算成 UTC naive，避免跨时区比较错位。
    """
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    if not isinstance(value, str) or not value.strip():
        if parsed is None:
            return None
    else:
        normalized = value.strip().replace("Z", "+00:00")
        # SQL grouping commonly returns a month bucket rather than a complete
        # date (for example MySQL DATE_FORMAT(..., '%Y-%m')).  Treat that
        # unambiguous calendar bucket as the first day of the month so charts
        # and trend analysis keep the real temporal ordering.
        if re.fullmatch(r"\d{4}-\d{2}", normalized):
            normalized += "-01"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(normalized), datetime.min.time())
            except ValueError:
                return None
    if parsed is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _date_column_names(data: list[dict], columns: list[dict]) -> set[str]:
    """Recognize driver-native dates and ISO date strings returned by SQL functions."""

    names: set[str] = set()
    for column in columns:
        name = str(column.get("columnName") or "")
        data_type = str(column.get("dataType") or "").lower()
        if any(token in data_type for token in DATE_TYPES):
            names.add(name)
            continue
        values = [row.get(name) for row in data if row.get(name) not in (None, "")]
        if len(values) < 2:
            continue
        parsed = sum(_temporal(value) is not None for value in values)
        if parsed / len(values) >= 0.8:
            names.add(name)
    return names


def _metric_label(columns: list[dict], name: str) -> str:
    """取指标的中文标签：有字段注释用注释，否则回落到字段名（不翻译标识符）。"""
    column = next((item for item in columns if item.get("columnName") == name), {})
    return str(column.get("comment") or name)


def enhanced_analysis(
    data: list[dict], columns: list[dict], statistics: dict, *, truncated: bool,
) -> dict:
    """仅对带真实时间字段的返回样本生成趋势、异常和下一观测点投影。"""

    date_names = _date_column_names(data, columns)
    date_columns = [item for item in columns if str(item.get("columnName") or "") in date_names]
    numeric_columns = [
        item for item in columns
        if any(token in str(item.get("dataType") or "").lower() for token in NUMERIC_TYPES)
        and not _unit_column(item)
        and not ((statistics.get('columns') or {}).get(item.get('columnName')) or {}).get('aggregationBlocked')
    ]
    time_column = date_columns[0] if date_columns else None
    time_name = str((time_column or {}).get("columnName") or "")
    metric_profiles: list[dict] = []

    for column in numeric_columns[:8]:
        name = str(column.get("columnName") or "")
        field_stat = (statistics.get("columns") or {}).get(name) or {}
        profile = {
            "field": name,
            "label": _metric_label(columns, name),
            "count": int(field_stat.get("count") or 0),
            "trend": None,
            "anomaly": {
                "method": field_stat.get("outlierMethod") or "not_available",
                "count": int(field_stat.get("outlierCount") or 0),
                "lowerBound": field_stat.get("outlierLowerBound"),
                "upperBound": field_stat.get("outlierUpperBound"),
            },
            "projection": None,
        }
        series: list[tuple[datetime, Decimal]] = []
        if time_name:
            for row in data:
                point_time = _temporal(row.get(time_name))
                point_value = numeric(row.get(name))
                if point_time is not None and point_value is not None:
                    series.append((point_time, point_value))
            series.sort(key=lambda item: item[0])

        precision = _arithmetic_precision([value for _, value in series], products=True)
        if precision is None:
            profile["aggregationBlocked"] = "decimal_precision_budget"
            metric_profiles.append(profile)
            continue
        profile["arithmetic"] = _arithmetic_policy(precision)
        with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
            if len(series) >= 2:
                first_time, first_value = series[0]
                last_time, last_value = series[-1]
                change = last_value - first_value
                percent = None if first_value == 0 else change / abs(first_value) * Decimal(100)
                profile["trend"] = {
                    "direction": "up" if change > 0 else "down" if change < 0 else "stable",
                    "firstValue": _decimal_text(first_value),
                    "lastValue": _decimal_text(last_value),
                    "absoluteChange": _decimal_text(change),
                    "percentChange": _decimal_text(percent) if percent is not None else None,
                    "startTime": first_time.isoformat(),
                    "endTime": last_time.isoformat(),
                    "observations": len(series),
                    "method": "ordered_first_last_on_returned_rows",
                }

            if len(series) >= 4:
                # 以观测序号做最小二乘拟合，预测点与拟合值使用同一个中心化坐标。
                size = Decimal(len(series))
                x_mean = Decimal(len(series) - 1) / Decimal(2)
                y_mean = sum((value for _, value in series), Decimal(0)) / size
                denominator = sum((Decimal(index) - x_mean) ** 2 for index in range(len(series)))
                slope = (
                    sum((Decimal(index) - x_mean) * (value - y_mean) for index, (_, value) in enumerate(series))
                    / denominator
                ) if denominator else Decimal(0)
                projected = y_mean + slope * (size - x_mean)
                fitted = [y_mean + slope * (Decimal(index) - x_mean) for index in range(len(series))]
                residual = sum((value - fitted[index]) ** 2 for index, (_, value) in enumerate(series))
                variation = sum((value - y_mean) ** 2 for _, value in series)
                fit = Decimal(1) - residual / variation if variation else Decimal(1)
                fit = max(Decimal(0), min(Decimal(1), fit))
                intervals = [
                    Decimal(str((series[index][0] - series[index - 1][0]).total_seconds()))
                    for index in range(1, len(series))
                    if series[index][0] > series[index - 1][0]
                ]
                next_time = None
                if intervals:
                    seconds = float(median(intervals))
                    next_time = datetime.fromtimestamp(series[-1][0].timestamp() + seconds, tz=series[-1][0].tzinfo).isoformat()
                profile["projection"] = {
                    "value": _decimal_text(projected),
                    "nextTime": next_time,
                    "observations": len(series),
                    "fitR2": _decimal_text(fit.quantize(Decimal("0.0001"))),
                    "confidence": "medium" if len(series) >= 6 and fit >= Decimal("0.8") else "low",
                    "method": "linear_next_observation_on_returned_rows",
                    "caveat": "仅按本次返回结果线性外推下一观测点，不代表预算或正式业务预测。",
                }
        metric_profiles.append(profile)

    recommendations: list[dict] = []
    if truncated:
        recommendations.append({
            "type": "scope", "title": "缩小查询范围后再判断趋势",
            "description": "结果已截断，当前趋势、异常与投影只覆盖已返回样本。",
        })
    missing_cells = int(statistics.get("missingCells") or 0)
    if missing_cells:
        recommendations.append({
            "type": "quality", "title": "优先核查缺失字段",
            "description": f"本次结果有 {missing_cells} 个缺失单元格，建议先确认采集与空值业务规则。",
        })
    anomaly_total = sum(int(item["anomaly"]["count"] or 0) for item in metric_profiles)
    if anomaly_total:
        recommendations.append({
            "type": "anomaly", "title": "按业务阈值复核统计离群点",
            "description": f"数值字段共识别 {anomaly_total} 个 IQR 离群值；统计离群不直接等于业务异常。",
        })
    trend_profiles = [item for item in metric_profiles if item.get("trend")]
    if trend_profiles:
        recommendations.append({
            "type": "trend", "title": "结合业务周期复核趋势",
            "description": f"已按 {time_name} 对 {len(trend_profiles)} 个数值字段排序分析，建议补充同比、环比或目标值再做经营判断。",
        })

    return {
        "scope": "returned_rows",
        "truncated": truncated,
        "timeDimension": time_name or None,
        "timeDimensionLabel": _metric_label(columns, time_name) if time_name else None,
        "metrics": metric_profiles,
        "recommendations": recommendations[:4],
        "limitations": [
            "所有结论只基于本次 SQL 返回结果。",
            "没有真实时间字段时不生成趋势和预测。",
        ],
    }


def describe(data: list[dict], columns: list[dict], *, truncated: bool) -> tuple[dict, list[dict], str, list[dict]]:
    """对**本次返回的结果**做确定性描述统计，并给出数据质量发现与图表建议。

    参数：``data``/``columns`` 为实际返回的行与列；``truncated`` 来自查询层，用于
    在结论里明确“统计只覆盖已返回样本”。

    返回 ``(statistics, insights, analysis, hints)``：
      statistics 含逐列 count/null/unique、数值列的 sum/min/max/mean/median 与
      IQR(1.5) 离群（仅当样本数 >= 4），以及 completeness/duplicateRows；
      insights 是可追溯的质量发现（缺失、离群、重复组合、跨单位警告）；
      analysis 是口径说明文本，truncated 或单位不明时会显式声明范围限制；
      hints 只是图表**建议**（给出 xField/yFields），不代替前端决定。

    重要口径：结果集中可能混有不同计量单位（unit 列多值或为空）的数量列会被标记
    ``aggregationBlocked=mixed_or_unknown_units``，此时不输出合计、均值、趋势与占比，
    也不把缺失值当作 0——避免把跨单位相加得到的数字当成业务事实。
    """
    row_count = len(data)
    total_cells = row_count * len(columns)
    empty_cells = 0
    field_stats = {}
    numeric_columns = []
    category_columns = []
    date_columns = []
    insights = []
    temporal_names = _date_column_names(data, columns)
    unit_names = [c['columnName'] for c in columns if _unit_column(c)]
    unit_keys = {tuple(str(row.get(name)) for name in unit_names) for row in data}
    unsafe_units = bool(unit_names) and (len(unit_keys) > 1 or any(
        row.get(name) is None or str(row.get(name)).strip() == '' for row in data for name in unit_names))
    blocked_quantities = []
    for column in columns:
        name = column["columnName"]
        label = str(column.get("comment") or name)
        values = [row.get(name) for row in data]
        missing = sum(value is None or value == "" for value in values)
        empty_cells += missing
        counts = Counter(json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values if value is not None)
        item = {"count": len(values) - missing, "nullCount": missing,
                "nullRate": round(missing / row_count, 6) if row_count else 0,
                "uniqueCount": len(counts)}
        data_type = column["dataType"].lower()
        if unsafe_units and _quantity_column(column):
            item['aggregationBlocked'] = 'mixed_or_unknown_units'
            item['unitFields'] = unit_names
            blocked_quantities.append(label)
        elif any(token in data_type for token in NUMERIC_TYPES) and not _unit_column(column):
            numbers = [parsed for value in values if (parsed := numeric(value)) is not None]
            numeric_columns.append(name)
            if numbers:
                precision = _arithmetic_precision(numbers)
                if precision is None:
                    item["aggregationBlocked"] = "decimal_precision_budget"
                    numeric_columns.remove(name)
                    field_stats[name] = item
                    insights.append({"type": "precision", "severity": "warning",
                                     "title": label + "超出统计精度范围",
                                     "description": "原始明细已保留，未对异常位宽或指数的数值生成近似统计。",
                                     "recommendation": "核实字段类型、单位及科学计数法后重新统计。"})
                    continue
                item["arithmetic"] = _arithmetic_policy(precision)
                with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
                    total = sum(numbers, Decimal(0))
                    item.update({"sum": str(total), "min": str(min(numbers)), "max": str(max(numbers)),
                                 "mean": str(total / len(numbers)), "median": str(median(numbers))})
                    if len(numbers) >= 4:
                        sorted_values = sorted(numbers)
                        center = len(sorted_values) // 2
                        q1 = median(sorted_values[:center])
                        q3 = median(sorted_values[(len(sorted_values) + 1) // 2:])
                        distance = q3 - q1
                        lower_bound = q1 - Decimal("1.5") * distance
                        upper_bound = q3 + Decimal("1.5") * distance
                        item["outlierCount"] = sum(value < lower_bound or value > upper_bound for value in numbers)
                        item["outlierMethod"] = "IQR_1.5_on_returned_rows"
                        item["outlierLowerBound"] = _decimal_text(lower_bound)
                        item["outlierUpperBound"] = _decimal_text(upper_bound)
                        if item["outlierCount"]:
                            insights.append({"type": "outlier", "severity": "warning", "title": label + "存在离群值",
                                             "description": f"返回样本中 {item['outlierCount']} 个值超出 1.5 倍四分位距范围。",
                                             "recommendation": "结合业务阈值核查；统计离群不直接等于业务错误。"})
        elif name in temporal_names:
            date_columns.append(name)
        else:
            category_columns.append(name)
            item["topValues"] = [{"value": json.loads(value), "count": count} for value, count in counts.most_common(10)]
        if missing:
            insights.append({"type": "missing", "severity": "warning", "title": label + "存在缺失值",
                             "description": f"返回样本中 {missing}/{row_count} 行为空。",
                             "recommendation": "检查采集、字段映射和允许为空的业务规则。"})
        field_stats[name] = item
    unique_rows = len({json.dumps(row, sort_keys=True, ensure_ascii=False) for row in data})
    duplicate_count = row_count - unique_rows
    if duplicate_count:
        insights.append({"type": "duplicate", "severity": "info", "title": "本次返回字段存在重复组合",
                         "description": f"返回样本有 {duplicate_count} 行的所选字段值与之前的行一致，不代表原表整行重复。",
                         "recommendation": "结合主键和业务粒度判断，不能直接删除可能合法的重复记录。"})
    statistics = {"scope": "returned_rows", "rowCount": row_count, "columnCount": len(columns),
                  "truncated": truncated, "missingCells": empty_cells, "duplicateRows": duplicate_count,
                  "completeness": round((total_cells - empty_cells) / total_cells, 6) if total_cells else None,
                  "columns": field_stats}
    hints = []
    dimension = (date_columns or category_columns or [None])[0]
    if numeric_columns and dimension:
        kind = "line" if date_columns else "bar"
        hints.append({"chartType": kind, "priority": 1, "reason": "时间与数值趋势" if date_columns else "类别与数值对比",
                      "config": {"xField": dimension, "yFields": numeric_columns[:4], "aggregation": "none"}})
        if category_columns and 1 < len(data) <= 12 and all((numeric(row.get(numeric_columns[0])) or 0) >= 0 for row in data):
            hints.append({"chartType": "pie", "priority": 2, "reason": "非负数值的少量分类占比",
                          "config": {"nameField": category_columns[0], "valueField": numeric_columns[0]}})
    if len(numeric_columns) >= 2:
        hints.append({"chartType": "scatter", "priority": 3, "reason": "查看两项数值分布，不自动推断因果",
                      "config": {"xField": numeric_columns[0], "yField": numeric_columns[1]}})
    hints.append({"chartType": "table", "priority": 10, "reason": "逐项核查原始返回值", "config": {}})
    scope = "结果已按行数上限截断，以下统计仅覆盖已返回样本。" if truncated else "统计覆盖本次 SQL 返回的全部结果，不等同于整个数据源。"
    analysis = f"本次返回 {row_count} 行、{len(columns)} 列。{scope}\n缺失单元格 {empty_cells} 个，返回字段重复组合 {duplicate_count} 行。"
    if blocked_quantities:
        warning = ('计量单位混合或缺失，未对' + '、'.join(blocked_quantities)
                   + '生成跨单位合计、均值、趋势或占比。应按物料和经核实的计量单位分别统计。')
        statistics['aggregationWarnings'] = [warning]
        analysis += '\n' + warning
        insights.append({'type': 'unit_scope', 'severity': 'warning', 'title': '数量统计需要一致的计量单位',
                         'description': warning, 'recommendation': '保留逐项明细，核实计量单位后再比较。'})
    if unit_names:
        analysis += '\n计量单位为原始返回值；数值编码未核实字典时不能改写为桶、根或件，不能从规格包装推断交易单位。'
    return statistics, insights, analysis, hints
