"""Read a selected table using real metadata and bound filter values.

中文补充：这是"读取值"节点的**选表模式**（readMode=table）实现，也是"不写 SQL"能力的主要落点：
用户只选数据表、勾选字段、配置筛选/排序/取前 N 条，SQL 由本模块用 sqlglot 的表达式 AST 生成，
所有用户值都通过绑定参数（``exp.Placeholder``）传入，表名与列名走 ``quoted=True`` 标识符，
因此不存在把用户输入拼进 SQL 文本的路径。

边界（重要）：只支持 MySQL、PostgreSQL、SQLite 三种提供方，并且只支持单表 SELECT——
不支持 JOIN、子查询、聚合或自定义表达式。

**仍需用户手写 SQL / JSONPath 的节点**（本模块不覆盖这些场景，也不应试图扩到这里）：
- 手写只读 SQL：``database-query``（SQL 由用户在节点里填写，走分析模块的只读 SQL 校验）；
- 自然语言问数：``report-analysis``（query 是自然语言问题，由统一分析服务转成查询）；
- 手写 JSONPath：``jsonpath-extract``（记录路径 + 字段路径与类型）、``output-table``（recordPath）、
  ``setvar``（变量 path）、节点命名输入 inputs 的 path、``http-request`` 绑定的数据源字段路径。

表的展示名与字段的中文含义来自已同步的元数据注释（``table_metadata`` / ``SourceMetadata``），
因此界面上看到的是数据库里的中文注释名；注释缺失时回退显示物理名（见 ``read_table_values``）。

筛选值来源有三类：固定值、上游字段（value_source=input）、当前绑定设备（value_source=device）。
上游取值为空时生成恒假条件（1 = 0），绝不放宽成"无筛选读取"。
"""
from sqlglot import exp

from app.errors import ValidationError
from app.modules.analytics.connections import source_version
from app.modules.analytics.metadata import table_metadata
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sources import get_source
from app.modules.analytics.sql_safety import DIALECTS


def selected_table(ctx, config):
    """解析并校验选表配置，返回 ``(source, table_metadata)``。

    校验内容：数据源可用且提供方受支持；固定了 datasourceVersion 时版本必须一致；
    表必须存在于已同步元数据中；读取字段、筛选字段、关联字段、排序字段都必须落在该表的列集合内
    （字段名是物理列名，界面上的中文含义来自同步的表/列注释，本函数不把注释当字段名使用）。

    异常：ValidationError（提供方不支持、版本变化、字段越界）。
    副作用：只读元数据与数据源记录。
    """
    source = get_source(ctx, config.datasource_id, active=True)
    if source.provider not in {"mysql", "postgres", "sqlite"}:
        raise ValidationError("直接选表读取支持 MySQL、PostgreSQL 和 SQLite 数据源")
    if config.datasource_version is not None and source_version(source) != config.datasource_version:
        raise ValidationError("数据源已更新，请重新选择数据源并核对表和字段")
    table = table_metadata(ctx, source, config.table_name)
    allowed = {c["columnName"] for c in table["columns"]}
    if (set(config.fields) | {f.field for f in config.filters} | {m.field for m in config.matches}
            | {o.field for o in config.order_by}) - allowed:
        raise ValidationError("读取或筛选字段已变化，或不在允许范围内，请重新选择字段")
    return source, table


def upstream_values(inputs, field):
    """从上游记录里取出指定字段的去重值列表，最多 10000 个。

    兼容"记录数组"与"单对象 / 结果包"形态；上游缺该字段、字段是嵌套对象或数组、
    上游不是记录数组时抛 ValidationError（提示重新选择字段）。
    超过 10000 个不同取值时报错要求先缩小读取范围，不做静默截断。
    """
    supplied = inputs.get("input")
    rows = supplied.get("data", supplied) if isinstance(supplied, dict) else supplied
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise ValidationError("请连接上游节点，并为筛选条件选择上游字段")
    values = []
    for row in rows:
        if not isinstance(row, dict) or field not in row:
            raise ValidationError(f"上游结果没有字段“{field}”，请重新选择")
        value = row[field]
        if isinstance(value, (dict, list)):
            raise ValidationError("筛选字段必须是文本、数字或日期，不能是嵌套对象")
        if value is not None and value not in values:
            values.append(value)
        if len(values) > 10000:
            raise ValidationError("上游筛选值超过 10000 个，请先缩小读取范围")
    return values


def table_query(ctx, config, inputs, source):
    """把选表配置翻译成目标方言的 SQL 文本与绑定参数字典。

    返回 ``(sql, parameters)``。要点：
    - ``contains`` 用 ``strpos``/等价函数实现，使 ``%`` 与 ``_`` 保持字面量，
      避免 LIKE 通配符把用户输入意外当成模式（这是刻意的方言取舍）；
    - ``in`` 展开成多个占位符；非集合运算符只接受单值，多值时报错提示改用"匹配上游任一值"；
    - ``value_source=device`` 时校验设备与所选数据源一致，取值用设备编码；
    - 关联读取（match_mode != off）对每个匹配字段生成 IN 条件，取值为空同样生成恒假条件；
    - ``take`` 才加 LIMIT，否则读取全部符合条件的记录（由 max_rows 兜底）。

    异常：ValidationError。副作用：仅可能读取设备记录，不执行查询。
    """
    fields = list(dict.fromkeys([*config.fields, *[m.field for m in config.matches]]))
    query = exp.select(*[exp.column(name, quoted=True) for name in fields]).from_(
        exp.Table(this=exp.to_identifier(config.table_name, quoted=True)))
    parameters = {}

    def bound(value):
        """把一个用户值登记为新的绑定参数并返回占位符节点。

        参数名按出现顺序生成（read_value_0、read_value_1…），因此同一个值会生成多个占位符
        （不做去重，避免因为"参数名相同"而在 SQL 文本替换阶段出现歧义）。
        """
        name = f"read_value_{len(parameters)}"
        parameters[name] = value
        return exp.Placeholder(this=name)

    comparisons = {"eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}
    for rule in config.filters:
        column = exp.column(rule.field, quoted=True)
        if rule.operator in {"is_null", "not_null"}:
            condition = exp.Is(this=column, expression=exp.Null())
            if rule.operator == "not_null":
                condition = exp.Not(this=condition)
        else:
            if rule.value_source == "input":
                values = upstream_values(inputs, rule.input_field)
                if rule.skip_empty and values and all(v == "" for v in values):
                    continue
            elif rule.value_source == "device":
                from app.modules.alerts.registry import device
                selected = device(ctx, config.device_id, active=True)
                if selected.datasource_id != config.datasource_id:
                    raise ValidationError("当前设备不属于所选数据源")
                values = [selected.code]
            else:
                values = [rule.value]
            if not values:
                # An empty previous read must never turn into an unfiltered read.
                condition = exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))
            elif rule.operator == "in":
                condition = exp.In(this=column, expressions=[bound(v) for v in values])
            elif len(values) != 1:
                raise ValidationError("上游字段有多个值，请把筛选方式改为“匹配上游任一值”")
            elif rule.operator == "contains":
                # A function keeps % and _ literal, with no LIKE wildcard surprises.
                condition = exp.GT(this=exp.StrPosition(this=column, substr=bound(str(values[0]))), expression=exp.Literal.number(0))
            else:
                condition = comparisons[rule.operator](this=column, expression=bound(values[0]))
        query = query.where(condition)
    if config.match_mode != "off":
        for match in config.matches:
            values = upstream_values(inputs, match.input_field)
            condition = (exp.In(this=exp.column(match.field, quoted=True), expressions=[bound(v) for v in values])
                         if values else exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0)))
            query = query.where(condition)
    for order in config.order_by:
        query = query.order_by(exp.Ordered(this=exp.column(order.field, quoted=True), desc=order.direction == "desc"))
    if config.take is not None:
        query = query.limit(config.take)
    return query.sql(dialect=DIALECTS[source.provider]), parameters


def read_table_values(ctx, config, inputs, *, preview=False):
    """执行选表读取并返回带元数据的结果包。

    参数：
        config：``ReadValuesConfig``（read_mode 必须为 table）。
        inputs：节点输入，供筛选值来自上游字段时取值。
        preview：预览模式——只取前 20 行且**允许截断**（属性面板预览不要求完整）；
            非预览模式一旦 ``truncated`` 立即报错，绝不把部分记录交给后续判定。

    返回：``data`` + ``columns`` + ``tableName`` + ``tableComment`` 的结果包；
    列名与数据行统一按 ``field_aliases`` 改写，并回填真实列注释（回填用的是原始列名，
    否则别名会查不到注释）；``generatedSql`` 被移除——SQL 只留在授权范围内的查询追溯里，
    不进入业务输出。

    关联读取时委托 ``table_matching.attach_rows`` 把表记录挂到上游记录上。
    异常：ValidationError（截断、字段变化、上游缺字段等）；
    副作用：执行只读查询（走 analytics 的 ``workflows.execute`` 权限），不修改任何数据。
    """
    from app.modules.analytics import queries
    source, table = selected_table(ctx, config)
    query, parameters = table_query(ctx, config, inputs, source)
    result = queries.execute_query(ctx, QueryInput(datasourceId=source.id, query=query, mode="sql",
        parameters=parameters, maxRows=min(20, config.max_rows) if preview else config.max_rows,
        needAnalysis=False, needChart=False), permission="workflows.execute")
    if result.truncated and not preview:
        raise ValidationError("读取结果超过行数上限，请增加筛选条件或调整读取上限；未将部分记录交给后续判断")
    data = result.model_dump(mode="json")
    data.pop("generatedSql", None)
    if config.match_mode != "off":
        from app.modules.workflows.table_matching import attach_rows
        from app.modules.workflows.nodes import packet
        data.update(packet(attach_rows(config, inputs, data["data"])))
    else:
        data["data"] = [{config.field_aliases.get(f, f): row.get(f) for f in config.fields} for row in data["data"]]
        data["columns"] = [c for c in data["columns"] if c["columnName"] in config.fields]
        for column in data["columns"]:
            column["columnName"] = config.field_aliases.get(column["columnName"], column["columnName"])
    columns = {c["columnName"]: c for c in table["columns"]}
    original_names = {config.field_aliases.get(f, f): f for f in config.fields}
    for column in data["columns"]:
        column["comment"] = columns.get(original_names.get(column["columnName"], column["columnName"]), {}).get("comment", "")
    data.update(tableName=config.table_name, tableComment=table.get("tableComment", ""))
    return data
