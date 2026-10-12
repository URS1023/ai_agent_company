"""Actual source introspection; cached snapshots are bound to config versions."""
from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.orm.attributes import flag_modified

from app.errors import AppError, NotFoundError, ToolExecutionError
from app.modules.analytics.connections import (
    connection_failure, elastic_client, elastic_index_pattern, readonly_connection, source_version,
)
from app.modules.analytics.models import SourceMetadata
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, checkpoint


def _inspect_step(operation, *args, **kwargs):
    """在每个结构读取的前后检查停止信号，避免取消后继续扫描其他表。"""
    checkpoint()
    result = operation(*args, **kwargs)
    checkpoint()
    return result


def _column_scope(source, tables: list[dict]) -> list[dict]:
    """按数据源的 allowedTables / allowedColumns 收敛可见范围（表与字段双级过滤）。

    规则：表名以下划线/点号或 sqlite_/pg_ 开头的一律排除（系统与隐藏表）；
    配置了 allowedTables 时只保留其中的表；allowedColumns 针对单表限定字段，
    字段被过滤到空则该表整体不可见。返回的是**授权快照**，后续 SQL 授权、
    语义索引与提示词都以此为准，因此这里必须是最严格的一层。
    """
    config = source.connection_config or {}
    permitted = set(config.get("allowedTables") or [])
    fields = config.get("allowedColumns") or {}
    result = []
    for table in tables:
        name = table["tableName"]
        if name.startswith((".", "_", "sqlite_", "pg_")) or (permitted and name not in permitted):
            continue
        if name in fields:
            table = {**table, "columns": [column for column in table["columns"] if column["columnName"] in fields[name]]}
        if table["columns"]:
            result.append(table)
    # 关系展示跟随双方字段授权收敛；避免缓存里的旧关系造成前端误导关联。
    from app.modules.analytics.semantic import _scoped_declared_relations
    table_map = {table["tableName"]: table for table in result}
    scoped = []
    for table in result:
        foreign_keys = []
        for relation in table.get("foreignKeys") or []:
            candidate = {"sourceTable": table["tableName"], "targetTable": relation.get("referredTable"),
                         "columns": relation.get("columns") or [],
                         "targetColumns": relation.get("referredColumns") or [], "relationType": "foreign_key"}
            if _scoped_declared_relations(table, table_map, [candidate]):
                foreign_keys.append(relation)
        scoped.append({**table, "foreignKeys": foreign_keys,
                       "declaredRelations": _scoped_declared_relations(
                           table, table_map, table.get("declaredRelations") or [])})
    return scoped


def inspect_sql_source(db, source) -> list[dict]:
    """同步关系型数据源的真实结构：表/视图、字段类型与注释、主键、外键与统计。

    只读：使用 readonly_connection，不执行任何 DDL/DML。
    降级行为（重要）：
      - MySQL 的 information_schema 统计（行数估算、数据大小、建表/更新时间、表注释）
        在账号无权限时被静默跳过，此时结构仍以 SQLAlchemy inspection 为准；
      - 行数为估算值时 ``rowCountEstimated=True``，未统计时为 ``null``，不能当成 0；
      - 字段注释来自驱动返回的 ``comment``，拿不到就是空字符串，不猜测语义。
    表数量超过 2000 且未配置 allowedTables 时抛 ``ValidationError``，要求管理员先
    收窄范围（避免一次同步打爆元数据表与内存）。
    """
    checkpoint()
    with readonly_connection(db, source) as connection:
        checkpoint()
        inspector = inspect(connection)
        table_statistics = {}
        if source.provider == "mysql":
            try:
                table_statistics = {
                    str(row["TABLE_NAME"]): dict(row)
                    for row in _inspect_step(connection.exec_driver_sql,
                        "SELECT TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, "
                        "CREATE_TIME, UPDATE_TIME, TABLE_COMMENT FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = DATABASE()"
                    ).mappings().all()
                }
            except Exception as exc:
                # 只有明确的统计权限不足允许跳过；超时或断连必须停止，不能继续发结构查询。
                original = getattr(exc, "orig", exc)
                code = original.args[0] if getattr(original, "args", ()) else None
                if code not in {1142, 1143, 1227}:
                    raise
                table_statistics = {}
        tables = []
        names = [(name, "TABLE") for name in _inspect_step(inspector.get_table_names)]
        names.extend((name, "VIEW") for name in _inspect_step(inspector.get_view_names))
        if len(names) > 2000:
            permitted = set((source.connection_config or {}).get("allowedTables") or [])
            if not permitted:
                from app.errors import ValidationError
                raise ValidationError("数据表超过 2000 张，请先配置允许访问的表范围")
            names = [item for item in names if item[0] in permitted]
        for name, kind in names:
            checkpoint()
            if name.startswith(("sqlite_", "pg_")):
                continue
            configured = (source.connection_config or {}).get("allowedTables") or []
            if configured and name not in configured:
                continue
            primary = set((_inspect_step(inspector.get_pk_constraint, name) or {}).get("constrained_columns") or [])
            try:
                comment = (_inspect_step(inspector.get_table_comment, name) or {}).get("text") or ""
            except NotImplementedError:
                comment = ""
            statistics = table_statistics.get(name) or {}
            comment = comment or str(statistics.get("TABLE_COMMENT") or "")
            columns = [{
                "columnName": column["name"], "dataType": str(column["type"]),
                "comment": column.get("comment") or "", "nullable": bool(column.get("nullable", True)),
                "primaryKey": column["name"] in primary,
            } for column in _inspect_step(inspector.get_columns, name)]
            tables.append({
                "tableName": name, "tableComment": comment, "tableType": kind,
                "rowCount": (int(statistics["TABLE_ROWS"])
                             if statistics.get("TABLE_ROWS") is not None else None),
                "rowCountEstimated": bool(statistics),
                "dataSizeBytes": (int(statistics.get("DATA_LENGTH") or 0)
                                  + int(statistics.get("INDEX_LENGTH") or 0)) if statistics else None,
                "createTime": (statistics.get("CREATE_TIME").isoformat()
                               if getattr(statistics.get("CREATE_TIME"), "isoformat", None) else None),
                "updateTime": (statistics.get("UPDATE_TIME").isoformat()
                               if getattr(statistics.get("UPDATE_TIME"), "isoformat", None) else None),
                "columns": columns,
                "foreignKeys": [{
                    "columns": item.get("constrained_columns") or [],
                    "referredTable": item.get("referred_table"),
                    "referredColumns": item.get("referred_columns") or [],
                } for item in _inspect_step(inspector.get_foreign_keys, name)],
            })
        return _column_scope(source, tables)


def inspect_elastic_source(db, source) -> list[dict]:
    """同步 Elasticsearch 索引映射：索引名作为表名，嵌套 properties 展平为字段名。

    ``flatten`` 用 ``a.b.c`` 形式保留嵌套路径，因此字段名与 ES 查询语法一致；
    ES 没有列注释与主外键概念，相关字段固定为空/空列表，不做任何推断。
    与 SQL 源一样，结果最终经过 ``_column_scope`` 收敛授权范围。
    """
    checkpoint()
    with elastic_client(db, source) as client:
        response = _inspect_step(client.get, "/" + elastic_index_pattern(source) + "/_mapping")
        response.raise_for_status()
        result = []
        for name, mapping in response.json().items():
            checkpoint()
            columns = []

            def flatten(properties: dict, prefix: str = ""):
                """递归展平嵌套 mapping；带 properties 的节点不是叶子，本身不作为字段。"""
                for key, definition in properties.items():
                    full_name = prefix + key
                    if "properties" in definition:
                        flatten(definition["properties"], full_name + ".")
                    else:
                        columns.append({"columnName": full_name, "dataType": definition.get("type", "unknown"),
                                        "comment": "", "nullable": True, "primaryKey": False})
            flatten(mapping.get("mappings", {}).get("properties", {}))
            result.append({"tableName": name, "tableComment": "", "tableType": "INDEX",
                           "rowCount": None, "columns": columns, "foreignKeys": []})
        return _column_scope(source, result)


def source_metadata(ctx, source, *, refresh: bool = False) -> SourceMetadata:
    """读取已授权来源的版本化结构；迟到同步不得覆盖后续配置。"""

    # 调用前置条件：数据源必须已由调用方通过权限与数据范围校验（本函数不重复鉴权）。
    # 缓存口径：仅当快照 status=ready 且 source_version 与当前配置一致时才命中缓存，
    # 因此改了连接参数/表范围后旧快照自动失效，不会拿过期结构去授权 SQL。
    # 刷新语义：refresh=False（普通会话读元数据）只做关键词目录，不调用向量模型；
    # refresh=True（管理员点击同步）才建立语义索引。语义索引失败会降级为
    # keyword_only 并照常保存表/字段元数据——模型不可用不应让真实结构同步失败。
    checkpoint()
    record = ctx.db.scalar(select(SourceMetadata).where(
        SourceMetadata.workspace_id == ctx.workspace_id, SourceMetadata.datasource_id == source.id,
    ))
    if record and record.status == "ready" and record.source_version == source_version(source) and not refresh:
        return record
    try:
        tables = inspect_elastic_source(ctx.db, source) if source.provider == "elasticsearch" else inspect_sql_source(ctx.db, source)
        if refresh and source.provider == "mysql":
            from .lowcode_metadata import enrich_lowcode_metadata
            tables = enrich_lowcode_metadata(ctx.db, source, tables)
    except (AppError, ChatCancelled, ChatTaskTimeout):
        raise
    except Exception as exc:
        raise connection_failure(source, exc) from exc
    checkpoint()
    if record is None:
        record = SourceMetadata(workspace_id=ctx.workspace_id, owner_user_id=source.owner_user_id,
                                datasource_id=source.id, source_version=source_version(source))
        ctx.db.add(record)
    record.tables_json = tables
    record.source_version = source_version(source)
    record.status = "ready"
    record.error_message = ""
    from app.modules.analytics.semantic import build_semantic_index, catalog_profile
    from app.modules.analytics.profile_updates import update_profile
    from app.modules.analytics.connection_pool import release_read_batch
    # 结构连接释放后才进入语义索引，避免模型计算期间占用业务连接。
    release_read_batch()
    # 向量不可用已在索引内部降级；关系读取失败必须中断，不能吞掉断连/超时继续同步。
    profile = (build_semantic_index(ctx, source, tables) if refresh else
               catalog_profile(source, tables, semantic_status="keyword_only", indexed_tables=0))
    checkpoint()
    if refresh:
        # 索引补充的已核实关系属于同一结构快照，显式标记 JSON 修改确保重新持久化。
        flag_modified(record, "tables_json")
    update_profile(ctx.db, source, profile, expected_version=record.source_version)
    ctx.db.flush()
    return record


def table_metadata(ctx, source, table_name: str) -> dict:
    """取单张表的授权结构（字段、注释、外键）；不在授权快照中时抛 ``NotFoundError``。

    只读且不含行数据；返回值是元数据快照里的原始字典，调用方不得原地修改后再
    依赖其持久化状态（需要改动请先 deepcopy）。
    """
    record = source_metadata(ctx, source)
    for table in record.tables_json:
        if table["tableName"] == table_name:
            return table
    raise NotFoundError("该表不存在或不在数据源允许范围内")
