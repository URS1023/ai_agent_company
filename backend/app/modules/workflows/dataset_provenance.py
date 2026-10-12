"""Describe saved read datasets without executing SQL or exposing its contents.

中文补充：本模块服务于"读取值"节点的数据集选择列表，只做**静态解析**——
用 sqlglot 解析已保存数据集的 SQL 文本，读出它引用了哪些物理表，再叠加
已同步的表注释作为展示标签。它绝不执行 SQL、不返回 SQL 原文、也不返回连接凭据。
解析失败一律降级为空列表：旧模板或非法模板不能让整个选择列表报错，
真正的查询授权会在节点实际运行时由分析模块重新校验。
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, UnsupportedError
from sqlglot.optimizer.scope import traverse_scope

from app.common.owned_records import visible_records
from app.modules.analytics.connections import source_version
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.sql_safety import DIALECTS


def referenced_tables(sql: str, provider: str) -> list[str]:
    """Resolve physical FROM/JOIN sources, excluding CTE names and subquery aliases.

    中文补充：只接受单条 SELECT / UNION 语句，且必须是已知方言；超出 30000 字符
    的 SQL 直接视为不可解析。返回排序后的物理表名列表（大小写不敏感排序），
    失败时返回空列表而不是抛异常，理由见模块 docstring。
    """
    if not sql or len(sql) > 30000 or provider not in DIALECTS:
        return []
    try:
        statements = sqlglot.parse(sql, read=DIALECTS[provider])
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
            return []
        names = {source.name for scope in traverse_scope(statements[0])
                 for _, source in scope.selected_sources.values()
                 if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier)}
        return sorted(names, key=str.casefold)
    except (OptimizeError, ParseError, UnsupportedError, RecursionError):
        # An invalid/legacy template must not break the entire choice list.
        # Normal query authorization still validates it when the node runs.
        return []


def source_table_labels(ctx, source) -> dict[str, dict]:
    """读取该数据源当前版本已同步的表注释，供表名到中文标签的映射使用。

    返回 ``{表名小写: 元数据字典}``。元数据按 ``source_version`` 精确匹配，
    避免把旧连接配置的表注释显示到新版本数据源上。需要 ``datasources.view`` 权限。
    """
    metadata = ctx.db.scalar(visible_records(ctx, SourceMetadata, "datasources.view").where(
        SourceMetadata.datasource_id == source.id,
        SourceMetadata.source_version == source_version(source),
    ))
    return {table["tableName"].casefold(): table for table in (metadata.tables_json if metadata else [])}


def describe_tables(sql: str, source, labels: dict[str, dict]) -> list[dict]:
    """把数据集引用的物理表整理成前端展示项，并按数据源白名单再过滤一次。

    ``labels`` 是 ``source_table_labels`` 的结果；没有注释时回退显示原始表名。
    ``allowedTables`` 为空表示该数据源未限制表白名单（全部放行），
    非空时只保留名单内的表——这里只影响"能否被看到"，真正的读取授权仍在查询时校验。
    """
    permitted = {name.casefold() for name in ((source.connection_config or {}).get("allowedTables") or [])}
    return [{"name": name, "label": labels.get(name.casefold(), {}).get("comment") or name}
            for name in referenced_tables(sql, source.provider)
            if not permitted or name.casefold() in permitted]
