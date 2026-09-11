"""Executor-only checks; no journal ownership or vault persistence is simulated."""

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError
from test_workflow_provisioning_contracts import APP, BOUND_HASH, CREDENTIAL, NOW, WORKFLOW, WS, advance

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, InvalidInput
from enterprise_platform.application.workflow_enrollment_contracts import EnrollmentView
from enterprise_platform.application.workflow_enrollment_execution import (
    EnrollmentExecutionResult,
    WorkflowEnrollmentExecutor,
)
from enterprise_platform.application.workflow_publication_read import NativePublicationMetadata, PublicationReadRejected
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession
from enterprise_platform.application.workflow_token_execution import NativeWorkflowTokenOutcome, TokenIssueRejected

ENROLLMENT = str(UUID(int=101))
TOKEN_ID = str(UUID(int=102))
TOKEN = "app-" + "a" * 24
PRINCIPAL = Principal(actor_id="actor", workspace_id=WS, workspace_role="admin", display_name="Admin")
SESSION = NativeSetupSession(None, "Bearer fixture", "csrf-fixture")


def metadata(**changes):
    return NativePublicationMetadata(
        **(
            {
                "workspace_id": WS,
                "app_id": APP,
                "workflow_id": WORKFLOW,
                "graph_hash": BOUND_HASH,
                "provider_id": "enterprise/enterprise_device_assessment/enterprise_device",
                "tool_name": "evaluate_device",
                "credential_id": CREDENTIAL,
                "node_id": "assessment",
            }
            | changes
        )
    )


def view(state="verification_claimed"):
    return EnrollmentView(
        id=ENROLLMENT,
        provisioning=advance(4).view,
        revision=2 if state == "verification_claimed" else 4,
        state=state,
        publication=metadata() if state == "token_claimed" else None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def ports():
    reader, issuer = AsyncMock(), AsyncMock()
    reader.read.return_value = metadata()
    issuer.issue.return_value = NativeWorkflowTokenOutcome(
        state="issued", workspace_id=WS, app_id=APP, token_id=TOKEN_ID, token=SecretStr(TOKEN)
    )
    return WorkflowEnrollmentExecutor(reader=reader, token_issuer=issuer), reader, issuer


def test_verifies_exact_frozen_publication_once(ports):
    executor, reader, issuer = ports
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, view()))
    assert result.state == "verified" and result.publication == metadata()
    reader.read.assert_awaited_once_with(
        PRINCIPAL, SESSION, app_id=APP, workflow_id=WORKFLOW, expected_graph_hash=BOUND_HASH, credential_id=CREDENTIAL
    )
    issuer.issue.assert_not_awaited()


def test_issues_once_using_stable_enrollment_id_without_serializing_secret(ports):
    executor, reader, issuer = ports
    result = asyncio.run(executor.execute(PRINCIPAL, SESSION, view("token_claimed")))
    assert result.state == "issued" and result.issued.token.get_secret_value() == TOKEN
    assert TOKEN not in repr(result) and TOKEN not in result.model_dump_json()
    assert "token" not in result.model_dump()["issued"]
    issuer.issue.assert_awaited_once_with(PRINCIPAL, SESSION, app_id=APP, operation_id=ENROLLMENT)
    reader.read.assert_not_awaited()


@pytest.mark.parametrize("field", ["workspace_id", "actor_id"])
def test_wrong_principal_scope_fails_before_io(ports, field):
    principal = PRINCIPAL.model_copy(update={field: "other"})
    with pytest.raises(AccessDenied):
        asyncio.run(ports[0].execute(principal, SESSION, view()))
    ports[1].read.assert_not_awaited()
    ports[2].issue.assert_not_awaited()


def test_nonclaimed_state_rejected_before_io(ports):
    candidate = view().model_copy(update={"state": "pending_verification", "revision": 1})
    with pytest.raises(InvalidInput):
        asyncio.run(ports[0].execute(PRINCIPAL, SESSION, candidate))
    ports[1].read.assert_not_awaited()
    ports[2].issue.assert_not_awaited()


def test_revalidates_nested_constructed_snapshot_before_io(ports):
    candidate = view()
    bad = candidate.provisioning.model_copy(update={"state": "in_progress"})
    candidate = candidate.model_copy(update={"provisioning": bad})
    with pytest.raises(InvalidInput):
        asyncio.run(ports[0].execute(PRINCIPAL, SESSION, candidate))
    ports[1].read.assert_not_awaited()
    ports[2].issue.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", APP),
        ("app_id", WORKFLOW),
        ("workflow_id", APP),
        ("graph_hash", "c" * 64),
        ("credential_id", APP),
        ("provider_id", "other"),
        ("tool_name", "other"),
        ("node_id", "other"),
    ],
)
def test_read_mismatch_or_constructed_metadata_rejected(ports, field, value):
    ports[1].read.return_value = metadata().model_copy(update={field: value})
    result = asyncio.run(ports[0].execute(PRINCIPAL, SESSION, view()))
    assert result.state == "rejected" and result.publication is None
    ports[1].read.assert_awaited_once()
    ports[2].issue.assert_not_awaited()


@pytest.mark.parametrize("error", [PublicationReadRejected(), RuntimeError("private-read-detail")])
def test_read_errors_are_bounded_rejections(ports, error):
    ports[1].read.side_effect = error
    result = asyncio.run(ports[0].execute(PRINCIPAL, SESSION, view()))
    assert result.state == "rejected" and "private" not in result.model_dump_json()


@pytest.mark.parametrize("error,state", [(TokenIssueRejected(), "rejected"), (RuntimeError(TOKEN), "uncertain")])
def test_write_errors_never_retry_and_hide_raw_errors(ports, error, state):
    ports[2].issue.side_effect = error
    result = asyncio.run(ports[0].execute(PRINCIPAL, SESSION, view("token_claimed")))
    assert result.state == state and result.issued is None
    assert TOKEN not in result.model_dump_json()
    ports[2].issue.assert_awaited_once()


def test_unconfirmed_issued_result_is_uncertain(ports):
    ports[2].issue.return_value = NativeWorkflowTokenOutcome(state="uncertain", reason_code="unconfirmed")
    assert asyncio.run(ports[0].execute(PRINCIPAL, SESSION, view("token_claimed"))).state == "uncertain"


@pytest.mark.parametrize(
    "field,value", [("workspace_id", APP), ("app_id", WORKFLOW), ("token_id", "bad"), ("token", SecretStr("invalid"))]
)
def test_bad_issued_scope_or_secret_is_uncertain(ports, field, value):
    ports[2].issue.return_value = ports[2].issue.return_value.model_copy(update={field: value})
    result = asyncio.run(ports[0].execute(PRINCIPAL, SESSION, view("token_claimed")))
    assert result.state == "uncertain" and result.issued is None


@pytest.mark.parametrize("state", ["verification_claimed", "token_claimed"])
def test_cancellation_propagates_without_retry(ports, state):
    port = ports[1].read if state == "verification_claimed" else ports[2].issue
    port.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ports[0].execute(PRINCIPAL, SESSION, view(state)))
    port.assert_awaited_once()


def test_result_is_frozen_and_rejects_incoherent_or_extra_fields():
    result = EnrollmentExecutionResult(state="rejected", reason_code="preflight_rejected")
    with pytest.raises(ValidationError):
        result.state = "verified"
    with pytest.raises(ValidationError):
        EnrollmentExecutionResult(state="verified")
    with pytest.raises(ValidationError):
        EnrollmentExecutionResult(state="uncertain", reason_code="bad reason")
    with pytest.raises(ValidationError):
        EnrollmentExecutionResult(state="rejected", reason_code="rejected", token=TOKEN)


@pytest.mark.parametrize("state", ["pending_verification", "verified", "token_stored", "rejected", "uncertain"])
def test_all_nonclaimed_states_stop_before_native_calls(ports, state):
    candidate = view().model_copy(update={"state": state})
    with pytest.raises(InvalidInput):
        asyncio.run(ports[0].execute(PRINCIPAL, SESSION, candidate))
    ports[1].read.assert_not_awaited()
    ports[2].issue.assert_not_awaited()


def test_revalidates_nested_publication_before_token_call(ports):
    candidate = view("token_claimed")
    candidate = candidate.model_copy(update={"publication": metadata().model_copy(update={"credential_id": APP})})
    with pytest.raises(InvalidInput):
        asyncio.run(ports[0].execute(PRINCIPAL, SESSION, candidate))
    ports[1].read.assert_not_awaited()
    ports[2].issue.assert_not_awaited()


def test_execution_result_revalidates_constructed_metadata():
    with pytest.raises(ValidationError):
        EnrollmentExecutionResult(state="verified", publication=metadata().model_copy(update={"node_id": "other"}))
