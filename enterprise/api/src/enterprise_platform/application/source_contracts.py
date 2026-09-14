"""Source form input and strictly credential-free browser views.

An absent credential on PUT means retain the stored credential, never a masked
placeholder. Connection targets changing requires fresh credentials. Server IDs,
workspace, revisions and tested status are never supplied by the form.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator

from enterprise_platform.domain.data_sources import CursorPagination, ReadLimits, ReadOnlyFlag

from .contracts import Contract, Identifier
from .input_capture import ParameterDeclaration

ParameterName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)]
Label = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
Host = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=253, pattern=r"^[A-Za-z0-9:.\-]+$")]
TableName = Annotated[
    str,
    StringConstraints(strict=True, max_length=240, pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$"),
]


class SourceContract(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class DbSourceSettings(SourceContract):
    kind: Literal["db"] = "db"
    dialect: Literal["postgresql", "mysql"]
    host: Host
    port: int = Field(ge=1, le=65535)
    database: Label
    tls: bool = True
    allowed_tables: tuple[TableName, ...] = Field(min_length=1, max_length=100)
    sql: str = Field(min_length=1, max_length=20000)


class DbSourceDraft(DbSourceSettings):
    username: SecretStr | None = Field(default=None, repr=False)
    password: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def complete_credentials(self) -> Self:
        if (self.username is None) != (self.password is None):
            raise ValueError("Provide username and password together")
        if (
            self.username is not None
            and self.password is not None
            and (
                not 1 <= len(self.username.get_secret_value()) <= 256
                or not 1 <= len(self.password.get_secret_value()) <= 4096
                or "\x00" in self.username.get_secret_value() + self.password.get_secret_value()
            )
        ):
            raise ValueError("Invalid database credential")
        return self


class HeaderCredential(SourceContract):
    name: str = Field(pattern=r"^[A-Za-z0-9-]+$", min_length=1, max_length=128)
    value: SecretStr = Field(repr=False)

    @model_validator(mode="after")
    def valid_header(self) -> Self:
        if not 1 <= len(self.value.get_secret_value()) <= 4096 or any(
            ord(c) < 32 or ord(c) == 127 for c in self.value.get_secret_value()
        ):
            raise ValueError("Invalid HTTP credential header")
        return self


class HttpSourceSettings(SourceContract):
    kind: Literal["http"] = "http"
    url: str = Field(min_length=1, max_length=4096)
    method: Literal["GET", "POST"] = "GET"
    allow_plain_http: bool = False
    rows_path: tuple[Identifier, ...] = Field(default=(), max_length=16)
    pagination: CursorPagination | None = None

    @field_validator("pagination", mode="before")
    @classmethod
    def wire_cursor_paths(cls, value: object) -> object:
        # FastAPI validates decoded JSON as Python; only adapt its array containers.
        # The reader's strict cursor policy still validates every scalar and field.
        if isinstance(value, dict):
            result = value.copy()
            for key in ("next_cursor_path", "has_more_path"):
                if isinstance(result.get(key), list):
                    result[key] = tuple(result[key])
            return result
        return value


class HttpSourceDraft(HttpSourceSettings):
    headers: tuple[HeaderCredential, ...] | None = Field(default=None, max_length=30, repr=False)


class SourceScope(SourceContract):
    enabled: bool = Field(default=True, strict=True)
    name: Label
    device_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=1000)
    device_parameter: ParameterName
    device_column: Identifier
    scope_attribute: Literal["id", "device_code"] = "device_code"
    department_parameter: ParameterName | None = None
    parameters: tuple[ParameterDeclaration, ...] = Field(default=(), max_length=100)
    limits: ReadLimits = Field(default_factory=ReadLimits)

    @model_validator(mode="after")
    def unambiguous_scope(self) -> Self:
        names = [self.device_parameter] + ([self.department_parameter] if self.department_parameter else [])
        inputs = [item.input_key for item in self.parameters]
        parameter_names = [item.parameter_name for item in self.parameters]
        if (
            len(set(self.device_ids)) != len(self.device_ids)
            or len(set(names + parameter_names)) != len(names + parameter_names)
            or len(set(inputs)) != len(inputs)
            or set(inputs) & set(names)
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) for name in parameter_names)
        ):
            raise ValueError("Duplicate or invalid device/parameter scope")
        return self


class SourceDraft(SourceScope):
    read_only_confirmed: ReadOnlyFlag
    connection: Annotated[DbSourceDraft | HttpSourceDraft, Field(discriminator="kind")]


class DbSourceView(DbSourceSettings):
    credentials_configured: bool


class HttpSourceView(HttpSourceSettings):
    header_names: tuple[str, ...]
    credentials_configured: bool


class SourceView(SourceScope):
    workspace_id: Identifier
    source_id: Identifier
    source_revision: Identifier
    read_id: Identifier
    read_revision: Identifier
    revision: int = Field(ge=1)
    connection: Annotated[DbSourceView | HttpSourceView, Field(discriminator="kind")]
    connection_status: Literal["not_tested"] = "not_tested"
    created_at: AwareDatetime
    updated_at: AwareDatetime


class SourceCapabilities(SourceContract):
    can_manage: bool
    write_enabled: bool
    reason_code: (
        Literal["encryption_key_missing", "source_egress_policy_missing", "source_management_unavailable"] | None
    )
