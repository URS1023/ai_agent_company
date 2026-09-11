"""Capture actual source rows only when a trusted workflow read node calls this service.

The HTTP token boundary is external. Nonces and credential-bearing registrations
never enter workflow/model inputs. Historical captures bypass live registrations;
fresh reads require an exact catalog version and server-owned device scope. Driver
timeouts bound DB work: cancelling an asyncio worker does not cancel its DB thread.
"""

import asyncio
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

import httpx
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from enterprise_platform.adapters.data_sources import DatabaseSourceReader, HttpSourceReader, read_fingerprint
from enterprise_platform.domain.data_sources import (
    DatabaseSourceConfig,
    FrozenRows,
    HttpRead,
    HttpSourceConfig,
    PageEvidence,
    Policy,
    SourceRef,
    SourceValue,
    SqlRead,
)
from enterprise_platform.domain.measurements import MeasurementError, parse_decimal

from .contracts import Device, Identifier, JsonObject, Run, canonical_hash, canonical_json
from .errors import AccessDenied, InvalidInput, InvalidState
from .ports import EnterpriseRepository

type ParameterKind = Literal["string", "integer", "decimal", "boolean", "date", "datetime"]
type ReadKey = tuple[str, str, str, str, str]


class ParameterDeclaration(Policy):
    input_key: Identifier
    parameter_name: Identifier
    kind: ParameterKind
    nullable: bool = False


class RegisteredRead(Policy):
    connection: DatabaseSourceConfig | HttpSourceConfig
    read: SqlRead | HttpRead
    device_ids: frozenset[Identifier] = Field(min_length=1)
    device_parameter: Identifier
    device_column: Identifier
    scope_attribute: Literal["id", "device_code"] = "device_code"
    department_parameter: Identifier | None = None
    parameters: tuple[ParameterDeclaration, ...] = ()

    @model_validator(mode="after")
    def check_registration(self) -> "RegisteredRead":
        if self.connection.source != self.read.source or isinstance(self.connection, HttpSourceConfig) != isinstance(
            self.read, HttpRead
        ):
            raise ValueError("Connection and read contracts must agree")
        names = [item.parameter_name for item in self.parameters]
        protected = [self.device_parameter] + ([self.department_parameter] if self.department_parameter else [])
        inputs = [item.input_key for item in self.parameters]
        if (
            len(set(names + protected)) != len(names + protected)
            or len(set(inputs)) != len(inputs)
            or set(inputs) & set(protected)
        ):
            raise ValueError("Duplicate parameters or user-controlled scope keys are excluded")
        if isinstance(self.read, HttpRead) and set(names + protected) != self.read.parameter_names:
            raise ValueError("Registered parameters must cover the complete HTTP parameter contract")
        return self


def _read_key(read: SqlRead | HttpRead | FrozenRows) -> ReadKey:
    return (
        read.source.workspace_id,
        read.source.source_id,
        read.source.revision,
        read.read_id,
        read.read_revision if isinstance(read, FrozenRows) else read.revision,
    )


def _run_key(run: Run) -> ReadKey:
    return run.workspace_id, run.spec.source_id, run.spec.source_revision, run.spec.read_id, run.spec.read_revision


class RegisteredReadRegistry(Protocol):
    def resolve(
        self, workspace_id: str, source_id: str, source_revision: str, read_id: str, read_revision: str
    ) -> RegisteredRead: ...


@dataclass(frozen=True, slots=True, init=False)
class ImmutableReadCatalog:
    """Production injection seam; replacing a catalog is an explicit server configuration operation."""

    _entries: tuple[RegisteredRead, ...]

    def __init__(self, entries: tuple[RegisteredRead, ...]) -> None:
        copied = tuple(RegisteredRead.model_validate(entry.model_dump()) for entry in entries)
        if len({_read_key(entry.read) for entry in copied}) != len(copied):
            raise ValueError("Duplicate registered read version")
        object.__setattr__(self, "_entries", copied)

    def resolve(
        self, workspace_id: str, source_id: str, source_revision: str, read_id: str, read_revision: str
    ) -> RegisteredRead:
        key = (workspace_id, source_id, source_revision, read_id, read_revision)
        for entry in self._entries:
            if _read_key(entry.read) == key:
                return entry
        raise InvalidInput("registered_read_unavailable")


class SourceReaderPort(Protocol):
    async def read(self, registration: RegisteredRead, parameters: Mapping[str, SourceValue]) -> FrozenRows: ...


class RegisteredSourceReader:
    _http_transport: httpx.AsyncBaseTransport | None

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http_transport = http_transport

    async def read(self, registration: RegisteredRead, parameters: Mapping[str, SourceValue]) -> FrozenRows:
        if isinstance(registration.connection, HttpSourceConfig) and isinstance(registration.read, HttpRead):
            async with HttpSourceReader(registration.connection, transport=self._http_transport) as reader:
                return await reader.read(registration.read, parameters)
        if isinstance(registration.connection, DatabaseSourceConfig) and isinstance(registration.read, SqlRead):
            connection_config, read_config = registration.connection, registration.read

            def execute() -> FrozenRows:
                reader = DatabaseSourceReader(connection_config)
                try:
                    return reader.read(read_config, parameters)
                finally:
                    reader.close()

            return await asyncio.to_thread(execute)
        raise InvalidInput("registered_reader_type_mismatch")


class _Cell(Policy):
    kind: Literal["string", "integer", "decimal", "boolean", "date", "datetime", "null"]
    value: str | bool | None

    @classmethod
    def encode(cls, value: SourceValue) -> "_Cell":
        if value is None:
            return cls(kind="null", value=None)
        if isinstance(value, bool):
            return cls(kind="boolean", value=value)
        if isinstance(value, datetime):
            return cls(kind="datetime", value=value.isoformat())
        if isinstance(value, date):
            return cls(kind="date", value=value.isoformat())
        if isinstance(value, Decimal):
            return cls(kind="decimal", value=str(value))
        return cls(kind="integer" if isinstance(value, int) else "string", value=str(value))

    def decode(self) -> SourceValue:
        if self.kind == "null" and self.value is None:
            return None
        if self.kind == "boolean" and type(self.value) is bool:
            return self.value
        if isinstance(self.value, str):
            if self.kind == "string":
                return self.value
            if self.kind == "decimal":
                return parse_decimal(self.value)
            if self.kind == "integer" and re.fullmatch(r"-?(0|[1-9][0-9]*)", self.value):
                return int(self.value)
            if self.kind == "date":
                return date.fromisoformat(self.value)
            if self.kind == "datetime":
                return datetime.fromisoformat(self.value)
        raise InvalidInput("input_snapshot_cell_invalid")


class _Page(Policy):
    number: int = Field(ge=1)
    row_count: int = Field(ge=0)
    response_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_cursor_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class _Rows(Policy):
    source: SourceRef
    read_id: Identifier
    read_revision: Identifier
    read_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: AwareDatetime
    columns: tuple[str, ...]
    rows: tuple[tuple[_Cell, ...], ...]
    pages: tuple[_Page, ...]

    def decode(self) -> FrozenRows:
        return FrozenRows(
            self.source,
            self.read_id,
            self.read_revision,
            self.read_fingerprint,
            self.captured_at,
            self.columns,
            tuple(tuple(cell.decode() for cell in row) for row in self.rows),
            tuple(
                PageEvidence(page.number, page.row_count, page.response_digest, page.request_cursor_digest)
                for page in self.pages
            ),
        )


def encode_rows(rows: FrozenRows) -> JsonObject:
    return _Rows(
        source=rows.source,
        read_id=rows.read_id,
        read_revision=rows.read_revision,
        read_fingerprint=rows.read_fingerprint,
        captured_at=rows.captured_at,
        columns=rows.columns,
        rows=tuple(tuple(_Cell.encode(value) for value in row) for row in rows.rows),
        pages=tuple(
            _Page(
                number=p.number,
                row_count=p.row_count,
                response_digest=p.response_digest,
                request_cursor_digest=p.request_cursor_digest,
            )
            for p in rows.pages
        ),
    ).model_dump(mode="json")


def decode_rows(document: JsonObject) -> FrozenRows:
    try:
        return _Rows.model_validate_json(_snapshot_json(document)).decode()
    except (ValueError, ValidationError):
        raise InvalidInput("input_snapshot_invalid") from None


def _snapshot_json(document: JsonObject) -> str:
    value = canonical_json(document)
    if len(value.encode()) > 16 * 1024 * 1024:
        raise InvalidInput("input_snapshot_size_limit")
    return value


class _Capture(Policy):
    schema_version: Literal[1] = 1
    device_id: Identifier
    parameters_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_column: Identifier
    scope_value: str
    data: _Rows


def _scope(rows: FrozenRows, column: str, value: str) -> None:
    if rows.rows and (column not in rows.columns or any(record.get(column) != value for record in rows.records)):
        raise AccessDenied("source_row_device_scope_mismatch")


def restore_capture(run: Run) -> FrozenRows:
    """Validate and restore an existing capture without consulting live registrations or performing I/O."""
    if run.input_snapshot is None:
        raise InvalidState("input_snapshot_missing")
    try:
        snapshot = _Capture.model_validate_json(_snapshot_json(run.input_snapshot))
        rows = snapshot.data.decode()
        if (
            snapshot.device_id != run.spec.device_id
            or snapshot.parameters_digest != canonical_hash(run.spec.parameters)
            or _read_key(rows) != _run_key(run)
        ):
            raise InvalidInput("input_snapshot_scope_mismatch")
        _scope(rows, snapshot.scope_column, snapshot.scope_value)
        return rows
    except (ValueError, ValidationError):
        raise InvalidInput("input_snapshot_invalid") from None


def _parameter(value: object, declaration: ParameterDeclaration) -> SourceValue:
    if value is None and declaration.nullable:
        return None
    try:
        if declaration.kind == "string" and isinstance(value, str):
            return value
        if declaration.kind == "boolean" and type(value) is bool:
            return value
        if declaration.kind == "integer" and type(value) is int:
            parse_decimal(value)
            return value
        if declaration.kind == "decimal" and (type(value) is int or isinstance(value, str)):
            return parse_decimal(value)
        if declaration.kind in {"date", "datetime"} and isinstance(value, str):
            if declaration.kind == "date":
                return date.fromisoformat(value)
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed
    except (ValueError, MeasurementError):
        pass
    raise InvalidInput("capture_parameter_type_mismatch")


def _bind(run: Run, device: Device, entry: RegisteredRead) -> tuple[str, dict[str, SourceValue]]:
    return bind_registered_parameters(device, entry, run.spec.parameters)


def bind_registered_parameters(
    device: Device, entry: RegisteredRead, parameters: Mapping[str, object]
) -> tuple[str, dict[str, SourceValue]]:
    """Bind declared inputs while deriving protected scope exclusively from the device record."""
    if set(parameters) != {item.input_key for item in entry.parameters}:
        raise InvalidInput("capture_parameter_keys_mismatch")
    scope_value = device.id if entry.scope_attribute == "id" else device.device_code
    values: dict[str, SourceValue] = {entry.device_parameter: scope_value}
    if entry.department_parameter:
        values[entry.department_parameter] = device.department
    values.update({item.parameter_name: _parameter(parameters[item.input_key], item) for item in entry.parameters})
    return scope_value, values


class InputCaptureService:
    repository: EnterpriseRepository
    registry: RegisteredReadRegistry
    reader: SourceReaderPort
    actor_id: str

    def __init__(
        self,
        repository: EnterpriseRepository,
        registry: RegisteredReadRegistry,
        reader: SourceReaderPort,
        *,
        actor_id: str = "enterprise-read-node",
    ) -> None:
        self.repository, self.registry, self.reader, self.actor_id = repository, registry, reader, actor_id

    async def capture(self, workspace_id: str, run_id: str, nonce: str) -> FrozenRows:
        run = await asyncio.to_thread(self.repository.get_run, workspace_id, run_id)
        if (run.workspace_id, run.id) != (workspace_id, run_id) or run.status not in {
            "claimed",
            "dispatched",
            "uncertain",
        }:
            raise InvalidState("capture_run_not_in_flight")
        if (
            not nonce
            or not run.dispatch_nonce
            or not secrets.compare_digest(nonce.encode(), run.dispatch_nonce.encode())
        ):
            raise InvalidState("capture_nonce_mismatch")
        if run.input_snapshot is not None:
            return restore_capture(run)
        if run.spec.recompute_of:
            raise InvalidState("recompute_input_unavailable")
        entry = await asyncio.to_thread(self.registry.resolve, *_run_key(run))
        if _read_key(entry.read) != _run_key(run):
            raise InvalidInput("registered_read_identity_mismatch")
        if run.spec.device_id not in entry.device_ids:
            raise AccessDenied("device_source_access_denied")
        device = await asyncio.to_thread(self.repository.get_device, workspace_id, run.spec.device_id)
        if device.workspace_id != workspace_id or device.id != run.spec.device_id or device.deleted_at is not None:
            raise AccessDenied("capture_device_unavailable")
        scope_value, parameters = _bind(run, device, entry)
        expected_fingerprint = read_fingerprint(entry.read, tuple(sorted(parameters.items())))
        parameters_digest = canonical_hash(run.spec.parameters)
        rows = await self.reader.read(entry, parameters)
        if _read_key(rows) != _run_key(run):
            raise InvalidInput("source_result_identity_mismatch")
        if rows.read_fingerprint != expected_fingerprint:
            raise InvalidInput("source_result_execution_mismatch")
        _scope(rows, entry.device_column, scope_value)
        document = _Capture(
            device_id=run.spec.device_id,
            parameters_digest=parameters_digest,
            scope_column=entry.device_column,
            scope_value=scope_value,
            data=_Rows.model_validate_json(canonical_json(encode_rows(rows))),
        ).model_dump(mode="json")
        _snapshot_json(document)
        saved = await asyncio.to_thread(
            self.repository.capture_input, workspace_id, run_id, nonce, document, actor_id=self.actor_id
        )
        return restore_capture(saved)
