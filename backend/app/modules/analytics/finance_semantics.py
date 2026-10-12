"""用授权元数据和 SQL 字段血缘校验财务口径；不改 SQL、不调用模型或业务数据库。"""
from __future__ import annotations

from dataclasses import dataclass
import re

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.errors import ValidationError


_TERMS = {
    "receivable": ("应收", "未收", "待收", "receivable", "unreceived_amount"),
    "receipt": ("已收", "实收", "收款", "回款", "receipt", "received_amount"),
    "payable": ("应付", "未付", "待付", "payable", "unpaid_amount"),
    "payment": ("已付", "实付", "付款", "支付", "payment", "paid_amount"),
}
_DIRECTIONS = {"receivable": "incoming", "receipt": "incoming", "payable": "outgoing", "payment": "outgoing"}


def finance_categories(text: str) -> set[str]:
    """识别明确财务口径；收款单位等对方属性不证明资金流向，否定要求不作正向请求。"""
    value = str(text or "").lower()
    value = re.sub(r"(?:收款|付款)(?:单位|对象|对方|账号|账户|人|方|银行|名称)", "", value)
    value = re.sub(r"(应|未|待)(收|付)款", r"\1\2", value)
    result = set()
    for category, terms in _TERMS.items():
        for term in terms:
            for match in re.finditer(re.escape(term), value):
                if term in {"paid_amount", "received_amount"} and match.start() and value[match.start() - 1].isalpha():
                    continue
                if re.search(r"(?:(?:不要|不能|不得)(?:再|擅自)?(?:改为|换成|改成|替换为|替换成)|"
                             r"(?:不要|不查|不看|不含|不包括|排除|不是|非)(?:查询|查|看|统计|的)?)\s*$",
                             value[max(0, match.start() - 16):match.start()]):
                    continue
                result.add(category)
    return result


def _directions(categories: set[str]) -> set[str]:
    return {_DIRECTIONS[category] for category in categories}


def _natural_text(value: str) -> str:
    """SQL 本身与其别名不能冒充原始业务要求。"""
    value = str(value or "").strip()
    return "" if re.match(r"^(?:SELECT|WITH|```(?:sql|mysql|sqlite|postgresql)?)\b", value, re.I) else value


def _requested_scope(task: str, user_intent: dict | None) -> tuple[set[str], set[str], str]:
    """当前明确子任务可选混合总任务的一部分，但不能把单向要求变成相反方向。"""
    intent = user_intent or {}
    current = _natural_text(intent.get("currentRequest") or "")
    original = finance_categories(current)
    if not original:
        for turn in reversed(intent.get("previousUserTurns") or []):
            original = finance_categories(_natural_text(turn.get("request") or ""))
            if original:
                break
    execution_task = _natural_text(intent.get("plannerTask") or task)
    step = finance_categories(execution_task)
    if original and step and not _directions(step).issubset(_directions(original)):
        _reject("finance_task_scope_mismatch", "本次取数子任务改变了用户要求的资金方向", {
            "requestedCategories": sorted(original), "taskCategories": sorted(step)},
            "保留用户原始应收/应付方向，使用正确字段重新提交本次 SQL；不要用相反业务近似。")
    return step or original, original, execution_task


def _reject(reason: str, message: str, evidence: dict, correction: str) -> None:
    """未知口径与数据不存在不同，失败回执保留可供原生执行器修正的证据。"""
    raise ValidationError(message, executionBlocked=True, businessDataQueried=False, canProveDataAbsence=False,
        semanticValidation={"status": "rejected", "reasonCode": reason, "evidence": evidence},
        correction=correction)


def _table_categories(table: dict) -> set[str]:
    """已登记含义和真实表标题提供表级方向，不从模型解释或输出别名取证。"""
    declared = table.get("financeCategory")
    if isinstance(declared, str) and declared in _TERMS:
        return {declared}
    # 明确业务注释优先，避免 customer_payment 等英文物理名把客户回款误判为对外付款。
    return finance_categories(table.get("tableComment") or "") or finance_categories(table.get("tableName") or "")


def _column_meaning(table: dict, column: dict) -> tuple[set[str], bool]:
    """金额以字段自身口径为准；合同金额不能因所在表相关就自动变成应收余额。"""
    name = str(column.get("columnName") or "")
    comment = str(column.get("comment") or "")
    declared = column.get("financeCategory")
    categories = {declared} if isinstance(declared, str) and declared in _TERMS else finance_categories(comment or name)
    description = name + " " + comment
    identity = column.get("primaryKey") or column.get("businessKey") or re.search(r"(?:^|_)id$|Id$|ID$", name)
    dimension = re.search(r"日期|时间|单位|名称|账号|账户|编号|状态|方式|比例|比率|税率|率$|date|time|status|rate", description, re.I)
    monetary = bool(not identity and not dimension and (categories or re.search(
        r"金额|余额|价款|费用|成本|单价|总价", comment) or re.search(r"amount|balance|money|amt", name, re.I)))
    if monetary and not categories and not re.search(r"合同|销售金额|成本|费用|单价|总价|contract", description, re.I):
        categories = _table_categories(table)
    return categories if monetary else set(), monetary


@dataclass
class _Lineage:
    """解析已通过 SQL 授权的 AST，追踪别名、子查询、CTE 与 UNION 的真实字段。"""
    tree: exp.Expression

    def __post_init__(self):
        self.scopes = list(traverse_scope(self.tree))
        self.by_expression = {id(scope.expression): scope for scope in self.scopes}
        self.outputs: dict[int, list[tuple[str, set[tuple[str, str]]]]] = {}
        self.counts: dict[tuple[int, str], bool] = {}
        for scope in self.scopes:
            if scope.union_scopes:
                branches = [self.outputs.get(id(branch), []) for branch in scope.union_scopes]
                self.outputs[id(scope)] = [(name, set().union(*(branch[index][1] for branch in branches if len(branch) > index)))
                    for index, (name, _) in enumerate(branches[0] if branches else [])]
                for index, (name, _) in enumerate(self.outputs[id(scope)]):
                    self.counts[id(scope), name] = any(self.counts.get((id(branch), self.outputs[id(branch)][index][0]), False)
                        for branch in scope.union_scopes if len(self.outputs[id(branch)]) > index)
            else:
                self.outputs[id(scope)] = [(selection.alias_or_name, self.origins(selection, scope))
                    for selection in scope.expression.selects]
                for selection in scope.expression.selects:
                    self.counts[id(scope), selection.alias_or_name] = self.is_count(selection, scope)

    def value_children(self, node: exp.Expression):
        """CASE 判断、窗口排序和过滤列决定取哪些行，不是金额的值来源。"""
        if isinstance(node, exp.Select):
            return node.selects
        if isinstance(node, exp.Case):
            return [item.args.get("true") for item in node.args.get("ifs") or []] + [node.args.get("default")]
        if isinstance(node, exp.If):
            return [node.args.get("true"), node.args.get("false")]
        if isinstance(node, (exp.Filter, exp.Window)):
            return [node.this]
        return node.iter_expressions()

    def is_count(self, expression: exp.Expression, scope: Scope) -> bool:
        """计数字段跨派生表后仍不能被重命名为金额。"""
        if isinstance(expression, exp.Count):
            return True
        if isinstance(expression, exp.Column):
            source = scope.sources.get(expression.table)
            return isinstance(source, Scope) and self.counts.get((id(source), expression.name), False)
        return any(self.is_count(child, scope) for child in self.value_children(expression) if child is not None)

    def column(self, column: exp.Column, scope: Scope) -> set[tuple[str, str]]:
        """限定字段已经由授权阶段消歧，派生表字段按投影位置继续追踪。"""
        cursor = scope
        while cursor:
            source = cursor.sources.get(column.table)
            if isinstance(source, exp.Table):
                return {(source.name, column.name)}
            if isinstance(source, Scope):
                return set().union(*(origins for name, origins in self.outputs.get(id(source), [])
                                     if name.lower() == column.name.lower()))
            cursor = cursor.parent
        return set()

    def origins(self, expression: exp.Expression, scope: Scope) -> set[tuple[str, str]]:
        """不让内层同名别名错误绑定到外层表。"""
        if isinstance(expression, exp.Count):
            return set()
        if isinstance(expression, exp.Column):
            owner = expression.find_ancestor(exp.Select, exp.SetOperation)
            return self.column(expression, self.by_expression.get(id(owner), scope))
        return set().union(*(self.origins(child, scope) for child in self.value_children(expression) if child is not None))

    def tables(self, scope: Scope) -> set[str]:
        """只计实际 FROM 引用，未使用的 CTE 不构成当前查询证据。"""
        result = set()
        for branch in scope.union_scopes:
            result.update(self.tables(branch))
        for _, source in scope.selected_sources.values():
            result.update({source.name} if isinstance(source, exp.Table) else self.tables(source))
        return result


def _relationship_groups(tables: list[dict]) -> list[frozenset]:
    """只接受当前授权快照中真实外键或已核验业务引用，完整保留复合键。"""
    from app.modules.analytics.semantic import _scoped_declared_relations
    table_map = {table["tableName"]: table for table in tables}
    result = set()
    for table in tables:
        declared = list(table.get("declaredRelations") or [])
        declared += [{"sourceTable": table["tableName"], "targetTable": fk.get("referredTable"),
                      "columns": fk.get("columns"), "targetColumns": fk.get("referredColumns"),
                      "relationType": "foreign_key"} for fk in table.get("foreignKeys") or []]
        for relation in _scoped_declared_relations(table, table_map, declared):
            if relation.get("joinKeysKnown") is False or relation.get("relationType") not in {"reference", "foreign_key"}:
                continue
            left = relation.get("columns") or [relation.get("sourceField")]
            right = relation.get("targetColumns") or [relation.get("targetField")]
            if left and len(left) == len(right) and all(left) and all(right):
                result.add(frozenset(tuple(sorted(((table["tableName"].lower(), a.lower()),
                                                  (relation["targetTable"].lower(), b.lower())))) for a, b in zip(left, right)))
    return list(result)


def _validate_joins(lineage: _Lineage, tables: list[dict]) -> int:
    """金融关联须由完整声明键连通；已正确关联后的金额比较只是筛选，不另猜关系。"""
    groups = _relationship_groups(tables)
    checked = 0
    for scope in lineage.scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        joins = scope.expression.args.get("joins") or []
        if not joins:
            continue
        predicates = [join.args.get("on") for join in joins] + [scope.expression.args.get("where")]
        by_aliases: dict[tuple[str, str], set[tuple]] = {}
        for predicate in filter(None, predicates):
            for comparison in predicate.find_all(exp.EQ):
                left, right = comparison.args.get("this"), comparison.args.get("expression")
                if (not isinstance(left, exp.Column) or not isinstance(right, exp.Column)
                        or comparison.find_ancestor(exp.Or, exp.Not)):
                    continue
                if not left.table or left.table == right.table:
                    continue
                own, other = lineage.column(left, scope), lineage.column(right, scope)
                if len(own) != 1 or len(other) != 1:
                    continue
                pair = tuple(sorted((tuple(part.lower() for part in next(iter(own))),
                                     tuple(part.lower() for part in next(iter(other))))))
                by_aliases.setdefault(tuple(sorted((left.table, right.table))), set()).add(pair)
        graph: dict[str, set[str]] = {}
        for aliases, pairs in by_aliases.items():
            matched = [group for group in groups if group.issubset(pairs)]
            if matched:
                a, b = aliases
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)
                checked += len(set().union(*matched))
        from_clause = scope.expression.args.get("from_")
        anchor = from_clause.this.alias_or_name if from_clause is not None else ""
        reachable, pending = {anchor}, [anchor]
        while pending:
            for alias in graph.get(pending.pop(), set()) - reachable:
                reachable.add(alias)
                pending.append(alias)
        for join in joins:
            alias = join.this.alias_or_name
            if alias in reachable:
                continue
            source = scope.sources.get(alias)
            scalar = isinstance(source, Scope) and isinstance(source.expression, exp.Select) and not source.expression.args.get("group") and any(
                selection.find(exp.AggFunc) for selection in source.expression.selects)
            if not scalar:
                pairs = set().union(*(pairs for aliases, pairs in by_aliases.items() if alias in aliases))
                _reject("finance_join_unverified", "财务关联缺少已核验的完整字段依据", {
                    "joinedAlias": alias, "joinFields": [[list(endpoint) for endpoint in pair] for pair in sorted(pairs)]},
                    "使用 schema 中 foreignKeys 或 joinKeysKnown=true 的完整业务键；客户名称与收款单位不能作为推测关联。")
    return checked


def finance_metadata_profile(table: dict) -> dict:
    """把同一份确定性元数据口径提供给执行器，减少提交前反复试错。"""
    fields = []
    for column in table.get("columns") or []:
        categories, monetary = _column_meaning(table, column)
        if monetary:
            fields.append({"columnName": column["columnName"], "comment": column.get("comment") or "",
                           "categories": sorted(categories), "meaningConfirmed": bool(categories)})
    return {"tableCategories": sorted(_table_categories(table)), "monetaryFields": fields,
            "meaningSource": "authorized_metadata", "noDataExistenceClaim": True}


def validate_finance_query(safe_query, tables: list[dict], task: str = "", *, user_intent: dict | None = None) -> dict:
    """在业务执行前确定性校验资金方向、金额字段与关系；成功后原样执行授权 SQL。"""
    expected, _, execution_task = _requested_scope(task, user_intent)
    table_map = {table["tableName"].lower(): table for table in tables}
    lineage = _Lineage(safe_query.tree)
    if not lineage.scopes:
        return {"status": "not_applicable"}
    root = lineage.scopes[-1]
    used_tables = lineage.tables(root)
    table_categories = set().union(*(_table_categories(table_map[name.lower()]) for name in used_tables if name.lower() in table_map))
    outputs, selected_categories, monetary_columns = [], set(), []
    for label, origins in lineage.outputs[id(root)]:
        categories, monetary, unconfirmed_money = set(), False, False
        for table_name, column_name in origins:
            table = table_map.get(table_name.lower(), {})
            column = next((column for column in table.get("columns") or [] if column["columnName"].lower() == column_name.lower()), {})
            meaning, is_money = _column_meaning(table, column)
            categories.update(meaning)
            monetary = monetary or is_money
            unconfirmed_money = unconfirmed_money or (is_money and not meaning)
            if is_money:
                monetary_columns.append({"table": table_name, "column": column_name, "comment": column.get("comment") or "",
                                         "categories": sorted(meaning)})
        claimed = finance_categories(label)
        if claimed and monetary and (unconfirmed_money or not categories or not claimed.issubset(categories)
                                      or not _directions(categories).issubset(_directions(claimed))):
            _reject("finance_measure_mismatch", "结果名称与真实金额字段的业务口径不一致", {
                "output": label, "claimedCategories": sorted(claimed), "fieldCategories": sorted(categories),
                "sourceFields": sorted(origins)}, "保留真实字段口径；合同金额、已收金额、未付金额均不能改名冒充应收。")
        if claimed and not monetary:
            field_claims = set()
            for table_name, column_name in origins:
                table = table_map.get(table_name.lower(), {})
                column = next((column for column in table.get("columns") or []
                               if column["columnName"].lower() == column_name.lower()), {})
                field_claims.update(finance_categories(column.get("comment") or column_name))
                field_claims.update(_table_categories(table))
            if lineage.counts.get((id(root), label)):
                field_claims.update(table_categories)
            if not claimed.issubset(field_claims) or re.search(r"金额|余额|单价|amount|balance", label, re.I):
                _reject("finance_measure_unverified", "结果标注的财务口径没有真实字段依据", {
                    "output": label, "claimedCategories": sorted(claimed), "sourceFields": sorted(origins)},
                    "不要将常量、付款计数或无口径字段标为应收；提供相应真实字段或明确业务定义。")
        outputs.append({"label": label, "categories": sorted(categories), "monetary": monetary})
        selected_categories.update(categories)
    financial = bool(expected or table_categories or selected_categories or any(finance_categories(item["label"]) for item in outputs))
    if not financial:
        return {"status": "not_applicable"}
    checked_joins = _validate_joins(lineage, tables)
    actual_directions = _directions(selected_categories or table_categories)
    expected_directions = _directions(expected)
    if expected_directions and actual_directions and not actual_directions.issubset(expected_directions):
        _reject("finance_direction_mismatch", "SQL 取数方向与本次要求不一致，付款不能代替应收", {
            "requestedCategories": sorted(expected), "actualCategories": sorted(selected_categories or table_categories),
            "monetaryFields": monetary_columns}, "按用户要求重新选择已核实的财务字段；缺少依据时说明口径待确认，不要用相反方向近似。")
    scope_text = " ".join([execution_task, str((user_intent or {}).get("currentRequest") or ""),
        *(str(turn.get("request") or "") for turn in (user_intent or {}).get("previousUserTurns") or [])])
    strict = expected.intersection({"receivable", "payable"})
    if "receipt" in expected and re.search(r"已收|实收|(?:收款|回款)金额", scope_text):
        strict.add("receipt")
    if "payment" in expected and re.search(r"已付|实付|(?:付款|支付)金额", scope_text):
        strict.add("payment")
    # “付款情况”可包含待付明细；明确应收/应付或实收实付金额则必须有该口径的真实字段。
    if expected and monetary_columns and (not selected_categories or (strict and not strict.intersection(selected_categories))):
        _reject("finance_measure_unverified", "当前金额字段尚不能证明用户要求的财务口径", {
            "requestedCategories": sorted(expected), "monetaryFields": monetary_columns},
            "查找具有明确口径的字段或已登记业务定义；目录候选或合同金额不能证明应收数据不存在或可用。")
    return {"status": "validated" if expected and monetary_columns else "context_only",
            "method": "authorized_metadata_ast", "requestedCategories": sorted(expected),
            "actualCategories": sorted(selected_categories), "verifiedJoinConditions": checked_joins,
            "monetaryFields": monetary_columns}
