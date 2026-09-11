"""Synchronous durable journal port; claims must commit before invoking native I/O.

Implementations atomically persist transitions and audit events. Finalization
records an already-owned outcome even if mutable device state changes meanwhile.
No lease expiry or read call grants permission to repeat an ambiguous native POST.
"""

from typing import Protocol

from .contracts import Page
from .workflow_provisioning_contracts import PhaseCommand, PhaseOutcome, ProvisioningView, StoredProvisioning


class WorkflowProvisioningRepository(Protocol):
    def create(self, provisioning: StoredProvisioning, *, actor_id: str) -> StoredProvisioning: ...
    def get(self, workspace_id: str, provisioning_id: str) -> StoredProvisioning: ...
    def find_request(self, workspace_id: str, request_key: str) -> StoredProvisioning | None: ...
    def list(
        self,
        workspace_id: str,
        *,
        setup_id: str | None = None,
        actor_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Page[ProvisioningView]: ...
    def claim(
        self,
        workspace_id: str,
        provisioning_id: str,
        *,
        command: PhaseCommand,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredProvisioning: ...
    def finish(
        self,
        workspace_id: str,
        provisioning_id: str,
        *,
        outcome: PhaseOutcome,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredProvisioning: ...
