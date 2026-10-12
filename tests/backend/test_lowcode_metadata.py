"""低代码元数据与提示压缩的纯数据、假连接测试，不访问真实业务库。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.config import settings
from app.modules.analytics import lowcode_metadata as lowcode
from app.modules.analytics.queries import _bounded_schema


def business_tables():
    """只授权三个业务字段，不给同一实体的隐藏字段扩权。"""
    return [{"tableName": "t_stock", "columns": [
        {"columnName": "stockId", "dataType": "char(40)", "primaryKey": False},
        {"columnName": "c_name", "dataType": "varchar(200)"},
        {"columnName": "c_unit", "dataType": "smallint"},
    ]}]


ENTITIES = [{"entityCode": 101, "name": "Stock", "physicalName": "t_stock"}]
FIELDS = [
    {"entityCode": 101, "name": "stockId", "physicalName": "stockId", "type": "PrimaryKey", "idFieldFlag": 1, "nameFieldFlag": 0},
    {"entityCode": 101, "name": "name", "physicalName": "c_name", "type": "Text", "idFieldFlag": 0, "nameFieldFlag": 1},
    {"entityCode": 101, "name": "unit", "physicalName": "c_unit", "type": "Option", "idFieldFlag": 0, "nameFieldFlag": 0,
     "fieldViewModel": '{"optionSyncModel":{"entityName":"ProductType","fieldName":"meterage"}}'},
]
OPTIONS = [{"entityName": "ProductType", "fieldName": "meterage", "value": 5, "label": "升"},
           {"entityName": "ProductType", "fieldName": "meterage", "value": 2, "label": "千克"}]


def columns(tables):
    """按真实字段名检查合并结果。"""
    return {column["columnName"]: column for column in tables[0]["columns"]}


def test_merge_retains_field_types_flags_and_real_synced_units_without_mutation():
    tables = business_tables()
    original = deepcopy(tables)
    merged = lowcode.merge_lowcode_metadata(tables, ENTITIES, FIELDS, OPTIONS)
    result = columns(merged)
    assert result["stockId"]["businessKey"] and not result["stockId"]["primaryKey"]
    assert result["c_name"]["nameField"]
    assert result["c_unit"]["dataType"] == "smallint"
    assert result["c_unit"]["enumValues"] == [{"value": "2", "label": "千克"}, {"value": "5", "label": "升"}]
    assert result["c_unit"]["enumComplete"]
    assert result["c_unit"]["enumSource"]["entityName"] == "ProductType"
    assert tables == original


def test_merge_never_adds_hidden_fields_or_unavailable_tables():
    fields = FIELDS + [{**FIELDS[2], "physicalName": "c_hidden"}, {**FIELDS[0], "entityCode": 102}]
    entities = ENTITIES + [{"entityCode": 102, "name": "Secret", "physicalName": "t_secret"}]
    result = lowcode.merge_lowcode_metadata(business_tables(), entities, fields, OPTIONS)
    assert list(columns(result)) == ["stockId", "c_name", "c_unit"]
    assert len(result) == 1


@pytest.mark.parametrize("value, expected", [(0, False), ("0", False), (None, False), ("false", False), (1, True), ("1", True)])
def test_merge_flags_do_not_treat_text_zero_as_true(value, expected):
    result = columns(lowcode.merge_lowcode_metadata(business_tables(), ENTITIES,
        [{**FIELDS[0], "idFieldFlag": value, "nameFieldFlag": value}], []))["stockId"]
    assert result["businessKey"] is expected and result["nameField"] is expected


@pytest.mark.parametrize("view", ["{invalid", "[]", '"plain"', [], [1],
    {"optionSyncModel": []}, {"optionSyncModel": "broken"}, {"optionSyncModel": {"entityName": "ProductType"}}])
def test_bad_json_and_partial_sync_do_not_fall_back_to_wrong_dictionary(view):
    local_options = [{"entityName": "Stock", "fieldName": "unit", "value": 5, "label": "错误单位"}]
    result = columns(lowcode.merge_lowcode_metadata(business_tables(), ENTITIES,
        [{**FIELDS[2], "fieldViewModel": view}], OPTIONS + local_options))["c_unit"]
    assert result["enumValues"] == [] and not result["enumComplete"]
    assert result["enumSource"] == {}


def test_unsynced_option_uses_its_own_declared_dictionary():
    local_options = [{"entityName": "Stock", "fieldName": "unit", "value": 5, "label": "本地单位"}]
    result = columns(lowcode.merge_lowcode_metadata(business_tables(), ENTITIES,
        [{**FIELDS[2], "fieldViewModel": None}], OPTIONS + local_options))["c_unit"]
    assert result["enumValues"] == [{"value": "5", "label": "本地单位"}]


def test_duplicate_and_malformed_options_do_not_create_false_unit_labels():
    options = OPTIONS + [OPTIONS[0], {**OPTIONS[0], "label": "桶"},
                         {**OPTIONS[1], "value": 9, "label": "支"},
                         {**OPTIONS[1], "value": 9, "label": None},
                         {**OPTIONS[1], "value": None}]
    result = columns(lowcode.merge_lowcode_metadata(business_tables(), ENTITIES, FIELDS, options))["c_unit"]
    assert result["enumValues"] == [{"value": "2", "label": "千克"}]
    assert result["enumComplete"] is False


def test_conflicting_entity_codes_do_not_assign_arbitrary_business_metadata():
    tables = business_tables() + [{"tableName": "t_other", "columns": [{"columnName": "stockId"}]}]
    entities = ENTITIES + [{"entityCode": 101, "name": "Other", "physicalName": "t_other"}]
    result = lowcode.merge_lowcode_metadata(tables, entities, FIELDS, OPTIONS)
    assert "businessKey" not in columns(result)["stockId"]
    assert "businessKey" not in result[1]["columns"][0]


def schema_with_metadata(*, options=True):
    """构造元表的授权字段目录，不访问连接。"""
    fields = {
        "t_meta_entity": ["entityCode", "name", "physicalName"],
        "t_meta_field": ["entityCode", "name", "physicalName", "type", "idFieldFlag", "nameFieldFlag", "fieldViewModel"],
    }
    if options:
        fields["t_option_item"] = ["entityName", "fieldName", "value", "label"]
    return business_tables() + [{"tableName": name, "columns": [{"columnName": column} for column in names]}
                                for name, names in fields.items()]


def fake_connection(monkeypatch, replies):
    """记录实际 SQL 和参数，任何额外查询都会耗尽队列并失败。"""
    calls = []
    pending = iter(replies)

    class Connection:
        def execute(self, statement, parameters):
            calls.append((str(statement), parameters))
            response = next(pending)
            if isinstance(response, Exception):
                raise response
            return SimpleNamespace(mappings=lambda: response)

    @contextmanager
    def readonly(*args):
        yield Connection()

    monkeypatch.setattr(lowcode, "readonly_connection", readonly)
    return calls


@pytest.mark.parametrize("provider", ["mysql", "sqlite"])
def test_missing_metadata_or_non_mysql_never_opens_connection(monkeypatch, provider):
    def forbidden(*args):
        raise AssertionError("元数据不满足条件时不得连接")
    monkeypatch.setattr(lowcode, "readonly_connection", forbidden)
    tables = business_tables() if provider == "mysql" else schema_with_metadata()
    assert lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider=provider), tables) is tables


def test_sync_does_not_read_options_for_hidden_business_fields(monkeypatch):
    hidden_field = {**FIELDS[2], "physicalName": "c_hidden", "name": "secret", "fieldViewModel": None}
    calls = fake_connection(monkeypatch, [ENTITIES, FIELDS + [hidden_field], OPTIONS])
    result = lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider="mysql"), schema_with_metadata())
    assert len(calls) == 3
    assert set(calls[-1][1].values()) == {"ProductType", "meterage"}
    assert "c_hidden" not in columns(result)


def test_sync_missing_option_table_still_marks_authorized_business_keys(monkeypatch):
    calls = fake_connection(monkeypatch, [ENTITIES, FIELDS])
    result = lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider="mysql"), schema_with_metadata(options=False))
    assert len(calls) == 2 and columns(result)["stockId"]["businessKey"]
    assert columns(result)["c_unit"]["enumValues"] == []
    assert not columns(result)["c_unit"]["enumComplete"]


def test_missing_required_metadata_field_never_opens_connection(monkeypatch):
    tables = schema_with_metadata()
    tables[1]["columns"] = [{"columnName": "entityCode"}, {"columnName": "name"}]
    calls = fake_connection(monkeypatch, [])
    assert lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider="mysql"), tables) is tables
    assert calls == []


def test_hidden_option_label_is_not_queried(monkeypatch):
    tables = schema_with_metadata()
    tables[-1]["columns"] = [column for column in tables[-1]["columns"] if column["columnName"] != "label"]
    calls = fake_connection(monkeypatch, [ENTITIES, FIELDS])
    result = lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider="mysql"), tables)
    assert len(calls) == 2 and columns(result)["c_unit"]["enumValues"] == []


@pytest.mark.parametrize("replies", [
    [ENTITIES * 2001], [ENTITIES, [FIELDS[0]] * 20001], [ENTITIES, FIELDS, [OPTIONS[0]] * 20001],
])
def test_truncated_metadata_never_claims_complete_dictionary(monkeypatch, replies):
    tables = schema_with_metadata()
    calls = fake_connection(monkeypatch, replies)
    assert lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider="mysql"), tables) is tables
    assert len(calls) == len(replies)


def test_sync_failure_does_not_retry_or_continue(monkeypatch):
    calls = fake_connection(monkeypatch, [RuntimeError("测试连接失败")])
    with pytest.raises(RuntimeError, match="测试连接失败"):
        lowcode.enrich_lowcode_metadata(None, SimpleNamespace(provider="mysql"), schema_with_metadata())
    assert len(calls) == 1


def test_bounded_schema_preserves_business_join_status_name_and_unit_fields(monkeypatch):
    monkeypatch.setattr(settings, "ANALYTICS_NL_MAX_COLUMNS_PER_TABLE", 6)
    fields = [{"columnName": f"f{i}"} for i in range(45)] + [
        {"columnName": "unit", "comment": "计量单位"}, {"columnName": "displayName", "nameField": True},
        {"columnName": "isDeleted"}, {"columnName": "warehouseId"},
        {"columnName": "stockId", "businessKey": True}, {"columnName": "autoId", "primaryKey": True},
    ]
    tables = [{"tableName": "stock", "columns": fields, "foreignKeys": [
        {"columns": ["warehouseId"], "referredTable": "warehouse", "referredColumns": ["warehouseId"]}]}]
    original = deepcopy(tables)
    result = _bounded_schema(tables)[0]
    assert {c["columnName"] for c in result["columns"]} == {"autoId", "stockId", "warehouseId", "isDeleted", "displayName", "unit"}
    assert result["truncatedSchema"] and tables == original


def test_bounded_schema_keeps_incoming_declared_relation_key_and_marks_enum_truncation(monkeypatch):
    monkeypatch.setattr(settings, "ANALYTICS_NL_MAX_TABLES", 1)
    monkeypatch.setattr(settings, "ANALYTICS_NL_MAX_COLUMNS_PER_TABLE", 2)
    tables = [{"tableName": "target", "columns": [{"columnName": f"f{i}"} for i in range(45)] + [
        {"columnName": "unit", "enumValues": [{"value": str(i), "label": str(i)} for i in range(20)], "enumComplete": True},
        {"columnName": "targetKey"},
    ]}, {"tableName": "source", "columns": [], "declaredRelations": [
        {"sourceTable": "source", "sourceField": "reference", "targetTable": "target", "targetField": "targetKey"}]}]
    result = _bounded_schema(tables)
    assert len(result) == 1
    columns_by_name = {c["columnName"]: c for c in result[0]["columns"]}
    assert set(columns_by_name) == {"targetKey", "unit"}
    assert len(columns_by_name["unit"]["enumValues"]) == 12
    assert columns_by_name["unit"]["enumTruncated"] and not columns_by_name["unit"]["enumComplete"]
    assert tables[0]["columns"][-2]["enumComplete"] and len(tables[0]["columns"][-2]["enumValues"]) == 20
