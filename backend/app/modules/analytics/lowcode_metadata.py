"""将已授权低代码字段定义和选项字典同步进元数据快照，不在查询时猜测编码。"""
from __future__ import annotations

from copy import deepcopy
import json

from sqlalchemy import text

from app.modules.analytics.connections import readonly_connection


def _object(value):
    """字段展示配置允许数据库 JSON 或 JSON 文本，损坏内容不作为字典依据。"""
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def _flag(value):
    """低代码标志允许数据库整数及文本 0/1，不把字符串 0 当作真。"""
    return value is True or value == 1 or value == "1"


def _option_source(field, entity):
    """只有完整、合法的同步配置才可选择其他实体字典。"""
    view = _object(field.get("fieldViewModel"))
    if view is None:
        return None
    sync = view.get("optionSyncModel")
    if sync in (None, {}):
        key = (entity.get("name"), field.get("name"))
    elif isinstance(sync, dict):
        key = (sync.get("entityName"), sync.get("fieldName"))
    else:
        return None
    return key if all(isinstance(value, str) and value.strip() for value in key) else None


def merge_lowcode_metadata(tables: list[dict], entities: list[dict], fields: list[dict], options: list[dict]) -> list[dict]:
    """按真实字段定义附加业务主键、名称字段和无歧义枚举，不改原始 SQL 类型。"""
    result = deepcopy(tables)
    by_table = {row["tableName"]: row for row in result}
    by_code, ambiguous_codes = {}, set()
    for row in entities:
        if row.get("physicalName") not in by_table or row.get("entityCode") is None:
            continue
        code = str(row["entityCode"])
        if code in by_code and (by_code[code].get("physicalName"), by_code[code].get("name")) != (row.get("physicalName"), row.get("name")):
            ambiguous_codes.add(code)
        by_code[code] = row
    for code in ambiguous_codes:
        by_code.pop(code, None)
    grouped, invalid_options, invalid_values = {}, set(), set()
    for row in options:
        key = (row.get("entityName"), row.get("fieldName"))
        value, label = row.get("value"), row.get("label")
        if value is None or isinstance(value, bool) or not isinstance(value, (str, int)) or not isinstance(label, str) or not label.strip():
            invalid_options.add(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                invalid_values.add((key, str(value)))
            continue
        grouped.setdefault(key, {}).setdefault(str(value), set()).add(label)
    for field in fields:
        entity = by_code.get(str(field.get("entityCode")))
        if not entity:
            continue
        table = by_table[entity["physicalName"]]
        column = next((c for c in table["columns"] if c["columnName"] == field.get("physicalName")), None)
        if column is None:
            continue
        column["businessKey"] = _flag(field.get("idFieldFlag"))
        column["nameField"] = _flag(field.get("nameFieldFlag"))
        column["semanticType"] = field.get("type") or ""
        if field.get("type") != "Option":
            continue
        key = _option_source(field, entity)
        values = grouped.get(key) or {}
        column["enumValues"] = [{"value": code, "label": next(iter(labels))}
                                for code, labels in sorted(values.items()) if len(labels) == 1 and (key, code) not in invalid_values]
        column["enumSource"] = {"table": "t_option_item", "entityName": key[0], "fieldName": key[1]} if key else {}
        column["enumComplete"] = bool(values) and key not in invalid_options and all(len(labels) == 1 for labels in values.values())
    return result


def enrich_lowcode_metadata(db, source, tables: list[dict]) -> list[dict]:
    """仅同步时读取已授权元表；选项表不可见时仍可识别已授权业务主键。"""
    required = {
        "t_meta_entity": {"entityCode", "name", "physicalName"},
        "t_meta_field": {"entityCode", "name", "physicalName", "type", "idFieldFlag", "nameFieldFlag"},
    }
    by_table = {row["tableName"]: row for row in tables}
    if source.provider != "mysql" or any(name not in by_table or not columns.issubset(
            {c["columnName"] for c in by_table[name]["columns"]}) for name, columns in required.items()):
        return tables
    field_columns = {c["columnName"] for c in by_table["t_meta_field"]["columns"]}
    option_columns = {c["columnName"] for c in by_table.get("t_option_item", {}).get("columns", [])}
    can_read_options = {"entityName", "fieldName", "value", "label"}.issubset(option_columns) and "fieldViewModel" in field_columns
    names = [name for name in by_table if not name.startswith("t_meta_")][:2000]
    if not names:
        return tables
    with readonly_connection(db, source) as connection:
        parameters = {f"name{i}": name for i, name in enumerate(names)}
        placeholders = ",".join(":" + key for key in parameters)
        entities = list(connection.execute(text("SELECT entityCode,name,physicalName FROM t_meta_entity "
            f"WHERE physicalName IN ({placeholders}) LIMIT 2001"), parameters).mappings())
        if not entities or len(entities) > 2000:
            return tables
        parameters = {f"code{i}": row["entityCode"] for i, row in enumerate(entities)}
        placeholders = ",".join(":" + key for key in parameters)
        view_column = "fieldViewModel" if "fieldViewModel" in field_columns else "NULL AS fieldViewModel"
        fields = list(connection.execute(text("SELECT entityCode,name,physicalName,type,idFieldFlag,nameFieldFlag," + view_column + " "
            f"FROM t_meta_field WHERE entityCode IN ({placeholders}) AND (type='Option' OR idFieldFlag=1 OR nameFieldFlag=1) LIMIT 20001"), parameters).mappings())
        if len(fields) > 20000:
            # 元数据不完整时不把部分字典标成完整；原始结构仍可使用。
            return tables
        by_code = {str(row["entityCode"]): row for row in entities}
        # 隐藏的业务字段不能扩展字典读取范围，即使元表中仍能看到它的声明。
        authorized_columns = {name: {column["columnName"] for column in table["columns"]} for name, table in by_table.items()}
        fields = [field for field in fields if str(field["entityCode"]) in by_code and field["physicalName"] in
                  authorized_columns.get(by_code[str(field["entityCode"])]["physicalName"], set())]
        keys = set()
        for field in fields:
            if field["type"] == "Option" and can_read_options:
                key = _option_source(field, by_code[str(field["entityCode"])])
                if key:
                    keys.add(key)
        options = []
        if keys:
            parameters, conditions = {}, []
            for index, (entity, field) in enumerate(sorted(keys)):
                parameters.update({f"entity{index}": entity, f"field{index}": field})
                conditions.append(f"(entityName=:entity{index} AND fieldName=:field{index})")
            options = list(connection.execute(text("SELECT entityName,fieldName,value,label FROM t_option_item WHERE "
                + " OR ".join(conditions) + " LIMIT 20001"), parameters).mappings())
            if len(options) > 20000:
                return tables
    return merge_lowcode_metadata(tables, entities, fields, options)
