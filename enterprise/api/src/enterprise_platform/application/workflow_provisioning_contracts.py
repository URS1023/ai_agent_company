"""Pure, secret-free phase journal for post-import native provisioning.

The import-only SetupView remains unchanged. Publication is a checkpoint awaiting
execution enrollment, key registration, specifications, and business binding.
Claims have no time-based takeover: ambiguous native POSTs require reconciliation.
"""

import hmac
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from .contracts import Contract, Identifier, Scenario, canonical_hash
from .errors import AccessDenied, Conflict
from .workflow_draft_read import canonical_native_uuid
from .workflow_setup_contracts import SetupView

NativeUUID = Annotated[
    str, StringConstraints(strict=True, min_length=36, max_length=36), AfterValidator(canonical_native_uuid)
]
NativeHash = Annotated[str, StringConstraints(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(strict=True, ge=1)]
Reason = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,127}$")]
RequestKey = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[!-~]+$")]
PluginIdentifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512, pattern=r"^[!-~]+$")]
type PhaseKind = Literal["read_draft", "prepare_credential", "bind_credential", "publish"]
type PhaseState = Literal["queued", "claimed", "succeeded", "rejected", "uncertain"]
type ProvisioningState = Literal["in_progress", "rejected", "uncertain", "published_pending_enrollment"]
_PHASE_ORDER: tuple[PhaseKind, ...] = ("read_draft", "prepare_credential", "bind_credential", "publish")


class _JournalContract(Contract):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class _Command(_JournalContract):
    operation_id: NativeUUID
    app_id: NativeUUID


class ReadDraftCommand(_Command):
    kind: Literal["read_draft"] = "read_draft"


class PrepareCredentialCommand(_Command):
    kind: Literal["prepare_credential"] = "prepare_credential"
    config_ref: Identifier
    config_revision: Revision


class BindCredentialCommand(_Command):
    kind: Literal["bind_credential"] = "bind_credential"
    draft_id: NativeUUID
    credential_id: NativeUUID
    expected_draft_hash: NativeHash


class PublishCommand(_Command):
    kind: Literal["publish"] = "publish"
    expected_draft_hash: NativeHash


PhaseCommand = Annotated[
    ReadDraftCommand | PrepareCredentialCommand | BindCredentialCommand | PublishCommand, Field(discriminator="kind")
]


class ReadDraftReceipt(_JournalContract):
    kind: Literal["read_draft"] = "read_draft"
    workspace_id: NativeUUID
    app_id: NativeUUID
    draft_id: NativeUUID
    draft_hash: NativeHash


class PrepareCredentialReceipt(_JournalContract):
    kind: Literal["prepare_credential"] = "prepare_credential"
    app_id: NativeUUID
    credential_id: NativeUUID
    plugin_unique_identifier: PluginIdentifier


class BindCredentialReceipt(_JournalContract):
    kind: Literal["bind_credential"] = "bind_credential"
    app_id: NativeUUID
    draft_id: NativeUUID
    credential_id: NativeUUID
    accepted_draft_hash: NativeHash
    draft_hash: NativeHash


class PublishReceipt(_JournalContract):
    kind: Literal["publish"] = "publish"
    app_id: NativeUUID
    workflow_id: NativeUUID
    accepted_draft_hash: NativeHash


PhaseReceipt = Annotated[
    ReadDraftReceipt | PrepareCredentialReceipt | BindCredentialReceipt | PublishReceipt, Field(discriminator="kind")
]


class PhaseOutcome(_JournalContract):
    state: Literal["succeeded", "rejected", "uncertain"]
    receipt: PhaseReceipt | None = None
    reason_code: Reason | None = None

    @model_validator(mode="after")
    def coherent_outcome(self) -> Self:
        if self.state == "succeeded":
            if self.receipt is None or self.reason_code is not None:
                raise ValueError("Success requires only a confirmed receipt")
        elif self.receipt is not None or self.reason_code is None:
            raise ValueError("Unconfirmed outcomes require only a reason code")
        return self


class ProvisioningPhaseView(_JournalContract):
    command: PhaseCommand
    state: PhaseState
    revision: Revision
    receipt: PhaseReceipt | None = None
    reason_code: Reason | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def coherent_phase(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Invalid phase chronology")
        if self.state in {"queued", "claimed"}:
            if self.receipt is not None or self.reason_code is not None:
                raise ValueError("Pending phases have no outcome")
        if self.state == "succeeded" or self.state == "rejected" or self.state == "uncertain":
            PhaseOutcome(state=self.state, receipt=self.receipt, reason_code=self.reason_code)
        if self.receipt is not None:
            if (self.receipt.kind, self.receipt.app_id) != (self.command.kind, self.command.app_id):
                raise ValueError("Receipt does not match the phase command")
            if isinstance(self.command, BindCredentialCommand) and isinstance(self.receipt, BindCredentialReceipt):
                if (self.receipt.draft_id, self.receipt.credential_id, self.receipt.accepted_draft_hash) != (
                    self.command.draft_id,
                    self.command.credential_id,
                    self.command.expected_draft_hash,
                ):
                    raise ValueError("Binding receipt does not match the frozen command")
            if isinstance(self.command, PublishCommand) and isinstance(self.receipt, PublishReceipt):
                if self.receipt.accepted_draft_hash != self.command.expected_draft_hash:
                    raise ValueError("Publication receipt does not match the frozen graph hash")
        return self


class ProvisioningView(_JournalContract):
    id: NativeUUID
    workspace_id: NativeUUID
    app_id: NativeUUID
    setup_id: Identifier
    setup_revision: Revision
    actor_id: Identifier
    device_id: Identifier
    scenario: Scenario
    source_id: Identifier
    source_revision: Identifier
    read_id: Identifier
    read_revision: Identifier
    expected_source_revision: Revision
    expected_binding_revision: Revision | None = None
    config_ref: Identifier
    config_revision: Revision
    revision: Revision
    state: ProvisioningState = "in_progress"
    phases: tuple[ProvisioningPhaseView, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def coherent_journal(self) -> Self:
        if self.updated_at < self.created_at or len(self.phases) > len(_PHASE_ORDER):
            raise ValueError("Invalid provisioning chronology or phase count")
        operation_ids: set[str] = set()
        previous_time = self.created_at
        for index, phase in enumerate(self.phases):
            command = phase.command
            if command.kind != _PHASE_ORDER[index] or command.app_id != self.app_id:
                raise ValueError("Invalid phase order or app scope")
            if command.operation_id in operation_ids:
                raise ValueError("Each phase needs its own stable operation identity")
            operation_ids.add(command.operation_id)
            if phase.created_at < previous_time or phase.updated_at > self.updated_at:
                raise ValueError("Phase chronology does not match the operation")
            previous_time = phase.updated_at
            if index < len(self.phases) - 1 and phase.state != "succeeded":
                raise ValueError("The preceding phase must have a confirmed successful receipt")
            if isinstance(phase.receipt, ReadDraftReceipt) and phase.receipt.workspace_id != self.workspace_id:
                raise ValueError("Read receipt workspace mismatch")
            if isinstance(command, PrepareCredentialCommand):
                if (command.config_ref, command.config_revision) != (self.config_ref, self.config_revision):
                    raise ValueError("Credential configuration must remain frozen")
            elif isinstance(command, BindCredentialCommand):
                read = self.phases[0].receipt
                credential = self.phases[1].receipt
                if not isinstance(read, ReadDraftReceipt) or not isinstance(credential, PrepareCredentialReceipt):
                    raise ValueError("Binding requires confirmed draft and credential identities")
                if (command.draft_id, command.expected_draft_hash, command.credential_id) != (
                    read.draft_id,
                    read.draft_hash,
                    credential.credential_id,
                ):
                    raise ValueError("Binding command does not match preceding receipts")
            elif isinstance(command, PublishCommand):
                bound = self.phases[2].receipt
                if not isinstance(bound, BindCredentialReceipt) or command.expected_draft_hash != bound.draft_hash:
                    raise ValueError("Publication must use the confirmed bound draft hash")
        expected_state: ProvisioningState = "in_progress"
        if self.phases:
            last = self.phases[-1]
            if last.state in {"rejected", "uncertain"}:
                expected_state = "rejected" if last.state == "rejected" else "uncertain"
            elif last.command.kind == "publish" and last.state == "succeeded":
                expected_state = "published_pending_enrollment"
        if self.state != expected_state:
            raise ValueError("Provisioning state does not match its phase journal")
        return self


class StoredProvisioning(_JournalContract):
    view: ProvisioningView
    request_key: RequestKey = Field(repr=False, exclude=True)
    request_hash: NativeHash = Field(repr=False, exclude=True)
    claim_nonce: NativeUUID | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def coherent_claim(self) -> Self:
        claimed = bool(self.view.phases and self.view.phases[-1].state == "claimed")
        if claimed != (self.claim_nonce is not None):
            raise ValueError("A claim nonce exists only during the active claimed phase")
        return self


def initialize_provisioning(
    setup: SetupView,
    *,
    actor_id: str,
    operation_id: str,
    config_ref: str,
    config_revision: int,
    request_key: str,
    now: datetime,
) -> StoredProvisioning:
    setup = SetupView.model_validate(setup.model_dump())
    if setup.state != "draft_ready" or setup.app_id is None:
        raise Conflict("workflow_provisioning_confirmed_setup_required")
    if now.tzinfo is None or now < setup.updated_at:
        raise Conflict("workflow_provisioning_invalid_time")
    view = ProvisioningView(
        id=operation_id,
        workspace_id=setup.workspace_id,
        app_id=setup.app_id,
        setup_id=setup.id,
        setup_revision=setup.revision,
        actor_id=actor_id,
        device_id=setup.device_id,
        scenario=setup.scenario,
        source_id=setup.source_id,
        source_revision=setup.source_revision,
        read_id=setup.read_id,
        read_revision=setup.read_revision,
        expected_source_revision=setup.expected_source_revision,
        expected_binding_revision=setup.expected_binding_revision,
        config_ref=config_ref,
        config_revision=config_revision,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    request_hash = canonical_hash(
        {
            "actor_id": actor_id,
            "setup_id": setup.id,
            "setup_revision": setup.revision,
            "config_ref": config_ref,
            "config_revision": config_revision,
        }
    )
    return StoredProvisioning(view=view, request_key=request_key, request_hash=request_hash)


def _check_transition(stored: StoredProvisioning, expected_revision: int, actor_id: str, now: datetime) -> None:
    if actor_id != stored.view.actor_id:
        raise AccessDenied("workflow_provisioning_actor_mismatch")
    if type(expected_revision) is not int or expected_revision != stored.view.revision:
        raise Conflict("workflow_provisioning_revision_conflict")
    if now.tzinfo is None or now < stored.view.updated_at:
        raise Conflict("workflow_provisioning_invalid_time")


def claim_provisioning(
    stored: StoredProvisioning,
    *,
    command: PhaseCommand,
    nonce: str,
    expected_revision: int,
    actor_id: str,
    now: datetime,
) -> StoredProvisioning:
    stored = StoredProvisioning.model_validate(stored)
    _check_transition(stored, expected_revision, actor_id, now)
    view = stored.view
    if view.state != "in_progress" or stored.claim_nonce is not None:
        raise Conflict("workflow_provisioning_not_claimable")
    phases = view.phases
    if phases and phases[-1].state == "queued":
        pending = phases[-1]
        if pending.command != command:
            raise Conflict("workflow_provisioning_command_changed")
        current = ProvisioningPhaseView.model_validate(
            pending.model_dump()
            | {
                "state": "claimed",
                "revision": pending.revision + 1,
                "updated_at": now,
            }
        )
        phases = phases[:-1] + (current,)
    else:
        current = ProvisioningPhaseView(command=command, state="claimed", revision=1, created_at=now, updated_at=now)
        phases = phases + (current,)
    updated = ProvisioningView.model_validate(
        view.model_dump()
        | {
            "phases": phases,
            "revision": view.revision + 1,
            "updated_at": now,
        }
    )
    return StoredProvisioning(
        view=updated, request_key=stored.request_key, request_hash=stored.request_hash, claim_nonce=nonce
    )


def finish_provisioning(
    stored: StoredProvisioning,
    *,
    outcome: PhaseOutcome,
    nonce: str,
    expected_revision: int,
    actor_id: str,
    now: datetime,
) -> StoredProvisioning:
    stored = StoredProvisioning.model_validate(stored)
    outcome = PhaseOutcome.model_validate(outcome)
    _check_transition(stored, expected_revision, actor_id, now)
    if (
        not stored.claim_nonce
        or not isinstance(nonce, str)
        or not nonce.isascii()
        or not hmac.compare_digest(stored.claim_nonce, nonce)
    ):
        raise Conflict("workflow_provisioning_claim_lost")
    pending = stored.view.phases[-1]
    current = ProvisioningPhaseView.model_validate(
        pending.model_dump()
        | {
            "state": outcome.state,
            "receipt": outcome.receipt,
            "reason_code": outcome.reason_code,
            "revision": pending.revision + 1,
            "updated_at": now,
        }
    )
    state: ProvisioningState = "in_progress"
    if outcome.state in {"rejected", "uncertain"}:
        state = "rejected" if outcome.state == "rejected" else "uncertain"
    elif current.command.kind == "publish":
        state = "published_pending_enrollment"
    updated = ProvisioningView.model_validate(
        stored.view.model_dump()
        | {
            "phases": stored.view.phases[:-1] + (current,),
            "state": state,
            "revision": stored.view.revision + 1,
            "updated_at": now,
        }
    )
    return StoredProvisioning(view=updated, request_key=stored.request_key, request_hash=stored.request_hash)
