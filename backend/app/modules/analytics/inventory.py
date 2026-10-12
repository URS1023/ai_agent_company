"""库存业务的确定性适配器：授权结构、单次查询与可复用的持久结果。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import time

from sqlalchemy import select

from app.common.authorization import require_permission
from app.errors import ValidationError
from app.modules.analytics.connections import source_version
from app.modules.analytics.inventory_identity import inventory_material_counts, material_identity_columns
from app.modules.analytics.models import QueryHistory, SourceMetadata, SourceSemanticDocument
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import InventoryQueryInput, QueryInput, QueryResult
from app.modules.analytics.sources import get_source


# 这些名称来自已核对的业务实体，不使用词义相似或同名字段推断关系。
STOCK = "t_spinventory"
MATERIAL = "t_sparepartsinfor"
WAREHOUSE = "t_basicinformationofthewarehouse"
LOCATION = "t_warehouselocationinformation"
IDENTIFIERS = {
    STOCK: "spInventoryId", MATERIAL: "sparePartsInforId",
    WAREHOUSE: "basicInformationoftheWarehouseId",
    LOCATION: "warehouseLocationInformationId",
}
MAX_UNIT_PROJECTION_OPTIONS = 100


def _declared_relations(ctx, source, tables: list[dict]) -> list[dict]:
    """只读当前版本的关系声明；不为一次库存查询遍历外部低代码元数据。"""
    relations = []
    for table in tables:
        for foreign_key in table.get("foreignKeys") or []:
            columns = foreign_key.get("columns") or []
            targets = foreign_key.get("referredColumns") or []
            if columns and len(columns) == len(targets):
                relations.append({"sourceTable": table["tableName"], "columns": columns,
                                  "targetTable": foreign_key.get("referredTable"), "targetColumns": targets,
                                  "relationType": "foreign_key"})
        relations.extend(table.get("declaredRelations") or [])
    documents = ctx.db.scalars(select(SourceSemanticDocument).where(
        SourceSemanticDocument.workspace_id == ctx.workspace_id,
        SourceSemanticDocument.datasource_id == source.id,
        SourceSemanticDocument.source_version == source_version(source),
        SourceSemanticDocument.table_name.in_([STOCK, MATERIAL, WAREHOUSE, LOCATION]),
    )).all()
    for document in documents:
        relations.extend(document.relations_json or [])
    return relations


def _unit_mappings(kind: str) -> list[tuple[str, str]]:
    """确定不同目录的授权单位字段及输出名称。"""
    return [(STOCK, "库存计量单位"), (MATERIAL, "物料计量单位")] if kind == "inventory" else (
        [(MATERIAL, "计量单位")] if kind == "materials" else [])


def _unit_labels(tables: list[dict], table_name: str) -> dict[str, str]:
    """只解释授权字典中唯一的单位编码，重复且冲突的标签保持未知。"""
    table = next((item for item in tables if item.get("tableName") == table_name), {})
    column = next((item for item in table.get("columns", []) if item.get("columnName") == "c_unit"), {})
    labels, ambiguous = {}, set()
    for option in column.get("enumValues") or []:
        if not isinstance(option, dict) or option.get("value") is None or not option.get("label"):
            continue
        key, label = str(option["value"]), str(option["label"])
        if key in labels and labels[key] != label:
            ambiguous.add(key)
        labels[key] = label
    return {key: value for key, value in labels.items() if key not in ambiguous}


def build_inventory_query(payload: InventoryQueryInput, tables: list[dict], relations: list[dict],
                          *, provider: str, material_primary_key_complete: bool = True) -> tuple[str, dict, dict]:
    """把有类型的业务筛选编译成参数化 SQL；缺少字段不擅自扩大查询范围。"""
    if provider not in {"mysql", "postgres", "sqlite"}:
        raise ValidationError("当前库存适配器需要关系型业务数据源")
    catalog = {table["tableName"]: {column["columnName"] for column in table.get("columns", [])}
               for table in tables}
    quote = (lambda value: '`' + value.replace('`', '``') + '`') if provider == "mysql" else (
        lambda value: '"' + value.replace('"', '""') + '"')
    base = {"inventory": STOCK, "materials": MATERIAL, "warehouses": WAREHOUSE}[payload.kind]
    required = {IDENTIFIERS[base], "isDeleted"}
    if not required.issubset(catalog.get(base, set())):
        raise ValidationError("授权目录缺少该业务查询所需的标识或有效状态字段", business=payload.kind)
    aliases = {base: "b"}
    joins, missing, evidence = [], [], []
    material_identity = None

    def ref(table: str, column: str, *, required: bool = False) -> str:
        """未授权或未关联字段以 NULL 表示，并把原因放进覆盖信息。"""
        if table in aliases and column in catalog.get(table, set()):
            return f"{aliases[table]}.{quote(column)}"
        if required:
            raise ValidationError("授权目录不足以应用该筛选条件", field=column)
        missing.append(f"{table}.{column}")
        return "NULL"

    def join_table(source_table: str, source_column: str, target: str, alias: str) -> None:
        """只接受向前的真实外键或已同步的单值 reference 声明。"""
        target_id = IDENTIFIERS[target]
        declaration, join_pairs = None, []
        for item in relations:
            if (item.get("sourceTable") != source_table or item.get("targetTable") != target
                    or item.get("relationDirection") or item.get("relationType") not in {"foreign_key", "reference"}):
                continue
            source_fields = item.get("columns") or [item.get("sourceField")]
            target_fields = item.get("targetColumns") or [item.get("targetField") or target_id]
            pairs = list(zip(source_fields, target_fields))
            if len(source_fields) != len(target_fields) or (source_column, target_id) not in pairs:
                continue
            # 完整物理外键优先；旧语义缓存曾把复合外键拆开，不能让残留项缩小连接条件。
            if declaration is None or len(pairs) > len(join_pairs):
                declaration, join_pairs = item, pairs
        available = (source_table in aliases and "isDeleted" in catalog.get(target, set())
                     and all(source_field in catalog.get(source_table, set())
                             and target_field in catalog.get(target, set()) for source_field, target_field in join_pairs))
        if not declaration or not available:
            evidence.append({"sourceTable": source_table, "sourceField": source_column, "targetTable": target,
                             "status": "unavailable", "reason": "缺少已声明关系或授权关联字段"})
            return
        aliases[target] = alias
        conditions = [f"{ref(source_table, source_field)} = {ref(target, target_field)}"
                      for source_field, target_field in join_pairs]
        conditions.append(f"{ref(target, 'isDeleted')} = 0")
        joins.append(f"LEFT JOIN {quote(target)} {alias} ON " + " AND ".join(conditions))
        evidence.append({**declaration, "resolvedTargetField": target_id, "status": "joined"})

    if payload.kind == "inventory":
        join_table(STOCK, "c_productId", MATERIAL, "m")
        join_table(STOCK, "c_warehouseId", WAREHOUSE, "w")
        join_table(STOCK, "c_locationId", LOCATION, "l")
        projection = [
            ("库存记录标识", ref(STOCK, IDENTIFIERS[STOCK])), ("物料标识", ref(STOCK, "c_productId")),
            ("备件名称", ref(MATERIAL, "c_productName")), ("备件编码", ref(MATERIAL, "c_productcode")),
            ("库存规格型号", ref(STOCK, "c_productModel")), ("物料规格型号", ref(MATERIAL, "c_productModel")),
            ("实际可用库存", ref(STOCK, "c_availableStock")), ("库存计量单位编码", ref(STOCK, "c_unit")),
            ("物料计量单位编码", ref(MATERIAL, "c_unit")), ("安全库存", ref(MATERIAL, "c_safetyStock")),
            ("仓库标识", ref(STOCK, "c_warehouseId")), ("仓库名称", ref(WAREHOUSE, "c_warehouseName")),
            ("仓位标识", ref(STOCK, "c_locationId")), ("仓位序号", ref(LOCATION, "c_locationSeqNo")),
            ("仓位编码", ref(LOCATION, "c_locationCode")), ("批次号", ref(STOCK, "c_batchNo")),
        ]
        identity_pairs = material_identity_columns(tables, STOCK, MATERIAL,
                                                  primary_key_complete=material_primary_key_complete)
        identity_columns, identity_components = [], []
        for index, (source_field, target_field) in enumerate(identity_pairs, start=1):
            label = "物料标识" if source_field == "c_productId" else f"物料身份分量{index}"
            if source_field != "c_productId":
                projection.append((label, ref(STOCK, source_field, required=True)))
            identity_columns.append(label)
            identity_components.append({"resultColumn": label, "sourceField": source_field, "targetField": target_field})
        material_identity = {
            "status": "available" if identity_pairs else "unavailable", "columns": identity_columns,
            "sourceTable": STOCK, "targetTable": MATERIAL, "components": identity_components,
            "scope": "仅采用授权快照中完整声明的物料引用身份，名称不参与去重；关联失效不删除库存记录。",
        }
        # 物料级阈值不能与单批次库存直接比较；保留阈值与原始空值供统一业务规则核对。
        quantity, threshold = ref(STOCK, "c_availableStock"), ref(MATERIAL, "c_safetyStock")
        projection.append(("库存预警", f"CASE WHEN {quantity} IS NULL OR {threshold} IS NULL "
                           "THEN '未知' ELSE '待核对' END"))
        filter_columns = {"warehouse_id": (STOCK, "c_warehouseId"), "material_id": (STOCK, "c_productId"),
                          "batch_number": (STOCK, "c_batchNo"), "name": (MATERIAL, "c_productName")}
    elif payload.kind == "materials":
        projection = [("物料标识", ref(MATERIAL, IDENTIFIERS[MATERIAL])),
                      ("备件名称", ref(MATERIAL, "c_productName")), ("备件编码", ref(MATERIAL, "c_productcode")),
                      ("规格型号", ref(MATERIAL, "c_productModel")), ("计量单位编码", ref(MATERIAL, "c_unit")),
                      ("安全库存", ref(MATERIAL, "c_safetyStock")),
                      ("默认仓库标识", ref(MATERIAL, "c_basicInformationoftheWarehouse"))]
        filter_columns = {"material_id": (MATERIAL, IDENTIFIERS[MATERIAL]), "name": (MATERIAL, "c_productName")}
    else:
        projection = [("仓库标识", ref(WAREHOUSE, IDENTIFIERS[WAREHOUSE])),
                      ("仓库名称", ref(WAREHOUSE, "c_warehouseName")), ("仓库编码", ref(WAREHOUSE, "c_warehouseCode")),
                      ("仓库地址", ref(WAREHOUSE, "c_warehouseAddress")), ("仓库状态编码", ref(WAREHOUSE, "c_warehouseStatus"))]
        filter_columns = {"warehouse_id": (WAREHOUSE, IDENTIFIERS[WAREHOUSE]), "name": (WAREHOUSE, "c_warehouseName")}

    where, parameters = [f"{ref(base, 'isDeleted')} = 0"], {}
    for key, columns in filter_columns.items():
        value = getattr(payload, key)
        if not value:
            continue
        expression = ref(*columns, required=True)
        if key == "name":
            parameters[key] = "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
            where.append(f"{expression} LIKE :{key} ESCAPE '!'")
        else:
            parameters[key] = value
            where.append(f"{expression} = :{key}")
    # 单位标签也属于真实 SQL 投影，历史和导出可沿原 SQL 验证字段范围，无须放宽快照权限。
    for unit_index, (table_name, label_name) in enumerate(_unit_mappings(payload.kind)):
        labels = _unit_labels(tables, table_name)
        expression = "NULL"
        # 不截取字典前缀造成同类编码部分可信；大字典全部保持未知并在覆盖证据中说明。
        if 0 < len(labels) <= MAX_UNIT_PROJECTION_OPTIONS:
            unit_code = ref(table_name, "c_unit")
            # 字节比较保留 Python 字典的精确语义，不让数据库排序规则合并大小写或尾空格。
            def exact_text(expression: str) -> str:
                if provider == "mysql":
                    return f"CAST({expression} AS BINARY)"
                if provider == "postgres":
                    parameters.update(unit_escape_from="\\", unit_escape_to="\\\\")
                    return (f"CAST(REPLACE(CAST({expression} AS TEXT), :unit_escape_from, "
                            ":unit_escape_to) AS BYTEA)")
                return f"CAST(CAST({expression} AS TEXT) AS BLOB)"

            comparable_code = exact_text(unit_code)
            cases = []
            for option_index, (code, label) in enumerate(labels.items()):
                code_key, label_key = f"unit_{unit_index}_{option_index}_code", f"unit_{unit_index}_{option_index}_label"
                parameters.update({code_key: code, label_key: label})
                comparable_parameter = exact_text(f":{code_key}")
                cases.append(f"WHEN {comparable_parameter} THEN :{label_key}")
            expression = f"CASE {comparable_code} " + " ".join(cases) + " ELSE NULL END"
        projection.append((label_name, expression))
    sql = "SELECT " + ", ".join(f"{expression} AS {quote(label)}" for label, expression in projection)
    sql += f" FROM {quote(base)} b " + " ".join(joins) + " WHERE " + " AND ".join(where)
    # 多取一行的上限仍交给统一查询管线执行，避免恰好满页时误报已经取完。
    sql += f" ORDER BY {ref(base, IDENTIFIERS[base])} LIMIT {payload.page_size + 1} OFFSET {payload.offset}"
    coverage = {"adapter": "spare_parts_inventory_v1", "kind": payload.kind,
                "relations": evidence, "unavailableFields": sorted(set(missing))}
    if material_identity is not None:
        coverage["materialIdentity"] = material_identity
    return sql, parameters, coverage


def query_inventory(ctx, payload: InventoryQueryInput) -> QueryResult:
    """业务接口、聊天工具、导出共同使用一份经过授权的库存结果。"""
    from app.modules.runtime.task_control import checkpoint
    checkpoint()
    started = time.monotonic()
    require_permission(ctx, "analytics.execute")
    source = get_source(ctx, payload.datasource_id, active=True)
    snapshot = ctx.db.scalar(select(SourceMetadata).where(SourceMetadata.workspace_id == ctx.workspace_id,
        SourceMetadata.datasource_id == source.id, SourceMetadata.source_version == source_version(source),
        SourceMetadata.status == "ready"))
    if snapshot is None:
        raise ValidationError("当前库存来源缺少有效的已同步结构，请先在数据源管理中同步元数据")
    tables = snapshot.tables_json
    # 快照只保留可见列的主键标记，目标缩列后无法证明复合主键是否完整；不能从剩余标记猜完整身份。
    material_primary_key_complete = MATERIAL not in ((source.connection_config or {}).get("allowedColumns") or {})
    sql, parameters, coverage = build_inventory_query(
        payload, tables, _declared_relations(ctx, source, tables), provider=source.provider,
        material_primary_key_complete=material_primary_key_complete,
    )
    result = execute_query(ctx, QueryInput(datasourceId=source.id, query=sql, mode="sql",
        parameters=parameters, maxRows=payload.page_size, needAnalysis=False, needChart=False))
    _resolve_units(result, payload.kind, tables, coverage)
    # 查询时刻与历史业务期间分开记录，ISO 偏移避免夜间任务把 UTC 日期说成本地昨天。
    captured_at = datetime.now(timezone.utc).astimezone()
    coverage["capturedAt"] = captured_at.isoformat()
    coverage["timeZoneOffset"] = captured_at.strftime("%z")
    result.statistics = {
        "scope": "returned_page", "rowCount": len(result.data), "offset": payload.offset,
        "pageSize": min(payload.page_size, int((source.connection_config or {}).get("maxRows") or 1000)),
        "hasMore": result.truncated, "nextOffset": payload.offset + len(result.data) if result.truncated else None,
        "totalCountKnown": False, "quantityTotal": None,
        "quantityPolicy": "保留物料、单位、仓库与批次明细，不把不同物料或不同单位相加",
        "missingValues": {column.columnName: sum(row.get(column.columnName) is None for row in result.data)
                          for column in result.columns},
    }
    if payload.kind == "inventory":
        result.statistics.update(inventory_material_counts(result.data, coverage.get("materialIdentity")))
        result.statistics["alertStatusCounts"] = dict(Counter(row.get("库存预警") for row in result.data))
        result.statistics["unitConsistency"] = dict(Counter(
            "未知" if row.get("库存计量单位") is None or row.get("物料计量单位") is None else (
                "一致" if row["库存计量单位"] == row["物料计量单位"] else "不一致") for row in result.data))
        result.statistics["unitConsistencyScope"] = (
            "逐条比较同一库存记录的库存计量单位与关联物料计量单位；"
            "一致不代表所有记录使用相同单位，不能据此跨记录合计库存数量。"
        )
        # 分别列出字段的单位分布，以记录条数计数；未知单位保留 NULL，不并入任何已知单位。
        result.statistics["unitCounts"] = {
            field: [{"unit": unit, "rowCount": count} for unit, count in
                    Counter(row.get(field) for row in result.data).items()]
            for field in ("库存计量单位", "物料计量单位")
        }
        result.statistics["unitCountsScope"] = "本页按单位分组的记录条数，不是库存数量合计；unit=null 表示单位未知。"
    result.retrievalEvidence = coverage
    result.analysis = (
        "当前业务数据的本页快照；返回条数不代表全库数量，不表示历史期间。"
        "缺失字段与原始 NULL 不补零，单位名称仅来自授权元数据选项，编码保留原值；不同物料、单位不能合计。"
        "名称为空时以 ID 追溯；未授权或无声明的关联不会被猜测。"
    )
    if payload.kind == "inventory":
        result.analysis += (
            "单位一致性仅逐条核对库存字段与物料字段，不代表所有记录单位相同；"
            "不同记录的单位分布见 unitCounts，不同物料或不同单位的库存数量不可相加。"
        )
        result.analysis += "库存预警：数量或安全库存缺失为未知；阈值存在仍需核对物料级比较范围与计量单位，不能逐批次直接判正常。"
        result.analysis += result.statistics["materialCountScope"]
    # 保存相同的范围说明，后续表格/PPT复用 queryId 不需要重新查询或让模型抄写数字。
    record = ctx.db.get(QueryHistory, result.queryId)
    result.executionTime = int((time.monotonic() - started) * 1000)
    record.execution_ms = result.executionTime
    record.result_json = result.model_dump(mode="json")
    ctx.db.commit()
    return result


def _resolve_units(result: QueryResult, kind: str, tables: list[dict], coverage: dict) -> None:
    """记录同次 SQL 的授权字典覆盖，不追加无法通过原 SQL 验证的输出列。"""
    unit_evidence = []
    for table_name, label_name in _unit_mappings(kind):
        labels = _unit_labels(tables, table_name)
        for column in result.columns:
            if column.columnName == label_name:
                column.dataType = "TEXT"
                column.comment = "授权元数据中的计量单位名称"
        over_budget = len(labels) > MAX_UNIT_PROJECTION_OPTIONS
        unit_evidence.append({"tableName": table_name, "columnName": "c_unit", "optionCount": len(labels),
                              "projectionStatus": "unavailable" if over_budget else "available",
                              "reason": "单位字典超过单字段100项投影预算，标签全部保持未知" if over_budget else "",
                              "unresolvedRows": sum(row[label_name] is None for row in result.data)})
    coverage["unitDictionaries"] = unit_evidence
