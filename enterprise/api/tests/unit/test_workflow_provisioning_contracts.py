from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.workflow_provisioning_contracts import (
    BindCredentialCommand,
    BindCredentialReceipt,
    PhaseOutcome,
    PrepareCredentialCommand,
    PrepareCredentialReceipt,
    ProvisioningView,
    PublishCommand,
    PublishReceipt,
    ReadDraftCommand,
    ReadDraftReceipt,
    StoredProvisioning,
    claim_provisioning,
    finish_provisioning,
    initialize_provisioning,
)
from enterprise_platform.application.workflow_setup_contracts import SetupView

NOW = datetime(2026, 9, 10, tzinfo=UTC)
WS = str(UUID(int=1))
APP = str(UUID(int=2))
DRAFT = str(UUID(int=3))
CREDENTIAL = str(UUID(int=4))
WORKFLOW = str(UUID(int=5))
OPERATION = str(UUID(int=6))
NONCE = str(UUID(int=7))
HASH = "a" * 64
BOUND_HASH = "b" * 64


def setup() -> SetupView:
    return SetupView(
        id="setup",
        workspace_id=WS,
        device_id="device",
        scenario="alert",
        source_id="source",
        source_revision="source-v1",
        read_id="read",
        read_revision="read-v1",
        expected_source_revision=2,
        expected_binding_revision=None,
        revision=3,
        state="draft_ready",
        name="Draft",
        app_id=APP,
        import_id="import",
        created_at=NOW,
        updated_at=NOW,
    )


def initial() -> StoredProvisioning:
    return initialize_provisioning(
        setup(),
        actor_id="actor",
        operation_id=OPERATION,
        config_ref="plugin-config",
        config_revision=1,
        request_key="request-1",
        now=NOW,
    )


def commands():
    return [
        ReadDraftCommand(kind="read_draft", operation_id=str(UUID(int=11)), app_id=APP),
        PrepareCredentialCommand(
            kind="prepare_credential",
            operation_id=str(UUID(int=12)),
            app_id=APP,
            config_ref="plugin-config",
            config_revision=1,
        ),
        BindCredentialCommand(
            kind="bind_credential",
            operation_id=str(UUID(int=13)),
            app_id=APP,
            draft_id=DRAFT,
            credential_id=CREDENTIAL,
            expected_draft_hash=HASH,
        ),
        PublishCommand(kind="publish", operation_id=str(UUID(int=14)), app_id=APP, expected_draft_hash=BOUND_HASH),
    ]


def receipts():
    return [
        ReadDraftReceipt(kind="read_draft", workspace_id=WS, app_id=APP, draft_id=DRAFT, draft_hash=HASH),
        PrepareCredentialReceipt(
            kind="prepare_credential",
            app_id=APP,
            credential_id=CREDENTIAL,
            plugin_unique_identifier="enterprise/plugin:1@digest",
        ),
        BindCredentialReceipt(
            kind="bind_credential",
            app_id=APP,
            draft_id=DRAFT,
            credential_id=CREDENTIAL,
            accepted_draft_hash=HASH,
            draft_hash=BOUND_HASH,
        ),
        PublishReceipt(kind="publish", app_id=APP, workflow_id=WORKFLOW, accepted_draft_hash=BOUND_HASH),
    ]


def advance(index: int) -> StoredProvisioning:
    value = initial()
    for command, receipt in zip(commands()[:index], receipts()[:index], strict=True):
        value = claim_provisioning(
            value, command=command, nonce=NONCE, expected_revision=value.view.revision, actor_id="actor", now=NOW
        )
        value = finish_provisioning(
            value,
            nonce=NONCE,
            expected_revision=value.view.revision,
            actor_id="actor",
            outcome=PhaseOutcome(state="succeeded", receipt=receipt),
            now=NOW,
        )
    return value


def test_four_ordered_phases_end_published_pending_enrollment_not_ready():
    value = advance(4)
    assert value.view.state == "published_pending_enrollment"
    assert [phase.command.kind for phase in value.view.phases] == [
        "read_draft",
        "prepare_credential",
        "bind_credential",
        "publish",
    ]
    assert all(phase.state == "succeeded" for phase in value.view.phases)
    assert value.view.revision == 9 and value.claim_nonce is None
    assert value.view.source_revision == "source-v1" and value.view.read_revision == "read-v1"
    assert value.view.expected_binding_revision is None


def test_initialize_only_from_confirmed_draft_and_preserve_s5_snapshot():
    value = initial()
    assert value.view.setup_id == "setup" and value.view.setup_revision == 3
    assert value.view.app_id == APP and value.view.actor_id == "actor"
    assert setup().state == "draft_ready"
    with pytest.raises(Conflict):
        initialize_provisioning(
            setup().model_copy(update={"state": "uncertain"}),
            actor_id="actor",
            operation_id=OPERATION,
            config_ref="plugin-config",
            config_revision=1,
            request_key="key",
            now=NOW,
        )


def test_claim_nonce_is_internal_and_only_present_while_claimed():
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    assert value.claim_nonce == NONCE and value.view.phases[-1].state == "claimed"
    assert "claim_nonce" not in value.model_dump() and NONCE not in repr(value)
    assert "request_hash" not in value.model_dump() and "request_key" not in value.model_dump()
    with pytest.raises(ValidationError):
        StoredProvisioning(view=value.view, request_key=value.request_key, request_hash=value.request_hash)
    with pytest.raises(ValidationError):
        StoredProvisioning(view=initial().view, request_key="key", request_hash=HASH, claim_nonce=NONCE)


@pytest.mark.parametrize("phase", [1, 2, 3])
def test_rejects_skipping_phase_prerequisites(phase):
    with pytest.raises((Conflict, ValidationError)):
        claim_provisioning(
            initial(), command=commands()[phase], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
        )


@pytest.mark.parametrize("change", [{"app_id": WS}, {"operation_id": "bad"}, {"operation_id": str(UUID(int=0))}])
def test_command_invalid_identity_cannot_bypass_validation_with_model_copy(change):
    with pytest.raises(ValidationError):
        claim_provisioning(
            initial(),
            command=commands()[0].model_copy(update=change),
            nonce=NONCE,
            expected_revision=1,
            actor_id="actor",
            now=NOW,
        )


@pytest.mark.parametrize(
    "phase,change",
    [
        (1, {"config_ref": "other"}),
        (1, {"config_revision": 2}),
        (2, {"draft_id": APP}),
        (2, {"credential_id": APP}),
        (2, {"expected_draft_hash": BOUND_HASH}),
        (3, {"expected_draft_hash": HASH}),
        (1, {"operation_id": str(UUID(int=11))}),
    ],
)
def test_frozen_phase_inputs_must_match_prior_receipts_and_configuration(phase, change):
    value = advance(phase)
    with pytest.raises(ValidationError):
        claim_provisioning(
            value,
            command=commands()[phase].model_copy(update=change),
            nonce=NONCE,
            expected_revision=value.view.revision,
            actor_id="actor",
            now=NOW,
        )


@pytest.mark.parametrize(
    "phase,change",
    [
        (0, {"workspace_id": APP}),
        (0, {"app_id": WS}),
        (0, {"draft_id": str(UUID(int=0))}),
        (1, {"app_id": WS}),
        (2, {"draft_id": APP}),
        (2, {"credential_id": APP}),
        (2, {"accepted_draft_hash": BOUND_HASH}),
        (3, {"accepted_draft_hash": HASH}),
        (3, {"workflow_id": "draft"}),
    ],
)
def test_rejects_mismatched_receipts(phase, change):
    value = advance(phase)
    value = claim_provisioning(
        value, command=commands()[phase], nonce=NONCE, expected_revision=value.view.revision, actor_id="actor", now=NOW
    )
    with pytest.raises(ValidationError):
        finish_provisioning(
            value,
            outcome=PhaseOutcome(state="succeeded", receipt=receipts()[phase].model_copy(update=change)),
            nonce=NONCE,
            expected_revision=value.view.revision,
            actor_id="actor",
            now=NOW,
        )


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
def test_unresolved_or_rejected_phase_never_auto_reclaims(state):
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    value = finish_provisioning(
        value,
        outcome=PhaseOutcome(state=state, reason_code="native_unavailable"),
        nonce=NONCE,
        expected_revision=value.view.revision,
        actor_id="actor",
        now=NOW,
    )
    assert value.view.state == state and value.claim_nonce is None
    with pytest.raises(Conflict):
        claim_provisioning(
            value,
            command=commands()[0],
            nonce=NONCE,
            expected_revision=value.view.revision,
            actor_id="actor",
            now=NOW + timedelta(days=30),
        )


@pytest.mark.parametrize("kwargs", [{"expected_revision": 99}, {"actor_id": "other"}, {"nonce": str(UUID(int=8))}])
def test_finish_is_fenced_by_revision_actor_and_nonce(kwargs):
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    arguments = dict(nonce=NONCE, expected_revision=value.view.revision, actor_id="actor", now=NOW) | kwargs
    with pytest.raises((Conflict, AccessDenied)):
        finish_provisioning(value, outcome=PhaseOutcome(state="succeeded", receipt=receipts()[0]), **arguments)


@pytest.mark.parametrize(
    "body",
    [
        {"state": "succeeded"},
        {"state": "succeeded", "receipt": receipts()[0], "reason_code": "error"},
        {"state": "uncertain"},
        {"state": "rejected", "receipt": receipts()[0], "reason_code": "error"},
        {"state": "uncertain", "reason_code": "secret text"},
    ],
)
def test_outcomes_are_coherent_and_reason_is_bounded_code(body):
    with pytest.raises(ValidationError):
        PhaseOutcome.model_validate(body)


@pytest.mark.parametrize("secret", ["token", "signing_secret", "cookie_header", "authorization", "credentials"])
def test_operation_forbids_secret_or_session_fields(secret):
    with pytest.raises(ValidationError):
        ProvisioningView.model_validate(initial().view.model_dump() | {secret: "private"})


def test_chronology_and_frozen_models():
    value = initial()
    with pytest.raises((Conflict, ValidationError)):
        claim_provisioning(
            value,
            command=commands()[0],
            nonce=NONCE,
            expected_revision=1,
            actor_id="actor",
            now=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError):
        value.view.actor_id = "other"
    with pytest.raises(ValidationError):
        ProvisioningView.model_validate(value.view.model_dump() | {"updated_at": NOW.replace(tzinfo=None)})


def test_claimed_phase_never_repeats_even_after_time_passes():
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    with pytest.raises(Conflict):
        claim_provisioning(
            value,
            command=commands()[0],
            nonce=NONCE,
            expected_revision=value.view.revision,
            actor_id="actor",
            now=NOW + timedelta(days=1),
        )


@pytest.mark.parametrize("nonce", ["not-ascii-密钥", "", "bad"])
def test_invalid_finish_nonce_is_a_fenced_conflict(nonce):
    value = claim_provisioning(
        initial(), command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    with pytest.raises(Conflict):
        finish_provisioning(
            value,
            outcome=PhaseOutcome(state="succeeded", receipt=receipts()[0]),
            nonce=nonce,
            expected_revision=value.view.revision,
            actor_id="actor",
            now=NOW,
        )


def test_public_journal_json_round_trip_keeps_exact_receipts():
    value = advance(4)
    assert ProvisioningView.model_validate_json(value.view.model_dump_json()) == value.view
    assert "claim_nonce" not in value.view.model_dump_json()


def test_queued_command_is_frozen_and_claimable_once():
    from enterprise_platform.application.workflow_provisioning_contracts import ProvisioningPhaseView

    value = initial()
    queued = ProvisioningPhaseView(command=commands()[0], state="queued", revision=1, created_at=NOW, updated_at=NOW)
    view = ProvisioningView.model_validate(value.view.model_dump() | {"phases": (queued,)})
    value = StoredProvisioning(view=view, request_key=value.request_key, request_hash=value.request_hash)
    with pytest.raises(Conflict):
        claim_provisioning(
            value,
            command=commands()[0].model_copy(update={"operation_id": str(UUID(int=99))}),
            nonce=NONCE,
            expected_revision=1,
            actor_id="actor",
            now=NOW,
        )
    claimed = claim_provisioning(
        value, command=commands()[0], nonce=NONCE, expected_revision=1, actor_id="actor", now=NOW
    )
    assert len(claimed.view.phases) == 1 and claimed.view.phases[0].revision == 2


def test_duplicate_finish_and_claim_after_publish_are_rejected():
    value = advance(4)
    with pytest.raises(Conflict):
        finish_provisioning(
            value,
            outcome=PhaseOutcome(state="succeeded", receipt=receipts()[3]),
            nonce=NONCE,
            expected_revision=value.view.revision,
            actor_id="actor",
            now=NOW,
        )
    with pytest.raises(Conflict):
        claim_provisioning(
            value, command=commands()[3], nonce=NONCE, expected_revision=value.view.revision, actor_id="actor", now=NOW
        )
