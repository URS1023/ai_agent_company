"""Bounded branch-directory reads for one actor/app, not native conversation history.

The caller must authorize native app access before reading. Cursor ordering uses
persisted branch identifiers; it does not invent recency from random root UUIDs.
"""

from uuid import UUID

from pydantic import Field, field_validator

from .contracts import Contract, Identifier
from .workbench_branches import BranchContext


class BranchListQuery(Contract):
    workspace_id: Identifier
    actor_id: Identifier
    installed_app_id: UUID
    after: Identifier | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)

    @field_validator("installed_app_id")
    @classmethod
    def nonzero_app(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("An installed app identity is required")
        return value


class BranchPage(Contract):
    items: tuple[BranchContext, ...]
    next_after: Identifier | None = None
