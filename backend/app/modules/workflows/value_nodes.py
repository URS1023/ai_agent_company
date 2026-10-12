"""Reusable value nodes. Reads never make a business decision; calculations and
comparisons are separately configured nodes on the graph.

中文补充：读取值 / 计算值 / 输出结果三个"轻量值节点"的实现，面向最常见的
"取数 -> 换算 -> 出结论"场景，特点是**都不需要手写 SQL 或脚本**：

- ``read-values`` 有两种模式：``table``（选表模式，见 ``table_values``：用户只选表与字段，
  SQL 由系统用绑定参数生成）与 ``dataset``（数据集模式，SQL 存在分析模块已保存的模板里，
  本节点只能选数据集、勾选输出字段、给模板参数提供运行时取值，见 ``read_template``）。
  两种模式都**不接受用户在本节点编写 SQL**；需要自定义 SQL 时必须改用 ``database-query`` 节点。
- ``calculate-values``：固定枚举的换算/取值运算（日期差、加减乘除、区间上下限、单位、映射等），
  没有公式求值，无法执行代码。
- ``output-values``：把结论写成固定的中文字段（结果/说明/status 等），供报告与通知消费。

约束：读取结果截断即报错（不得按部分记录判断）；读取全程以 ``workflows.execute`` 权限执行。
"""
import re
from copy import deepcopy
from datetime import datetime, timedelta
from calendar import monthrange

from app.common.owned_records import owned_record, visible_records
from app.errors import ValidationError
from app.models import DataSource
from app.modules.analytics.models import QueryTemplate
from app.modules.analytics.schemas import QueryInput
from app.modules.collection.extraction import json_value
from app.modules.workflows.expressions import calculate, number


def read_template(ctx, config, *, active=False):
    """校验并返回数据集模式选中的查询模板（``QueryTemplate``）。

    ``active=True``（真正要执行）时的额外要求：模板必须是 SQL 模式且带 ``workflowValues`` 元数据、
    属于所选数据源、版本与节点固定值一致、勾选字段仍存在于元数据列中；
    绑定了设备时还要求设备属于该数据源。``active=False`` 时只校验归属权限（用于历史回看）。

    异常：ValidationError（各项不满足，文案都提示"请重新选择/保存"）；权限异常来自 owned_record。
    副作用：只读；不执行模板 SQL。
    """
    row = owned_record(ctx, QueryTemplate, config.template_id, "analytics.view")
    metadata = row.parameters_json or {}
    if active and (row.query_mode != "sql" or not metadata.get("workflowValues")):
        raise ValidationError("请选择已配置字段的数据集")
    if active and metadata.get("datasourceId") != config.datasource_id:
        raise ValidationError("数据集不属于所选数据源")
    if active and row.version != config.template_version:
        raise ValidationError("数据集定义已更新，请重新选择后保存；历史结果仍可查看")
    if config.device_id:
        from app.modules.alerts.registry import device
        selected = device(ctx, config.device_id, active=active, deleted=not active)
        if active and selected.datasource_id != config.datasource_id:
            raise ValidationError("设备不属于此数据源")
    available = {c.get("name") for c in metadata.get("columns", []) if isinstance(c, dict)}
    if active and set(config.fields) - available:
        raise ValidationError("选择的读取字段已变化，请重新选择字段")
    return row


def source_options(ctx, source_id):
    """``GET /api/v1/workflows/value-sources`` 的数据源：列出该数据源下可选的数据集与字段。

    只返回有 ``workflowValues`` 元数据的 SQL 模板，并附带 ``sourceTables``（由
    ``dataset_provenance`` 静态解析出的物理表与中文注释）、可输出列、
    是否按设备过滤（模板带 deviceKey 参数）以及模板输入参数。
    **不返回 SQL 原文，也不返回连接凭据**。需要 ``datasources.view`` 权限。
    """
    from app.modules.workflows.dataset_provenance import describe_tables, source_table_labels

    source = owned_record(ctx, DataSource, source_id, "datasources.view")
    labels = source_table_labels(ctx, source)
    rows = ctx.db.scalars(visible_records(ctx, QueryTemplate, "analytics.view")).all()
    return [{"id": r.id, "version": r.version, "name": r.name,
             "sourceTables": describe_tables(r.content, source, labels),
             "columns": r.parameters_json.get("columns", []),
             "deviceScoped": any(p.get("name") == "deviceKey" for p in r.parameters_json.get("inputs", []) if isinstance(p, dict)),
             "parameters": r.parameters_json.get("inputs", [])}
            for r in rows if r.query_mode == "sql" and (r.parameters_json or {}).get("workflowValues")
            and r.parameters_json.get("datasourceId") == source_id]


def read_values(ctx, config, inputs):
    """read-values 节点执行入口：按 read_mode 分派到选表模式或数据集模式。

    数据集模式的执行细节：
    - 模板声明的每个输入参数，若来源是 ``input`` 且上游对象里存在同名键则用上游值，
      否则回退到参数默认值；设备模式下强制写入 ``deviceKey``（取设备编码）；
    - 结果截断立即报错（"不能按部分记录判断"）；
    - 列名会按数据集声明的列名/标识字段做大小写不敏感的回写，因为某些方言会把
      未加引号的别名规范化，导致下游按字段名选择时找不到列；
    - 声明了 ``recordKey`` 标识字段时校验其存在、唯一且非空，重复标识直接报错，
      避免后续按记录关联时产生不确定结果；
    - 勾选字段时始终保留 ``identityFields``（业务报告要用的记录/设备/计划标识）；
    - 输出前移除 ``generatedSql``，SQL 只保留在授权范围内的查询追溯里。

    异常：ValidationError（截断、标识缺失/重复）；副作用：执行只读查询（``workflows.execute``）。
    """
    if config.read_mode == "table":
        from app.modules.workflows.table_values import read_table_values
        return read_table_values(ctx, config, inputs)
    from app.modules.analytics import queries
    row = read_template(ctx, config, active=True)
    parameters = dict(config.parameters)
    supplied = inputs.get("input")
    for field in row.parameters_json.get("inputs", []):
        name = field["name"]
        if config.parameter_sources.get(name, "input") == "input" and isinstance(supplied, dict) and name in supplied:
            parameters[name] = supplied[name]
        elif name not in parameters:
            parameters[name] = field.get("default")
    if config.device_id:
        from app.modules.alerts.registry import device
        parameters["deviceKey"] = device(ctx, config.device_id, active=True).code
    result = queries.execute_query(ctx, QueryInput(datasourceId=config.datasource_id,
        query=row.content, mode="sql", parameters=parameters, maxRows=config.max_rows,
        needAnalysis=False, needChart=False), permission="workflows.execute")
    if result.truncated:
        raise ValidationError("读取结果超过所选范围，请缩小范围，不能按部分记录判断")
    data = result.model_dump(mode="json")
    # Some SQL dialects normalize unquoted aliases. Restore the declared names
    # consistently before downstream field selection and device identification.
    names = [c["name"] for c in row.parameters_json.get("columns", [])] + row.parameters_json.get("identityFields", [])
    declared = {name.casefold(): name for name in names}
    aliases = {c["columnName"]: declared.get(c["columnName"].casefold(), c["columnName"]) for c in data["columns"]}
    data["data"] = [{aliases.get(k, k): v for k, v in record.items()} for record in data["data"]]
    for column in data["columns"]:
        column["columnName"] = aliases[column["columnName"]]
    if "recordKey" in row.parameters_json.get("identityFields", []):
        identities = [r.get("recordKey") for r in data["data"]]
        if any(not isinstance(key, (str, int)) or isinstance(key, bool) or key == "" for key in identities):
            raise ValidationError("数据集缺少有效记录标识，请核对来源字段")
        if len(set(identities)) != len(identities):
            raise ValidationError("读取结果中记录关联重复，请核对数据集关联，未继续生成重复或不确定的判断")
    if config.fields:
        selected_fields = set(config.fields) | set(row.parameters_json.get("identityFields", []))
        data["data"] = [{key: value for key, value in r.items() if key in selected_fields} for r in data["data"]]
        data["columns"] = [c for c in data["columns"] if c["columnName"] in selected_fields]
    # SQL stays in the authorized query trace, not in the business-facing output.
    data.pop("generatedSql", None)
    data["datasetName"] = row.name
    return data


def operand(value, row):
    """解析单个操作数：``source=field`` 时从当前行取字段值，否则取常量。

    字段缺失返回 None（由 ``calculate_one`` 统一按"无值"处理），不抛异常。
    """
    return row.get(value.field) if value.source == "field" else value.value


def scalar_text(value):
    """把标量转成去空白的文本，并把 ``℃`` 归一成 ``°C`` 以便单位正则统一匹配。

    None 返回空串（而不是 "None"），使"无值"在后续判断里保持为假值。
    """
    return str(value).strip().replace("℃", "°C") if value is not None else ""


def calculate_one(config, row):
    """对一行记录执行配置的单项计算，无法得出确定值时返回 None。

    分三类运算：
    - 直接映射：copy / coalesce / now / lookup / mapped_field；
    - 文本解析：range_lower/range_upper/range_unit 与 number/unit/lower_limit/upper_limit。
      只接受**完整匹配**的写法（例如 ``0.02-0.04Kg``、"在 5V 以上"），
      含有多个数值、附加散文、单位混用或上下限颠倒时一律返回 None——按契约要求，
      这类情况必须由人工确认，不能把"猜出来的数"写进判定结果；
    - 数值/日期运算：add_hours / add_months / contains / days_between / hours_between 及四则运算；
      日期时区一侧带时区一侧不带时返回 None；除零返回 None。

    注意这里的实现刻意"宽入严出"：入参类型不对就退化为 None，而不是抛错中断整批计算。
    """
    left = operand(config.left, row)
    right = operand(config.right, row) if config.right else None
    op = config.operation
    if op == "copy":
        return left
    if op == "coalesce":
        return right if left is None or left == "" else left
    if op == "now":
        return datetime.now().isoformat(timespec="seconds")
    if op == "lookup":
        return config.mapping.get(scalar_text(left))
    if op == "mapped_field":
        selected = config.mapping.get(scalar_text(left))
        return row.get(selected) if isinstance(selected, str) else None
    if left is None or left == "":
        return None
    if op in {"range_lower", "range_upper", "range_unit"}:
        # A full explicit interval such as 0.02-0.04Kg. Prose, alternative
        # quantities, mixed units and reversed bounds need human confirmation.
        match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(?:-|~|～|至)\s*([+-]?\d+(?:\.\d+)?)\s*(kg|g)?", scalar_text(left), re.I)
        if not match or number(match.group(1)) > number(match.group(2)):
            return None
        if op == "range_unit":
            return (match.group(3) or "").lower()
        return json_value(number(match.group(1 if op == "range_lower" else 2)))
    if op in {"number", "unit", "lower_limit", "upper_limit"}:
        # Full scalar / full single-bound requirement only. Multiple numbers or
        # combined prose never become a guessed measurement or threshold.
        text = scalar_text(left)
        pattern = r"([+-]?\d+(?:\.\d+)?)\s*(V|°C|A|%|kg|g)?"
        if op == "lower_limit":
            pattern = r"(?:在)?" + pattern + r"(?:以上|及以上)"
        elif op == "upper_limit":
            pattern = r"(?:在)?" + pattern + r"(?:以下|及以下)"
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            return None
        return (match.group(2) or "") if op == "unit" else json_value(number(match.group(1)))
    if right is None or right == "":
        return None
    if op in {"add_hours", "add_months"}:
        try:
            start = datetime.fromisoformat(scalar_text(left))
            amount = number(right)
            if op == "add_hours":
                return (start + timedelta(hours=float(amount))).isoformat()
            if amount != int(amount):
                return None
            months = start.year * 12 + start.month - 1 + int(amount)
            year, month = divmod(months, 12)
            return start.replace(year=year, month=month + 1, day=min(start.day, monthrange(year, month + 1)[1])).isoformat()
        except (ValueError, TypeError, OverflowError, ValidationError):
            return None
    if op == "contains":
        return right in left if isinstance(left, str) and isinstance(right, str) else None
    if op in {"days_between", "hours_between"}:
        try:
            start, end = datetime.fromisoformat(scalar_text(left)), datetime.fromisoformat(scalar_text(right))
            if (start.tzinfo is None) != (end.tzinfo is None):
                return None
            seconds = (end - start).total_seconds()
            return seconds / (86400 if op == "days_between" else 3600)
        except (ValueError, TypeError, OverflowError):
            return None
    try:
        first, second = number(left), number(right)
    except ValidationError:
        return None
    if op == "divide" and second == 0:
        return None
    try:
        return json_value(calculate({"add": "add", "subtract": "sub", "multiply": "mul", "divide": "div"}[op], first, second))
    except ValidationError:
        return None


def calculate_values(config, value, budget):
    """calculate-values 节点执行入口：对每条记录依次执行配置的多个计算。

    同一行内的计算按声明顺序执行，因此后项可以引用前项刚写入的字段（依赖顺序而非 DAG）。
    每项计算都会 ``budget.consume()``，超出规则预算即失败。
    异常：上游不是记录数组时由 ``records`` 抛 ValidationError。
    """
    from app.modules.workflows.nodes import records, packet
    rows = []
    for original in records(value):
        row = dict(original)
        for calculation in config.calculations:
            budget.consume()
            row[calculation.name] = calculate_one(calculation, row)
        rows.append(row)
    return packet(rows)


def output_values(config, value, budget):
    """output-values 节点执行入口：把结论写进记录，形成业务报告可消费的行。

    行为：
    - ``fields`` 非空时先按选择裁剪字段，为空则保留原始字段；
    - ``recordKey/deviceKey/planId/itemId`` 四个业务标识**始终保留**，即使未被勾选——
      否则下游无法把结论关联回具体记录；
    - 写入结论字段 ``结果``/``说明``（中文，给报告用）与 ``status``/``reason``
      （英文，给判断与通知用），另加 ``alertCount``/``pendingCount`` 便于聚合；
      质检模式（mode=quality）改用 ``不合格标记``/``待确认标记`` 并删除英文冗余字段，
      保证同一批数据只有一套口径。
    - 结论文案由 ``conclusion`` 固定映射：normal/warning/pending -> 正常|合格 / 预警|不合格 / 待确认。

    异常：上游不是记录数组时抛 ValidationError。副作用：无（纯数据成形）。
    """
    from app.modules.workflows.nodes import records, packet
    labels = {"normal": "正常", "warning": "预警", "pending": "待确认"}
    if config.mode == "quality":
        labels.update(normal="合格", warning="不合格")
    rows = []
    for original in records(value):
        budget.consume()
        row = {k: deepcopy(original.get(k)) for k in config.fields} if config.fields else deepcopy(original)
        # Business report identity is always retained through field selection.
        for k in ("recordKey", "deviceKey", "planId", "itemId"):
            if k in original:
                row[k] = original[k]
        row.update(结果=labels[config.conclusion], 说明=config.message,
                   status=config.conclusion, reason=config.message,
                   alertCount=int(config.conclusion == "warning"), pendingCount=int(config.conclusion == "pending"))
        if config.mode == "quality":
            row.update(不合格标记=int(config.conclusion == "warning"), 待确认标记=int(config.conclusion == "pending"))
            for redundant in ("reason", "alertCount", "pendingCount"):
                row.pop(redundant, None)
        rows.append(row)
    return packet(rows)
