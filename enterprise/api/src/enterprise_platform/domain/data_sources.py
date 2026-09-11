"""Server-owned connection policies and immutable evidence from actual reads.

Connection objects, especially their secret fields, stay outside model/tool inputs.
Application services resolve current workspace authorization before selecting these
policies; a read must also match the exact source revision captured by that service.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from .measurements import MeasurementError, parse_decimal, require_aware_time

type SourceValue = str | int | bool | Decimal | datetime | date | None
type SourceErrorCode = Literal[
    "source_mismatch",
    "query_rejected",
    "parameter_mismatch",
    "response_invalid",
    "read_failed",
    "http_status",
    "timeout",
    "row_limit",
    "size_limit",
    "page_limit",
    "repeated_cursor",
    "truncated_result",
]
Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=240, strip_whitespace=True)]
ParameterName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)]


def _confirmed_read(value: object) -> Literal[True]:
    if value is not True:
        raise ValueError("A server-owned boolean read-only confirmation is required")
    return True


ReadOnlyFlag = Annotated[Literal[True], BeforeValidator(_confirmed_read)]


class DataSourceError(ValueError):
    """Stable failure codes without credentials, SQL, raw responses or driver messages."""

    code: SourceErrorCode
    status_code: int | None
    pages: tuple["PageEvidence", ...]

    def __init__(
        self, code: SourceErrorCode, *, status_code: int | None = None, pages: tuple["PageEvidence", ...] = ()
    ) -> None:
        self.code, self.status_code = code, status_code
        self.pages = tuple(pages)
        super().__init__(code)


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class SourceRef(Policy):
    workspace_id: Name
    source_id: Name
    revision: Name


class ReadLimits(Policy):
    max_rows: int = Field(default=1000, ge=1, le=100000)
    max_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_pages: int = Field(default=20, ge=1, le=100)
    timeout_seconds: float = Field(default=15.0, ge=0.01, le=120.0)


class DatabaseSourceConfig(Policy):
    source: SourceRef
    dialect: Literal["postgresql", "mysql", "sqlite"]
    connection_url: SecretStr = Field(repr=False)
    allowed_tables: frozenset[Name] = Field(min_length=1)
    read_only_role: ReadOnlyFlag
    tls: bool = True
    limits: ReadLimits = Field(default_factory=ReadLimits)

    @model_validator(mode="after")
    def validate_tables(self) -> "DatabaseSourceConfig":
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}", name)
            for name in self.allowed_tables
        ):
            raise ValueError("Allowed tables require explicit simple identifiers, optionally schema-qualified")
        return self


class HttpSourceConfig(Policy):
    source: SourceRef
    url: str = Field(min_length=1, max_length=4096)
    allowed_hosts: frozenset[Name] = Field(min_length=1)
    headers: tuple[tuple[Name, SecretStr], ...] = Field(default=(), repr=False)
    allow_plain_http: bool = False
    limits: ReadLimits = Field(default_factory=ReadLimits)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "HttpSourceConfig":
        parsed = urlsplit(self.url)
        allowed = {host.lower() for host in self.allowed_hosts}
        if (
            parsed.scheme not in ({"https", "http"} if self.allow_plain_http else {"https"})
            or not parsed.hostname
            or parsed.hostname.lower() not in allowed
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.hostname.endswith(".")
            or parsed.port == 0
        ):
            raise ValueError("Endpoint must use an exact registered host, TLS and a fixed path without credentials")
        names = set()
        forbidden = {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization", "cookie"}
        for name, secret in self.headers:
            if (
                name.lower() in names
                or name.lower() in forbidden
                or not re.fullmatch(r"[A-Za-z0-9-]+", name)
                or any(char in secret.get_secret_value() for char in "\r\n")
            ):
                raise ValueError("Duplicate, routing or malformed headers are excluded")
            names.add(name.lower())
        return self


class SqlRead(Policy):
    source: SourceRef
    read_id: Name
    revision: Name
    sql: str = Field(min_length=1, max_length=20000)


class CursorPagination(Policy):
    parameter: ParameterName
    next_cursor_path: tuple[Name, ...] = Field(min_length=1)
    has_more_path: tuple[Name, ...] = Field(default=("has_more",), min_length=1)


class HttpRead(Policy):
    source: SourceRef
    read_id: Name
    revision: Name
    method: Literal["GET", "POST"]
    read_only: ReadOnlyFlag
    parameter_names: frozenset[ParameterName] = frozenset()
    rows_path: tuple[Name, ...] = ()
    pagination: CursorPagination | None = None

    @model_validator(mode="after")
    def separate_cursor(self) -> "HttpRead":
        if self.pagination and self.pagination.parameter in self.parameter_names:
            raise ValueError("Pagination cursor is managed by the reader, not supplied by the user")
        return self


def source_value(value: object) -> SourceValue:
    """Keep source scalar types; binary floats and nested cells need explicit mapping upstream."""
    if value is None or isinstance(value, (str, bool, datetime, date)):
        return value
    if isinstance(value, (Decimal, int)):
        try:
            parse_decimal(value)
        except MeasurementError:
            raise DataSourceError("response_invalid") from None
        return value
    raise DataSourceError("response_invalid")


@dataclass(frozen=True, slots=True)
class PreparedSql:
    sql: str
    parameters: tuple[tuple[str, SourceValue], ...]


@dataclass(frozen=True, slots=True)
class PageEvidence:
    number: int
    row_count: int
    response_digest: str
    request_cursor_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.number) is not int
            or self.number < 1
            or type(self.row_count) is not int
            or self.row_count < 0
            or not re.fullmatch(r"[0-9a-f]{64}", self.response_digest)
            or self.request_cursor_digest is not None
            and not re.fullmatch(r"[0-9a-f]{64}", self.request_cursor_digest)
        ):
            raise DataSourceError("response_invalid")


@dataclass(frozen=True, slots=True)
class FrozenRows:
    source: SourceRef
    read_id: str
    read_revision: str
    read_fingerprint: str
    captured_at: datetime
    columns: tuple[str, ...]
    rows: tuple[tuple[SourceValue, ...], ...]
    pages: tuple[PageEvidence, ...]

    def __post_init__(self) -> None:
        require_aware_time(self.captured_at, "captured_at")
        if len(set(self.columns)) != len(self.columns) or any(
            not isinstance(name, str) or not name for name in self.columns
        ):
            raise DataSourceError("response_invalid")
        rows = tuple(tuple(source_value(value) for value in row) for row in self.rows)
        pages = tuple(self.pages)
        if any(len(row) != len(self.columns) for row in rows):
            raise DataSourceError("response_invalid")
        if (
            not pages
            or any(page.number != index for index, page in enumerate(pages, start=1))
            or sum(page.row_count for page in pages) != len(rows)
        ):
            raise DataSourceError("response_invalid")
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "pages", pages)

    @property
    def complete(self) -> bool:
        """Only complete reads produce this type; partial reads raise DataSourceError."""
        return True

    @property
    def records(self) -> tuple[dict[str, SourceValue], ...]:
        return tuple(dict(zip(self.columns, row, strict=True)) for row in self.rows)
