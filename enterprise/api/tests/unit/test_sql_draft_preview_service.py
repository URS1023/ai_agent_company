import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_dashboard_refresh_service import principal
from test_dashboard_sql_trial import setup as trial_setup
from test_sql_draft_preview import fixture

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput


def setup():
    evidence, draft, design = fixture()
    parts = trial_setup()
    parts[2].get.return_value = draft
    parts[1].get.return_value = replace(parts[1].get.return_value, design=design)
    permit = parts[4].authorize.return_value
    parts[4].authorize.side_effect = lambda actor, current, slot: permit.model_copy(
        update={
            "draft_hash": canonical_hash(current.model_dump(mode="json")),
            "slot_id": slot,
        }
    )
    store = Mock()
    records = {item.trial_id: item for item in evidence}
    store.get.side_effect = lambda workspace, trial: records[trial]
    service = DashboardService(parts[1], Mock(), sql_trial=parts[0], sql_trial_evidence=store)
    return service, parts, store, evidence


def preview(service, ids=("trial-1", "trial-0"), actor=None):
    return asyncio.run(service.preview_sql_draft(actor or principal(), "dashboard-1", "draft-1", ids))


def test_group_preview_reauthorizes_every_slot_without_execution_or_publication():
    service, parts, store, evidence = setup()
    result = preview(service)
    assert [item.slot.slot_id for item in result.items] == ["a", "b"]
    assert result.missing_required_slots == ()
    assert parts[4].authorize.call_count == 8
    assert store.get.call_count == 2
    parts[5].execute.assert_not_awaited()
    parts[1].commit.assert_not_called()
    parts[1].save_bindings.assert_not_called()
    store.create.assert_not_called()


@pytest.mark.parametrize("ids", [(), ("trial-0", "trial-0"), tuple(f"trial-{i}" for i in range(101))])
def test_invalid_selection_is_rejected_before_storage(ids):
    service, parts, store, evidence = setup()
    with pytest.raises(InvalidInput):
        preview(service, ids)
    store.get.assert_not_called()


def test_readonly_actor_cannot_load_selected_evidence():
    service, parts, store, evidence = setup()
    with pytest.raises(AccessDenied):
        preview(service, actor=principal().model_copy(update={"workspace_role": "normal"}))
    store.get.assert_not_called()


def test_foreign_second_trial_never_returns_partial_preview():
    service, parts, store, evidence = setup()
    store.get.side_effect = [evidence[0], evidence[1].model_copy(update={"trial_id": "foreign"})]
    with pytest.raises(AccessDenied):
        preview(service, ("trial-0", "trial-1"))
    parts[5].execute.assert_not_awaited()


def test_revocation_after_projection_discards_all_slots():
    service, parts, store, evidence = setup()
    original = parts[4].authorize.side_effect
    calls = 0

    def authorize(*args):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise AccessDenied()
        return original(*args)

    parts[4].authorize.side_effect = authorize
    with pytest.raises(AccessDenied):
        preview(service)


def test_stale_dashboard_never_returns_a_partial_preview():
    service, parts, store, evidence = setup()
    parts[1].get.return_value = replace(parts[1].get.return_value, revision=2)
    with pytest.raises(Conflict):
        preview(service)
