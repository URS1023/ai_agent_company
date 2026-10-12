"""Connection lifecycle coverage uses only isolated in-memory/file SQLite."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.errors import ToolExecutionError, ValidationError
from app.models import DataSource
from app.modules.analytics import connection_pool as pools, connections
from app.modules.analytics.models import QueryHistory, DataSourceQueryLease
from test_analytics import source
from test_workflow_table_values import config
from test_workflows import create_flow, execute, graph, node, result


@pytest.fixture(autouse=True)
def clean_pools():
    pools.dispose_read_pools()
    yield
    pools.release_read_batch()
    pools.dispose_read_pools()


def factory():
    return create_engine("sqlite://", poolclass=QueuePool, pool_size=2,
                         max_overflow=0, pool_timeout=0.01)


def test_pool_reuses_physical_connection_and_bounds_checkout():
    key = ("workspace", "source", "v1")
    with pools.pooled_engine(key, factory) as engine:
        with engine.connect() as first:
            raw = first.connection.driver_connection
        with engine.connect() as second:
            assert second.connection.driver_connection is raw
            with engine.connect():
                with pytest.raises(PoolTimeout):
                    engine.connect()
        assert engine.pool.checkedout() == 0


def test_pool_generation_and_workspace_isolation():
    def get(key):
        with pools.pooled_engine(key, factory) as engine:
            with engine.connect():
                pass
            return engine
    original = get(("w1", "s", "v1"))
    assert get(("w1", "s", "v1")) is original
    assert get(("w2", "s", "v1")) is not original
    assert get(("w1", "s", "new-password")) is not original
    assert original.pool.checkedin() == 0
    assert ("w2", "s", "v1") in pools._pools


def test_busy_pool_is_retired_on_source_update_and_closed_on_return():
    key = ("w", "s", "v1")
    with pools.read_batch():
        with pools.pooled_engine(key, factory) as engine:
            with pools.borrow_read_connection(engine, reusable=True):
                pass
        pools.dispose_source_pools("w", "s")
        assert key in pools._pools
        with pytest.raises(ToolExecutionError):
            with pools.pooled_engine(key, factory):
                pass
    assert key not in pools._pools
    assert engine.pool.checkedin() == 0


def test_registry_never_evicts_borrowed_connections_to_exceed_budget(monkeypatch):
    monkeypatch.setattr(pools, "MAX_POOLS", 1)
    with pools.pooled_engine(("w", "s", "1"), factory) as engine:
        with engine.connect():
            with pytest.raises(ToolExecutionError):
                with pools.pooled_engine(("w", "other", "1"), factory):
                    pass
    with pools.pooled_engine(("w", "other", "1"), factory):
        assert len(pools._pools) == 1


def test_idle_reaper_does_not_connect_or_close_borrowers(monkeypatch):
    with pools.pooled_engine(("w", "s", "1"), factory) as engine:
        with engine.connect():
            monkeypatch.setattr(pools, "IDLE_SECONDS", -1)
            pools.reap_idle_pools()
            assert len(pools._pools) == 1
    assert not pools._pools


def test_batch_reuses_checkout_switches_sources_and_releases_on_exception():
    a, b = factory(), factory()
    try:
        with pytest.raises(ValueError), pools.read_batch():
            with pools.borrow_read_connection(a, reusable=True) as first:
                pass
            with pools.borrow_read_connection(a, reusable=True) as second:
                assert first is second
            with pools.borrow_read_connection(b, reusable=True):
                assert a.pool.checkedout() == 0
                raise ValueError("stop")
        assert a.pool.checkedout() == b.pool.checkedout() == 0
        assert pools.current_read_batch() is None
    finally:
        a.dispose()
        b.dispose()


def test_nested_batch_and_worker_do_not_share_connections():
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    engine = factory()
    try:
        with pools.read_batch():
            with pools.borrow_read_connection(engine, reusable=True):
                pass
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(copy_context().run, pools.current_read_batch).result() is None
            with pools.read_batch():
                assert engine.pool.checkedout() == 0
            assert pools.current_read_batch() is not None
    finally:
        engine.dispose()


def test_mysql_transactions_reset_per_query_and_errors_are_not_retried(monkeypatch):
    from unittest.mock import MagicMock
    engine = MagicMock()
    engine.dialect.is_mariadb = False
    connection = engine.connect.return_value
    source_row = SimpleNamespace(provider="mysql", id="s", status="active", connection_config={"timeoutSeconds": 3})

    @contextmanager
    def engine_context(*args, **kwargs):
        yield engine

    monkeypatch.setattr(connections, "sql_engine", engine_context)
    with pools.read_batch():
        for _ in range(2):
            with connections.readonly_connection(None, source_row):
                pass
        assert engine.connect.call_count == 1
        assert connection.rollback.call_count == 2
        assert [call.args[0] for call in connection.exec_driver_sql.call_args_list] == [
            "SET TRANSACTION READ ONLY", "START TRANSACTION READ ONLY",
            "SET SESSION MAX_EXECUTION_TIME=3000",
        ] * 2
        with pytest.raises(ValueError):
            with connections.readonly_connection(None, source_row):
                raise ValueError("failed query")
        connection.invalidate.assert_called_once()
        assert engine.connect.call_count == 1
        assert pools.current_read_batch().connection is None


def test_saved_engine_rotates_credentials_and_bypasses_pool_for_writes_and_previews(monkeypatch):
    calls = []
    password = ["fixture-password-one"]
    def sqlite_factory(url, **options):
        calls.append(options)
        return create_engine("sqlite://", **{key: value for key, value in options.items()
                                            if key not in {"connect_args", "hide_parameters"}})
    monkeypatch.setattr(connections, "create_engine", sqlite_factory)
    monkeypatch.setattr(connections, "connection_password", lambda *_: password[0])
    row = DataSource(id="source", workspace_id="workspace", provider="mysql", status="active",
                     connection_config={"host": "127.0.0.1", "databaseName": "fixture"})
    with connections.sql_engine(None, row) as original:
        pass
    with connections.sql_engine(None, row) as reused:
        assert reused is original and len(calls) == 1
    row.connection_config = {**row.connection_config, "metadataSyncedAt": "2026-09-14"}
    with connections.sql_engine(None, row) as reused:
        assert reused is original  # Catalog refresh does not change a connection.
    password[0] = "fixture-password-two"
    with connections.sql_engine(None, row) as changed:
        assert changed is not original and len(calls) == 2
    for options in ({"write": True}, {"password_override": "preview"}):
        with connections.sql_engine(None, row, **options) as temporary:
            assert type(temporary.pool).__name__ == "NullPool"
    assert calls[0]["pool_size"] == 2 and calls[0]["max_overflow"] == 0


def test_metadata_returns_connection_before_embedding_index(client, administrator, source, db, monkeypatch):
    from app.modules.analytics import metadata, semantic
    path = connections.managed_database_path(administrator["X-Workspace-Id"], source["databaseName"])
    engine = create_engine("sqlite:///" + path.as_posix(), poolclass=QueuePool)
    @contextmanager
    def sqlite_engine(*args, **kwargs):
        yield engine
    monkeypatch.setattr(connections, "sql_engine", sqlite_engine)
    monkeypatch.setattr(connections, "reusable_source", lambda *args, **kwargs: True)
    called = []
    def build_index(*args):
        assert engine.pool.checkedout() == 0
        called.append(True)
        return {}
    monkeypatch.setattr(semantic, "build_semantic_index", build_index)
    ctx = SimpleNamespace(db=db, workspace_id=administrator["X-Workspace-Id"])
    try:
        with pools.read_batch():
            metadata.source_metadata(ctx, db.get(DataSource, source["id"]), refresh=True)
        assert called == [True] and engine.pool.checkedout() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("boundary", ["end", "delay", "llm", "question", "failed", "cancelled"])
def test_workflow_reads_share_checkout_and_release_before_other_nodes(
    client, administrator, source, db, monkeypatch, boundary,
):
    from app.modules.workflows import engine as workflow_engine
    # Exercise the real workflow, authorization, SQL and history on local SQLite;
    # only the adapter/pooling eligibility is substituted for the MySQL adapter.
    path = connections.managed_database_path(administrator["X-Workspace-Id"], source["databaseName"])
    read_engine = create_engine("sqlite:///" + path.as_posix(), poolclass=QueuePool,
                                pool_size=2, max_overflow=0, connect_args={"check_same_thread": False})
    checkouts = []
    event.listen(read_engine, "checkout", lambda *args: checkouts.append(1))

    @contextmanager
    def sqlite_engine(*args, **kwargs):
        yield read_engine

    monkeypatch.setattr(connections, "sql_engine", sqlite_engine)
    monkeypatch.setattr(connections, "reusable_source", lambda *args, **kwargs: True)
    configs = {"delay": {"seconds": 1}, "question": {"prompt": "确认"},
               "llm": {"modelProfile": "reasoning", "promptTemplate": "{{ input }}"}}
    middle = [node("boundary", boundary, configs[boundary])] if boundary in configs else []
    flow = create_flow(client, administrator, graph(
        node("first", "read-values", config(source)),
        node("second", "read-values", config(source)),
        *middle,
    ))
    original = workflow_engine.execute_node
    seen = []
    finished_reads = []
    def execute_node(ctx, current, *args, **kwargs):
        if current.kind == boundary:
            assert read_engine.pool.checkedout() == 0
            seen.append(boundary)
            if boundary == "llm":
                return args[0]["input"]  # No model/network in this lifecycle test.
        value = original(ctx, current, *args, **kwargs)
        if current.kind == "read-values":
            finished_reads.append(current.id)
        if current.id == "second" and boundary == "failed":
            raise ValidationError("Simulated post-query node failure")
        return value
    monkeypatch.setattr(workflow_engine, "execute_node", execute_node)
    if boundary == "cancelled":
        monkeypatch.setattr(workflow_engine, "_cancel_requested", lambda *_: len(finished_reads) == 2)
    try:
        http_status = 202 if boundary in {"delay", "question"} else 502 if boundary == "failed" else 200
        run = result(execute(client, administrator, flow), http_status)
        status = "waiting" if boundary in {"delay", "question"} else boundary if boundary in {"failed", "cancelled"} else "completed"
        assert run["status"] == status, run
        assert len(checkouts) == 1
        assert seen == ([] if boundary in {"failed", "cancelled"} else [boundary])
        assert read_engine.pool.checkedout() == 0
        histories = db.scalars(select(QueryHistory).where(QueryHistory.datasource_id == source["id"])).all()
        assert len(histories) == 2 and all(h.status == "success" for h in histories)
        assert not db.scalars(select(DataSourceQueryLease)).all()
    finally:
        pools.release_read_batch()
        read_engine.dispose()
