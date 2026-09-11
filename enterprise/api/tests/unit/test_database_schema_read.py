from unittest.mock import create_autospec, patch

import pytest
from sqlalchemy import Connection, Engine, Integer, String
from sqlalchemy.exc import SQLAlchemyError
from test_data_sources import database

from enterprise_platform.adapters.data_sources import DatabaseSourceReader
from enterprise_platform.domain.data_sources import DataSourceError


def setup():
    engine = create_autospec(Engine, instance=True)
    connection = create_autospec(Connection, instance=True)
    engine.connect.return_value.__enter__.return_value = connection
    with patch("enterprise_platform.adapters.data_sources.create_engine", return_value=engine):
        reader = DatabaseSourceReader(database())
    return reader, engine, connection


def test_describes_only_requested_columns_in_read_only_transaction_without_sample_rows():
    reader, engine, connection = setup()
    with patch("enterprise_platform.adapters.data_sources.inspect") as inspector:
        inspector.return_value.get_columns.return_value = [
            {"name": "count", "type": Integer(), "comment": "private comment", "default": "private default"},
            {"name": "secret", "type": String()},
        ]
        result = reader.describe_columns("public.measurements", ("count",))
    assert result == (("count", "INTEGER"),)
    inspector.assert_called_once_with(connection)
    inspector.return_value.get_columns.assert_called_once_with("measurements", schema="public")
    connection.exec_driver_sql.assert_called_once_with("SET TRANSACTION READ ONLY")
    assert connection.execute.call_count == 1  # statement timeout only; inspector reads catalog metadata
    reader.close()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "table,columns",
    [("private.data", ("count",)), ("public.measurements", ()), ("public.measurements", ("count", "count"))],
)
def test_invalid_selection_never_connects(table, columns):
    reader, engine, _ = setup()
    with pytest.raises(DataSourceError):
        reader.describe_columns(table, columns)
    engine.connect.assert_not_called()


@pytest.mark.parametrize("case", ["missing", "duplicate", "driver", "too_many"])
def test_incomplete_or_failed_schema_is_not_returned(case):
    reader, _, _ = setup()
    with patch("enterprise_platform.adapters.data_sources.inspect") as inspector:
        columns = [{"name": "count", "type": Integer()}]
        if case == "missing":
            columns = []
        elif case == "duplicate":
            columns *= 2
        elif case == "too_many":
            columns *= 501
        inspector.return_value.get_columns.return_value = columns
        if case == "driver":
            inspector.return_value.get_columns.side_effect = SQLAlchemyError("private connection error")
        with pytest.raises(DataSourceError) as error:
            reader.describe_columns("public.measurements", ("count",))
    assert "private" not in str(error.value)


def test_expired_shared_deadline_never_opens_a_connection():
    reader, engine, _ = setup()
    with patch("time.monotonic", return_value=100.0), pytest.raises(DataSourceError, match="timeout"):
        reader.describe_columns("public.measurements", ("count",), deadline=99.0)
    engine.connect.assert_not_called()


@pytest.mark.parametrize("deadline,expected", [(100.5, "500"), (1000.0, "15000")])
def test_shared_deadline_shortens_but_never_extends_configured_statement_timeout(deadline, expected):
    reader, _, connection = setup()
    with (
        patch("time.monotonic", return_value=100.0),
        patch("enterprise_platform.adapters.data_sources.inspect") as inspector,
    ):
        inspector.return_value.get_columns.return_value = [{"name": "count", "type": Integer()}]
        assert reader.describe_columns("public.measurements", ("count",), deadline=deadline) == (("count", "INTEGER"),)
    assert connection.execute.call_args.args[1] == {"timeout": expected}


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_deadline_never_opens_a_connection(deadline):
    reader, engine, _ = setup()
    with pytest.raises(DataSourceError, match="query_rejected"):
        reader.describe_columns("public.measurements", ("count",), deadline=deadline)
    engine.connect.assert_not_called()


def test_metadata_returning_after_shared_deadline_is_discarded():
    reader, _, _ = setup()
    now = [100.0]

    def describe(*args, **kwargs):
        now[0] = 102.0
        return [{"name": "count", "type": Integer()}]

    with (
        patch("time.monotonic", side_effect=lambda: now[0]),
        patch("enterprise_platform.adapters.data_sources.inspect") as inspector,
    ):
        inspector.return_value.get_columns.side_effect = describe
        with pytest.raises(DataSourceError, match="timeout"):
            reader.describe_columns("public.measurements", ("count",), deadline=101.0)
