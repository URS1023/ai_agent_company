from decimal import Decimal
from unittest.mock import create_autospec, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, CursorResult, Engine
from sqlalchemy.exc import SQLAlchemyError
from test_data_sources import database, sql_read
from test_sql_plan_budget import mysql, postgres

from enterprise_platform.adapters.data_sources import DatabaseSourceReader
from enterprise_platform.adapters.sql_plan_budget import SqlPlanBudget
from enterprise_platform.domain.data_sources import DataSourceError, ReadLimits


def setup(dialect="postgresql", plan=None, values=((1,),), max_rows=100):
    engine = create_autospec(Engine, instance=True)
    connection = create_autospec(Connection, instance=True)
    planned = create_autospec(CursorResult, instance=True)
    cursor = create_autospec(CursorResult, instance=True)
    engine.connect.return_value.__enter__.return_value = connection
    connection.execution_options.return_value = connection
    planned.scalar_one.return_value = plan if plan is not None else (postgres() if dialect == "postgresql" else mysql())
    cursor.keys.return_value = ("value",)
    cursor.__iter__.return_value = iter(values)
    connection.execute.side_effect = lambda statement, params: (
        planned if str(statement).startswith("EXPLAIN") else cursor
    )
    config = database().model_copy(update={"limits": ReadLimits(max_rows=max_rows)})
    if dialect == "mysql":
        config = config.model_copy(
            update={"dialect": "mysql", "connection_url": SecretStr("mysql+pymysql://reader:fixture@db/test")}
        )
    with patch("enterprise_platform.adapters.data_sources.create_engine", return_value=engine):
        reader = DatabaseSourceReader(config)
    return reader, engine, connection, planned, cursor


@pytest.mark.parametrize(
    "dialect,prefix", [("postgresql", "EXPLAIN (FORMAT JSON) "), ("mysql", "EXPLAIN FORMAT=JSON ")]
)
def test_budgeted_read_checks_plan_before_executing_exact_parameterized_sql(dialect, prefix):
    reader, _, connection, planned, cursor = setup(dialect)
    sql = "SELECT count AS value FROM public.measurements WHERE device = :device"
    capture = reader.read(sql_read(sql), {"device": "0001"}, plan_budget=SqlPlanBudget(100, Decimal("10")))
    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert prefix + sql in statements
    assert not any("ANALYZE" in value for value in statements)
    assert statements[-1] == sql
    assert connection.execute.call_args.args[1] == {"device": "0001"}
    assert capture.rows == ((1,),)
    planned.close.assert_called_once()
    cursor.close.assert_called_once()
    connection.exec_driver_sql.assert_called_once_with("SET TRANSACTION READ ONLY")


@pytest.mark.parametrize("plan", [postgres(1001), {}, "broken"])
def test_rejected_plan_never_runs_the_data_query(plan):
    reader, _, connection, planned, _ = setup(plan=plan)
    with pytest.raises(DataSourceError):
        reader.read(
            sql_read("SELECT count FROM public.measurements"), {}, plan_budget=SqlPlanBudget(100, Decimal("10"))
        )
    connection.execution_options.assert_not_called()
    planned.close.assert_called_once()


def test_planner_driver_failure_is_opaque_and_never_retries_or_executes():
    reader, _, connection, planned, _ = setup()
    planned.scalar_one.side_effect = SQLAlchemyError("private driver detail")
    with pytest.raises(DataSourceError, match="read_failed") as error:
        reader.read(
            sql_read("SELECT count FROM public.measurements"), {}, plan_budget=SqlPlanBudget(100, Decimal("10"))
        )
    assert "private" not in str(error.value)
    connection.execution_options.assert_not_called()
    planned.close.assert_called_once()


def test_unsafe_sql_is_rejected_before_planning_or_connection():
    reader, engine, _, _, _ = setup()
    with pytest.raises(DataSourceError):
        reader.read(sql_read("DELETE FROM public.measurements"), {}, plan_budget=SqlPlanBudget(100, Decimal("10")))
    engine.connect.assert_not_called()


def test_expired_shared_budget_never_connects():
    reader, engine, _, _, _ = setup()
    with patch("time.monotonic", return_value=100.0), pytest.raises(DataSourceError, match="timeout"):
        reader.read(
            sql_read("SELECT count FROM public.measurements"),
            {},
            deadline=99.0,
            plan_budget=SqlPlanBudget(100, Decimal("10")),
        )
    engine.connect.assert_not_called()


def test_planning_consumes_the_execution_deadline():
    reader, _, connection, planned, _ = setup()
    now = [100.0]

    def result():
        now[0] = 101.0
        return postgres()

    planned.scalar_one.side_effect = result
    with patch("time.monotonic", side_effect=lambda: now[0]), pytest.raises(DataSourceError, match="timeout"):
        reader.read(
            sql_read("SELECT count FROM public.measurements"),
            {},
            deadline=101.0,
            plan_budget=SqlPlanBudget(100, Decimal("10")),
        )
    connection.execution_options.assert_not_called()
    planned.close.assert_called_once()


def test_plan_admission_does_not_disable_runtime_row_limit():
    reader, _, _, _, cursor = setup(values=((1,), (2,)), max_rows=1)
    with pytest.raises(DataSourceError, match="row_limit"):
        reader.read(
            sql_read("SELECT count FROM public.measurements"), {}, plan_budget=SqlPlanBudget(100, Decimal("10"))
        )
    cursor.close.assert_called_once()


@pytest.mark.parametrize("dialect,expected", [("postgresql", "500"), ("mysql", 500)])
def test_execution_statement_timeout_uses_remaining_planning_budget(dialect, expected):
    reader, _, connection, planned, _ = setup(dialect)
    now = [100.0]

    def result():
        now[0] = 100.5
        return postgres() if dialect == "postgresql" else mysql()

    planned.scalar_one.side_effect = result
    with patch("time.monotonic", side_effect=lambda: now[0]):
        reader.read(
            sql_read("SELECT count FROM public.measurements"),
            {},
            deadline=101.0,
            plan_budget=SqlPlanBudget(100, Decimal("10")),
        )
    timeouts = [call.args[1]["timeout"] for call in connection.execute.call_args_list if "timeout" in call.args[1]]
    assert timeouts[-1] == expected
