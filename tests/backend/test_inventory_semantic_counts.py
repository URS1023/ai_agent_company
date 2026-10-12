"""库存物料实体计数与结算的真实 API、Worker 回归，仅使用临时 SQLite。"""
import json
import sqlite3

import pytest
from sqlalchemy import select

from app.modules.analytics import inventory, queries
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.models import QueryHistory, SourceMetadata
from app.providers import model
from business_worker_fakes import isolated_dsh_worker
from test_business_data_regression_0928 import compound_source
from test_inventory_evidence import UNSUPPORTED, prepare_inventory, read_inventory
from test_inventory_query import API, call_inventory, inventory_source


@pytest.fixture
def bounded_inventory_reads(monkeypatch):
    """每例最多两次真实库存 SELECT，禁止自然语言规划与隐式补查。"""
    calls = []
    execute = queries.execute_read

    def read(*args, **kwargs):
        assert len(calls) < 2, "库存统计不得增加业务查询"
        calls.append(str(args[2] if len(args) > 2 else kwargs))
        return execute(*args, **kwargs)

    def no_model(*args, **kwargs):
        raise AssertionError("库存统计不得调用模型规划")

    monkeypatch.setattr(queries, "execute_read", read)
    monkeypatch.setattr(queries, "natural_language_sql", no_model)
    monkeypatch.setattr(model, "chat_for_run", no_model)
    monkeypatch.setattr(model, "stream_chat", no_model)
    return calls


def emit_receipts(label, **receipts):
    """输出正式接口原始回执，供单次红绿执行记录留存。"""
    print("\nB17_RAW " + json.dumps({"case": label, **receipts}, ensure_ascii=True, default=str))


def read_api_and_worker(client, headers, source, worker, db, *, api_params=None, worker_params=None):
    """两条正式路径各查一次，并先保存原始结果再作语义断言。"""
    response = call_inventory(client, headers, source, pageSize=20, **(api_params or {}))
    assert response.status_code == 200, response.text
    api = response.json()["data"]
    cid, run = prepare_inventory(client, headers, source, worker)
    receipt = read_inventory(worker, run, source, page_size=20, **(worker_params or {}))
    assert receipt["status"] == "success", receipt
    settled = worker.settle(run["run_id"], "completed", UNSUPPORTED)
    emit_receipts("api-worker", api=api, worker=receipt, settled=settled)
    view = receipt["data"]
    assert db.get(QueryHistory, api["queryId"]).result_json == api
    assert db.get(QueryHistory, view["queryId"]).result_json == view
    for result in (api, view):
        historical = client.get(API + "/history/" + result["queryId"], headers=headers)
        assert historical.status_code == 200, historical.text
        assert historical.json()["data"] == result
    assert api["statistics"] == view["statistics"]
    assert worker.history(headers, cid)["messages"][-1]["content"] == settled["content"]
    assert set(api["modelUsage"]) <= {"businessSemantics"}
    assert set(view["modelUsage"]) <= {"businessSemantics"}
    return api, settled["content"]


def narrow_source(client, headers, source, allowed_columns):
    """从公开配置入口缩列并同步当前授权快照，不篡改内部回执。"""
    response = client.put(API + "/datasources/" + source["id"], headers=headers, json={
        "name": source["name"], "type": source["type"], "databaseName": source["databaseName"],
        "version": source["version"], "allowedTables": source["allowedTables"],
        "allowedColumns": allowed_columns,
    })
    assert response.status_code == 200, response.text
    source = response.json()["data"]
    response = client.post(API + f"/datasources/{source['id']}/sync", headers=headers)
    assert response.status_code == 200, response.text
    return source


def test_production_source_discovery_keeps_inventory_fact_correction(
    client, administrator, inventory_source, isolated_dsh_worker, bounded_inventory_reads,
):
    worker = isolated_dsh_worker
    cid, run = prepare_inventory(client, administrator, inventory_source, worker)
    discovery = worker.execute(run["run_id"], "source-discovery", 1, "list_business_data_sources", {})
    assert discovery["status"] == "success", discovery
    receipt = read_inventory(worker, run, inventory_source, sequence=2)
    assert receipt["status"] == "success", receipt
    settled = worker.settle(run["run_id"], "completed", UNSUPPORTED)
    emit_receipts("discovery", discovery=discovery, inventory=receipt, settled=settled,
                  business_queries=len(bounded_inventory_reads))
    assert "本页快照" in settled["content"] and "6 条库存记录" in settled["content"]
    assert UNSUPPORTED not in settled["content"]
    assert worker.history(administrator, cid)["messages"][-1]["content"] == settled["content"]
    assert len(bounded_inventory_reads) == 1


def test_compound_identity_counts_two_entities_in_same_page(
    client, administrator, compound_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    result, answer = read_api_and_worker(client, administrator, compound_source, isolated_dsh_worker, db)
    assert [row["备件名称"] for row in result["data"]] == ["甲组织备件", "乙组织备件"]
    assert result["statistics"]["materialCount"] == 2
    assert result["statistics"]["materialCountKnown"] is True
    assert result["statistics"]["knownMaterialCount"] == 2
    assert result["statistics"]["unknownMaterialRows"] == 0
    assert "涉及 2 种物料" in answer
    assert len(bounded_inventory_reads) == 2


@pytest.mark.parametrize("hidden_table", [inventory.STOCK, inventory.MATERIAL])
def test_hidden_composite_key_cannot_be_rebuilt_from_name_or_bare_id(
    client, administrator, compound_source, isolated_dsh_worker, db, bounded_inventory_reads, hidden_table,
):
    columns = (["spInventoryId", "isDeleted", "c_productId", "c_availableStock"]
               if hidden_table == inventory.STOCK else ["sparePartsInforId", "isDeleted", "c_productName"])
    source = narrow_source(client, administrator, compound_source, {hidden_table: columns})
    result, answer = read_api_and_worker(client, administrator, source, isolated_dsh_worker, db)
    assert len(result["data"]) == 2
    assert result["statistics"]["materialCount"] is None
    assert result["statistics"]["knownMaterialCount"] == 0
    assert result["statistics"]["unknownMaterialRows"] == 2
    assert all("c_orgId" not in statement for statement in bounded_inventory_reads)
    assert "物料种数无法完整确认" in answer and "涉及 1 种物料" not in answer


def test_one_material_across_warehouses_and_batches_remains_one(
    client, administrator, inventory_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    result, answer = read_api_and_worker(client, administrator, inventory_source, isolated_dsh_worker, db,
        api_params={"materialId": "m2"}, worker_params={"material_id": "m2"})
    assert len(result["data"]) == 2
    assert len({row["仓库标识"] for row in result["data"]}) == 2
    assert len({row["批次号"] for row in result["data"]}) == 2
    assert result["statistics"]["materialCount"] == 1
    assert result["statistics"]["materialCountKnown"] is True
    assert "涉及 1 种物料" in answer


def test_orphan_and_soft_deleted_references_keep_inventory_and_identity(
    client, administrator, inventory_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    result, answer = read_api_and_worker(client, administrator, inventory_source, isolated_dsh_worker, db)
    rows = {row["库存记录标识"]: row for row in result["data"]}
    assert len(rows) == 6 and rows["s5"]["备件名称"] is None and rows["s6"]["备件名称"] is None
    assert rows["s5"]["物料标识"] == "missing" and rows["s6"]["物料标识"] == "md"
    assert result["statistics"]["materialCount"] == 5
    assert result["statistics"]["unknownMaterialRows"] == 0
    assert "涉及 5 种物料" in answer


@pytest.mark.parametrize("value,all_rows", [(None, False), (None, True), ("", False), ("  ", False)])
def test_raw_null_and_empty_material_identity_are_unknown(
    client, administrator, inventory_source, isolated_dsh_worker, db, bounded_inventory_reads, value, all_rows,
):
    path = managed_database_path(administrator["X-Workspace-Id"], inventory_source["databaseName"])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE t_spinventory SET c_productId=? WHERE isDeleted=0 AND (? OR spInventoryId='s1')",
                           (value, all_rows))
    result, answer = read_api_and_worker(client, administrator, inventory_source, isolated_dsh_worker, db)
    assert len(result["data"]) == 6 and result["data"][0]["物料标识"] == value
    assert result["statistics"]["materialCount"] is None
    assert result["statistics"]["knownMaterialCount"] == (0 if all_rows else 4)
    assert result["statistics"]["unknownMaterialRows"] == (6 if all_rows else 1)
    assert "物料种数无法完整确认" in answer


def test_numeric_zero_is_a_known_material_identity(
    client, administrator, compound_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    path = managed_database_path(administrator["X-Workspace-Id"], compound_source["databaseName"])
    # 独立临时夹具改为数值键，SQLite TEXT 亲和性会把 0 转成字符串，无法覆盖原生数值零。
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            DROP TABLE t_spinventory;
            DROP TABLE t_sparepartsinfor;
            CREATE TABLE t_sparepartsinfor (sparePartsInforId INTEGER PRIMARY KEY, isDeleted INTEGER);
            CREATE TABLE t_spinventory (spInventoryId TEXT PRIMARY KEY, isDeleted INTEGER, c_productId INTEGER,
                FOREIGN KEY(c_productId) REFERENCES t_sparepartsinfor(sparePartsInforId));
            INSERT INTO t_sparepartsinfor VALUES (0,0);
            INSERT INTO t_spinventory VALUES ('s1',0,0),('s2',0,0),('s3',0,NULL),('s4',0,'');
        """)
    response = client.post(API + f"/datasources/{compound_source['id']}/sync", headers=administrator)
    assert response.status_code == 200, response.text
    result, answer = read_api_and_worker(client, administrator, compound_source, isolated_dsh_worker, db)
    assert result["data"][0]["物料标识"] == 0 and isinstance(result["data"][0]["物料标识"], int)
    assert result["statistics"]["knownMaterialCount"] == 1
    assert result["statistics"]["unknownMaterialRows"] == 2
    assert result["statistics"]["materialCount"] is None
    assert "已确认 1 种物料身份" in answer and "2 条库存记录的物料身份未知" in answer


def test_empty_page_does_not_claim_no_inventory(
    client, administrator, inventory_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    result, answer = read_api_and_worker(client, administrator, inventory_source, isolated_dsh_worker, db,
        api_params={"offset": 100}, worker_params={"offset": 100})
    assert result["data"] == [] and result["statistics"]["materialCount"] == 0
    assert result["statistics"]["materialCountKnown"] is True
    assert "当前页未返回记录" in answer and "不能证明全库没有库存" in answer


@pytest.mark.parametrize("key_schema", ["no_primary", "hidden_composite_primary"])
def test_foreign_key_without_complete_visible_primary_is_not_entity_proof(
    client, administrator, compound_source, isolated_dsh_worker, db, bounded_inventory_reads, key_schema,
):
    path = managed_database_path(administrator["X-Workspace-Id"], compound_source["databaseName"])
    primary = ", PRIMARY KEY(sparePartsInforId,c_orgId)" if key_schema == "hidden_composite_primary" else ""
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            DROP TABLE t_spinventory;
            DROP TABLE t_sparepartsinfor;
            CREATE TABLE t_sparepartsinfor (sparePartsInforId TEXT, c_orgId TEXT, c_referenceCode TEXT,
                isDeleted INTEGER, UNIQUE(sparePartsInforId,c_referenceCode)""" + primary + """);
            CREATE TABLE t_spinventory (spInventoryId TEXT PRIMARY KEY, isDeleted INTEGER,
                c_productId TEXT, c_referenceCode TEXT,
                FOREIGN KEY(c_productId,c_referenceCode) REFERENCES t_sparepartsinfor(sparePartsInforId,c_referenceCode));
            INSERT INTO t_sparepartsinfor VALUES ('m1','orgA','codeA',0),('m1','orgB','codeB',0);
            INSERT INTO t_spinventory VALUES ('s1',0,'m1','codeA'),('s2',0,'m1','codeB');
        """)
    source = compound_source
    if key_schema == "hidden_composite_primary":
        source = narrow_source(client, administrator, source, {
            inventory.MATERIAL: ["sparePartsInforId", "c_referenceCode", "isDeleted"],
        })
    else:
        response = client.post(API + f"/datasources/{source['id']}/sync", headers=administrator)
        assert response.status_code == 200, response.text
    result, answer = read_api_and_worker(client, administrator, source, isolated_dsh_worker, db)
    assert len(result["data"]) == 2 and result["statistics"]["materialCount"] is None
    assert result["statistics"]["unknownMaterialRows"] == 2
    assert "物料种数无法完整确认" in answer
    assert all("c_orgId" not in statement for statement in bounded_inventory_reads)


def test_hidden_material_identity_is_unknown_in_api_history_and_worker(
    client, administrator, inventory_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    source = narrow_source(client, administrator, inventory_source, {
        inventory.STOCK: ["spInventoryId", "isDeleted", "c_availableStock"],
    })
    result, answer = read_api_and_worker(client, administrator, source, isolated_dsh_worker, db)
    assert len(result["data"]) == 6 and all(row["物料标识"] is None for row in result["data"])
    assert result["statistics"]["materialCount"] is None
    assert result["statistics"]["materialCountKnown"] is False
    assert result["statistics"]["knownMaterialCount"] == 0
    assert result["statistics"]["unknownMaterialRows"] == 6
    assert "物料种数无法完整确认" in answer and "6 条库存记录的物料身份未知" in answer
    assert "涉及 0 种物料" not in answer
    assert len(bounded_inventory_reads) == 2


def test_unit_projection_preserves_conflicts_and_binds_dictionary_text(
    client, administrator, inventory_source, isolated_dsh_worker, db, bounded_inventory_reads,
):
    literal_label = "千克'); DROP TABLE t_spinventory; --"
    snapshot = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == inventory_source["id"]))
    snapshot.tables_json = [{**table, "columns": [{**column, "enumValues": [
        {"value": "5", "label": "升"}, {"value": "5", "label": "桶"},
        {"value": "2", "label": literal_label},
    ]} if column["columnName"] == "c_unit" else column for column in table["columns"]]}
        for table in snapshot.tables_json]
    db.commit()
    result, _ = read_api_and_worker(client, administrator, inventory_source, isolated_dsh_worker, db)
    rows = {row["库存记录标识"]: row for row in result["data"]}
    assert len(rows) == 6 and rows["s1"]["库存计量单位"] is None
    assert rows["s2"]["库存计量单位"] == literal_label
    assert rows["s4"]["库存计量单位"] is None and rows["s4"]["库存计量单位编码"] == 999
    assert "DROP TABLE" not in result["generatedSql"]
    assert literal_label in result["parameters"].values()


@pytest.mark.parametrize("dictionary_case", ["exact_match", "over_budget"])
def test_unit_projection_is_exact_and_bounded(
    client, administrator, compound_source, isolated_dsh_worker, db, bounded_inventory_reads, dictionary_case,
):
    path = managed_database_path(administrator["X-Workspace-Id"], compound_source["databaseName"])
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            DROP TABLE t_spinventory;
            DROP TABLE t_sparepartsinfor;
            CREATE TABLE t_sparepartsinfor (sparePartsInforId TEXT PRIMARY KEY, isDeleted INTEGER,
                c_unit TEXT COLLATE NOCASE);
            CREATE TABLE t_spinventory (spInventoryId TEXT PRIMARY KEY, isDeleted INTEGER, c_productId TEXT,
                c_unit TEXT COLLATE NOCASE, FOREIGN KEY(c_productId) REFERENCES t_sparepartsinfor(sparePartsInforId));
            INSERT INTO t_sparepartsinfor VALUES ('m1',0,'kg');
            INSERT INTO t_spinventory VALUES ('s1',0,'m1','kg'),('s2',0,'m1','KG'),('s3',0,'m1','kg ');
        """)
    response = client.post(API + f"/datasources/{compound_source['id']}/sync", headers=administrator)
    assert response.status_code == 200, response.text
    options = [{"value": "kg", "label": "小写单位"}, {"value": "KG", "label": "大写单位"}]
    if dictionary_case == "over_budget":
        options.extend({"value": f"code{index}", "label": f"单位{index}"} for index in range(100))
    snapshot = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == compound_source["id"]))
    snapshot.tables_json = [{**table, "columns": [{**column, "enumValues": options}
        if column["columnName"] == "c_unit" else column for column in table["columns"]]}
        for table in snapshot.tables_json]
    db.commit()
    result, _ = read_api_and_worker(client, administrator, compound_source, isolated_dsh_worker, db)
    labels = [row["库存计量单位"] for row in result["data"]]
    assert len(result["generatedSql"]) < 30000
    if dictionary_case == "over_budget":
        assert labels == [None, None, None]
        assert all(item["projectionStatus"] == "unavailable" and "预算" in item["reason"]
                   for item in result["retrievalEvidence"]["unitDictionaries"])
    else:
        assert labels == ["小写单位", "大写单位", None]
