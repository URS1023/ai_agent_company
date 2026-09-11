from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.workflow_credential_contracts import CredentialView
from enterprise_platform.application.workflow_enrollment_contracts import (
    EnrollmentView,
    StoredEnrollment,
    claim_enrollment,
    finish_enrollment_token,
    finish_enrollment_verification,
    initialize_enrollment,
)
from enterprise_platform.application.workflow_provisioning_contracts import (
    BindCredentialCommand,
    BindCredentialReceipt,
    PhaseOutcome,
    PrepareCredentialCommand,
    PrepareCredentialReceipt,
    PublishCommand,
    PublishReceipt,
    ReadDraftCommand,
    ReadDraftReceipt,
    StoredProvisioning,
    claim_provisioning,
    finish_provisioning,
    initialize_provisioning,
)
from enterprise_platform.application.workflow_publication_read import NativePublicationMetadata
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


ENROLLMENT = str(UUID(int=20))
SECRET_REF = str(UUID(int=21))
TOKEN = str(UUID(int=22))


def enrollment():
    return initialize_enrollment(advance(4).view, enrollment_id=ENROLLMENT, secret_ref=SECRET_REF, now=NOW)


def publication():
    return NativePublicationMetadata(
        workspace_id=WS,
        app_id=APP,
        workflow_id=WORKFLOW,
        graph_hash=BOUND_HASH,
        credential_id=CREDENTIAL,
        provider_id="enterprise/enterprise_device_assessment/enterprise_device",
        tool_name="evaluate_device",
        node_id="assessment",
    )


def credential():
    return CredentialView(
        workspace_id=WS, app_id=APP, secret_ref=SECRET_REF, revision=1, active=True, created_at=NOW, updated_at=NOW
    )


def transition_args(stored):
    return dict(nonce=NONCE, expected_revision=stored.view.revision, actor_id="actor", now=NOW)


def claimed_verify():
    stored = enrollment()
    return claim_enrollment(stored, phase="verify_publication", **transition_args(stored))


def verified():
    stored = claimed_verify()
    return finish_enrollment_verification(stored, publication=publication(), **transition_args(stored))


def claimed_token():
    stored = verified()
    return claim_enrollment(stored, phase="issue_token", **transition_args(stored))


def test_exact_enrollment_stops_at_token_stored_not_executable():
    initial = enrollment()
    assert initial.view.state == "pending_verification" and initial.view.revision == 1
    stored = claimed_token()
    result = finish_enrollment_token(
        stored, state="token_stored", native_token_id=TOKEN, credential=credential(), **transition_args(stored)
    )
    assert result.view.state == "token_stored" and result.view.revision == 5
    assert result.claim_nonce is None and result.view.native_token_id == TOKEN
    assert result.view.publication == publication() and result.view.credential == credential()
    assert "secret_ref" not in initial.model_dump() and "claim_nonce" not in initial.model_dump()
    assert SECRET_REF not in repr(initial)


@pytest.mark.parametrize("phase", ["issue_token", "invalid"])
def test_initial_phase_rejects_skip(phase):
    stored = enrollment()
    with pytest.raises((Conflict, ValidationError)):
        claim_enrollment(stored, phase=phase, **transition_args(stored))


@pytest.mark.parametrize(
    "field,value",
    [("workspace_id", APP), ("app_id", WS), ("workflow_id", APP), ("graph_hash", HASH), ("credential_id", APP)],
)
def test_verification_exact_publication_required(field, value):
    stored = claimed_verify()
    receipt = publication().model_copy(update={field: value})
    with pytest.raises((Conflict, ValidationError)):
        finish_enrollment_verification(stored, publication=receipt, **transition_args(stored))


@pytest.mark.parametrize(
    "field,value", [("workspace_id", APP), ("app_id", WS), ("secret_ref", APP), ("revision", 2), ("active", False)]
)
def test_token_stored_requires_new_active_exact_vault_reference(field, value):
    stored = claimed_token()
    receipt = credential().model_copy(update={field: value})
    with pytest.raises((Conflict, ValidationError)):
        finish_enrollment_token(
            stored, state="token_stored", native_token_id=TOKEN, credential=receipt, **transition_args(stored)
        )


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
def test_token_failure_preserves_publication_without_token(state):
    stored = claimed_token()
    result = finish_enrollment_token(stored, state=state, reason_code="native_unconfirmed", **transition_args(stored))
    assert result.view.state == state and result.view.publication == publication()
    assert result.view.credential is None and result.view.native_token_id is None
    assert result.claim_nonce is None


def test_verification_rejection_is_terminal():
    stored = claimed_verify()
    result = finish_enrollment_verification(stored, reason_code="publication_missing", **transition_args(stored))
    assert result.view.state == "rejected" and result.view.publication is None
    with pytest.raises(Conflict):
        claim_enrollment(result, phase="verify_publication", **transition_args(result))


@pytest.mark.parametrize(
    "factory,finish", [(claimed_verify, finish_enrollment_verification), (claimed_token, finish_enrollment_token)]
)
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("actor_id", "other", AccessDenied),
        ("expected_revision", True, Conflict),
        ("expected_revision", 99, Conflict),
        ("now", NOW - timedelta(seconds=1), Conflict),
        ("now", NOW.replace(tzinfo=None), Conflict),
        ("nonce", "other", Conflict),
        ("nonce", "非ASCII", Conflict),
        ("nonce", None, Conflict),
    ],
)
def test_finish_actor_revision_time_and_nonce_guards(factory, finish, field, value, error):
    stored = factory()
    args = transition_args(stored) | {field: value, "reason_code": "failure"}
    if finish is finish_enrollment_token:
        args["state"] = "uncertain"
    with pytest.raises(error):
        finish(stored, **args)


@pytest.mark.parametrize("factory", [claimed_verify, claimed_token])
def test_claim_never_reclaimed_even_after_long_delay(factory):
    stored = factory()
    for phase in ["verify_publication", "issue_token"]:
        with pytest.raises(Conflict):
            claim_enrollment(stored, phase=phase, **(transition_args(stored) | {"now": NOW + timedelta(days=365)}))


@pytest.mark.parametrize("factory", [enrollment, verified])
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("actor_id", "other", AccessDenied),
        ("expected_revision", True, Conflict),
        ("expected_revision", 99, Conflict),
        ("now", NOW - timedelta(seconds=1), Conflict),
        ("now", NOW.replace(tzinfo=None), Conflict),
        ("nonce", "invalid", ValidationError),
    ],
)
def test_claim_guards(factory, field, value, error):
    stored = factory()
    phase = "verify_publication" if stored.view.state == "pending_verification" else "issue_token"
    with pytest.raises(error):
        claim_enrollment(stored, phase=phase, **(transition_args(stored) | {field: value}))


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_initialization_requires_complete_provisioning(index):
    with pytest.raises(Conflict):
        initialize_enrollment(advance(index).view, enrollment_id=ENROLLMENT, secret_ref=SECRET_REF, now=NOW)


def test_unvalidated_nested_publication_rejected():
    stored = claimed_verify()
    for field in ["provider_id", "tool_name", "node_id"]:
        with pytest.raises(ValidationError):
            finish_enrollment_verification(
                stored, publication=publication().model_copy(update={field: "wrong"}), **transition_args(stored)
            )


@pytest.mark.parametrize(
    "changes",
    [
        dict(publication=None),
        dict(reason_code="unexpected"),
        dict(native_token_id=TOKEN),
        dict(credential=credential()),
        dict(updated_at=NOW - timedelta(seconds=1)),
    ],
)
def test_public_view_revalidates_coherence(changes):
    view = verified().view.model_copy(update=changes)
    with pytest.raises(ValidationError):
        EnrollmentView.model_validate(view)


def test_private_claim_fields_frozen_and_not_public():
    stored = claimed_verify()
    assert NONCE not in repr(stored) and SECRET_REF not in repr(stored)
    assert set(stored.model_dump()) == {"view"}
    assert "claim_nonce" not in stored.model_dump_json()
    with pytest.raises(ValidationError):
        stored.secret_ref = APP
    with pytest.raises(ValidationError):
        StoredEnrollment.model_validate(stored.model_copy(update={"claim_nonce": None}))


@pytest.mark.parametrize("kwargs", [{}, {"publication": publication(), "reason_code": "failure"}])
def test_verification_requires_one_outcome(kwargs):
    stored = claimed_verify()
    with pytest.raises(Conflict):
        finish_enrollment_verification(stored, **kwargs, **transition_args(stored))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(state="token_stored"),
        dict(state="uncertain"),
        dict(state="rejected", reason_code="failure", native_token_id=TOKEN),
        dict(state="token_stored", native_token_id=TOKEN, credential=credential(), reason_code="failure"),
        dict(state="token_stored", native_token_id="invalid", credential=credential()),
        dict(state="token_stored", native_token_id=TOKEN, credential=credential().model_copy(update={"active": 1})),
        dict(state="rejected", reason_code="PRIVATE secret"),
        dict(state="verified"),
    ],
)
def test_token_outcome_coherence(kwargs):
    stored = claimed_token()
    with pytest.raises((ValidationError, Conflict)):
        finish_enrollment_token(stored, **kwargs, **transition_args(stored))


def test_successful_finish_is_not_replayed():
    stored = claimed_token()
    result = finish_enrollment_token(
        stored, state="token_stored", native_token_id=TOKEN, credential=credential(), **transition_args(stored)
    )
    with pytest.raises(Conflict):
        finish_enrollment_token(
            result, state="token_stored", native_token_id=TOKEN, credential=credential(), **transition_args(result)
        )
