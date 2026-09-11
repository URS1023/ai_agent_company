"""Translate ordinary forms into the existing read-only reader contracts, without I/O."""

from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from sqlalchemy.engine import URL, make_url

from enterprise_platform.adapters.data_sources import validate_read_sql
from enterprise_platform.domain.data_sources import (
    DatabaseSourceConfig,
    HttpRead,
    HttpSourceConfig,
    SourceRef,
    SourceValue,
    SqlRead,
)

from .errors import AccessDenied, InvalidInput
from .input_capture import RegisteredRead
from .source_contracts import (
    DbSourceDraft,
    DbSourceView,
    Host,
    HttpSourceDraft,
    HttpSourceView,
    SourceContract,
    SourceDraft,
    SourceView,
)


class SourceEndpoint(SourceContract):
    kind: Literal["db", "http"]
    host: Host
    port: int = Field(ge=1, le=65535)
    dialect: Literal["postgresql", "mysql"] | None = None
    scheme: Literal["http", "https"] | None = None
    allow_insecure: bool = False

    @model_validator(mode="after")
    def complete_endpoint(self) -> Self:
        if (
            (self.kind == "db" and (self.dialect is None or self.scheme is not None))
            or (self.kind == "http" and (self.scheme is None or self.dialect is not None))
            or self.host.endswith(".")
        ):
            raise ValueError("Invalid operator source endpoint")
        if self.scheme == "http" and not self.allow_insecure:
            raise ValueError("HTTP requires an explicit operator insecure opt-in")
        return self


@dataclass(frozen=True)
class EndpointPolicy:
    entries: tuple[SourceEndpoint, ...]

    def __post_init__(self) -> None:
        if len(self.entries) > 1000:
            raise ValueError("Source endpoint limit exceeded")
        copied = tuple(SourceEndpoint.model_validate(item.model_dump()) for item in self.entries)
        keys = [(e.kind, e.host.lower(), e.port, e.dialect, e.scheme) for e in copied]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate source endpoint")
        object.__setattr__(self, "entries", copied)

    def require(self, connection: DbSourceDraft | HttpSourceDraft | DbSourceView | HttpSourceView) -> None:
        if isinstance(connection, (DbSourceDraft, DbSourceView)):
            host, port, dialect, scheme = connection.host, connection.port, connection.dialect, None
            insecure = not connection.tls
        else:
            try:
                url = urlsplit(connection.url)
                host, port, dialect, scheme = (
                    url.hostname or "",
                    url.port or (443 if url.scheme == "https" else 80),
                    None,
                    url.scheme,
                )
                if url.username or url.password or url.query or url.fragment or url.hostname is None:
                    raise ValueError()
            except ValueError:
                raise InvalidInput("invalid_source_endpoint") from None
            insecure = scheme != "https"
        if not any(
            (e.kind, e.host.lower(), e.port, e.dialect, e.scheme)
            == (connection.kind, host.lower(), port, dialect, scheme)
            and (not insecure or e.allow_insecure)
            for e in self.entries
        ):
            raise AccessDenied("source_endpoint_not_allowed")


def _db_target(connection: DbSourceDraft | DbSourceView) -> tuple[str, str, int, str, bool]:
    return connection.dialect, connection.host.lower(), connection.port, connection.database, connection.tls


def _http_target(connection: HttpSourceDraft | HttpSourceView) -> tuple[str, str, bool]:
    return connection.url, connection.method, connection.allow_plain_http


def registration(
    draft: SourceDraft,
    *,
    source: SourceRef,
    read_id: str,
    read_revision: str,
    previous: tuple[SourceView, RegisteredRead] | None,
) -> tuple[DbSourceView | HttpSourceView, RegisteredRead]:
    """Absent secrets only retain an unchanged target; explicit empty HTTP headers clear them."""
    config = draft.connection
    names = {draft.device_parameter, *(p.parameter_name for p in draft.parameters)}
    if draft.department_parameter:
        names.add(draft.department_parameter)
    connection: DatabaseSourceConfig | HttpSourceConfig
    read: SqlRead | HttpRead
    public: DbSourceView | HttpSourceView
    if isinstance(config, DbSourceDraft):
        username, password = config.username, config.password
        if username is None or password is None:
            if (
                previous is None
                or not isinstance(previous[0].connection, DbSourceView)
                or not isinstance(previous[1].connection, DatabaseSourceConfig)
                or _db_target(config) != _db_target(previous[0].connection)
            ):
                raise InvalidInput("fresh_database_credentials_required")
            url = make_url(previous[1].connection.connection_url.get_secret_value())
            username, password = SecretStr(url.username or ""), SecretStr(url.password or "")
        driver = "postgresql+psycopg" if config.dialect == "postgresql" else "mysql+pymysql"
        url = URL.create(
            driver,
            username=username.get_secret_value(),
            password=password.get_secret_value(),
            host=config.host,
            port=config.port,
            database=config.database,
        )
        connection = DatabaseSourceConfig(
            source=source,
            dialect=config.dialect,
            connection_url=SecretStr(url.render_as_string(hide_password=False)),
            allowed_tables=frozenset(config.allowed_tables),
            read_only_role=True,
            tls=config.tls,
            limits=draft.limits,
        )
        read = SqlRead(source=source, read_id=read_id, revision=read_revision, sql=config.sql)
        placeholder_values: dict[str, SourceValue] = {name: "validation-only" for name in names}
        validate_read_sql(read, connection, placeholder_values)
        public = DbSourceView.model_validate(
            {**config.model_dump(exclude={"username", "password"}), "credentials_configured": True}
        )
    else:
        headers = tuple((item.name, item.value) for item in config.headers) if config.headers is not None else ()
        if config.headers is None and previous is not None and isinstance(previous[1].connection, HttpSourceConfig):
            if not isinstance(previous[0].connection, HttpSourceView):
                raise InvalidInput("source_type_mismatch")
            if previous[1].connection.headers and _http_target(config) != _http_target(previous[0].connection):
                raise InvalidInput("fresh_http_credentials_required")
            headers = previous[1].connection.headers
        parsed = urlsplit(config.url)
        connection = HttpSourceConfig(
            source=source,
            url=config.url,
            allowed_hosts=frozenset({parsed.hostname or ""}),
            headers=headers,
            allow_plain_http=config.allow_plain_http,
            limits=draft.limits,
        )
        read = HttpRead(
            source=source,
            read_id=read_id,
            revision=read_revision,
            method=config.method,
            read_only=True,
            parameter_names=frozenset(names),
            rows_path=config.rows_path,
            pagination=config.pagination,
        )
        public = HttpSourceView.model_validate(
            {
                **config.model_dump(exclude={"headers"}),
                "header_names": tuple(name for name, _ in headers),
                "credentials_configured": bool(headers),
            }
        )
    entry = RegisteredRead(
        connection=connection,
        read=read,
        device_ids=frozenset(draft.device_ids),
        device_parameter=draft.device_parameter,
        device_column=draft.device_column,
        scope_attribute=draft.scope_attribute,
        department_parameter=draft.department_parameter,
        parameters=draft.parameters,
    )
    return public, entry
