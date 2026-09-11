"""Bounded registered reads; no discovery, writeback, retries or model credentials.

Production databases require a separately provisioned read-only role, enforced again
with read-only transactions. HTTP hosts and transports are server policy; configure
network egress policy for registered internal gateways as well as the exact host list.
"""

import asyncio
import hashlib
import json
import math
import re
import sqlite3
import ssl
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import httpx
import sqlglot
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from enterprise_platform.adapters.sql_plan_budget import SqlPlanBudget, validate_plan_budget
from enterprise_platform.domain.data_sources import (
    DatabaseSourceConfig,
    DataSourceError,
    FrozenRows,
    HttpRead,
    HttpSourceConfig,
    PageEvidence,
    PreparedSql,
    SourceRef,
    SourceValue,
    SqlRead,
    source_value,
)

_DIALECTS = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}
_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COALESCE",
    "NULLIF",
    "ABS",
    "ROUND",
    "LOWER",
    "UPPER",
    "LENGTH",
    "TRIM",
    "CAST",
    "TRYCAST",
    "EXTRACT",
    "DATETRUNC",
    "TIMESTAMPTRUNC",
    "CASE",
    "IF",
    "EXISTS",
}
_FORBIDDEN = {
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
    "merge",
    "command",
    "into",
    "lock",
    "set",
    "transaction",
    "commit",
    "rollback",
    "copy",
    "execute",
    "use",
    "parameter",
    "propertyeq",
    "dot",
}
_MISSING = object()


def _check_source(expected: SourceRef, actual: SourceRef) -> None:
    if expected != actual:
        raise DataSourceError("source_mismatch")


def _parameters(
    parameters: Mapping[str, SourceValue], names: frozenset[str], max_bytes: int
) -> tuple[tuple[str, SourceValue], ...]:
    if set(parameters) != names:
        raise DataSourceError("parameter_mismatch")
    if any(isinstance(value, str) and len(value) > max_bytes for value in parameters.values()):
        raise DataSourceError("size_limit")
    try:
        bound = tuple((key, source_value(value)) for key, value in sorted(parameters.items()))
    except DataSourceError:
        raise DataSourceError("parameter_mismatch") from None
    if sum(len(key.encode()) + len(str(value).encode()) for key, value in bound) > max_bytes:
        raise DataSourceError("size_limit")
    return bound


def validate_sql_structure(
    sql: str, *, dialect: Literal["postgresql", "mysql", "sqlite"], allowed_tables: frozenset[str]
) -> exp.Select:
    """One SELECT/CTE, actual scoped tables, known pure functions and exact named binds."""
    if not 1 <= len(sql) <= 20000:
        raise DataSourceError("query_rejected")
    # Native executable comments are absent from the parsed AST. This restricted
    # grammar also excludes MySQL's non-comment double-minus form; use named binds
    # for literal values containing these sequences rather than weakening the check.
    if re.search(r"/\*\s*(?:!|m!|\+)", sql, re.IGNORECASE) or (
        dialect == "mysql" and re.search(r"--(?=[^ \t\r\n\f\v])", sql)
    ):
        raise DataSourceError("query_rejected")
    try:
        statements = sqlglot.parse(sql, read=_DIALECTS[dialect])
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise DataSourceError("query_rejected")
        tree = statements[0]
        nodes = list(tree.walk())
        if len(nodes) > 2048:
            raise DataSourceError("query_rejected")
        for node in nodes:
            if node.key in _FORBIDDEN or isinstance(node, exp.With) and node.args.get("recursive"):
                raise DataSourceError("query_rejected")
            # SQLGlot classifies boolean connectors as Func as well. Their children
            # still undergo this walk; only these exact operators bypass the function list.
            if isinstance(node, exp.Func) and not isinstance(node, (exp.And, exp.Or)):
                function = node.name.upper() if isinstance(node, exp.Anonymous) else node.key.upper()
                if function not in _FUNCTIONS:
                    raise DataSourceError("query_rejected")
        for scope in traverse_scope(tree):
            for _, (_, selected) in scope.selected_sources.items():
                if isinstance(selected, exp.Table):
                    name = ".".join(part.name for part in selected.parts)
                    if not isinstance(selected.this, exp.Identifier) or name not in allowed_tables:
                        raise DataSourceError("query_rejected")
                elif not isinstance(selected, Scope):
                    raise DataSourceError("query_rejected")
        names = frozenset(node.name for node in tree.find_all(exp.Placeholder))
        if "" in names or set(text(sql).compile().params) != names:
            raise DataSourceError("query_rejected")
    except (SqlglotError, RecursionError, ValueError) as error:
        if isinstance(error, DataSourceError):
            raise
        raise DataSourceError("query_rejected") from None
    return tree


def validate_read_sql(
    read: SqlRead, config: DatabaseSourceConfig, parameters: Mapping[str, SourceValue]
) -> PreparedSql:
    read = SqlRead.model_validate(read.model_dump())
    _check_source(config.source, read.source)
    tree = validate_sql_structure(read.sql, dialect=config.dialect, allowed_tables=config.allowed_tables)
    names = frozenset(node.name for node in tree.find_all(exp.Placeholder))
    return PreparedSql(read.sql, _parameters(parameters, names, config.limits.max_bytes))


def read_fingerprint(read: SqlRead | HttpRead, parameters: tuple[tuple[str, SourceValue], ...]) -> str:
    document = read.model_dump(mode="json")
    if isinstance(read, HttpRead):
        document["parameter_names"] = sorted(read.parameter_names)
    value = {"read": document, "parameters": [(key, type(value).__name__, str(value)) for key, value in parameters]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class DatabaseSourceReader:
    """Synchronous driver boundary; invoke from a worker, never an async event-loop thread."""

    def __init__(self, config: DatabaseSourceConfig) -> None:
        self._config = DatabaseSourceConfig.model_validate(config.model_dump())
        try:
            url = make_url(config.connection_url.get_secret_value())
            if url.get_backend_name() != config.dialect:
                raise ValueError
            timeout = math.ceil(config.limits.timeout_seconds)
            if config.dialect == "sqlite":
                if not url.database or url.database == ":memory:" or url.query:
                    raise ValueError
                url = URL.create(
                    "sqlite", database=Path(url.database).absolute().as_uri(), query={"mode": "ro", "uri": "true"}
                )
                self._engine = create_engine(
                    url, poolclass=NullPool, hide_parameters=True, connect_args={"timeout": timeout}
                )
            elif config.dialect == "postgresql" and url.drivername == "postgresql+psycopg":
                self._engine = create_engine(
                    url,
                    poolclass=NullPool,
                    hide_parameters=True,
                    connect_args={
                        "connect_timeout": timeout,
                        "sslmode": "verify-full" if config.tls else "disable",
                    },
                )
            elif config.dialect == "mysql" and url.drivername == "mysql+pymysql":
                self._engine = create_engine(
                    url,
                    poolclass=NullPool,
                    hide_parameters=True,
                    connect_args={
                        "connect_timeout": timeout,
                        "read_timeout": timeout,
                        "write_timeout": timeout,
                        "ssl": ssl.create_default_context() if config.tls else None,
                    },
                )
            else:
                raise ValueError
        except (ValueError, SQLAlchemyError):
            raise DataSourceError("read_failed") from None

    def close(self) -> None:
        self._engine.dispose()

    def describe_columns(
        self, table: str, columns: tuple[str, ...], *, deadline: float | None = None
    ) -> tuple[tuple[str, str], ...]:
        """Read selected catalog names/types under an optionally shared monotonic deadline.

        The shared deadline may shorten, never extend, the source timeout. Blocking
        driver calls retain driver timeouts; an overdue result is discarded on return.
        No defaults or samples are returned.
        """
        parts = table.split(".")
        if (
            table not in self._config.allowed_tables
            or len(parts) > 2
            or not 1 <= len(columns) <= 500
            or len(set(columns)) != len(columns)
            or any(not name.strip() or len(name) > 240 for name in columns)
        ):
            raise DataSourceError("query_rejected")
        if deadline is not None and not math.isfinite(deadline):
            raise DataSourceError("query_rejected")
        local_deadline = time.monotonic() + self._config.limits.timeout_seconds
        deadline = local_deadline if deadline is None else min(deadline, local_deadline)
        if time.monotonic() >= deadline:
            raise DataSourceError("timeout")
        try:
            with self._engine.connect() as connection:
                if time.monotonic() >= deadline:
                    raise DataSourceError("timeout")
                self._read_only(connection, deadline)
                metadata = inspect(connection).get_columns(parts[-1], schema=parts[0] if len(parts) == 2 else None)
                if time.monotonic() >= deadline:
                    raise DataSourceError("timeout")
                if len(metadata) > 500:
                    raise DataSourceError("size_limit")
                selected: dict[str, str] = {}
                for column in metadata:
                    name = column["name"]
                    if name in columns:
                        if name in selected:
                            raise DataSourceError("response_invalid")
                        sql_type = str(column["type"])
                        if not sql_type.strip() or len(sql_type) > 128:
                            raise DataSourceError("response_invalid")
                        selected[name] = sql_type
                if set(selected) != set(columns):
                    raise DataSourceError("response_invalid")
                result = tuple((name, selected[name]) for name in sorted(selected))
                if len(json.dumps(result).encode("utf-8")) > self._config.limits.max_bytes:
                    raise DataSourceError("size_limit")
                if time.monotonic() >= deadline:
                    raise DataSourceError("timeout")
                return result
        except (SQLAlchemyError, ValueError, TypeError, KeyError) as error:
            if isinstance(error, DataSourceError):
                raise
            raise DataSourceError("read_failed") from None

    def _read_only(self, connection: Connection, deadline: float) -> None:
        if self._config.dialect in {"postgresql", "mysql"}:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            self._statement_timeout(connection, deadline)
        else:
            connection.exec_driver_sql("PRAGMA query_only = ON")
            driver = connection.connection.driver_connection
            if not isinstance(driver, sqlite3.Connection):
                raise DataSourceError("read_failed")
            driver.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)

    def _statement_timeout(self, connection: Connection, deadline: float) -> None:
        milliseconds = max(1, int((deadline - time.monotonic()) * 1000))
        if self._config.dialect == "postgresql":
            connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": str(milliseconds)}
            )
        elif self._config.dialect == "mysql":
            connection.execute(text("SET SESSION MAX_EXECUTION_TIME = :timeout"), {"timeout": milliseconds})

    def read(
        self,
        read: SqlRead,
        parameters: Mapping[str, SourceValue],
        *,
        deadline: float | None = None,
        plan_budget: SqlPlanBudget | None = None,
    ) -> FrozenRows:
        """Read registered SQL, optionally admitting its estimated plan before execution.

        Planning and execution share a shortening-only deadline. An admitted estimate
        is neither authorization nor a measured scan limit; callers still resolve
        grants and provision read-only credentials. No ANALYZE or SQL rewrite is used.
        """
        prepared = validate_read_sql(read, self._config, parameters)
        limits = self._config.limits
        dialect = self._config.dialect
        if plan_budget is not None and dialect == "sqlite":
            raise DataSourceError("query_rejected")
        if deadline is not None and not math.isfinite(deadline):
            raise DataSourceError("query_rejected")
        local_deadline = time.monotonic() + limits.timeout_seconds
        deadline = local_deadline if deadline is None else min(local_deadline, deadline)
        if time.monotonic() >= deadline:
            raise DataSourceError("timeout")
        try:
            with self._engine.connect() as connection:
                if time.monotonic() >= deadline:
                    raise DataSourceError("timeout")
                self._read_only(connection, deadline)
                if plan_budget is not None and dialect != "sqlite":
                    prefix = "EXPLAIN (FORMAT JSON) " if dialect == "postgresql" else "EXPLAIN FORMAT=JSON "
                    planned = connection.execute(text(prefix + prepared.sql), dict(prepared.parameters))
                    try:
                        validate_plan_budget(planned.scalar_one(), dialect=dialect, budget=plan_budget)
                    finally:
                        planned.close()
                    if time.monotonic() >= deadline:
                        raise DataSourceError("timeout")
                    self._statement_timeout(connection, deadline)
                if time.monotonic() >= deadline:
                    raise DataSourceError("timeout")
                result = connection.execution_options(
                    stream_results=True, max_row_buffer=min(limits.max_rows + 1, 1000)
                ).execute(text(prepared.sql), dict(prepared.parameters))
                try:
                    columns = tuple(str(name) for name in result.keys())
                    result_digest = hashlib.sha256(json.dumps(columns, separators=(",", ":")).encode())
                    values: list[tuple[SourceValue, ...]] = []
                    size = 0
                    for row in result:
                        if time.monotonic() >= deadline:
                            raise DataSourceError("timeout")
                        if len(values) >= limits.max_rows:
                            raise DataSourceError("row_limit")
                        converted = tuple(source_value(value) for value in row)
                        size += sum(len(str(value).encode()) for value in converted)
                        if size > limits.max_bytes:
                            raise DataSourceError("size_limit")
                        encoded = json.dumps(
                            [(type(value).__name__, str(value)) for value in converted], separators=(",", ":")
                        )
                        result_digest.update(b"\n" + encoded.encode())
                        values.append(converted)
                    if time.monotonic() >= deadline:
                        raise DataSourceError("timeout")
                finally:
                    result.close()
        except SQLAlchemyError:
            raise DataSourceError("timeout" if time.monotonic() >= deadline else "read_failed") from None
        return FrozenRows(
            read.source,
            read.read_id,
            read.revision,
            read_fingerprint(read, prepared.parameters),
            datetime.now(UTC),
            columns,
            tuple(values),
            (PageEvidence(1, len(values), result_digest.hexdigest()),),
        )


def _lookup(value: object, path: tuple[str, ...]) -> object:
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_number(value: str) -> object:
    raise ValueError


def _json_parameter(value: SourceValue) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return json.dumps(value.isoformat())
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _page_rows(
    payload: object, read: HttpRead, columns: tuple[str, ...] | None, remaining_rows: int
) -> tuple[tuple[str, ...] | None, list[tuple[SourceValue, ...]]]:
    records = _lookup(payload, read.rows_path)
    if not isinstance(records, list):
        raise DataSourceError("response_invalid")
    if len(records) > remaining_rows:
        raise DataSourceError("row_limit")
    rows: list[tuple[SourceValue, ...]] = []
    for record in records:
        if not isinstance(record, dict) or not record or any(not isinstance(key, str) or not key for key in record):
            raise DataSourceError("response_invalid")
        if columns is None:
            columns = tuple(record)
        if set(record) != set(columns):
            raise DataSourceError("response_invalid")
        rows.append(tuple(source_value(record[name]) for name in columns))
    return columns, rows


def _next_cursor(payload: object, read: HttpRead) -> str | int | None:
    for container in (payload, _lookup(payload, read.rows_path[:-1])):
        truncated = _lookup(container, ("truncated",))
        more = _lookup(container, ("has_more",))
        if any(value is not _MISSING and type(value) is not bool for value in (truncated, more)):
            raise DataSourceError("response_invalid")
        if truncated is True:
            raise DataSourceError("truncated_result")
        if not read.pagination and more is True:
            raise DataSourceError("truncated_result")
        cursor_hint = _lookup(container, ("next_cursor",))
        if not read.pagination and cursor_hint is not _MISSING and cursor_hint is not None and cursor_hint != "":
            raise DataSourceError("truncated_result")
    if read.pagination is None:
        return None
    cursor = _lookup(payload, read.pagination.next_cursor_path)
    more = _lookup(payload, read.pagination.has_more_path)
    if cursor is _MISSING or more is not _MISSING and type(more) is not bool:
        raise DataSourceError("response_invalid")
    if cursor is None or cursor == "":
        if more is True:
            raise DataSourceError("truncated_result")
        return None
    if type(cursor) not in (str, int) or len(str(cursor)) > 1024 or more is False:
        raise DataSourceError("response_invalid")
    if isinstance(cursor, (str, int)):
        return cursor
    raise DataSourceError("response_invalid")


class HttpSourceReader:
    """Async cancellation bounds the whole read, including all pages and slow response bodies."""

    def __init__(self, config: HttpSourceConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = HttpSourceConfig.model_validate(config.model_dump())
        headers = {name: value.get_secret_value() for name, value in config.headers}
        headers["Accept-Encoding"] = "identity"
        self._client = httpx.AsyncClient(
            headers=headers,
            transport=transport,
            verify=True,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(config.limits.timeout_seconds),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=0),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self._client.aclose()

    async def read(self, read: HttpRead, parameters: Mapping[str, SourceValue]) -> FrozenRows:
        read = HttpRead.model_validate(read.model_dump())
        _check_source(self._config.source, read.source)
        bound = _parameters(parameters, read.parameter_names, self._config.limits.max_bytes)
        pages: list[PageEvidence] = []
        try:
            async with asyncio.timeout(self._config.limits.timeout_seconds):
                return await self._collect(read, bound, pages)
        except TimeoutError:
            raise DataSourceError("timeout", pages=tuple(pages)) from None
        except (httpx.RequestError, UnicodeError, ValueError, RecursionError) as error:
            if isinstance(error, DataSourceError):
                error.pages = tuple(pages)
                raise
            raise DataSourceError(
                "timeout" if isinstance(error, httpx.TimeoutException) else "response_invalid", pages=tuple(pages)
            ) from None

    async def _collect(
        self, read: HttpRead, bound: tuple[tuple[str, SourceValue], ...], pages: list[PageEvidence]
    ) -> FrozenRows:
        limits = self._config.limits
        all_rows: list[tuple[SourceValue, ...]] = []
        columns: tuple[str, ...] | None = None
        seen: set[str] = set()
        cursor: str | int | None = None
        total_bytes = 0
        for page_number in range(1, limits.max_pages + 1):
            parameters = dict(bound)
            if read.pagination and cursor is not None:
                parameters[read.pagination.parameter] = cursor
            body: bytes | None = None
            query: dict[str, str] | None = None
            if read.method == "POST":
                body = (
                    "{"
                    + ",".join(f"{json.dumps(key)}:{_json_parameter(value)}" for key, value in parameters.items())
                    + "}"
                ).encode()
                if len(body) > limits.max_bytes:
                    raise DataSourceError("size_limit")
            else:
                if any(value is None for value in parameters.values()):
                    raise DataSourceError("parameter_mismatch")
                query = {
                    key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in parameters.items()
                }
            async with self._client.stream(
                read.method,
                self._config.url,
                params=query,
                content=body,
                headers={"Content-Type": "application/json"} if body else None,
            ) as response:
                if response.status_code != 200:
                    raise DataSourceError("http_status", status_code=response.status_code)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise DataSourceError("response_invalid")
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    total_bytes += len(chunk)
                    if total_bytes > limits.max_bytes:
                        raise DataSourceError("size_limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
            payload: object = json.loads(
                raw, parse_float=Decimal, parse_constant=_invalid_number, object_pairs_hook=_json_object
            )
            columns, rows = _page_rows(payload, read, columns, limits.max_rows - len(all_rows))
            if len(all_rows) + len(rows) > limits.max_rows:
                raise DataSourceError("row_limit")
            all_rows.extend(rows)
            pages.append(
                PageEvidence(
                    page_number,
                    len(rows),
                    hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(_json_parameter(cursor).encode()).hexdigest() if cursor is not None else None,
                )
            )
            cursor = _next_cursor(payload, read)
            if cursor is None:
                return FrozenRows(
                    read.source,
                    read.read_id,
                    read.revision,
                    read_fingerprint(read, bound),
                    datetime.now(UTC),
                    columns or (),
                    tuple(all_rows),
                    tuple(pages),
                )
            digest = hashlib.sha256(_json_parameter(cursor).encode()).hexdigest()
            if digest in seen:
                raise DataSourceError("repeated_cursor")
            seen.add(digest)
        raise DataSourceError("page_limit")
