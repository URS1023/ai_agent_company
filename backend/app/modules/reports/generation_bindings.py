"""Materialize each planned widget independently, using the shared source schema."""
from __future__ import annotations

from copy import deepcopy
import re

from app.errors import ModelError


def query_column_labels(sql: str, schemas: list[dict], datasource_id: str) -> dict[str, str]:
    """Label returned aliases from the real source column, without changing SQL.

    用 sqlglot 解析已生成 SQL，把每个 SELECT 别名的展示名回溯到源表列注释，**只读不改写
    SQL**。无法可靠判断时宁可留空也不猜：UNION 各分支口径可能不同，因此整体返回 {}；
    只有引用能唯一映射到源列时才给出标签。解析失败同样返回 {}，由调用方回退到别名。
    """

    import sqlglot
    from sqlglot import exp
    source = next((item for item in schemas if item.get('id') == datasource_id), {})
    try:
        tree = sqlglot.parse_one(sql, read=source.get('dialect') or 'mysql')
    except Exception:
        return {}
    if isinstance(tree, exp.SetOperation):
        # UNION positions can combine different measures; the first branch's
        # source comment is not an authoritative label for the combined column.
        return {}
    tables = {item.name for item in tree.find_all(exp.Table)}
    labels = {}
    columns = {}
    for table in source.get('tables', []):
        if table.get('tableName') in tables:
            for column in table.get('columns', []):
                columns.setdefault(column.get('columnName'), set()).add(column.get('comment') or column.get('columnName'))
    selected = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    for expression in selected.expressions if selected else []:
        key = expression.alias_or_name
        if not key:
            continue
        if expression.find(exp.RowNumber):
            labels[key] = '序号'
        elif key.lower() in {'month','year','date'}:
            labels[key] = {'month':'月份','year':'年份','date':'日期'}[key.lower()]
        else:
            referenced = {column.name for column in expression.find_all(exp.Column)}
            matches = set().union(*(columns.get(column, set()) for column in referenced)) if referenced else set()
            if len(matches) == 1:
                labels[key] = next(iter(matches))
    return labels


def bind_table_headers(widget: dict, fields: list[str]) -> None:
    """Use explicit field mapping for display labels; never guess columns by order.

    就地改写 ``widget.config.option.header``：``fields`` 是查询真实返回的列名，表头键必须
    能落到其中之一，否则整组表头作废并回退成"字段名即列名"。禁止按位置猜列（把中文展示名
    直接当字段名）——那会让列与实际数据错位却仍显示成功。无入参可校验，无返回值。
    """

    config = widget.setdefault("config", {})
    option = config.setdefault("option", {})
    mapping = config.get("dataMapping") or {}
    columns = mapping.get("columns") or []
    headers = option.get("header") or []
    bound = []
    for index, header in enumerate(headers if isinstance(headers, list) else []):
        if isinstance(header, dict):
            key, label = header.get("key") or header.get("field"), header.get("label") or header.get("name")
        else:
            key = label = str(header)
            key = re.sub(r"[（(][^（）()]*[）)]\s*$", "", key).strip()
        if key not in fields and isinstance(columns, list) and index < len(columns):
            mapped = columns[index]
            key = mapped.get("key") or mapped.get("field") if isinstance(mapped, dict) else mapped
        if key not in fields:
            # An unsupported display label cannot hide otherwise valid query rows.
            # Fall back to the actual field names, not positional label guessing.
            bound = []
            break
        bound.append({**(header if isinstance(header, dict) else {}), "key": key, "label": str(label or key)})
    option["header"] = bound or [{"key": field, "label": field} for field in fields]


def normalize_binding(widget: dict, source_ids: set[str]) -> dict:
    """把组件上散落的取数信息归一成唯一一份 ``widget["query"]`` 绑定。

    模型可能把 SQL/数据源写在 query、config、generatedSql 或顶层 sql 等不同位置，此函数按
    固定优先级取第一个非空值，并把结果写回 ``widget["query"]`` 供后续真实执行；只有确实
    带 SQL 或显式声明 ``sourceType=http`` 时才写回，避免把空绑定覆盖成"看似已绑定"。
    单数据源场景允许省略 datasourceId（由 source_ids 推断），零/多数据源则必须显式给出。
    绑定不是对象时抛 ``ModelError``（属于模型输出契约错误，由生成流程计入失败分支）。
    """

    config = widget.get("config") or {}
    query = widget.get("query") or {}
    if not isinstance(config, dict) or not isinstance(query, dict):
        raise ModelError("组件数据绑定必须为对象")
    sql = query.get("sql") or config.get("sql") or widget.get("generatedSql") or widget.get("sql")
    source_id = query.get("datasourceId") or config.get("datasourceId") or widget.get("datasourceId")
    if not source_id and len(source_ids) == 1:
        source_id = next(iter(source_ids))
    source_type_raw = query.get("sourceType") or config.get("sourceType")
    source_type = str(source_type_raw).strip().lower() if source_type_raw else None
    result = {"datasourceId": source_id, "sql": sql,
              "sourceType": source_type,
              "parameters": query.get("parameters", config.get("parameters", {})) or {}}
    if sql or source_type == "http":
        widget["query"] = result
    return result


def materialize_binding(ctx, widget: dict, schemas: list[dict], requirement: str, document: dict, call_model) -> tuple[dict, dict]:
    """Complete one binding while keeping SQL limited to database sources.

    逐组件独立绑定：一次模型调用只解决这一个组件的取数，避免整屏重生成。``call_model``
    由调用方注入（生成流程的带预算/重试封装），此处不自行重试。

    返回 ``(binding, usage)``；binding 形如 ``{datasourceId, sql, parameters}``，HTTP 源为
    ``{datasourceId, sourceType: "http", parameters}`` 且**不得携带 SQL**。以下情况抛
    ``ModelError``（带 widget 标识，便于前端定位到具体组件）：数据源不在授权集合内、
    parameters 不是对象、数据库源缺少 SQL、HTTP 源带了 SQL。SQL 只做形状校验，安全与
    只读由后续执行层把关。
    """

    binding, usage = call_model(ctx,
        "为已规划的这一个报表组件生成真实取数绑定，不重新生成整屏。"
        "只使用 sources 中授权的表、字段和数据源 ID，保持组件指标、单位和原始需求。"
        "数据库源使用当前库的未限定表名，不添加 schema/database 前缀，不查询系统库。"
        "数据库绑定只读 SELECT；HTTP 源返回 sourceType=http 和 datasourceId，不输出 SQL，数据由已保存固定采集配置提供。"
        "不知道数值时不能写常量，不得用无关表的 COUNT 替代原指标。"
        "没有真实金额字段时不能从附件路径或报表名称臆造金额。"
        '只返回 JSON：数据库为{"datasourceId":"授权ID","sql":"SELECT ...","parameters":{}}；HTTP 为{"datasourceId":"授权ID","sourceType":"http","parameters":{}}；'
        '确实缺少字段则返回 {"clarification":"缺少什么字段"}。',
        {"task": "materialize_report_widget", "requirement": requirement, "customerRequirement": document,
         "widget": {key: deepcopy(widget[key]) for key in ("id", "component", "componentName", "unit", "businessScope", "requirement") if key in widget},
         "sources": schemas})
    if binding.get("query") and isinstance(binding["query"], dict):
        binding = binding["query"]
    ids = {s["id"] for s in schemas}
    sql = binding.get("sql")
    source_id = binding.get("datasourceId") or (next(iter(ids)) if len(ids) == 1 else None)
    source = next((schema for schema in schemas if schema.get("id") == source_id), None)
    parameters = binding.get("parameters", {})
    if source_id not in ids or not isinstance(parameters, dict):
        raise ModelError("「" + str(widget.get("componentName") or widget.get("id")) + "」仍缺少有效的真实取数绑定", widget=widget.get("id"))
    if source and source.get("sourceType") == "http":
        if sql:
            raise ModelError("HTTP 数据源绑定不得包含 SQL", widget=widget.get("id"))
        return {"datasourceId": source_id, "sourceType": "http", "parameters": parameters}, usage
    if not isinstance(sql, str) or not sql.strip():
        raise ModelError("「" + str(widget.get("componentName") or widget.get("id")) + "」仍缺少有效的真实取数绑定", widget=widget.get("id"))
    return {"datasourceId": source_id, "sql": sql, "parameters": parameters}, usage
