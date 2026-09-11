"""Strict Office permission change command, independent of persistence."""

from dataclasses import dataclass
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from enterprise_platform.domain.office_content import OfficeContent

from .contracts import Identifier


class OfficePermissionChange(OfficeContent):
    actor_id: Identifier
    expected_acl_revision: int = Field(ge=1, le=9223372036854775807)
    can_read: bool
    can_edit: bool

    @model_validator(mode="after")
    def edit_requires_read(self) -> Self:
        if self.can_edit and not self.can_read:
            raise ValueError("Editing requires read permission")
        return self


@dataclass(frozen=True, slots=True)
class OfficeManagementContext:
    workspace_id: str
    file_id: UUID
    created_by: str
    acl_revision: int
