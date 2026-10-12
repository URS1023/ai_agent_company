"""Database connectors exercise the real authorized SQLite execution path.

中文说明：数据库连接器走真实的"已授权 SQLite"执行路径：保留字段类型并上报截断、
按数据范围限定元数据发现与查询、查询前重查数据源在线状态。
隔离方式：复用 test_analytics 的受管 SQLite 夹具 + client 夹具，不连外部数据库。
"""
from app.models import DataSource
from test_analytics import source  # shared managed SQLite fixture, no external DB


def connector(client, headers, source):
    response = client.post("/api/connectors", headers=headers, json={
        "name": "受控数据库工具", "provider": "database", "data_source_id": source["id"],
    })
    assert response.status_code == 201, response.text
    return response.json()["connector_id"]


def invoke(client, headers, connector_id, tool, arguments):
    response = client.post(f"/api/connectors/{connector_id}/test-invoke", headers=headers,
                           json={"tool_name": "database." + tool, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()


def test_database_connector_preserves_types_and_reports_truncation(client, administrator, source, db):
    record = db.get(DataSource, source["id"])
    record.connection_config = {**record.connection_config, "maxRows": 2}
    db.commit()
    cid = connector(client, administrator, source)
    output = invoke(client, administrator, cid, "query", {"sql": "SELECT id, amount FROM sales ORDER BY id"})
    assert output["status"] == "success", output
    assert output["data"]["rows"] == [{"id": 1, "amount": 100}, {"id": 2, "amount": 200}]
    assert output["data"]["truncated"] is True
    assert output["data"]["row_count"] == 2
    assert output["data"]["datasource_id"] == source["id"]
    assert output["data"]["column_metadata"][0]["dataType"] == "bigint"
    output = invoke(client, administrator, cid, "query", {"sql": "SELECT amount FROM sales WHERE id = 4"})
    assert output["data"]["rows"] == [{"amount": None}]
    assert output["data"]["truncated"] is False


def test_database_connector_scopes_discovery_and_queries(client, administrator, source, db):
    record = db.get(DataSource, source["id"])
    record.connection_config = {**record.connection_config, "allowedColumns": {"sales": ["id", "amount"]}}
    db.commit()
    cid = connector(client, administrator, source)
    output = invoke(client, administrator, cid, "list_tables", {})
    assert output["status"] == "success", output
    assert output["data"]["rows"] == [{"table_name": "sales"}]
    output = invoke(client, administrator, cid, "describe_table", {"table": "sales"})
    assert [row["Field"] for row in output["data"]["rows"]] == ["id", "amount"]
    for sql in ["SELECT * FROM restricted_data", "SELECT note FROM sales",
                "SELECT LOAD_FILE('/tmp/never-read')", "SELECT SLEEP(10)",
                "SELECT 1 INTO OUTFILE '/tmp/never-written'", "SELECT * FROM information_schema.tables",
                "SELECT 1; DELETE FROM sales", "PRAGMA database_list"]:
        output = invoke(client, administrator, cid, "query", {"sql": sql})
        assert output["status"] == "failed", (sql, output)
    output = invoke(client, administrator, cid, "describe_table", {"table": "restricted_data"})
    assert output["status"] == "failed"


def test_database_connector_rechecks_live_source_status(client, administrator, source, db):
    cid = connector(client, administrator, source)
    record = db.get(DataSource, source["id"])
    record.status = "disabled"
    db.commit()
    response = client.post(f"/api/connectors/{cid}/test-invoke", headers=administrator,
                           json={"tool_name": "database.query", "arguments": {"sql": "SELECT 1"}})
    assert response.status_code != 200 or response.json()["status"] == "failed"
