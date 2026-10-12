"""Execute a validated read with bounded time, rows and serialized result size."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import date, datetime, time as datetime_time
from decimal import Decimal

from sqlglot import exp

from app.errors import AppError, ToolExecutionError, ValidationError
from app.common.json_numbers import preserve_json_integers
from app.modules.analytics.connections import connection_failure, elastic_client, readonly_connection
from app.modules.analytics.sql_safety import SafeQuery, driver_query
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, checkpoint

# 单次查询结果序列化后的字节上限。超过即拒绝而不是截断，因为静默截断会让
# 统计、大屏和导出拿到不完整却“看起来正常”的数据。
MAX_RESULT_BYTES = 8 * 1024 * 1024


def json_value(value):
    """保留 Decimal 与大整数精度，并拒绝将非有限数写入 JSON。"""

    # 用途：把驱动返回值转成可 JSON 序列化的结构。金额等 Decimal 转十进制字符串而
    # 不是浮点数，避免精度丢失；非有限浮点收敛为 null，防止前端 JSON.parse 失败；
    # 二进制只保留类型与长度，不回传内容（可能是大文本或敏感文件）。
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"type": "binary", "size": len(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int):
        return preserve_json_integers(value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return str(value)


def _inferred_type(value) -> str:
    """从第一个非空样本值推断结果列类型；布尔必须先于整数判断（bool 是 int 子类）。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, float):
        return "double"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, datetime_time):
        return "time"
    if isinstance(value, bytes):
        return "binary"
    return "varchar"


def _source_column_metadata(expression: exp.Column, metadata: list[dict]) -> dict:
    """Resolve metadata only for an actual projected source column.

    Output aliases must never be looked up globally: an aggregate alias such as
    ``value`` can otherwise collide with an unrelated VARCHAR column in another
    authorized table when the query returns no rows.
    """
    column_name = expression.name.casefold()
    table_name = expression.table.casefold() if expression.table else ""
    candidates = []
    for table in metadata:
        if table_name and str(table.get("tableName") or "").casefold() != table_name:
            continue
        candidates.extend(
            column for column in table.get("columns", [])
            if str(column.get("columnName") or "").casefold() == column_name
        )
    if len(candidates) == 1:
        return candidates[0]
    return {}


def _empty_projection_type(expression: exp.Expression, metadata: list[dict]) -> tuple[str, dict]:
    """Infer an empty result column from its validated SQL projection."""
    while isinstance(expression, (exp.Alias, exp.Paren)):
        expression = expression.this

    if isinstance(expression, exp.Column):
        column = _source_column_metadata(expression, metadata)
        return str(column.get("dataType") or "varchar"), column

    if isinstance(expression, exp.Count) or expression.find(exp.Count):
        return "bigint", {}

    numeric_aggregates = {"SUM", "AVG", "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "VARIANCE", "VAR_POP", "VAR_SAMP", "MEDIAN"}
    functions = list(expression.find_all(exp.Func))
    function_names = {
        (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).upper()
        for function in functions
    }
    if function_names & numeric_aggregates:
        return "decimal", {}

    if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Pow)):
        return "decimal", {}

    if isinstance(expression, exp.Cast):
        target = str(expression.args.get("to") or "").casefold()
        if any(name in target for name in ("int", "serial")):
            return "bigint", {}
        if any(name in target for name in ("decimal", "numeric", "double", "float", "real")):
            return "decimal", {}
        if "datetime" in target or "timestamp" in target:
            return "datetime", {}
        if "date" in target:
            return "date", {}
        if "time" in target:
            return "time", {}
        if any(name in target for name in ("binary", "blob")):
            return "binary", {}
        return "varchar", {}

    if isinstance(expression, exp.Literal):
        if expression.is_int:
            return "bigint", {}
        if expression.is_number:
            return "decimal", {}
        return "varchar", {}

    if function_names & {"YEAR", "MONTH", "DAY", "DAY_OF_MONTH", "DAY_OF_WEEK", "DAY_OF_YEAR", "WEEK", "WEEK_OF_YEAR", "HOUR", "MINUTE", "SECOND", "LENGTH", "CHAR_LENGTH", "CHARACTER_LENGTH", "JSON_ARRAY_LENGTH"}:
        return "bigint", {}
    if function_names & {"DATE", "TS_OR_DS_TO_DATE", "STR_TO_DATE"}:
        return "date", {}
    if function_names & {"DATETIME", "TIMESTAMP", "CURRENT_TIMESTAMP", "NOW", "TS_OR_DS_TO_TIMESTAMP"}:
        return "datetime", {}
    if function_names & {"TIME", "CURRENT_TIME"}:
        return "time", {}
    return "varchar", {}


def _projection_metadata(query: SafeQuery, index: int, metadata: list[dict]) -> tuple[str, dict]:
    """按投影顺序取第 index 列的“空结果兜底类型”与字段元数据。

    只在查询返回 0 行时使用：此时没有样本值可推断类型，只能用 SQL 投影表达式
    推断（见 ``_empty_projection_type``）；列数不匹配时退回 varchar，不抛错。
    """
    selections = list(getattr(query.tree, "selects", ()) or ())
    if index >= len(selections):
        return "varchar", {}
    return _empty_projection_type(selections[index], metadata)


def execute_read(db, source, query: SafeQuery, metadata: list[dict], *, cancelled=None,
                 before_dispatch=None) -> tuple[list[dict], list[dict], bool]:
    """执行已授权只读查询；检查取消并返回真实行与截断标记。"""

    # 参数：query 必须是 sql_safety 授权后的 SafeQuery（本函数不再做授权判断）；
    # cancelled 为可选的事件对象，用于把用户取消/任务超时传导到 SQLite 执行循环。
    # 返回 (data, columns, truncated)：truncated=True 表示源库还有未返回的行（由授权
    # 阶段多取的那一行探测得出），调用方必须把该标志透传给统计与前端，不能把限量样本
    # 当成全量结果。
    # 上限与只读保证：
    #   行数：最多 query.max_rows 行返回给调用方；
    #   字节：序列化后超过 MAX_RESULT_BYTES 直接抛 ValidationError；
    #   超时：connection_config.timeoutSeconds（1–60 秒），MySQL 用会话级
    #   max_execution_time、Postgres 用 statement_timeout、SQLite 用 progress handler；
    #   只读：MySQL/Postgres 走只读事务，SQLite 额外装 authorizer 拒绝写语句、PRAGMA、
    #   ATTACH/DETACH 与 load_extension/readfile 等函数。
    # 抛出：语句或结果超限抛 ValidationError；连接/驱动异常统一包装为脱敏的
    # ToolExecutionError（不泄露连接串、密码与驱动堆栈）。
    started = time.monotonic()
    timeout = max(1, min(60, int((source.connection_config or {}).get("timeoutSeconds") or 15)))
    interrupted = None

    def check_control():
        """兼容显式事件与当前任务控制；取消优先，不把迟到结果当成成功。"""
        if cancelled is not None and cancelled.is_set():
            raise ChatCancelled("已停止本次任务，完成的结果已保留。")
        checkpoint()

    def progress_handler():
        """仅将本回调实际触发的中断恢复为对应原因，不改写其他驱动错误。"""
        nonlocal interrupted
        try:
            check_control()
            if time.monotonic() - started > timeout:
                raise ToolExecutionError("本次只读查询达到单次执行时间上限", provider=source.provider,
                                         error_type="query_timeout", failure_domain="datasource")
        except (ChatCancelled, ChatTaskTimeout, ToolExecutionError) as exc:
            interrupted = exc
            return 1
        return 0

    try:
        check_control()
        if before_dispatch is not None:
            before_dispatch()
        if source.provider == "elasticsearch":
            with elastic_client(db, source) as client:
                check_control()
                body = {"query": query.sql, "fetch_size": query.max_rows + 1,
                        "request_timeout": str(timeout) + "s", "page_timeout": str(timeout) + "s"}
                if query.parameters:
                    raise ValidationError("Elasticsearch SQL 暂不接受命名参数；请使用其只读查询接口")
                if before_dispatch is not None:
                    before_dispatch()
                check_control()
                response = client.post("/_sql?format=json", json=body)
                response.raise_for_status()
                payload = response.json()
                names = [item["name"] for item in payload.get("columns", [])]
                raw_rows = payload.get("rows", [])
                has_cursor = bool(payload.get("cursor"))
                if has_cursor:
                    client.post("/_sql/close", json={"cursor": payload["cursor"]}).raise_for_status()
                check_control()
                columns = [{"columnName": item["name"], "dataType": item.get("type") or "varchar",
                            "comment": "", "nullable": True, "primaryKey": False} for item in payload.get("columns", [])]
                rows = [dict(zip(names, row)) for row in raw_rows[:query.max_rows]]
                truncated = len(raw_rows) > query.max_rows or has_cursor
        else:
            with readonly_connection(db, source) as connection:
                check_control()
                if source.provider == "sqlite":
                    raw_connection = connection.connection.driver_connection
                    raw_connection.set_progress_handler(
                        progress_handler, 1000,
                    )

                    def authorize(operation, argument1, argument2, _database, _trigger):
                        """SQLite 运行时兜底只读：AST 校验之外的最后一层防线。

                        即使上层授权被绕过，这里也会拒绝写语句、PRAGMA、ATTACH/DETACH
                        以及 load_extension/readfile/writefile 之类的危险函数。
                        """
                        if operation in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
                                         sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                            return sqlite3.SQLITE_DENY
                        if operation == sqlite3.SQLITE_FUNCTION and (argument2 or "").lower() in {"load_extension", "readfile", "writefile"}:
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK
                    raw_connection.set_authorizer(authorize)
                sql, parameters = driver_query(query, source.provider)
                if before_dispatch is not None:
                    before_dispatch()
                check_control()
                cursor = connection.exec_driver_sql(sql, parameters)
                check_control()
                names = list(cursor.keys())
                if len(names) != len(set(names)):
                    # 重名列会让 dict(zip(...)) 静默丢列，读者会以为结果只有这些字段。
                    raise ValidationError("查询结果包含重复列名，请使用 AS 设置唯一别名")
                # 多取一行用于判断截断，因此这里必须取 max_rows + 1 而不是 max_rows。
                raw_rows = cursor.fetchmany(query.max_rows + 1)
                check_control()
                truncated = len(raw_rows) > query.max_rows
                columns = []
                for index, name in enumerate(names):
                    value = next((row[index] for row in raw_rows if row[index] is not None), None)
                    empty_type, meta = _projection_metadata(query, index, metadata)
                    columns.append({"columnName": name, "dataType": _inferred_type(value) if value is not None else empty_type,
                                    "comment": meta.get("comment") or "", "nullable": True, "primaryKey": False})
                rows = [dict(zip(names, row)) for row in raw_rows[:query.max_rows]]
        data = [{key: json_value(value) for key, value in row.items()} for row in rows]
        # allow_nan=False 让非法浮点在序列化阶段就失败，而不是产出非法 JSON 交给前端。
        if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValidationError("查询结果超过 8 MB，请减少字段、行数或排除大文本/二进制字段")
        check_control()
        return data, columns, truncated
    except (AppError, ChatCancelled, ChatTaskTimeout):
        # 业务错误与任务取消/超时必须原样上抛：包装成连接失败会让用户停止被误报为
        # 数据源故障，也会让上层无法区分重试策略。
        raise
    except Exception as exc:
        if interrupted is not None:
            raise interrupted from exc
        raise connection_failure(source, exc) from exc
