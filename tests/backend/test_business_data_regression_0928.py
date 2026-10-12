"""内部数据接口边界回归：使用临时 SQLite，覆盖复合关系和分页范围。"""
from copy import deepcopy
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import DataSource
from app.modules.analytics import inventory, metadata, semantic
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.models import SourceMetadata, SourceSemanticDocument
from app.modules.business_ai.service import _discovery_table_summary
from test_analytics import source
from test_inventory_query import inventory_source, call_inventory


API = "/api/v1/analytics"


def compound_tables():
    """物料编码在不同组织可重复，连接必须同时使用组织标识。"""
    return [
        {"tableName": "stock", "columns": [{"columnName": name} for name in ("material", "org")],
         "foreignKeys": [{"columns": ["material", "org"], "referredTable": "material",
                          "referredColumns": ["id", "org"]}]},
        {"tableName": "material", "columns": [{"columnName": name} for name in ("id", "org")]},
    ]


def test_relation_sync_keeps_compound_keys_together():
    relations = semantic.extract_relations(None, SimpleNamespace(provider="sqlite"), compound_tables())
    assert len(relations) == 1
    assert relations[0]["columns"] == ["material", "org"]
    assert relations[0]["targetColumns"] == ["id", "org"]
    assert not relations[0]["sourceField"] and not relations[0]["targetField"]


def test_existing_split_relation_cache_is_repaired_without_database():
    tables = compound_tables()
    legacy = [{"sourceTable": "stock", "targetTable": "material", "relationType": "foreign_key",
               "sourceField": left, "targetField": right} for left, right in [("material", "id"), ("org", "org")]]
    repaired = semantic._scoped_declared_relations(tables[0], {table["tableName"]: table for table in tables}, legacy)
    assert len(repaired) == 1 and repaired[0]["columns"] == ["material", "org"]
    assert repaired[0]["targetColumns"] == ["id", "org"]


def test_partial_compound_key_is_not_exposed_as_usable_relation():
    tables = compound_tables()
    config = {"allowedColumns": {"stock": ["material"]}}
    result = metadata._column_scope(SimpleNamespace(connection_config=config), tables)
    assert result[0]["foreignKeys"] == []
    assert semantic.extract_relations(None, SimpleNamespace(provider="sqlite"), result) == []


@pytest.mark.parametrize("failure", [TimeoutError("统计读取超时"), ConnectionError("统计连接中断")])
def test_metadata_statistics_failure_stops_before_any_followup_query(monkeypatch, failure):
    calls = []

    class Connection:
        def exec_driver_sql(self, statement):
            calls.append(statement)
            raise failure

    @contextmanager
    def readonly(*args):
        yield Connection()

    def forbidden(*args):
        raise AssertionError("统计故障后不能继续查询表结构")

    monkeypatch.setattr(metadata, "readonly_connection", readonly)
    monkeypatch.setattr(metadata, "inspect", lambda connection: SimpleNamespace(get_table_names=forbidden))
    with pytest.raises(type(failure)) as captured:
        metadata.inspect_sql_source(None, SimpleNamespace(provider="mysql"))
    assert captured.value is failure and len(calls) == 1


def test_metadata_statistics_permission_denied_still_allows_schema(monkeypatch):
    from pymysql.err import OperationalError

    @contextmanager
    def readonly(*args):
        def denied(statement):
            raise OperationalError(1142, "统计查询未授权")
        yield SimpleNamespace(exec_driver_sql=denied)

    monkeypatch.setattr(metadata, "readonly_connection", readonly)
    monkeypatch.setattr(metadata, "inspect", lambda connection: SimpleNamespace(
        get_table_names=lambda: [], get_view_names=lambda: []))
    assert metadata.inspect_sql_source(None, SimpleNamespace(provider="mysql", connection_config={})) == []


def test_cached_relation_refresh_preserves_reverse_compound_direction(client, administrator, db, source, monkeypatch):
    tables = compound_tables()
    row = db.get(DataSource, source["id"])
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    monkeypatch.setattr(semantic.model, "embed_texts", lambda docs, **kwargs: [[] for _ in docs])
    semantic.build_semantic_index(ctx, row, tables)
    semantic.build_semantic_index(ctx, row, tables, cached_only=True)
    reverse = db.scalar(select(SourceSemanticDocument).where(
        SourceSemanticDocument.datasource_id == row.id, SourceSemanticDocument.table_name == "material"))
    assert len(reverse.relations_json) == 1
    assert reverse.relations_json[0]["relationDirection"] == "reverse"
    assert reverse.relations_json[0]["columns"] == ["id", "org"]
    assert reverse.relations_json[0]["targetColumns"] == ["material", "org"]


@pytest.fixture
def compound_source(client, administrator, monkeypatch, tmp_path):
    """通过公开创建及同步接口登记两组织同编码的真实本机临时表。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    path = managed_database_path(administrator["X-Workspace-Id"], "compound-inventory.sqlite3", must_exist=False)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE t_sparepartsinfor (
                sparePartsInforId TEXT, c_orgId TEXT, isDeleted INTEGER, c_productName TEXT,
                PRIMARY KEY(sparePartsInforId, c_orgId)
            );
            CREATE TABLE t_spinventory (
                spInventoryId TEXT PRIMARY KEY, isDeleted INTEGER, c_productId TEXT, c_orgId TEXT,
                c_availableStock NUMERIC,
                FOREIGN KEY(c_productId, c_orgId) REFERENCES t_sparepartsinfor(sparePartsInforId, c_orgId)
            );
            INSERT INTO t_sparepartsinfor VALUES ('m1','orgA',0,'甲组织备件'), ('m1','orgB',0,'乙组织备件');
            INSERT INTO t_spinventory VALUES ('s1',0,'m1','orgA',2), ('s2',0,'m1','orgB',3);
        """)
    response = client.post(API + "/datasources", headers=administrator, json={
        "name": "复合关系验证库", "type": "sqlite", "databaseName": path.name,
        "allowedTables": [inventory.STOCK, inventory.MATERIAL],
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_inventory_after_sync_preserves_one_row_per_stock_and_correct_organization(
    client, administrator, compound_source,
):
    for _ in range(2):
        refreshed = client.post(API + f"/datasources/{compound_source['id']}/sync", headers=administrator)
        assert refreshed.status_code == 200, refreshed.text
        response = call_inventory(client, administrator, compound_source, pageSize=1)
        assert response.status_code == 200, response.text
        first = response.json()["data"]
        second = call_inventory(client, administrator, compound_source, pageSize=1,
                                offset=first["statistics"]["nextOffset"]).json()["data"]
        assert [(row["库存记录标识"], row["备件名称"]) for row in first["data"] + second["data"]] == [
            ("s1", "甲组织备件"), ("s2", "乙组织备件")]
        assert not second["truncated"] and second["statistics"]["nextOffset"] is None


def test_inventory_source_limit_last_and_out_of_range_pages(client, administrator, inventory_source, db):
    row = db.get(DataSource, inventory_source["id"])
    row.connection_config = {**row.connection_config, "maxRows": 2}
    db.commit()
    pages = [call_inventory(client, administrator, inventory_source, pageSize=5, offset=offset).json()["data"]
             for offset in (0, 2, 4, 6, 100)]
    assert [[r["库存记录标识"] for r in page["data"]] for page in pages] == [
        ["s1", "s2"], ["s3", "s4"], ["s5", "s6"], [], []]
    assert [page["statistics"]["nextOffset"] for page in pages] == [2, 4, None, None, None]
    assert all(page["statistics"]["pageSize"] == 2 for page in pages)
    assert all(page["statistics"]["totalCountKnown"] is False for page in pages)


def test_inventory_unit_consistency_does_not_claim_uniform_units_across_rows(
    client, administrator, inventory_source, db,
):
    """四条记录各自字段一致，但两桶两根必须明确保留为两种单位。"""
    path = managed_database_path(administrator["X-Workspace-Id"], inventory_source["databaseName"])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE t_sparepartsinfor SET c_unit=5 WHERE sparePartsInforId='m3'")
        connection.execute("UPDATE t_spinventory SET c_unit=5 WHERE spInventoryId='s4'")
    snapshot = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == inventory_source["id"]))
    snapshot.tables_json = [{**table, "columns": [{**column, "enumValues": [
        {"value": "5", "label": "桶"}, {"value": "2", "label": "根"}]}
        if column["columnName"] == "c_unit" else column for column in table["columns"]]}
        for table in snapshot.tables_json]
    db.commit()
    response = call_inventory(client, administrator, inventory_source, pageSize=4)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    statistics = result["statistics"]
    assert statistics["unitConsistency"] == {"一致": 4}
    assert "逐条比较同一库存记录" in statistics["unitConsistencyScope"]
    for field in ("库存计量单位", "物料计量单位"):
        assert statistics["unitCounts"][field] == [{"unit": "桶", "rowCount": 2}, {"unit": "根", "rowCount": 2}]
    assert "不是库存数量合计" in statistics["unitCountsScope"]
    assert "不代表所有记录单位相同" in result["analysis"] and statistics["quantityTotal"] is None


def test_inventory_unknown_units_remain_separate_in_unit_distribution(client, administrator, inventory_source):
    response = call_inventory(client, administrator, inventory_source)
    assert response.status_code == 200, response.text
    counts = response.json()["data"]["statistics"]["unitCounts"]
    assert counts["库存计量单位"] == [
        {"unit": "升", "rowCount": 2}, {"unit": "千克", "rowCount": 2}, {"unit": None, "rowCount": 2}]
    assert counts["物料计量单位"] == [
        {"unit": "升", "rowCount": 1}, {"unit": "千克", "rowCount": 2}, {"unit": None, "rowCount": 3}]


def test_discovery_truncated_enum_cannot_claim_full_dictionary():
    table = {"tableName": "stock", "columns": [{"columnName": "unit", "enumComplete": True,
        "enumValues": [{"value": str(i), "label": str(i)} for i in range(31)]}]}
    original = deepcopy(table)
    column = _discovery_table_summary(table, [table])["fields"][0]
    assert len(column["enumValues"]) == 30 and column["enumTruncated"]
    assert not column["enumComplete"] and table == original


def test_discovery_stale_schema_cannot_be_used_as_automatic_source(client, administrator, db, source, monkeypatch):
    row = db.get(DataSource, source["id"])
    row.connection_config = {**row.connection_config, "version": row.connection_config["version"] + 1}
    db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("目录接口不能为过期快照隐式联网同步")

    monkeypatch.setattr(semantic, "readonly_connection", forbidden)
    response = client.post("/api/v1/ai/business-actions/data-sources/discover", headers=administrator, json={})
    assert response.status_code == 200, response.text
    found = next(item for item in response.json()["data"]["sources"] if item["id"] == row.id)
    assert found["metadataStatus"] == "stale"
    assert found["automaticReady"] is False and found["tables"] == [] and found["returnedTableCount"] == 0
