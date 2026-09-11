import asyncio
from datetime import timedelta

import pytest
from test_schedule_service import fixture as service_fixture
from test_scheduling import START, schedule

from enterprise_platform.application.contracts import Run, canonical_hash
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.scheduling import ScheduleCommit, plan_tick


def fixture():
    service, repo, business, authority, _ = service_fixture()
    value = schedule(workspace_id="w", binding_id="b", device_id="d", service_actor_id="service")
    repo.get.return_value = value
    bound = business.get_binding.return_value
    business.get_binding.return_value = bound.model_copy(
        update={"manifest": {"input_keys": ["window_start", "window_end"]}}
    )
    principal = authority.get_grant.return_value.principal

    def commit(old, spec):
        decision = plan_tick(old, START)
        run = None
        if spec is not None:
            run = Run(
                id="run-1",
                workspace_id="w",
                actor_id="service",
                request_key=decision.occurrence.id,
                payload_hash=canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": None}),
                spec=spec,
                status="queued",
                created_at=START,
                updated_at=START,
            )
        return ScheduleCommit(
            old.model_copy(update={"next_due_at": decision.next_due_at}), run, decision.discarded_ticks
        )

    repo.commit_tick.side_effect = commit
    return service, repo, business, authority, principal, value


def test_tick_reauthorizes_and_prepares_then_uses_atomic_commit() -> None:
    service, repo, _, authority, principal, value = fixture()
    result = asyncio.run(service.tick(principal, value.id))
    assert result.run is not None and result.run.actor_id == "service"
    assert result.run.spec.parameters["window_end"] == START.isoformat()
    assert result.schedule.next_due_at == START + timedelta(seconds=60)
    assert authority.get_grant.call_count == 2
    authority.get_grant.assert_called_with("w", "service")
    authority.has_native_timer.assert_called_once()
    repo.commit_tick.assert_called_once()


def test_human_or_different_service_identity_cannot_tick() -> None:
    service, repo, _, authority, principal, value = fixture()
    with pytest.raises(AccessDenied):
        asyncio.run(service.tick(principal.model_copy(update={"actor_id": "human"}), value.id))
    authority.get_grant.assert_not_called()
    repo.commit_tick.assert_not_called()


def test_paused_tick_performs_no_binding_or_authority_calls_and_no_writes() -> None:
    service, repo, business, authority, principal, value = fixture()
    repo.get.return_value = value.model_copy(update={"enabled": False})
    result = asyncio.run(service.tick(principal, value.id))
    assert result.run is None and result.discarded_ticks == 0
    business.get_binding.assert_not_called()
    authority.get_grant.assert_not_called()
    repo.commit_tick.assert_not_called()


@pytest.mark.parametrize("failure", ["revoked", "native_timer", "missing"])
def test_current_authority_failure_prevents_tick(failure: str) -> None:
    service, repo, _, authority, principal, value = fixture()
    if failure == "revoked":
        authority.get_grant.return_value = authority.get_grant.return_value.model_copy(update={"enabled": False})
    elif failure == "missing":
        authority.get_grant.return_value = None
    else:
        authority.has_native_timer.return_value = True
    with pytest.raises((AccessDenied, Conflict)):
        asyncio.run(service.tick(principal, value.id))
    repo.commit_tick.assert_not_called()


def test_commit_conflict_is_propagated_without_retry() -> None:
    service, repo, _, _, principal, value = fixture()
    repo.commit_tick.side_effect = Conflict("concurrent_pause")
    with pytest.raises(Conflict):
        asyncio.run(service.tick(principal, value.id))
    repo.commit_tick.assert_called_once()


@pytest.mark.parametrize("tamper", ["workspace", "cursor", "missing_run", "actor", "key"])
def test_tick_rejects_mismatched_commit_receipts(tamper: str) -> None:
    service, repo, _, _, principal, value = fixture()
    original = repo.commit_tick.side_effect

    def corrupt(old, spec):
        receipt = original(old, spec)
        saved, run = receipt.schedule, receipt.run
        if tamper == "workspace":
            saved = saved.model_copy(update={"workspace_id": "other"})
        elif tamper == "cursor":
            saved = old
        elif tamper == "missing_run":
            run = None
        elif tamper == "actor":
            run = run.model_copy(update={"actor_id": "other"})
        else:
            run = run.model_copy(update={"request_key": "different"})
        return ScheduleCommit(saved, run, receipt.discarded_ticks)

    repo.commit_tick.side_effect = corrupt
    with pytest.raises(Conflict):
        asyncio.run(service.tick(principal, value.id))
    repo.commit_tick.assert_called_once()


def test_expired_skip_reauthorizes_before_advancing_without_a_run() -> None:
    service, repo, _, authority, principal, value = fixture()
    service._clock = lambda: START + timedelta(seconds=11)
    repo.commit_tick.side_effect = lambda old, spec: ScheduleCommit(
        old.model_copy(update={"next_due_at": START + timedelta(seconds=60)}), None, 1
    )
    result = asyncio.run(service.tick(principal, value.id))
    assert result.run is None and result.discarded_ticks == 1
    assert authority.get_grant.call_count == 2
    repo.commit_tick.assert_called_once_with(value, None)


def test_readonly_principal_is_denied_before_schedule_lookup() -> None:
    service, repo, _, _, principal, value = fixture()
    with pytest.raises(AccessDenied):
        asyncio.run(service.tick(principal.model_copy(update={"workspace_role": "normal"}), value.id))
    repo.get.assert_not_called()


def test_source_registration_removed_after_enable_prevents_new_enqueue() -> None:
    service, repo, _, _, principal, value = fixture()
    service._reads = ImmutableReadCatalog(())
    with pytest.raises(InvalidInput):
        asyncio.run(service.tick(principal, value.id))
    repo.commit_tick.assert_not_called()
