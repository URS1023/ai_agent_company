"""库存专用接口的真实 SQLite 验证，不访问业务库或模型。"""
from __future__ import annotations

import io
import sqlite3
from datetime import datetime

import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, select

from app.config import settings
from app.errors import ToolExecutionError
from app.modules.analytics import inventory, queries
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.models import QueryHistory, SourceMetadata, SourceSemanticDocument


API = "/api/v1/analytics"


@pytest.fixture
def inventory_source(client, administrator, monkeypatch, tmp_path, db):
    """真实字段名的本机临时库，包含孤立引用、空阈值及不同单位等反例。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    path = managed_database_path(administrator["X-Workspace-Id"], "inventory-fixture.sqlite3", must_exist=False)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE t_basicinformationofthewarehouse (
                basicInformationoftheWarehouseId TEXT PRIMARY KEY, isDeleted INTEGER,
                c_warehouseName TEXT, c_warehouseCode TEXT, c_warehouseAddress TEXT, c_warehouseStatus INTEGER
            );
            CREATE TABLE t_warehouselocationinformation (
                warehouseLocationInformationId TEXT PRIMARY KEY, isDeleted INTEGER,
                c_locationSeqNo TEXT, c_locationCode TEXT
            );
            CREATE TABLE t_sparepartsinfor (
                sparePartsInforId TEXT PRIMARY KEY, isDeleted INTEGER, c_productName TEXT,
                c_productcode TEXT, c_productModel TEXT, c_unit INTEGER,
                c_safetyStock NUMERIC, c_basicInformationoftheWarehouse TEXT
            );
            CREATE TABLE t_spinventory (
                spInventoryId TEXT PRIMARY KEY, isDeleted INTEGER, c_productId TEXT,
                c_warehouseId TEXT, c_locationId TEXT, c_batchNo TEXT,
                c_availableStock NUMERIC, c_unit INTEGER, c_productModel TEXT,
                FOREIGN KEY(c_productId) REFERENCES t_sparepartsinfor(sparePartsInforId),
                FOREIGN KEY(c_warehouseId) REFERENCES t_basicinformationofthewarehouse(basicInformationoftheWarehouseId),
                FOREIGN KEY(c_locationId) REFERENCES t_warehouselocationinformation(warehouseLocationInformationId)
            );
        """)
        connection.executemany("INSERT INTO t_basicinformationofthewarehouse VALUES (?,?,?,?,?,?)", [
            ("w1", 0, "一号仓库", "W-1", None, 1), ("w2", 0, "二号仓库", "W-2", "园区", 1),
            ("wd", 1, "已删除仓库", "W-D", None, 1),
        ])
        connection.execute("INSERT INTO t_warehouselocationinformation VALUES ('l1',0,'A-01','LOC-01')")
        connection.executemany("INSERT INTO t_sparepartsinfor VALUES (?,?,?,?,?,?,?,?)", [
            ("m1", 0, "46#机械油", "M-1", "46#", 5, None, "w1"),
            ("m2", 0, "圆钢", "M-2", "20mm", 2, 100, "w1"),
            ("m3", 0, "特殊%_件", "M-3", None, 999, None, "w2"),
            ("md", 1, "已删除物料", "M-D", None, 5, 99, "w1"),
        ])
        connection.executemany("INSERT INTO t_spinventory VALUES (?,?,?,?,?,?,?,?,?)", [
            ("s1", 0, "m1", "w1", "l1", "B1", 2, 5, "46#"),
            ("s2", 0, "m2", "w1", None, "B2", 120, 2, "20mm"),
            ("s3", 0, "m2", "w2", None, "B3", 90, 2, "20mm"),
            ("s4", 0, "m3", "w2", None, "B4", None, 999, None),
            ("s5", 0, "missing", "missing", None, "B5", 4, None, None),
            ("s6", 0, "md", "wd", None, "B6", 3, 5, None),
            ("s7", 1, "m1", "w1", None, "B7", 999, 5, None),
            ("s8", None, "m1", "w1", None, "B8", 999, 5, None),
        ])
    response = client.post(API + "/datasources", headers=administrator, json={
        "name": "库存接口验证库", "type": "sqlite", "databaseName": path.name,
        "allowedTables": [inventory.STOCK, inventory.MATERIAL, inventory.WAREHOUSE, inventory.LOCATION],
    })
    assert response.status_code == 201, response.text
    source = response.json()["data"]
    response = client.get(API + f"/datasources/{source['id']}/tables/info", headers=administrator)
    assert response.status_code == 200, response.text
    metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == source["id"]))
    tables = [{**table, "columns": [{**column, "enumValues": [{"value": "5", "label": "升"}, {"value": "2", "label": "千克"}]}
                                    if column["columnName"] == "c_unit" else column for column in table["columns"]]}
              for table in metadata.tables_json]
    metadata.tables_json = tables
    db.commit()
    return source


def call_inventory(client, administrator, source, **params):
    """通过公开接口验证 Worker 与工具复用的服务合同。"""
    return client.post(API + "/inventory/query", headers=administrator,
                       json={"datasourceId": source["id"], **params})


def test_inventory_keeps_batches_nulls_units_and_orphan_references(client, administrator, inventory_source, monkeypatch, db):
    calls = []
    real_execute = inventory.execute_query

    def execute(ctx, payload):
        calls.append(payload)
        return real_execute(ctx, payload)

    def no_model(*args, **kwargs):
        raise AssertionError("库存专用查询不能调用模型")

    monkeypatch.setattr(inventory, "execute_query", execute)
    monkeypatch.setattr(queries, "natural_language_sql", no_model)
    response = call_inventory(client, administrator, inventory_source)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert len(calls) == 1 and calls[0].mode == "sql"
    assert result["totalCount"] == 6
    # 确定性业务语义检查会登记元信息，不属于模型调用或 token 用量。
    assert set(result["modelUsage"]) <= {"businessSemantics"}
    assert result["statistics"]["quantityTotal"] is None
    assert datetime.fromisoformat(result["retrievalEvidence"]["capturedAt"]).utcoffset() is not None
    assert "库存记录标识" in result["data"][0], repr(result["data"][0])
    rows = {row["库存记录标识"]: row for row in result["data"]}
    assert rows["s1"]["安全库存"] is None and rows["s1"]["库存预警"] == "未知"
    assert rows["s1"]["库存计量单位编码"] == 5 and rows["s1"]["库存计量单位"] == "升"
    assert rows["s2"]["库存计量单位"] == "千克"
    assert rows["s1"]["仓库名称"] == "一号仓库" and rows["s1"]["仓位编码"] == "LOC-01"
    assert rows["s2"]["库存预警"] == rows["s3"]["库存预警"] == "待核对"
    assert rows["s2"]["批次号"] != rows["s3"]["批次号"]
    assert rows["s4"]["实际可用库存"] is None and rows["s4"]["库存计量单位"] is None
    assert rows["s5"]["物料标识"] == "missing" and rows["s5"]["备件名称"] is None
    assert rows["s6"]["备件名称"] is None and rows["s6"]["仓库名称"] is None
    assert not result["truncated"] and result["statistics"]["nextOffset"] is None
    stored = db.get(QueryHistory, result["queryId"])
    assert stored.result_json == result
    historical = client.get(API + "/history/" + result["queryId"], headers=administrator)
    assert historical.json()["data"] == result


def test_inventory_exact_filter_and_pagination(client, administrator, inventory_source):
    first = call_inventory(client, administrator, inventory_source, pageSize=2).json()["data"]
    assert first["truncated"] and first["statistics"]["nextOffset"] == 2
    second = call_inventory(client, administrator, inventory_source, pageSize=2, offset=2).json()["data"]
    assert [r["库存记录标识"] for r in first["data"] + second["data"]] == ["s1", "s2", "s3", "s4"]
    result = call_inventory(client, administrator, inventory_source, materialId="m2", warehouseId="w2", batchNumber="B3").json()["data"]
    assert [row["库存记录标识"] for row in result["data"]] == ["s3"]
    assert {key: value for key, value in result["parameters"].items() if not key.startswith("unit_")} == {
        "material_id": "m2", "warehouse_id": "w2", "batch_number": "B3"}


def test_inventory_literal_name_and_catalogs(client, administrator, inventory_source):
    result = call_inventory(client, administrator, inventory_source, name="%_").json()["data"]
    assert [row["库存记录标识"] for row in result["data"]] == ["s4"]
    assert call_inventory(client, administrator, inventory_source, name="' OR 1=1 --").json()["data"]["data"] == []
    warehouses = call_inventory(client, administrator, inventory_source, kind="warehouses", name="一号").json()["data"]
    assert [row["仓库标识"] for row in warehouses["data"]] == ["w1"]
    assert "t_spinventory" not in warehouses["generatedSql"]
    materials = call_inventory(client, administrator, inventory_source, kind="materials", materialId="m1").json()["data"]
    assert materials["data"][0]["计量单位"] == "升"
    assert "t_spinventory" not in materials["generatedSql"]


@pytest.mark.parametrize("params", [
    {"kind": "warehouses", "materialId": "m1"}, {"kind": "materials", "warehouseId": "w1"},
    {"kind": "materials", "batchNumber": "B1"}, {"offset": -1}, {"pageSize": 1001},
    {"dateFrom": "2026-09-01"},
])
def test_inventory_rejects_unsupported_filters(client, administrator, inventory_source, params):
    response = call_inventory(client, administrator, inventory_source, **params)
    assert response.status_code == 422, response.text


def test_inventory_does_not_guess_missing_relations(client, administrator, inventory_source, db):
    metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == inventory_source["id"]))
    # 同步后快照也保存声明关系；此反例需移除两种来源，才能代表完全没有关系依据。
    metadata.tables_json = [{**table, "foreignKeys": [], "declaredRelations": []} for table in metadata.tables_json]
    db.execute(delete(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == inventory_source["id"]))
    db.commit()
    result = call_inventory(client, administrator, inventory_source).json()["data"]
    assert result["totalCount"] == 6
    assert all(row["备件名称"] is None and row["仓库名称"] is None for row in result["data"])
    assert all(item["status"] == "unavailable" for item in result["retrievalEvidence"]["relations"])
    response = call_inventory(client, administrator, inventory_source, name="机械油")
    assert response.status_code == 422 and "筛选" in response.text


def test_inventory_rejects_hidden_filter_column(client, administrator, inventory_source, db):
    metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == inventory_source["id"]))
    metadata.tables_json = [{**table, "columns": [column for column in table["columns"] if column["columnName"] != "c_batchNo"]}
                            for table in metadata.tables_json]
    db.commit()
    response = call_inventory(client, administrator, inventory_source, batchNumber="B1")
    assert response.status_code == 422 and "筛选" in response.text


def test_inventory_failure_stops_without_retry(client, administrator, inventory_source, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ToolExecutionError("测试的数据库执行失败")

    monkeypatch.setattr(queries, "execute_read", fail)
    response = call_inventory(client, administrator, inventory_source)
    assert response.status_code == 502 and calls == [1]


def test_inventory_export_uses_same_values(client, administrator, inventory_source):
    result = call_inventory(client, administrator, inventory_source, warehouseId="w1").json()["data"]
    response = client.post(API + "/export", headers=administrator, json={"queryId": result["queryId"], "format": "xlsx"})
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content))
    exported = list(book.worksheets[0].values)
    assert list(exported[0]) == [column["columnName"] for column in result["columns"]]
    assert len(exported) - 1 == len(result["data"])
    assert list(exported[1]) == [result["data"][0].get(column) for column in exported[0]]


def test_inventory_missing_snapshot_does_not_introspect_or_query(client, administrator, inventory_source, db, monkeypatch):
    """没有同步结构时明确返回补齐步骤，不能为一次取数隐式扫描全库。"""
    from app.modules.analytics import metadata

    db.execute(delete(SourceMetadata).where(SourceMetadata.datasource_id == inventory_source["id"]))
    db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("无有效快照时不允许连接业务库")

    monkeypatch.setattr(metadata, "inspect_sql_source", forbidden)
    monkeypatch.setattr(inventory, "execute_query", forbidden)
    response = call_inventory(client, administrator, inventory_source)
    assert response.status_code == 422 and "同步元数据" in response.text
