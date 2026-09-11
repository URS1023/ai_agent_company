"""Real SQLite driver verification, deliberately excluded from local unit runs.

The CI fixture writes only its own temporary database before the read-only adapter
opens it. PostgreSQL/MySQL read-only roles, TLS and server timeouts require their
own deployment integration jobs; these SQLite checks do not certify those drivers.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from enterprise_platform.adapters.data_sources import DatabaseSourceReader
from enterprise_platform.domain.data_sources import (
    DatabaseSourceConfig,
    DataSourceError,
    ReadLimits,
    SourceRef,
    SqlRead,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CI") != "true", reason="CI-only database read"),
]


@pytest.fixture
def config(tmp_path: Path) -> DatabaseSourceConfig:
    url = f"sqlite:///{(tmp_path / 'source.sqlite').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE measurements (id TEXT PRIMARY KEY, reading TEXT, zero INTEGER, optional TEXT)")
        )
        connection.execute(
            text("INSERT INTO measurements VALUES (:id, :reading, :zero, :optional)"),
            [
                {"id": "0001", "reading": "0.1234567890123456789", "zero": 0, "optional": None},
                {"id": "0002", "reading": "100.00000000000000001", "zero": 0, "optional": "recorded"},
            ],
        )
    engine.dispose()
    return DatabaseSourceConfig(
        source=SourceRef(workspace_id="ci", source_id="temporary", revision="source-1"),
        dialect="sqlite",
        connection_url=SecretStr(url),
        allowed_tables=frozenset({"measurements"}),
        read_only_role=True,
    )


@pytest.fixture
def reader(config: DatabaseSourceConfig) -> Iterator[DatabaseSourceReader]:
    result = DatabaseSourceReader(config)
    yield result
    result.close()


def query(config: DatabaseSourceConfig, sql: str) -> SqlRead:
    return SqlRead(source=config.source, read_id="ci-read", revision="read-1", sql=sql)


def test_actual_rows_preserve_source_precision_and_identity(
    config: DatabaseSourceConfig, reader: DatabaseSourceReader
) -> None:
    result = reader.read(
        query(config, "SELECT id, reading, zero, optional FROM measurements WHERE id = :id"), {"id": "0001"}
    )
    assert result.records == ({"id": "0001", "reading": "0.1234567890123456789", "zero": 0, "optional": None},)
    assert result.complete is True
    assert result.source == config.source
    assert len(result.read_fingerprint) == 64


def test_bind_value_never_changes_sql_scope(config: DatabaseSourceConfig, reader: DatabaseSourceReader) -> None:
    result = reader.read(query(config, "SELECT id FROM measurements WHERE id = :id"), {"id": "0001' OR 1=1 --"})
    assert result.rows == ()


def test_engine_itself_is_read_only_even_without_ast(
    config: DatabaseSourceConfig, reader: DatabaseSourceReader
) -> None:
    with reader._engine.connect() as connection, pytest.raises(OperationalError):
        connection.execute(text("DELETE FROM measurements"))
    assert len(reader.read(query(config, "SELECT id FROM measurements"), {}).rows) == 2


def test_actual_row_overflow_is_not_reported_as_complete(config: DatabaseSourceConfig) -> None:
    limited = config.model_copy(update={"limits": ReadLimits(max_rows=1)})
    reader = DatabaseSourceReader(limited)
    try:
        with pytest.raises(DataSourceError, match="row_limit"):
            reader.read(query(config, "SELECT id FROM measurements"), {})
    finally:
        reader.close()


def test_sqlite_progress_handler_interrupts_expensive_read(config: DatabaseSourceConfig) -> None:
    limited = config.model_copy(update={"limits": ReadLimits(timeout_seconds=0.01)})
    reader = DatabaseSourceReader(limited)
    sql = "SELECT COUNT(*) AS count FROM " + " CROSS JOIN ".join(f"measurements AS t{index}" for index in range(30))
    try:
        with pytest.raises(DataSourceError, match="timeout"):
            reader.read(query(config, sql), {})
    finally:
        reader.close()
