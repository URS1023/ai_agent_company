"""Atomic activation persistence in the independent enterprise database."""

from typing import Protocol

from .workflow_activation_contracts import ActivationView


class WorkflowActivationRepository(Protocol):
    def create(self, activation: ActivationView, *, actor_id: str) -> ActivationView:
        """Validate live dependencies, update binding and insert activation plus audits atomically."""
        ...

    def get(self, workspace_id: str, activation_id: str) -> ActivationView: ...

    def find_enrollment(self, workspace_id: str, enrollment_id: str) -> ActivationView | None: ...

    def revoke(self, workspace_id: str, activation_id: str, *, expected_revision: int, actor_id: str) -> ActivationView:
        """Fence active revision and commit revocation with its audit, leaving binding intact."""
        ...
