"""Execute one committed enrollment claim without persistence, retries or activation.

The future caller must commit and retain exclusive journal ownership before each
invocation. A claimed input state is a prerequisite, not proof of ownership.
Cancellation propagates; unknown token writes remain uncertain. Issued tokens
are ephemeral and must be encrypted atomically with finalization by the caller.
"""

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .contracts import Contract, Principal
from .errors import AccessDenied, InvalidInput
from .workflow_enrollment_contracts import EnrollmentView
from .workflow_provisioning_contracts import PrepareCredentialReceipt, PublishReceipt, Reason
from .workflow_publication_read import NativePublicationMetadata, PublicationReadRejected, WorkflowPublicationReader
from .workflow_setup_execution import NativeSetupSession
from .workflow_token_execution import NativeWorkflowTokenOutcome, TokenIssueRejected, WorkflowTokenIssuer


class EnrollmentExecutionResult(Contract):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True, revalidate_instances="always"
    )
    state: Literal["verified", "issued", "rejected", "uncertain"]
    publication: NativePublicationMetadata | None = None
    issued: NativeWorkflowTokenOutcome | None = Field(default=None, repr=False)
    reason_code: Reason | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.state == "verified":
            if self.publication is None or self.issued is not None or self.reason_code is not None:
                raise ValueError("Verified execution requires only publication metadata")
            NativePublicationMetadata.model_validate(self.publication.model_dump())
        elif self.state == "issued":
            if (
                self.issued is None
                or self.issued.state != "issued"
                or self.publication is not None
                or self.reason_code is not None
            ):
                raise ValueError("Issued execution requires only a confirmed token outcome")
        elif self.publication is not None or self.issued is not None or self.reason_code is None:
            raise ValueError("Unconfirmed execution requires only a bounded reason")
        return self


class WorkflowEnrollmentExecutor:
    def __init__(self, *, reader: WorkflowPublicationReader, token_issuer: WorkflowTokenIssuer) -> None:
        self._reader = reader
        self._token_issuer = token_issuer

    async def execute(
        self, principal: Principal, session: NativeSetupSession, view: EnrollmentView
    ) -> EnrollmentExecutionResult:
        try:
            view = EnrollmentView.model_validate(view.model_dump())
        except (ValueError, TypeError, AttributeError):
            raise InvalidInput("invalid_workflow_enrollment_execution") from None
        provisioning = view.provisioning
        if (principal.workspace_id, principal.actor_id) != (provisioning.workspace_id, provisioning.actor_id):
            raise AccessDenied("workflow_enrollment_actor_scope_mismatch")
        if view.state not in {"verification_claimed", "token_claimed"}:
            raise InvalidInput("workflow_enrollment_execution_not_claimed")
        if view.state == "verification_claimed":
            published, prepared = provisioning.phases[3].receipt, provisioning.phases[1].receipt
            if not isinstance(published, PublishReceipt) or not isinstance(prepared, PrepareCredentialReceipt):
                raise InvalidInput("workflow_enrollment_receipts_invalid")
            try:
                metadata = await self._reader.read(
                    principal,
                    session,
                    app_id=provisioning.app_id,
                    workflow_id=published.workflow_id,
                    expected_graph_hash=published.accepted_draft_hash,
                    credential_id=prepared.credential_id,
                )
                metadata = NativePublicationMetadata.model_validate(metadata.model_dump())
                if (
                    metadata.workspace_id,
                    metadata.app_id,
                    metadata.workflow_id,
                    metadata.graph_hash,
                    metadata.credential_id,
                ) != (
                    provisioning.workspace_id,
                    provisioning.app_id,
                    published.workflow_id,
                    published.accepted_draft_hash,
                    prepared.credential_id,
                ):
                    raise ValueError("Publication does not match frozen provisioning")
                return EnrollmentExecutionResult(state="verified", publication=metadata)
            except PublicationReadRejected:
                return EnrollmentExecutionResult(state="rejected", reason_code="native_publication_preflight_rejected")
            except Exception:
                return EnrollmentExecutionResult(state="rejected", reason_code="native_publication_unconfirmed")
        try:
            issued = await self._token_issuer.issue(
                principal, session, app_id=provisioning.app_id, operation_id=view.id
            )
            # Token fields are intentionally excluded from dumps; restore the ephemeral
            # SecretStr explicitly when validating an adapter's complete outcome.
            issued = NativeWorkflowTokenOutcome.model_validate(issued.model_dump() | {"token": issued.token})
            if issued.state != "issued" or (issued.workspace_id, issued.app_id) != (
                provisioning.workspace_id,
                provisioning.app_id,
            ):
                return EnrollmentExecutionResult(state="uncertain", reason_code="native_token_issue_unconfirmed")
            return EnrollmentExecutionResult(state="issued", issued=issued)
        except TokenIssueRejected:
            return EnrollmentExecutionResult(state="rejected", reason_code="native_token_issue_preflight_rejected")
        except Exception:
            return EnrollmentExecutionResult(state="uncertain", reason_code="native_token_issue_unconfirmed")
