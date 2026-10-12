"""DSH 查询回执缓存与只读连接初始化也必须遵循本次任务停止信号。"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.analytics import connection_pool, connections, dsh_query_execution
from app.modules.runtime.task_control import ChatCancelled, TaskControl, current
from test_data_profile import profile_fixture
from test_dsh_query_execution import dsh_fixture, execute as execute_dsh


@pytest.mark.parametrize("phase", ["entry", "scope"])
def test_dsh_cached_receipt_cannot_bypass_cancellation(dsh_fixture, monkeypatch, phase):
    first = execute_dsh(dsh_fixture)
    assert first.status == "success" and len(dsh_fixture.calls) == 1
    original = dsh_query_execution.query_context

    def scoped(*args, **kwargs):
        result = original(*args, **kwargs)
        dsh_fixture.control.cancelled.set()
        return result

    if phase == "entry":
        dsh_fixture.control.cancelled.set()
    else:
        monkeypatch.setattr(dsh_query_execution, "query_context", scoped)
    with pytest.raises(ChatCancelled):
        execute_dsh(dsh_fixture)
    assert len(dsh_fixture.calls) == 1


def test_dsh_does_not_add_new_receipt_after_task_stops(dsh_fixture, monkeypatch):
    original = dsh_query_execution.execute_query

    def completed(*args, **kwargs):
        result = original(*args, **kwargs)
        dsh_fixture.control.cancelled.set()
        return result

    monkeypatch.setattr(dsh_query_execution, "execute_query", completed)
    with pytest.raises(ChatCancelled):
        execute_dsh(dsh_fixture)
    assert len(dsh_fixture.calls) == 1
    assert dsh_fixture.control.query_receipts == {}


@pytest.mark.parametrize("provider", ["mysql", "postgres"])
def test_readonly_setup_cancel_preserves_signal_and_invalidates_borrower(monkeypatch, provider):
    """只用假引擎确认 SET 后不再派发下一句，池借用仍归还，不连接数据库。"""
    control = TaskControl()
    engine = MagicMock()
    engine.dialect.is_mariadb = False
    connection = engine.connect.return_value
    connection.exec_driver_sql.side_effect = lambda sql: control.cancelled.set()

    @contextmanager
    def engine_context(*args, **kwargs):
        yield engine

    monkeypatch.setattr(connections, "sql_engine", engine_context)
    token = current.set(control)
    try:
        with connection_pool.read_batch():
            with pytest.raises(ChatCancelled):
                with connections.readonly_connection(None, SimpleNamespace(
                    provider=provider, id="temporary-source", status="active", connection_config={"timeoutSeconds": 3})):
                    pytest.fail("取消后不得到达业务 SQL")
            assert connection_pool.current_read_batch().connection is None
    finally:
        current.reset(token)
        engine.dispose()
    assert connection.exec_driver_sql.call_count == 1
    connection.invalidate.assert_called_once()
