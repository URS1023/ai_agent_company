"""通过正式 HTTP/服务与临时平台事务复现查询租约及连接器计量，不访问业务库。"""
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql

from app.db import new_session
from app.models import DataSource
from app.modules.analytics import governance, queries
from app.modules.analytics.models import DataSourceQueryLease, QueryHistory
from app.modules.analytics.schemas import QueryInput
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, TaskControl, current
from app.providers.tool.base import ToolSpec
from app.providers.tool.gateway import ToolGateway, build_context
from app.providers.tool import database_provider
from test_analytics import API, source, run_sql
from test_analytics_task_control import query_context
from test_database_provider import connector, invoke
from test_system_foundation import activate_user, create_role, create_user


def set_policy(client, administrator, source, *, daily=10, concurrency=1):
    """使用正式治理编辑入口，不绕过策略版本合同。"""
    url = API + f"/datasources/{source['id']}/policy"
    version = client.get(url, headers=administrator).json()["data"]["version"]
    response = client.put(url, headers=administrator, json={"version": version,
        "maxConcurrency": concurrency, "dailyQueryLimit": daily,
        "healthCheckEnabled": False, "healthIntervalMinutes": 60})
    assert response.status_code == 200, response.text


def test_lease_reservation_counts_before_history_exists(client, administrator, source, db):
    """acquire 已提交但历史未插入的真实事务窗口也不能超发日额度。"""
    set_policy(client, administrator, source, daily=1, concurrency=2)
    ctx = query_context(db, administrator)
    first = governance.acquire_query_lease(ctx, db.get(DataSource, source["id"]))
    second = None
    try:
        with new_session() as other:
            other_ctx = query_context(other, administrator)
            with pytest.raises(governance.DataSourceDailyLimit):
                second = governance.acquire_query_lease(other_ctx, other.get(DataSource, source["id"]))
    finally:
        if second:
            governance.release_query_lease(ctx, second, source["id"])
        governance.release_query_lease(ctx, first, source["id"])


def test_expired_preparation_cannot_dispatch_sql_or_release_other_lease(client, administrator, source, db, monkeypatch):
    set_policy(client, administrator, source)
    clock = {"now": datetime.utcnow()}

    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return clock["now"]

    monkeypatch.setattr(governance, "datetime", Clock)
    other_ids = []

    def delayed_model(*args, **kwargs):
        clock["now"] += timedelta(seconds=500)
        with new_session() as other:
            other_ctx = query_context(other, administrator)
            other_ids.append(governance.acquire_query_lease(other_ctx, other.get(DataSource, source["id"])))
        return "SELECT amount FROM sales", {}

    read = Mock(return_value=([{"amount": 1}], [{"columnName": "amount", "dataType": "bigint"}], False))
    monkeypatch.setattr(queries, "natural_language_sql", delayed_model)
    monkeypatch.setattr(queries, "execute_read", read)
    ctx = query_context(db, administrator)
    try:
        with pytest.raises(governance.DataSourceBusy):
            queries.execute_query(ctx, QueryInput(datasourceId=source["id"], query="sales amount", mode="natural_language"))
        assert read.call_count == 0
        db.expire_all()
        assert [row.id for row in db.scalars(select(DataSourceQueryLease))] == other_ids
        assert db.scalars(select(QueryHistory)).one().status == "failed"
    finally:
        for lease_id in other_ids:
            governance.release_query_lease(ctx, lease_id, source["id"])


@pytest.mark.parametrize("limit", ["concurrency", "daily"])
def test_connector_query_enforces_formal_source_limits_before_driver(
    client, administrator, source, db, monkeypatch, limit,
):
    set_policy(client, administrator, source, daily=1)
    cid = connector(client, administrator, source)
    if limit == "concurrency":
        db.add(DataSourceQueryLease(workspace_id=administrator["X-Workspace-Id"], datasource_id=source["id"],
            user_id="occupied", expires_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()
    else:
        assert run_sql(client, administrator, source).status_code == 200
    read = Mock(side_effect=AssertionError("受限连接器不得派发 SQL"))
    monkeypatch.setattr(database_provider, "execute_read", read)
    output = invoke(client, administrator, cid, "query", {"sql": "SELECT amount FROM sales"})
    assert output["status"] == "failed"
    assert output["error_code"] == ("datasource_busy" if limit == "concurrency" else "datasource_daily_limit")
    assert read.call_count == 0


def test_connector_attempt_is_counted_once_then_blocks_next_query(client, administrator, source, db):
    set_policy(client, administrator, source, daily=1)
    cid = connector(client, administrator, source)
    first = invoke(client, administrator, cid, "query", {"sql": "SELECT amount FROM sales"})
    assert first["status"] == "success", first
    db.expire_all()
    history = db.scalars(select(QueryHistory)).one()
    assert history.status == "success" and history.generated_sql == first["data"]["sql"]
    assert history.run_id == first["runId"]
    # 新计量记录仍是合法 QueryResult，历史与导出都走原有授权链而不重查业务库。
    detail = client.get(API + "/history/" + history.id, headers=administrator)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["data"] == first["data"]["rows"]
    exported = client.post(API + "/export", headers=administrator, json={"queryId": history.id, "format": "json"})
    assert exported.status_code == 200, exported.text
    assert exported.json()["queryId"] == history.id
    assert db.scalars(select(DataSourceQueryLease)).all() == []
    second = invoke(client, administrator, cid, "query", {"sql": "SELECT id FROM sales"})
    assert second["status"] == "failed" and second["error_code"] == "datasource_daily_limit"
    status = client.get(API + f"/datasources/{source['id']}/pool-status", headers=administrator).json()["data"]
    assert status["queriesToday"] == 1 and status["activeQueries"] == 0


def test_main_query_history_and_active_lease_are_one_attempt(client, administrator, source, db, monkeypatch):
    set_policy(client, administrator, source, daily=1)
    original = queries.execute_read
    identities = []

    def read(*args, **kwargs):
        with new_session() as other:
            ctx = query_context(other, administrator)
            history = other.scalars(select(QueryHistory)).one()
            lease = other.scalars(select(DataSourceQueryLease)).one()
            identities.append((history.id, lease.id))
            assert governance._usage_counts(ctx, source["id"]) == (1, 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(queries, "execute_read", read)
    response = run_sql(client, administrator, source)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert identities == [(payload["queryId"], payload["queryId"])]
    db.expire_all()
    history = db.get(QueryHistory, payload["queryId"])
    assert history.run_id == payload["runId"] and history.result_json["queryId"] == history.id
    assert db.scalars(select(DataSourceQueryLease)).all() == []


@pytest.mark.parametrize("failure_type", [RuntimeError, ChatCancelled, ChatTaskTimeout])
def test_connector_failure_retains_one_attempt_and_releases_lease(
    client, administrator, source, db, monkeypatch, failure_type,
):
    set_policy(client, administrator, source, daily=1)
    actor = query_context(db, administrator)
    context = build_context(db=db, workspace_id=actor.workspace_id, user_id=actor.user_id, run_id="")
    spec = ToolSpec(name="database.query", description="隔离查询", provider="database",
                    input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
                    permissions=["datasources.execute"])
    control = TaskControl()
    failure = failure_type("不得返回的驱动内部明文")
    reader = Mock(side_effect=failure)
    monkeypatch.setattr(database_provider, "execute_read", reader)
    token = current.set(control)
    try:
        gateway = ToolGateway(db)
        if failure_type is RuntimeError:
            result = gateway.execute_spec(spec, {"sql": "SELECT amount FROM sales"},
                {"data_source_id": source["id"]}, context)
            assert result.status == "failed"
        else:
            with pytest.raises(failure_type) as raised:
                gateway.execute_spec(spec, {"sql": "SELECT amount FROM sales"},
                    {"data_source_id": source["id"]}, context)
            assert raised.value is failure
    finally:
        current.reset(token)
    assert reader.call_count == 1
    db.expire_all()
    history = db.scalars(select(QueryHistory)).one()
    assert history.status == "failed" and history.result_json == {}
    assert "驱动内部明文" not in history.error_message
    assert db.scalars(select(DataSourceQueryLease)).all() == []
    assert governance._usage_counts(actor, source["id"]) == (0, 1)
    with pytest.raises(governance.DataSourceDailyLimit):
        governance.acquire_query_lease(actor, db.get(DataSource, source["id"]))


def test_connector_keeps_source_permission_without_analytics_execute(client, administrator, source, db):
    set_policy(client, administrator, source, daily=1)
    cid = connector(client, administrator, source)
    role = create_role(client, administrator, code="connector_query_only", scope="workspace",
        permissions=["home.view", "connectors.view", "connectors.execute", "datasources.view", "datasources.execute"])
    create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    listed = invoke(client, headers, cid, "list_tables", {})
    assert listed["status"] == "success", listed
    assert db.scalars(select(QueryHistory)).all() == []
    result = invoke(client, headers, cid, "query", {"sql": "SELECT amount FROM sales"})
    assert result["status"] == "success", result
    assert run_sql(client, headers, source).status_code in {403, 404}


def test_reservation_counts_compile_to_current_locking_reads(client, administrator, source, db):
    """只证明实际发出语句的锁定读契约，不以 SQLite 冒充 MySQL RR 并发验证。"""
    actor = query_context(db, administrator)
    statements = []

    def observe(state):
        if state.is_select:
            statements.append(str(state.statement.compile(dialect=mysql.dialect())))

    event.listen(db, "do_orm_execute", observe)
    lease_id = None
    try:
        lease_id = governance.acquire_query_lease(actor, db.get(DataSource, source["id"]))
    finally:
        event.remove(db, "do_orm_execute", observe)
        if lease_id:
            governance.release_query_lease(actor, lease_id, source["id"])
    for table in ["analytics_query_history", "analytics_datasource_query_leases"]:
        usage = [statement for statement in statements if f"FROM {table}" in statement]
        assert usage and all("FOR UPDATE" in statement for statement in usage)


def test_sql_dispatch_guard_locks_source_before_flushing_history(client, administrator, source, db):
    """实际 SQL 发出次序必须父源锁在先，ORM autoflush 不能偷偷反转锁顺序。"""
    actor = query_context(db, administrator)
    row = db.get(DataSource, source["id"])
    lease_id = governance.acquire_query_lease(actor, row)
    history = QueryHistory(id=lease_id, workspace_id=actor.workspace_id, owner_user_id=actor.user_id,
                           datasource_id=row.id, query_text="SELECT amount FROM sales")
    db.add(history)
    db.commit()
    history.generated_sql = "SELECT amount FROM sales"
    order = []
    engine = db.get_bind()

    def observe(connection, cursor, statement, parameters, context, executemany):
        compiled = getattr(context, "compiled", None)
        expression = compiled.statement if compiled is not None else None
        if expression is None:
            return
        if getattr(expression, "is_update", False) and expression.table.name == "analytics_query_history":
            order.append("history_write")
        if (getattr(expression, "is_select", False) and expression._for_update_arg is not None
                and "FROM data_sources" in statement):
            order.append("source_lock")

    event.listen(engine, "before_cursor_execute", observe)
    try:
        governance.validate_query_lease(actor, row, lease_id)
        assert order.index("source_lock") < order.index("history_write"), order
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        db.rollback()
        governance.release_query_lease(actor, lease_id, row.id)


@pytest.mark.parametrize("daily", [0, 2])
def test_reservation_history_read_is_bounded_by_configured_daily_limit(client, administrator, source, db, daily):
    """不限日额度时仅检查并发；有限额度不需锁扫超过上限的历史。"""
    set_policy(client, administrator, source, daily=daily)
    actor = query_context(db, administrator)
    for _ in range(3):
        db.add(QueryHistory(workspace_id=actor.workspace_id, owner_user_id=actor.user_id,
                            datasource_id=source["id"], query_text="SELECT amount FROM sales", status="failed"))
    db.commit()
    statements = []

    def observe(state):
        if state.is_select:
            statements.append(str(state.statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})))

    event.listen(db, "do_orm_execute", observe)
    lease_id = None
    try:
        if daily:
            with pytest.raises(governance.DataSourceDailyLimit):
                governance.acquire_query_lease(actor, db.get(DataSource, source["id"]))
        else:
            lease_id = governance.acquire_query_lease(actor, db.get(DataSource, source["id"]))
    finally:
        event.remove(db, "do_orm_execute", observe)
        if lease_id:
            governance.release_query_lease(actor, lease_id, source["id"])
    history_reads = [statement for statement in statements if "FROM analytics_query_history" in statement]
    assert len(history_reads) == (1 if daily else 0)
    if daily:
        assert "LIMIT 2" in history_reads[0] and "FOR UPDATE" in history_reads[0]
