"""Server-owned plugin HMAC configuration, independent of Service API credentials."""

from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, StrictBool, StringConstraints, field_validator, model_validator

from .contracts import Contract, Principal
from .errors import DependencyUnavailable
from .workflow_setup_execution import NativeSetupSession

AsciiId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[!-~]+$")]


def canonical_uuid(value: str) -> str:
    parsed = UUID(value)
    if str(parsed) != value or parsed.int == 0:
        raise ValueError("Invalid native identity")
    return value


class PluginCredentialConfiguration(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    origin: str = Field(strict=True, max_length=2048)
    key_id: AsciiId
    signing_secret: SecretStr = Field(repr=False, exclude=True)
    allow_insecure_http: StrictBool = False
    expected_plugin_unique_identifier: Annotated[
        str, StringConstraints(strict=True, min_length=1, max_length=512, pattern=r"^[!-~]+$")
    ]

    @field_validator("signing_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not 32 <= len(raw) <= 256 or any(not 33 <= ord(c) <= 126 for c in raw):
            raise ValueError("Invalid plugin signing secret")
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
            or any(ord(c) <= 32 or c in "\\%" for c in self.origin)
            or (url.scheme == "http" and not self.allow_insecure_http)
        ):
            raise ValueError("Invalid fixed plugin origin")
        return self


class NativePluginCredentialOutcome(Contract):
    state: Literal["credential_created", "uncertain"]
    credential_id: str | None = None
    plugin_unique_identifier: str | None = None
    reason_code: str | None = None

    @model_validator(mode="after")
    def valid_state(self) -> Self:
        if self.state == "credential_created":
            if self.credential_id is None or not self.plugin_unique_identifier or self.reason_code is not None:
                raise ValueError("Confirmed credential identity required")
            canonical_uuid(self.credential_id)
        elif self.credential_id is not None or not self.reason_code:
            raise ValueError("Uncertain credential requires a reason without an identity")
        return self


class PluginCredentialRejected(DependencyUnavailable):
    code = "native_plugin_credential_rejected"


class WorkflowPluginCredentialClient(Protocol):
    async def prepare(
        self, principal: Principal, session: NativeSetupSession, *, app_id: str, operation_id: str
    ) -> NativePluginCredentialOutcome: ...
