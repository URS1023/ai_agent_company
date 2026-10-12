"""将成功查询原样绑定到指定模板，保持组件和视觉布局，不重新取数或调用模型。"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import math
import re

from ...errors import ValidationError
from ..reports.template_lock import assert_visual_unchanged, clear_template_runtime


_METRICS = {"JStatsSummary", "JNumber", "JCountTo"}
_CARTESIAN = {"JBar", "JLine", "JArea", "JBarGroup", "JHorizontalBar"}
_TABLES = {"JScrollBoard", "JTable"}
_UNIT = re.compile(r"[（(](元|万元|亿元|%|吨|千克|kg|件|台)[）)]")
_NUMERIC_TYPE = re.compile(r"^(?:tinyint|smallint|mediumint|int|integer|bigint|decimal|numeric|float|double|real|number|money|smallmoney)(?:\b|\()", re.I)
_DECIMAL_TEXT = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")


def _numeric(value):
    """空值保持未知，只接受浏览器可表示的有限数值和十进制数字文本。"""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str) and not _DECIMAL_TEXT.fullmatch(value.strip()):
        return False
    try:
        return Decimal(str(value)).is_finite() and math.isfinite(float(value))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return False


def _label_info(label, unit=""):
    """单位作为独立后缀保留，避免挤占指标标题的可读空间。"""
    found = _UNIT.search(label)
    return {"label": _UNIT.sub("", label), "unit": unit or (found.group(1) if found else "")}


def _field_info(ctx, query):
    """从保存的列注释和 SQL 实际来源恢复标签，不把模板指标名当成业务含义。"""
    import sqlglot
    from sqlglot import exp
    from sqlalchemy import select
    from ..analytics.models import SourceMetadata

    info = {c["columnName"]: _label_info(c.get("comment") or c["columnName"], c.get("unit") or "")
            for c in query.result_json.get("columns") or [] if c.get("columnName")}
    metadata = ctx.db.scalar(select(SourceMetadata).where(SourceMetadata.workspace_id == ctx.workspace_id,
                                                          SourceMetadata.datasource_id == query.datasource_id))
    tables = {t.get("tableName"): t for t in (metadata.tables_json or [])} if metadata else {}
    verified_fields = {(f.get("table"), f.get("column")): f for f in
                       ((query.model_usage_json or {}).get("businessSemantics") or {}).get("monetaryFields") or []}
    try:
        tree = sqlglot.parse_one(query.generated_sql, read="mysql")
        aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
        for item in tree.selects:
            name = item.alias_or_name
            columns = list(item.find_all(exp.Column))
            table_name = aliases.get(columns[0].table, columns[0].table) if len(columns) == 1 else ""
            if not table_name and len(aliases) == 1:
                table_name = next(iter(aliases.values()))
            table = tables.get(table_name, {})
            column = next((c for c in table.get("columns") or []
                           if len(columns) == 1 and c.get("columnName") == columns[0].name), {})
            if not column and len(columns) == 1:
                column = verified_fields.get((table_name, columns[0].name), {})
            label = str(column.get("comment") or "").strip()
            if item.find(exp.Count):
                label = "记录数"
            elif label and item.find(exp.Sum) and "合计" not in label:
                label += "合计"
            if label:
                # 卡片标题只表达指标本身；来源由 queryId 追溯，不能挤掉真正的字段名。
                info[name] = _label_info(label)
    except (sqlglot.errors.ParseError, AttributeError):
        pass
    return info


def _money_field(field):
    """只用字段语义决定展示优先级，不据此推算金额或单位。"""
    return bool(re.search(r"amount|receivable|payable|balance|金额|余额|应收|应付", field, re.I)
                and not re.search(r"count|date|time|ratio|rate|数量|日期|比例|率", field, re.I))


def _identifier(field, column):
    """数字主键和业务编码不是待展示数值指标，显式选择时仍保留原字段。"""
    return bool(column.get("primaryKey") or column.get("businessKey")
                or re.search(r"(?:^|_)(?:id|code)(?:$|_)|编号|序号|编码", field, re.I)
                or field.endswith(("Id", "ID")))


def _table_capacity(widget, canvas_width):
    """按画布比例保留最小列宽，小画布窄表不能靠横向堆列假装完整展示。"""
    return max(0, int(float(widget.get("w") or 0) / max(48.0, float(canvas_width or 1920) / 16)))


def _visible_slot(widget, canvas):
    """绑定前排除隐藏和画布外组件，避免保存后才发现指标不可见。"""
    if widget.get("visible") is False:
        return False
    x, y, width, height = (widget.get(key) for key in ("x", "y", "w", "h"))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (x, y, width, height)):
        return False
    return (width > 0 and height > 0 and x >= 0 and y >= 0
            and x + width <= canvas.get("width", 0) and y + height <= canvas.get("height", 0))


def _take_slot(slots, preferences, *, largest=True, table_columns=1, canvas_width=1920):
    """先匹配组件用途，再挑可读面积最大的槽，避免主图空着而数据挤进角落。"""
    for components in preferences:
        candidates = [w for w in slots if w["component"] in components
                      and (w["component"] not in _TABLES or _table_capacity(w, canvas_width) >= table_columns)]
        if candidates:
            chosen = (max(candidates, key=lambda w: float(w.get("w") or 0) * float(w.get("h") or 0)) if largest
                      else min(candidates, key=lambda w: (float(w.get("y") or 0), float(w.get("x") or 0))))
            slots.remove(chosen)
            return chosen
    return None


def template_snapshot_content(ctx, title, queries, template, bindings=None):
    """按真实字段复用快照填充指标、主图与补充明细，不重新取数或推算汇总。"""
    content = clear_template_runtime(template["content"])
    widgets = content.get("widgets") or []
    canvas_width = content.get("canvas", {}).get("width") or 1920
    slots = [w for w in widgets if w.get("component") in _METRICS | _CARTESIAN | _TABLES
             and _visible_slot(w, content.get("canvas", {}))]
    binding_map = {}
    for binding in bindings or []:
        if not isinstance(binding, dict) or binding.get("query_id") in binding_map:
            raise ValidationError("bindings 必须按 query_id 唯一指定真实字段")
        binding_map[binding.get("query_id")] = binding
    if set(binding_map) - {q.id for q in queries}:
        raise ValidationError("bindings 包含 query_ids 之外的查询")
    scopes, assignments, plans = [], [], []
    for query in queries:
        result = query.result_json
        rows = result.get("data")
        if (query.query_mode == "profile" or result.get("queryType") == "profile"
                or result.get("evidenceKind") == "coverage_only"):
            raise ValidationError("覆盖诊断不能用作大屏业务指标，请传入业务 SQL 查询标识")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or type(result.get("truncated")) is not bool:
            raise ValidationError("保存的查询缺少有效行数据或截断标记")
        fields = list(dict.fromkeys([c["columnName"] for c in result.get("columns") or [] if c.get("columnName")]
                                   + [k for row in rows for k in row]))
        if not fields:
            raise ValidationError("保存的查询缺少字段信息")
        numeric = [f for f in fields if any(_numeric(r.get(f)) for r in rows)
                   and all(r.get(f) is None or _numeric(r.get(f)) for r in rows)]
        column_info = {c["columnName"]: c for c in result.get("columns") or [] if c.get("columnName")}
        labels = [f for f in fields if f not in numeric]
        binding = binding_map.get(query.id, {})
        value = binding.get("value_field")
        label = binding.get("label_field")
        invalid_declared = [f for f in fields
                            if not _identifier(f, column_info.get(f, {}))
                            and _NUMERIC_TYPE.search(str(column_info.get(f, {}).get("dataType") or ""))
                            and (not value or value == f)
                            and any(row.get(f) is not None and not _numeric(row.get(f)) for row in rows)]
        if invalid_declared:
            raise ValidationError("声明为数值的字段含有不可验证的值，不能省略或补零后生成大屏；请核对原始字段格式",
                                  recovery="correct_input", execution_started=False, query_id=query.id,
                                  invalid_numeric_fields=invalid_declared)
        if value and value not in fields or label and label not in fields:
            raise ValidationError("绑定字段不在该查询真实返回列中", query_id=query.id, available_fields=fields)
        unknown = [f for f in fields if rows and all(r.get(f) is None for r in rows)]
        measures = [f for f in fields if not _identifier(f, column_info.get(f, {}))
                    and (f in numeric or f in unknown and
                         (_money_field(f) or _NUMERIC_TYPE.search(str(column_info.get(f, {}).get("dataType") or ""))))]
        if value and rows and value not in numeric and value not in unknown:
            raise ValidationError("显式数值字段没有可验证数值，不能作为指标或图表展示",
                                  recovery="correct_input", execution_started=False,
                                  query_id=query.id, numeric_fields=numeric)
        # 默认优先金额列，仍以真实字段名展示；显式映射始终优先，不混合金额与计数。
        value = value or next((f for f in measures if _money_field(f)), None) or next(iter(measures), None)
        label = label or next((f for f in labels if f not in unknown), None)
        preferences = [_TABLES] if not rows or not value or result["truncated"] else (
            [_METRICS, _TABLES] if len(rows) == 1 else [_CARTESIAN, _TABLES])
        selected_columns = list(dict.fromkeys(filter(None, [label, value]))) if binding.get("value_field") else None
        chosen = _take_slot(slots, preferences, table_columns=len(selected_columns or fields), canvas_width=canvas_width)
        if chosen is None:
            raise ValidationError("所选模板的兼容数据槽不足，尚未保存；请减少查询或明确调整布局",
                                  recovery="correct_input", execution_started=False,
                                  query_id=query.id, compatible_components=sorted(set.union(*preferences)),
                                  required_table_columns=len(selected_columns or fields),
                                  available_tables=[{"widget_id": w["id"], "readable_columns": _table_capacity(w, canvas_width)}
                                                    for w in slots if w["component"] in _TABLES])
        labels_info = _field_info(ctx, query)
        plan = {"query": query, "fields": fields, "numeric": numeric, "unknown": unknown,
                "measures": measures, "value": value, "label": label, "labels_info": labels_info, "shown": set(),
                "required": list(filter(None, [label, binding["value_field"]])) if binding.get("value_field")
                            else list(dict.fromkeys(filter(None, [label, *measures])))}
        plans.append(plan)
        assigned_columns = selected_columns if chosen["component"] in _TABLES else None
        assignments.append((chosen, plan, value, assigned_columns))
        plan["shown"].update((assigned_columns or fields) if chosen["component"] in _TABLES else filter(None, [label, value]))

    # 先让每份查询都有位置，再展开同一查询的其他指标，避免第一份宽结果独占全部卡片。
    for plan in plans:
        query, fields = plan["query"], plan["fields"]
        rows = query.result_json["data"]
        if not rows or query.result_json["truncated"]:
            continue
        candidates = plan["measures"]
        candidates = sorted(candidates, key=lambda f: not _money_field(f))
        for value in candidates:
            if value in plan["shown"]:
                continue
            preferences = [_METRICS] if len(rows) == 1 else ([_CARTESIAN] if plan["label"] else [])
            chosen = _take_slot(slots, preferences, largest=len(rows) != 1)
            columns = None
            if chosen is None:
                # 狭窄的模板表格只投影当前维度和指标，完整原始数据仍保存在快照中。
                columns = list(dict.fromkeys(filter(None, [plan["label"], value])))
                chosen = _take_slot(slots, [_TABLES], table_columns=len(columns), canvas_width=canvas_width)
            if chosen is None:
                continue
            assignments.append((chosen, plan, value, columns))
            plan["shown"].update(columns or filter(None, [plan["label"], value]))

    # 默认多指标必须完整交付。容量不足时可用原始表承接，不能用“已完成”掩盖缺项。
    for plan in plans:
        missing = [f for f in plan["required"] if f not in plan["shown"]]
        if not missing:
            continue
        existing_table = next((index for index, (widget, assigned, _, _) in enumerate(assignments)
                               if assigned is plan and widget["component"] in _TABLES
                               and _table_capacity(widget, canvas_width) >= len(plan["fields"])), None)
        if existing_table is not None:
            widget, _, value, _ = assignments[existing_table]
            assignments[existing_table] = (widget, plan, value, list(plan["fields"]))
        else:
            widget = _take_slot(slots, [_TABLES], table_columns=len(plan["fields"]), canvas_width=canvas_width)
            if widget is None:
                raise ValidationError("所选模板无法完整展示查询的数值指标，尚未保存；请选择带明细表的模板或显式指定展示字段",
                                      recovery="correct_input", execution_started=False, query_id=plan["query"].id,
                                      required_fields=plan["required"], missing_fields=missing,
                                      available_components=sorted({w["component"] for w in slots}),
                                      required_table_columns=len(plan["fields"]),
                                      available_tables=[{"widget_id": w["id"], "readable_columns": _table_capacity(w, canvas_width)}
                                                        for w in slots if w["component"] in _TABLES])
            assignments.append((widget, plan, plan["value"], list(plan["fields"])))
        plan["shown"].update(plan["fields"])

    for chosen, plan, value, columns in assignments:
        query, fields, label, labels_info = plan["query"], plan["fields"], plan["label"], plan["labels_info"]
        metric = labels_info.get(value, {}).get("label") or value or "查询结果"
        # 统计口径仍由原字段和查询追溯；卡片不重复添加“合计”挤占标题空间。
        metric = re.sub(r"合计$", "", metric)
        if len(query.result_json["data"]) > 1 and label:
            metric = f"{metric}（按{labels_info.get(label, {}).get('label') or label}）"
        displayed = (columns or fields) if chosen["component"] in _TABLES else list(filter(None, [label, value]))
        scope = {"queryId": query.id, "datasourceId": query.datasource_id, "returnedRows": len(query.result_json["data"]),
                 "columnCount": len(fields), "truncated": query.result_json["truncated"],
                 "queriedAt": query.created_at.isoformat(), "widgetId": chosen["id"],
                 "displayedFields": displayed, "requiredFields": plan["required"],
                 "unshownFields": [f for f in fields if f not in plan["shown"]]}
        scopes.append(scope)
        unit = labels_info.get(value, {}).get("unit") or ""
        rows = deepcopy(query.result_json["data"])
        chosen.update(query={"datasourceId": query.datasource_id, "sql": query.generated_sql,
                             "parameters": deepcopy(query.result_json.get("parameters") or {})},
                      datasourceId=query.datasource_id, generatedSql=query.generated_sql,
                      data=rows, dataStatus="verified", metricName=metric)
        chosen["config"].update(dataType=1, datasourceId=query.datasource_id, sql=query.generated_sql,
                                queryId=query.id, dataStatus="verified", verifiedAt=query.created_at.isoformat(),
                                chartDataParsed=deepcopy(rows), chartData=json.dumps(rows, ensure_ascii=False),
                                snapshotOnly=True, truncated=query.result_json["truncated"],
                                snapshotBinding={**{k: v for k, v in {"valueField": value, "labelField": label}.items() if v},
                                                 "unit": unit, **({"columns": columns} if columns else {})})
        if chosen["component"] in _TABLES:
            chosen["config"]["columnLabels"] = {field: labels_info.get(field, {}).get("label") or field for field in fields}
    # 标题只是本次业务绑定文本，保留原模板字体、字号、位置及所有装饰。
    title_widget = next((w for w in widgets if w.get("component") == "JText" and
                         ("title" in w.get("id", "").lower() or w.get("componentName") == template.get("name"))), None)
    if title_widget:
        title_widget["metricName"] = title
    assert_visual_unchanged(template["content"], content)
    return content, scopes
