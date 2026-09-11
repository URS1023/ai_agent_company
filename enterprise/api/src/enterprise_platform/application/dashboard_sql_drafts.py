"""Version-pinned SQL draft evidence; storing a draft never approves execution."""

from typing import Annotated, Literal, Protocol, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from enterprise_platform.domain.data_sources import SourceRef

from .contracts import Contract, Identifier
from .dashboard_sql_proposals import SqlProposals


class SqlDraftRecord(Contract):
    schema_version: Literal[1] = 1
    draft_id: Identifier
    workspace_id: Identifier
    actor_id: Identifier
    dashboard_id: Identifier
    dashboard_revision: int = Field(strict=True, ge=1)
    design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: SourceRef
    schema_revision: Identifier
    prompt: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=8000)]
    proposals: SqlProposals
    created_at: AwareDatetime
    status: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def consistent_context(self) -> Self:
        if self.workspace_id != self.source.workspace_id:
            raise ValueError("Draft source scope mismatch")
        slots = [slot.slot_id for slot in self.proposals.slots]
        if len(slots) != len(set(slots)):
            raise ValueError("Duplicate draft slot")
        return self


class SqlDraftRepository(Protocol):
    def create(self, record: SqlDraftRecord) -> SqlDraftRecord: ...

    def get(self, workspace_id: str, draft_id: str) -> SqlDraftRecord: ...
