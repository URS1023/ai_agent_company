"""财务旧元数据回归：仅加载脱敏结构文件和内存桩，禁止业务数据库及模型调用。"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.modules.analytics import business_catalog, semantic
from app.modules.analytics.models import SourceSemanticDocument
from app.modules.analytics.queries import _bounded_schema
from test_semantic_relation_contract import ENTITIES, FIELDS, fake_connection, relation_tables


@pytest.fixture
def legacy_tables():
    """真实缓存仅提取表列声明；测试文件不包含业务行或连接信息。"""
    return json.loads((Path(__file__).parents[1] / "fixtures" / "finance_metadata_legacy.json").read_text(
        encoding="utf-8"))["tables"]


@pytest.fixture
def cached_context(monkeypatch):
    """目录查询仅访问内存平台记录，任何业务连接、取样或向量调用都会失败。"""
    def forbidden(*args, **kwargs):
        raise AssertionError("离线元数据回归不能读取业务库或调用模型")
    monkeypatch.setattr(semantic, "readonly_connection", forbidden)
    monkeypatch.setattr(semantic, "attach_safe_samples", forbidden)
    monkeypatch.setattr(semantic.model, "embed_texts", forbidden)
    ctx = SimpleNamespace(db=SimpleNamespace(scalars=lambda *args: SimpleNamespace(all=lambda: [])),
                          workspace_id="offline-workspace")
    source = SimpleNamespace(id="offline-source", provider="mysql", connection_config={})
    return ctx, source


def reference_to(table, target, *, reverse=False):
    """只选择明确引用，避免把没有连接条件的主从表线索当作外键。"""
    return next(relation for relation in table["declaredRelations"]
                if relation["targetTable"] == target and relation["relationType"] == "reference"
                and (relation.get("relationDirection") == "reverse") is reverse)


def test_saved_customer_display_name_is_repaired_to_declared_entity_key(legacy_tables, cached_context):
    ctx, source = cached_context
    selected, _ = semantic.recall_tables(ctx, source, legacy_tables, "t_salecontract", seed_k=1, cached_only=True)
    tables = {table["tableName"]: table for table in selected}
    relation = reference_to(tables["t_salecontract"], "t_custbaseinfo")
    assert (relation["sourceField"], relation["targetField"]) == ("c_custBaseID", "custBaseInfoId")
    assert relation["joinKeysKnown"] is True
    assert relation["sourceDeclared"]["sourceField"] == "c_custName"
    assert relation["joinKeyEvidence"] == {
        "identityTable": "t_custbaseinfo", "identityField": "custBaseInfoId",
        "identityEvidence": "declared_key_comment", "referenceTable": "t_salecontract",
        "referenceField": "c_custBaseID", "referenceEvidence": "declared_reference",
    }
    assert reference_to(tables["t_custbaseinfo"], "t_salecontract", reverse=True)["sourceField"] == "custBaseInfoId"
    # 不改写授权原始快照，且 autoId 从来不是这条关系的业务键。
    assert legacy_tables[0]["declaredRelations"][0]["sourceField"] == "c_custName"


@pytest.mark.parametrize("detail", ["t_salecost", "t_saleproductinfo"])
def test_saved_contract_number_is_not_used_as_detail_identity(legacy_tables, cached_context, detail):
    ctx, source = cached_context
    selected, _ = semantic.recall_tables(ctx, source, legacy_tables, detail, seed_k=1, cached_only=True)
    relation = reference_to(next(table for table in selected if table["tableName"] == detail), "t_salecontract")
    assert (relation["sourceField"], relation["targetField"]) == ("mdSaleContractId", "saleContractId")
    assert relation["sourceDeclared"]["sourceField"] == "c_contractNo"


@pytest.mark.parametrize("case", ["ambiguous", "physical_only", "type_mismatch", "unknown_type", "explicit_false"])
def test_unverified_legacy_identity_stays_unknown_and_keeps_original_evidence(legacy_tables, case):
    by_name = {table["tableName"]: table for table in legacy_tables}
    customer = by_name["t_custbaseinfo"]
    identity = customer["columns"][1]
    if case == "ambiguous":
        customer["columns"].append({**identity, "columnName": "other_entity_key"})
    elif case == "physical_only":
        identity["comment"] = "普通字段"
    elif case == "type_mismatch":
        identity["dataType"] = "CHAR(20)"
    elif case == "unknown_type":
        identity.pop("dataType")
    else:
        identity["businessKey"] = False
    original = deepcopy(customer["declaredRelations"][0])
    result = semantic._scoped_declared_relations(customer, by_name, [original])
    assert len(result) == 1
    assert result[0]["joinKeysKnown"] is False
    assert result[0]["sourceField"] == result[0]["targetField"] == ""
    assert result[0]["sourceDeclared"]["sourceField"] == "c_custName"
    assert "joinKeyEvidence" not in result[0]
    assert semantic._scoped_declared_relations(customer, by_name, result) == result
    extracted = semantic.extract_relations(None, SimpleNamespace(provider="mysql"),
        [{**customer, "declaredRelations": result}, by_name["t_salecontract"]], cached_only=True)
    assert extracted[0]["sourceDeclared"] == result[0]["sourceDeclared"]


def test_reference_with_actual_foreign_key_keeps_declared_physical_identity():
    tables = [{"tableName": "details", "columns": [{"columnName": "owner", "dataType": "INTEGER"}],
               "foreignKeys": [{"columns": ["owner"], "referredTable": "owners", "referredColumns": ["id"]}]},
              {"tableName": "owners", "columns": [{"columnName": "id", "dataType": "INTEGER", "primaryKey": True}]}]
    relation = {"sourceTable": "details", "sourceField": "owner", "targetTable": "owners", "targetField": "id",
                "relationType": "reference"}
    result = semantic._scoped_declared_relations(tables[0], {t["tableName"]: t for t in tables}, [relation])[0]
    assert result["sourceField"] == "owner" and result["targetField"] == "id"
    assert result["joinKeysKnown"] and result["joinKeyEvidence"]["identityEvidence"] == "foreign_key"


@pytest.mark.parametrize("identity", ["name", "entityCode"])
def test_ambiguous_lowcode_entity_mapping_never_silently_selects_another_table(monkeypatch, identity):
    tables = relation_tables()
    tables.append({"tableName": "other_warehouse", "columns": [{"columnName": "id", "businessKey": True}]})
    other = {"entityCode": 3, "name": "OtherWarehouse", "physicalName": "other_warehouse"}
    other[identity] = ENTITIES[1][identity]
    calls = fake_connection(monkeypatch, [ENTITIES + [other], FIELDS])
    result = semantic.extract_relations(None, SimpleNamespace(provider="mysql"), tables)
    assert len(calls) == 2
    if identity == "name":
        assert result == []
    else:
        # 指向物理目标仍由唯一逻辑名确定，不能把歧义代码中的名称列映射到别表。
        assert all(relation["targetTable"] == "warehouse" for relation in result)
        assert all(not relation.get("targetNameField") for relation in result)


def test_repaired_relation_cannot_reintroduce_hidden_original_fields(legacy_tables):
    by_name = {table["tableName"]: table for table in legacy_tables}
    customer = by_name["t_custbaseinfo"]
    original = deepcopy(customer["declaredRelations"])
    customer["columns"] = [column for column in customer["columns"] if column["columnName"] != "c_custName"]
    assert semantic._scoped_declared_relations(customer, by_name, original) == []


def test_full_authorized_fields_recalled_despite_truncated_stored_document(cached_context, monkeypatch):
    ctx, source = cached_context
    late = {"tableName": "t_business_records", "tableComment": "业务记录", "columns": [
        {"columnName": f"misc_{index}", "comment": "辅助说明"} for index in range(60)] + [
        {"columnName": "receivable_balance", "comment": "应收余额"}]}
    others = [{"tableName": f"t_misc_{index}", "tableComment": "业务记录", "columns": []} for index in range(16)]
    documents = [SourceSemanticDocument(table_name=late["tableName"],
        document_text=semantic._metadata_text(late), relations_json=[], embedding_json=[1.0])]
    assert "应收" not in documents[0].document_text
    ctx.db.scalars = lambda *args: SimpleNamespace(all=lambda: documents)
    selected, evidence = semantic.recall_tables(ctx, source, [late, *others], "应收余额", seed_k=1, cached_only=True)
    assert selected[0]["tableName"] == late["tableName"] and evidence[0]["lexicalScore"] > 0
    bounded = _bounded_schema(selected, "应收余额")[0]
    assert "receivable_balance" in {column["columnName"] for column in bounded["columns"]}
    assert bounded["truncatedSchema"] is True


def test_prompt_keeps_late_requested_amount_and_date_after_required_keys(monkeypatch):
    monkeypatch.setattr(settings, "ANALYTICS_NL_MAX_COLUMNS_PER_TABLE", 6)
    columns = [{"columnName": "autoId", "primaryKey": True}, {"columnName": "isDeleted"}]
    columns += [{"columnName": f"note_{index}", "comment": "辅助备注"} for index in range(70)]
    columns += [{"columnName": "c_balance", "comment": "应收余额"},
                {"columnName": "c_confirmed", "comment": "收入确认日期"},
                {"columnName": "recordKey", "comment": "id主键", "nullable": False, "dataType": "CHAR(40)"}]
    result = _bounded_schema([{"tableName": "t_business", "columns": columns}], "按收入确认日期统计应收余额")[0]
    assert {"autoId", "isDeleted", "recordKey", "c_balance", "c_confirmed"}.issubset(
        {column["columnName"] for column in result["columns"]})


def test_receivable_title_outranks_payee_field_and_compound_topics_keep_both_sides(legacy_tables, cached_context):
    ctx, source = cached_context
    payment = next(table for table in legacy_tables if table["tableName"] == "t_fundpaymentapprovalform")
    receivables = [{"tableName": f"t_receivables_{index}", "tableComment": "应收收款明细",
                    "columns": [{"columnName": "balance", "comment": "应收余额"}]} for index in range(12)]
    selected, _ = semantic.recall_tables(ctx, source, [payment, *receivables], "收款情况", seed_k=1, cached_only=True)
    assert selected[0]["tableName"].startswith("t_receivables_")
    selected, _ = semantic.recall_tables(ctx, source, [payment, *receivables], "应收收款明细以及付款",
                                         seed_k=2, max_extra=0, cached_only=True)
    assert len(selected) == 2 and payment["tableName"] in {table["tableName"] for table in selected}
    assert any(table["tableName"].startswith("t_receivables_") for table in selected)


def test_schema_reports_full_cache_search_separately_from_candidate_coverage(legacy_tables, cached_context, monkeypatch):
    ctx, source = cached_context
    tables = legacy_tables + [{"tableName": f"t_business_{index}", "columns": [{"columnName": "id"}]}
                             for index in range(20)]
    tables.append({"tableName": "t_meta_entity", "columns": [{"columnName": "name"}]})
    ctx.db.scalar = lambda *args: SimpleNamespace(status="ready", source_version=1, tables_json=tables)
    requested_permissions = []
    monkeypatch.setattr(business_catalog, "require_permission", lambda ctx, permission: requested_permissions.append(permission))
    result = business_catalog.business_schema(ctx, source, "应收财务")
    coverage = result["coverage"]
    assert requested_permissions == ["analytics.execute"]
    assert coverage["authorizedTableCount"] == 27 and coverage["searchedTableCount"] == 26
    assert coverage["searchedFieldCount"] == 53
    assert coverage["returnedTableCount"] == len(result["tables"]) < coverage["searchedTableCount"]
    assert coverage["allSearchableMetadataScanned"] is True and coverage["candidateListTruncated"] is True
    assert coverage["businessDataQueried"] is False and coverage["canProveDataAbsence"] is False
    assert "没有专用表不能证明业务信息不存在" in result["guidance"]
    assert "付款表的收款单位" in result["guidance"]
