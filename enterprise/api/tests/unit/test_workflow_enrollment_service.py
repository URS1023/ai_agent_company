import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from test_workflow_enrollment_contracts import (
    APP,
    NOW,
    TOKEN,
    WS,
    claimed_verify,
    credential,
    enrollment,
    publication,
    verified,
)

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, PersistenceError
from enterprise_platform.application.workflow_enrollment_contracts import (
    claim_enrollment,
    finish_enrollment_token,
    finish_enrollment_verification,
)
from enterprise_platform.application.workflow_enrollment_execution import EnrollmentExecutionResult
from enterprise_platform.application.workflow_enrollment_service import WorkflowEnrollmentService
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession
from enterprise_platform.application.workflow_token_execution import NativeWorkflowTokenOutcome

PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession(None, "Bearer test", None)


def harness():
    repo, business = MagicMock(), MagicMock()
    provisioning, executor = AsyncMock(), AsyncMock()
    repo.get.return_value = enrollment()
    repo.find_provisioning.return_value = None
    repo.create.side_effect = lambda value, **kwargs: value
    provisioning.get.return_value = enrollment().view.provisioning
    business.get_device.return_value = MagicMock(workspace_id=WS, id="device", deleted_at=None)
    events = []

    def claim(ws, identity, **kwargs):
        events.append("claim")
        result = claim_enrollment(repo.get.return_value, now=NOW, **kwargs)
        repo.get.return_value = result
        return result

    async def execute(*args):
        assert events == ["claim"]
        events.append("native")
        return EnrollmentExecutionResult(state="verified", publication=publication())

    def finish(ws, identity, **kwargs):
        events.append("finish")
        return finish_enrollment_verification(repo.get.return_value, now=NOW, **kwargs)

    repo.claim.side_effect = claim
    repo.finish_verification.side_effect = finish
    executor.execute.side_effect = execute
    return WorkflowEnrollmentService(repo, business, provisioning, executor, clock=lambda: NOW), repo, executor, events


def test_start_freezes_complete_provisioning_without_native_io():
    service, repo, executor, _ = harness()
    result = asyncio.run(service.start(PRINCIPAL, enrollment().view.provisioning.id))
    assert result.state == "pending_verification"
    assert result.provisioning == enrollment().view.provisioning
    executor.execute.assert_not_called()
    repo.create.assert_called_once()


def test_find_by_provisioning_is_read_only_and_handles_absence():
    service, repo, executor, _ = harness()
    assert asyncio.run(service.find(PRINCIPAL, enrollment().view.provisioning.id)) is None
    repo.find_provisioning.return_value = enrollment()
    assert asyncio.run(service.find(PRINCIPAL, enrollment().view.provisioning.id)) == enrollment().view
    repo.create.assert_not_called()
    executor.execute.assert_not_called()


def test_find_rejects_mismatched_provisioning_snapshot():
    service, repo, _, _ = harness()
    wrong = enrollment().view.provisioning.model_copy(update={"device_id": "other"})
    repo.find_provisioning.return_value = enrollment().model_copy(
        update={"view": enrollment().view.model_copy(update={"provisioning": wrong})}
    )
    with pytest.raises(Conflict):
        asyncio.run(service.find(PRINCIPAL, enrollment().view.provisioning.id))


def test_verified_phase_commits_claim_before_native():
    service, _, _, events = harness()
    result = asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=1))
    assert result.state == "verified"
    assert events == ["claim", "native", "finish"]


def test_existing_claim_never_reexecutes():
    service, repo, executor, _ = harness()
    repo.get.return_value = claimed_verify()
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=2))
    executor.execute.assert_not_called()


def test_wrong_actor_has_no_native_call():
    service, _, executor, _ = harness()
    with pytest.raises(AccessDenied):
        asyncio.run(
            service.advance(
                PRINCIPAL.model_copy(update={"actor_id": "other"}), SESSION, enrollment().view.id, expected_revision=1
            )
        )
    executor.execute.assert_not_called()


def test_unconfirmed_claim_blocks_native():
    service, repo, executor, _ = harness()
    repo.claim.side_effect = lambda *args, **kwargs: enrollment()
    with pytest.raises(PersistenceError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=1))
    executor.execute.assert_not_called()


def test_cancellation_preserves_claim_without_finalization():
    service, repo, executor, _ = harness()
    executor.execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=1))
    repo.finish_verification.assert_not_called()
    assert repo.get.return_value.view.state == "verification_claimed"


def test_token_success_uses_atomic_store_and_returns_no_secret():
    service, repo, executor, events = harness()
    repo.get.return_value = verified()
    token = NativeWorkflowTokenOutcome(
        state="issued", workspace_id=WS, app_id=APP, token_id=TOKEN, token=SecretStr("app-" + "a" * 24)
    )
    executor.execute.side_effect = None
    executor.execute.return_value = EnrollmentExecutionResult(state="issued", issued=token)

    def store(ws, identity, *, issued, **kwargs):
        assert events == ["claim"]
        assert issued == token
        return finish_enrollment_token(
            repo.get.return_value,
            state="token_stored",
            native_token_id=TOKEN,
            credential=credential(),
            now=NOW,
            **kwargs,
        )

    repo.store_issued_token.side_effect = store
    result = asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=3))
    assert result.state == "token_stored"
    assert token.token.get_secret_value() not in result.model_dump_json()
    repo.finish_token_failure.assert_not_called()


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
def test_token_failure_records_no_vault_write(state):
    service, repo, executor, _ = harness()
    repo.get.return_value = verified()
    executor.execute.side_effect = None
    executor.execute.return_value = EnrollmentExecutionResult(state=state, reason_code="native_unconfirmed")
    repo.finish_token_failure.side_effect = lambda ws, identity, **kwargs: finish_enrollment_token(
        repo.get.return_value, now=NOW, **kwargs
    )
    result = asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=3))
    assert result.state == state
    repo.store_issued_token.assert_not_called()


def test_store_failure_retains_claim_and_has_no_retry():
    service, repo, executor, _ = harness()
    repo.get.return_value = verified()
    executor.execute.side_effect = None
    executor.execute.return_value = EnrollmentExecutionResult(
        state="issued",
        issued=NativeWorkflowTokenOutcome(
            state="issued", workspace_id=WS, app_id=APP, token_id=TOKEN, token=SecretStr("app-" + "a" * 24)
        ),
    )
    repo.store_issued_token.side_effect = PersistenceError("write_failed")
    with pytest.raises(PersistenceError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=3))
    assert repo.get.return_value.view.state == "token_claimed"
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, enrollment().view.id, expected_revision=4))
    assert executor.execute.call_count == 1
