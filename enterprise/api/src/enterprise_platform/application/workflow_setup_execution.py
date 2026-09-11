"""Ephemeral native session and import outcome at the provisioning I/O boundary.

Session values are neither business command fields nor durable setup state. An
imported draft is not a published, credential-bound or executable business workflow.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from .contracts import Principal, Scenario
from .errors import DependencyUnavailable


@dataclass(frozen=True)
class NativeSetupSession:
    cookie_header: str | None = field(repr=False)
    authorization: str | None = field(repr=False)
    csrf_token: str | None = field(repr=False)


@dataclass(frozen=True)
class NativeImportOutcome:
    state: Literal["draft_ready", "confirmation_required", "uncertain", "failed"]
    app_id: str | None = None
    import_id: str | None = None
    reason_code: str | None = None


class SetupImportRejected(DependencyUnavailable):
    """Preflight failed before sending the native import POST."""

    code = "native_workflow_setup_unavailable"


class WorkflowDraftImporter(Protocol):
    async def import_default(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        setup_id: str,
        scenario: Scenario,
        name: str,
    ) -> NativeImportOutcome: ...
