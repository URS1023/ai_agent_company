"""Credential-free, draft-only workflow import setup contracts."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from .contracts import Contract, Identifier, Scenario

type SetupState = Literal["queued", "importing", "draft_ready", "confirmation_required", "uncertain", "failed"]
type SetupFinalState = Literal["draft_ready", "confirmation_required", "uncertain", "failed"]
Revision = Annotated[int, Field(strict=True, ge=1)]


class SetupRequest(Contract):
    source_id: Identifier
    expected_source_revision: Revision
    expected_binding_revision: Revision | None = None


class SetupView(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    id: Identifier
    workspace_id: Identifier
    device_id: Identifier
    scenario: Scenario
    source_id: Identifier
    source_revision: Identifier
    read_id: Identifier
    read_revision: Identifier
    expected_source_revision: Revision
    expected_binding_revision: Revision | None = None
    revision: Revision
    state: SetupState
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
    app_id: Identifier | None = None
    import_id: Identifier | None = None
    reason_code: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,127}$")] | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        if self.state in {"queued", "importing"} and (self.app_id is not None or self.import_id is not None):
            raise ValueError("Native identifiers require an import result")
        if self.state == "draft_ready" and (self.app_id is None or self.import_id is None):
            raise ValueError("Draft result requires app and import identifiers")
        if self.state == "confirmation_required" and self.import_id is None:
            raise ValueError("Confirmation requires an import identifier")
        if self.updated_at < self.created_at:
            raise ValueError("Invalid setup timestamps")
        return self
