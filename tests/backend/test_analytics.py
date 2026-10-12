"""Real SQLite queries plus safety, provenance, export and authorization contracts.

中文说明：数据分析主用例：真实 SQLite 查询 + SQL 安全、来源溯源、导出与授权契约。
验证行为：禁止语句在驱动执行前被拒；命名参数不改写引号内文本；MySQL 日期/数字函数放行；
失败查询脱敏且不留假结果；字段范围展开/隐藏字段拒绝；导出防公式注入与 HTML 转义；
查询权限与数据源权限相互独立；自然语言查询走工作区与已校验 SQL，字段修复守恒。
隔离方式：client 夹具（内存 SQLite）+ monkeypatch 替换模型，不连业务库与外部模型。
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.errors import ValidationError
from app.models import Conversation, DataSource, ModelProfile, Run, Secret
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.models import QueryHistory, SourceMetadata, SourceSemanticDocument
from app.modules.analytics.queries import parse_model_object
from app.modules.analytics.semantic import explicitly_requested_tables, query_semantics
from app.modules.analytics.sql_safety import authorize_sql, driver_query
from app.modules.analytics.statistics import describe, enhanced_analysis
from app.modules.system.models import Role
from app.providers import model
from test_system_foundation import activate_user, create_role, create_user

API = "/api/v1/analytics"
TABLES = [{"tableName": "sales", "columns": [
    {"columnName": "department", "dataType": "TEXT"}, {"columnName": "amount", "dataType": "DECIMAL"},
]}]


@pytest.fixture
def source(client, administrator, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    path = managed_database_path(administrator["X-Workspace-Id"], "analytics-fixture.sqlite3", must_exist=False)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, department TEXT, amount INTEGER, note TEXT)")
        connection.executemany("INSERT INTO sales VALUES (?, ?, ?, ?)", [
            (1, "设备", 100, "正常"), (2, "财务", 200, "=1+1"), (3, "设备", 300, "<script>alert(1)</script>"),
            (4, "财务", None, "缺失"), (5, "设备", 500, "备注"),
        ])
        connection.execute("CREATE TABLE restricted_data (private_note TEXT)")
        connection.execute("INSERT INTO restricted_data VALUES ('not-authorized')")
    response = client.post(API + "/datasources", headers=administrator, json={
        "name": "分析验证库", "type": "sqlite", "databaseName": path.name, "allowedTables": ["sales"],
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


def run_sql(client, headers, source, sql="SELECT * FROM sales ORDER BY id", **extra):
    return client.post(API + "/query", headers=headers, json={
        "datasourceId": source["id"], "query": sql, "mode": "sql", **extra,
    })


@pytest.mark.parametrize(("content", "expected"), [
    ('{"sql":"SELECT 1","explanation":"直接 JSON"}', {"sql": "SELECT 1", "explanation": "直接 JSON"}),
    ('分析结果如下：\n```json\n{"sql":"SELECT 2"}\n```\n请执行。', {"sql": "SELECT 2"}),
    ('说明文字 {"sql":"SELECT \'{设备}\' AS label"} 尾随文字', {"sql": "SELECT '{设备}' AS label"}),
    ('"{\\"sql\\":\\"SELECT 3\\"}"', {"sql": "SELECT 3"}),
])
def test_model_json_parser_accepts_common_valid_wrappers(content, expected):
    assert parse_model_object(content) == expected


@pytest.mark.parametrize("sql", [
    "DELETE FROM sales", "SELECT * FROM sales; DELETE FROM sales",
    "UPDATE sales SET amount=1 RETURNING *", "INSERT INTO sales SELECT * FROM sales",
    "CREATE TABLE copied AS SELECT * FROM sales", "DROP TABLE sales",
    "SELECT LOAD_FILE('/etc/passwd')", "SELECT SLEEP(30)", "SELECT randomblob(100000000)",
    "SELECT * FROM sqlite_master", "SELECT * FROM restricted_data", "SELECT * FROM other.sales",
    "SELECT * INTO OUTFILE '/tmp/x' FROM sales", "SELECT * FROM sales FOR UPDATE",
    "PRAGMA database_list", "ATTACH DATABASE '/tmp/secret' AS secret",
    "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
    "SELECT * FROM read_csv('/tmp/secret')", "SELECT secret_column FROM sales",
    "SELECT * FROM sales LIMIT :limit", "SELECT * FROM sales OFFSET 1000001",
])
def test_disallowed_sql_is_rejected_without_driver_execution(sql):
    with pytest.raises(ValidationError):
        authorize_sql(sql, provider="sqlite", tables=TABLES, max_rows=20)


def test_named_parameters_do_not_modify_quoted_strings():
    query = authorize_sql(
        "SELECT department, SUM(amount) AS total FROM sales WHERE department != :name AND department LIKE '%a%' GROUP BY department",
        provider="mysql", tables=TABLES, max_rows=10, parameters={"name": "' OR 1=1 --"},
    )
    sql, params = driver_query(query, "mysql")
    assert "%(name)s" in sql and "%%a%%" in sql
    assert params["name"] not in sql
    assert params["name"] == "' OR 1=1 --"


def test_mysql_date_format_internal_cast_is_allowed():
    tables = [{"tableName": "sales", "columns": [
        {"columnName": "sale_date", "dataType": "DATE"},
        {"columnName": "amount", "dataType": "DECIMAL"},
    ]}]
    query = authorize_sql(
        "SELECT DATE_FORMAT(sale_date, '%Y-%m') AS month, SUM(amount) AS total "
        "FROM sales GROUP BY DATE_FORMAT(sale_date, '%Y-%m') ORDER BY month",
        provider="mysql", tables=tables, max_rows=100,
    )
    assert "DATE_FORMAT" in query.sql
    assert "TS_OR_DS_TO_TIMESTAMP" not in query.sql
    sql, params = driver_query(query, "mysql")
    assert "'%%Y-%%m'" in sql
    assert params is None


def test_mysql_number_format_internal_function_is_allowed():
    """sqlglot represents MySQL FORMAT() as its NUMBER_TO_STR AST function."""
    tables = [{"tableName": "sales", "columns": [
        {"columnName": "amount", "dataType": "DECIMAL"},
    ]}]
    query = authorize_sql(
        "SELECT CONCAT('¥', FORMAT(COALESCE(amount, 0), 0)) AS formatted_amount FROM sales",
        provider="mysql", tables=tables, max_rows=100,
    )
    assert "FORMAT" in query.sql
    sql, params = driver_query(query, "mysql")
    assert "FORMAT" in sql
    assert params is None

def test_unexpected_query_failure_is_redacted_and_finalizes_history(client, administrator, source, monkeypatch, db):
    from app.modules.analytics import queries
    def broken_adapter(*args, **kwargs):
        raise RuntimeError("SELECT private_password FROM secret_config")
    monkeypatch.setattr(queries, "execute_read", broken_adapter)
    response = run_sql(client, administrator, source)
    assert response.status_code == 502
    assert "private_password" not in response.text and "secret_config" not in response.text
    query_id = response.json()["query_id"]
    record = db.get(QueryHistory, query_id)
    assert record.status == "failed"
    assert db.get(Run, record.run_id).status == "failed"
    assert record.result_json == {}


def test_source_metadata_health_and_exact_sql_results(client, administrator, source, db):
    response = client.get(API + f"/datasources/{source['id']}/tables/info", headers=administrator)
    assert response.status_code == 200, response.text
    assert [row["tableName"] for row in response.json()["data"]] == ["sales"]
    assert response.json()["data"][0]["rowCount"] is None
    assert response.json()["data"][0]["columns"][0]["primaryKey"]
    health = client.get(API + f"/datasources/{source['id']}/health", headers=administrator)
    assert health.status_code == 200, health.text
    response = run_sql(client, administrator, source, "SELECT department, SUM(amount) AS total FROM sales GROUP BY department ORDER BY department")
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert {row["department"]: row["total"] for row in result["data"]} == {"设备": 900, "财务": 200}
    assert result["truncated"] is False
    assert result["totalCount"] == 2
    assert result["runId"]
    run = db.get(Run, result["runId"])
    assert run.status == "completed" and run.conversation_id
    assert run.context_snapshot["execution_scope"] == "standalone"
    assert db.get(Conversation, run.conversation_id).archived_at is not None
    history = client.get(API + "/history/" + result["queryId"], headers=administrator)
    assert history.json()["data"] == result


def test_source_creation_auto_syncs_catalog_description_and_semantic_index(
    client, administrator, source, db,
):
    """Creating a source must make it immediately understandable to AI and users."""

    assert source["metadataStatus"] == "ready"
    assert source["descriptionSource"] == "metadata"
    assert source["autoDescription"]
    assert source["businessTableCount"] == 1
    assert source["fieldCount"] == 4
    assert source["semanticIndexStatus"] in {"ready", "keyword_only"}
    assert source["semanticIndexedTableCount"] == 1
    assert "sqlite" in source["environmentSummary"].lower()

    metadata = db.scalar(select(SourceMetadata).where(
        SourceMetadata.datasource_id == source["id"],
    ))
    document = db.scalar(select(SourceSemanticDocument).where(
        SourceSemanticDocument.datasource_id == source["id"],
        SourceSemanticDocument.table_name == "sales",
    ))
    assert metadata is not None and metadata.status == "ready"
    assert document is not None and "表名: sales" in document.document_text
    assert document.source_version == source["version"]
    assert document.index_status in {"ready", "keyword_only"}

    listed = client.get(API + "/datasources", headers=administrator)
    assert listed.status_code == 200, listed.text
    listed_source = next(row for row in listed.json()["data"] if row["id"] == source["id"])
    assert listed_source["autoDescription"] == source["autoDescription"]
    assert listed_source["semanticIndexedTableCount"] == 1


def test_empty_aggregate_result_keeps_numeric_alias_type(client, administrator, source):
    response = run_sql(
        client, administrator, source,
        "SELECT department AS period, COUNT(*) AS value FROM sales "
        "WHERE amount > 999999 GROUP BY department",
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["data"] == []
    assert {column["columnName"]: column["dataType"] for column in result["columns"]} == {
        "period": "TEXT", "value": "bigint",
    }


def test_row_limits_parameters_and_read_only_database(client, administrator, source):
    limited = run_sql(client, administrator, source, maxRows=2)
    assert limited.status_code == 200, limited.text
    assert limited.json()["data"]["totalCount"] == 2 and limited.json()["data"]["truncated"]
    response = run_sql(client, administrator, source, "SELECT amount FROM sales WHERE department = :dept AND amount >= :minimum ORDER BY amount",
                       parameters={"dept": "设备", "minimum": 200})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["data"] == [{"amount": 300}, {"amount": 500}]
    attack = run_sql(client, administrator, source, "SELECT * FROM sales WHERE department=:dept", parameters={"dept": "' OR 1=1 --"})
    assert attack.status_code == 200 and attack.json()["data"]["data"] == []
    rejected = run_sql(client, administrator, source, "DELETE FROM sales")
    assert rejected.status_code == 422, rejected.text
    assert run_sql(client, administrator, source).json()["data"]["totalCount"] == 5


def test_parameterized_history_retains_replay_values(client, administrator, source):
    response = run_sql(client, administrator, source,
                       'SELECT amount FROM sales WHERE department = :department', parameters={'department': '设备'})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['parameters'] == {'department': '设备'}
    saved = client.get(API + '/history/' + result['queryId'], headers=administrator).json()['data']
    assert saved['parameters'] == result['parameters']
    listed = client.get(API + '/history/recent', headers=administrator).json()['data']
    assert listed[0]['parameters'] == result['parameters']
    replay = run_sql(client, administrator, source, saved['generatedSql'], parameters=saved['parameters'])
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['data'] == result['data']


def test_analysis_and_chart_flags_do_not_return_unrequested_insights(client, administrator, source):
    response = run_sql(
        client, administrator, source,
        "SELECT department, amount FROM sales ORDER BY id",
        needAnalysis=False, needChart=False,
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["statistics"] == {
        "scope": "not_requested", "rowCount": 5, "columnCount": 2, "truncated": False,
    }
    assert result["analysis"] == ""
    assert result["insights"] == []
    assert result["enhancedAnalysis"] == {}
    assert result["chartRecommendations"] == []


def test_field_scope_expands_star_and_rejects_hidden_fields(client, administrator, source):
    request = {"name": source["name"], "type": source["type"], "databaseName": source["databaseName"],
               "version": source["version"], "allowedTables": ["sales"], "allowedColumns": {"sales": ["department", "amount"]}}
    response = client.put(API + "/datasources/" + source["id"], headers=administrator, json=request)
    assert response.status_code == 200, response.text
    assert client.put(API + "/datasources/" + source["id"], headers=administrator, json=request).status_code == 409
    result = run_sql(client, administrator, source, "SELECT * FROM sales")
    assert result.status_code == 200, result.text
    assert set(result.json()["data"]["data"][0]) == {"department", "amount"}
    assert run_sql(client, administrator, source, "SELECT note FROM sales").status_code == 422
    assert run_sql(client, administrator, source, "SELECT * FROM restricted_data").status_code == 422


def test_failed_query_is_persisted_without_fake_result(client, administrator, source, db):
    response = run_sql(client, administrator, source, "SELECT secret FROM nonexistent")
    assert response.status_code == 422, response.text
    row = db.scalar(select(QueryHistory).where(QueryHistory.status == "failed"))
    assert row and row.result_json == {}
    assert db.get(Run, row.run_id).status == "failed"
    assert client.get(API + "/history/" + row.id, headers=administrator).status_code == 422


def test_real_quality_statistics_are_sample_scoped(client, administrator, source):
    response = client.post(API + "/quality-check", headers=administrator,
                           json={"datasourceId": source["id"], "tableName": "sales", "maxRows": 5})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["sampleSize"] == 5 and result["scope"] == "returned_rows"
    assert result["statistics"]["columns"]["amount"]["nullCount"] == 1
    assert result["statistics"]["columns"]["amount"]["sum"] == "1100"
    assert any(item["type"] == "missing" for item in result["issues"])


def test_exports_are_formula_safe_and_html_escaped(client, administrator, source):
    response = run_sql(client, administrator, source)
    assert response.status_code == 200, response.text
    query_id = response.json()["data"]["queryId"]
    csv_response = client.post(API + "/export", headers=administrator, json={"queryId": query_id, "format": "csv"})
    assert csv_response.status_code == 200, csv_response.text
    csv_rows = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert csv_rows[2][3] == "'=1+1"
    html_response = client.post(API + "/export", headers=administrator, json={"queryId": query_id, "format": "html"})
    assert "<script>" not in html_response.text and "&lt;script&gt;" in html_response.text
    xlsx_response = client.post(API + "/export", headers=administrator, json={"queryId": query_id, "format": "xlsx"})
    assert xlsx_response.status_code == 200, xlsx_response.text[:200]
    from openpyxl import load_workbook
    workbook = load_workbook(io.BytesIO(xlsx_response.content), data_only=False)
    assert workbook["查询结果"]["D3"].value == "=1+1"
    assert workbook["查询结果"]["D3"].data_type == "s"
    assert workbook["数据来源"]["B1"].value == query_id


def test_query_and_source_access_are_independent_and_history_rechecks_source_scope(client, administrator, source):
    result = run_sql(client, administrator, source).json()["data"]
    role = create_role(client, administrator, code="analyst_restricted",
                       permissions=["home.view", "analytics.view", "analytics.execute", "analytics.export", "datasources.view"], scope="self")
    user = create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    assert client.get(API + "/datasources", headers=headers).json()["data"] == []
    assert run_sql(client, headers, source).status_code in {403, 404}
    assert client.get(API + "/history/" + result["queryId"], headers=headers).status_code in {403, 404}
    assert client.post(API + "/export", headers=headers, json={"queryId": result["queryId"], "format": "json"}).status_code in {403, 404}
    assert client.get(API + "/history/recent", headers=headers).json()["data"] == []


def test_source_secret_redaction_and_stale_version(client, administrator, db):
    secret_value = "NeverReturnThisSecret123!"
    request = {"name": "未连接测试", "type": "mysql", "host": "127.0.0.1", "databaseName": "analytics_business_test",
               "username": "reader", "password": secret_value}
    response = client.post(API + "/datasources", headers=administrator, json=request)
    assert response.status_code == 201, response.text
    assert secret_value not in response.text and "password" not in response.json()["data"]
    source_id = response.json()["data"]["id"]
    row = db.get(DataSource, source_id)
    assert row.secret_id and secret_value not in json.dumps(row.connection_config)
    assert secret_value not in str(db.get(Secret, row.secret_id).ciphertext)
    request.update({"id": source_id, "version": 1, "password": "", "name": "改名"})
    assert client.put(API + "/datasources/" + source_id, headers=administrator, json=request).status_code == 200
    assert client.put(API + "/datasources/" + source_id, headers=administrator, json=request).status_code == 409


def test_source_password_conflict_rolls_back_new_secret(client, administrator, source, monkeypatch, db):
    """A lost compare-and-swap must not leave the proposed password behind."""
    from app.modules.analytics import sources
    original_create = sources.create_secret
    before = set(db.scalars(select(Secret.id)).all())

    def create_during_conflict(session, **kwargs):
        secret = original_create(session, **kwargs)
        # Simulate a configuration change after the initial version read but
        # before the conditional update. It shares the transaction on purpose.
        session.execute(update(DataSource).where(DataSource.id == source['id']).values(
            connection_config={"version": source['version'] + 1},
        ).execution_options(synchronize_session=False))
        return secret

    monkeypatch.setattr(sources, 'create_secret', create_during_conflict)
    response = client.put(API + '/datasources/' + source['id'], headers=administrator, json={
        'name': source['name'], 'type': 'sqlite', 'databaseName': source['databaseName'],
        'version': source['version'], 'password': 'isolated-conflicting-password',
    })
    assert response.status_code == 409, response.text
    db.expire_all()
    assert set(db.scalars(select(Secret.id)).all()) == before
    assert db.get(DataSource, source['id']).secret_id is None


def test_import_creates_new_managed_source_without_chat(client, administrator, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    response = client.post(API + "/datasources/import", headers=administrator, data={"name": "设备导入", "table_name": "device_readings"},
                           files={"file": ("devices.csv", "设备编号,温度\n001,80\n002,90\n".encode("utf-8"), "text/csv")})
    assert response.status_code == 201, response.text
    imported = response.json()["data"]
    result = run_sql(client, administrator, imported, 'SELECT "设备编号", CAST("温度" AS INTEGER) AS temperature FROM device_readings')
    assert result.status_code == 200, result.text
    assert result.json()["data"]["data"] == [{"设备编号": "001", "temperature": 80}, {"设备编号": "002", "temperature": 90}]


def test_source_disable_delete_keeps_history_and_file(client, administrator, source):
    result = run_sql(client, administrator, source).json()["data"]
    request = {"name": source["name"], "type": "sqlite", "databaseName": source["databaseName"], "version": 1, "enabled": False}
    assert client.put(API + "/datasources/" + source["id"], headers=administrator, json=request).status_code == 200
    assert run_sql(client, administrator, source).status_code == 422
    assert client.get(API + "/history/" + result["queryId"], headers=administrator).status_code == 200
    assert client.delete(API + "/datasources/" + source["id"], headers=administrator).status_code == 200
    assert client.get(API + "/datasources", headers=administrator).json()["data"] == []
    assert managed_database_path(administrator["X-Workspace-Id"], source["databaseName"]).exists()


def test_natural_language_model_uses_workspace_and_validated_sql(client, administrator, source, monkeypatch):
    observed = []
    def mock_completion(profile, messages, **kwargs):
        observed.append(kwargs)
        assert "restricted_data" not in messages[-1]["content"]
        return {"content": '{"sql":"SELECT COUNT(*) AS total FROM sales","explanation":"按行数统计"}', "model": "isolated-test", "usage": {"total_tokens": 20}}
    monkeypatch.setattr(model, "chat_for_run", mock_completion)
    response = client.post(API + "/query", headers=administrator, json={"datasourceId": source["id"], "query": "统计记录数"})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["data"] == [{"total": 5}]
    assert result["retrievalEvidence"]["intent"] == "summary"
    assert result["retrievalEvidence"]["selectedTables"][0]["tableName"] == "sales"
    assert "metadata_comment" in result["retrievalEvidence"]["selectedTables"][0]["channels"]
    assert result["retrievalEvidence"]["contextTurns"] == 0
    assert observed[0]["workspace_id"] == administrator["X-Workspace-Id"]
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {"content": '{"sql":"DELETE FROM sales"}'})
    response = client.post(API + "/query", headers=administrator, json={"datasourceId": source["id"], "query": "统计记录数"})
    assert response.status_code == 422


def test_syntax_repair_executes_validated_scope_and_persists_result(client, administrator, source, monkeypatch, db):
    responses = iter(['SELECT id, department, amount FROM sales WHERE (',
                      "SELECT id, department, amount FROM sales WHERE department='设备' ORDER BY id"])
    calls = []
    def completion(*args, **kwargs):
        calls.append(kwargs)
        return {'content': json.dumps({'sql': next(responses), 'explanation': '设备部门明细'})}
    monkeypatch.setattr(model, 'chat_for_run', completion)
    response = client.post(API + '/query', headers=administrator,
        json={'datasourceId': source['id'], 'query': '查询设备部门的金额明细'})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert [r['id'] for r in result['data']] == [1, 3, 5]
    assert {r['department'] for r in result['data']} == {'设备'}
    assert result['modelUsage']['sqlRepairs'] == 1 and len(calls) == 2
    assert all(c['max_retries'] == 0 for c in calls)
    assert db.get(QueryHistory, result['queryId']).status == 'success'


@pytest.mark.parametrize('table_name', ['act_evt_log', 'flw_runtime'])
def test_explicit_authorized_workflow_table_reaches_model_and_executes(
    client, administrator, source, monkeypatch, table_name,
):
    path = managed_database_path(administrator['X-Workspace-Id'], source['databaseName'])
    with sqlite3.connect(path) as connection:
        connection.execute(f'CREATE TABLE {table_name} (id INTEGER, private_payload TEXT)')
        connection.execute(f"INSERT INTO {table_name} VALUES (1, 'not-in-model-context')")
    response = client.put(API + '/datasources/' + source['id'], headers=administrator, json={
        'name': source['name'], 'type': 'sqlite', 'databaseName': source['databaseName'],
        'version': source['version'], 'allowedTables': [table_name],
        'allowedColumns': {table_name: ['id']},
    })
    assert response.status_code == 200, response.text
    observed = []

    def completion(profile, messages, **kwargs):
        context = json.loads(messages[-1]['content'])
        observed.append(context)
        assert [table['tableName'] for table in context['schema']] == [table_name]
        assert [column['columnName'] for column in context['schema'][0]['columns']] == ['id']
        assert 'private_payload' not in messages[-1]['content']
        assert 'not-in-model-context' not in messages[-1]['content']
        return {'content': json.dumps({'sql': f'SELECT COUNT(*) AS total FROM {table_name}'})}

    monkeypatch.setattr(model, 'chat_for_run', completion)
    response = client.post(API + '/query', headers=administrator, json={
        'datasourceId': source['id'], 'query': f'统计 {table_name} 的记录总数',
    })
    assert response.status_code == 200, response.text
    assert response.json()['data']['data'] == [{'total': 1}]
    assert 'explicit_table' in response.json()['data']['retrievalEvidence']['selectedTables'][0]['channels']
    assert observed
    # General questions still do not automatically send infrastructure tables.
    response = client.post(API + '/query', headers=administrator, json={
        'datasourceId': source['id'], 'query': '统计业务记录总数',
    })
    assert response.status_code == 422, response.text
    assert len(observed) == 1


def test_explicit_table_match_cannot_expand_authorization_or_match_prefix():
    tables = [{'tableName': 'act_evt_log'}, {'tableName': 'sales'}]
    assert explicitly_requested_tables(tables, '查看 ACT_EVT_LOG（明细）') == {'act_evt_log'}
    assert explicitly_requested_tables(tables, '查看 xact_evt_log 和 act_evt_log_backup') == set()
    assert explicitly_requested_tables(tables, '查看 restricted_data') == set()


def test_natural_language_follow_up_reports_real_context_turns(client, administrator, source, monkeypatch):
    monkeypatch.setattr(model, "chat_for_run", lambda *args, **kwargs: {
        "content": '{"sql":"SELECT COUNT(*) AS total FROM sales","explanation":"按记录数统计"}',
        "model": "context-test", "usage": {},
    })
    session_id = "analysis-context-contract"
    first = client.post(API + "/query", headers=administrator, json={
        "datasourceId": source["id"], "query": "统计全部记录", "sessionId": session_id,
    })
    assert first.status_code == 200, first.text
    second = client.post(API + "/query", headers=administrator, json={
        "datasourceId": source["id"], "query": "继续，只看设备", "sessionId": session_id,
    })
    assert second.status_code == 200, second.text
    evidence = second.json()["data"]["retrievalEvidence"]
    assert evidence["contextTurns"] == 1
    assert evidence["followUp"] is True


def test_decimal_statistics_preserve_precision():
    stats, _, _, _ = describe([{"amount": "0.1"}, {"amount": "0.2"}],
                              [{"columnName": "amount", "dataType": "decimal"}], truncated=False)
    assert stats["columns"]["amount"]["sum"] == "0.3"


def test_quality_insights_use_business_labels_and_explain_projected_duplicates():
    rows = [{"amount": value} for value in [1, 1, 1, 1, 1, 1, 100, None]]
    columns = [{"columnName": "amount", "dataType": "decimal", "comment": "出库金额"}]
    stats, insights, explanation, _ = describe(rows, columns, truncated=False)
    assert stats["duplicateRows"] == 5
    assert next(item for item in insights if item["type"] == "missing")["title"] == "出库金额存在缺失值"
    assert next(item for item in insights if item["type"] == "outlier")["title"] == "出库金额存在离群值"
    assert "不代表原表整行重复" in next(item for item in insights if item["type"] == "duplicate")["description"]
    assert "返回字段重复组合 5 行" in explanation


def test_enhanced_analysis_requires_time_and_keeps_projection_cautious():
    rows = [
        {"month": f"2026-{month:02d}-01", "amount": value}
        for month, value in enumerate([10, 11, 12, 13, 14, 15, 16, 100], 1)
    ]
    columns = [
        # SQLite DATE()/strftime() values are strings; analysis must still
        # recognize a real ISO date series from returned values.
        {"columnName": "month", "dataType": "varchar", "comment": "月份"},
        {"columnName": "amount", "dataType": "decimal", "comment": "销售额"},
    ]
    statistics, _, _, _ = describe(rows, columns, truncated=False)
    result = enhanced_analysis(rows, columns, statistics, truncated=False)
    metric = result["metrics"][0]
    assert result["timeDimension"] == "month"
    assert metric["trend"]["direction"] == "up"
    assert metric["trend"]["observations"] == 8
    assert metric["anomaly"]["count"] == 1
    assert metric["projection"]["confidence"] in {"low", "medium"}
    assert "不代表预算或正式业务预测" in metric["projection"]["caveat"]

    no_time_columns = [{"columnName": "amount", "dataType": "decimal", "comment": "销售额"}]
    no_time_stats, _, _, _ = describe([{"amount": 1}, {"amount": 2}], no_time_columns, truncated=False)
    no_time = enhanced_analysis([{"amount": 1}, {"amount": 2}], no_time_columns, no_time_stats, truncated=False)
    assert no_time["timeDimension"] is None
    assert no_time["metrics"][0]["trend"] is None
    assert no_time["metrics"][0]["projection"] is None


def test_month_bucket_is_a_real_time_dimension_for_mysql_grouped_results():
    rows = [
        {"month": "2024-01", "sales_amount": "95300.000000"},
        {"month": "2024-10", "sales_amount": "31980.000000"},
    ]
    columns = [
        {"columnName": "month", "dataType": "varchar", "comment": "月份"},
        {"columnName": "sales_amount", "dataType": "decimal", "comment": "销售金额"},
    ]
    statistics, _, _, charts = describe(rows, columns, truncated=False)
    result = enhanced_analysis(rows, columns, statistics, truncated=False)
    assert result["timeDimension"] == "month"
    assert result["metrics"][0]["trend"]["direction"] == "down"
    assert charts[0]["chartType"] == "line"


def test_query_semantics_exposes_domain_intent_and_follow_up_without_model():
    profile = query_semantics("继续查看财务销售按月趋势")
    assert profile["intent"] == "trend"
    assert profile["businessDomains"] == ["财务", "销售"]
    assert profile["followUpCandidate"] is True
    assert profile["method"] == "metadata_comment_keyword_embedding_relation"


def test_managed_database_and_platform_database_boundaries(client, administrator, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    for filename in ("../outside.db", "C:/secret.db", "..\\secret.db"):
        with pytest.raises(ValidationError):
            managed_database_path(administrator["X-Workspace-Id"], filename)
    response = client.post(API + "/datasources", headers=administrator, json={
        "name": "平台库不可开放", "type": "mysql", "host": "127.0.0.1", "username": "root", "databaseName": settings.PLATFORM_DB,
    })
    assert response.status_code == 422


def test_query_templates_crud_version_and_use(client, administrator):
    payload = {"name": "统计业务表", "category": "设备", "content": "统计设备记录数"}
    response = client.post(API + "/query-templates", headers=administrator, json=payload)
    assert response.status_code == 201, response.text
    item = response.json()["data"]
    searched = client.get(API + "/query-templates", headers=administrator, params={"keyword": "设备"})
    assert [row["id"] for row in searched.json()["data"]] == [item["id"]]
    used = client.post(API + f"/query-templates/{item['id']}/use", headers=administrator)
    assert used.status_code == 200 and used.json()["data"]["usageCount"] == 1
    assert client.put(API + f"/query-templates/{item['id']}", headers=administrator,
                      json={**payload, "version": item["version"]}).status_code == 409
    assert client.delete(API + f"/query-templates/{item['id']}", headers=administrator).status_code == 200
