"""Cloud Brain metadata indexing, hybrid table recall and safe samples.

The index is built only from an already-authorized source snapshot.  It uses
database comments as the primary business dictionary, optional embeddings as
an additional recall channel, and only real foreign-key/low-code metadata for
relationship expansion.  It never persists raw business rows.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
import json
import math
import re
from typing import Iterable

from sqlalchemy import delete, select

from app.modules.analytics.connections import database_name, readonly_connection, source_version
from app.modules.analytics.models import SourceSemanticDocument
from app.providers import model
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, checkpoint

# 向量文档限制字段数；完整授权列仍参与词法检索和 SQL 授权。
MAX_DOCUMENT_COLUMNS = 40
# 向量模型每次请求的文本条数；过大既慢又容易触发服务端限制。
EMBEDDING_BATCH_SIZE = 32
DEFAULT_SEED_TABLES = 8
DEFAULT_RELATION_EXPANSION = 4
# 抽样是给模型“理解字段口径”用的，因此样本量被严格限制，且只附着在提示副本上。
MAX_SAMPLE_TABLES = 6
MAX_SAMPLE_COLUMNS = 10
MAX_SAMPLE_ROWS = 3

# 系统/框架表前缀：召回时默认排除，避免低代码框架表挤掉真实业务表。
# 但用户可以显式点名某张表（见 explicitly_requested_tables），此时不排除。
SYSTEM_TABLE_PREFIXES = (
    "act_", "flw_", "qrtz_", "sys_", "system_", "audit_", "t_meta_",
    "information_schema", "mysql.", "pg_", "sqlite_",
)
# 敏感字段标记（中英文）：命中即不参与抽样，避免把密码、证件号、联系方式等
# 真实业务值送进模型提示或返回前端。判断基于字段名 + 字段注释。
SENSITIVE_MARKERS = (
    "password", "passwd", "pwd", "secret", "token", "apikey", "api_key",
    "credential", "authorization", "cookie", "private_key", "access_key",
    "idcard", "id_card", "identity_no", "bank_account", "card_no",
    "mobile", "phone", "email", "address", "c_mima",
    "密码", "密钥", "令牌", "身份证", "银行卡", "手机号", "电话", "邮箱", "地址",
)
# 低代码元数据里的系统审计/组织字段：这些字段虽然带关系语义，但会污染业务关联
# （例如 createdBy 指向用户表），因此明确排除在关系扩展之外。
SYSTEM_RELATION_FIELDS = {
    "createdby", "modifiedby", "owneruser", "ownerdepartment",
    "departmentid", "parentdepartmentid", "roles", "lastapprovedby",
}

DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "财务": (
        "财务", "资金", "收款", "付款", "回款", "应收", "应付", "发票", "成本",
        "利润", "预算", "现金流", "finance", "financial", "payment", "receivable",
        "payable", "invoice", "cost", "profit", "cashflow",
    ),
    "销售": (
        "销售", "订单", "客户", "合同", "收入", "出库", "商品", "产品", "区域",
        "sales", "sale", "order", "customer", "contract", "revenue", "shipment",
    ),
    "设备": (
        "设备", "预警", "告警", "故障", "维修", "保养", "巡检", "运维", "传感器",
        "device", "equipment", "alert", "warning", "fault", "maintenance", "inspection",
    ),
    "质量": (
        "质量", "质检", "检验", "缺陷", "成分", "指标", "合格", "不合格",
        "quality", "inspection", "defect", "metric", "composition",
    ),
    "生产": (
        "生产", "制造", "工单", "工序", "车间", "产量", "物料", "库存",
        "production", "manufacturing", "workorder", "material", "inventory",
    ),
    "项目": ("项目", "任务", "里程碑", "project", "task", "milestone"),
}


def is_business_table(table_name: str) -> bool:
    """判断是否业务表：排除系统前缀、隐藏表（以 ``.``/``_`` 开头）与空名。

    这是统计口径（业务表数、注释覆盖率）与召回范围的共同基准，改这里会同时影响
    界面上的数据源画像。
    """
    name = str(table_name or "").strip().lower()
    return bool(name) and not name.startswith((".", "_", *SYSTEM_TABLE_PREFIXES))


def explicitly_requested_tables(tables: list[dict], query: str) -> set[str]:
    """Recognize complete identifiers only within the authorized metadata snapshot.

    Prefix-based exclusions keep infrastructure out of general business recall,
    but must not hide an authorized workflow table a user explicitly asks about.
    """
    return {
        str(table["tableName"]) for table in tables
        if table.get("tableName") and re.search(
            r"(?<![a-z0-9_])" + re.escape(str(table["tableName"])) + r"(?![a-z0-9_])",
            str(query or ""), re.IGNORECASE,
        )
    }


def _metadata_text(table: dict, *, full_columns: bool = False) -> str:
    """把一张表的授权元数据渲染成单行语义文档（表名 + 表注释 + 字段名(字段注释)）。

    向量文档限制字段数；词法检索使用完整授权列，避免表尾金额、日期等字段失召回。
    """
    fields = []
    columns = table.get("columns") or []
    for column in (columns if full_columns else columns[:MAX_DOCUMENT_COLUMNS]):
        name = str(column.get("columnName") or "").strip()
        comment = str(column.get("comment") or "").strip()
        fields.append(f"{name}({comment})" if comment else name)
    return (
        f"表名: {table.get('tableName') or ''}; "
        f"表说明: {table.get('tableComment') or ''}; "
        f"字段: {', '.join(fields)}"
    )


def _expanded_terms(text: str) -> set[str]:
    """把问题切成检索词：按标点/连接词切分，补英文标识符，并做业务域别名扩展。

    别名扩展是**辅助证据**：命中某个业务域会把该域全部别名加入检索词，因此打分时
    必须降低扩展词的权重（见 ``_lexical_score``），否则“库存”这类通用词会让整个
    生产域的表分数趋同。
    """
    lowered = str(text or "").strip().lower()
    terms = {
        item for item in re.split(r"(?:\s+|[,，、;；/|+&：:()（）]+|和|与|及)", lowered)
        if len(item) >= 2
    }
    terms.update(re.findall(r"[a-z][a-z0-9_]{1,}", lowered))
    from app.modules.analytics.finance_semantics import finance_categories
    categories = finance_categories(lowered)
    incoming = bool(categories.intersection({"receivable", "receipt"}))
    outgoing = bool(categories.intersection({"payable", "payment"}))
    for domain, aliases in DOMAIN_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            if domain == "财务" and incoming != outgoing:
                # 单向取数只扩展同侧术语；应收不能借整个财务词表把大量付款表推到前面。
                aliases = (("财务", "finance", "financial", "应收", "未收", "收款", "回款", "receivable")
                           if incoming else ("财务", "finance", "financial", "应付", "未付", "付款", "支付", "payable", "payment"))
            elif domain == "财务" and not categories and re.search(r"(?:收款|付款)(?:单位|对象|账户|账号|人|方)", lowered):
                continue
            terms.update(alias.lower() for alias in aliases)
    return terms


QUERY_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anomaly", ("异常", "离群", "预警", "告警", "故障", "不合格", "anomaly", "alert")),
    ("prediction", ("预测", "预计", "趋势外推", "forecast", "predict")),
    ("trend", ("趋势", "变化", "环比", "同比", "按月", "按日", "trend", "over time")),
    ("ranking", ("排名", "排行", "top", "最高", "最低", "前十", "前10")),
    ("comparison", ("对比", "比较", "差异", " versus ", " vs ", "compare")),
    ("detail", ("明细", "列表", "逐条", "详情", "detail", "list")),
    ("summary", ("汇总", "统计", "总计", "合计", "多少", "概览", "summary", "count")),
)
FOLLOW_UP_MARKERS = (
    "继续", "再看", "再按", "改为", "换成", "上述", "这些", "其中", "它们",
    "上一条", "刚才", "然后", "只看", "再比较", "continue", "those", "instead",
)


def query_semantics(text: str) -> dict:
    """Expose deterministic query expansion/classification as audit evidence."""

    # 用纯规则给出意图、业务域、扩展词与“是否像追问”的判断，供检索打分和结果页的
    # 证据展示使用。规则是确定性的：同样的问句永远得到同样的分类，便于审计复现。
    # 已知边界：意图与业务域都是关键词命中，问句里的否定或反讽（例如“不是库存”）
    # 仍会命中“库存”域；因此这些字段只作检索与展示证据，不能作为业务结论或授权依据。
    lowered = str(text or "").strip().lower()
    domains = [
        domain for domain, aliases in DOMAIN_ALIASES.items()
        if any(alias.lower() in lowered for alias in aliases)
    ]
    intent = next((name for name, markers in QUERY_INTENTS if any(marker in lowered for marker in markers)), "general")
    terms = sorted(_expanded_terms(text), key=lambda value: (len(value), value))[:40]
    return {
        "intent": intent,
        "businessDomains": domains,
        "expandedTerms": terms,
        "followUpCandidate": any(marker in lowered for marker in FOLLOW_UP_MARKERS),
        "method": "metadata_comment_keyword_embedding_relation",
    }


def _keywords(table: dict) -> list[str]:
    """提取单表的关键词索引，作为没有向量模型时的确定性召回通道。

    中英文分别处理：英文标识符按单词切分，中文按非中文字符切段（长度 2–16）。
    结果排序后截断，保证同样的元数据生成同样的关键词集合（可复现）。
    """
    text = _metadata_text(table).lower()
    values = set(re.findall(r"[a-z][a-z0-9_]{1,}", text))
    values.update(item for item in re.split(r"[^\u4e00-\u9fff]+", text) if 1 < len(item) <= 16)
    for aliases in DOMAIN_ALIASES.values():
        values.update(alias.lower() for alias in aliases if alias.lower() in text)
    return sorted(values)[:240]


def _normalize_vector(values: Iterable[float]) -> list[float]:
    """向量归一化；零向量返回空列表（表示“没有可用向量”，而不是全 0 向量参与打分）。"""
    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return []
    return [value / norm for value in vector]


def _dot(left: list[float], right: list[float]) -> float:
    """归一化向量的点积即余弦相似度；两侧长度不一致时按较短者计算，不抛错。"""
    return sum(a * b for a, b in zip(left, right))


def _row_value(row: dict, key: str, default=None):
    """按不区分大小写读取行字典的值（驱动返回的列名大小写不稳定，需兼容）。"""
    lowered = key.lower()
    for name, value in row.items():
        if str(name).lower() == lowered:
            return value
    return default


def _add_relation(
    relations: list[dict], seen: set[tuple], *, source_table: str, target_table: str,
    relation_type: str, source_field: str = "", target_field: str = "",
    field_label: str = "", target_label: str = "", target_name_field: str = "",
    source_fields: list[str] | None = None, target_fields: list[str] | None = None,
    direction: str = "", unknown_declaration: dict | None = None,
) -> None:
    """追加一条声明式关系并去重；自关联与缺表名直接忽略（不猜同名字段关系）。

    ``seen`` 以关系类型、方向和完整连接字段组去重，复合键不会变成多条独立引用。
    调用方必须传入真实存在且已授权的表名。
    """
    if not source_table or not target_table or source_table == target_table:
        return
    source_fields = list(source_fields or ([source_field] if source_field else []))
    target_fields = list(target_fields or ([target_field] if target_field else []))
    identity = (source_table, target_table, relation_type, tuple(source_fields), tuple(target_fields), direction)
    if unknown_declaration:
        identity += (json.dumps(unknown_declaration, sort_keys=True, ensure_ascii=False),)
    if identity in seen:
        return
    seen.add(identity)
    relations.append({
        "sourceTable": source_table,
        "targetTable": target_table,
        "relationType": relation_type,
        "sourceField": source_fields[0] if len(source_fields) == 1 else "",
        "targetField": target_fields[0] if len(target_fields) == 1 else "",
        "fieldLabel": field_label,
        "targetLabel": target_label,
        "targetNameField": target_name_field,
    })
    if len(source_fields) > 1 or len(target_fields) > 1:
        # 复合外键是一组同时成立的连接条件，不能拆成任意单字段可独立关联的关系。
        relations[-1].update(columns=source_fields, targetColumns=target_fields)
    if direction:
        relations[-1]["relationDirection"] = direction
    if relation_type == "main_detail" and not source_field and not target_field:
        relations[-1]["joinKeysKnown"] = False


def _business_key_evidence(table: dict) -> tuple[str, str]:
    """接受唯一显式业务键；旧快照仅使用明确声明标识主键的非空文本字段。"""
    keys = {str(column.get("columnName") or "") for column in table.get("columns") or []
            if (column.get("businessKey") is True or str(column.get("semanticType") or "").lower() == "primarykey")
            and column.get("columnName")}
    if keys:
        return (next(iter(keys)), "lowcode_primary_key") if len(keys) == 1 else ("", "")
    keys = {str(column.get("columnName") or "") for column in table.get("columns") or []
            if "businessKey" not in column and column.get("nullable") is False
            and not column.get("primaryKey") and column.get("columnName")
            and re.fullmatch(r"(?:id主键|业务主键|实体主键)", re.sub(r"\s+", "", str(column.get("comment") or "")).lower())
            and re.match(r"(?:n?varchar|n?char|uuid)\b", str(column.get("dataType") or ""), re.I)}
    return (next(iter(keys)), "declared_key_comment") if len(keys) == 1 else ("", "")


def _business_key(table: dict) -> str:
    """返回唯一有声明依据的实体键，绝不以字段后缀或物理自增键猜测。"""
    return _business_key_evidence(table)[0]


def _reference_types_compatible(left: dict, right: dict, *, required: bool) -> bool:
    """旧注释补键要求引用两端类型可核验，已标注业务键仍拒绝已知类型冲突。"""
    values = [str(column.get("dataType") or "").strip().lower() for column in (left, right)]
    if not all(values):
        return not required
    parsed = [re.match(r"([a-z]+)(?:\((\d+)\))?", value) for value in values]
    if not all(parsed):
        return False
    text_types = {"char", "varchar", "nchar", "nvarchar"}
    if all(item[1] in text_types for item in parsed):
        return not all(item[2] for item in parsed) or parsed[0][2] == parsed[1][2]
    return parsed[0][1] == parsed[1][1] and parsed[0][2] == parsed[1][2]


def _scoped_declared_relations(table: dict, table_map: dict[str, dict], relations: list[dict]) -> list[dict]:
    """合并缓存声明关系时复核两端真实字段；缓存中的隐藏字段不能重新进入提示。"""
    source_columns = {str(column.get("columnName")) for column in table.get("columns") or []}
    retained = {}
    for relation in relations:
        value = dict(relation)
        if value.get("sourceTable") and value["sourceTable"] != table.get("tableName"):
            continue
        target = table_map.get(str(relation.get("targetTable") or ""))
        if not target:
            continue
        value.setdefault("sourceTable", table.get("tableName"))
        target_columns = {str(column.get("columnName")) for column in target.get("columns") or []}
        if value.get("targetNameField") and value["targetNameField"] not in target_columns:
            value.pop("targetNameField")
        if value.get("joinKeysKnown") is False and value.get("sourceDeclared"):
            # 未确认关系可保留为表级线索，但证据中的每个字段仍须在当前授权范围内。
            declared = value["sourceDeclared"]
            if any(declared.get(side + "Table") not in table_map or
                   (declared.get(side + "Field") and declared[side + "Field"] not in {
                       column.get("columnName") for column in table_map[declared[side + "Table"]].get("columns") or []})
                   for side in ("source", "target")):
                continue
            value.update(sourceField="", targetField="")
            value.pop("columns", None)
            value.pop("targetColumns", None)
            if value.get("targetNameField") and value["targetNameField"] not in target_columns:
                value.pop("targetNameField")
            retained[json.dumps(value, sort_keys=True, ensure_ascii=False)] = value
            continue
        if value.get("relationType") == "foreign_key":
            # 旧语义文档可能将复合外键拆成单字段；利用当前快照修复，不能继续传播错误 JOIN。
            own_fields = value.get("columns") or [value.get("sourceField")]
            other_fields = value.get("targetColumns") or [value.get("targetField")]
            pairs = set(zip(own_fields, other_fields))
            reversed_relation = value.get("relationDirection") == "reverse"
            origin, destination = (target, table) if reversed_relation else (table, target)
            matches = []
            for foreign_key in origin.get("foreignKeys") or []:
                if foreign_key.get("referredTable") != destination.get("tableName"):
                    continue
                origin_fields = list(foreign_key.get("columns") or [])
                destination_fields = list(foreign_key.get("referredColumns") or [])
                left, right = (destination_fields, origin_fields) if reversed_relation else (origin_fields, destination_fields)
                if left and len(left) == len(right) and pairs and pairs.issubset(set(zip(left, right))):
                    if (left, right) not in matches:
                        matches.append((left, right))
            if len(matches) > 1:
                exact = [(left, right) for left, right in matches if set(zip(left, right)) == pairs]
                if len(exact) != 1:
                    continue
                matches = exact
            if matches and len(matches[0][0]) > 1:
                value.update(columns=matches[0][0], targetColumns=matches[0][1], sourceField="", targetField="")
        if (value.get("relationType") == "main_detail" and not value.get("sourceField")
                and not value.get("targetField") and not value.get("columns") and not value.get("targetColumns")):
            # 主从实体声明可用于一跳表召回，但没有连接键时不能据此生成 JOIN。
            value["joinKeysKnown"] = False
            if value.get("targetNameField") and value["targetNameField"] not in target_columns:
                value.pop("targetNameField")
            retained[json.dumps(value, sort_keys=True, ensure_ascii=False)] = value
            continue
        if value.get("relationType") == "reference":
            reverse = value.get("relationDirection") == "reverse"
            identity_table, reference_table = (table, target) if reverse else (target, table)
            identity_slot, reference_slot = ("sourceField", "targetField") if reverse else ("targetField", "sourceField")
            identity_columns = {column["columnName"]: column for column in identity_table.get("columns") or []}
            reference_columns = {column["columnName"]: column for column in reference_table.get("columns") or []}
            # 隐藏字段的旧关系仍直接丢弃，不借修复步骤重新扩大字段可见范围。
            if ((value.get(identity_slot) and value[identity_slot] not in identity_columns)
                    or (value.get(reference_slot) and value[reference_slot] not in reference_columns)):
                continue
            if not value.get("columns") and not value.get("targetColumns"):
                key, evidence = _business_key_evidence(identity_table)
                reference_column = reference_columns.get(value.get(reference_slot))
                foreign_key_targets = {foreign_key["referredColumns"][0]
                    for foreign_key in reference_table.get("foreignKeys") or []
                    if foreign_key.get("referredTable") == identity_table.get("tableName")
                    and foreign_key.get("columns") == [value.get(reference_slot)]
                    and len(foreign_key.get("referredColumns") or []) == 1
                    and foreign_key["referredColumns"][0] in identity_columns}
                if foreign_key_targets:
                    # 数据库真实外键优先于低代码推导；有明确约束时物理键也可以作为引用端。
                    key, evidence = ((next(iter(foreign_key_targets)), "foreign_key")
                                     if len(foreign_key_targets) == 1 else ("", ""))
                if key and reference_column is not None:
                    if _reference_types_compatible(identity_columns[key], reference_column,
                                                   required=evidence == "declared_key_comment"):
                        if value.get(identity_slot) != key:
                            value.setdefault("sourceDeclared", {name: value.get(name, "") for name in
                                ("sourceTable", "sourceField", "targetTable", "targetField", "relationDirection")})
                        value[identity_slot] = key
                        value["joinKeysKnown"] = True
                        value["joinKeyEvidence"] = {"identityTable": identity_table["tableName"], "identityField": key,
                            "identityEvidence": evidence, "referenceTable": reference_table["tableName"],
                            "referenceField": value[reference_slot], "referenceEvidence": "declared_reference"}
                    else:
                        value.setdefault("sourceDeclared", {name: value.get(name, "") for name in
                            ("sourceTable", "sourceField", "targetTable", "targetField", "relationDirection")})
                        value.update(sourceField="", targetField="", joinKeysKnown=False,
                                     joinKeyIssue="声明业务键与引用字段类型不兼容，不能据此关联")
                        value.pop("joinKeyEvidence", None)
                        retained[json.dumps(value, sort_keys=True, ensure_ascii=False)] = value
                        continue
                elif value.get(identity_slot) and reference_column is not None:
                    # 名称字段、物理自增键或多业务键均不能代替已确认的实体标识。
                    value.setdefault("sourceDeclared", {name: value.get(name, "") for name in
                        ("sourceTable", "sourceField", "targetTable", "targetField", "relationDirection")})
                    value.update(sourceField="", targetField="", joinKeysKnown=False,
                                 joinKeyIssue="缺少唯一且可核验的声明业务键，不能据此关联")
                    value.pop("joinKeyEvidence", None)
                    retained[json.dumps(value, sort_keys=True, ensure_ascii=False)] = value
                    continue
        source_fields = value.get("columns") or [value.get("sourceField")]
        target_fields = value.get("targetColumns") or [value.get("targetField")]
        if (not source_fields or len(source_fields) != len(target_fields)
                or any(field not in source_columns for field in source_fields)
                or any(field not in target_columns for field in target_fields)):
            continue
        if value.get("targetNameField") and value["targetNameField"] not in target_columns:
            value.pop("targetNameField")
        retained[json.dumps(value, sort_keys=True, ensure_ascii=False)] = value
    canonical = {}
    for value in retained.values():
        identity = (value.get("sourceTable"), value.get("targetTable"), value.get("relationType"),
                    value.get("relationDirection") or "",
                    tuple(value.get("columns") or [value.get("sourceField") or ""]),
                    tuple(value.get("targetColumns") or [value.get("targetField") or ""]))
        if value.get("joinKeysKnown") is False:
            identity += (json.dumps(value.get("sourceDeclared") or {}, sort_keys=True, ensure_ascii=False),)
        if identity in canonical:
            # 双向补全可遇到同一声明；优先保留原有名称标签，并补齐可追溯证据。
            for key, item in value.items():
                if key not in canonical[identity] or canonical[identity][key] in (None, ""):
                    canonical[identity][key] = item
        else:
            canonical[identity] = value
    return list(canonical.values())


def _reverse_declared_relation(relation: dict) -> dict:
    """反转一条已声明关系的方向，不增加或猜测连接条件。"""
    reverse = {**relation, "sourceTable": relation["targetTable"], "targetTable": relation["sourceTable"],
               "sourceField": relation.get("targetField") or "", "targetField": relation.get("sourceField") or ""}
    if relation.get("relationDirection") == "reverse":
        reverse.pop("relationDirection", None)
    else:
        reverse["relationDirection"] = "reverse"
    if relation.get("columns"):
        reverse.update(columns=list(relation["targetColumns"]), targetColumns=list(relation["columns"]))
    # 展示名称属于原目标表；反转后不能把它绑定到另一张表。
    reverse.pop("targetNameField", None)
    reverse.pop("targetLabel", None)
    return reverse


def extract_relations(db, source, tables: list[dict], *, cached_only: bool = False) -> list[dict]:
    """读取声明的关系；目录发现只使用缓存外键，显式同步才读取低代码元数据表。"""

    # 两个来源，按可信度排序：
    #   1. 数据库真实外键（始终可用，且两端表都必须在授权快照内）；
    #   2. MySQL 低代码元数据表 t_meta_entity / t_meta_field（主从关系、
    #      reference/many_to_many 引用、名称字段），仅在两张元数据表都被授权时读取。
    # 缺少授权元字段或引用对不上授权表时，不猜测同名字段关联；实际连接/查询失败
    # 必须立即抛出并停止同步，不能在超时、断连后改用另一条 SQL 自动重试。
    table_map = {str(table.get("tableName") or ""): table for table in tables}
    real_tables = set(table_map)
    relations: list[dict] = []
    seen: set[tuple] = set()
    for table in tables:
        source_table = str(table.get("tableName") or "")
        for foreign_key in table.get("foreignKeys") or []:
            target = str(foreign_key.get("referredTable") or "")
            if target not in real_tables:
                continue
            columns = list(foreign_key.get("columns") or [])
            referred = list(foreign_key.get("referredColumns") or [])
            if not columns or len(columns) != len(referred):
                continue
            scoped = _scoped_declared_relations(table, table_map, [{
                "sourceTable": source_table, "targetTable": target, "relationType": "foreign_key",
                "columns": columns, "targetColumns": referred,
            }])
            if scoped:
                _add_relation(
                    relations, seen, source_table=source_table, target_table=target,
                    relation_type="foreign_key", source_fields=columns, target_fields=referred,
                )

    if cached_only:
        table_map = {str(table.get("tableName") or ""): table for table in tables}
        for table in tables:
            for relation in _scoped_declared_relations(table, table_map, table.get("declaredRelations") or []):
                previous_count = len(relations)
                source_fields = relation.get("columns") or [relation.get("sourceField")]
                target_fields = relation.get("targetColumns") or [relation.get("targetField")]
                _add_relation(relations, seen, source_table=str(table.get("tableName") or ""),
                              target_table=relation["targetTable"], relation_type=relation.get("relationType") or "reference",
                              source_fields=[field for field in source_fields if field],
                              target_fields=[field for field in target_fields if field],
                              field_label=relation.get("fieldLabel") or "", target_label=relation.get("targetLabel") or "",
                              target_name_field=relation.get("targetNameField") or "",
                              direction=relation.get("relationDirection") or "",
                              unknown_declaration=relation.get("sourceDeclared") if relation.get("joinKeysKnown") is False else None)
                if len(relations) > previous_count:
                    relations[-1].update({key: deepcopy(relation[key]) for key in
                        ("joinKeysKnown", "joinKeyEvidence", "joinKeyIssue", "sourceDeclared") if key in relation})

    if cached_only or source.provider != "mysql" or not {"t_meta_entity", "t_meta_field"}.issubset(real_tables):
        return relations

    table_map = {str(table.get("tableName") or ""): table for table in tables}
    entity_fields = {column.get("columnName") for column in table_map["t_meta_entity"].get("columns") or []}
    field_fields = {column.get("columnName") for column in table_map["t_meta_field"].get("columns") or []}
    if (not {"entityCode", "name", "physicalName"}.issubset(entity_fields)
            or not {"entityCode", "name", "physicalName", "type", "referTo"}.issubset(field_fields)):
        return relations
    entity_select = "entityCode, name, physicalName, " + ", ".join(
        name if name in entity_fields else f"NULL AS {name}"
        for name in ("label", "detailEntityFlag", "mainEntity"))
    field_select = "entityCode, name, physicalName, type, referTo, " + ", ".join(
        name if name in field_fields else f"NULL AS {name}" for name in ("label", "nameFieldFlag"))
    with readonly_connection(db, source) as connection:
        checkpoint()
        entity_rows = [dict(row) for row in connection.exec_driver_sql(
            f"SELECT {entity_select} FROM t_meta_entity LIMIT 10000"
        ).mappings().all()]
        checkpoint()
        field_rows = [dict(row) for row in connection.exec_driver_sql(
            f"SELECT {field_select} FROM t_meta_field LIMIT 30000"
        ).mappings().all()]
        checkpoint()

    name_to_physical: dict[str, str] = {}
    code_to_physical: dict[str, str] = {}
    ambiguous_names, ambiguous_codes = set(), set()
    label_by_physical: dict[str, str] = {}
    pending_main_detail: list[tuple[str, str]] = []
    def register_entity(mapping: dict[str, str], ambiguous: set[str], key: str, physical: str) -> None:
        """同一声明标识映射多张物理表时取消映射，不能让最后一行悄然覆盖目标表。"""
        if not key or key in ambiguous:
            return
        if key in mapping and mapping[key] != physical:
            ambiguous.add(key)
            mapping.pop(key)
        else:
            mapping[key] = physical
    for row in entity_rows:
        logical_name = str(_row_value(row, "name") or "").strip()
        code = str(_row_value(row, "entityCode") or "").strip()
        physical = str(_row_value(row, "physicalName") or "").strip()
        if not physical or physical not in real_tables:
            continue
        register_entity(name_to_physical, ambiguous_names, logical_name, physical)
        register_entity(code_to_physical, ambiguous_codes, code, physical)
        label_by_physical[physical] = str(_row_value(row, "label") or "").strip()
        if int(_row_value(row, "detailEntityFlag", 0) or 0) == 1:
            main_entity = str(_row_value(row, "mainEntity") or "").split(",", 1)[0].strip()
            if main_entity:
                pending_main_detail.append((main_entity, physical))

    for main_entity, detail_table in pending_main_detail:
        main_table = name_to_physical.get(main_entity)
        if main_table:
            _add_relation(
                relations, seen, source_table=main_table, target_table=detail_table,
                relation_type="main_detail", target_label=label_by_physical.get(detail_table, ""),
            )

    name_fields: dict[str, str] = {}
    for row in field_rows:
        if int(_row_value(row, "nameFieldFlag", 0) or 0) == 1:
            code = str(_row_value(row, "entityCode") or "")
            name_fields[code] = str(_row_value(row, "physicalName") or "")

    code_by_physical = {physical: code for code, physical in code_to_physical.items()}
    for row in field_rows:
        logical_field = str(_row_value(row, "name") or "").strip()
        if logical_field.lower() in SYSTEM_RELATION_FIELDS:
            continue
        refer_to = str(_row_value(row, "referTo") or "").split(",", 1)[0].strip()
        if not refer_to:
            continue
        source_table = code_to_physical.get(str(_row_value(row, "entityCode") or ""))
        target_table = name_to_physical.get(refer_to)
        if not source_table or not target_table:
            continue
        relation_type = "many_to_many" if "many" in str(_row_value(row, "type") or "").lower() else "reference"
        target_code = code_by_physical.get(target_table, "")
        _add_relation(
            relations, seen, source_table=source_table, target_table=target_table,
            relation_type=relation_type,
            source_field=str(_row_value(row, "physicalName") or ""),
            target_field=_business_key(table_map[target_table]),
            field_label=str(_row_value(row, "label") or ""),
            target_label=label_by_physical.get(target_table, ""),
            target_name_field=name_fields.get(target_code, ""),
        )
    return [relation for table in tables for relation in _scoped_declared_relations(
        table, table_map, [relation for relation in relations if relation["sourceTable"] == table["tableName"]])]


def catalog_profile(source, tables: list[dict], *, semantic_status: str, indexed_tables: int) -> dict:
    """生成数据源画像（自动说明、业务域、注释覆盖率、语义索引状态）。

    只统计业务表（``is_business_table``），因此 ``tableCount`` 是全部授权表、
    ``businessTableCount`` 才是业务表；注释覆盖率反映“有多少表和字段有数据库注释”，
    是判断能否做语义检索的前提。``semantic_status``/``indexed_tables`` 由调用方
    显式传入，避免把“没建索引”伪装成 ready。
    """
    business_tables = [table for table in tables if is_business_table(str(table.get("tableName") or ""))]
    field_count = sum(len(table.get("columns") or []) for table in business_tables)
    commented_tables = sum(bool(str(table.get("tableComment") or "").strip()) for table in business_tables)
    commented_fields = sum(
        bool(str(column.get("comment") or "").strip())
        for table in business_tables for column in (table.get("columns") or [])
    )
    domain_scores = Counter()
    combined = " ".join(_metadata_text(table).lower() for table in business_tables)
    for domain, aliases in DOMAIN_ALIASES.items():
        domain_scores[domain] = sum(combined.count(alias.lower()) for alias in aliases)
    domains = [name for name, score in domain_scores.most_common() if score > 0][:5]
    representative = [
        str(table.get("tableComment") or "").strip()
        for table in business_tables if str(table.get("tableComment") or "").strip()
    ][:4]
    database = database_name(source) or source.name
    coverage = "、".join(domains) if domains else "通用业务"
    examples = "；代表数据包括" + "、".join(representative) if representative else ""
    auto_description = (
        f"{database} 的{coverage}数据源，已同步 {len(business_tables)} 张业务表和 "
        f"{field_count} 个字段{examples}。"
    )[:1900]
    return {
        "autoDescription": auto_description,
        "metadataStatus": "ready",
        "tableCount": len(tables),
        "businessTableCount": len(business_tables),
        "fieldCount": field_count,
        "commentedTableCount": commented_tables,
        "commentedFieldCount": commented_fields,
        "semanticIndexStatus": semantic_status,
        "semanticIndexedTableCount": indexed_tables,
        "businessDomains": domains,
        "metadataSyncedAt": datetime.utcnow().isoformat(),
    }


def build_semantic_index(ctx, source, tables: list[dict], *, cached_only: bool = False) -> dict:
    """建立表目录；缓存模式不连接业务库或模型，并保留同版本已同步关系与向量。"""

    # 用途：为授权快照里的每张业务表建立/刷新语义文档、关键词、可选向量与关系，
    # 并清理已不存在的表。返回画像字典（同时写回 source.connection_config）。
    # 副作用：写 SourceSemanticDocument（可能删除过期行）并可能调用向量模型；
    # 不持久化任何业务行数据，向量只由元数据文本生成。
    # 降级行为：向量模型不可用、返回条数不匹配或返回空向量时，整体降级为
    # keyword_only（清空向量但保留文档与关键词），表结构与关键词召回仍然可用；
    # 索引状态会如实写入 index_status 与数据源画像，不会声称 ready。
    checkpoint()
    candidates = [table for table in tables if is_business_table(str(table.get("tableName") or ""))]
    relations = extract_relations(ctx.db, source, tables, cached_only=cached_only)
    relation_map: dict[str, list[dict]] = defaultdict(list)
    for relation in relations:
        relation_map[relation["sourceTable"]].append(relation)
        reverse = _reverse_declared_relation(relation)
        relation_map[reverse["sourceTable"]].append(reverse)

    existing = {
        row.table_name: row for row in ctx.db.scalars(select(SourceSemanticDocument).where(
            SourceSemanticDocument.workspace_id == ctx.workspace_id,
            SourceSemanticDocument.datasource_id == source.id,
        )).all()
    }
    names = {str(table.get("tableName") or "") for table in candidates}
    stale = set(existing) - names

    documents = [_metadata_text(table) for table in candidates]
    vectors: list[list[float]] = [[] for _ in candidates]
    vector_ready = bool(candidates)
    version = source_version(source)
    if cached_only:
        # 旧版目录的惰性升级只补本地派生缓存；同步后的向量不可被一次列表读取清空。
        for index, table in enumerate(candidates):
            previous = existing.get(str(table.get("tableName") or ""))
            if previous and previous.source_version == version and previous.document_text == documents[index]:
                vectors[index] = list(previous.embedding_json or [])
        vector_ready = vector_ready and all(vectors)
    else:
        try:
            for start in range(0, len(documents), EMBEDDING_BATCH_SIZE):
                checkpoint()
                batch = model.embed_texts(
                    documents[start:start + EMBEDDING_BATCH_SIZE], workspace_id=ctx.workspace_id,
                    usage_context={"entry": "datasource_metadata", "datasource_id": source.id},
                )
                checkpoint()
                if len(batch) != len(documents[start:start + EMBEDDING_BATCH_SIZE]):
                    raise RuntimeError("向量数量与表文档数量不一致")
                vectors[start:start + len(batch)] = [_normalize_vector(item) for item in batch]
            vector_ready = vector_ready and all(vectors)
        except (ChatCancelled, ChatTaskTimeout):
            raise
        except Exception:
            # 向量失败只降级向量通道，文档与关键词照常写入，索引状态标为 keyword_only。
            vector_ready = False
            vectors = [[] for _ in candidates]

    checkpoint()
    for index, table in enumerate(candidates):
        name = str(table.get("tableName") or "")
        row = existing.get(name)
        saved_relations = (list(row.relations_json or [])
                           if cached_only and row and row.source_version == version else [])
        if row is None:
            row = SourceSemanticDocument(
                workspace_id=ctx.workspace_id, owner_user_id=source.owner_user_id,
                datasource_id=source.id, table_name=name, source_version=version,
                document_text=documents[index],
            )
            ctx.db.add(row)
        row.source_version = version
        row.document_text = documents[index]
        row.keywords_json = _keywords(table)
        row.embedding_json = vectors[index]
        if cached_only:
            combined_relations = _scoped_declared_relations(table,
                {str(item.get("tableName") or ""): item for item in candidates},
                saved_relations + relation_map.get(name, []))
            row.relations_json = list({
                json.dumps(relation, sort_keys=True, ensure_ascii=False): relation
                for relation in combined_relations if relation.get("targetTable") in names
            }.values())[:80]
        else:
            row.relations_json = _scoped_declared_relations(table,
                {str(item.get("tableName") or ""): item for item in candidates}, relation_map.get(name, []))[:80]
            # 同步后结构 API 与语义召回共享相同关系合同；快照不能只保存列而遗漏真实引用。
            table["declaredRelations"] = list(row.relations_json)
        row.index_status = "ready" if vectors[index] else "keyword_only"
    status = "ready" if vector_ready else "keyword_only"
    profile = catalog_profile(source, tables, semantic_status=status, indexed_tables=len(candidates))
    from app.modules.analytics.profile_updates import update_profile
    # 外部读取/向量等待结束后才校验版本并锁住画像行；失败时不能落旧字段或旧索引。
    update_profile(ctx.db, source, profile, expected_version=version)
    if stale:
        ctx.db.execute(delete(SourceSemanticDocument).where(
            SourceSemanticDocument.workspace_id == ctx.workspace_id,
            SourceSemanticDocument.datasource_id == source.id,
            SourceSemanticDocument.table_name.in_(stale),
        ))
    ctx.db.flush()
    return profile


def _literal_fragments(query: str, text: str) -> list[str]:
    """Find non-overlapping Chinese phrases actually shared with the metadata."""
    ignored = {'查询', '查看', '信息', '数据', '帮我', '一下', '包括', '名称', '情况'}
    candidates = []
    # 只保留与元数据文本真实共有的中文片段，并按长度优先去重取词，
    # 因此“有哪些财务数据”不会因为泛用词而命中无关表。
    for match in re.finditer(r'[\u4e00-\u9fff]{2,}', query):
        value = match.group()
        for size in range(min(12, len(value)), 1, -1):
            for start in range(len(value) - size + 1):
                phrase = value[start:start + size]
                if phrase not in ignored and phrase in text:
                    candidates.append((size, match.start() + start, phrase))
    used, fragments = set(), []
    for size, start, phrase in sorted(candidates, reverse=True):
        positions = set(range(start, start + size))
        if not positions.intersection(used):
            used.update(positions)
            if phrase not in fragments:
                fragments.append(phrase)
    return fragments


def _lexical_score(query: str, terms: set[str], document: SourceSemanticDocument, *, document_text: str | None = None) -> float:
    """词法相关度打分：表名 > 文档标题（表名+表注释）> 全文，扩展词权重降为 0.1。

    额外对“问题与元数据共有的中文片段”按长度平方加权，让长专有名词（如“不合格品处理单”）
    显著高于泛用词。分数只用于排序，不作为业务结论，因此允许启发式权重存在。
    """
    text = (document_text if document_text is not None else document.document_text).lower()
    table_name = document.table_name.lower()
    heading = ";".join(text.split(";", 2)[:2])
    score = 0.0
    query_text = query.lower().strip()
    if query_text and query_text in text:
        score += 24.0
    for term in terms:
        # Domain expansion is supporting evidence. A word such as "库存"
        # must not make every production/maintenance synonym equally relevant.
        weight = 1.0 if term in query_text else 0.1
        if term == table_name:
            score += 28.0 * weight
        elif term in table_name:
            score += 16.0 * weight
        elif term in heading:
            score += 11.0 * weight
        elif term in text:
            score += 3.0 * weight
    score += sum(len(term) ** 2 * 3 for term in _literal_fragments(query_text, heading))
    score += sum(len(term) for term in _literal_fragments(query_text, text))
    return score


def column_query_score(column: dict, query: str) -> float:
    """只按问题与当前字段的真实标识、注释共有片段选列，不扩展业务同义词。"""
    name = str(column.get("columnName") or "").lower()
    text = name + " " + str(column.get("comment") or "").lower()
    query = str(query or "").lower()
    score = sum(len(term) ** 2 for term in _literal_fragments(query, text))
    for term in re.findall(r"[a-z_][a-z0-9_]*", query):
        if term == name:
            score += 100
        elif re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text):
            score += 10
    return float(score)


def _finance_topic_seeds(query: str, scored: list[tuple], table_map: dict[str, dict], seed_k: int,
                         explicit_names: set[str]) -> list[tuple]:
    """复合财务问题为两侧明确表标题各留候选位，付款表的收款单位不充当应收表。"""
    topics = (("应收", "收款", "回款", "receivable"), ("应付", "付款", "支付", "payable", "payment"))
    requested = [topic for topic in topics if any(term in query.lower() for term in topic)]
    if len(requested) < 2:
        return scored[:seed_k]
    required = [item for item in scored if item[3].table_name in explicit_names][:seed_k]
    names = {item[3].table_name for item in required}
    for topic in requested:
        for item in scored:
            table = table_map[item[3].table_name]
            heading = (table["tableName"] + " " + str(table.get("tableComment") or "")).lower()
            if any(term in heading for term in topic):
                if item[3].table_name not in names and len(required) < seed_k:
                    required.append(item)
                    names.add(item[3].table_name)
                break
    required.extend(item for item in scored if item[3].table_name not in names)
    return required[:seed_k]


def recall_tables(
    ctx, source, tables: list[dict], query: str, *, seed_k: int = DEFAULT_SEED_TABLES,
    max_extra: int = DEFAULT_RELATION_EXPANSION, include_samples: bool = True,
    cached_only: bool = False,
) -> tuple[list[dict], list[dict]]:
    """按词法、可选向量及声明关系召回；缓存模式同时禁止模型调用和业务数据取样。"""

    # 三通道召回：元数据注释词法匹配（始终可用）、可选向量相似度（仅在词法证据不足
    # 且未显式点名表时调用，避免无谓的模型开销）、以及种子表的**一跳声明关系**扩展。
    # 关系扩展有独立预算 max_extra，并跨所有种子表统一挑选，不让第一张无关表吃掉
    # 全部预算。
    # 返回 (tables, evidence)：表副本带 declaredRelations 与 retrievalEvidence
    # （rank/channels/reason），evidence 是逐表可复核的召回依据。召回结果只用于提示，
    # 真实授权范围仍来自完整快照。
    # 副作用：include_samples=True 时会用只读连接取少量脱敏样本附着在返回副本上；
    # 可能调用一次向量模型；不写业务数据。
    checkpoint()
    explicit_names = explicitly_requested_tables(tables, query)
    table_map = {
        str(table.get("tableName") or ""): table for table in tables
        if is_business_table(str(table.get("tableName") or ""))
        or str(table.get("tableName") or "") in explicit_names
    }
    if not table_map:
        return [], []
    docs = list(ctx.db.scalars(select(SourceSemanticDocument).where(
        SourceSemanticDocument.workspace_id == ctx.workspace_id,
        SourceSemanticDocument.datasource_id == source.id,
        SourceSemanticDocument.source_version == source_version(source),
        SourceSemanticDocument.table_name.in_(list(table_map)),
    )).all())
    indexed_names = {row.table_name for row in docs}
    for name in table_map.keys() - indexed_names:
        # Cold/partial indexes use the authorized snapshot immediately. Building
        # hundreds of embeddings belongs to metadata sync, never a chat request.
        docs.append(SourceSemanticDocument(
            table_name=name, document_text=_metadata_text(table_map[name]),
            embedding_json=[], relations_json=[{
                "targetTable": relation.get("referredTable"), "relationType": "foreign_key",
                "columns": relation.get("columns", []), "targetColumns": relation.get("referredColumns", []),
            } for relation in table_map[name].get("foreignKeys", [])],
        ))
    declared_by_name = {row.table_name: _scoped_declared_relations(table_map[row.table_name], table_map,
        list(row.relations_json or []) + list(table_map[row.table_name].get("declaredRelations") or [])) for row in docs}
    # 旧缓存可能只在客户、合同等主表保存反向引用；恢复另一方向后子表也能准确关联。
    reversed_by_name: dict[str, list[dict]] = defaultdict(list)
    for relations in declared_by_name.values():
        for relation in relations:
            reverse = _reverse_declared_relation(relation)
            reversed_by_name[reverse["sourceTable"]].append(reverse)
    for name in declared_by_name:
        declared_by_name[name] = _scoped_declared_relations(table_map[name], table_map,
            declared_by_name[name] + reversed_by_name[name])

    terms = _expanded_terms(query)
    lexical = {row.table_name: _lexical_score(query, terms, row,
        document_text=_metadata_text(table_map[row.table_name], full_columns=True)) for row in docs}
    lexical_rank = sorted(docs, key=lambda row: (lexical[row.table_name], row.table_name), reverse=True)

    query_vector: list[float] = []
    if not cached_only and not explicit_names and max(lexical.values(), default=0) < 11 and any(row.embedding_json for row in docs):
        try:
            checkpoint()
            query_vector = _normalize_vector(model.embed_texts(
                [query], workspace_id=ctx.workspace_id,
                usage_context={"entry": "datasource_recall", "datasource_id": source.id},
            )[0])
            checkpoint()
        except (ChatCancelled, ChatTaskTimeout):
            raise
        except Exception:
            query_vector = []
    vector_scores = {
        row.table_name: _dot(query_vector, list(row.embedding_json or []))
        for row in docs if query_vector and row.embedding_json
    }
    vector_rank = sorted(
        [row for row in docs if row.table_name in vector_scores],
        key=lambda row: vector_scores[row.table_name], reverse=True,
    )
    lexical_position = {row.table_name: index for index, row in enumerate(lexical_rank, 1)}
    vector_position = {row.table_name: index for index, row in enumerate(vector_rank, 1)}

    scored = []
    for row in docs:
        name = row.table_name
        hybrid = lexical[name] * 0.02 + 1.0 / (60 + lexical_position[name])
        channels = ["metadata_comment"]
        if name in explicit_names:
            channels.append("explicit_table")
        if name in vector_position:
            hybrid += 1.35 / (60 + vector_position[name]) + max(0.0, vector_scores[name]) * 0.08
            channels.append("embedding")
        scored.append((hybrid, lexical[name], vector_scores.get(name), row, channels))
    scored.sort(key=lambda item: (item[3].table_name in explicit_names, item[0], item[1], item[3].table_name), reverse=True)
    seeds = _finance_topic_seeds(query, scored, table_map, min(seed_k, len(scored)), explicit_names)

    selected_names = [item[3].table_name for item in seeds]
    seen = set(selected_names)
    relation_reason: dict[str, str] = {}
    document_by_name = {row.table_name: row for row in docs}
    added = 0
    relation_candidates = []
    for _, _, _, row, _ in seeds:
        for relation in declared_by_name.get(row.table_name) or []:
            target = str(relation.get("targetTable") or "")
            if target not in table_map or target in seen:
                continue
            relation_candidates.append((relation.get('relationDirection') == 'reverse',
                                        -lexical.get(target, 0), selected_names.index(row.table_name),
                                        row.table_name, target, relation))
    # Resolve the best declared references across all seeds, rather than let
    # the first unrelated table consume the entire relation budget.
    for _, _, _, source_name, target, relation in sorted(relation_candidates, key=lambda item: item[:5]):
        if added >= max_extra:
            break
        if target in seen:
            continue
        seen.add(target)
        selected_names.append(target)
        relation_reason[target] = f"由 {source_name} 的 {relation.get('relationType') or 'declared'} 关系扩展"
        added += 1

    score_by_name = {item[3].table_name: item for item in scored}
    selected: list[dict] = []
    evidence: list[dict] = []
    for position, name in enumerate(selected_names, 1):
        table = deepcopy(table_map[name])
        row = document_by_name.get(name)
        table["declaredRelations"] = list(declared_by_name.get(name) or [])[:30]
        item = score_by_name.get(name)
        channels = list(item[4]) if item else ["declared_relation"]
        if name in relation_reason:
            channels.append("declared_relation")
        table["retrievalEvidence"] = {
            "rank": position,
            "channels": channels,
            "reason": relation_reason.get(name) or (
                "问题明确指定了当前数据源已授权的表" if name in explicit_names
                else "表名、表注释与字段语义匹配本次问题"
            ),
        }
        selected.append(table)
        evidence.append({
            "tableName": name,
            "rank": position,
            "lexicalScore": round(float(item[1]), 4) if item else 0,
            "vectorScore": round(float(item[2]), 6) if item and item[2] is not None else None,
            "channels": channels,
            "reason": table["retrievalEvidence"]["reason"],
        })

    # 召回完成后才取样本：样本只附着在本次返回的副本上，落库的文档不含业务行。
    if include_samples and not cached_only:
        attach_safe_samples(ctx.db, source, selected)
    return selected, evidence


def _safe_sample_column(column: dict) -> bool:
    """抽样候选列：字段名或注释命中敏感标记、或类型是大对象/二进制的一律排除。

    只做屏蔽不做事后脱敏——敏感字段根本不取，避免真实值出现在提示、日志或响应中。
    """
    text = (
        str(column.get("columnName") or "") + " " + str(column.get("comment") or "")
    ).lower()
    data_type = str(column.get("dataType") or "").lower()
    return not any(marker in text for marker in SENSITIVE_MARKERS) and not any(
        marker in data_type for marker in ("blob", "binary", "image", "geometry")
    )


def _quote_identifier(value: str, provider: str) -> str:
    """按方言引号化标识符（MySQL 反引号、其余双引号），并转义引号本身。

    抽样 SQL 是拼接出来的，因此表名/字段名必须先经过这里的引号化，不能依赖
    上游的过滤（同一份代码也被其它只读场景复用）。
    """
    return "`" + value.replace("`", "``") + "`" if provider == "mysql" else '"' + value.replace('"', '""') + '"'


def _json_value(value):
    """样本值序列化：Decimal 用十进制文本保留精度，日期用 ISO，长文本截断到 500 字符。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)[:500]


def attach_safe_samples(db, source, tables: list[dict]) -> None:
    """读取少量脱敏样本补充提示；普通失败可降级，任务停止必须向上传播。"""

    # 只处理提示用的表副本：样本按 MAX_SAMPLE_TABLES/COLUMNS/ROWS 限制数量与行数，
    # 单表失败或整段失败都静默跳过（样本是增强信息，不能因此让查询失败），
    # 并且只读连接、不写回任何持久化结构。
    sql_tables = tables[:MAX_SAMPLE_TABLES]
    if source.provider not in {"mysql", "postgres", "sqlite"} or not sql_tables:
        return
    try:
        checkpoint()
        with readonly_connection(db, source) as connection:
            for table in sql_tables:
                checkpoint()
                columns = [column for column in table.get("columns") or [] if _safe_sample_column(column)]
                columns = columns[:MAX_SAMPLE_COLUMNS]
                if not columns:
                    continue
                table_name = str(table.get("tableName") or "")
                selected = ", ".join(_quote_identifier(str(column["columnName"]), source.provider) for column in columns)
                statement = (
                    f"SELECT {selected} FROM {_quote_identifier(table_name, source.provider)} "
                    f"LIMIT {MAX_SAMPLE_ROWS}"
                )
                try:
                    rows = connection.exec_driver_sql(statement).mappings().all()
                    checkpoint()
                except (ChatCancelled, ChatTaskTimeout):
                    raise
                except Exception:
                    continue
                table["sampleRows"] = [
                    {str(key): _json_value(value) for key, value in dict(row).items()}
                    for row in rows
                ]
    except (ChatCancelled, ChatTaskTimeout):
        raise
    except Exception:
        return


def source_match_score(source_view, metadata_tables: list[dict], requirement: str) -> tuple[float, list[str]]:
    """Cheap deterministic source ranking used before an explicit selection."""

    # 用途：在用户没有指定数据源时按关键词给出候选排序，纯规则、不连接数据库、
    # 不调用模型，因此可以在发现接口里对多个候选快速打分。返回 (分数, 命中词)。
    # 已知边界：命中词只做子串匹配，中文同义改写（如“回款”与“收款”）依赖
    # DOMAIN_ALIASES 覆盖，未覆盖的说法会漏配；分数只影响候选排序，最终由用户勾选，
    # 因此低分不代表数据不存在。
    terms = _expanded_terms(requirement)
    source_text = " ".join([
        str(source_view.name or ""), str(source_view.description or ""),
        *[_metadata_text(table) for table in metadata_tables if is_business_table(str(table.get("tableName") or ""))],
    ]).lower()
    matched = [term for term in terms if term in source_text]
    table_hits = sum(
        1 for table in metadata_tables
        if any(term in _metadata_text(table).lower() for term in terms)
    )
    coverage = min(2.0, math.log10(max(1, table_hits) + 1))
    return len(matched) * 2.0 + table_hits * 0.15 + coverage, matched[:12]
