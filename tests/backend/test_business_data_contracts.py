"""内部业务数据合同回归：独立分页、目录字段保真及纯缓存发现，不连接真实业务库。"""
from types import SimpleNamespace

from sqlalchemy import delete, select

from app.models import AuthIdentity, DataSource
from app.modules.analytics import semantic
from app.modules.analytics.models import QueryHistory, SourceMetadata, SourceSemanticDocument
from app.modules.business_ai.service import _discovery_table_summary
from app.modules.chat_artifacts.alert_spreadsheet import alert_workbook_spec
from app.providers.tool.gateway import ToolGateway, build_context
from test_alert_chat_evidence import saved_problem
from test_analytics import source
from test_collection import create_source as create_http_source, upstream


API = "/api/v1/ai/business-actions"


def test_alert_report_and_problem_pages_advance_independently(client, administrator, db):
    older, _ = saved_problem(client, administrator, db, count=4, device_code="PAGE-OLDER")
    newer, _ = saved_problem(client, administrator, db, count=1, device_code="PAGE-NEWER")
    response = client.post(API + "/alerts/overview", headers=administrator, json={
        "limit": 1, "reportsOffset": 0, "problemsOffset": 1,
    })
    assert response.status_code == 200, response.text
    view = response.json()["data"]
    assert [row["id"] for row in view["reports"]] == [newer.id]
    assert [row["reportId"] for row in view["problems"]] == [older.id]
    summary = view["summary"]
    assert summary["reportsPage"]["offset"] == 0
    assert summary["reportsPage"]["nextOffset"] == 1
    assert summary["problemsPage"]["offset"] == 1
    assert summary["problemsPage"]["nextOffset"] == 2
    assert summary["reportsPage"]["total"] is None
    assert summary["returnedProblemsInReportPage"] == 0
    assert summary["returnedProblemsOutsideReportPage"] == 1
    assert summary["relationship"] == "independent_recent_records"
    assert "不代表这些报告的全部问题" in view["message"]
    assert sum(summary["severityCounts"].values()) == 1
    source_rows = dict(alert_workbook_spec(view)["sheets"][-1]["rows"])
    assert source_rows["报告分页起点"] == 0 and source_rows["问题分页起点"] == 1
    assert source_rows["问题下页起点"] == 2
    assert source_rows["本问题页归属其他报告的问题数"] == 1


def test_single_report_and_problem_history_do_not_claim_collection_totals(client, administrator, db):
    report, problem = saved_problem(client, administrator, db, count=3)
    response = client.post(API + "/alerts/overview", headers=administrator, json={
        "reportId": report.id, "limit": 2, "reportsOffset": 100, "problemsOffset": 2,
    })
    view = response.json()["data"]
    assert view["summary"]["reportsPage"]["offset"] == 0
    assert view["summary"]["reportsPage"]["total"] == 1
    assert view["summary"]["problemsPage"]["total"] is None
    assert view["summary"]["problemsPage"]["nextOffset"] is None
    assert view["summary"]["relationship"] == "specified_report"
    response = client.post(API + "/alerts/overview", headers=administrator, json={
        "problemId": problem.id, "limit": 2, "offset": 99,
    })
    view = response.json()["data"]
    assert view["processingHistory"]["offset"] == 99
    assert view["summary"]["reportsPage"]["offset"] == view["summary"]["problemsPage"]["offset"] == 0
    assert view["summary"]["relationship"] == "specified_problem"


def test_empty_out_of_range_alert_page_cannot_invent_a_total(client, administrator):
    response = client.post(API + "/alerts/overview", headers=administrator, json={"offset": 999})
    summary = response.json()["data"]["summary"]
    for key in ("reportsPage", "problemsPage"):
        assert summary[key]["returnedCount"] == 0
        assert summary[key]["totalKnown"] is False and summary[key]["total"] is None
    first = client.post(API + "/alerts/overview", headers=administrator, json={}).json()["data"]
    assert first["summary"]["reportsPage"]["total"] == first["summary"]["problemsPage"]["total"] == 0


def test_discovery_summary_keeps_late_primary_foreign_and_name_columns():
    stock = {"tableName": "stock", "columns": [
        {"columnName": f"note_{index}", "dataType": "TEXT"} for index in range(25)
    ] + [{"columnName": "stock_id", "primaryKey": True}, {"columnName": "warehouse_id"}],
        "foreignKeys": [{"columns": ["warehouse_id"], "referredTable": "warehouse", "referredColumns": ["id"]}],
        "declaredRelations": [{"sourceField": "warehouse_id", "targetTable": "warehouse",
                               "targetField": "id", "targetNameField": "warehouse_name"}]}
    warehouse = {"tableName": "warehouse", "columns": [
        {"columnName": f"note_{index}"} for index in range(25)
    ] + [{"columnName": "id", "primaryKey": True}, {"columnName": "warehouse_name"}]}
    view = _discovery_table_summary(stock, [stock, warehouse])
    fields = {row["name"]: row for row in view["fields"]}
    assert fields["stock_id"]["primaryKey"] is True and "warehouse_id" in fields
    assert view["fieldsTruncated"] is True and view["fieldCount"] == 27
    assert view["returnedFieldCount"] == 20 and view["foreignKeys"] == stock["foreignKeys"]
    lookup = _discovery_table_summary(warehouse, [stock, warehouse])
    assert {"id", "warehouse_name"}.issubset({row["name"] for row in lookup["fields"]})


def test_discovery_legacy_mysql_catalog_never_calls_database_or_embeddings(
    client, administrator, db, source, monkeypatch,
):
    row = db.get(DataSource, source["id"])
    row.provider = "mysql"
    row.connection_config = {key: value for key, value in row.connection_config.items()
                             if key not in {"autoDescription", "semanticIndexStatus", "semanticIndexedTableCount"}}
    metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id))
    metadata.tables_json = list(metadata.tables_json) + [
        {"tableName": name, "columns": [{"columnName": "id"}]} for name in ("t_meta_entity", "t_meta_field")
    ]
    db.execute(delete(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == row.id))
    db.commit()
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("目录发现不能连接业务库或外部模型")

    monkeypatch.setattr(semantic, "readonly_connection", forbidden)
    monkeypatch.setattr(semantic.model, "embed_texts", forbidden)
    response = client.post(API + "/data-sources/discover", headers=administrator, json={"keyword": "sales"})
    assert response.status_code == 200, response.text
    discovered = next(item for item in response.json()["data"]["sources"] if item["id"] == row.id)
    assert forbidden_calls == []
    assert discovered["returnedTableCount"] == len(discovered["tables"])
    assert discovered["tables"][0]["fields"][0]["primaryKey"] is True


def test_cached_semantic_refresh_preserves_current_relations_and_vectors(
    client, administrator, db, source, monkeypatch,
):
    row = db.get(DataSource, source["id"])
    metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id))
    table = metadata.tables_json[0]
    related = {"tableName": "departments", "columns": [{"columnName": "id", "primaryKey": True}]}
    tables = [table, related]
    entry = db.scalar(select(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == row.id))
    relation = {"sourceTable": "sales", "targetTable": "departments", "sourceField": "department",
                "targetField": "id", "relationType": "reference"}
    entry.relations_json = [relation]
    entry.embedding_json = [1.0, 0.0]
    db.commit()
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("缓存模式禁止任何外部数据读取")

    monkeypatch.setattr(semantic, "readonly_connection", forbidden)
    monkeypatch.setattr(semantic.model, "embed_texts", forbidden)
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    semantic.build_semantic_index(ctx, row, tables, cached_only=True)
    db.refresh(entry)
    assert entry.embedding_json == [1.0, 0.0]
    # 只有物理主键不足以确认低代码业务引用；保留旧声明用于定位，不继续提供错误 JOIN。
    saved = entry.relations_json[0]
    assert saved["joinKeysKnown"] is False and saved["sourceDeclared"]["targetField"] == "id"
    assert saved["sourceField"] == saved["targetField"] == ""
    recalled, _ = semantic.recall_tables(ctx, row, tables, "unmatched_request", cached_only=True)
    assert recalled and forbidden_calls == []


def test_http_discovery_and_tool_share_saved_field_contract_without_nested_model(
    client, administrator, db, upstream, monkeypatch,
):
    http_source, _ = create_http_source(client, administrator, upstream,
        name="财务与设备接口", allowAutomatic=True, fields=[
            {"name": "设备", "path": "$.device", "type": "string", "comment": "设备编号"},
            {"name": "金额", "path": "$.amount", "type": "decimal", "comment": "财务金额"},
            {"name": "启用", "path": "$.enabled", "type": "boolean"},
        ])
    response = client.post(API + "/data-sources/discover", headers=administrator,
                           json={"keyword": "财务设备"})
    assert response.status_code == 200, response.text
    discovered = next(row for row in response.json()["data"]["sources"] if row["id"] == http_source["id"])
    assert discovered["returnedTableCount"] == 1 and not upstream[1]
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin"))
    context = build_context(db=db, workspace_id=administrator["X-Workspace-Id"], user_id=identity.user_id,
                            run_id="isolated-http-contract", selected_datasource_ids=[http_source["id"]])

    def forbidden_model(*args, **kwargs):
        raise AssertionError("固定 HTTP 业务接口不需要再调用 SQL 生成模型")

    monkeypatch.setattr(semantic.model, "chat_for_run", forbidden_model)
    gateway = ToolGateway(db)
    rejected = gateway.execute(administrator["X-Workspace-Id"], "query_business_data",
                               {"query": "SELECT * FROM remote", "mode": "sql"}, context)
    assert rejected.status == "failed" and not upstream[1]
    result = gateway.execute(administrator["X-Workspace-Id"], "query_business_data",
                             {"query": "读取财务和设备指标", "mode": "natural_language", "max_rows": 1}, context)
    assert result.status == "success", result.error_summary
    assert result.data["data"] == [{"设备": "A", "金额": "123.450", "启用": False}]
    assert result.data["queryType"] == "http" and result.data["generatedSql"] == ""
    assert result.data["rowOrigins"] == [0] and len(upstream[1]) == 1
    history = db.get(QueryHistory, result.data["queryId"])
    assert history.query_text == "读取财务和设备指标"


def test_schema_tool_reads_only_cached_metadata_even_when_vectors_exist(
    client, administrator, db, source, monkeypatch,
):
    entry = db.scalar(select(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == source["id"]))
    entry.embedding_json = [1.0, 0.0]
    db.commit()
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("schema 工具不应触发业务连接或外部模型")

    monkeypatch.setattr(semantic, "readonly_connection", forbidden)
    monkeypatch.setattr(semantic.model, "embed_texts", forbidden)
    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin"))
    context = build_context(db=db, workspace_id=administrator["X-Workspace-Id"], user_id=identity.user_id,
                            run_id="isolated-schema-contract", selected_datasource_ids=[source["id"]])
    result = ToolGateway(db).execute(administrator["X-Workspace-Id"], "query_business_data",
                                    {"query": "unmatched_query", "mode": "schema"}, context)
    assert result.status == "success", result.error_summary
    assert result.data["tables"] and attempted == []


def test_cold_schema_recall_keeps_snapshot_relations_and_rejects_hidden_join_fields(
    client, administrator, db, source,
):
    row = db.get(DataSource, source["id"])
    metadata = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id))
    relation = {"sourceTable": "sales", "sourceField": "department", "targetTable": "departments",
                "targetField": "id", "targetNameField": "title", "relationType": "reference"}
    sales = {**metadata.tables_json[0], "declaredRelations": [relation,
        {**relation, "targetField": "hidden_id"}, {**relation, "sourceField": "hidden_fk"}]}
    departments = {"tableName": "departments", "columns": [{"columnName": "id"}, {"columnName": "title"}]}
    tables = [sales, departments]
    db.execute(delete(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == row.id))
    db.commit()
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    recalled, _ = semantic.recall_tables(ctx, row, tables, "sales", seed_k=1, max_extra=2, cached_only=True)
    assert {table["tableName"] for table in recalled} == {"sales", "departments"}
    selected = next(table for table in recalled if table["tableName"] == "sales")
    assert len(selected["declaredRelations"]) == 1
    saved = selected["declaredRelations"][0]
    assert saved["joinKeysKnown"] is False and saved["sourceDeclared"]["targetField"] == "id"
    assert saved["sourceDeclared"]["sourceField"] == "department"
    semantic.build_semantic_index(ctx, row, tables, cached_only=True)
    entry = db.scalar(select(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == row.id,
                                                           SourceSemanticDocument.table_name == "sales"))
    assert len(entry.relations_json) == 1
    assert entry.relations_json[0]["joinKeysKnown"] is False
    assert entry.relations_json[0]["sourceDeclared"]["targetField"] == "id"
