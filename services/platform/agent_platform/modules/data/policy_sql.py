"""把服务端结构化行条件装入已授权AST；不拼接SQL、不在聚合或结果之后过滤。"""
from dataclasses import replace

from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from agent_platform.core.errors import DomainError

from .row_policies import RowPolicyService, scope_digest
from .sql_safety import physical_tables

PARAMETER_PREFIX = "__platform_rls_"


def reject_reserved_parameters(parameters):
    if any(isinstance(name, str) and name.lower().startswith(PARAMETER_PREFIX) for name in parameters):
        raise DomainError(422, "reserved_query_parameter", "查询参数使用了保留名称，请更名")


def referenced_fields(tree, tables):
    """记录实际引用的源字段；宽权限同事不因多余可见列被排除在合法协作之外。"""
    result = {name: set() for name in tables}
    for scope in traverse_scope(tree):
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table) and source.name in result:
                result[source.name].add(column.name)
    return {name: sorted(fields) for name, fields in sorted(result.items())}


def apply_row_policies(safe, source, session, actor, visible_schema, *, compile_sql=True):
    """物理表替换为带条件的同名关系，保留LEFT JOIN和自关联；参数仅来自服务端。"""
    policies = RowPolicyService(session, actor)
    scopes, versions = policies.effective(source, safe.tables)
    fields = referenced_fields(safe.tree, safe.tables)
    access = {"used_tables": list(safe.tables), "field_scope": fields, "row_scope": scopes,
              "policy_versions": versions, "scope_hash": scope_digest(actor.workspace_id, source.id, fields, scopes)}
    restricted = {name: scope for name, scope in scopes.items() if scope["mode"] == "rules"}
    if not restricted or not compile_sql:
        return replace(safe, access_scope=access)
    if source.provider == "elasticsearch":
        raise DomainError(422, "row_policy_provider_unsupported", "该索引已启用行权限，当前连接器尚不支持安全执行")
    tree = safe.tree.copy()
    parameters = dict(safe.parameters)
    visible = {table["tableName"]: [column["columnName"] for column in table["columns"]] for table in visible_schema}

    def bound(value):
        if len(parameters) >= 900:
            raise DomainError(422, "row_policy_budget_exceeded", "本次行权限条件过多，请简化规则")
        name = PARAMETER_PREFIX + str(len(parameters))
        while name in parameters:
            name += "_"
        parameters[name] = value
        return exp.Placeholder(this=name)

    def predicate(condition):
        column = exp.column(condition["field"], quoted=True)
        op, value = condition["operator"], condition["value"]
        if op in ("is_null", "not_null"):
            result = exp.Is(this=column, expression=exp.Null())
            return exp.Not(this=result) if op == "not_null" else result
        if op in ("in", "not_in"):
            result = exp.In(this=column, expressions=[bound(item) for item in value])
            return exp.Not(this=result) if op == "not_in" else result
        operations = {"eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}
        return operations[op](this=column, expression=bound(value))

    # 先枚举原树的真实表，不能在遍历中继续给刚生成的权限子查询重复套壳。
    for table in physical_tables(tree):
        scope = restricted.get(table.name)
        if scope is None:
            continue
        groups = [exp.and_(*(predicate(condition) for condition in conditions)) for conditions in scope["clauses"]]
        where = exp.or_(*groups) if groups else exp.false()
        original = table.copy()
        original.set("alias", None)
        relation = exp.select(*(exp.column(field, quoted=True) for field in visible[table.name])).from_(original).where(where)
        alias = table.args.get("alias")
        replacement = relation.subquery()
        replacement.set("alias", alias.copy() if alias is not None else exp.TableAlias(this=table.this.copy()))
        table.replace(replacement)
    sql = tree.sql(dialect=safe.dialect, comments=False)
    if len(sql) > 120000 or sum(1 for _ in tree.walk()) > 10000:
        raise DomainError(422, "row_policy_budget_exceeded", "本次行权限条件过多，请简化规则")
    return replace(safe, tree=tree, sql=sql, parameters=parameters, access_scope=access)
