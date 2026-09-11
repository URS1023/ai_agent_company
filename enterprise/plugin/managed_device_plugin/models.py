"""Self-contained wire contracts matching the enterprise managed execution boundary."""

import json
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

AsciiId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[!-~]+$")]
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]


class PluginFailure(Exception):
    """Only bounded stable codes are exposed to the native tool error boundary."""


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PluginCredentials(Contract):
    origin: str = Field(strict=True, max_length=2048)
    key_id: AsciiId
    secret: SecretStr = Field(repr=False)
    expected_app_id: AsciiId
    allow_insecure_http: StrictBool = False

    @field_validator("secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not 32 <= len(raw) <= 256 or any(not 33 <= ord(c) <= 126 for c in raw):
            raise ValueError("Invalid operator secret")
        return value

    @model_validator(mode="after")
    def fixed_origin(self) -> Self:
        url = urlsplit(self.origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.port == 0
            or any(ord(char) <= 32 or char in "\\%" for char in self.origin)
            or (url.scheme == "http" and not self.allow_insecure_http)
        ):
            raise ValueError("Invalid fixed enterprise origin")
        return self


class ExecutionMetadata(Contract):
    workspace_id: AsciiId
    app_id: AsciiId
    workflow_id: UUID
    native_run_id: UUID
    node_id: AsciiId
    node_execution_id: UUID
    invoke_from: Literal["service-api"]

    @field_validator("workflow_id", "native_run_id", "node_execution_id", mode="before")
    @classmethod
    def canonical_uuid(cls, value: object) -> str:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Canonical native UUID text required")
        return value


class BusinessResult(Contract):
    scenario: Literal["alert", "quality"]
    conclusion: Literal["normal", "issues", "no_data", "incomplete", "passed", "failed", "review"]
    complete: StrictBool
    evidence: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_verdict(self) -> Self:
        allowed = (
            {"normal", "issues", "no_data", "incomplete"}
            if self.scenario == "alert"
            else {"passed", "failed", "review", "incomplete"}
        )
        if self.conclusion not in allowed:
            raise ValueError("Scenario conclusion mismatch")
        if self.conclusion in {"normal", "passed", "review"} and not self.complete:
            raise ValueError("Complete evidence required")
        if self.conclusion in {"no_data", "incomplete"} and self.complete:
            raise ValueError("Missing evidence is not complete")
        json.dumps(self.evidence, allow_nan=False)
        return self


class BusinessEnvelope(Contract):
    run_id: Identifier
    device_id: Identifier
    specification_revision: Identifier
    input_snapshot_digest: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
    result: BusinessResult


def parse_credentials(value: object) -> PluginCredentials:
    try:
        return PluginCredentials.model_validate(value)
    except ValueError:
        raise PluginFailure("invalid_plugin_credentials") from None


def parse_metadata(value: object, session_app_id: str | None, credentials: PluginCredentials) -> ExecutionMetadata:
    try:
        metadata = ExecutionMetadata.model_validate(value)
    except ValueError:
        raise PluginFailure("invalid_execution_metadata") from None
    if not session_app_id or session_app_id != metadata.app_id or metadata.app_id != credentials.expected_app_id:
        raise PluginFailure("execution_app_mismatch")
    return metadata
