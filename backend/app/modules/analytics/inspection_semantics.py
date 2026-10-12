"""执行前核验已知设备业务的删除标记和巡检统计口径。"""
import re
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from app.modules.analytics.sql_safety import SqlFieldValidationError


_DEVICE_TABLE_COLUMNS = {
    "t_devicemanagement": {"devicemanagementid", "isdeleted"},
    "t_inspectiondevice": {"c_device", "mddinspectionmaintenanceplanid", "isdeleted"},
    "t_dinspectionmaintenanceplan": {"dinspectionmaintenanceplanid", "isdeleted"},
    "t_deviceinspectionmaintenance": {"deviceinspectionmaintenanceid", "isdeleted"},
    "t_xunjianjihuarunhuabaoyang": {"xunjianjihuarunhuabaoyangid", "isdeleted"},
}


def _requests_active_records(question):
    """只约束明确的有效记录请求，显式要求查看空标记或已删记录时尊重原意。"""
    text = str(question)
    if not re.search(r"未(?:被|逻辑|软)?删除|未删|没有删除|有效(?:的)?(?:设备|记录|数据|巡检|保养|计划)", text):
        return False
    # 排除类说明不能反过来被识别为“需要已删或 NULL”；其他字段空值也不改变删除口径。
    scope_text = re.sub(r"(?:排除|不含|不包含|不包括|不要|不查询|不查看)[^，。；;]{0,12}(?:已删除|已删|NULL|空值)", "", text, flags=re.I)
    if re.search(r"已删除|已删", scope_text):
        return False
    if re.search(r"(?:isDeleted|删除(?:标记|状态|字段)).{0,10}(?:为|是|等于|=|IS)\s*(?:NULL|空值|空)|"
                 r"(?:包括|包含|计入|允许|也要).{0,10}(?:NULL|空值).{0,10}(?:删除标记|删除状态|isDeleted)|"
                 r"(?:删除标记|删除状态|isDeleted).{0,10}(?:包括|包含|计入|允许|也要).{0,10}(?:NULL|空值)|"
                 r"(?:NULL|空值).{0,10}(?:视为|当作|算作|也算).{0,5}未删除", scope_text, re.I):
        return False
    return True


def _guarantees_active(predicate, alias, *, unqualified=False):
    """保守验证每个 OR 分支都受严格等零条件约束，不能只搜索是否出现过等号。"""
    if isinstance(predicate, (exp.Where, exp.Paren)):
        return _guarantees_active(predicate.this, alias, unqualified=unqualified)
    if isinstance(predicate, exp.And):
        return any(_guarantees_active(item, alias, unqualified=unqualified) for item in (predicate.this, predicate.expression))
    if isinstance(predicate, exp.Or):
        return all(_guarantees_active(item, alias, unqualified=unqualified) for item in (predicate.this, predicate.expression))
    if not isinstance(predicate, exp.EQ):
        return False
    for column, value in ((predicate.this, predicate.expression), (predicate.expression, predicate.this)):
        if (isinstance(column, exp.Column) and column.name.lower() == "isdeleted"
                and (column.table.lower() == alias or (unqualified and not column.table))
                and isinstance(value, exp.Literal) and value.this == "0"):
            return True
    return False


def validate_device_active_records(safe_query, question, tables):
    """仅对已知表名与字段指纹启用规则；不改写 SQL，不把未知外部源套进设备业务口径。"""
    if not _requests_active_records(question):
        return
    known = set()
    for table in tables:
        name = str(table.get("tableName", "")).lower()
        columns = {str(column.get("columnName", "")).lower() for column in table.get("columns", [])}
        if name in _DEVICE_TABLE_COLUMNS and _DEVICE_TABLE_COLUMNS[name].issubset(columns):
            known.add(name)
    if not known:
        return
    for scope in traverse_scope(safe_query.tree):
        statement = scope.expression
        if not isinstance(statement, exp.Select):
            continue
        predicates = [statement.args.get("where")]
        # 内连接的 ON 同样约束结果行；外连接 ON 不能约束保留侧，须按当前别名判断。
        predicates.extend(join.args.get("on") for join in statement.args.get("joins", [])
                          if not join.side and join.kind in {"", "INNER"})
        for alias, (_node, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table) or source.name.lower() not in known:
                continue
            # LEFT JOIN 右侧只有匹配行需要满足 ON；未匹配时的空占位不代表真实的未知删除记录。
            # 同一 ON 中的左侧条件不能过滤保留行。RIGHT/FULL 的 ON 暂不作为有效记录证明。
            applicable = predicates + [join.args.get("on") for join in statement.args.get("joins", [])
                                       if join.side == "LEFT" and join.kind in {"", "OUTER"}
                                       and join.this.alias_or_name.lower() == alias.lower()]
            if not any(_guarantees_active(predicate, alias.lower(), unqualified=len(scope.selected_sources) == 1)
                       for predicate in applicable):
                raise SqlFieldValidationError(
                    f"已知设备业务表 {source.name} 的未删除/有效记录口径必须用 {alias}.isDeleted = 0 严格筛选。"
                    "NULL 表示删除标记未知，不能自动视为未删除；不得使用 OR isDeleted IS NULL 或 COALESCE/IFNULL(isDeleted,0) 放宽。"
                    "保持其他设备、时间和关联范围；数据库尚未执行。"
                )


def validate_inspection_aggregation(safe_query, question):
    if not re.search('巡检', str(question)):
        return
    tree = safe_query.tree
    tables = {table.name.lower() for table in tree.find_all(exp.Table)}
    if not tables.intersection({'t_inspectiondevice','t_deviceinspectionmaintenance'}):
        return
    for count in tree.find_all(exp.Count):
        if isinstance(count.this, exp.Distinct) and count.find(exp.Coalesce):
            raise SqlFieldValidationError('巡检设备去重计数不能把未关联的 NULL 替换为空字符串；COUNT(DISTINCT 真实设备标识) 会自然排除无完成记录的设备。')
    # 已审阅设备库中此字段才是实际完成标志；c_recordType 是周期，非空不等于完成巡检。
    aliases = [alias for alias in tree.find_all(exp.Alias) if re.search(r'已巡检|完成巡检',alias.alias)]
    if aliases and 't_deviceinspectionmaintenance' in tables:
        comparisons = list(tree.find_all(exp.EQ)) + list(tree.find_all(exp.In))
        if not any(any(c.name.lower() == 'c_inspectionstatus' for c in comparison.find_all(exp.Column)) for comparison in comparisons):
            raise SqlFieldValidationError('已巡检设备必须依据 c_inspectionStatus 完成状态筛选；c_recordType 表示周期，非空不代表巡检完成。保持用户时间、计划和有效记录范围。')
