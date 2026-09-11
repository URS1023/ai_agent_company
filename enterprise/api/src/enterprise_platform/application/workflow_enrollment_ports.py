"""Durable enrollment boundary in the independent enterprise database.

Every mutation commits its journal transition and audit together. Claims commit
before native I/O, never expire, and are not transferable. Finalization records
an owned outcome even when mutable device configuration has since changed.
"""

from typing import Literal, Protocol

from .workflow_enrollment_contracts import EnrollmentPhase, StoredEnrollment
from .workflow_publication_read import NativePublicationMetadata
from .workflow_token_execution import NativeWorkflowTokenOutcome


class WorkflowEnrollmentRepository(Protocol):
    def create(self, enrollment: StoredEnrollment, *, actor_id: str) -> StoredEnrollment:
        """Pin the persisted completed provisioning; unique by workspace/provisioning.

        Verify live device/setup eligibility and exact persisted provisioning
        snapshot in the transaction. Conflicting snapshots must not be adopted.
        """
        ...

    def get(self, workspace_id: str, enrollment_id: str) -> StoredEnrollment: ...

    def find_provisioning(self, workspace_id: str, provisioning_id: str) -> StoredEnrollment | None: ...

    def claim(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        phase: EnrollmentPhase,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment: ...

    def finish_verification(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        publication: NativePublicationMetadata | None = None,
        reason_code: str | None = None,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment: ...

    def store_issued_token(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        issued: NativeWorkflowTokenOutcome,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment:
        """Atomically encrypt/store the issued token and finalize token_stored.

        Revalidate the ephemeral issued outcome and exact workspace/app scope.
        Under the same row lock/transaction, verify actor/revision/nonce, insert
        a new active revision-1 vault row at the private enrollment secret_ref,
        finalize with its public receipt and native token ID, and append audit.
        Roll back all writes on failure; never overwrite/adopt an existing key.
        Do not call a vault method that opens or commits a separate transaction.
        Never persist/log plaintext or activate execution as a side effect.
        """
        ...

    def finish_token_failure(
        self,
        workspace_id: str,
        enrollment_id: str,
        *,
        state: Literal["rejected", "uncertain"],
        reason_code: str,
        nonce: str,
        expected_revision: int,
        actor_id: str,
    ) -> StoredEnrollment: ...
