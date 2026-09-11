"""Pure enrollment checkpoints; token storage is not execution activation.

Claims have no expiry or takeover. The repository must atomically store encrypted
credentials and the token_stored transition; these functions perform no I/O.
"""

import hmac
from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .contracts import Contract
from .errors import AccessDenied, Conflict
from .workflow_credential_contracts import CredentialView
from .workflow_provisioning_contracts import (
    NativeUUID,
    PrepareCredentialReceipt,
    ProvisioningView,
    PublishReceipt,
    Reason,
    Revision,
)
from .workflow_publication_read import NativePublicationMetadata

type EnrollmentState = Literal[
    "pending_verification", "verification_claimed", "verified", "token_claimed", "token_stored", "rejected", "uncertain"
]
type EnrollmentPhase = Literal["verify_publication", "issue_token"]


class _EnrollmentContract(Contract):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True, revalidate_instances="always"
    )


class EnrollmentView(_EnrollmentContract):
    id: NativeUUID
    provisioning: ProvisioningView
    revision: Revision
    state: EnrollmentState
    publication: NativePublicationMetadata | None = None
    native_token_id: NativeUUID | None = None
    credential: CredentialView | None = None
    reason_code: Reason | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def coherent_enrollment(self) -> Self:
        provisioning = self.provisioning
        if (
            provisioning.state != "published_pending_enrollment"
            or len(provisioning.phases) != 4
            or any(phase.state != "succeeded" for phase in provisioning.phases)
        ):
            raise ValueError("Confirmed completed provisioning required")
        if self.created_at < provisioning.updated_at or self.updated_at < self.created_at:
            raise ValueError("Invalid enrollment chronology")
        if self.publication is not None:
            publication = NativePublicationMetadata.model_validate(self.publication.model_dump(warnings=False))
            published = provisioning.phases[3].receipt
            prepared = provisioning.phases[1].receipt
            if not isinstance(published, PublishReceipt) or not isinstance(prepared, PrepareCredentialReceipt):
                raise ValueError("Confirmed publication and credential receipts required")
            if (
                publication.workspace_id,
                publication.app_id,
                publication.workflow_id,
                publication.graph_hash,
                publication.credential_id,
            ) != (
                provisioning.workspace_id,
                provisioning.app_id,
                published.workflow_id,
                published.accepted_draft_hash,
                prepared.credential_id,
            ):
                raise ValueError("Publication does not match frozen provisioning")
        needs_publication = self.state in {"verified", "token_claimed", "token_stored", "uncertain"}
        if needs_publication and self.publication is None:
            raise ValueError("Confirmed publication required")
        if self.state in {"pending_verification", "verification_claimed"} and self.publication is not None:
            raise ValueError("Pending verification has no publication receipt")
        if self.state in {"rejected", "uncertain"}:
            if self.reason_code is None:
                raise ValueError("Failed enrollment requires a reason")
        elif self.reason_code is not None:
            raise ValueError("Only failed enrollment has a reason")
        if self.state == "token_stored":
            if self.native_token_id is None or self.credential is None:
                raise ValueError("Stored token requires native identity and credential reference")
            credential = CredentialView.model_validate(self.credential.model_dump(warnings=False))
            if (credential.workspace_id, credential.app_id, credential.revision, credential.active) != (
                provisioning.workspace_id,
                provisioning.app_id,
                1,
                True,
            ):
                raise ValueError("New active scoped credential required")
            if not self.created_at <= credential.created_at <= credential.updated_at <= self.updated_at:
                raise ValueError("Invalid credential chronology")
        elif self.native_token_id is not None or self.credential is not None:
            raise ValueError("Only stored tokens have credential identities")
        return self


class StoredEnrollment(_EnrollmentContract):
    view: EnrollmentView
    secret_ref: NativeUUID = Field(repr=False, exclude=True)
    claim_nonce: NativeUUID | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def coherent_claim(self) -> Self:
        if (self.view.state in {"verification_claimed", "token_claimed"}) != (self.claim_nonce is not None):
            raise ValueError("Claim nonce exists only for an active claim")
        if self.view.credential is not None and self.view.credential.secret_ref != self.secret_ref:
            raise ValueError("Credential does not match the private enrollment reference")
        return self


def initialize_enrollment(
    provisioning: ProvisioningView, *, enrollment_id: str, secret_ref: str, now: datetime
) -> StoredEnrollment:
    provisioning = ProvisioningView.model_validate(provisioning)
    if provisioning.state != "published_pending_enrollment":
        raise Conflict("workflow_enrollment_complete_provisioning_required")
    if now.tzinfo is None or now < provisioning.updated_at:
        raise Conflict("workflow_enrollment_invalid_time")
    return StoredEnrollment(
        view=EnrollmentView(
            id=enrollment_id,
            provisioning=provisioning,
            revision=1,
            state="pending_verification",
            created_at=now,
            updated_at=now,
        ),
        secret_ref=secret_ref,
    )


def _check(stored: StoredEnrollment, expected_revision: int, actor_id: str, now: datetime) -> None:
    if actor_id != stored.view.provisioning.actor_id:
        raise AccessDenied("workflow_enrollment_actor_mismatch")
    if type(expected_revision) is not int or expected_revision != stored.view.revision:
        raise Conflict("workflow_enrollment_revision_conflict")
    if now.tzinfo is None or now < stored.view.updated_at:
        raise Conflict("workflow_enrollment_invalid_time")


def _check_nonce(stored: StoredEnrollment, nonce: str) -> None:
    if (
        stored.claim_nonce is None
        or not isinstance(nonce, str)
        or not nonce.isascii()
        or not hmac.compare_digest(stored.claim_nonce, nonce)
    ):
        raise Conflict("workflow_enrollment_claim_lost")


def claim_enrollment(
    stored: StoredEnrollment,
    *,
    phase: EnrollmentPhase,
    nonce: str,
    expected_revision: int,
    actor_id: str,
    now: datetime,
) -> StoredEnrollment:
    stored = StoredEnrollment.model_validate(stored)
    _check(stored, expected_revision, actor_id, now)
    if phase == "verify_publication" and stored.view.state == "pending_verification":
        state: EnrollmentState = "verification_claimed"
    elif phase == "issue_token" and stored.view.state == "verified":
        state = "token_claimed"
    else:
        raise Conflict("workflow_enrollment_not_claimable")
    view = EnrollmentView.model_validate(
        stored.view.model_dump() | {"state": state, "revision": stored.view.revision + 1, "updated_at": now}
    )
    return StoredEnrollment(view=view, secret_ref=stored.secret_ref, claim_nonce=nonce)


def finish_enrollment_verification(
    stored: StoredEnrollment,
    *,
    publication: NativePublicationMetadata | None = None,
    reason_code: str | None = None,
    nonce: str,
    expected_revision: int,
    actor_id: str,
    now: datetime,
) -> StoredEnrollment:
    stored = StoredEnrollment.model_validate(stored)
    _check(stored, expected_revision, actor_id, now)
    _check_nonce(stored, nonce)
    if stored.view.state != "verification_claimed":
        raise Conflict("workflow_enrollment_verification_not_claimed")
    if (publication is None) == (reason_code is None):
        raise Conflict("workflow_enrollment_invalid_verification_outcome")
    view = EnrollmentView.model_validate(
        stored.view.model_dump()
        | {
            "state": "verified" if publication is not None else "rejected",
            "publication": publication,
            "reason_code": reason_code,
            "revision": stored.view.revision + 1,
            "updated_at": now,
        }
    )
    return StoredEnrollment(view=view, secret_ref=stored.secret_ref)


def finish_enrollment_token(
    stored: StoredEnrollment,
    *,
    state: Literal["token_stored", "rejected", "uncertain"],
    native_token_id: str | None = None,
    credential: CredentialView | None = None,
    reason_code: str | None = None,
    nonce: str,
    expected_revision: int,
    actor_id: str,
    now: datetime,
) -> StoredEnrollment:
    stored = StoredEnrollment.model_validate(stored)
    _check(stored, expected_revision, actor_id, now)
    _check_nonce(stored, nonce)
    if stored.view.state != "token_claimed":
        raise Conflict("workflow_enrollment_token_not_claimed")
    if state not in {"token_stored", "rejected", "uncertain"}:
        raise Conflict("workflow_enrollment_invalid_token_outcome")
    view = EnrollmentView.model_validate(
        stored.view.model_dump()
        | {
            "state": state,
            "native_token_id": native_token_id,
            "credential": credential,
            "reason_code": reason_code,
            "revision": stored.view.revision + 1,
            "updated_at": now,
        }
    )
    return StoredEnrollment(view=view, secret_ref=stored.secret_ref)
