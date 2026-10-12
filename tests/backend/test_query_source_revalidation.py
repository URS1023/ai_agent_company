"""临时文件 SQLite 的正式编辑与查询入口，验证准备期间变更不会派发旧查询。"""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from sqlalchemy import select

from app.modules.analytics import execution, queries, sources
from app.modules.analytics.models import DataSourceQueryLease, QueryHistory
from test_analytics import API, source
from test_analytics_config_lifecycle import client
from test_system_foundation import activate_user, create_role, create_user
from test_database_provider import connector, invoke


def edit_source(client, headers, source, *, enabled=True):
    """通过正式入口收窄字段，独立请求必须取得新版本回执。"""
    response = client.put(API + "/datasources/" + source["id"], headers=headers, json={
        "name": source["name"], "type": "sqlite", "databaseName": source["databaseName"],
        "allowedTables": ["sales"], "allowedColumns": {"sales": ["id"]},
        "version": source["version"], "enabled": enabled,
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["version"] == source["version"] + 1


def test_http_model_preparation_source_edit_stops_before_execution(client, administrator, source, db, monkeypatch):
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    calls = []

    def model(*_args, **_kwargs):
        calls.append("model")
        edit_source(client, administrator, source)
        return "SELECT amount FROM sales", {}

    reader = Mock(return_value=([{"amount": 100}], [{"columnName": "amount", "dataType": "integer"}], False))
    monkeypatch.setattr(queries, "natural_language_sql", model)
    monkeypatch.setattr(queries, "execute_read", reader)
    response = client.post(API + "/query", headers=administrator, json={
        "datasourceId": source["id"], "query": "查看 sales 金额", "mode": "natural_language",
    })
    assert response.status_code == 409, response.text
    assert reader.call_count == 0
    assert calls == ["model"]
    db.expire_all()
    history = db.scalars(select(QueryHistory)).one()
    assert history.status == "failed" and not history.result_json
    assert db.scalars(select(DataSourceQueryLease)).all() == []


@pytest.mark.parametrize("change", ["disable", "delete"])
def test_http_sql_preparation_state_change_never_queries(client, administrator, source, db, monkeypatch, change):
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    original = queries.authorize_sql

    def authorize(*args, **kwargs):
        value = original(*args, **kwargs)
        if change == "delete":
            response = client.delete(API + "/datasources/" + source["id"], headers=administrator)
            assert response.status_code == 200, response.text
        else:
            edit_source(client, administrator, source, enabled=False)
        return value

    reader = Mock(side_effect=AssertionError("来源停用后不应读取业务库"))
    monkeypatch.setattr(queries, "authorize_sql", authorize)
    monkeypatch.setattr(queries, "execute_read", reader)
    response = client.post(API + "/execute-sql", headers=administrator,
        params={"datasourceId": source["id"], "sql": "SELECT amount FROM sales"})
    assert response.status_code in {404, 409, 422}, response.text
    assert reader.call_count == 0
    db.expire_all()
    assert db.scalars(select(QueryHistory)).one().status == "failed"
    assert db.scalars(select(DataSourceQueryLease)).all() == []


def test_role_revocation_during_model_wait_rechecks_fresh_policy(client, administrator, source, monkeypatch):
    role = create_role(client, administrator, code="query_preparation", scope="workspace",
                       permissions=["home.view", "datasources.view", "analytics.execute"])
    create_user(client, administrator, [role["id"]])
    headers = activate_user(client)

    def model(*_args, **_kwargs):
        changed = client.patch("/api/v1/system/roles/" + role["id"], headers=administrator,
            json={"version": role["version"], "permissions": ["home.view", "datasources.view"]})
        assert changed.status_code == 200, changed.text
        return "SELECT amount FROM sales", {}

    reader = Mock(side_effect=AssertionError("旧策略不得继续派发查询"))
    monkeypatch.setattr(queries, "natural_language_sql", model)
    monkeypatch.setattr(queries, "execute_read", reader)
    response = client.post(API + "/query", headers=headers, json={
        "datasourceId": source["id"], "query": "查看 sales 金额", "mode": "natural_language",
    })
    assert response.status_code == 403, response.text
    assert reader.call_count == 0


def test_source_edit_after_connection_borrow_stops_at_actual_dispatch(client, administrator, source, monkeypatch):
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    original = execution.readonly_connection
    dispatched = []
    released = []

    @contextmanager
    def borrowed(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            edit_source(client, administrator, source)
            driver = connection.exec_driver_sql
            monkeypatch.setattr(connection, "exec_driver_sql", lambda sql, *a, **kw:
                (dispatched.append(sql), driver(sql, *a, **kw))[1])
            try:
                yield connection
            finally:
                released.append(True)

    monkeypatch.setattr(execution, "readonly_connection", borrowed)
    response = client.post(API + "/query", headers=administrator, json={
        "datasourceId": source["id"], "query": "SELECT amount FROM sales", "mode": "sql",
    })
    assert response.status_code == 409, response.text
    assert dispatched == [] and released == [True]


def test_database_tool_during_preparation_keeps_original_scope(client, administrator, source, db, monkeypatch):
    from app.providers.tool import database_provider
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    cid = connector(client, administrator, source)
    original = database_provider.authorize_sql

    def authorize(*args, **kwargs):
        value = original(*args, **kwargs)
        edit_source(client, administrator, source)
        return value

    reader = Mock(side_effect=AssertionError("连接器不得沿旧配置发查询"))
    monkeypatch.setattr(database_provider, "authorize_sql", authorize)
    monkeypatch.setattr(database_provider, "execute_read", reader)
    result = invoke(client, administrator, cid, "query", {"sql": "SELECT amount FROM sales"})
    assert result["status"] == "failed" and result["error_code"] == "conflict", result
    assert reader.call_count == 0
    db.expire_all()
    assert db.scalars(select(QueryHistory)).one().status == "failed"
    assert db.scalars(select(DataSourceQueryLease)).all() == []


def test_unchanged_source_and_derived_profile_do_not_block_query(client, administrator, source, db, monkeypatch):
    from app.db import new_session
    from app.models import DataSource
    original = queries.authorize_sql

    def authorize(*args, **kwargs):
        value = original(*args, **kwargs)
        with new_session() as other:
            row = other.get(DataSource, source["id"])
            row.connection_config = {**row.connection_config, "tableCount": 99, "autoDescription": "派生说明"}
            other.commit()
        return value

    monkeypatch.setattr(queries, "authorize_sql", authorize)
    response = client.post(API + "/query", headers=administrator, json={
        "datasourceId": source["id"], "query": "SELECT amount FROM sales WHERE id = 1", "mode": "sql",
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["data"] == [{"amount": 100}]


def test_explicit_source_grant_revoked_during_model_wait_never_queries(client, administrator, source, db, monkeypatch):
    from app.common.deps import resolve_principal
    role = create_role(client, administrator, code="query_shared_source", scope="self",
                       permissions=["home.view", "datasources.view", "analytics.execute"])
    create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    user = resolve_principal(db, headers["Authorization"][7:]).user
    granted = client.post("/api/v1/system/resource-grants", headers=administrator, json={
        "resource_type": "datasources", "resource_id": source["id"],
        "subject_type": "user", "subject_id": user.id, "actions": ["view"]})
    assert granted.status_code == 201, granted.text

    def model(*_args, **_kwargs):
        response = client.delete("/api/v1/system/resource-grants/" + granted.json()["id"], headers=administrator)
        assert response.status_code == 200, response.text
        return "SELECT amount FROM sales", {}

    reader = Mock(side_effect=AssertionError("共享权限撤回后不能继续查询"))
    monkeypatch.setattr(queries, "natural_language_sql", model)
    monkeypatch.setattr(queries, "execute_read", reader)
    response = client.post(API + "/query", headers=headers, json={
        "datasourceId": source["id"], "query": "查看 sales 金额", "mode": "natural_language"})
    assert response.status_code in {403, 404}, response.text
    assert reader.call_count == 0
