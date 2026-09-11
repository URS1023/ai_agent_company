import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal
from unittest.mock import create_autospec, patch

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Connection, CursorResult, Engine

from enterprise_platform.adapters.data_sources import DatabaseSourceReader, HttpSourceReader, validate_read_sql
from enterprise_platform.domain.data_sources import (
    CursorPagination,
    DatabaseSourceConfig,
    DataSourceError,
    HttpRead,
    HttpSourceConfig,
    ReadLimits,
    SourceRef,
    SqlRead,
)


def source() -> SourceRef:
    return SourceRef(workspace_id="workspace-1", source_id="production", revision="source-1")


def database() -> DatabaseSourceConfig:
    return DatabaseSourceConfig(
        source=source(),
        dialect="postgresql",
        connection_url=SecretStr("postgresql+psycopg://reader:secret@db/test"),
        allowed_tables=frozenset({"public.measurements"}),
        read_only_role=True,
    )


def sql_read(sql: str) -> SqlRead:
    return SqlRead(source=source(), read_id="recent", revision="read-1", sql=sql)


def http_source(**changes) -> HttpSourceConfig:
    return HttpSourceConfig(
        source=source(),
        url="https://gateway.example.test/readings",
        allowed_hosts=frozenset({"gateway.example.test"}),
        headers=(("Authorization", SecretStr("Bearer fixture-secret")),),
        **changes,
    )


def http_read(**changes) -> HttpRead:
    return HttpRead(
        source=source(),
        read_id="recent",
        revision="read-1",
        method="GET",
        read_only=True,
        parameter_names=frozenset({"device"}),
        rows_path=("data",),
        **changes,
    )


def run_http(handler, *, config=None, read=None, parameters=None):
    async def execute():
        async with HttpSourceReader(config or http_source(), transport=httpx.MockTransport(handler)) as reader:
            return await reader.read(read or http_read(), parameters or {"device": "0001"})

    return asyncio.run(execute())


def test_select_cte_uses_real_table_scope_and_bound_parameters() -> None:
    sql = "WITH recent AS (SELECT * FROM public.measurements WHERE device = :device) SELECT COUNT(*) AS n FROM recent"
    prepared = validate_read_sql(sql_read(sql), database(), {"device": "0001' OR 1=1 --"})

    assert prepared.sql == sql
    assert dict(prepared.parameters) == {"device": "0001' OR 1=1 --"}
    assert "0001" not in prepared.sql


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM public.measurements",
        "UPDATE public.measurements SET value=1",
        "INSERT INTO public.measurements VALUES (1)",
        "DROP TABLE public.measurements",
        "SELECT * FROM public.measurements; SELECT * FROM public.measurements",
        "WITH x AS (DELETE FROM public.measurements RETURNING *) SELECT * FROM x",
        "SELECT pg_sleep(30) FROM public.measurements",
        "SELECT pg_read_file('/etc/passwd') FROM public.measurements",
        "SELECT public.abs(1) FROM public.measurements",
        "SELECT * INTO stolen FROM public.measurements",
        "SELECT * FROM public.measurements FOR UPDATE",
        "SELECT * FROM private.secrets",
        "SELECT * FROM read_csv('/etc/passwd')",
        "WITH secrets AS (SELECT * FROM public.measurements) SELECT * FROM private.secrets",
        "SELECT * FROM private.secrets WHERE EXISTS (WITH secrets AS (SELECT 1) SELECT * FROM secrets)",
        "EXPLAIN SELECT * FROM public.measurements",
    ],
)
def test_mutating_dangerous_or_out_of_scope_sql_is_rejected(sql: str) -> None:
    with pytest.raises(DataSourceError) as error:
        validate_read_sql(sql_read(sql), database(), {})
    assert error.value.code == "query_rejected"
    assert sql not in str(error.value)


@pytest.mark.parametrize("parameters", [{}, {"device": "0001", "extra": "x"}, {"device": 0.1}])
def test_query_parameters_require_exact_names_and_exact_scalar_types(parameters) -> None:
    with pytest.raises(DataSourceError, match="parameter_mismatch"):
        validate_read_sql(sql_read("SELECT * FROM public.measurements WHERE device = :device"), database(), parameters)


def test_connection_and_read_revisions_are_checked_before_execution() -> None:
    read = sql_read("SELECT * FROM public.measurements").model_copy(
        update={"source": source().model_copy(update={"revision": "old"})}
    )
    with pytest.raises(DataSourceError, match="source_mismatch"):
        validate_read_sql(read, database(), {})


def test_secrets_are_redacted_and_config_extra_fields_are_rejected() -> None:
    config = database()
    assert "reader:secret" not in repr(config)
    assert "reader:secret" not in config.model_dump_json()
    assert "fixture-secret" not in http_source().model_dump_json()
    with pytest.raises(ValidationError):
        ReadLimits(max_rows=0)
    with pytest.raises(ValidationError):
        SqlRead(source=source(), read_id="r", revision="1", sql="SELECT 1", credential="secret")


def test_http_rows_preserve_decimal_null_zero_and_leading_zero_ids() -> None:
    def handler(request):
        assert request.url.host == "gateway.example.test"
        assert request.url.params["device"] == "0001"
        assert request.headers["authorization"] == "Bearer fixture-secret"
        return httpx.Response(
            200, content=b'{"data":[{"id":"0001","reading":0.1234567890123456789,"zero":0,"missing":null}]}'
        )

    result = run_http(handler)

    assert result.records == ({"id": "0001", "reading": Decimal("0.1234567890123456789"), "zero": 0, "missing": None},)
    assert type(result.records[0]["zero"]) is int
    assert result.source == source()
    assert result.captured_at.tzinfo is not None
    assert len(result.pages) == 1
    assert result.pages[0].row_count == 1
    assert result.complete is True
    result.records[0]["id"] = "changed"
    assert result.records[0]["id"] == "0001"
    with pytest.raises(FrozenInstanceError):
        result.rows = ()


def test_http_empty_rows_are_real_empty_data() -> None:
    result = run_http(lambda request: httpx.Response(200, json={"data": []}))
    assert result.rows == ()
    assert result.complete is True


@pytest.mark.parametrize(
    "change",
    [
        {"url": "http://gateway.example.test/readings"},
        {"url": "https://other.example.test/readings"},
        {"url": "https://user:password@gateway.example.test/readings"},
        {"url": "https://gateway.example.test/readings?token=secret"},
        {"url": "https://gateway.example.test/readings#fragment"},
        {"headers": (("Host", SecretStr("other.example.test")),)},
    ],
)
def test_http_config_is_fixed_host_tls_and_header_constrained(change) -> None:
    payload = http_source().model_dump()
    payload.update(change)
    with pytest.raises(ValidationError):
        HttpSourceConfig.model_validate(payload)


def test_unregistered_mutating_http_methods_are_rejected() -> None:
    payload = http_read().model_dump()
    payload["method"], payload["read_only"] = "POST", False
    with pytest.raises(ValidationError):
        HttpRead.model_validate(payload)
    payload["method"], payload["read_only"] = "DELETE", True
    with pytest.raises(ValidationError):
        HttpRead.model_validate(payload)


def test_registered_read_only_post_binds_parameters_as_json() -> None:
    def handler(request):
        assert request.method == "POST"
        assert request.content == b'{"device":"0001"}'
        return httpx.Response(200, json={"data": [{"id": "0001"}]})

    result = run_http(handler, read=http_read().model_copy(update={"method": "POST"}))
    assert result.records == ({"id": "0001"},)


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 429, 500])
def test_http_errors_never_return_business_data_or_retry(status: int) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="fixture-secret", headers={"location": "https://other.example.test"})

    with pytest.raises(DataSourceError) as error:
        run_http(handler)
    assert error.value.code == "http_status"
    assert error.value.status_code == status
    assert "fixture-secret" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body,code",
    [
        (b"not json", "response_invalid"),
        (b'{"data":[{"value":NaN}]}', "response_invalid"),
        (b'{"data":[{"nested":{}}]}', "response_invalid"),
        (b'{"data":[{"a":1},{"b":2}]}', "response_invalid"),
        (b'{"data":[],"has_more":true}', "truncated_result"),
        (b'{"data":[],"truncated":true}', "truncated_result"),
        (b'{"other":[]}', "response_invalid"),
    ],
)
def test_malformed_and_incomplete_http_data_is_explicit_failure(body: bytes, code: str) -> None:
    with pytest.raises(DataSourceError) as error:
        run_http(lambda request: httpx.Response(200, content=body))
    assert error.value.code == code


def test_response_bytes_and_total_rows_are_bounded() -> None:
    with pytest.raises(DataSourceError, match="size_limit"):
        run_http(
            lambda request: httpx.Response(200, content=b"x" * 101),
            config=http_source(limits=ReadLimits(max_bytes=100)),
        )
    with pytest.raises(DataSourceError, match="row_limit"):
        run_http(
            lambda request: httpx.Response(200, json={"data": [{"v": 1}, {"v": 2}]}),
            config=http_source(limits=ReadLimits(max_rows=1)),
        )


def test_pagination_collects_all_pages_and_hashes_page_provenance() -> None:
    cursors = []

    def handler(request):
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "0001" if cursor is None else "0002"}],
                "next": "page-2" if cursor is None else None,
                "has_more": cursor is None,
            },
        )

    read = http_read(pagination=CursorPagination(parameter="cursor", next_cursor_path=("next",)))
    result = run_http(handler, read=read)

    assert cursors == [None, "page-2"]
    assert result.records == ({"id": "0001"}, {"id": "0002"})
    assert tuple(page.number for page in result.pages) == (1, 2)
    assert result.pages[0].response_digest != result.pages[1].response_digest
    assert result.pages[1].request_cursor_digest is not None
    assert "page-2" not in repr(result.pages)


@pytest.mark.parametrize("failure", ["repeat", "pages", "rows", "later_page", "no_cursor"])
def test_pagination_partial_failures_never_publish_partial_rows(failure: str) -> None:
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if failure == "later_page" and count == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "0001"}],
                "has_more": True,
                "next": None if failure == "no_cursor" else "same" if failure == "repeat" else str(count),
            },
        )

    limits = (
        ReadLimits(max_pages=1) if failure == "pages" else ReadLimits(max_rows=1) if failure == "rows" else ReadLimits()
    )
    read = http_read(pagination=CursorPagination(parameter="cursor", next_cursor_path=("next",)))
    with pytest.raises(DataSourceError):
        run_http(handler, config=http_source(limits=limits), read=read)
    assert count <= 2


def test_total_deadline_cancels_a_slow_body_not_just_each_read() -> None:
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for part in (b'{"data":[', b'{"id":"0001"}', b"]}"):
                await asyncio.sleep(0.025)
                yield part

    with pytest.raises(DataSourceError, match="timeout"):
        run_http(
            lambda request: httpx.Response(200, stream=SlowStream()),
            config=http_source(limits=ReadLimits(timeout_seconds=0.04)),
        )


def test_unregistered_parameters_and_wrong_source_do_not_dispatch() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    with pytest.raises(DataSourceError, match="parameter_mismatch"):
        run_http(handler, parameters={"device": "0001", "url": "https://other.test"})
    wrong = http_read().model_copy(update={"source": source().model_copy(update={"workspace_id": "other"})})
    with pytest.raises(DataSourceError, match="source_mismatch"):
        run_http(handler, read=wrong)
    assert calls == []


def test_failed_later_page_preserves_evidence_but_exposes_no_partial_success() -> None:
    def handler(request):
        if request.url.params.get("cursor"):
            return httpx.Response(503, text="fixture-secret")
        return httpx.Response(200, json={"data": [{"id": "0001"}], "next": "second", "has_more": True})

    read = http_read(pagination=CursorPagination(parameter="cursor", next_cursor_path=("next",)))
    with pytest.raises(DataSourceError) as error:
        run_http(handler, read=read)
    assert error.value.pages[0].number == 1
    assert error.value.pages[0].row_count == 1
    assert len(error.value.pages[0].response_digest) == 64
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [
        b'{"data":[],"has_more":"true"}',
        b'{"data":[],"truncated":"true"}',
        b'{"data":[],"has_more":true,"has_more":false}',
    ],
)
def test_ambiguous_completion_metadata_does_not_claim_success(body: bytes) -> None:
    with pytest.raises(DataSourceError, match="response_invalid"):
        run_http(lambda request: httpx.Response(200, content=body))


def test_unregistered_cursor_does_not_silently_discard_more_data() -> None:
    with pytest.raises(DataSourceError, match="truncated_result"):
        run_http(lambda request: httpx.Response(200, json={"data": [], "next_cursor": "more"}))


def test_large_parameters_are_rejected_before_transport_or_sql_execution() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    with pytest.raises(DataSourceError, match="size_limit"):
        run_http(handler, config=http_source(limits=ReadLimits(max_bytes=32)), parameters={"device": "x" * 100})
    limited = database().model_copy(update={"limits": ReadLimits(max_bytes=32)})
    with pytest.raises(DataSourceError, match="size_limit"):
        validate_read_sql(
            sql_read("SELECT * FROM public.measurements WHERE device = :device"), limited, {"device": "x" * 100}
        )
    assert calls == []


def test_read_only_declarations_require_boolean_confirmation_not_numeric_coercion() -> None:
    payload = http_read().model_dump()
    payload["read_only"] = 1
    with pytest.raises(ValidationError):
        HttpRead.model_validate(payload)
    config = database().model_dump()
    config["read_only_role"] = 1
    with pytest.raises(ValidationError):
        DatabaseSourceConfig.model_validate(config)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM allowed /*!50000 UNION SELECT * FROM forbidden */",
        "SELECT /*!50000 SLEEP(10), */ * FROM allowed",
        "SELECT * FROM allowed /*M!100100 UNION SELECT * FROM forbidden */",
        "SELECT * FROM allowed /*m! UNION SELECT * FROM forbidden */",
        "SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM allowed",
        "SELECT id FROM allowed WHERE 1=1--1 UNION SELECT id FROM forbidden",
    ],
)
def test_mysql_executable_comments_and_execution_hints_are_rejected(sql: str) -> None:
    config = database().model_copy(update={"dialect": "mysql", "allowed_tables": frozenset({"allowed"})})
    with pytest.raises(DataSourceError, match="query_rejected"):
        validate_read_sql(sql_read(sql), config, {})


def test_normal_sql_comments_keep_named_parameter_binding() -> None:
    sql = "SELECT /* source records */ * FROM public.measurements WHERE id = :device -- stable read\n"
    result = validate_read_sql(sql_read(sql), database(), {"device": "0001"})
    assert result.sql == sql
    assert dict(result.parameters) == {"device": "0001"}


def test_mysql_ordinary_line_comment_remains_a_bound_read() -> None:
    config = database().model_copy(update={"dialect": "mysql", "allowed_tables": frozenset({"allowed"})})
    sql = "SELECT id FROM allowed WHERE id=:device -- source record\n"
    assert dict(validate_read_sql(sql_read(sql), config, {"device": "0001"}).parameters) == {"device": "0001"}


@pytest.mark.parametrize("values", [(), (("0001", Decimal("0.0000000000000000001"), None, 0),)])
def test_database_result_set_has_typed_evidence_using_mock_driver_only(values) -> None:
    engine = create_autospec(Engine, instance=True)
    connection = create_autospec(Connection, instance=True)
    cursor = create_autospec(CursorResult, instance=True)
    engine.connect.return_value.__enter__.return_value = connection
    connection.execution_options.return_value = connection
    connection.execute.return_value = cursor
    cursor.keys.return_value = ("device", "value", "optional", "zero")
    cursor.__iter__.return_value = iter(values)
    with patch("enterprise_platform.adapters.data_sources.create_engine", return_value=engine):
        reader = DatabaseSourceReader(database())
        try:
            result = reader.read(
                sql_read("SELECT * FROM public.measurements WHERE device = :device"), {"device": "0001"}
            )
        finally:
            reader.close()
    assert len(result.pages) == 1
    assert result.pages[0].number == 1
    assert result.pages[0].row_count == len(values)
    assert len(result.pages[0].response_digest) == 64
    assert result.rows == values
    cursor.close.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "sqlite"])
@pytest.mark.parametrize(
    "predicate",
    [
        "device = :device AND department = :department AND batch_id = :batch_id",
        "device = :device AND (department = :department OR batch_id = :batch_id)",
        "device = :device AND NOT (department = :department OR batch_id = :batch_id)",
    ],
)
def test_boolean_filters_remain_bound_reads(dialect: str, predicate: str) -> None:
    config = database().model_copy(update={"dialect": dialect})
    sql = f"SELECT * FROM public.measurements WHERE {predicate}"
    parameters = {"device": "0001", "department": "line-A", "batch_id": "batch' OR 1=1 --"}
    prepared = validate_read_sql(sql_read(sql), config, parameters)
    assert prepared.sql == sql
    assert dict(prepared.parameters) == parameters
    assert parameters["batch_id"] not in prepared.sql


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "sqlite"])
@pytest.mark.parametrize(
    "predicate",
    [
        "device = :device AND unknown_operation(1) = 1",
        "device = :device OR pg_sleep(10) = 1",
        "device = :device AND EXISTS (SELECT * FROM private.secrets)",
    ],
)
def test_boolean_filters_do_not_hide_disallowed_functions_or_tables(dialect: str, predicate: str) -> None:
    config = database().model_copy(update={"dialect": dialect})
    with pytest.raises(DataSourceError, match="query_rejected"):
        validate_read_sql(sql_read(f"SELECT * FROM public.measurements WHERE {predicate}"), config, {"device": "0001"})
