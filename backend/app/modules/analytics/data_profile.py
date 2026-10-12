"""固定的单表数据覆盖诊断；不把诊断范围冒充用户的业务统计范围。"""
from __future__ import annotations

from copy import deepcopy
import re

from sqlalchemy import select

from app.common.authorization import require_permission
from app.errors import ValidationError
from app.modules.analytics.connections import source_version
from app.modules.analytics.models import QueryHistory, SourceMetadata
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import QueryInput
from app.modules.runtime.task_control import checkpoint, current


PROFILE_GUIDANCE = (
    "这是一张明确表的日期覆盖诊断，统计授权表的全部日期，删除过滤以scope.filters为准，不是用户指定月份、部门或客户的业务查询。"
    "记录数、空日期数和日期起止不代表营收、应收、回款或账户余额，不能作为这些财务指标导出或生成大屏。"
    "最早和最晚日期只表示该表日期字段的边界，不证明期间每个月都有记录，也不能断言整个数据源没有相关业务数据。"
    "诊断后正式取数仍保留用户原始月份、部门等条件；0行只说明实际执行SQL没有命中，不能说金额为0。"
    "同一轮相同表和日期字段的成功诊断会复用，不必重复调用。"
)


def _delete_flag_values(column: dict, provider: str) -> tuple[str, str]:
    """只识别明确的布尔或整数标记；文本、未知类型与多位 BIT 不能套用 0/1。"""
    data_type = re.sub(r"\s+", " ", str(column.get("dataType") or "").strip()).upper()
    boolean = data_type in {"BOOL", "BOOLEAN"}
    integer = re.fullmatch(
        r"(?:TINYINT|SMALLINT|MEDIUMINT|INT|INTEGER|BIGINT)(?:\(\s*\d+\s*\))?(?: UNSIGNED)?",
        data_type,
    )
    single_bit = provider == "mysql" and re.fullmatch(r"BIT\(\s*1\s*\)", data_type)
    if not (boolean or integer or single_bit):
        raise ValidationError("删除标记口径未知：仅支持明确的布尔、整数 0/1 或 MySQL BIT(1)，不猜测文本或未知类型的枚举映射")
    for option in column.get("enumValues") or []:
        value = option.get("value") if isinstance(option, dict) else None
        allowed = {"0", "1", "FALSE", "TRUE"} if boolean else {"0", "1"}
        if str(value).strip().upper() not in allowed:
            raise ValidationError("删除标记口径未知：元数据枚举不是明确的 0/1 标记，不能按未删除记录统计")
    return ("FALSE", "TRUE") if boolean else ("0", "1")


def build_profile_query(tables: list[dict], table_name: str, date_column: str, *, provider: str) -> tuple[str, dict]:
    """只从授权缓存中的真实表和日期列生成一条固定聚合，不接收任意 SQL 或金额表达式。"""
    if provider not in {"mysql", "postgres", "sqlite"}:
        raise ValidationError("数据覆盖诊断仅支持关系型数据库，不执行 HTTP/API 请求")
    matches = [table for table in tables if table.get("tableName") == table_name]
    if not table_name or len(matches) != 1:
        raise ValidationError("覆盖诊断需要 schema 返回的明确授权表名")
    table = matches[0]
    columns = {column["columnName"]: column for column in table.get("columns", [])}
    column = columns.get(date_column)
    if not date_column or not column:
        raise ValidationError("覆盖诊断需要该表已授权的明确日期字段")
    if not re.match(r"^(?:DATE|DATETIME|TIMESTAMP)\b", str(column.get("dataType") or "").strip(), re.I):
        raise ValidationError("覆盖诊断只接受 DATE、DATETIME 或 TIMESTAMP 字段，不猜测文本或金额字段的日期含义")
    quote = (lambda value: "`" + value.replace("`", "``") + "`") if provider == "mysql" else (
        lambda value: '"' + value.replace('"', '""') + '"')
    date = quote(date_column)
    deleted = [name for name in columns if name.lower().replace("_", "") == "isdeleted"]
    if len(deleted) > 1:
        raise ValidationError("该表存在多个删除标记，覆盖诊断无法唯一确定有效记录口径")
    filters = []
    record_count = "COUNT(*)"
    null_date_count = f"COUNT(CASE WHEN {date} IS NULL THEN 1 END)"
    coverage_date = date
    validity_count = ""
    if deleted:
        name = deleted[0]
        zero, one = _delete_flag_values(columns[name], provider)
        flag = quote(name)
        keep = f"{flag} = {zero}"
        # 在同一遍聚合中核实实际值域；WHERE 提前过滤会掩盖 2、文本或 NULL 等未知标记。
        record_count = f"COUNT(CASE WHEN {keep} THEN 1 END)"
        null_date_count = f"COUNT(CASE WHEN {keep} AND {date} IS NULL THEN 1 END)"
        coverage_date = f"CASE WHEN {keep} THEN {date} END"
        validity_count = (f", COUNT(CASE WHEN {flag} IS NULL OR {flag} NOT IN ({zero}, {one}) "
                          f"THEN 1 END) AS {quote('invalid_delete_flag_count')}")
        filters.append({"column": name, "operator": "=", "value": False if zero == "FALSE" else 0})
    sql = (f"SELECT {record_count} AS {quote('record_count')}, "
           f"{null_date_count} AS {quote('null_date_count')}, "
           f"MIN({coverage_date}) AS {quote('earliest_date')}, "
           f"MAX({coverage_date}) AS {quote('latest_date')}{validity_count} "
           f"FROM {quote(table_name)} LIMIT 1")
    scope = {"purpose": "data_coverage_profile", "tableName": table_name,
             "tableComment": table.get("tableComment") or "", "dateColumn": date_column,
             "dateColumnComment": column.get("comment") or "", "filters": filters,
             "dateRange": "all_dates", "rowScope": "undeleted_records" if deleted else "all_authorized_table_records",
             "businessFiltersApplied": False, "financialMetrics": False,
             "metricSemantics": "record_coverage_not_account_balances", "queryBudget": 1}
    return sql, scope


def profile_data_coverage(ctx, source, table_name: str, date_column: str, *, run_id: str | None = None) -> dict:
    """一次固定 SQL 返回一行诊断；缺少当前缓存就停止，不隐式同步或调用模型。"""
    checkpoint()
    require_permission(ctx, "analytics.execute")
    if source.provider not in {"mysql", "postgres", "sqlite"}:
        raise ValidationError("数据覆盖诊断仅支持关系型数据库，不执行 HTTP/API 请求")
    record = ctx.db.scalar(select(SourceMetadata).where(
        SourceMetadata.workspace_id == ctx.workspace_id, SourceMetadata.datasource_id == source.id,
    ))
    version = source_version(source)
    if not record or record.status != "ready" or record.source_version != version:
        raise ValidationError("覆盖诊断需要当前版本已同步的授权元数据，请先在数据源管理中同步")
    sql, scope = build_profile_query(record.tables_json, table_name, date_column, provider=source.provider)
    control = current.get()
    cache_key = ("data_coverage_profile", ctx.workspace_id, ctx.user_id, run_id, source.id, version, table_name, date_column)
    if run_id and control and cache_key in control.query_receipts:
        return {**deepcopy(control.query_receipts[cache_key]), "reused": True}
    # 不写入聊天 SQL 历史上下文，避免之后把诊断 SQL 当成业务统计模板。
    result = execute_query(ctx, QueryInput(datasourceId=source.id, query=sql, mode="sql",
                                         maxRows=1, needAnalysis=False, needChart=False))
    values = result.model_dump(mode="json")
    rows = values.get("data") or []
    validation_error = ""
    if len(rows) != 1 or values.get("truncated"):
        validation_error = "覆盖诊断未返回唯一完整的聚合结果，不能据此推断数据范围"
    elif scope["filters"] and rows[0].get("invalid_delete_flag_count") != 0:
        validation_error = "删除标记口径未知：该表含空值或非 0/1 标记，不能据此断言未删除记录为零或推断日期覆盖"
        scope = {**scope, "rowScope": "unknown_deletion_semantics"}
    history = ctx.db.get(QueryHistory, result.queryId)
    if history:
        history.query_mode = "profile"
        history.query_text = f"数据覆盖诊断：{table_name}.{date_column}"
        history.result_json = {**values, "queryType": "profile", "evidenceKind": "coverage_only", "analysis": PROFILE_GUIDANCE,
                               "retrievalEvidence": scope, "success": not bool(validation_error), "errorMessage": validation_error}
        if validation_error:
            history.status = "failed"
            history.error_message = validation_error
        ctx.db.commit()
    if validation_error:
        raise ValidationError(validation_error)
    row = rows[0]
    profile = {"recordCount": row["record_count"], "nullDateCount": row["null_date_count"],
               "earliestDate": row["earliest_date"], "latestDate": row["latest_date"]}
    view = {"resultType": "data_coverage_profile", "evidenceKind": "coverage_only", "datasourceId": source.id,
            "profileQueryId": result.queryId, "scope": scope, "profile": profile,
            "executedSql": result.generatedSql, "executionTime": result.executionTime,
            "summary": "已核对单表日期覆盖；以下是覆盖诊断，不是应收、营收或账户余额。",
            "guidance": PROFILE_GUIDANCE, "reused": False}
    # 返回独立的 profileQueryId，不冒充可直接制作业务交付的 queryId。
    if run_id and control and len(control.query_receipts) < 16:
        control.query_receipts[cache_key] = deepcopy(view)
    return view
