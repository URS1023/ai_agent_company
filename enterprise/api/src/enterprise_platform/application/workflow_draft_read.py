"""Secret-free native draft identities at the server provisioning boundary."""

from typing import Protocol
from uuid import UUID

from pydantic import Field, field_validator

from .contracts import Contract, Principal
from .errors import DependencyUnavailable
from .workflow_setup_execution import NativeSetupSession


def canonical_native_uuid(value: str) -> str:
    parsed = UUID(value)
    if parsed.int == 0 or str(parsed) != value:
        raise ValueError("Canonical native identity required")
    return value


class NativeDraftMetadata(Contract):
    workspace_id: str = Field(strict=True)
    app_id: str = Field(strict=True)
    draft_id: str = Field(strict=True)
    draft_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")

    @field_validator("workspace_id", "app_id", "draft_id")
    @classmethod
    def valid_identity(cls, value: str) -> str:
        return canonical_native_uuid(value)


class DraftReadRejected(DependencyUnavailable):
    code = "native_workflow_draft_read_unavailable"


class WorkflowDraftReader(Protocol):
    async def read(self, principal: Principal, session: NativeSetupSession, *, app_id: str) -> NativeDraftMetadata: ...
