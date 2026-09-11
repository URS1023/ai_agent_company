"""Credential-only native draft mutation; caller persists ownership before invoking."""

from dataclasses import dataclass
from typing import Literal, Protocol

from .contracts import Principal
from .errors import DependencyUnavailable
from .workflow_setup_execution import NativeSetupSession


class DraftCredentialRejected(DependencyUnavailable):
    code = "native_workflow_draft_credential_unavailable"


@dataclass(frozen=True)
class NativeDraftCredentialOutcome:
    state: Literal["draft_bound", "uncertain"]
    app_id: str | None = None
    draft_id: str | None = None
    credential_id: str | None = None
    accepted_draft_hash: str | None = None
    draft_hash: str | None = None
    reason_code: str | None = None


class WorkflowDraftCredentialBinder(Protocol):
    async def bind_credential(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        draft_id: str,
        expected_draft_hash: str,
        credential_id: str,
    ) -> NativeDraftCredentialOutcome: ...
