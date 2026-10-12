"""低代码关系同步与缓存合同；只用临时平台库和本地连接桩，不读取真实业务库。"""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import DataSource
from app.modules.analytics import metadata, semantic
from app.modules.analytics.models import SourceMetadata, SourceSemanticDocument
from test_analytics import source


def relation_tables():
    """自增键与业务键并存，验证关联使用实体标识。"""
    return [
        {"tableName": "stock", "columns": [{"columnName": "warehouse_ref"}]},
        {"tableName": "warehouse", "columns": [
            {"columnName": "autoId", "primaryKey": True},
            {"columnName": "warehouseId", "businessKey": True},
            {"columnName": "warehouseName", "nameField": True}]},
        {"tableName": "t_meta_entity", "columns": [{"columnName": name} for name in
            ("entityCode", "name", "physicalName")]},
        {"tableName": "t_meta_field", "columns": [{"columnName": name} for name in
            ("entityCode", "name", "physicalName", "type", "referTo")]},
    ]


ENTITIES = [{"entityCode": 1, "name": "Stock", "physicalName": "stock"},
            {"entityCode": 2, "name": "Warehouse", "physicalName": "warehouse"}]
FIELDS = [{"entityCode": 1, "name": "warehouse", "physicalName": "warehouse_ref",
           "type": "Reference", "referTo": "Warehouse"}]


def fake_connection(monkeypatch, replies):
    """严格按查询队列响应；多一次重试就会使测试失败。"""
    calls, pending = [], iter(replies)

    class Connection:
        def exec_driver_sql(self, sql):
            calls.append(sql)
            value = next(pending)
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: value))

    @contextmanager
    def readonly(*args):
        yield Connection()

    monkeypatch.setattr(semantic, "readonly_connection", readonly)
    return calls


@pytest.mark.parametrize("key_case", ["unique", "physical_only", "ambiguous", "explicit_hidden"])
def test_legacy_reference_requires_unique_authorized_business_key(key_case):
    tables = relation_tables()
    relation = {"sourceTable": "stock", "sourceField": "warehouse_ref", "targetTable": "warehouse",
                "targetField": "", "targetNameField": "hidden_name", "relationType": "reference"}
    if key_case == "physical_only":
        tables[1]["columns"][1].pop("businessKey")
    elif key_case == "ambiguous":
        tables[1]["columns"][0]["businessKey"] = True
    elif key_case == "explicit_hidden":
        relation["targetField"] = "hidden_key"
    result = semantic._scoped_declared_relations(tables[0], {table["tableName"]: table for table in tables}, [relation])
    if key_case == "unique":
        assert result[0]["targetField"] == "warehouseId"
        assert "targetNameField" not in result[0]
    else:
        assert result == []


def test_legacy_reverse_reference_repairs_name_field_to_business_key():
    tables = relation_tables()
    relation = {"sourceTable": "warehouse", "sourceField": "warehouseName", "targetTable": "stock",
                "targetField": "warehouse_ref", "relationType": "reference", "relationDirection": "reverse"}
    result = semantic._scoped_declared_relations(tables[1], {table["tableName"]: table for table in tables}, [relation])
    assert result[0]["sourceField"] == "warehouseId"


def test_sync_selects_available_metadata_fields_without_failing_then_retrying(monkeypatch):
    calls = fake_connection(monkeypatch, [ENTITIES, FIELDS])
    relations = semantic.extract_relations(None, SimpleNamespace(provider="mysql"), relation_tables())
    assert len(calls) == 2
    assert "NULL AS nameFieldFlag" in calls[1] and "NULL AS label" in calls[0]
    assert relations[0]["targetField"] == "warehouseId"
    assert relations[0]["sourceField"] == "warehouse_ref"


def test_declared_main_detail_relation_remains_recallable_without_invented_join_keys(monkeypatch):
    tables = relation_tables()
    tables[2]["columns"] += [{"columnName": "detailEntityFlag"}, {"columnName": "mainEntity"}]
    entities = [{**ENTITIES[0], "detailEntityFlag": 1, "mainEntity": "Warehouse"}, ENTITIES[1]]
    calls = fake_connection(monkeypatch, [entities, []])
    relations = semantic.extract_relations(None, SimpleNamespace(provider="mysql"), tables)
    assert len(relations) == 1 and len(calls) == 2
    assert relations[0]["sourceTable"] == "warehouse" and relations[0]["targetTable"] == "stock"
    assert relations[0]["joinKeysKnown"] is False
    assert not relations[0]["sourceField"] and not relations[0]["targetField"]


@pytest.mark.parametrize("stage", ["entities", "fields"])
@pytest.mark.parametrize("failure", [TimeoutError("业务查询超时"), ConnectionError("业务连接断开")])
def test_sync_read_failure_does_not_retry_or_degrade(monkeypatch, stage, failure):
    replies = [failure] if stage == "entities" else [ENTITIES, failure]
    calls = fake_connection(monkeypatch, replies)
    with pytest.raises(type(failure)) as captured:
        semantic.extract_relations(None, SimpleNamespace(provider="mysql"), relation_tables())
    assert captured.value is failure and len(calls) == len(replies)


def test_complete_sync_persists_business_key_relations_and_cached_recall(
    client, administrator, db, source, monkeypatch,
):
    row = db.get(DataSource, source["id"])
    row.provider = "mysql"
    tables = relation_tables()
    monkeypatch.setattr(metadata, "inspect_sql_source", lambda *args: deepcopy(tables))
    from app.modules.analytics import lowcode_metadata
    monkeypatch.setattr(lowcode_metadata, "enrich_lowcode_metadata", lambda _db, _source, value: value)
    monkeypatch.setattr(semantic.model, "embed_texts", lambda values, **kwargs: [[1.0, 0.0] for _ in values])
    calls = fake_connection(monkeypatch, [ENTITIES, FIELDS])
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    snapshot = metadata.source_metadata(ctx, row, refresh=True)
    db.commit()
    db.expire_all()
    persisted = db.get(SourceMetadata, snapshot.id)
    stock = next(table for table in persisted.tables_json if table["tableName"] == "stock")
    assert stock["declaredRelations"][0]["targetField"] == "warehouseId"
    recalled, _ = semantic.recall_tables(ctx, row, persisted.tables_json, "stock", seed_k=1, cached_only=True)
    assert {table["tableName"] for table in recalled} == {"stock", "warehouse"}
    reverse = next(table for table in recalled if table["tableName"] == "warehouse")["declaredRelations"]
    assert reverse[0]["sourceField"] == "warehouseId"
    # 同版本旧索引缺目标键时，缓存召回也必须补全且不得连接业务库。
    entry = db.scalar(select(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == row.id,
                                                           SourceSemanticDocument.table_name == "stock"))
    entry.relations_json = [{**entry.relations_json[0], "targetField": ""}]
    no_declared_tables = [{**table, "declaredRelations": []} for table in persisted.tables_json]
    recalled, _ = semantic.recall_tables(ctx, row, no_declared_tables, "stock", seed_k=1, cached_only=True)
    assert next(table for table in recalled if table["tableName"] == "stock")["declaredRelations"][0]["targetField"] == "warehouseId"
    assert len(calls) == 2


def test_source_metadata_propagates_relation_failure_before_model(
    client, administrator, db, source, monkeypatch,
):
    row = db.get(DataSource, source["id"])
    row.provider = "mysql"
    monkeypatch.setattr(metadata, "inspect_sql_source", lambda *args: relation_tables())
    from app.modules.analytics import lowcode_metadata
    monkeypatch.setattr(lowcode_metadata, "enrich_lowcode_metadata", lambda _db, _source, value: value)
    failure = TimeoutError("关系读取超时")
    calls = fake_connection(monkeypatch, [ENTITIES, failure])
    model_calls = []
    monkeypatch.setattr(semantic.model, "embed_texts", lambda *args, **kwargs: model_calls.append(True))
    with pytest.raises(TimeoutError) as captured:
        metadata.source_metadata(SimpleNamespace(db=db, workspace_id=row.workspace_id), row, refresh=True)
    assert captured.value is failure and len(calls) == 2 and not model_calls
