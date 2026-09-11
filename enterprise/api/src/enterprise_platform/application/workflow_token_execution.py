"""Ephemeral Service API token issuance, not durable enrollment or vault registration."""

import re
from typing import Literal, Protocol, Self

from pydantic import ConfigDict, Field, SecretStr, field_validator, model_validator

from .contracts import Contract, Principal
from .errors import DependencyUnavailable
from .workflow_draft_read import canonical_native_uuid
from .workflow_setup_execution import NativeSetupSession


def validate_native_app_token(value: SecretStr) -> SecretStr:
    if re.fullmatch(r"app-[A-Za-z0-9]{24}", value.get_secret_value()) is None:
        raise ValueError("Invalid native application token")
    return value


class TokenIssueRejected(DependencyUnavailable):
    code = "native_workflow_token_issue_unavailable"


class NativeWorkflowTokenOutcome(Contract):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )
    state: Literal["issued", "uncertain"]
    workspace_id: str | None = None
    app_id: str | None = None
    token_id: str | None = None
    token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,127}$")

    @field_validator("workspace_id", "app_id", "token_id")
    @classmethod
    def valid_id(cls, value: str | None) -> str | None:
        return canonical_native_uuid(value) if value is not None else None

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr | None) -> SecretStr | None:
        return validate_native_app_token(value) if value is not None else None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        fields = (self.workspace_id, self.app_id, self.token_id, self.token)
        if self.state == "issued":
            if any(value is None for value in fields) or self.reason_code is not None:
                raise ValueError("Confirmed token outcome requires exact identity and secret")
        elif any(value is not None for value in fields) or self.reason_code is None:
            raise ValueError("Uncertain token outcome must contain only a reason")
        return self


class WorkflowTokenIssuer(Protocol):
    async def issue(
        self, principal: Principal, session: NativeSetupSession, *, app_id: str, operation_id: str
    ) -> NativeWorkflowTokenOutcome: ...
