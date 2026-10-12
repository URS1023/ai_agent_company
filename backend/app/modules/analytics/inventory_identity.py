"""库存页物料身份的纯统计合同，查询结果与最终短答使用相同口径。"""
from __future__ import annotations


def material_identity_columns(tables: list[dict], stock: str, material: str,
                              *, primary_key_complete: bool = True) -> list[tuple[str, str]]:
    """仅以授权快照中的完整物理外键或已核验业务键关系确认实体身份。"""
    table_map = {table["tableName"]: table for table in tables}
    source, target = table_map.get(stock, {}), table_map.get(material, {})
    source_columns = {column["columnName"] for column in source.get("columns", [])}
    target_columns = {column["columnName"] for column in target.get("columns", [])}
    primary = {column["columnName"] for column in target.get("columns", []) if column.get("primaryKey")}
    candidates = []
    for relation in source.get("foreignKeys") or []:
        left, right = relation.get("columns") or [], relation.get("referredColumns") or []
        if (primary_key_complete and primary and relation.get("referredTable") == material
                and left and len(left) == len(right)
                and len(set(left)) == len(left) and len(set(right)) == len(right)
                and set(left).issubset(source_columns) and set(right).issubset(target_columns)
                and primary.issubset(right) and ("c_productId", "sparePartsInforId") in zip(left, right)):
            # 外键含额外核对字段时，已声明完整主键仍是实体身份，不按属性版本拆成多个物料。
            candidates.append(tuple(sorted((own, other) for own, other in zip(left, right)
                                           if not primary or other in primary)))
    # 同一物料字段有多套不同声明时不替业务选择实体口径。
    candidates = set(candidates)
    if len(candidates) == 1:
        return list(next(iter(candidates)))
    if candidates:
        return []
    for relation in source.get("declaredRelations") or []:
        evidence = relation.get("joinKeyEvidence") or {}
        if (relation.get("relationType") == "reference" and not relation.get("relationDirection")
                and relation.get("sourceTable") == stock
                and relation.get("targetTable") == material and relation.get("joinKeysKnown") is True
                and relation.get("sourceField") == "c_productId" and relation.get("targetField") == "sparePartsInforId"
                and evidence.get("identityTable") == material and evidence.get("identityField") == "sparePartsInforId"
                and evidence.get("referenceTable") == stock and evidence.get("referenceField") == "c_productId"
                and evidence.get("identityEvidence") in {"lowcode_primary_key", "declared_key_comment"}
                and "c_productId" in source_columns and "sparePartsInforId" in target_columns):
            return [("c_productId", "sparePartsInforId")]
    return []


def inventory_material_counts(rows: list[dict], identity: dict | None) -> dict:
    """空值、空白标识和缺少完整授权键均为未知；数值零保留为合法身份。"""
    identity = identity or {}
    columns = identity.get("columns") if identity.get("status") == "available" else []
    known, unknown = set(), 0
    for row in rows:
        values = tuple(row.get(column) for column in columns) if columns else ()
        if not values or any(value is None or isinstance(value, str) and not value.strip() for value in values):
            unknown += 1
        else:
            known.add(values)
    return {
        "materialCount": len(known) if unknown == 0 else None,
        "materialCountKnown": unknown == 0,
        "knownMaterialCount": len(known),
        "unknownMaterialRows": unknown,
        "materialCountScope": "本页按完整已授权物料引用身份去重，保留孤立或已删除引用；"
                              "不是物料目录有效记录数，存在未知身份时仅报告已知范围。",
    }
