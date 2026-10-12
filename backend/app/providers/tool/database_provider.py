"""Database tools share analytics authorization, read-only execution and limits.

Connections always come from a saved, currently authorized data source. Table
and column discovery return its scoped metadata, never unrestricted SHOW SQL.

中文补充（只读语义与授权边界）：
- 数据源必须来自连接器绑定的已保存数据源，并经
  ``get_source(auth_ctx, ..., "datasources.execute", active=True)`` 做权限与
  数据范围校验；未授权、已停用或跨工作区的数据源一律拒绝。
- 表/字段清单不做 ``SHOW TABLES`` 之类的原始查询，而是返回数据源已同步的
  scoped 元数据（``_column_scope`` 会再按列级白名单过滤），避免把库内全貌泄露。
- 查询执行走 analytics 的授权管线：``parse_read_query`` 先做语句形态与节点/函数
  白名单的静态拒绝，``authorize_sql`` 再绑定授权表、列、参数与行数上限，
  ``execute_read`` 最终在只读连接（MySQL/Postgres 只读事务、SQLite authorizer）上执行，
  并返回 truncated 标志而不是把限量样本当全量。

已知边界（不要把词法/AST 筛查当成唯一防线）：
- ``parse_read_query`` 看的只是提交上来的 SQL 文本本身。被授权视图、存储过程或
  服务端函数内部再执行的语句不在可见范围内，这类残留风险只能靠会话只读兜底
  （MySQL/Postgres 只读事务、SQLite 的 set_authorizer 拒绝写语句/PRAGMA/ATTACH
  与 load_extension/readfile）；elasticsearch 复用 mysql 方言解析，方言差异处同样
  依赖服务端只读查询接口。
- 因此本模块绝不允许在 ``execute_read`` 之前自行降级为"直接连接执行"；
  ``database.list_tables`` / ``describe_table`` 也必须始终走元数据路径。
"""
from __future__ import annotations

import time

from app.errors import AppError, ToolExecutionError, ValidationError
from app.modules.analytics import governance
from app.modules.analytics.connections import database_name, source_version
from app.modules.analytics.execution import execute_read
from app.modules.analytics.metadata import _column_scope, source_metadata
from app.modules.analytics.models import QueryHistory
from app.modules.analytics.schemas import QueryResult
from app.modules.analytics.sources import get_source
from app.modules.analytics.sql_safety import authorize_sql, parse_read_query
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, checkpoint, current

from .base import ToolResult
from .normalizer import normalize


def _result(tool: str, source, columns: list[dict], rows: list[dict], *,
            truncated: bool = False, sql: str = "") -> ToolResult:
    """组装数据库工具的返回结构（列名、列元数据、行、行数、截断标志、来源与 SQL）。

    ``truncated`` 必须来自执行层的真实探测结果：为 True 表示源库还有未返回的行，
    调用方与前端据此提示"结果被截断"，不能默认成 False 冒充全量。
    ``sql`` 是授权阶段重新生成的规范化语句（表名已引号化），用于审计与排查；
    ``datasource_id`` 便于前端标注数据来源。
    """
    return normalize({
        "status": "success",
        "data": {
            "columns": [column["columnName"] for column in columns],
            "column_metadata": columns,
            "rows": rows, "row_count": len(rows), "truncated": truncated,
            "datasource_id": source.id, "sql": sql,
        },
    }, tool_name=tool)


def invoke(spec, args: dict, ctx: dict) -> ToolResult:
    """执行 database.* 系列工具：list_tables / describe_table / query。

    参数：``spec.name`` 决定动作（同时兼容带 ``database.`` 前缀与短名两种历史写法）；
    ``ctx["connector_spec"]["data_source_id"]`` 指定数据源；``args`` 中
    ``sql``/``query`` 为语句、``table``/``table_name`` 为表名、``parameters``
    为命名参数。
    权限：全部动作都要求 ``datasources.execute``；query 还需通过 SQL 授权管线
    的语句形态、节点/函数白名单、表/列范围与参数校验。
    返回：ToolResult（表清单、表结构或查询结果，含 truncated 标志）。
    异常：未知工具名/未绑定数据源/表不在授权范围抛 ToolExecutionError 或
    ValidationError；SQL 不合规一律拒绝执行（fail-closed），不会降级为放行。
    副作用：query 只读业务数据，并在平台写入治理租约和可追溯查询历史。
    """
    checkpoint()
    name = spec.name
    if name not in {"database.list_tables", "list_tables", "database.describe_table",
                    "describe_table", "database.query", "query"}:
        raise ToolExecutionError(f"未知的数据库工具: {name}")
    auth_ctx = auth_context_from_tool(ctx)
    connector = ctx.get("connector_spec") or {}
    source_id = connector.get("data_source_id") or connector.get("dataSourceId")
    if not source_id:
        raise ToolExecutionError("数据库工具必须绑定已保存并授权的数据源")
    source = get_source(auth_ctx, source_id, "datasources.execute", active=True)
    sql = str(args.get("sql") or args.get("query") or "")
    if name in {"database.query", "query"}:
        # 语句形态筛查放在元数据读取之前：语法/节点不合规时连数据源都不打开。
        parse_read_query(sql, source.provider)
        return _query(name, args, ctx, auth_ctx, source, sql, spec=spec)
    tables = _column_scope(source, source_metadata(auth_ctx, source).tables_json)

    if name in {"database.list_tables", "list_tables"}:
        columns = [{"columnName": "table_name", "dataType": "varchar"}]
        return _result(name, source, columns, [{"table_name": table["tableName"]} for table in tables])
    if name in {"database.describe_table", "describe_table"}:
        table_name = str(args.get("table") or args.get("table_name") or "").strip()
        table = next((table for table in tables if table["tableName"] == table_name), None)
        if table is None:
            raise ValidationError("表不存在或不在数据源允许范围内")
        fields = ["Field", "Type", "Null", "Key", "Default", "Extra"]
        rows = [{"Field": column["columnName"], "Type": column.get("dataType", ""),
                 "Null": "YES" if column.get("nullable", True) else "NO",
                 "Key": "PRI" if column.get("primaryKey") else "",
                 "Default": column.get("default"), "Extra": ""}
                for column in table["columns"]]
        return _result(name, source, [{"columnName": field, "dataType": "varchar"} for field in fields], rows)



def _query(name, args, ctx, auth_ctx, source, sql, *, spec=None) -> ToolResult:
    """保留连接器权限与输出合同，同时接入统一租约和恰好一次尝试计量。"""
    from app.modules.analytics.query_authority import QueryAuthority
    authority = QueryAuthority(auth_ctx, source, permission="datasources.execute",
                               source_permission="datasources.execute", run_id=ctx.get("run_id"))
    if spec is not None:
        authority.bind_tool(spec)
    authority.check()
    lease_id = governance.acquire_query_lease(auth_ctx, source)
    started = time.monotonic()
    record = None
    try:
        record = QueryHistory(id=lease_id, workspace_id=auth_ctx.workspace_id,
            owner_user_id=auth_ctx.user_id, datasource_id=source.id,
            run_id=ctx.get("run_id") or None, query_text=sql, query_mode="sql",
            datasource_version=source_version(source))
        auth_ctx.db.add(record)
        auth_ctx.db.commit()
        checkpoint()
        metadata = source_metadata(auth_ctx, source)
        authority.bind_schema(metadata.tables_json)
        tables = _column_scope(source, metadata.tables_json)
        max_rows = max(1, min(200, int((source.connection_config or {}).get("maxRows") or 200)))
        # 行数上限取来源配置与 200 的较小值，治理接入不能放宽原连接器合同。
        query = authorize_sql(sql, provider=source.provider, tables=tables,
                              max_rows=max_rows, parameters=args.get("parameters"), database=database_name(source))
        record.generated_sql = query.sql
        governance.validate_query_lease(auth_ctx, source, lease_id)
        checkpoint()
        authority.check()
        control = current.get()
        rows, columns, truncated = execute_read(auth_ctx.db, source, query, tables,
                                               cancelled=control.cancelled if control else None,
                                               before_dispatch=authority.check)
        checkpoint()
        elapsed = int((time.monotonic() - started) * 1000)
        # 保存统一结果结构，历史/导出仍能沿 queryId 与现有 Run 查验来源及列权限。
        snapshot = QueryResult(queryId=record.id, runId=record.run_id, generatedSql=query.sql,
            parameters=args.get("parameters") or {}, queryType="sql", executionTime=elapsed,
            data=rows, columns=columns, totalCount=len(rows), truncated=truncated,
            statistics={"scope": "not_requested", "rowCount": len(rows), "truncated": truncated},
            datasourceId=source.id, datasourceVersion=source_version(source))
        checkpoint()
        record.status = "success"
        record.result_json = snapshot.model_dump(mode="json")
        record.execution_ms = elapsed
        auth_ctx.db.commit()
        result = _result(name, source, columns, rows, truncated=truncated, sql=query.sql)
        # 新回执保留真实历史标识，旧回执撤列后不能再凭同名 SQL 猜测来源。
        result.data["queryId"] = record.id
        return result
    except Exception as exc:
        from app.modules.analytics.connection_pool import release_read_batch
        release_read_batch()
        auth_ctx.db.rollback()
        # 仅收口确已提交的本次历史；建历史失败不制造半成品，也不泄露驱动细节。
        saved = auth_ctx.db.get(QueryHistory, lease_id) if record is not None else None
        if saved is not None:
            saved.status = "failed"
            saved.result_json = {}
            if isinstance(exc, ChatCancelled):
                saved.error_message = "已停止本次任务，完成的结果已保留。"
            elif isinstance(exc, ChatTaskTimeout):
                saved.error_message = "本次任务已达到总时间上限，完成的结果已保留。"
            else:
                saved.error_message = exc.message if isinstance(exc, AppError) else "数据库工具执行失败，请查看运行记录"
            saved.execution_ms = int((time.monotonic() - started) * 1000)
            auth_ctx.db.commit()
        raise
    finally:
        governance.release_query_lease(auth_ctx, lease_id, source.id)
