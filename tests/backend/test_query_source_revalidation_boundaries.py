"""来源重验的 ES 派发点、客户端范围和读事务边界；全部为临时平台或受控桩。"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db import new_session
from app.errors import ConflictError
from app.models import DataSource
from app.modules.analytics import execution, queries, sources
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.query_authority import QueryAuthority
from app.modules.analytics.sql_safety import authorize_sql
from test_analytics import API, source
from test_analytics_config_lifecycle import client
from test_analytics_task_control import query_context


def test_elastic_client_created_before_formal_edit_does_not_dispatch_sql(client, administrator, source, db, monkeypatch):
    row = db.get(DataSource, source["id"])
    row.provider = "elasticsearch"
    row.connection_config = {**row.connection_config, "host": "127.0.0.1", "port": 9200, "databaseName": "sales"}
    db.commit()
    tables = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id)).tables_json
    authority = QueryAuthority(query_context(db, administrator), row)
    authority.bind_schema(tables)
    query = authorize_sql("SELECT amount FROM sales", provider=row.provider, tables=tables, max_rows=2)
    client_stub = Mock()
    released = []
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)

    @contextmanager
    def opened(*_args, **_kwargs):
        response = client.put(API + "/datasources/" + row.id, headers=administrator, json={
            "name": "受控 ES", "type": "elasticsearch", "host": "127.0.0.1", "port": 9200,
            "databaseName": "sales", "version": 1, "allowedTables": ["sales"], "allowedColumns": {"sales": ["id"]},
        })
        assert response.status_code == 200, response.text
        try:
            yield client_stub
        finally:
            released.append(True)

    monkeypatch.setattr(execution, "elastic_client", opened)
    with pytest.raises(ConflictError):
        execution.execute_read(db, row, query, tables, before_dispatch=authority.check)
    client_stub.post.assert_not_called()
    assert released == [True]


@pytest.mark.parametrize("edit", ["scope", "disable"])
def test_service_client_changes_during_preparation_are_not_admin_authority(
    client, administrator, source, monkeypatch, edit,
):
    scopes = ["analytics.execute", "datasources.view"]
    created = client.post("/api/v1/system-tools/integration-clients", headers=administrator,
        json={"name": "查询隔离客户端", "redirectUris": [], "scopes": scopes})
    assert created.status_code == 201, created.text
    registration = created.json()
    issued = client.post("/api/auth/client-token", json={"clientId": registration["clientId"],
        "clientSecret": registration["clientSecret"], "workspaceId": administrator["X-Workspace-Id"]})
    assert issued.status_code == 200, issued.text
    headers = {"Authorization": "Bearer " + issued.json()["accessToken"],
               "X-Workspace-Id": administrator["X-Workspace-Id"]}

    def model(*_args, **_kwargs):
        url = "/api/v1/system-tools/integration-clients/" + registration["id"]
        if edit == "scope":
            response = client.patch(url, headers=administrator, json={"name": registration["name"],
                "version": registration["version"], "scopes": ["datasources.view"], "redirectUris": []})
        else:
            response = client.post(url + "/disable", headers=administrator, json={"version": registration["version"]})
        assert response.status_code == 200, response.text
        return "SELECT amount FROM sales", {}

    reader = Mock(side_effect=AssertionError("服务身份不能在撤权后继续执行"))
    monkeypatch.setattr(queries, "natural_language_sql", model)
    monkeypatch.setattr(queries, "execute_read", reader)
    response = client.post(API + "/query", headers=headers, json={
        "datasourceId": source["id"], "query": "查看 sales 金额", "mode": "natural_language"})
    assert response.status_code in {401, 403}, response.text
    assert reader.call_count == 0


@pytest.mark.parametrize("change", ["columns_same_version", "metadata_same_version"])
def test_unchanged_version_does_not_hide_changed_execution_contract(client, administrator, source, db, change):
    row = db.get(DataSource, source["id"])
    snapshot = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id))
    authority = QueryAuthority(query_context(db, administrator), row)
    authority.bind_schema(snapshot.tables_json)
    with new_session() as other:
        if change == "columns_same_version":
            current = other.get(DataSource, row.id)
            current.connection_config = {**current.connection_config, "allowedColumns": {"sales": ["id"]}}
        else:
            current = other.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id))
            tables = deepcopy(current.tables_json)
            tables[0]["columns"] = [field for field in tables[0]["columns"] if field["columnName"] != "amount"]
            current.tables_json = tables
        other.commit()
    with pytest.raises(ConflictError):
        authority.check()


def test_fresh_read_never_commits_pending_caller_changes_or_overwrites_scope(client, administrator, source, db):
    row = db.get(DataSource, source["id"])
    ctx = query_context(db, administrator)
    authority = QueryAuthority(ctx, row)
    row.name = "调用者尚未提交的改名"
    before_scope = dict(db.info)
    authority.check()
    assert db.is_modified(row) and db.info == before_scope
    with new_session() as other:
        assert other.get(DataSource, row.id).name == source["name"]


def test_caller_selected_permission_is_revalidated_without_replacing_it(client, administrator, source, db, monkeypatch):
    from test_system_foundation import create_role, create_user, activate_user
    role = create_role(client, administrator, code="query_custom_permission", scope="workspace",
        permissions=["home.view", "datasources.view", "analytics.export"])
    create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    from app.common.deps import resolve_principal, AuthContext
    from app.models import Workspace
    principal = resolve_principal(db, headers["Authorization"][7:])
    ctx = AuthContext(db, principal.user, db.get(Workspace, administrator["X-Workspace-Id"]), "member", "")
    from app.modules.analytics.schemas import QueryInput
    result = queries.execute_query(ctx, QueryInput(datasourceId=source["id"], query="SELECT amount FROM sales WHERE id=1", mode="sql"),
                                   permission="analytics.export")
    assert result.data == [{"amount": 100}]


def test_database_receipt_contains_replayable_history_without_analytics_execute(client, administrator, source, db):
    from app.modules.analytics.query_authority import authorize_query_replay
    from test_database_provider import connector, invoke
    from app.modules.analytics.models import QueryHistory
    cid = connector(client, administrator, source)
    result = invoke(client, administrator, cid, "query", {"sql": "SELECT amount FROM sales WHERE id=1"})
    assert result["status"] == "success", result
    record = db.get(QueryHistory, result["data"]["queryId"])
    assert record.result_json["data"] == result["data"]["rows"] == [{"amount": 100}]
    ctx = query_context(db, administrator)
    ctx.client_scopes = frozenset({"datasources.execute"})
    # 服务权限上限仅允许原数据库执行权限；不要隐式要求分析或历史查看权限。
    assert authorize_query_replay(ctx, db.get(DataSource, source["id"]), result, source_permission="datasources.execute")
