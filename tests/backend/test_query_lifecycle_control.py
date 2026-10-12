"""查询取消链路只使用临时 SQLite、内存控制块和驱动桩，禁止真实库与模型。"""
from contextlib import contextmanager
import sqlite3
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.errors import ToolExecutionError
from app.models import DataSource, Run
from app.modules.analytics import execution, metadata, queries, semantic
from app.modules.analytics.models import DataSourceQueryLease, QueryHistory, SourceSemanticDocument
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sql_safety import authorize_sql
from app.modules.runtime.task_control import ChatCancelled, ChatTaskTimeout, TaskControl, current
from app.providers import model
from test_analytics import source
from test_analytics_task_control import query_context
from test_data_profile import profile_fixture
from test_dsh_query_execution import dsh_fixture, execute as execute_dsh


@pytest.mark.parametrize("phase", ["before_sql", "after_sql"])
def test_query_cancellation_stops_dispatch_or_late_success(client, administrator, source, db, monkeypatch, phase):
    """取消发生在准备末尾或返回途中，都不能登记成功或再发 SQL。"""
    control = TaskControl()
    original_metadata = queries.source_metadata
    observed = []

    def prepared(*args, **kwargs):
        result = original_metadata(*args, **kwargs)
        if phase == "before_sql":
            control.cancelled.set()
        return result

    def read(*args, **kwargs):
        observed.append(kwargs.get("cancelled"))
        control.cancelled.set()
        return [{"amount": 1}], [{"columnName": "amount", "dataType": "bigint"}], False

    monkeypatch.setattr(queries, "source_metadata", prepared)
    monkeypatch.setattr(queries, "execute_read", read)
    token = current.set(control)
    try:
        with pytest.raises(ChatCancelled):
            queries.execute_query(query_context(db, administrator), QueryInput(
                datasourceId=source["id"], query="SELECT amount FROM sales", mode="sql"))
    finally:
        current.reset(token)
    assert observed == ([] if phase == "before_sql" else [control.cancelled])
    db.expire_all()
    history = db.scalars(select(QueryHistory)).one()
    assert history.status == "failed" and history.result_json == {}
    assert db.get(Run, history.run_id).error_code == "chat_cancelled"
    assert db.scalars(select(DataSourceQueryLease)).all() == []


def test_dsh_late_cancel_cannot_cache_successful_query_receipt(dsh_fixture, db, monkeypatch):
    """保留真实 DSH 网关/用户范围合同，只替换最底层的业务读取。"""
    calls = []

    def read(*args, **kwargs):
        calls.append(kwargs.get("cancelled"))
        dsh_fixture.control.cancelled.set()
        return [{"amount": 200}], [{"columnName": "amount", "dataType": "bigint"}], False

    monkeypatch.setattr(queries, "execute_read", read)
    with pytest.raises(ChatCancelled):
        execute_dsh(dsh_fixture)
    assert calls == [dsh_fixture.control.cancelled]
    assert dsh_fixture.control.query_receipts == {}
    db.expire_all()
    assert db.scalars(select(QueryHistory)).one().status == "failed"
    assert db.scalars(select(DataSourceQueryLease)).all() == []


def _fake_read(monkeypatch, event, *, failure=None):
    """假驱动保留进度回调和连接退出事实，不执行任何 SQL。"""
    state = SimpleNamespace(calls=0, exited=False, progress=None)
    raw = SimpleNamespace(set_progress_handler=lambda callback, count: setattr(state, "progress", callback),
                          set_authorizer=lambda callback: None)

    def execute(*args):
        state.calls += 1
        if failure:
            if failure == "cancel":
                event.set()
                assert state.progress() == 1
            raise sqlite3.OperationalError("interrupted" if failure == "cancel" else "unavailable")
        return SimpleNamespace(keys=lambda: ["amount"], fetchmany=lambda count: [(1,)])

    @contextmanager
    def connection(*args, **kwargs):
        try:
            yield SimpleNamespace(connection=SimpleNamespace(driver_connection=raw), exec_driver_sql=execute)
        finally:
            state.exited = True

    monkeypatch.setattr(execution, "readonly_connection", connection)
    return state


@pytest.mark.parametrize("phase", ["before", "during", "ordinary_failure"])
def test_sqlite_driver_event_has_correct_terminal_identity(monkeypatch, phase):
    event = Event()
    if phase == "before":
        event.set()
    state = _fake_read(monkeypatch, event, failure={"during": "cancel", "ordinary_failure": "ordinary"}.get(phase))
    source = SimpleNamespace(provider="sqlite", connection_config={"timeoutSeconds": 3})
    tables = [{"tableName": "sales", "columns": [{"columnName": "amount", "dataType": "INTEGER"}]}]
    safe = authorize_sql("SELECT amount FROM sales", provider="sqlite", tables=tables, max_rows=2)
    expected = ToolExecutionError if phase == "ordinary_failure" else ChatCancelled
    with pytest.raises(expected):
        execution.execute_read(None, source, safe, tables, cancelled=event)
    assert state.calls == (0 if phase == "before" else 1)
    assert phase == "before" or state.exited


def test_metadata_cancellation_stops_next_inspector_call(client, administrator, source, db, monkeypatch):
    control = TaskControl()
    inspector = Mock()
    inspector.get_table_names.side_effect = lambda: (control.cancelled.set() or ["sales"])
    inspector.get_view_names.return_value = []
    inspector.get_pk_constraint.return_value = {"constrained_columns": []}
    inspector.get_table_comment.return_value = {}
    inspector.get_columns.return_value = [{"name": "amount", "type": "INTEGER"}]
    inspector.get_foreign_keys.return_value = []
    monkeypatch.setattr(metadata, "inspect", lambda connection: inspector)
    token = current.set(control)
    try:
        with pytest.raises(ChatCancelled):
            metadata.source_metadata(query_context(db, administrator), db.get(DataSource, source["id"]), refresh=True)
    finally:
        current.reset(token)
        db.rollback()
    assert inspector.get_table_names.call_count == 1
    assert inspector.get_view_names.call_count == inspector.get_columns.call_count == 0


@pytest.mark.parametrize("phase", ["build", "recall"])
@pytest.mark.parametrize("failure_type", [ChatCancelled, ChatTaskTimeout])
def test_semantic_model_control_exception_is_not_keyword_degradation(
    client, administrator, source, db, monkeypatch, phase, failure_type,
):
    ctx = query_context(db, administrator)
    row = db.get(DataSource, source["id"])
    tables = metadata.source_metadata(ctx, row).tables_json
    if phase == "recall":
        document = db.scalar(select(SourceSemanticDocument).where(SourceSemanticDocument.datasource_id == row.id))
        assert document is not None
        document.embedding_json = [1.0, 0.0]
        db.commit()
    failure = failure_type("仅内部诊断内容")
    embedding = Mock(side_effect=failure)
    monkeypatch.setattr(model, "embed_texts", embedding)
    with pytest.raises(failure_type) as raised:
        if phase == "build":
            semantic.build_semantic_index(ctx, row, tables)
        else:
            semantic.recall_tables(ctx, row, tables, "zzzzzz", include_samples=False)
    assert raised.value is failure and embedding.call_count == 1
    db.rollback()


@pytest.mark.parametrize("phase", ["before", "after"])
def test_driver_task_deadline_stops_new_or_late_success(monkeypatch, phase):
    control = TaskControl(deadline=time.monotonic() - 1 if phase == "before" else None)
    state = _fake_read(monkeypatch, control.cancelled)
    original = execution.driver_query

    def driver_query(*args, **kwargs):
        result = original(*args, **kwargs)
        if phase == "after":
            control.deadline = time.monotonic() - 1
        return result

    monkeypatch.setattr(execution, "driver_query", driver_query)
    tables = [{"tableName": "sales", "columns": [{"columnName": "amount", "dataType": "INTEGER"}]}]
    safe = authorize_sql("SELECT amount FROM sales", provider="sqlite", tables=tables, max_rows=2)
    token = current.set(control)
    try:
        with pytest.raises(ChatTaskTimeout):
            execution.execute_read(None, SimpleNamespace(provider="sqlite", connection_config={}), safe, tables)
    finally:
        current.reset(token)
    assert state.calls == 0


@pytest.mark.parametrize("failure_type", [ChatCancelled, ChatTaskTimeout])
def test_optional_semantic_samples_do_not_swallow_task_stop(monkeypatch, failure_type):
    failure = failure_type("停止样本读取")
    driver = Mock(side_effect=failure)

    @contextmanager
    def connection(*args):
        yield SimpleNamespace(exec_driver_sql=driver)

    monkeypatch.setattr(semantic, "readonly_connection", connection)
    tables = [{"tableName": name, "columns": [{"columnName": "amount", "dataType": "INTEGER"}]}
              for name in ["sales", "inventory"]]
    with pytest.raises(failure_type) as raised:
        semantic.attach_safe_samples(None, SimpleNamespace(provider="sqlite"), tables)
    assert raised.value is failure and driver.call_count == 1
