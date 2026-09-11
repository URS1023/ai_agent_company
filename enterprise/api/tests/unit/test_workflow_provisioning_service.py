import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, NotFound, PersistenceError
from enterprise_platform.application.workflow_provisioning_contracts import (
    PhaseOutcome,
    ReadDraftReceipt,
    claim_provisioning,
    finish_provisioning,
    initialize_provisioning,
)
from enterprise_platform.application.workflow_provisioning_service import WorkflowProvisioningService
from enterprise_platform.application.workflow_setup_contracts import SetupView
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

WS = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
OP = "33333333-3333-4333-8333-333333333333"
DRAFT = "44444444-4444-4444-8444-444444444444"
NOW = datetime(2026, 9, 10, tzinfo=UTC)
PRINCIPAL = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
SESSION = NativeSetupSession(None, "Bearer private", None)


@pytest.fixture
def harness():
    setup = SetupView(
        id="setup",
        workspace_id=WS,
        device_id="device",
        scenario="alert",
        source_id="source",
        source_revision="s1",
        read_id="read",
        read_revision="r1",
        expected_source_revision=1,
        revision=3,
        state="draft_ready",
        name="Device alert",
        app_id=APP,
        import_id="import",
        created_at=NOW,
        updated_at=NOW,
    )
    stored = initialize_provisioning(
        setup, actor_id="actor", operation_id=OP, config_ref="config", config_revision=1, request_key="request", now=NOW
    )
    repository, business = MagicMock(), MagicMock()
    setups, executor = AsyncMock(), AsyncMock()
    business.get_device.return_value = MagicMock(workspace_id=WS, id="device", deleted_at=None)
    setups.get.return_value = setup
    repository.get.return_value = stored
    repository.find_request.return_value = None
    repository.create.side_effect = lambda value, **kwargs: value
    events = []

    def claim(workspace, operation, **kwargs):
        events.append("claim_committed")
        result = claim_provisioning(repository.get.return_value, now=NOW, **kwargs)
        repository.get.return_value = result
        return result

    def finish(workspace, operation, **kwargs):
        events.append("finish_committed")
        result = finish_provisioning(repository.get.return_value, now=NOW, **kwargs)
        repository.get.return_value = result
        return result

    async def execute(principal, session, command):
        assert events == ["claim_committed"]
        events.append("native")
        return PhaseOutcome(
            state="succeeded",
            receipt=ReadDraftReceipt(workspace_id=WS, app_id=APP, draft_id=DRAFT, draft_hash="a" * 64),
        )

    repository.claim.side_effect = claim
    repository.finish.side_effect = finish
    executor.execute.side_effect = execute
    service = WorkflowProvisioningService(repository, business, setups, executor, clock=lambda: NOW)
    return service, repository, business, setups, executor, stored, events


def test_claim_is_committed_before_execution_and_finished_once(harness):
    service, repository, business, setups, executor, stored, events = harness
    result = asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    assert events == ["claim_committed", "native", "finish_committed"]
    assert result.phases[-1].state == "succeeded"
    assert result.revision == 3
    assert "private" not in result.model_dump_json()


def test_claim_failure_prevents_http(harness):
    service, repository, business, setups, executor, stored, events = harness
    repository.claim.side_effect = Conflict("concurrent_claim")
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    executor.execute.assert_not_awaited()
    repository.finish.assert_not_called()


def test_cancellation_preserves_claim_and_blocks_replay(harness):
    service, repository, business, setups, executor, stored, events = harness
    executor.execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    repository.finish.assert_not_called()
    assert repository.get.return_value.claim_nonce is not None
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=2))
    assert executor.execute.await_count == 1


def test_start_freezes_setup_without_native_side_effect(harness):
    service, repository, business, setups, executor, stored, events = harness
    result = asyncio.run(
        service.start(PRINCIPAL, "setup", config_ref="config", config_revision=1, request_key="request")
    )
    assert result.setup_id == "setup"
    assert result.source_revision == "s1"
    assert result.phases == ()
    executor.execute.assert_not_awaited()


def test_stale_revision_prevents_claim_and_io(harness):
    service, repository, business, setups, executor, stored, events = harness
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=2))
    repository.claim.assert_not_called()
    executor.execute.assert_not_awaited()


def test_all_four_phases_use_prior_receipts_and_stop_before_enrollment(harness):
    service, repository, business, setups, executor, stored, events = harness
    credential = "55555555-5555-4555-8555-555555555555"
    published = "66666666-6666-4666-8666-666666666666"

    async def execute(principal, session, command):
        assert events[-1] == "claim_committed"
        events.append(command.kind)
        receipt = dict(kind=command.kind, app_id=APP)
        if command.kind == "read_draft":
            receipt.update(workspace_id=WS, draft_id=DRAFT, draft_hash="a" * 64)
        elif command.kind == "prepare_credential":
            assert (command.config_ref, command.config_revision) == ("config", 1)
            receipt.update(credential_id=credential, plugin_unique_identifier="plugin@version")
        elif command.kind == "bind_credential":
            assert (command.draft_id, command.credential_id, command.expected_draft_hash) == (
                DRAFT,
                credential,
                "a" * 64,
            )
            receipt.update(draft_id=DRAFT, credential_id=credential, accepted_draft_hash="a" * 64, draft_hash="b" * 64)
        else:
            assert command.expected_draft_hash == "b" * 64
            receipt.update(workflow_id=published, accepted_draft_hash="b" * 64)
        return PhaseOutcome.model_validate(dict(state="succeeded", receipt=receipt))

    executor.execute.side_effect = execute
    for revision in (1, 3, 5, 7):
        result = asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=revision))
    assert result.state == "published_pending_enrollment"
    assert result.phases[-1].receipt.workflow_id == published
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=9))
    assert executor.execute.await_count == 4


def test_lost_final_write_retains_claim_and_never_reexecutes(harness):
    service, repository, business, setups, executor, stored, events = harness
    repository.finish.side_effect = PersistenceError("commit_unconfirmed")
    with pytest.raises(PersistenceError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    assert repository.get.return_value.claim_nonce is not None
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=2))
    assert executor.execute.await_count == 1


def test_wrong_claim_nonce_prevents_native_call(harness):
    service, repository, business, setups, executor, stored, events = harness

    def corrupt(workspace, operation, **kwargs):
        kwargs["nonce"] = DRAFT
        return claim_provisioning(stored, now=NOW, **kwargs)

    repository.claim.side_effect = corrupt
    with pytest.raises(PersistenceError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    executor.execute.assert_not_awaited()


@pytest.mark.parametrize("changes", [dict(actor_id="other"), dict(workspace_id=APP), dict(workspace_role="normal")])
def test_wrong_actor_workspace_or_role_prevents_claim(harness, changes):
    service, repository, business, setups, executor, stored, events = harness
    principal = Principal.model_validate(PRINCIPAL.model_dump() | changes)
    with pytest.raises(AccessDenied):
        asyncio.run(service.advance(principal, SESSION, OP, expected_revision=1))
    repository.claim.assert_not_called()
    executor.execute.assert_not_awaited()


def test_deleted_device_prevents_new_phase(harness):
    service, repository, business, setups, executor, stored, events = harness
    business.get_device.return_value.deleted_at = NOW
    with pytest.raises(NotFound):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    repository.claim.assert_not_called()


def test_same_request_replays_frozen_operation_without_reading_mutable_setup(harness):
    service, repository, business, setups, executor, stored, events = harness
    repository.find_request.return_value = stored
    result = asyncio.run(
        service.start(PRINCIPAL, "setup", config_ref="config", config_revision=1, request_key="request")
    )
    assert result == stored.view
    setups.get.assert_not_awaited()
    repository.create.assert_not_called()


@pytest.mark.parametrize("changes", [dict(setup_id="other"), dict(config_ref="other"), dict(config_revision=2)])
def test_request_key_reuse_with_different_command_is_rejected(harness, changes):
    service, repository, business, setups, executor, stored, events = harness
    repository.find_request.return_value = stored
    args = dict(setup_id="setup", config_ref="config", config_revision=1, request_key="request") | changes
    with pytest.raises(Conflict):
        asyncio.run(service.start(PRINCIPAL, **args))
    setups.get.assert_not_awaited()
    repository.create.assert_not_called()


def test_create_race_returns_same_request_winner(harness):
    service, repository, business, setups, executor, stored, events = harness
    repository.create.side_effect = None
    repository.create.return_value = stored
    result = asyncio.run(
        service.start(PRINCIPAL, "setup", config_ref="config", config_revision=1, request_key="request")
    )
    assert result.id == OP
    executor.execute.assert_not_awaited()


def test_finish_response_changed_snapshot_is_not_confirmed(harness):
    service, repository, business, setups, executor, stored, events = harness

    def corrupt(workspace, operation, **kwargs):
        finished = finish_provisioning(repository.get.return_value, now=NOW, **kwargs)
        return finished.model_copy(update={"view": finished.view.model_copy(update={"config_ref": "changed"})})

    repository.finish.side_effect = corrupt
    with pytest.raises(PersistenceError):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    assert executor.execute.await_count == 1


def test_executor_unknown_failure_is_persisted_uncertain(harness):
    service, repository, business, setups, executor, stored, events = harness
    executor.execute.side_effect = RuntimeError("private upstream detail")
    result = asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=1))
    assert result.state == "uncertain"
    assert "private upstream detail" not in result.model_dump_json()
    with pytest.raises(Conflict):
        asyncio.run(service.advance(PRINCIPAL, SESSION, OP, expected_revision=3))
    assert executor.execute.await_count == 1


def test_history_filters_actor_before_repository_pagination(harness):
    from enterprise_platform.application.contracts import Page

    service, repository, business, setups, executor, stored, events = harness
    page = Page(items=(stored.view,), total=4, offset=2, limit=1)
    repository.list.return_value = page
    result = asyncio.run(service.list(PRINCIPAL, "setup", offset=2, limit=1))
    assert result == page
    repository.list.assert_called_once_with(WS, setup_id="setup", actor_id="actor", offset=2, limit=1)
    executor.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "another"),
        ("workspace_id", APP),
        ("setup_id", "other"),
        ("device_id", "other"),
        ("app_id", DRAFT),
        ("scenario", "quality"),
    ],
)
def test_history_rejects_misscoped_repository_projection(harness, field, value):
    from enterprise_platform.application.contracts import Page

    service, repository, business, setups, executor, stored, events = harness
    repository.list.return_value = Page(
        items=(stored.view.model_copy(update={field: value}),), total=1, offset=0, limit=20
    )
    with pytest.raises(AccessDenied):
        asyncio.run(service.list(PRINCIPAL, "setup"))


@pytest.mark.parametrize("offset,limit", [(True, 20), (0, True), (-1, 20), (0, 101)])
def test_history_rejects_invalid_pagination_before_repository(harness, offset, limit):
    from enterprise_platform.application.errors import InvalidInput

    service, repository, business, setups, executor, stored, events = harness
    with pytest.raises(InvalidInput):
        asyncio.run(service.list(PRINCIPAL, "setup", offset=offset, limit=limit))
    repository.list.assert_not_called()


def test_history_checks_live_device_and_setup_before_listing(harness):
    service, repository, business, setups, executor, stored, events = harness
    business.get_device.return_value.deleted_at = NOW
    with pytest.raises(NotFound):
        asyncio.run(service.list(PRINCIPAL, "setup"))
    repository.list.assert_not_called()


def profile_service(harness):
    from test_workflow_plugin_profiles import profile

    from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry

    service, repository, business, setups, executor, stored, events = harness
    registry = WorkflowPluginProfileRegistry((profile(workspace_id=WS, display_name="Factory workflow"),))
    return WorkflowProvisioningService(repository, business, setups, executor, profiles=registry)


def test_profile_choices_only_after_setup_device_authorization(harness):
    service = profile_service(harness)
    choices = asyncio.run(service.list_profiles(PRINCIPAL, "setup"))
    assert len(choices) == 1
    assert choices[0].model_dump() == dict(config_ref="profile", config_revision=1, display_name="Factory workflow")
    harness[1].create.assert_not_called()
    harness[4].execute.assert_not_awaited()


def test_profile_choices_require_manage_permission(harness):
    service = profile_service(harness)
    member = PRINCIPAL.model_copy(update={"workspace_role": "normal"})
    with pytest.raises(AccessDenied):
        asyncio.run(service.list_profiles(member, "setup"))
    harness[3].get.assert_not_awaited()


def test_profile_choices_require_explicit_registry(harness):
    from enterprise_platform.application.errors import DependencyUnavailable

    with pytest.raises(DependencyUnavailable):
        asyncio.run(harness[0].list_profiles(PRINCIPAL, "setup"))


def test_profile_choices_reject_wrong_setup_and_deleted_device(harness):
    service = profile_service(harness)
    harness[3].get.return_value = harness[3].get.return_value.model_copy(update={"workspace_id": APP})
    with pytest.raises(AccessDenied):
        asyncio.run(service.list_profiles(PRINCIPAL, "setup"))


def test_profile_choices_require_confirmed_draft(harness):
    service = profile_service(harness)
    harness[3].get.return_value = harness[3].get.return_value.model_copy(update={"state": "uncertain"})
    with pytest.raises(Conflict):
        asyncio.run(service.list_profiles(PRINCIPAL, "setup"))
