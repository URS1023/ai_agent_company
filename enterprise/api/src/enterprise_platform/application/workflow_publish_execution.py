"""Exact native publication boundary, not executable enrollment or business binding.

The native draft hash identifies the graph only. Published features and variables
are the values in the locked native draft, not values attested by this hash.
Callers must persist an operation claim before requesting this side effect.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from .contracts import Principal
from .errors import DependencyUnavailable
from .workflow_setup_execution import NativeSetupSession


class PublishRejected(DependencyUnavailable):
    """Invalid command or capability preflight; no native publish POST was sent."""

    code = "native_workflow_publish_unavailable"


@dataclass(frozen=True)
class NativePublishOutcome:
    state: Literal["published", "uncertain"]
    app_id: str | None = None
    workflow_id: str | None = None
    accepted_draft_hash: str | None = None
    reason_code: str | None = None


class WorkflowPublisher(Protocol):
    async def publish(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        expected_draft_hash: str,
        operation_id: str,
    ) -> NativePublishOutcome: ...
