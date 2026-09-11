"""Server-only credentials and secret-free immutable reference views."""

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, StrictBool, field_validator

from enterprise_platform.application.contracts import Contract, Identifier


def validate_token(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw or len(raw) > 4096 or any(not 33 <= ord(char) <= 126 for char in raw):
        raise ValueError("Invalid bounded service credential")
    return value


class WorkflowCredential(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    workspace_id: Identifier
    app_id: Identifier
    secret_ref: Identifier
    api_key: SecretStr = Field(repr=False, exclude=True)

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: SecretStr) -> SecretStr:
        return validate_token(value)


class CredentialView(Contract):
    workspace_id: Identifier
    app_id: Identifier
    secret_ref: Identifier
    revision: int = Field(strict=True, ge=1)
    active: StrictBool
    created_at: AwareDatetime
    updated_at: AwareDatetime
